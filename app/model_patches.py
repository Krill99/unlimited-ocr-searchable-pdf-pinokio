from __future__ import annotations

import re
from pathlib import Path

PATCH_MARKER = "UOCR_PINOKIO_COMPAT_PATCH_V1"
LEGACY_PATCH_MARKERS = ("PINOKIO_SEARCHABLE_PDF_V3_PATCH",)


def _write_if_changed(path: Path, original: str, updated: str) -> bool:
    if updated == original:
        return False
    backup = path.with_suffix(path.suffix + ".pinokio-original")
    if not backup.exists():
        backup.write_text(original, encoding="utf-8")
    path.write_text(updated, encoding="utf-8")
    return True


def patch_model_files(model_dir: str | Path) -> dict:
    """
    Apply small, idempotent compatibility fixes to the locally downloaded
    baidu/Unlimited-OCR remote-code files.

    Fixes:
      1) Explicit attention_mask + pad_token_id in generation kwargs.
      2) Mark CLIP vision position_ids as a non-persistent deterministic buffer,
         so Transformers does not report it as a missing learned checkpoint weight.

    The original source files are backed up once as *.pinokio-original.
    """
    model_dir = Path(model_dir)
    result = {
        "modeling_unlimitedocr": "missing",
        "deepencoder": "missing",
        "changed": False,
    }

    modeling = model_dir / "modeling_unlimitedocr.py"
    if modeling.exists():
        text = modeling.read_text(encoding="utf-8")
        if PATCH_MARKER in text or any(marker in text for marker in LEGACY_PATCH_MARKERS):
            result["modeling_unlimitedocr"] = "already patched"
        else:
            # The official code constructs three gen_kwargs dictionaries (infer
            # normal, infer eval, infer_multi). Add explicit padding/mask args to
            # every one. The input tensor is 1-D at this point, so the attention
            # mask shape becomes [1, seq_len], matching input_ids.unsqueeze(0).
            needle = "eos_token_id=tokenizer.eos_token_id,"
            replacement = (
                "eos_token_id=tokenizer.eos_token_id,\n"
                "                pad_token_id=(tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id),\n"
                "                attention_mask=torch.ones_like(input_ids, dtype=torch.long).unsqueeze(0).cuda(),"
            )
            count = text.count(needle)
            updated = text.replace(needle, replacement)
            if count:
                updated = f"# {PATCH_MARKER}: explicit generation mask/pad compatibility\n" + updated
                changed = _write_if_changed(modeling, text, updated)
                result["modeling_unlimitedocr"] = f"patched {count} generation block(s)" if changed else "unchanged"
                result["changed"] = result["changed"] or changed
            else:
                result["modeling_unlimitedocr"] = "pattern not found; left unchanged"

    deepencoder = model_dir / "deepencoder.py"
    if deepencoder.exists():
        text = deepencoder.read_text(encoding="utf-8")
        if PATCH_MARKER in text or any(marker in text for marker in LEGACY_PATCH_MARKERS):
            result["deepencoder"] = "already patched"
        else:
            pattern = re.compile(
                r"self\.register_buffer\(\s*"
                r"([\"'])position_ids\1\s*,\s*"
                r"(torch\.arange\(self\.num_positions\)\.expand\(\(1\s*,\s*-1\)\))\s*"
                r"\)",
                re.MULTILINE,
            )
            updated, count = pattern.subn(
                'self.register_buffer("position_ids", \\2, persistent=False)',
                text,
                count=1,
            )
            if count:
                updated = f"# {PATCH_MARKER}: deterministic position_ids is not checkpoint state\n" + updated
                changed = _write_if_changed(deepencoder, text, updated)
                result["deepencoder"] = "patched position_ids buffer" if changed else "unchanged"
                result["changed"] = result["changed"] or changed
            else:
                result["deepencoder"] = "pattern not found; left unchanged"

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        raise SystemExit("Usage: python model_patches.py <model_dir>")
    info = patch_model_files(sys.argv[1])
    for key, value in info.items():
        print(f"{key}: {value}")
