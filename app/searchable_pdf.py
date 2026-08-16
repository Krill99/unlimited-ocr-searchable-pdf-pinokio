from __future__ import annotations

import ast
import os
import re
import tempfile
from typing import Iterable, Sequence

import pymupdf as fitz


# Unlimited-OCR emits blocks such as:
# <|det|>text [65, 90, 925, 148]<|/det|>Recognized text...
# Baidu's own visualization maps each coordinate with coord / 999 * image_size.
_COORD_MAX = 999.0

# Capture the whole detection payload, not only the first [...] pair. This also
# tolerates future / unusual outputs that contain a list of boxes.
_DET_MARKER_RE = re.compile(
    r"<\|det\|>\s*([A-Za-z_][\w-]*)\s*(.*?)\s*<\|/det\|>",
    re.DOTALL,
)
_REF_RE = re.compile(r"<\|ref\|>.*?<\|/ref\|>", re.DOTALL)

_FONT_CACHE: dict[str, fitz.Font] = {}


def _parse_bbox_payload(payload: str) -> tuple[float, float, float, float] | None:
    """Return one union bbox from either [x0,y0,x1,y1] or [[...], [...]]."""
    payload = (payload or "").strip()
    if not payload:
        return None

    # Keep only a Python-like list expression if extra whitespace / text appears.
    left = payload.find("[")
    right = payload.rfind("]")
    if left < 0 or right <= left:
        return None

    try:
        value = ast.literal_eval(payload[left : right + 1])
    except Exception:
        return None

    boxes: list[Sequence[float]] = []
    if (
        isinstance(value, (list, tuple))
        and len(value) >= 4
        and all(isinstance(v, (int, float)) for v in value[:4])
    ):
        boxes = [value[:4]]
    elif isinstance(value, (list, tuple)):
        for item in value:
            if (
                isinstance(item, (list, tuple))
                and len(item) >= 4
                and all(isinstance(v, (int, float)) for v in item[:4])
            ):
                boxes.append(item[:4])

    if not boxes:
        return None

    valid: list[tuple[float, float, float, float]] = []
    for b in boxes:
        x0, y0, x1, y1 = map(float, b[:4])
        if x1 > x0 and y1 > y0:
            valid.append((x0, y0, x1, y1))
    if not valid:
        return None

    return (
        min(b[0] for b in valid),
        min(b[1] for b in valid),
        max(b[2] for b in valid),
        max(b[3] for b in valid),
    )


def parse_ocr_layout_blocks(raw: str) -> list[dict]:
    """Parse all Unlimited-OCR layout blocks, including image-only regions."""
    raw = _REF_RE.sub("", raw or "")
    matches = list(_DET_MARKER_RE.finditer(raw))
    blocks: list[dict] = []

    if not matches:
        text = raw.strip()
        return [{"type": "text", "bbox": None, "text": text}] if text else []

    prefix = raw[: matches[0].start()].strip()
    if prefix:
        blocks.append({"type": "text", "bbox": None, "text": prefix})

    for i, match in enumerate(matches):
        category = (match.group(1) or "text").strip().lower()
        bbox = _parse_bbox_payload(match.group(2))
        content_start = match.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        text = raw[content_start:content_end].strip()
        # Keep image / figure blocks even when they have no following text.
        if bbox is not None or text:
            blocks.append({"type": category, "bbox": bbox, "text": text})

    return blocks


def parse_ocr_blocks(raw: str) -> list[dict]:
    """Parse searchable text blocks while excluding image-only layout regions."""
    return [
        block for block in parse_ocr_layout_blocks(raw)
        if block.get("type") != "image" and str(block.get("text") or "").strip()
    ]


def _clean_text(text: str) -> str:
    # Keep document text while removing control characters PDF text insertion dislikes.
    return "".join(ch for ch in (text or "") if ch in "\n\t" or ord(ch) >= 32).strip()


def _font_spec_for_text(page: fitz.Page, text: str) -> tuple[str, fitz.Font]:
    """Return a page font name plus a Font object with usable metrics."""
    has_cjk = any(
        ("\u3400" <= ch <= "\u4dbf")
        or ("\u4e00" <= ch <= "\u9fff")
        or ("\u3040" <= ch <= "\u30ff")
        or ("\uac00" <= ch <= "\ud7af")
        for ch in text
    )
    if has_cjk:
        key = "china-s"
        if key not in _FONT_CACHE:
            _FONT_CACHE[key] = fitz.Font(key)
        # Built-in CJK font is directly writable under this name.
        return key, _FONT_CACHE[key]

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
    ]
    for font_path in candidates:
        if not os.path.exists(font_path):
            continue
        key = os.path.normcase(os.path.abspath(font_path))
        try:
            if key not in _FONT_CACHE:
                _FONT_CACHE[key] = fitz.Font(fontfile=font_path)
            page.insert_font(fontname="OCRFont", fontfile=font_path)
            return "OCRFont", _FONT_CACHE[key]
        except Exception:
            continue

    key = "helv"
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = fitz.Font(key)
    return key, _FONT_CACHE[key]


def _normalized_bbox_to_rotated_page_rect(
    page: fitz.Page,
    bbox: tuple[float, float, float, float],
    source_size: tuple[int, int] | None = None,
) -> fitz.Rect:
    """
    Map Unlimited-OCR's normalized bbox to the exact displayed PDF page rectangle.

    Unlimited-OCR's own visualization uses x / 999 * original_image_width and
    y / 999 * original_image_height. If we know the exact raster dimensions used
    for OCR, first map to those pixels and then map those pixels back to the PDF
    page. This avoids small errors from raster rounding / unusual page dimensions.
    """
    x0, y0, x1, y1 = bbox
    x0 = max(0.0, min(_COORD_MAX, x0))
    y0 = max(0.0, min(_COORD_MAX, y0))
    x1 = max(0.0, min(_COORD_MAX, x1))
    y1 = max(0.0, min(_COORD_MAX, y1))

    page_rect = page.rect  # displayed / rotated coordinates

    if source_size and source_size[0] > 0 and source_size[1] > 0:
        img_w, img_h = float(source_size[0]), float(source_size[1])
        px0 = x0 / _COORD_MAX * img_w
        py0 = y0 / _COORD_MAX * img_h
        px1 = x1 / _COORD_MAX * img_w
        py1 = y1 / _COORD_MAX * img_h
        return fitz.Rect(
            page_rect.x0 + (px0 / img_w) * page_rect.width,
            page_rect.y0 + (py0 / img_h) * page_rect.height,
            page_rect.x0 + (px1 / img_w) * page_rect.width,
            page_rect.y0 + (py1 / img_h) * page_rect.height,
        )

    return fitz.Rect(
        page_rect.x0 + x0 / _COORD_MAX * page_rect.width,
        page_rect.y0 + y0 / _COORD_MAX * page_rect.height,
        page_rect.x0 + x1 / _COORD_MAX * page_rect.width,
        page_rect.y0 + y1 / _COORD_MAX * page_rect.height,
    )


def _split_lines(text: str) -> list[str]:
    text = _clean_text(text)
    if not text:
        return []
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]
    return lines or [re.sub(r"\s+", " ", text).strip()]


def _line_display_rects(rect: fitz.Rect, lines: list[str], font: fitz.Font) -> list[fitz.Rect]:
    """
    Divide a multi-line OCR block into line rectangles.

    Vertical slots are equal because Unlimited-OCR only gives one block rectangle.
    Horizontal widths are proportional to each line's font-metric width rather
    than incorrectly forcing short lines to fill the whole paragraph box.
    """
    if not lines:
        return []
    n = len(lines)
    slot_h = rect.height / n

    unit_widths = []
    for line in lines:
        try:
            unit_widths.append(max(0.001, font.text_length(line, fontsize=1.0)))
        except Exception:
            unit_widths.append(max(0.001, float(len(line))))
    max_width = max(unit_widths) if unit_widths else 1.0

    out: list[fitz.Rect] = []
    for i, (line, uw) in enumerate(zip(lines, unit_widths)):
        y0 = rect.y0 + i * slot_h
        y1 = rect.y0 + (i + 1) * slot_h
        # Preserve natural relative line length while allowing the longest line to
        # reach the detected block edge. A tiny minimum prevents zero-width boxes.
        width = max(1.0, rect.width * min(1.0, uw / max_width))
        out.append(fitz.Rect(rect.x0, y0, min(rect.x1, rect.x0 + width), y1))
    return out


def _insert_exact_invisible_line(
    page: fitz.Page,
    display_rect: fitz.Rect,
    text: str,
    fontname: str,
    font: fitz.Font,
) -> bool:
    """
    Insert one invisible line whose *PDF text bbox* matches display_rect.

    The previous implementation only chose a small font that fit inside the OCR
    box, so selection/search highlights occupied only part of the real text area.
    Here font ascender/descender metrics determine the exact cross-axis size and a
    morph matrix stretches the text along its writing direction to the target box.
    This also handles PDF /Rotate values 0, 90, 180 and 270.
    """
    text = re.sub(r"\s+", " ", _clean_text(text)).strip()
    if not text or display_rect.is_empty or display_rect.width <= 0.5 or display_rect.height <= 0.5:
        return False

    rotation = int(page.rotation or 0) % 360
    if rotation not in (0, 90, 180, 270):
        rotation = 0

    # Text insertion coordinates are always unrotated in PyMuPDF.
    rect = fitz.Rect(display_rect)
    if page.rotation:
        rect = rect * page.derotation_matrix

    asc = float(getattr(font, "ascender", 1.0) or 1.0)
    desc = float(getattr(font, "descender", -0.25) or -0.25)
    metric_h = max(0.1, asc - desc)

    cross_size = rect.height if rotation in (0, 180) else rect.width
    along_size = rect.width if rotation in (0, 180) else rect.height
    fontsize = max(0.5, cross_size / metric_h)

    try:
        natural = float(font.text_length(text, fontsize=fontsize))
    except Exception:
        natural = float(fitz.get_text_length(text, fontname=fontname, fontsize=fontsize))
    if natural <= 0.01:
        return False

    stretch = max(0.01, along_size / natural)

    if rotation == 0:
        point = fitz.Point(rect.x0, rect.y0 + asc * fontsize)
        morph = fitz.Matrix(stretch, 1.0)
    elif rotation == 90:
        point = fitz.Point(rect.x0 + asc * fontsize, rect.y1)
        morph = fitz.Matrix(1.0, stretch)
    elif rotation == 180:
        point = fitz.Point(rect.x1, rect.y0 - desc * fontsize)
        morph = fitz.Matrix(stretch, 1.0)
    else:  # 270
        point = fitz.Point(rect.x0 - desc * fontsize, rect.y0)
        morph = fitz.Matrix(1.0, stretch)

    try:
        page.insert_text(
            point,
            text,
            fontsize=fontsize,
            fontname=fontname,
            rotate=rotation,
            morph=(point, morph),
            render_mode=3,  # invisible but selectable/searchable
            overlay=True,
        )
        return True
    except Exception:
        # Maximum-compatibility retry. Latin-1 fallback is preferable to dropping
        # the searchable layer entirely on a problematic glyph/font.
        try:
            safe = text.encode("latin-1", "replace").decode("latin-1")
            fallback_font = _FONT_CACHE.setdefault("helv", fitz.Font("helv"))
            return _insert_exact_invisible_line(
                page,
                display_rect,
                safe,
                "helv",
                fallback_font,
            ) if fontname != "helv" else False
        except Exception:
            return False


def _insert_invisible_block(
    page: fitz.Page,
    display_rect: fitz.Rect,
    text: str,
) -> int:
    """Insert a block as one or more precisely fitted invisible text lines."""
    lines = _split_lines(text)
    if not lines:
        return 0

    fontname, font = _font_spec_for_text(page, text)
    line_rects = _line_display_rects(display_rect, lines, font)
    inserted = 0
    for line, line_rect in zip(lines, line_rects):
        if _insert_exact_invisible_line(page, line_rect, line, fontname, font):
            inserted += 1
    return inserted


def _insert_unpositioned_fallback(page: fitz.Page, text: str) -> int:
    """Ensure text without any bbox is still searchable, without pretending exact placement."""
    text = _clean_text(text)
    if not text:
        return 0

    page_rect = page.rect
    display_rect = fitz.Rect(
        page_rect.x0 + 8,
        page_rect.y0 + 8,
        page_rect.x1 - 8,
        page_rect.y1 - 8,
    )
    if display_rect.is_empty:
        return 0

    # A page-level fallback is intentionally tiny: it is searchable but is not
    # counted as geometrically aligned because the model supplied no coordinates.
    rect = display_rect * page.derotation_matrix if page.rotation else display_rect
    fontname, _ = _font_spec_for_text(page, text)
    try:
        rc = page.insert_textbox(
            rect,
            text,
            fontsize=2.5,
            fontname=fontname,
            render_mode=3,
            overlay=True,
            lineheight=1.0,
            rotate=int(page.rotation or 0) % 360,
        )
        return 1 if rc >= 0 else 0
    except Exception:
        return 0


def build_searchable_pdf(
    input_pdf: str,
    page_ocr_texts: Iterable[str],
    output_pdf: str | None = None,
    page_sources: Sequence[dict] | None = None,
) -> tuple[str, dict]:
    """
    Copy the original PDF and add a geometry-fitted invisible OCR text layer.

    page_sources may contain the exact OCR raster metadata for each page:
        {"width": int, "height": int, "path": str}
    Only width/height are required for coordinate mapping.
    """
    page_ocr_texts = list(page_ocr_texts or [])
    page_sources = list(page_sources or [])

    if output_pdf is None:
        stem = os.path.splitext(os.path.basename(input_pdf))[0]
        out_dir = tempfile.mkdtemp(prefix="searchable_pdf_")
        output_pdf = os.path.join(out_dir, f"{stem}_searchable.pdf")

    doc = fitz.open(input_pdf)
    if doc.needs_pass:
        doc.close()
        raise ValueError("Password-protected PDFs are not supported.")

    stats = {
        "pages": len(doc),
        "pages_with_ocr": 0,
        "positioned_blocks": 0,
        "positioned_lines": 0,
        "fallback_blocks": 0,
        "source_mapped_pages": 0,
        "geometry": "precise-box-fit",
    }

    for page_index, page in enumerate(doc):
        raw = page_ocr_texts[page_index] if page_index < len(page_ocr_texts) else ""
        blocks = parse_ocr_blocks(raw)
        if blocks:
            stats["pages_with_ocr"] += 1

        source_size: tuple[int, int] | None = None
        if page_index < len(page_sources):
            src = page_sources[page_index] or {}
            try:
                sw, sh = int(src.get("width", 0)), int(src.get("height", 0))
                if sw > 0 and sh > 0:
                    source_size = (sw, sh)
                    stats["source_mapped_pages"] += 1
            except Exception:
                source_size = None

        fallback_texts: list[str] = []
        for block in blocks:
            text = block.get("text", "")
            bbox = block.get("bbox")
            if bbox:
                display_rect = _normalized_bbox_to_rotated_page_rect(page, bbox, source_size)
                inserted_lines = _insert_invisible_block(page, display_rect, text)
                if inserted_lines:
                    stats["positioned_blocks"] += 1
                    stats["positioned_lines"] += inserted_lines
                else:
                    fallback_texts.append(text)
            else:
                fallback_texts.append(text)

        if fallback_texts:
            joined = "\n".join(t for t in fallback_texts if t.strip())
            if _insert_unpositioned_fallback(page, joined):
                stats["fallback_blocks"] += len(fallback_texts)

    # Save as a new PDF; the original file is never overwritten.
    doc.save(output_pdf, garbage=3, deflate=True)
    doc.close()
    return output_pdf, stats
