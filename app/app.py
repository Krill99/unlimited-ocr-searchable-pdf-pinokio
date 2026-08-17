from __future__ import annotations

import importlib
import json
import os
import queue
import re
import shutil
import socket
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Iterator, Literal

import pymupdf as fitz
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from PIL import Image
from pydantic import BaseModel

try:
    from searchable_pdf import build_searchable_pdf
    from reconstructed_pdf import build_reconstructed_pdf
    from model_patches import patch_model_files
except ImportError:
    from app.searchable_pdf import build_searchable_pdf
    from app.reconstructed_pdf import build_reconstructed_pdf
    from app.model_patches import patch_model_files


APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
MODEL_DIR = ROOT_DIR / "models" / "Unlimited-OCR"
WORK_ROOT = ROOT_DIR / "work"
WORK_ROOT.mkdir(parents=True, exist_ok=True)

MODEL_NAME = "baidu/Unlimited-OCR"
APP_VERSION = "1.1.0"
MOCK_MODE = os.environ.get("UOCR_MOCK", "0") == "1"

# Simple local registries. IDs, rather than arbitrary paths, are exposed to the browser.
_FILES: dict[str, dict] = {}
_DOWNLOADS: dict[str, dict] = {}
_REGISTRY_LOCK = threading.Lock()
_GPU_LOCK = threading.Lock()

model = None
tokenizer = None


def _safe_name(name: str | None, fallback: str = "document") -> str:
    name = os.path.basename(name or fallback)
    name = re.sub(r"[^A-Za-z0-9._()\- ]+", "_", name).strip(" .")
    return name or fallback


def _register_file(path: str | Path, orig_name: str, kind: str, **metadata) -> str:
    file_id = uuid.uuid4().hex
    with _REGISTRY_LOCK:
        _FILES[file_id] = {
            "path": str(Path(path).resolve()),
            "orig_name": _safe_name(orig_name),
            "kind": kind,
            "created": time.time(),
            **metadata,
        }
    return file_id


def _get_file(file_id: str, expected_kind: str | None = None) -> dict:
    with _REGISTRY_LOCK:
        item = _FILES.get(file_id)
    if not item:
        raise HTTPException(status_code=404, detail="Uploaded file not found. Please upload it again.")
    if expected_kind and item.get("kind") != expected_kind:
        raise HTTPException(status_code=400, detail=f"Expected {expected_kind}, got {item.get('kind')}.")
    if not os.path.isfile(item["path"]):
        raise HTTPException(status_code=410, detail="The temporary file has expired. Please upload it again.")
    return item


def _load_model() -> None:
    global model, tokenizer
    if MOCK_MODE:
        print("UOCR_MOCK=1: skipping model load.", flush=True)
        return

    import torch
    from transformers import AutoModel, AutoTokenizer

    if not torch.cuda.is_available():
        raise RuntimeError(
            "Unlimited-OCR local mode requires an NVIDIA CUDA GPU. "
            "PyTorch cannot see CUDA on this computer."
        )

    source = str(MODEL_DIR) if MODEL_DIR.exists() else MODEL_NAME
    local_only = MODEL_DIR.exists()

    if local_only:
        patch_info = patch_model_files(MODEL_DIR)
        print("Local model compatibility check:", flush=True)
        print(f"  generation: {patch_info['modeling_unlimitedocr']}", flush=True)
        print(f"  vision buffer: {patch_info['deepencoder']}", flush=True)

    gpu_name = torch.cuda.get_device_name(0)
    print(f"CUDA GPU: {gpu_name}", flush=True)
    print(f"Loading Unlimited-OCR from: {source}", flush=True)

    tokenizer = AutoTokenizer.from_pretrained(
        source,
        trust_remote_code=True,
        local_files_only=local_only,
    )

    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    print(f"Model dtype: {dtype}", flush=True)

    # Unlimited-OCR is a ~3B BF16 model. An 8 GB GPU is too tight for a
    # full `.cuda()` load because CUDA itself plus OCR activations/KV cache
    # also need VRAM. Reserve headroom and let Transformers/Accelerate keep
    # the remainder of the model in system RAM.
    props = torch.cuda.get_device_properties(0)
    total_vram = int(props.total_memory)
    reserve_vram = int(2.25 * 1024**3)
    gpu_budget = max(int(2.0 * 1024**3), total_vram - reserve_vram)

    try:
        import psutil
        vm = psutil.virtual_memory()
        total_ram = int(vm.total)
        available_ram = int(vm.available)
    except Exception:
        total_ram = int(16 * 1024**3)
        available_ram = int(12 * 1024**3)
    reserve_ram = int(2.0 * 1024**3)
    cpu_budget = max(int(2.0 * 1024**3), available_ram - reserve_ram)

    print(f"Total VRAM: {total_vram / 1024**3:.2f} GiB", flush=True)
    print(f"System RAM: {total_ram / 1024**3:.2f} GiB total, {available_ram / 1024**3:.2f} GiB available", flush=True)
    print(f"GPU model budget: {gpu_budget / 1024**3:.2f} GiB", flush=True)
    print(f"CPU offload budget: {cpu_budget / 1024**3:.2f} GiB", flush=True)
    print("Loading with device_map='auto' (GPU + CPU offload)...", flush=True)

    offload_dir = ROOT_DIR / "offload"
    offload_dir.mkdir(parents=True, exist_ok=True)

    model = AutoModel.from_pretrained(
        source,
        trust_remote_code=True,
        use_safetensors=True,
        dtype=dtype,
        local_files_only=local_only,
        device_map="auto",
        max_memory={0: gpu_budget, "cpu": cpu_budget},
        offload_folder=str(offload_dir),
        offload_buffers=True,
        low_cpu_mem_usage=True,
    ).eval()

    # Runtime guard as a second layer of compatibility. Even if a future
    # Unlimited-OCR source revision no longer matches our local source patch,
    # every generation call still receives an explicit attention mask and pad ID.
    _original_generate = model.generate

    def _generate_with_explicit_mask(*args, **kwargs):
        input_ids = kwargs.get("input_ids")
        if input_ids is not None and kwargs.get("attention_mask") is None:
            kwargs["attention_mask"] = torch.ones_like(input_ids, dtype=torch.long, device=input_ids.device)
        if kwargs.get("pad_token_id") is None:
            kwargs["pad_token_id"] = (
                tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
            )
        return _original_generate(*args, **kwargs)

    model.generate = _generate_with_explicit_mask
    print("Generation compatibility guard: active", flush=True)

    device_map = getattr(model, "hf_device_map", None)
    if device_map:
        gpu_modules = sum(1 for v in device_map.values() if v in (0, "cuda", "cuda:0"))
        cpu_modules = sum(1 for v in device_map.values() if v == "cpu")
        disk_modules = sum(1 for v in device_map.values() if v == "disk")
        print(
            f"Device map ready: {gpu_modules} module groups on GPU, "
            f"{cpu_modules} on CPU, {disk_modules} on disk.",
            flush=True,
        )

    print(
        f"CUDA allocated after load: {torch.cuda.memory_allocated(0) / 1024**3:.2f} GiB / "
        f"{total_vram / 1024**3:.2f} GiB",
        flush=True,
    )
    print("Unlimited-OCR model loaded.", flush=True)


def pdf_to_images(pdf_path: str, dpi: int = 200) -> list[dict]:
    """
    Rasterize PDF pages only for OCR and retain exact raster geometry.

    Unlimited-OCR reports boxes in the coordinate system of the image it actually
    receives. Keeping pixmap width/height lets the searchable-PDF builder map those
    boxes back through that exact raster instead of relying on page-size assumptions.
    """
    doc = fitz.open(pdf_path)
    if doc.needs_pass:
        doc.close()
        raise ValueError("Password-protected PDFs are not supported.")

    out_dir = tempfile.mkdtemp(prefix="pages_", dir=str(WORK_ROOT))
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    records: list[dict] = []
    try:
        for i, page in enumerate(doc):
            path = os.path.join(out_dir, f"page_{i + 1:04d}.png")
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pix.save(path)
            records.append({
                "path": path,
                "width": int(pix.width),
                "height": int(pix.height),
                "dpi": int(dpi),
                "page_index": i,
                "rotation": int(page.rotation or 0),
            })
    finally:
        doc.close()
    return records


def _mock_ocr(path: str) -> str:
    """Development smoke-test output. Never enabled by the Pinokio launcher."""
    return (
        "<|det|>title [80, 70, 920, 130]<|/det|># Unlimited OCR render test\n"
        "<|det|>text [80, 150, 920, 230]<|/det|>Greek math: \\(\\alpha + \\beta = \\Gamma\\)\n"
        "<|det|>image [180, 270, 820, 610]<|/det|>\n"
        "<|det|>formula [180, 660, 820, 840]<|/det|>\\[\\begin{array}{cc}\\alpha & \\beta \\\\ \\gamma & \\Delta\\end{array}\\]"
    )


def _run_model_ocr_streaming(
    path: str,
    mode: str,
    prompt: str,
    emit_line: Callable[[str], None] | None = None,
) -> str:
    """Run Unlimited-OCR and emit complete generated lines while inference is active.

    The upstream model normally uses a Transformers TextStreamer when eval_mode=False.
    We temporarily replace that streamer class with a quiet callback streamer, allowing
    the FastAPI response to send OCR lines to the webpage in real time while still
    retaining the full raw <|det|> output for PDF generation.
    """
    if MOCK_MODE:
        raw = _mock_ocr(path)
        for line in raw.splitlines(keepends=True):
            if emit_line:
                emit_line(line)
            time.sleep(0.04)
        return raw
    if model is None or tokenizer is None:
        raise RuntimeError("OCR model is not loaded.")

    from transformers import TextStreamer

    out_dir = tempfile.mkdtemp(prefix="ocr_", dir=str(WORK_ROOT))
    emitted_parts: list[str] = []
    captured_generation: dict[str, object] = {}
    try:
        if mode == "gundam":
            base_size, image_size, crop_mode, ngram_window = 1024, 640, True, 128
        else:
            base_size, image_size, crop_mode, ngram_window = 1024, 1024, False, 128

        kwargs = dict(
            prompt=f"<image>{prompt or 'document parsing.'}",
            image_file=path,
            output_path=out_dir,
            base_size=base_size,
            image_size=image_size,
            crop_mode=crop_mode,
            max_length=8192,
            no_repeat_ngram_size=35,
            ngram_window=ngram_window,
            save_results=False,
            eval_mode=False,
            tps_interval=0,
        )

        model_module = importlib.import_module(model.__class__.__module__)
        original_streamer_cls = getattr(model_module, "TPSTextStreamer", None)
        if original_streamer_cls is None:
            raise RuntimeError("Unlimited-OCR TPSTextStreamer was not found; live OCR streaming is unavailable.")

        class LiveLineStreamer(TextStreamer):
            def __init__(self, tok, interval=0, **stream_kwargs):
                super().__init__(tok, **stream_kwargs)
                self.interval = interval
                self._line_buffer = ""

            def on_finalized_text(self, text: str, stream_end: bool = False):
                if text:
                    emitted_parts.append(text)
                    self._line_buffer += text
                    while "\n" in self._line_buffer:
                        line, self._line_buffer = self._line_buffer.split("\n", 1)
                        if emit_line:
                            emit_line(line + "\n")
                if stream_end and self._line_buffer:
                    if emit_line:
                        emit_line(self._line_buffer)
                    self._line_buffer = ""

        # Only one OCR inference runs at a time. That makes temporarily replacing
        # the upstream streamer's module-global class safe for this local app.
        # We also capture generate()'s final token IDs so PDF construction uses the
        # exact decoded model output, not the display stream's incremental text.
        with _GPU_LOCK:
            setattr(model_module, "TPSTextStreamer", LiveLineStreamer)
            original_generate_for_capture = model.generate

            def capture_generate(*gen_args, **gen_kwargs):
                input_ids = gen_kwargs.get("input_ids")
                if input_ids is not None:
                    captured_generation["prompt_length"] = int(input_ids.shape[-1])
                output_ids = original_generate_for_capture(*gen_args, **gen_kwargs)
                captured_generation["output_ids"] = output_ids
                return output_ids

            model.generate = capture_generate
            try:
                model.infer(tokenizer, **kwargs)
            except RuntimeError as exc:
                message = str(exc)
                if "out of memory" in message.lower():
                    try:
                        import torch
                        torch.cuda.empty_cache()
                    except Exception:
                        pass
                    raise RuntimeError(
                        "CUDA ran out of memory while OCR was running. Close other GPU applications "
                        "or try Base mode / a smaller document page."
                    ) from exc
                raise
            finally:
                model.generate = original_generate_for_capture
                setattr(model_module, "TPSTextStreamer", original_streamer_cls)

        output_ids = captured_generation.get("output_ids")
        prompt_length = int(captured_generation.get("prompt_length") or 0)
        if output_ids is not None:
            sequences = getattr(output_ids, "sequences", output_ids)
            result = tokenizer.decode(sequences[0, prompt_length:])
        else:
            # Safety fallback: the concatenated streamer content remains usable.
            result = "".join(emitted_parts)

        stop_str = "<｜end▁of▁sentence｜>"
        if result.endswith(stop_str):
            result = result[:-len(stop_str)]
        result = result.strip()
        if not result:
            raise RuntimeError("Unlimited-OCR completed but did not return OCR text.")
        return result
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def _ndjson(obj: dict) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _cleanup_old_work(max_age_hours: int = 24) -> None:
    cutoff = time.time() - max_age_hours * 3600
    try:
        for child in WORK_ROOT.iterdir():
            try:
                if child.stat().st_mtime < cutoff:
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


class FileRequest(BaseModel):
    file_id: str


class OCRRequest(BaseModel):
    file_id: str
    mode: str = "gundam"
    prompt: str = "document parsing."
    page_number: int | None = None
    total_pages: int | None = None


class PDFBuildRequest(BaseModel):
    pdf_id: str
    page_texts: list[str]
    page_ids: list[str] = []
    output_mode: Literal["reconstructed", "searchable_scan"] = "searchable_scan"


class SearchablePDFRequest(BaseModel):
    # Backward-compatible request model for older clients.
    pdf_id: str
    page_texts: list[str]
    page_ids: list[str] = []


app = FastAPI(title="Unlimited OCR – Searchable PDF", version=APP_VERSION)


@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse((APP_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/favicon.ico")
def favicon():
    icon = ROOT_DIR / "icon.png"
    if not icon.exists():
        raise HTTPException(status_code=404, detail="No icon")
    return FileResponse(icon, media_type="image/png")


@app.get("/health")
def health():
    return {
        "ok": True,
        "version": APP_VERSION,
        "model": MODEL_NAME,
        "mock": MOCK_MODE,
        "gpu_ready": MOCK_MODE or model is not None,
    }


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    original = _safe_name(file.filename, "upload")
    suffix = Path(original).suffix.lower()
    is_pdf = suffix == ".pdf" or (file.content_type or "").lower() == "application/pdf"
    kind = "pdf" if is_pdf else "image"

    upload_dir = Path(tempfile.mkdtemp(prefix="upload_", dir=str(WORK_ROOT)))
    dest = upload_dir / original
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    finally:
        await file.close()

    if not dest.exists() or dest.stat().st_size == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    file_id = _register_file(dest, original, kind)
    return {
        "file_id": file_id,
        "filename": original,
        "kind": kind,
        "size": dest.stat().st_size,
    }


@app.post("/api/explode_pdf")
def explode_pdf(req: FileRequest):
    item = _get_file(req.file_id, "pdf")
    try:
        records = pdf_to_images(item["path"], dpi=200)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pages = []
    for i, record in enumerate(records):
        page_id = _register_file(
            record["path"],
            f"page_{i + 1:04d}.png",
            "page",
            parent_pdf_id=req.file_id,
            page_index=i,
            pixel_width=record["width"],
            pixel_height=record["height"],
            dpi=record["dpi"],
            rotation=record["rotation"],
        )
        pages.append({
            "file_id": page_id,
            "page": i + 1,
            "width": record["width"],
            "height": record["height"],
            "rotation": record["rotation"],
        })
    return {"pages": pages, "count": len(pages)}


@app.get("/api/page_region/{file_id}")
def page_region(file_id: str, x0: float, y0: float, x1: float, y1: float):
    """Return a cropped OCR layout region from an uploaded image / rasterized PDF page.

    Unlimited-OCR coordinates are normalized to the 0..999 image space. The crop
    is generated from the exact raster that was sent to the OCR model, so figures
    shown in the rendered Markdown view match the model's detected image region.
    """
    item = _get_file(file_id)
    if item.get("kind") not in {"image", "page"}:
        raise HTTPException(status_code=400, detail="Region preview requires an image or extracted PDF page.")

    vals = [float(x0), float(y0), float(x1), float(y1)]
    if not all(v == v and abs(v) != float("inf") for v in vals):
        raise HTTPException(status_code=400, detail="Invalid region coordinates.")
    x0, y0, x1, y1 = vals
    x0, x1 = sorted((max(0.0, min(999.0, x0)), max(0.0, min(999.0, x1))))
    y0, y1 = sorted((max(0.0, min(999.0, y0)), max(0.0, min(999.0, y1))))
    if x1 - x0 < 1 or y1 - y0 < 1:
        raise HTTPException(status_code=400, detail="Detected region is too small.")

    # Stable cache name so rerendering Markdown does not repeatedly crop the page.
    cache_dir = Path(item["path"]).parent / "regions"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = "_".join(str(int(round(v * 10))) for v in (x0, y0, x1, y1))
    output = cache_dir / f"region_{key}.png"

    if not output.exists():
        try:
            with Image.open(item["path"]) as im:
                im.load()
                left = max(0, min(im.width - 1, int(round(x0 / 999.0 * im.width))))
                top = max(0, min(im.height - 1, int(round(y0 / 999.0 * im.height))))
                right = max(left + 1, min(im.width, int(round(x1 / 999.0 * im.width))))
                bottom = max(top + 1, min(im.height, int(round(y1 / 999.0 * im.height))))
                crop = im.crop((left, top, right, bottom))
                if crop.mode not in {"RGB", "RGBA", "L"}:
                    crop = crop.convert("RGB")
                crop.save(output, format="PNG", optimize=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not extract image region: {exc}") from exc

    return FileResponse(
        output,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.post("/api/run_ocr")
def run_ocr(req: OCRRequest):
    item = _get_file(req.file_id)
    if item.get("kind") not in {"image", "page"}:
        raise HTTPException(status_code=400, detail="OCR endpoint requires an image or extracted PDF page.")

    def stream() -> Iterator[bytes]:
        page_label = "image"
        if req.page_number and req.total_pages:
            page_label = f"page {req.page_number}/{req.total_pages}"
        elif req.page_number:
            page_label = f"page {req.page_number}"

        started = time.perf_counter()
        events: queue.Queue[tuple[str, object]] = queue.Queue()
        result_holder: dict[str, object] = {}

        def emit_line(text: str) -> None:
            if text:
                events.put(("line", text))

        def worker() -> None:
            try:
                result_holder["raw"] = _run_model_ocr_streaming(
                    item["path"], req.mode, req.prompt, emit_line=emit_line
                )
            except Exception as exc:
                result_holder["error"] = exc
            finally:
                events.put(("finished", None))

        print(f"[OCR] {page_label} · started · mode={req.mode} · live lines", flush=True)
        yield _ndjson({"type": "status", "text": f"OCR {page_label}…"})
        thread = threading.Thread(target=worker, name=f"ocr-{req.file_id[:8]}", daemon=True)
        thread.start()

        while True:
            kind, payload = events.get()
            if kind == "line":
                yield _ndjson({"type": "delta", "text": str(payload)})
                continue
            if kind == "finished":
                break

        elapsed = time.perf_counter() - started
        error = result_holder.get("error")
        if error is not None:
            print(f"[OCR] {page_label} · ERROR after {elapsed:.1f}s · {error}", flush=True)
            yield _ndjson({"type": "error", "error": str(error), "done": True})
            return

        raw = str(result_holder.get("raw") or "")
        blocks = raw.count("<|det|>")
        print(f"[OCR] {page_label} · done in {elapsed:.1f}s · {blocks} detected blocks", flush=True)
        yield _ndjson({
            "type": "result", "text": raw, "done": True,
            "elapsed_seconds": round(elapsed, 2), "detected_blocks": blocks,
        })

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _page_sources_for_pdf(pdf_id: str, page_ids: list[str]) -> list[dict]:
    page_sources: list[dict] = []
    for i, page_id in enumerate(page_ids or []):
        try:
            page_item = _get_file(page_id, "page")
            if page_item.get("parent_pdf_id") != pdf_id:
                page_sources.append({})
                continue
            page_sources.append({
                "path": page_item.get("path"),
                "width": page_item.get("pixel_width", 0),
                "height": page_item.get("pixel_height", 0),
                "dpi": page_item.get("dpi", 0),
                "page_index": page_item.get("page_index", i),
            })
        except Exception:
            page_sources.append({})
    return page_sources


def _register_pdf_download(path: str, filename: str) -> dict:
    download_id = uuid.uuid4().hex
    with _REGISTRY_LOCK:
        _DOWNLOADS[download_id] = {
            "path": path,
            "filename": filename,
            "created": time.time(),
        }
    return {
        "download_id": download_id,
        "filename": filename,
        "download_url": f"/download/{download_id}",
    }


@app.post("/api/make_pdf")
def make_pdf(req: PDFBuildRequest):
    item = _get_file(req.pdf_id, "pdf")
    original_name = item["orig_name"]
    stem = Path(original_name).stem
    page_sources = _page_sources_for_pdf(req.pdf_id, req.page_ids)

    if req.output_mode == "reconstructed":
        out_dir = Path(tempfile.mkdtemp(prefix="reconstructed_", dir=str(WORK_ROOT)))
        output_name = _safe_name(f"{stem}_reconstructed.pdf", "reconstructed.pdf")
        output_path = out_dir / output_name
        try:
            print(f"[PDF] Building reconstructed PDF for {original_name}…", flush=True)
            built_path, stats = build_reconstructed_pdf(
                input_pdf=item["path"],
                page_ocr_texts=req.page_texts,
                output_pdf=str(output_path),
                page_sources=page_sources,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not build reconstructed PDF: {exc}") from exc

        print(
            f"[PDF] Reconstructed ready · {stats.get('pages_with_ocr', 0)}/{stats.get('pages', 0)} pages · "
            f"{stats.get('visible_text_blocks', 0)} visible text blocks · "
            f"{stats.get('image_blocks', 0)} image blocks · "
            f"{stats.get('image_fallback_blocks', 0)} visual fallbacks",
            flush=True,
        )
    else:
        out_dir = Path(tempfile.mkdtemp(prefix="searchable_", dir=str(WORK_ROOT)))
        output_name = _safe_name(f"{stem}_searchable.pdf", "searchable.pdf")
        output_path = out_dir / output_name
        try:
            print(f"[PDF] Building precise searchable scan for {original_name}…", flush=True)
            built_path, stats = build_searchable_pdf(
                input_pdf=item["path"],
                page_ocr_texts=req.page_texts,
                output_pdf=str(output_path),
                page_sources=page_sources,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not build searchable scan: {exc}") from exc

        print(
            f"[PDF] Searchable scan ready · {stats.get('pages_with_ocr', 0)}/{stats.get('pages', 0)} pages · "
            f"{stats.get('image_refined_blocks', 0)} image-refined blocks · "
            f"{stats.get('image_refined_lines', 0)} detected text lines · "
            f"{stats.get('word_refined_words', 0)} word-refined words · "
            f"{stats.get('box_fit_blocks', 0)} block-fit fallbacks · "
            f"{stats.get('fallback_blocks', 0)} unpositioned fallbacks",
            flush=True,
        )

    info = _register_pdf_download(built_path, output_name)
    return {**info, "output_mode": req.output_mode, "stats": stats}


@app.post("/api/make_searchable_pdf")
def make_searchable_pdf(req: SearchablePDFRequest):
    # Keep older clients working while the current UI uses /api/make_pdf.
    return make_pdf(PDFBuildRequest(
        pdf_id=req.pdf_id,
        page_texts=req.page_texts,
        page_ids=req.page_ids,
        output_mode="searchable_scan",
    ))


@app.get("/download/{download_id}")
def download(download_id: str):
    with _REGISTRY_LOCK:
        item = _DOWNLOADS.get(download_id)
    if not item or not os.path.isfile(item["path"]):
        raise HTTPException(status_code=404, detail="Generated PDF not found or expired.")
    return FileResponse(
        item["path"],
        media_type="application/pdf",
        filename=item["filename"],
    )


if __name__ == "__main__":
    _cleanup_old_work()
    print("Starting Unlimited OCR – Searchable PDF…", flush=True)
    _load_model()

    import uvicorn

    port = int(os.environ.get("PORT") or _free_port())
    url = f"http://127.0.0.1:{port}"
    # Pinokio's start.js watches this exact line and exposes the URL in its built-in Web UI.
    print(f"PINOKIO_URL={url}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
