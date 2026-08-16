from __future__ import annotations

import html
import io
import os
import re
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Sequence

import pymupdf as fitz
from PIL import Image

try:
    from searchable_pdf import (
        _COORD_MAX,
        _clean_text,
        _font_spec_for_text,
        _normalized_bbox_to_rotated_page_rect,
        parse_ocr_layout_blocks,
    )
except ImportError:
    from app.searchable_pdf import (
        _COORD_MAX,
        _clean_text,
        _font_spec_for_text,
        _normalized_bbox_to_rotated_page_rect,
        parse_ocr_layout_blocks,
    )


# Regions that are better preserved as source-image crops than re-typeset.
_RASTER_TYPES = {
    "image", "figure", "fig", "photo", "picture", "illustration", "diagram",
    "chart", "plot", "graph", "logo", "barcode", "qr", "qrcode", "stamp",
    "seal", "signature",
}

_TABLE_TYPES = {"table"}
_MATH_TYPES = {"formula", "equation", "math", "equation_inline", "formula_inline"}

_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.I | re.S)
_MATH_DELIM_RE = re.compile(r"\\\((.+?)\\\)|\\\[(.+?)\\\]|\$\$(.+?)\$\$", re.S)


# ---------------------------- lightweight markup ----------------------------

def _strip_light_markup(text: str) -> str:
    text = _clean_text(text)
    if not text:
        return ""
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    return text.strip()


_LATEX_SYMBOLS = {
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
    r"\epsilon": "ε", r"\varepsilon": "ε", r"\zeta": "ζ", r"\eta": "η",
    r"\theta": "θ", r"\vartheta": "ϑ", r"\iota": "ι", r"\kappa": "κ",
    r"\lambda": "λ", r"\mu": "μ", r"\nu": "ν", r"\xi": "ξ", r"\omicron": "ο",
    r"\pi": "π", r"\varpi": "ϖ", r"\rho": "ρ", r"\varrho": "ϱ", r"\sigma": "σ", r"\varsigma": "ς", r"\tau": "τ",
    r"\upsilon": "υ", r"\phi": "φ", r"\varphi": "ϕ", r"\chi": "χ",
    r"\psi": "ψ", r"\omega": "ω", r"\Gamma": "Γ", r"\Delta": "Δ",
    r"\Theta": "Θ", r"\Lambda": "Λ", r"\Xi": "Ξ", r"\Pi": "Π",
    r"\Sigma": "Σ", r"\Upsilon": "Υ", r"\Phi": "Φ", r"\Psi": "Ψ", r"\Omega": "Ω",
    r"\infty": "∞", r"\pm": "±", r"\mp": "∓", r"\times": "×",
    r"\cdot": "·", r"\leq": "≤", r"\le": "≤", r"\geq": "≥",
    r"\ge": "≥", r"\neq": "≠", r"\approx": "≈", r"\sim": "∼",
    r"\propto": "∝", r"\rightarrow": "→", r"\to": "→",
    r"\leftarrow": "←", r"\leftrightarrow": "↔", r"\partial": "∂",
    r"\nabla": "∇", r"\sum": "∑", r"\prod": "∏", r"\int": "∫",
    r"\sqrt": "√", r"\circ": "°", r"\degree": "°",
}


def _replace_balanced_command(s: str, command: str, repl) -> str:
    """Replace simple command{...}; iterate to handle nested groups from inside out."""
    pattern = re.compile(re.escape(command) + r"\{([^{}]*)\}")
    for _ in range(12):
        new = pattern.sub(lambda m: repl(m.group(1)), s)
        if new == s:
            break
        s = new
    return s


def _latex_fragment_to_html(expr: str) -> str:
    """Convert common OCR LaTeX to safe HTML with real sub/superscripts.

    This is intentionally not a full TeX engine. Display equations use MathText below;
    this converter is for inline math inside paragraphs and table cells.
    """
    s = html.escape(_clean_text(expr or ""))
    s = s.replace(r"\(", "").replace(r"\)", "")
    s = s.replace(r"\[", "").replace(r"\]", "")
    s = s.replace("$$", "").replace("$", "")
    s = s.replace(r"\left", "").replace(r"\right", "")
    s = s.replace(r"\,", " ").replace(r"\;", " ").replace(r"\:", " ")
    s = s.replace(r"\!", "").replace(r"\quad", "  ").replace(r"\qquad", "    ")

    # Text/style wrappers.
    for cmd in (r"\text", r"\mathrm", r"\operatorname", r"\textrm", r"\mathit"):
        s = _replace_balanced_command(s, cmd, lambda x: x)
    s = _replace_balanced_command(s, r"\mathbf", lambda x: f"<b>{x}</b>")
    s = _replace_balanced_command(s, r"\textbf", lambda x: f"<b>{x}</b>")

    # Fractions: readable stacked layout within table / paragraph HTML.
    frac = re.compile(r"\\(?:d?frac)\{([^{}]*)\}\{([^{}]*)\}")
    for _ in range(10):
        new = frac.sub(
            lambda m: (
                '<span class="frac"><span class="num">'
                + m.group(1)
                + '</span>⁄<span class="den">'
                + m.group(2)
                + "</span></span>"
            ),
            s,
        )
        if new == s:
            break
        s = new

    # Square root. For inline use, the radical glyph plus grouped content is clearer
    # than exposing the TeX command.
    s = _replace_balanced_command(s, r"\sqrt", lambda x: f"√({x})")

    # Superscript / subscript groups and common one-character forms.
    for _ in range(8):
        new = re.sub(r"\^\{([^{}]*)\}", r"<sup>\1</sup>", s)
        new = re.sub(r"_\{([^{}]*)\}", r"<sub>\1</sub>", new)
        if new == s:
            break
        s = new
    s = re.sub(r"\^([A-Za-z0-9+\-°])", r"<sup>\1</sup>", s)
    s = re.sub(r"_([A-Za-z0-9])", r"<sub>\1</sub>", s)

    for token, symbol in sorted(_LATEX_SYMBOLS.items(), key=lambda kv: -len(kv[0])):
        s = s.replace(token, symbol)

    # Common TeX punctuation / escaped characters.
    s = s.replace(r"\%", "%").replace(r"\_", "_").replace(r"\&", "&amp;")
    s = s.replace(r"\\", "<br>")

    # Remove remaining formatting-only commands instead of exposing backslashes.
    s = re.sub(r"\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle)\b", "", s)
    s = re.sub(r"\\[A-Za-z]+", "", s)
    s = s.replace("{", "").replace("}", "")
    return s.strip()


def _text_with_inline_math_to_html(text: str) -> str:
    """Convert normal OCR text to safe HTML while typesetting inline LaTeX."""
    raw = _strip_light_markup(text)
    if not raw:
        return ""

    pieces: list[str] = []
    pos = 0
    for m in _MATH_DELIM_RE.finditer(raw):
        before = raw[pos:m.start()]
        if before:
            pieces.append(html.escape(before).replace("\n", "<br>"))
        expr = next((g for g in m.groups() if g is not None), "")
        pieces.append(f'<span class="inline-math">{_latex_fragment_to_html(expr)}</span>')
        pos = m.end()
    tail = raw[pos:]
    if tail:
        pieces.append(html.escape(tail).replace("\n", "<br>"))

    # Some OCR outputs contain bare TeX without explicit \(...\) delimiters.
    result = "".join(pieces) if pieces else html.escape(raw).replace("\n", "<br>")
    if re.search(r"\\[A-Za-z]+|\^\{|_\{", result):
        # The string is already escaped, but our converter expects raw; unescape once.
        result = _latex_fragment_to_html(html.unescape(result))
    return result


# ------------------------------- HTML tables --------------------------------

class _OCRTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict]] = []
        self.current_row: list[dict] | None = None
        self.current_cell: dict | None = None

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            if self.current_row is not None and self.current_row:
                self.rows.append(self.current_row)
            self.current_row = []
        elif tag in {"td", "th"}:
            if self.current_row is None:
                self.current_row = []
            amap = {k.lower(): v for k, v in attrs}
            self.current_cell = {
                "header": tag == "th",
                "text": [],
                "colspan": max(1, _safe_int(amap.get("colspan"), 1)),
                "rowspan": max(1, _safe_int(amap.get("rowspan"), 1)),
            }
        elif tag == "br" and self.current_cell is not None:
            self.current_cell["text"].append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self.current_cell is not None:
            if self.current_row is None:
                self.current_row = []
            self.current_row.append(self.current_cell)
            self.current_cell = None
        elif tag == "tr":
            if self.current_row is not None:
                if self.current_cell is not None:
                    self.current_row.append(self.current_cell)
                    self.current_cell = None
                if self.current_row:
                    self.rows.append(self.current_row)
                self.current_row = None

    def handle_data(self, data):
        if self.current_cell is not None:
            self.current_cell["text"].append(data)

    def finish(self):
        if self.current_cell is not None:
            if self.current_row is None:
                self.current_row = []
            self.current_row.append(self.current_cell)
            self.current_cell = None
        if self.current_row:
            self.rows.append(self.current_row)
            self.current_row = None


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_table_html(raw: str) -> tuple[str | None, str, str]:
    """Return safe rendered table HTML plus text before/after the first table."""
    m = _TABLE_RE.search(raw or "")
    if not m:
        return None, raw or "", ""

    parser = _OCRTableParser()
    try:
        parser.feed(m.group(0))
        parser.close()
        parser.finish()
    except Exception:
        return None, raw[:m.start()], raw[m.end():]

    if not parser.rows:
        return None, raw[:m.start()], raw[m.end():]

    parts = ['<table class="ocr-table">']
    for row in parser.rows:
        parts.append("<tr>")
        for cell in row:
            tag = "th" if cell["header"] else "td"
            attrs = ""
            if cell["colspan"] > 1:
                attrs += f' colspan="{cell["colspan"]}"'
            if cell["rowspan"] > 1:
                attrs += f' rowspan="{cell["rowspan"]}"'
            cell_text = "".join(cell["text"]).strip()
            rendered = _text_with_inline_math_to_html(cell_text)
            parts.append(f"<{tag}{attrs}>{rendered}</{tag}>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts), raw[:m.start()], raw[m.end():]


_BASE_HTML_CSS = r"""
body { margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif; color: #000; }
p { margin: 0 0 3pt 0; }
.inline-math { font-family: 'Times New Roman', serif; }
sup, sub { font-size: 72%; line-height: 0; }
.frac { font-family: 'Times New Roman', serif; white-space: nowrap; }
.frac .num { vertical-align: super; font-size: 75%; }
.frac .den { vertical-align: sub; font-size: 75%; }
.ocr-table { border-collapse: collapse; width: 100%; table-layout: auto; font-size: 9pt; }
.ocr-table td, .ocr-table th { border: 0.7pt solid #111; padding: 2pt 3pt; vertical-align: middle; }
.ocr-table th { font-weight: bold; background-color: #f3f3f3; }
.caption { margin-top: 3pt; text-align: center; font-size: 8pt; }
"""


def _insert_html_block(page: fitz.Page, rect: fitz.Rect, body_html: str, *, css: str = "", scale_low: float = 0.25) -> bool:
    if not body_html or rect.is_empty or rect.width <= 1 or rect.height <= 1:
        return False
    try:
        spare, scale = page.insert_htmlbox(
            rect,
            body_html,
            css=_BASE_HTML_CSS + "\n" + css,
            scale_low=scale_low,
            overlay=True,
        )
        return spare >= 0 and scale > 0
    except Exception:
        return False


def _insert_table_block(page: fitz.Page, rect: fitz.Rect, raw: str) -> bool:
    table_html, before, after = _safe_table_html(raw)
    if not table_html:
        return False
    body: list[str] = []
    if _strip_light_markup(before):
        body.append(f"<p>{_text_with_inline_math_to_html(before)}</p>")
    body.append(table_html)
    if _strip_light_markup(after):
        body.append(f'<div class="caption">{_text_with_inline_math_to_html(after)}</div>')
    return _insert_html_block(page, rect, "".join(body), scale_low=0.18)


# ------------------------------- equations ----------------------------------

def _extract_display_math(text: str) -> str:
    s = _strip_light_markup(text).strip()
    # Strip standard delimiters if they wrap the entire block.
    for left, right in ((r"\[", r"\]"), (r"\(", r"\)"), ("$$", "$$"), ("$", "$")):
        if s.startswith(left) and s.endswith(right) and len(s) >= len(left) + len(right):
            s = s[len(left): len(s) - len(right)].strip()
            break
    s = re.sub(r"\\begin\{(?:equation\*?|aligned|align\*?)\}", "", s)
    s = re.sub(r"\\end\{(?:equation\*?|aligned|align\*?)\}", "", s)
    return s.strip()


def _looks_like_display_math(text: str, category: str) -> bool:
    if category in _MATH_TYPES:
        return True
    s = _strip_light_markup(text)
    if not s:
        return False
    # Entire block wrapped in a math delimiter.
    if (s.startswith(r"\[") and s.endswith(r"\]")) or (s.startswith("$$") and s.endswith("$$")):
        return True
    # A bare short TeX expression (without prose) can also be a formula region.
    markers = len(re.findall(r"\\(?:frac|sqrt|sum|int|alpha|beta|gamma|Delta|partial|cdot|times)\b|\^\{|_\{", s))
    prose_words = len(re.findall(r"\b[A-Za-z]{3,}\b", re.sub(r"\\[A-Za-z]+", "", s)))
    starts_math = bool(re.match(r"^\s*(?:\\(?:frac|sqrt|sum|int)|[A-Za-z0-9_{}^()+\-])", s))
    return markers >= 1 and prose_words <= 1 and starts_math and " " not in re.sub(r"\\[A-Za-z]+", "", s).strip()[:12]


def _mathtext_pdf_bytes(expr: str) -> bytes:
    """Render LaTeX-like math to a tiny vector PDF using Matplotlib MathText."""
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    expr = _extract_display_math(expr)
    if not expr:
        raise ValueError("empty equation")

    # MathText understands a broad TeX subset. Keep ordinary text within \mathrm
    # where OCR provided \text{} because \text support varies by expression.
    expr = re.sub(r"\\text\{([^{}]*)\}", r"\\mathrm{\1}", expr)
    expr = expr.replace(r"\dfrac", r"\frac")

    fig = plt.figure(figsize=(8, 1.5), dpi=100)
    fig.patch.set_alpha(0)
    fig.text(0.5, 0.5, f"${expr}$", ha="center", va="center", fontsize=24, color="black")
    out = io.BytesIO()
    fig.savefig(out, format="pdf", bbox_inches="tight", pad_inches=0.02, transparent=True)
    plt.close(fig)
    return out.getvalue()


def _insert_equation_block(page: fitz.Page, rect: fitz.Rect, text: str) -> bool:
    try:
        pdf_bytes = _mathtext_pdf_bytes(text)
        src = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            # Preserve equation aspect ratio and center it inside the OCR region.
            page.show_pdf_page(rect, src, 0, keep_proportion=True, overlay=True)
            return True
        finally:
            src.close()
    except Exception:
        # A readable HTML fallback is still much better than exposing raw TeX.
        human = _latex_fragment_to_html(_extract_display_math(text))
        return _insert_html_block(
            page,
            rect,
            f'<div style="font-family: Times New Roman, serif; text-align:center; font-size:15pt;">{human}</div>',
            scale_low=0.2,
        )


# ----------------------------- source image crops ----------------------------

def _source_pixel_box(bbox: tuple[float, float, float, float], width: int, height: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox
    x0 = max(0.0, min(_COORD_MAX, x0)); y0 = max(0.0, min(_COORD_MAX, y0))
    x1 = max(0.0, min(_COORD_MAX, x1)); y1 = max(0.0, min(_COORD_MAX, y1))
    px0 = max(0, int((x0 / _COORD_MAX) * width) - 1)
    py0 = max(0, int((y0 / _COORD_MAX) * height) - 1)
    px1 = min(width, int(((x1 / _COORD_MAX) * width) + 0.9999) + 1)
    py1 = min(height, int(((y1 / _COORD_MAX) * height) + 0.9999) + 1)
    return px0, py0, max(px0 + 1, px1), max(py0 + 1, py1)


def _open_source_image(source: dict, original_page: fitz.Page) -> Image.Image:
    path = str((source or {}).get("path") or "")
    if path and os.path.isfile(path):
        with Image.open(path) as im:
            return im.convert("RGB").copy()
    dpi = int((source or {}).get("dpi") or 200)
    pix = original_page.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _insert_crop(page: fitz.Page, rect: fitz.Rect, source_image: Image.Image, bbox: tuple[float, float, float, float]) -> bool:
    if rect.is_empty or rect.width <= 0.5 or rect.height <= 0.5:
        return False
    try:
        px = _source_pixel_box(bbox, source_image.width, source_image.height)
        crop = source_image.crop(px)
        buf = io.BytesIO(); crop.save(buf, format="PNG", optimize=True)
        page.insert_image(rect, stream=buf.getvalue(), keep_proportion=False, overlay=True)
        return True
    except Exception:
        return False


# ------------------------------- normal text --------------------------------

def _fits_textbox(page_rect: fitz.Rect, rect: fitz.Rect, text: str, font: fitz.Font, fontsize: float, lineheight: float) -> tuple[bool, fitz.TextWriter]:
    writer = fitz.TextWriter(page_rect)
    try:
        overflow = writer.fill_textbox(rect, text, font=font, fontsize=fontsize, lineheight=lineheight, align=0, warn=False)
        return len(overflow) == 0, writer
    except Exception:
        return False, writer


def _insert_visible_text_block(page: fitz.Page, rect: fitz.Rect, text: str, category: str) -> bool:
    """Render visible selectable text; inline LaTeX is converted to real math typography."""
    text = _strip_light_markup(text)
    if not text or rect.is_empty or rect.width <= 1 or rect.height <= 1:
        return False

    # If inline math exists, HTML insertion gives proper sub/superscripts and symbols.
    if _MATH_DELIM_RE.search(text) or re.search(r"\\[A-Za-z]+|\^\{|_\{", text):
        body = _text_with_inline_math_to_html(text)
        category = (category or "text").lower()
        size = "13pt" if category in {"title", "header", "section", "heading"} else "10pt"
        if category in {"caption", "footnote", "footer", "reference"}:
            size = "8pt"
        css = f"body {{ font-size: {size}; line-height: 1.15; }}"
        if _insert_html_block(page, rect, body, css=css, scale_low=0.2):
            return True

    _fontname, font = _font_spec_for_text(page, text)
    category = (category or "text").lower()
    if category in {"title", "header", "section", "heading"}:
        high = min(36.0, max(7.0, rect.height * 0.95))
    elif category in {"caption", "footnote", "footer", "reference"}:
        high = min(14.0, max(5.0, rect.height * 0.8))
    else:
        high = min(22.0, max(6.0, rect.height * 0.9))

    low = 2.5; lineheight = 1.05; best_writer = None; best_size = 0.0
    for _ in range(13):
        mid = (low + high) / 2.0
        fits, writer = _fits_textbox(page.rect, rect, text, font, mid, lineheight)
        if fits:
            best_writer = writer; best_size = mid; low = mid
        else:
            high = mid
    if best_writer is None:
        fits, writer = _fits_textbox(page.rect, rect, text, font, 2.25, 1.0)
        if not fits:
            return False
        best_writer = writer; best_size = 2.25
    try:
        best_writer.write_text(page, color=(0, 0, 0), opacity=1, overlay=True, render_mode=0)
        return best_size > 0
    except Exception:
        return False


def _insert_flow_fallback(page: fitz.Page, texts: list[str]) -> int:
    combined = "\n\n".join(_strip_light_markup(t) for t in texts if _strip_light_markup(t))
    if not combined:
        return 0
    margin = max(18.0, min(page.rect.width, page.rect.height) * 0.04)
    rect = fitz.Rect(margin, margin, page.rect.width - margin, page.rect.height - margin)
    if _insert_html_block(page, rect, _text_with_inline_math_to_html(combined), css="body{font-size:10pt;line-height:1.2;}", scale_low=0.2):
        return 1
    _fontname, font = _font_spec_for_text(page, combined)
    for size in (11.0, 10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0):
        fits, writer = _fits_textbox(page.rect, rect, combined, font, size, 1.1)
        if fits:
            writer.write_text(page, color=(0, 0, 0), opacity=1, overlay=True, render_mode=0)
            return 1
    return 0


# --------------------------------- builder -----------------------------------

def build_reconstructed_pdf(
    input_pdf: str,
    page_ocr_texts: Iterable[str],
    output_pdf: str | None = None,
    page_sources: Sequence[dict] | None = None,
) -> tuple[str, dict]:
    """Build a new born-digital PDF from Unlimited-OCR layout output.

    Structured reconstruction:
      * normal blocks -> real visible selectable PDF text
      * inline LaTeX -> proper sub/superscripts and mathematical symbols
      * display formulas -> typeset vector equations
      * HTML tables -> actual bordered PDF tables with selectable cell text
      * images / figures / charts -> exact source-image crops
    """
    page_ocr_texts = list(page_ocr_texts or [])
    page_sources = list(page_sources or [])

    if output_pdf is None:
        stem = os.path.splitext(os.path.basename(input_pdf))[0]
        out_dir = tempfile.mkdtemp(prefix="reconstructed_pdf_")
        output_pdf = os.path.join(out_dir, f"{stem}_reconstructed.pdf")

    source_doc = fitz.open(input_pdf)
    if source_doc.needs_pass:
        source_doc.close(); raise ValueError("Password-protected PDFs are not supported.")

    out = fitz.open()
    stats = {
        "pages": len(source_doc), "pages_with_ocr": 0,
        "visible_text_blocks": 0, "table_blocks": 0, "equation_blocks": 0,
        "image_blocks": 0, "image_fallback_blocks": 0,
        "unpositioned_text_blocks": 0, "full_page_fallbacks": 0,
        "geometry": "structured-reconstruction",
    }

    try:
        for page_index, original_page in enumerate(source_doc):
            display_rect = original_page.rect
            page = out.new_page(width=display_rect.width, height=display_rect.height)
            raw = page_ocr_texts[page_index] if page_index < len(page_ocr_texts) else ""
            blocks = parse_ocr_layout_blocks(raw)
            if blocks: stats["pages_with_ocr"] += 1

            src = page_sources[page_index] if page_index < len(page_sources) else {}
            source_size = None
            try:
                sw, sh = int((src or {}).get("width", 0)), int((src or {}).get("height", 0))
                if sw > 0 and sh > 0: source_size = (sw, sh)
            except Exception:
                source_size = None

            source_image = None
            positioned = [b for b in blocks if b.get("bbox")]
            unpositioned_texts = [b.get("text", "") for b in blocks if not b.get("bbox") and b.get("text")]
            stats["unpositioned_text_blocks"] += len(unpositioned_texts)

            # Raster regions first; text/tables/formulas go above them.
            for block in positioned:
                category = str(block.get("type") or "text").lower()
                bbox = block.get("bbox"); text = block.get("text", "")
                should_raster = category in _RASTER_TYPES or not str(text or "").strip()
                if not should_raster: continue
                if source_image is None: source_image = _open_source_image(src or {}, original_page)
                target = _normalized_bbox_to_rotated_page_rect(original_page, bbox, source_size)
                if _insert_crop(page, target, source_image, bbox): stats["image_blocks"] += 1

            for block in positioned:
                category = str(block.get("type") or "text").lower()
                bbox = block.get("bbox"); text = str(block.get("text") or "")
                if category in _RASTER_TYPES or not text.strip(): continue
                target = _normalized_bbox_to_rotated_page_rect(original_page, bbox, source_size)

                rendered = False
                # Tables are recognized either by the region type or literal HTML markup.
                if category in _TABLE_TYPES or _TABLE_RE.search(text):
                    rendered = _insert_table_block(page, target, text)
                    if rendered: stats["table_blocks"] += 1
                # Formula/equation regions become typeset vector math.
                elif _looks_like_display_math(text, category):
                    rendered = _insert_equation_block(page, target, text)
                    if rendered: stats["equation_blocks"] += 1
                else:
                    rendered = _insert_visible_text_block(page, target, text, category)
                    if rendered: stats["visible_text_blocks"] += 1

                if not rendered:
                    if source_image is None: source_image = _open_source_image(src or {}, original_page)
                    if _insert_crop(page, target, source_image, bbox): stats["image_fallback_blocks"] += 1

            if not positioned and unpositioned_texts:
                _insert_flow_fallback(page, unpositioned_texts)
            if not positioned and not unpositioned_texts:
                if source_image is None: source_image = _open_source_image(src or {}, original_page)
                buf = io.BytesIO(); source_image.save(buf, format="JPEG", quality=92)
                page.insert_image(page.rect, stream=buf.getvalue(), keep_proportion=False)
                stats["full_page_fallbacks"] += 1

        Path(output_pdf).parent.mkdir(parents=True, exist_ok=True)
        out.save(output_pdf, garbage=3, deflate=True)
    finally:
        out.close(); source_doc.close()

    return output_pdf, stats
