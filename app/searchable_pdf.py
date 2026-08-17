from __future__ import annotations

import ast
import os
import re
import tempfile
from typing import Iterable, Sequence

import numpy as np
from PIL import Image
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



_SKIP_IMAGE_REFINEMENT_TYPES = {
    "image", "figure", "fig", "photo", "picture", "illustration", "diagram",
    "chart", "plot", "graph", "logo", "barcode", "qr", "stamp", "seal",
    "signature", "table", "formula", "equation", "math", "chemical_formula",
}


def _normalized_bbox_to_pixel_rect(
    bbox: tuple[float, float, float, float],
    source_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """Map a 0..999 Unlimited-OCR bbox to clipped source-image pixel coordinates."""
    width, height = source_size
    x0, y0, x1, y1 = bbox
    x0 = max(0.0, min(_COORD_MAX, x0))
    y0 = max(0.0, min(_COORD_MAX, y0))
    x1 = max(0.0, min(_COORD_MAX, x1))
    y1 = max(0.0, min(_COORD_MAX, y1))
    px0 = max(0, min(width - 1, int(round(x0 / _COORD_MAX * width))))
    py0 = max(0, min(height - 1, int(round(y0 / _COORD_MAX * height))))
    px1 = max(px0 + 1, min(width, int(round(x1 / _COORD_MAX * width))))
    py1 = max(py0 + 1, min(height, int(round(y1 / _COORD_MAX * height))))
    return px0, py0, px1, py1


def _pixel_rect_to_rotated_page_rect(
    page: fitz.Page,
    pixel_rect: tuple[int, int, int, int],
    source_size: tuple[int, int],
) -> fitz.Rect:
    """Map a source-image pixel rectangle to the displayed / rotated PDF page."""
    width, height = source_size
    x0, y0, x1, y1 = pixel_rect
    page_rect = page.rect
    return fitz.Rect(
        page_rect.x0 + (x0 / width) * page_rect.width,
        page_rect.y0 + (y0 / height) * page_rect.height,
        page_rect.x0 + (x1 / width) * page_rect.width,
        page_rect.y0 + (y1 / height) * page_rect.height,
    )


def _otsu_threshold(gray: np.ndarray) -> int:
    """Small dependency-free Otsu threshold for document line segmentation."""
    flat = np.asarray(gray, dtype=np.uint8).ravel()
    if flat.size == 0:
        return 127
    hist = np.bincount(flat, minlength=256).astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return 127
    prob = hist / total
    omega = np.cumsum(prob)
    mu = np.cumsum(prob * np.arange(256, dtype=np.float64))
    mu_t = mu[-1]
    denom = omega * (1.0 - omega)
    sigma = np.zeros(256, dtype=np.float64)
    valid = denom > 1e-12
    sigma[valid] = ((mu_t * omega[valid] - mu[valid]) ** 2) / denom[valid]
    return int(np.argmax(sigma))


def _true_runs(flags: np.ndarray) -> list[tuple[int, int]]:
    """Return [start,end) runs of True values."""
    flags = np.asarray(flags, dtype=bool)
    if flags.size == 0:
        return []
    padded = np.pad(flags.astype(np.int8), (1, 1))
    edges = np.diff(padded)
    starts = np.where(edges == 1)[0]
    ends = np.where(edges == -1)[0]
    return [(int(a), int(b)) for a, b in zip(starts, ends) if b > a]


def _merge_runs(runs: list[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    if not runs:
        return []
    merged = [list(runs[0])]
    for start, end in runs[1:]:
        if start - merged[-1][1] <= max_gap:
            merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(int(a), int(b)) for a, b in merged]


def _foreground_mask(gray: np.ndarray) -> np.ndarray:
    """Return a conservative foreground mask for dark-on-light or light-on-dark text."""
    arr = np.asarray(gray, dtype=np.uint8)
    if arr.size == 0:
        return np.zeros_like(arr, dtype=bool)
    threshold = _otsu_threshold(arr)
    dark = arr <= threshold
    light = arr >= threshold
    dark_fraction = float(dark.mean())
    light_fraction = float(light.mean())

    # Documents are normally dark-on-light. Switch polarity only when the dark
    # mask would classify most of the region as foreground and the light mask is
    # substantially sparser (e.g. white text on a dark banner).
    if dark_fraction > 0.55 and 0.001 < light_fraction < dark_fraction:
        mask = light
    else:
        mask = dark

    # Ignore tiny isolated noise by requiring some local row support later.
    return mask


def _detect_word_runs(line_mask: np.ndarray, line_height: int) -> list[tuple[int, int]]:
    """Detect likely word x-runs from ink, merging character-size gaps only."""
    if line_mask.size == 0:
        return []
    col_active = line_mask.sum(axis=0) >= 1
    glyph_runs = _true_runs(col_active)
    if not glyph_runs:
        return []
    # At 200 dpi this is ~2-5 px for ordinary body text: enough to join letters
    # and punctuation, but normally smaller than an inter-word space.
    char_gap = max(1, int(round(max(3, line_height) * 0.20)))
    words = _merge_runs(glyph_runs, char_gap)
    return [(a, b) for a, b in words if b - a >= 1]


def _detect_text_line_geometry(
    source_image: Image.Image,
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """
    Detect real printed line rectangles inside one Unlimited-OCR block.

    This uses only the source pixels already sent to Unlimited-OCR: no second OCR
    engine is introduced. Horizontal projection finds text rows; per-row x bounds
    tighten each selection rectangle to the actual ink rather than the whole block.
    """
    width, height = source_image.size
    px0, py0, px1, py1 = _normalized_bbox_to_pixel_rect(bbox, (width, height))
    crop = np.asarray(source_image.crop((px0, py0, px1, py1)).convert("L"), dtype=np.uint8)
    if crop.ndim != 2 or crop.shape[0] < 3 or crop.shape[1] < 3:
        return []

    mask = _foreground_mask(crop)
    h, w = mask.shape
    # Require a tiny but meaningful amount of ink on a row. This avoids isolated
    # dust while retaining short headings / narrow columns.
    min_row_ink = max(2, int(round(w * 0.003)))
    row_active = mask.sum(axis=1) >= min_row_ink

    # Close small vertical gaps caused by thin glyphs / antialiasing, then form lines.
    raw_runs = _true_runs(row_active)
    line_gap = max(1, int(round(h * 0.012)))
    runs = _merge_runs(raw_runs, line_gap)
    min_line_h = max(2, int(round(h * 0.012)))
    runs = [(a, b) for a, b in runs if b - a >= min_line_h]
    if not runs or len(runs) > 80:
        return []

    # A single very tall ink band in a tall/narrow block is usually vertical text,
    # a graphic, or a bad threshold rather than a normal horizontal reading line.
    if len(runs) == 1 and (runs[0][1] - runs[0][0]) > h * 0.60 and h > w * 1.15:
        return []

    # Reject obvious non-text segmentation: text lines should not cover almost the
    # entire block vertically as dozens of tiny bands.
    coverage = sum(b - a for a, b in runs) / max(1, h)
    if len(runs) > 8 and coverage > 0.85:
        return []

    pad_y = max(1, int(round(h * 0.004)))
    out: list[dict] = []
    for start, end in runs:
        y0 = max(0, start - pad_y)
        y1 = min(h, end + pad_y)
        band = mask[y0:y1, :]
        cols = np.where(band.sum(axis=0) > 0)[0]
        if cols.size == 0:
            continue
        lx0 = max(0, int(cols[0]) - 1)
        lx1 = min(w, int(cols[-1]) + 2)
        if lx1 - lx0 < 2:
            continue

        word_runs = _detect_word_runs(band[:, lx0:lx1], y1 - y0)
        word_rects = [
            (px0 + lx0 + a, py0 + y0, px0 + lx0 + b, py0 + y1)
            for a, b in word_runs
        ]
        out.append({
            "pixel_rect": (px0 + lx0, py0 + y0, px0 + lx1, py0 + y1),
            "width": float(lx1 - lx0),
            "word_rects": word_rects,
        })
    return out


def _plain_search_text(text: str) -> str:
    """Remove lightweight Markdown decoration that should not become PDF search text."""
    text = _clean_text(text)
    # Markdown images disappear; links keep their visible label.
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    # Keep list numbers / bullets because they can be visibly present in the scan.
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return text.strip()


def _allocate_text_to_lines(text: str, widths: list[float], font: fitz.Font) -> list[str]:
    """Split one OCR paragraph into the number of physical lines detected in pixels."""
    n_lines = len(widths)
    if n_lines <= 0:
        return []
    cleaned = _plain_search_text(text)
    explicit = [re.sub(r"\s+", " ", ln).strip() for ln in cleaned.splitlines() if ln.strip()]
    if n_lines == 1:
        return [re.sub(r"\s+", " ", cleaned).strip()]
    if len(explicit) == n_lines:
        return explicit

    words = re.findall(r"\S+", " ".join(explicit) if explicit else cleaned)
    if not words:
        return []
    if len(words) <= n_lines:
        return words + [""] * (n_lines - len(words))

    def text_width(value: str) -> float:
        try:
            return max(0.01, float(font.text_length(value, fontsize=1.0)))
        except Exception:
            return max(0.01, float(len(value)))

    word_width = [text_width(w) for w in words]
    space_width = text_width(" ")
    prefix = [0.0]
    for value in word_width:
        prefix.append(prefix[-1] + value)

    def group_width(i: int, j: int) -> float:
        # words i:j
        return (prefix[j] - prefix[i]) + max(0, j - i - 1) * space_width

    safe_widths = [max(1.0, float(v)) for v in widths]
    total_natural = group_width(0, len(words))
    total_target = sum(safe_widths)
    expected = [total_natural * w / total_target for w in safe_widths]

    m = len(words)
    inf = float("inf")
    dp = [[inf] * (m + 1) for _ in range(n_lines + 1)]
    prev = [[-1] * (m + 1) for _ in range(n_lines + 1)]
    dp[0][0] = 0.0

    for line_idx in range(1, n_lines + 1):
        min_j = line_idx
        max_j = m - (n_lines - line_idx)
        for j in range(min_j, max_j + 1):
            i_min = line_idx - 1
            for i in range(i_min, j):
                if dp[line_idx - 1][i] == inf:
                    continue
                gw = group_width(i, j)
                target = max(0.01, expected[line_idx - 1])
                mismatch = (gw - target) / target
                cost = mismatch * mismatch
                # Strongly discourage gross overfill because that usually means a
                # visually short line was assigned too many words.
                if gw > target * 1.35:
                    cost += ((gw / target) - 1.35) * 2.0
                cand = dp[line_idx - 1][i] + cost
                if cand < dp[line_idx][j]:
                    dp[line_idx][j] = cand
                    prev[line_idx][j] = i

    if prev[n_lines][m] < 0:
        # Conservative greedy fallback.
        result, start = [], 0
        for line_idx in range(n_lines):
            remaining_lines = n_lines - line_idx
            if remaining_lines == 1:
                result.append(" ".join(words[start:]))
                break
            target = expected[line_idx]
            end = start + 1
            while end < m - (remaining_lines - 1):
                if group_width(start, end + 1) > target and end > start:
                    break
                end += 1
            result.append(" ".join(words[start:end]))
            start = end
        return result

    cuts = [m]
    j = m
    for line_idx in range(n_lines, 0, -1):
        i = prev[line_idx][j]
        cuts.append(i)
        j = i
    cuts.reverse()
    return [" ".join(words[cuts[k]:cuts[k + 1]]) for k in range(n_lines)]


def _insert_image_refined_block(
    page: fitz.Page,
    source_image: Image.Image,
    bbox: tuple[float, float, float, float],
    text: str,
    category: str,
) -> tuple[int, int, bool]:
    """Insert image-guided lines, with confidence-based word geometry when exact."""
    if category in _SKIP_IMAGE_REFINEMENT_TYPES:
        return 0, 0, False
    geometry = _detect_text_line_geometry(source_image, bbox)
    if not geometry:
        return 0, 0, False

    fontname, font = _font_spec_for_text(page, text)
    line_texts = _allocate_text_to_lines(text, [g["width"] for g in geometry], font)
    if not line_texts or not any(line_texts):
        return 0, 0, False

    source_size = source_image.size
    inserted_lines = 0
    inserted_words = 0
    for line_text, item in zip(line_texts, geometry):
        line_text = re.sub(r"\s+", " ", line_text).strip()
        if not line_text:
            continue
        words = re.findall(r"\S+", line_text)
        word_rects = item.get("word_rects") or []

        # Word-level placement is only used with an exact, credible segmentation.
        # Otherwise one precisely fitted line preserves phrase search more reliably.
        use_words = (
            2 <= len(words) <= 40
            and len(word_rects) == len(words)
            and all((r[2] - r[0]) >= 2 for r in word_rects)
        )
        if use_words:
            ok_count = 0
            for word, px_rect in zip(words, word_rects):
                display_rect = _pixel_rect_to_rotated_page_rect(page, px_rect, source_size)
                if _insert_exact_invisible_line(page, display_rect, word, fontname, font):
                    ok_count += 1
            if ok_count == len(words):
                inserted_words += ok_count
                inserted_lines += 1
                continue
            # If a font/glyph issue interrupted word insertion, do not duplicate the
            # successfully inserted words by adding the full line again.
            if ok_count:
                inserted_words += ok_count
                inserted_lines += 1
                continue

        display_rect = _pixel_rect_to_rotated_page_rect(page, item["pixel_rect"], source_size)
        if _insert_exact_invisible_line(page, display_rect, line_text, fontname, font):
            inserted_lines += 1

    return inserted_lines, inserted_words, inserted_lines > 0


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
        "image_refined_blocks": 0,
        "image_refined_lines": 0,
        "word_refined_words": 0,
        "box_fit_blocks": 0,
        "geometry": "image-guided-line-fit",
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

        source_image: Image.Image | None = None
        if page_index < len(page_sources):
            src_path = str((page_sources[page_index] or {}).get("path") or "")
            if src_path and os.path.exists(src_path):
                try:
                    with Image.open(src_path) as source_file:
                        source_image = source_file.convert("RGB")
                    # Use the actual image dimensions as source-of-truth if registry
                    # metadata is missing or stale.
                    source_size = source_image.size
                except Exception:
                    source_image = None

        fallback_texts: list[str] = []
        for block in blocks:
            text = block.get("text", "")
            bbox = block.get("bbox")
            category = str(block.get("type") or "text").lower()
            if bbox:
                refined = False
                if source_image is not None:
                    refined_lines, refined_words, refined = _insert_image_refined_block(
                        page, source_image, bbox, text, category
                    )
                    if refined:
                        stats["positioned_blocks"] += 1
                        stats["positioned_lines"] += refined_lines
                        stats["image_refined_blocks"] += 1
                        stats["image_refined_lines"] += refined_lines
                        stats["word_refined_words"] += refined_words
                if not refined:
                    display_rect = _normalized_bbox_to_rotated_page_rect(page, bbox, source_size)
                    inserted_lines = _insert_invisible_block(page, display_rect, text)
                    if inserted_lines:
                        stats["positioned_blocks"] += 1
                        stats["positioned_lines"] += inserted_lines
                        stats["box_fit_blocks"] += 1
                    else:
                        fallback_texts.append(text)
            else:
                fallback_texts.append(text)

        if source_image is not None:
            try:
                source_image.close()
            except Exception:
                pass

        if fallback_texts:
            joined = "\n".join(t for t in fallback_texts if t.strip())
            if _insert_unpositioned_fallback(page, joined):
                stats["fallback_blocks"] += len(fallback_texts)

    # Save as a new PDF; the original file is never overwritten.
    doc.save(output_pdf, garbage=3, deflate=True)
    doc.close()
    return output_pdf, stats
