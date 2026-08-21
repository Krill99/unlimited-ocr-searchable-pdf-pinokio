from __future__ import annotations

import math
import unicodedata
from pathlib import Path

import pymupdf as fitz


def _safe_ratio(num: float, den: float, default: float = 1.0) -> float:
    return float(num) / float(den) if den else default


def _is_suspicious_char(ch: str) -> bool:
    if ch in {"\ufffd", "\ufeff"}:
        return True
    cat = unicodedata.category(ch)
    if cat == "Cc" and ch not in "\n\r\t":
        return True
    if cat == "Co":  # private-use glyphs are a common sign of broken PDF encoding
        return True
    return False


def analyze_page_text(page: fitz.Page, page_number: int | None = None) -> dict:
    """Heuristically classify the page's existing PDF text layer.

    The goal is not linguistic grading. We only need to decide whether the page
    already contains enough plausible extractable text that adding a second OCR
    layer would likely create duplicates.
    """
    try:
        text = page.get_text("text", sort=True) or ""
    except Exception:
        text = ""
    try:
        words = page.get_text("words", sort=True) or []
    except Exception:
        words = []

    visible_chars = [c for c in text if not c.isspace()]
    printable_chars = [c for c in visible_chars if c.isprintable()]
    alnum_chars = [c for c in visible_chars if c.isalnum()]
    suspicious_chars = [c for c in visible_chars if _is_suspicious_char(c)]

    word_tokens = []
    for w in words:
        try:
            token = str(w[4]).strip()
        except Exception:
            token = ""
        if token:
            word_tokens.append(token)

    plausible_words = [
        w for w in word_tokens
        if any(ch.isalnum() for ch in w)
        and sum(ch.isprintable() for ch in w) / max(1, len(w)) >= 0.8
    ]

    char_count = len(visible_chars)
    alnum_count = len(alnum_chars)
    word_count = len(word_tokens)
    printable_ratio = _safe_ratio(len(printable_chars), char_count)
    suspicious_ratio = _safe_ratio(len(suspicious_chars), char_count, 0.0)
    plausible_word_ratio = _safe_ratio(len(plausible_words), word_count, 0.0)

    # A short title-only page can still be a valid text PDF. Conversely, a page
    # with a couple of stray hidden OCR tokens should not be treated as "usable".
    quantity_score = min(1.0, alnum_count / 36.0) * 0.5 + min(1.0, word_count / 8.0) * 0.3
    quality_score = printable_ratio * 0.12 + plausible_word_ratio * 0.08
    penalty = min(0.35, suspicious_ratio * 2.5)
    score = max(0.0, min(1.0, quantity_score + quality_score - penalty))

    has_text = bool(word_count or alnum_count >= 2)
    enough_content = (
        (word_count >= 3 and alnum_count >= 10)
        or (word_count >= 2 and alnum_count >= 18)
        or (word_count >= 1 and alnum_count >= 28)
    )
    quality_ok = printable_ratio >= 0.90 and suspicious_ratio <= 0.08 and plausible_word_ratio >= 0.55
    usable = bool(has_text and enough_content and quality_ok and score >= 0.48)

    if usable:
        status = "usable"
    elif has_text:
        status = "partial"
    else:
        status = "none"

    return {
        "page": int(page_number or (page.number + 1)),
        "status": status,
        "has_text": has_text,
        "usable": usable,
        "word_count": word_count,
        "character_count": char_count,
        "alphanumeric_characters": alnum_count,
        "printable_ratio": round(printable_ratio, 3),
        "suspicious_ratio": round(suspicious_ratio, 3),
        "score": round(score, 3),
        "sample": " ".join(text.split())[:180],
    }


def analyze_pdf_text(input_pdf: str | Path) -> dict:
    """Inspect every page and summarize whether an existing text layer is usable."""
    doc = fitz.open(str(input_pdf))
    if doc.needs_pass:
        doc.close()
        raise ValueError("Password-protected PDFs are not supported.")

    pages: list[dict] = []
    try:
        for i, page in enumerate(doc):
            pages.append(analyze_page_text(page, i + 1))
    finally:
        doc.close()

    total = len(pages)
    usable_pages = sum(1 for p in pages if p["usable"])
    text_pages = sum(1 for p in pages if p["has_text"])
    partial_pages = sum(1 for p in pages if p["status"] == "partial")
    no_text_pages = sum(1 for p in pages if p["status"] == "none")

    if total == 0:
        overall = "empty"
    elif usable_pages == total:
        overall = "all_usable"
    elif usable_pages > 0:
        overall = "mixed"
    elif text_pages > 0:
        overall = "text_not_usable"
    else:
        overall = "no_text"

    return {
        "pages": total,
        "text_pages": text_pages,
        "usable_pages": usable_pages,
        "partial_pages": partial_pages,
        "no_text_pages": no_text_pages,
        "overall": overall,
        "has_any_text": text_pages > 0,
        "has_usable_text": usable_pages > 0,
        "page_results": pages,
        "heuristic": "Extractable PDF text is considered usable when it contains enough plausible words/characters and has a clean printable encoding.",
    }
