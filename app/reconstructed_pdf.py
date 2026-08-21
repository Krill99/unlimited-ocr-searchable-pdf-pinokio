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
import numpy as np
from PIL import Image, ImageFont

try:
    from searchable_pdf import (
        _COORD_MAX,
        _clean_text,
        _font_spec_for_text,
        _normalized_bbox_to_rotated_page_rect,
        _normalized_bbox_to_pixel_rect,
        _pixel_rect_to_rotated_page_rect,
        _detect_text_line_geometry,
        _allocate_text_to_lines,
        _foreground_mask,
        parse_ocr_layout_blocks,
    )
except ImportError:
    from app.searchable_pdf import (
        _COORD_MAX,
        _clean_text,
        _font_spec_for_text,
        _normalized_bbox_to_rotated_page_rect,
        _normalized_bbox_to_pixel_rect,
        _pixel_rect_to_rotated_page_rect,
        _detect_text_line_geometry,
        _allocate_text_to_lines,
        _foreground_mask,
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
    r"\leftarrow": "←", r"\leftrightarrow": "↔", r"\Rightarrow": "⇒",
    r"\Leftarrow": "⇐", r"\Leftrightarrow": "⇔", r"\mapsto": "↦", r"\partial": "∂",
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
    _raw_expr = _clean_text(expr or "").replace("\n", "ZZZZUOCRBRZZZZ")
    s = html.escape(_raw_expr)
    s = s.replace(r"\(", "").replace(r"\)", "")
    s = s.replace(r"\[", "").replace(r"\]", "")
    s = s.replace("$$", "").replace("$", "")
    s = s.replace(r"\left", "").replace(r"\right", "")
    s = s.replace(r"\,", " ").replace(r"\;", " ").replace(r"\:", " ")
    s = s.replace(r"\!", "").replace(r"\quad", "  ").replace(r"\qquad", "    ")
    # Engineering OCR often emits the degree symbol as {}^\circ. A degree glyph
    # is already raised, so normalize the whole construct before generic scripts.
    s = re.sub(r"\{\}\s*\^\{?\\circ\}?", "°", s)
    s = re.sub(r"\{\}\s*\^\{?\\degree\}?", "°", s)
    s = re.sub(r"\{\}\s*\^\{?\\circ\}?", "°", s)
    s = re.sub(r"\{\}\s*\^\{?\\degree\}?", "°", s)
    s = re.sub(r"\^\{?\\circ\}?", "°", s)
    s = re.sub(r"\^\{?\\degree\}?", "°", s)

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
    s = s.replace("ZZZZUOCRBRZZZZ", "<br>")
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
    if re.search(r"\\[A-Za-z]+|\^\{|_\{", raw):
        # Bare TeX should be converted from the original OCR string so embedded
        # newlines remain semantic line breaks instead of becoming escaped <br>.
        result = _latex_fragment_to_html(raw)
    return result


# ------------------------------- HTML tables --------------------------------

class _OCRTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict]] = []
        self.current_row: list[dict] | None = None
        self.current_cell: dict | None = None
        self._bold_depth = 0
        self._italic_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            if self.current_row is not None and self.current_row:
                self.rows.append(self.current_row)
            self.current_row = []
        elif tag in {"td", "th"}:
            if self.current_row is None:
                self.current_row = []
            amap = {str(k).lower(): (v or "") for k, v in attrs}
            style = str(amap.get("style") or "").lower()
            align = str(amap.get("align") or "").lower().strip()
            valign = str(amap.get("valign") or "").lower().strip()
            self.current_cell = {
                "header": tag == "th",
                "bold": tag == "th" or ("font-weight" in style and any(x in style for x in ("bold", "600", "700", "800", "900"))),
                "italic": "font-style:italic" in style.replace(" ", ""),
                "text": [],
                "colspan": max(1, _safe_int(amap.get("colspan"), 1)),
                "rowspan": max(1, _safe_int(amap.get("rowspan"), 1)),
                "align": align if align in {"left", "center", "right"} else None,
                "valign": valign if valign in {"top", "middle", "bottom"} else None,
            }
        elif tag == "br":
            if self.current_cell is not None:
                self.current_cell["text"].append("\n")
        elif tag in {"b", "strong"}:
            self._bold_depth += 1
            if self.current_cell is not None:
                self.current_cell["bold"] = True
        elif tag in {"i", "em"}:
            self._italic_depth += 1
            if self.current_cell is not None:
                self.current_cell["italic"] = True

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"b", "strong"}:
            self._bold_depth = max(0, self._bold_depth - 1)
        elif tag in {"i", "em"}:
            self._italic_depth = max(0, self._italic_depth - 1)
        elif tag in {"td", "th"} and self.current_cell is not None:
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
            if self._bold_depth:
                self.current_cell["bold"] = True
            if self._italic_depth:
                self.current_cell["italic"] = True

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


def _css_rgb(color: tuple[float, float, float]) -> str:
    vals = [max(0, min(255, int(round(float(v) * 255.0)))) for v in color[:3]]
    return f"#{vals[0]:02x}{vals[1]:02x}{vals[2]:02x}"


def _global_table_source_style(
    source_image: Image.Image | None,
    bbox: tuple[float, float, float, float] | None,
    nrows: int,
    ncols: int,
) -> dict:
    """Sample only robust whole-table style hints from the source raster.

    Never infer per-cell positions or font sizes here.  The attached v1.1 build was
    stable because the table was laid out globally.  v1.7 preserves that behaviour
    while retaining only low-risk visual hints such as rule colour and an obviously
    coloured first/header row.
    """
    result = {
        "css": "",
        "header_color": None,
        "header_background": None,
        "header_bold": False,
    }
    if source_image is None or bbox is None:
        return result
    try:
        table_px = _source_pixel_box(bbox, *source_image.size)
        grid = _table_grid_positions(source_image, bbox, max(1, nrows), max(1, ncols))
        if grid:
            xfracs, yfracs = grid
        else:
            xfracs = [i / max(1, ncols) for i in range(max(1, ncols) + 1)]
            yfracs = [i / max(1, nrows) for i in range(max(1, nrows) + 1)]

        rule = _sample_grid_color(source_image, table_px, xfracs, yfracs)
        result["css"] = f".ocr-table td,.ocr-table th{{border-color:{_css_rgb(rule)};}}"

        x0, y0, x1, y1 = table_px
        hf = yfracs[1] if len(yfracs) > 1 else min(1.0, 1.0 / max(1, nrows))
        header_px = (x0, y0, x1, int(round(y0 + (y1 - y0) * hf)))
        hs = _cell_visual_style(source_image, header_px)
        r, g, b = hs.get("color", (0.0, 0.0, 0.0))
        mx, mn = max(r, g, b), min(r, g, b)
        sat = (mx - mn) / max(mx, 1e-6)
        if sat >= 0.18 and mx >= 0.25:
            result["header_color"] = _css_rgb((r, g, b))
            result["header_bold"] = True
        bg = hs.get("background", (1.0, 1.0, 1.0))
        if max(abs(float(v) - 1.0) for v in bg) >= 0.06:
            result["header_background"] = _css_rgb(bg)
        return result
    except Exception:
        return result


def _apply_first_row_inline_style(table_html: str, style: dict) -> str:
    """Apply source-derived header style inline for PyMuPDF HTML compatibility.

    Some versions of Story / insert_htmlbox do not consistently honour complex CSS
    selectors such as ``tr:first-child``.  Inline cell styles are deliberately used
    only for the first row and only for robust global properties.
    """
    color = style.get("header_color")
    bg = style.get("header_background")
    bold = bool(style.get("header_bold"))
    if not (color or bg or bold):
        return table_html
    m = re.search(r"<tr\b[^>]*>(.*?)</tr>", table_html, flags=re.I | re.S)
    if not m:
        return table_html
    row_html = m.group(0)
    declarations = []
    if color:
        declarations.append(f"color:{color}")
    if bg:
        declarations.append(f"background-color:{bg}")
    if bold:
        declarations.append("font-weight:700")
    decl = ";".join(declarations)

    def inject(mt: re.Match) -> str:
        tag, attrs = mt.group(1), mt.group(2) or ""
        sm = re.search(r'\sstyle\s*=\s*(["\'])(.*?)\1', attrs, flags=re.I | re.S)
        if sm:
            existing = sm.group(2).rstrip(";")
            new_style = (existing + ";" + decl).strip(";")
            attrs2 = attrs[:sm.start()] + f' style="{new_style}"' + attrs[sm.end():]
        else:
            attrs2 = attrs + f' style="{decl}"'
        return f"<{tag}{attrs2}>"

    styled = re.sub(r"<(td|th)([^>]*)>", inject, row_html, flags=re.I)
    return table_html[:m.start()] + styled + table_html[m.end():]


def _insert_table_block(
    page: fitz.Page,
    rect: fitz.Rect,
    raw: str,
    *,
    source_image: Image.Image | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> bool:
    """Render a table as one coherent HTML/vector layout.

    v1.7 restores the stable v1.1 strategy: the whole table is laid out and
    globally scaled as one object with ``table-layout:auto``.  This avoids the
    cascading per-cell geometry errors seen on dense / low-resolution scans in
    v1.6.x, while keeping real selectable PDF text and vector rules.
    """
    table_html, before, after = _safe_table_html(raw)
    if not table_html:
        return False
    body: list[str] = []
    if _strip_light_markup(before):
        body.append(f"<p>{_text_with_inline_math_to_html(before)}</p>")
    body.append(table_html)
    if _strip_light_markup(after):
        body.append(f'<div class="caption">{_text_with_inline_math_to_html(after)}</div>')

    # Keep the v1.1 table behaviour deliberately simple: one global layout, auto
    # columns, natural row heights, and global shrink-to-fit.  A 9 pt starting
    # size is merely a reference; insert_htmlbox scales the entire table uniformly
    # to the OCR rectangle, preserving internal typographic consistency.
    rows = max(1, len(re.findall(r"<tr\b", table_html, flags=re.I)))
    parsed_rows, _before, _after = _parse_ocr_table_rows(raw)
    cols = max((sum(max(1, int(c.get("colspan", 1) or 1)) for c in row) for row in parsed_rows), default=1)
    source_style = _global_table_source_style(source_image, bbox, rows, cols)
    # Apply first-row style directly to the table HTML for compatibility with
    # PyMuPDF versions that do not fully support complex CSS selectors.
    body = [
        _apply_first_row_inline_style(part, source_style) if '<table class="ocr-table">' in part else part
        for part in body
    ]
    css = (
        ".ocr-table{table-layout:auto;width:100%;font-size:9pt;height:auto;} "
        ".ocr-table td,.ocr-table th{padding:2pt 3pt;vertical-align:middle;} "
        + str(source_style.get("css") or "")
    )
    return _insert_html_block(page, rect, "".join(body), css=css, scale_low=0.18)


# ------------------------ geometry-aware PDF tables -------------------------

def _parse_ocr_table_rows(raw: str) -> tuple[list[list[dict]], str, str]:
    """Parse the first OCR HTML table and preserve text before/after it."""
    m = _TABLE_RE.search(raw or "")
    if not m:
        return [], raw or "", ""
    parser = _OCRTableParser()
    try:
        parser.feed(m.group(0))
        parser.close()
        parser.finish()
    except Exception:
        return [], raw[:m.start()], raw[m.end():]
    return parser.rows, raw[:m.start()], raw[m.end():]


def _table_grid_positions(
    source_image: Image.Image | None,
    bbox: tuple[float, float, float, float] | None,
    nrows: int,
    ncols: int,
) -> tuple[list[float], list[float]] | None:
    """Recover printed table rules from the exact source raster.

    v1.6.3 first uses direct grayscale continuity rather than generic foreground
    segmentation.  This is much more reliable for dense tables with unequal row
    heights because a true rule spans most of the table while text never does.
    """
    if source_image is None or bbox is None or nrows < 1 or ncols < 1:
        return None
    try:
        px0,py0,px1,py1=_normalized_bbox_to_pixel_rect(bbox,source_image.size)
        gray=np.asarray(source_image.crop((px0,py0,px1,py1)).convert('L'),dtype=np.uint8)
        if gray.shape[0]<8 or gray.shape[1]<8: return None
        bg=float(np.percentile(gray,90))
        # Include antialiased/light gray rules, but exclude the white paper.
        dark=gray < max(120.0,min(238.0,bg-18.0))

        def centers(scores: np.ndarray, expected: int) -> list[float] | None:
            scores=np.asarray(scores,dtype=float)
            peak=float(scores.max()) if scores.size else 0.0
            if peak < 0.22: return None
            threshold=max(0.28,min(0.72,peak*0.56))
            active=scores>=threshold
            # Bridge one-pixel antialias gaps in a rule.
            active=_bridge_boolean_gaps(active,max_gap=1)
            runs=[]; st=None
            for i,on in enumerate(active):
                if on and st is None: st=i
                elif not on and st is not None:
                    runs.append((st,i)); st=None
            if st is not None: runs.append((st,len(active)))
            # Score runs by continuity. If small extra runs remain, retain the
            # strongest expected rules instead of rejecting the whole grid.
            candidates=[]
            for a,b in runs:
                if b<=a: continue
                c=0.5*(a+b-1); strength=float(scores[a:b].max())
                candidates.append((c,strength))
            if len(candidates)>expected:
                candidates=sorted(candidates,key=lambda x:x[1],reverse=True)[:expected]
                candidates=sorted(candidates,key=lambda x:x[0])
            denom=max(1.0,float(len(scores)-1))
            fracs=[max(0.0,min(1.0,c/denom)) for c,_ in candidates]
            if len(fracs)==expected-1:
                if not fracs: return None
                # Model bboxes commonly clip the final right/bottom rule by one
                # pixel. If the first outer rule is present, the missing rule is
                # overwhelmingly likely to be the far edge even when the last
                # *interior* row lies at 95% of the table height.
                if fracs[0] <= 0.035 and fracs[-1] < 0.995:
                    fracs.append(1.0)
                elif fracs[-1] >= 0.965 and fracs[0] > 0.005:
                    fracs.insert(0,0.0)
            if len(fracs)!=expected: return None
            if fracs[0]<=0.08: fracs[0]=0.0
            if fracs[-1]>=0.92: fracs[-1]=1.0
            # Every cell must have meaningful extent.
            if any((b-a)<0.004 for a,b in zip(fracs[:-1],fracs[1:])): return None
            return fracs

        xs=centers(dark.mean(axis=0),ncols+1)
        ys=centers(dark.mean(axis=1),nrows+1)
        if xs is None or ys is None: return None
        if xs[0]>0.12 or xs[-1]<0.88 or ys[0]>0.12 or ys[-1]<0.88: return None
        return xs,ys
    except Exception:
        return None

def _pixel_rect_to_normalized_bbox(
    pixel_rect: tuple[int, int, int, int],
    image_size: tuple[int, int],
) -> tuple[float, float, float, float]:
    """Convert source-image pixels back to Unlimited-OCR's normalized 0..999 space."""
    x0, y0, x1, y1 = pixel_rect
    w, h = image_size
    return (
        max(0.0, min(_COORD_MAX, (float(x0) / max(1, w)) * _COORD_MAX)),
        max(0.0, min(_COORD_MAX, (float(y0) / max(1, h)) * _COORD_MAX)),
        max(0.0, min(_COORD_MAX, (float(x1) / max(1, w)) * _COORD_MAX)),
        max(0.0, min(_COORD_MAX, (float(y1) / max(1, h)) * _COORD_MAX)),
    )


def _cell_background_color(
    source_image: Image.Image | None,
    pixel_rect: tuple[int, int, int, int] | None,
) -> tuple[float, float, float]:
    """Estimate the cell fill from the median source pixels.

    Text occupies relatively few pixels, so the median is a good approximation of
    white / gray / tinted cell backgrounds without needing OCR style metadata.
    """
    if source_image is None or pixel_rect is None:
        return (1.0, 1.0, 1.0)
    try:
        x0, y0, x1, y1 = pixel_rect
        if x1 - x0 < 2 or y1 - y0 < 2:
            return (1.0, 1.0, 1.0)
        pad = max(1, min(4, int(round(min(x1 - x0, y1 - y0) * 0.04))))
        crop = np.asarray(source_image.crop((x0 + pad, y0 + pad, x1 - pad, y1 - pad)).convert('RGB'), dtype=np.uint8)
        if crop.size == 0:
            return (1.0, 1.0, 1.0)
        med = np.median(crop.reshape(-1, 3), axis=0)
        return tuple(float(v) / 255.0 for v in med[:3])
    except Exception:
        return (1.0, 1.0, 1.0)


def _table_rule_style(
    source_image: Image.Image | None,
    bbox: tuple[float, float, float, float] | None,
    xfracs: list[float],
    yfracs: list[float],
) -> tuple[tuple[float, float, float], float]:
    """Estimate table rule colour / thickness from the printed grid.

    Neutral low-saturation pixels are preferred so coloured header text does not
    accidentally determine the border colour.
    """
    if source_image is None or bbox is None:
        return (0.35, 0.35, 0.35), 0.5
    try:
        px0, py0, px1, py1 = _normalized_bbox_to_pixel_rect(bbox, source_image.size)
        rgb = np.asarray(source_image.crop((px0, py0, px1, py1)).convert('RGB'), dtype=np.uint8)
        h, w, _ = rgb.shape
        if h < 4 or w < 4:
            return (0.35, 0.35, 0.35), 0.5
        samples = []
        thicknesses = []
        gray = np.asarray(Image.fromarray(rgb).convert('L'), dtype=np.uint8)
        for frac in xfracs:
            x = max(0, min(w - 1, int(round(frac * (w - 1)))))
            a, b = max(0, x - 2), min(w, x + 3)
            strip = rgb[:, a:b, :].reshape(-1, 3)
            if strip.size:
                mx = strip.max(axis=1); mn = strip.min(axis=1)
                neutral = strip[(mx - mn < 35) & (strip.mean(axis=1) < 225)]
                if len(neutral): samples.append(neutral)
            # Estimate local dark run thickness around the expected line.
            score = (gray[:, a:b] < 220).mean(axis=0)
            thicknesses.append(max(1, int(np.sum(score > 0.35))))
        for frac in yfracs:
            y = max(0, min(h - 1, int(round(frac * (h - 1)))))
            a, b = max(0, y - 2), min(h, y + 3)
            strip = rgb[a:b, :, :].reshape(-1, 3)
            if strip.size:
                mx = strip.max(axis=1); mn = strip.min(axis=1)
                neutral = strip[(mx - mn < 35) & (strip.mean(axis=1) < 225)]
                if len(neutral): samples.append(neutral)
            score = (gray[a:b, :] < 220).mean(axis=1)
            thicknesses.append(max(1, int(np.sum(score > 0.35))))
        if samples:
            pixels = np.concatenate(samples, axis=0)
            med = np.median(pixels, axis=0)
            color = tuple(float(v) / 255.0 for v in med[:3])
        else:
            color = (0.35, 0.35, 0.35)
        # 200-DPI source is common: one pixel is ~0.36 pt. Keep a sane PDF range.
        px_thick = float(np.median(thicknesses)) if thicknesses else 1.0
        width_pt = max(0.28, min(1.15, px_thick * 72.0 / 200.0))
        return color, width_pt
    except Exception:
        return (0.35, 0.35, 0.35), 0.5


def _table_cell_pixel_rect(
    source_image: Image.Image | None,
    bbox: tuple[float, float, float, float] | None,
    xfracs: list[float],
    yfracs: list[float],
    r: int,
    c: int,
    rs: int,
    cs: int,
) -> tuple[int, int, int, int] | None:
    if source_image is None or bbox is None:
        return None
    try:
        px0, py0, px1, py1 = _normalized_bbox_to_pixel_rect(bbox, source_image.size)
        tw, th = max(1, px1 - px0), max(1, py1 - py0)
        x0 = px0 + int(round(xfracs[c] * tw))
        x1 = px0 + int(round(xfracs[c + cs] * tw))
        y0 = py0 + int(round(yfracs[r] * th))
        y1 = py0 + int(round(yfracs[r + rs] * th))
        return (max(px0, x0), max(py0, y0), min(px1, x1), min(py1, y1))
    except Exception:
        return None


def _table_cell_line_geometry(
    source_image: Image.Image | None,
    cell_px: tuple[int, int, int, int] | None,
) -> list[dict]:
    """Detect printed text rows inside a cell while excluding its border rules."""
    if source_image is None or cell_px is None:
        return []
    x0, y0, x1, y1 = cell_px
    cw, ch = x1 - x0, y1 - y0
    if cw < 6 or ch < 6:
        return []
    # Inset enough to exclude antialiased grid rules but retain text near edges.
    ix = max(2, min(6, int(round(cw * 0.025))))
    iy = max(2, min(5, int(round(ch * 0.06))))
    inner = (x0 + ix, y0 + iy, x1 - ix, y1 - iy)
    if inner[2] - inner[0] < 3 or inner[3] - inner[1] < 3:
        return []
    nb = _pixel_rect_to_normalized_bbox(inner, source_image.size)
    return _detect_text_line_geometry(source_image, nb)


def _infer_cell_alignment(
    cell_px: tuple[int, int, int, int] | None,
    line_geometry: list[dict],
) -> tuple[int, str]:
    """Return PyMuPDF horizontal alignment + descriptive label."""
    if cell_px is None or not line_geometry:
        return fitz.TEXT_ALIGN_LEFT, 'left'
    x0, _y0, x1, _y1 = cell_px
    ix0 = min(item['pixel_rect'][0] for item in line_geometry)
    ix1 = max(item['pixel_rect'][2] for item in line_geometry)
    left = max(0.0, ix0 - x0)
    right = max(0.0, x1 - ix1)
    tol = max(3.0, (x1 - x0) * 0.08)
    if abs(left - right) <= tol:
        return fitz.TEXT_ALIGN_CENTER, 'center'
    if right + tol < left:
        return fitz.TEXT_ALIGN_RIGHT, 'right'
    return fitz.TEXT_ALIGN_LEFT, 'left'


def _remove_table_rules(mask: np.ndarray) -> np.ndarray:
    """Remove long horizontal/vertical table rules from a foreground mask."""
    out = np.asarray(mask, dtype=bool).copy()
    if out.ndim != 2 or out.size == 0:
        return out
    h, w = out.shape
    row_long = np.where(out.mean(axis=1) >= 0.50)[0]
    col_long = np.where(out.mean(axis=0) >= 0.50)[0]
    for y in row_long:
        out[max(0, y - 1):min(h, y + 2), :] = False
    for x in col_long:
        out[:, max(0, x - 1):min(w, x + 2)] = False
    # Ignore the cell border itself. This also prevents antialiased rules from
    # influencing text colour and alignment estimates.
    my = max(2, min(8, max(h // 20, 3)))
    mx = max(2, min(8, max(w // 50, 3)))
    out[:my, :] = False; out[-my:, :] = False
    out[:, :mx] = False; out[:, -mx:] = False
    return out


def _bridge_boolean_gaps(values: np.ndarray, max_gap: int = 2) -> np.ndarray:
    values = np.asarray(values, dtype=bool).copy()
    if values.size < 3:
        return values
    true_idx = np.flatnonzero(values)
    for a, b in zip(true_idx[:-1], true_idx[1:]):
        if 1 < (b - a) <= max_gap + 1:
            values[a:b + 1] = True
    return values


def _cell_text_line_geometry(
    source_image: Image.Image,
    pixel_rect: tuple[int, int, int, int],
) -> list[dict]:
    """Detect actual printed text lines inside one table cell.

    Table borders are first suppressed, then row runs are recovered from the
    remaining ink. Returned rectangles are in full source-image pixel coordinates.
    """
    x0, y0, x1, y1 = [int(v) for v in pixel_rect]
    if x1 - x0 < 4 or y1 - y0 < 4:
        return []
    try:
        crop_rgb = np.asarray(source_image.crop((x0, y0, x1, y1)).convert("RGB"), dtype=np.uint8)
        gray = np.asarray(Image.fromarray(crop_rgb).convert("L"), dtype=np.uint8)
        mask = _remove_table_rules(_foreground_mask(gray))
        h, w = mask.shape
        if mask.sum() < 3:
            return []

        # A text row only needs a small amount of ink, but should contain more
        # than a lone noise pixel. Bridge tiny antialiasing gaps within one line.
        row_counts = mask.sum(axis=1)
        active = row_counts >= max(2, int(round(w * 0.012)))
        active = _bridge_boolean_gaps(active, max_gap=max(2, min(5, int(round(h * 0.10)))))

        runs: list[tuple[int, int]] = []
        st = None
        for i, on in enumerate(active):
            if on and st is None:
                st = i
            elif not on and st is not None:
                runs.append((st, i)); st = None
        if st is not None:
            runs.append((st, h))

        result: list[dict] = []
        for ya, yb in runs:
            if yb - ya < 2:
                continue
            sub = mask[ya:yb, :]
            ys, xs = np.where(sub)
            if xs.size < 3:
                continue
            xa = int(xs.min()); xb = int(xs.max()) + 1
            # Keep a tiny horizontal antialias pad for width matching, but do not
            # pad vertically: vertical padding systematically inflates the inferred
            # font size by about one pixel above and below on dense tables.
            xa = max(0, xa - 1); xb = min(w, xb + 1)
            ya2 = ya; yb2 = yb
            result.append({
                "pixel_rect": (x0 + xa, y0 + ya2, x0 + xb, y0 + yb2),
                "width": max(1, xb - xa),
                "height": max(1, yb2 - ya2),
            })
        return result
    except Exception:
        return []


def _cell_visual_style(
    source_image: Image.Image | None,
    pixel_rect: tuple[int, int, int, int] | None,
) -> dict:
    """Estimate cell text colour, background, and alignment from source pixels."""
    style = {
        "color": (0.0, 0.0, 0.0),
        "background": (1.0, 1.0, 1.0),
        "align": "left",
        "valign": "middle",
        "ink_density": 0.0,
    }
    if source_image is None or pixel_rect is None:
        return style
    x0, y0, x1, y1 = [int(v) for v in pixel_rect]
    if x1 - x0 < 3 or y1 - y0 < 3:
        return style
    try:
        rgb = np.asarray(source_image.crop((x0, y0, x1, y1)).convert("RGB"), dtype=np.uint8)
        gray = np.asarray(Image.fromarray(rgb).convert("L"), dtype=np.uint8)
        raw = _foreground_mask(gray)
        mask = _remove_table_rules(raw)
        h, w = mask.shape
        ys, xs = np.where(mask)

        # Background is the median of the lightest half of pixels. This preserves
        # light shaded headers without letting coloured text dominate the sample.
        lum = gray.reshape(-1)
        cutoff = np.percentile(lum, 55)
        bg_pixels = rgb.reshape(-1, 3)[lum >= cutoff]
        if bg_pixels.size:
            bg = np.median(bg_pixels, axis=0)
            style["background"] = tuple(float(v) / 255.0 for v in bg[:3])

        if xs.size >= 3:
            ink_pixels = rgb[mask]
            # Saturated coloured text (e.g. red table headers) is easier to identify
            # in HSV-like terms than by raw darkness. Prefer saturated foreground
            # pixels when enough are present, otherwise use all foreground pixels.
            mx = ink_pixels.max(axis=1).astype(float)
            mn = ink_pixels.min(axis=1).astype(float)
            sat = (mx - mn) / np.maximum(mx, 1.0)
            coloured = ink_pixels[sat >= 0.18]
            use = coloured if coloured.shape[0] >= max(3, int(0.12 * ink_pixels.shape[0])) else ink_pixels
            med = np.median(use, axis=0)
            if float(np.mean(med)) <= 240:
                style["color"] = tuple(float(v) / 255.0 for v in med[:3])

            left = float(xs.min()); right = float(xs.max() + 1)
            top = float(ys.min()); bottom = float(ys.max() + 1)
            lm = left; rm = max(0.0, w - right)
            tm = top; bm = max(0.0, h - bottom)
            center_x = (left + right) * 0.5 / max(1.0, float(w))
            center_y = (top + bottom) * 0.5 / max(1.0, float(h))
            if abs(lm - rm) <= max(3.0, 0.12 * w) or 0.40 <= center_x <= 0.60:
                style["align"] = "center"
            elif rm + max(2.0, 0.05 * w) < lm:
                style["align"] = "right"
            else:
                style["align"] = "left"
            if abs(tm - bm) <= max(2.0, 0.14 * h) or 0.36 <= center_y <= 0.64:
                style["valign"] = "middle"
            elif bm + max(2.0, 0.06 * h) < tm:
                style["valign"] = "bottom"
            else:
                style["valign"] = "top"
            style["ink_density"] = float(mask.mean())
    except Exception:
        pass
    return style


def _font_spec_for_table_cell(page: fitz.Page, text: str, *, bold: bool = False, italic: bool = False) -> tuple[str, fitz.Font]:
    """Choose a local font style for table cells while preserving Unicode."""
    has_cjk = any(
        ("\u3400" <= ch <= "\u4dbf") or ("\u4e00" <= ch <= "\u9fff")
        or ("\u3040" <= ch <= "\u30ff") or ("\uac00" <= ch <= "\ud7af")
        for ch in text
    )
    if has_cjk:
        return _font_spec_for_text(page, text)

    if bold and italic:
        candidates = [
            ("OCRTableBI", r"C:\\Windows\\Fonts\\arialbi.ttf"),
            ("OCRTableBI", r"C:\\Windows\\Fonts\\segoeuiz.ttf"),
            ("OCRTableBI", "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
        ]
    elif bold:
        candidates = [
            ("OCRTableB", r"C:\\Windows\\Fonts\\arialbd.ttf"),
            ("OCRTableB", r"C:\\Windows\\Fonts\\segoeuib.ttf"),
            ("OCRTableB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ]
    elif italic:
        candidates = [
            ("OCRTableI", r"C:\\Windows\\Fonts\\ariali.ttf"),
            ("OCRTableI", r"C:\\Windows\\Fonts\\segoeuii.ttf"),
            ("OCRTableI", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
        ]
    else:
        candidates = [
            ("OCRTable", r"C:\\Windows\\Fonts\\arial.ttf"),
            ("OCRTable", r"C:\\Windows\\Fonts\\segoeui.ttf"),
            ("OCRTable", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
    for fontname, path in candidates:
        if not os.path.exists(path):
            continue
        key = os.path.normcase(os.path.abspath(path))
        try:
            if key not in _RECON_FONT_CACHE:
                _RECON_FONT_CACHE[key] = fitz.Font(fontfile=path)
            page.insert_font(fontname=fontname, fontfile=path)
            _RECON_FONT_PATHS[fontname] = path
            return fontname, _RECON_FONT_CACHE[key]
        except Exception:
            continue
    return _font_spec_for_reconstruction(page, text, "header" if bold else "text")


def _fit_cell_text(
    page: fitz.Page,
    rect: fitz.Rect,
    text: str,
    *,
    bold: bool = False,
    italic: bool = False,
    source_image: Image.Image | None = None,
    pixel_rect: tuple[int, int, int, int] | None = None,
    align_hint: str | None = None,
    valign_hint: str | None = None,
) -> tuple[bool, dict]:
    """Render a table cell using source-derived line geometry and typography.

    Returns (success, diagnostics). The precision path places each visible line on
    the actual source ink rectangle; the fallback uses a styled textbox.
    """
    scripted_words = _split_script_runs_into_words(text)
    visible = " ".join("".join(v for v, _m in word) for word in scripted_words).strip()
    diag = {"precision": False, "lines": 0, "font_size": 0.0, "math_normalized": visible != _strip_light_markup(text)}
    if not visible or rect.width <= 2 or rect.height <= 2:
        return True, diag

    style = _cell_visual_style(source_image, pixel_rect)
    if align_hint in {"left", "center", "right"}:
        style["align"] = align_hint
    if valign_hint in {"top", "middle", "bottom"}:
        style["valign"] = valign_hint

    fontname, font = _font_spec_for_table_cell(page, visible, bold=bold, italic=italic)

    # Best path: detect exact printed lines inside the cell, then fit real PDF text
    # to those ink rectangles. This automatically reproduces left/center/right and
    # vertical placement from the source rather than relying on generic cell padding.
    if source_image is not None and pixel_rect is not None:
        geometry = _cell_text_line_geometry(source_image, pixel_rect)
        if geometry:
            line_texts = _allocate_text_to_lines(visible, [g["width"] for g in geometry], font)
            if line_texts and any(line_texts):
                line_runs = _script_words_for_allocated_lines(text, line_texts)
                ok_count = 0; sizes: list[float] = []
                for idx, (line_text, g) in enumerate(zip(line_texts, geometry)):
                    line_text = re.sub(r"\s+", " ", line_text).strip()
                    if not line_text:
                        continue
                    drect = _pixel_rect_to_rotated_page_rect(page, g["pixel_rect"], source_image.size)
                    color = _sample_foreground_color(source_image, g["pixel_rect"])
                    runs = line_runs[idx] if idx < len(line_runs) else [(line_text, "normal")]
                    success, fs = _insert_exact_scripted_line(page, drect, runs, fontname, font, color)
                    if success:
                        ok_count += 1; sizes.append(fs)
                if ok_count == len([t for t in line_texts if str(t).strip()]):
                    diag.update({
                        "precision": True,
                        "lines": ok_count,
                        "font_size": float(sum(sizes) / len(sizes)) if sizes else 0.0,
                        "color": style["color"],
                        "align": style["align"],
                        "valign": style["valign"],
                    })
                    return True, diag

    # Fallback for scripted math: PyMuPDF's HTML engine positions <sup>/<sub> using
    # ordinary glyphs, so negative exponents remain portable even when precision
    # source geometry is unavailable.
    if re.search(r"\^\{|_\{|\\[A-Za-z]+|\\\(|\\\[", text):
        body = _text_with_inline_math_to_html(text)
        if body:
            css = f"body{{font-size:{max(4.0, min(14.0, rect.height * 0.42)):.2f}pt;line-height:1.05;color:rgb({int(style['color'][0]*255)},{int(style['color'][1]*255)},{int(style['color'][2]*255)});}}"
            if _insert_html_block(page, rect, body, css=css, scale_low=0.25):
                diag.update({"lines": max(1, visible.count("\n") + 1), "font_size": 0.0, "color": style["color"], "align": style["align"], "valign": style["valign"]})
                return True, diag

    # Fallback: preserve estimated style and placement as closely as possible.
    pad_x = max(0.7, min(2.4, rect.width * 0.018))
    pad_y = max(0.5, min(1.8, rect.height * 0.06))
    inner = fitz.Rect(rect.x0 + pad_x, rect.y0 + pad_y, rect.x1 - pad_x, rect.y1 - pad_y)
    if inner.width <= 1 or inner.height <= 1:
        return False, diag
    amap = {"left": fitz.TEXT_ALIGN_LEFT, "center": fitz.TEXT_ALIGN_CENTER, "right": fitz.TEXT_ALIGN_RIGHT}
    align = amap.get(style["align"], fitz.TEXT_ALIGN_LEFT)
    lines = max(1, visible.count("\n") + 1)
    size = max(4.0, min(16.0, (inner.height / lines) * 0.68))
    for _ in range(28):
        # Vertically center / bottom-align by moving a tight textbox inside the cell.
        line_box_h = min(inner.height, max(size * 1.12 * lines, size * 1.25))
        if style["valign"] == "bottom":
            box = fitz.Rect(inner.x0, inner.y1 - line_box_h, inner.x1, inner.y1)
        elif style["valign"] == "middle":
            cy = (inner.y0 + inner.y1) * 0.5
            box = fitz.Rect(inner.x0, cy - line_box_h * 0.5, inner.x1, cy + line_box_h * 0.5)
        else:
            box = fitz.Rect(inner.x0, inner.y0, inner.x1, inner.y0 + line_box_h)
        try:
            spare = page.insert_textbox(
                box,
                visible,
                fontsize=size,
                fontname=fontname,
                color=style["color"],
                align=align,
                lineheight=1.03,
                overlay=True,
            )
            if spare >= -0.01:
                diag.update({"lines": lines, "font_size": size, "color": style["color"], "align": style["align"], "valign": style["valign"]})
                return True, diag
        except Exception:
            pass
        size *= 0.90
        if size < 3.2:
            break
    return False, diag


def _sample_grid_color(
    source_image: Image.Image | None,
    table_pixel_rect: tuple[int, int, int, int] | None,
    xfracs: list[float],
    yfracs: list[float],
) -> tuple[float, float, float]:
    """Estimate table-rule colour without being fooled by white background.

    Sampling only the darkest percentile fails when a rule strip is mostly white.
    Instead this keeps genuinely non-white, low-saturation pixels and favours their
    brighter cluster: gray rules win over incidental black text crossing a border,
    while genuinely black rules still remain black.
    """
    if source_image is None or table_pixel_rect is None:
        return (0.35, 0.35, 0.35)
    try:
        x0, y0, x1, y1 = table_pixel_rect
        rgb = np.asarray(source_image.crop((x0, y0, x1, y1)).convert("RGB"), dtype=np.uint8)
        h, w, _ = rgb.shape
        samples = []
        for f in xfracs:
            x = max(0, min(w - 1, int(round(f * (w - 1)))))
            samples.append(rgb[:, max(0, x - 2):min(w, x + 3), :].reshape(-1, 3))
        for f in yfracs:
            y = max(0, min(h - 1, int(round(f * (h - 1)))))
            samples.append(rgb[max(0, y - 2):min(h, y + 3), :, :].reshape(-1, 3))
        arr = np.concatenate(samples, axis=0) if samples else np.empty((0, 3), dtype=np.uint8)
        if arr.shape[0]:
            lum = arr.mean(axis=1)
            mx = arr.max(axis=1).astype(float); mn = arr.min(axis=1).astype(float)
            sat = (mx - mn) / np.maximum(mx, 1.0)
            neutral = arr[(lum < 245) & (sat < 0.18)]
            if neutral.shape[0] >= 4:
                nl = neutral.mean(axis=1)
                # Prefer the upper half of non-white neutral pixels. This usually
                # represents gray table rules rather than black characters crossing
                # the sampled strip. For black rules the values are all near zero.
                cut = np.percentile(nl, 55)
                rule = neutral[nl >= cut]
                if rule.shape[0] < 3:
                    rule = neutral
                med = np.median(rule, axis=0)
                return tuple(float(v) / 255.0 for v in med[:3])
    except Exception:
        pass
    return (0.35, 0.35, 0.35)



def _estimate_line_font_size_from_rect(
    page: fitz.Page,
    line_rect: fitz.Rect,
    runs: Sequence[tuple[str, str]],
    fontname: str,
    font: fitz.Font,
) -> float | None:
    """Estimate source font size from the *visible ink height* of one table line.

    v1.6.0 fitted every cell independently to its detected ink rectangle.  On dense
    low-resolution tables that amplified a one-pixel segmentation error into a very
    large font-size error.  v1.6.3 uses these local estimates only as samples, then
    applies a robust table-wide body/header size when rendering.
    """
    rect = fitz.Rect(line_rect)
    if rect.is_empty or rect.height <= 0.5:
        return None
    font_path = _RECON_FONT_PATHS.get(fontname)
    if font_path and os.path.isfile(font_path):
        try:
            (ux0, uy0, ux1, uy1), _adv, _ = _measure_script_runs_pil(runs, font_path, 1000)
            unit_h = max(0.05, (uy1 - uy0) / 1000.0)
            size = rect.height / unit_h
            if 2.0 <= size <= 30.0:
                return float(size)
        except Exception:
            pass
    # Metric fallback.  This is less exact than FreeType ink measurement but is
    # stable enough to contribute to the robust median.
    asc = float(getattr(font, 'ascender', 1.0) or 1.0)
    desc = float(getattr(font, 'descender', -0.25) or -0.25)
    unit_h = max(0.2, asc - desc)
    size = rect.height / unit_h
    return float(size) if 2.0 <= size <= 30.0 else None


def _robust_font_median(values: Sequence[float], fallback: float) -> float:
    vals = np.asarray([float(v) for v in values if v and np.isfinite(v)], dtype=float)
    if vals.size == 0:
        return float(fallback)
    med = float(np.median(vals))
    if vals.size >= 4:
        mad = float(np.median(np.abs(vals - med)))
        if mad > 0.05:
            keep = np.abs(vals - med) <= max(0.55, 2.8 * mad)
            if np.any(keep):
                med = float(np.median(vals[keep]))
    return float(max(3.0, min(18.0, med)))


def _insert_scripted_line_fixed_size(
    page: fitz.Page,
    target_rect: fitz.Rect,
    runs: Sequence[tuple[str, str]],
    fontname: str,
    font: fitz.Font,
    base_size: float,
    color: tuple[float, float, float],
) -> tuple[bool, float]:
    """Place visible table text at source position using a fixed source-derived size.

    Only horizontal morphing is permitted.  Vertical scale is never stretched to
    fill noisy cell geometry, which is the key stability change from v1.6.0.
    """
    runs = [(str(v), str(m)) for v, m in runs if str(v)]
    rect = fitz.Rect(target_rect)
    if not runs or rect.is_empty or rect.width <= 0.4 or rect.height <= 0.4:
        return False, 1.0
    base_size = float(max(2.5, min(20.0, base_size)))
    font_path = _RECON_FONT_PATHS.get(fontname)
    if font_path and os.path.isfile(font_path):
        try:
            probe = 1000
            (ux0, uy0, ux1, uy1), _total_adv, advances = _measure_script_runs_pil(runs, font_path, probe)
            ink_w = max(0.001, (ux1 - ux0) / probe * base_size)
            # Match source line width only within a conservative range.  If the OCR
            # text or substitute font differs strongly, preserve typography instead
            # of producing the extreme stretching seen in v1.6.0.
            stretch_raw = rect.width / ink_w
            stretch = max(0.78, min(1.24, stretch_raw))
            baseline_y = rect.y0 - (uy0 / probe) * base_size
            # Align the measured ink left edge to the source ink left edge.
            start_x = rect.x0 - (ux0 / probe) * base_size * stretch
            x_adv = 0.0
            for (value, mode), adv in zip(runs, advances):
                scale = _SCRIPT_SCALE if mode in {'sup', 'sub'} else 1.0
                shift = (_SUPER_BASELINE_SHIFT * base_size if mode == 'sup' else _SUB_BASELINE_SHIFT * base_size if mode == 'sub' else 0.0)
                point = fitz.Point(start_x + (x_adv / probe) * base_size * stretch, baseline_y + shift)
                page.insert_text(
                    point,
                    value,
                    fontsize=base_size * scale,
                    fontname=fontname,
                    morph=(point, fitz.Matrix(stretch, 1.0)),
                    color=color,
                    render_mode=0,
                    overlay=True,
                )
                x_adv += adv
            return True, stretch
        except Exception:
            pass

    # Built-in font fallback with the same fixed vertical size.
    widths=[]
    try:
        for value, mode in runs:
            fs=base_size * (_SCRIPT_SCALE if mode in {'sup','sub'} else 1.0)
            try: widths.append(float(font.text_length(value, fontsize=fs)))
            except Exception: widths.append(float(fitz.get_text_length(value, fontname=fontname, fontsize=fs)))
        natural=max(0.001, sum(widths))
        stretch=max(0.78, min(1.24, rect.width / natural))
        asc=float(getattr(font,'ascender',1.0) or 1.0)
        baseline=rect.y0 + max(0.5, asc*base_size)
        x=rect.x0
        for (value, mode), width in zip(runs, widths):
            fs=base_size * (_SCRIPT_SCALE if mode in {'sup','sub'} else 1.0)
            shift=(_SUPER_BASELINE_SHIFT*base_size if mode=='sup' else _SUB_BASELINE_SHIFT*base_size if mode=='sub' else 0.0)
            point=fitz.Point(x, baseline+shift)
            page.insert_text(point,value,fontsize=fs,fontname=fontname,
                             morph=(point,fitz.Matrix(stretch,1.0)),color=color,
                             render_mode=0,overlay=True)
            x += width*stretch
        return True, stretch
    except Exception:
        return False, 1.0


def _stable_table_cell_layout(
    page: fitz.Page,
    source_image: Image.Image | None,
    pixel_rect: tuple[int,int,int,int] | None,
    raw_text: str,
    *,
    bold: bool,
    italic: bool,
) -> dict:
    """Preflight one cell: recover lines and local font-size samples without drawing."""
    scripted_words = _split_script_runs_into_words(raw_text)
    visible = ' '.join(''.join(v for v,_m in word) for word in scripted_words).strip()
    fontname, font = _font_spec_for_table_cell(page, visible or ' ', bold=bold, italic=italic)
    geometry = _cell_text_line_geometry(source_image, pixel_rect) if source_image is not None and pixel_rect else []
    # Reject implausible segmentation.  A cell cannot reasonably have more printed
    # rows than words, and dense scan noise often appears as many 1-2 pixel rows.
    word_count=max(1, len(re.findall(r'\S+', visible)))
    if len(geometry) > max(4, min(8, word_count)):
        geometry=[]
    line_texts=[]
    line_runs=[]
    size_samples=[]
    if geometry and visible:
        line_texts=_allocate_text_to_lines(visible,[g['width'] for g in geometry],font)
        line_runs=_script_words_for_allocated_lines(raw_text,line_texts)
        if len(line_texts) != len(geometry):
            geometry=[]; line_texts=[]; line_runs=[]
        else:
            for idx,(txt,g) in enumerate(zip(line_texts,geometry)):
                if not str(txt).strip():
                    continue
                drect=_pixel_rect_to_rotated_page_rect(page,g['pixel_rect'],source_image.size)
                runs=line_runs[idx] if idx < len(line_runs) else [(str(txt),'normal')]
                fs=_estimate_line_font_size_from_rect(page,drect,runs,fontname,font)
                if fs is not None:
                    size_samples.append(fs)
    return {
        'visible':visible,'fontname':fontname,'font':font,'geometry':geometry,
        'line_texts':line_texts,'line_runs':line_runs,'size_samples':size_samples,
    }


def _insert_vector_table_block(
    page: fitz.Page,
    rect: fitz.Rect,
    raw: str,
    *,
    source_image: Image.Image | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> tuple[bool, dict]:
    """Draw a real vector/text table with source-matched stable typography.

    v1.6.3 deliberately does *not* use the v1.6.3 raster-table shortcut.  The
    source raster is measurement-only: grid, cell fills, line positions, colour
    and font-size samples.  Output is real PDF text and vector rules.
    """
    rows, _before, _after = _parse_ocr_table_rows(raw)
    diag={
        'cells':0,'precision_cells':0,'math_cells':0,'colored_cells':0,
        'centered_cells':0,'failed_cells':0,'source_grid':False,
        'body_font_size':0.0,'header_font_size':0.0,'stable_lines':0,
    }
    if not rows or rect.is_empty or rect.width <= 4 or rect.height <= 4:
        return False,diag
    nrows=len(rows)
    ncols=max(1,max(sum(max(1,int(c.get('colspan',1) or 1)) for c in row) for row in rows))
    grid=_table_grid_positions(source_image,bbox,nrows,ncols)
    if grid:
        xfracs,yfracs=grid; diag['source_grid']=True
    else:
        xfracs=[i/ncols for i in range(ncols+1)]
        yfracs=[i/nrows for i in range(nrows+1)]

    def xat(i):
        i=max(0,min(ncols,i)); return rect.x0+xfracs[i]*rect.width
    def yat(i):
        i=max(0,min(nrows,i)); return rect.y0+yfracs[i]*rect.height

    table_px=_normalized_bbox_to_pixel_rect(bbox,source_image.size) if source_image is not None and bbox is not None else None
    if table_px:
        tx0,ty0,tx1,ty1=table_px; tw=max(1,tx1-tx0); th=max(1,ty1-ty0)
        def cell_px(r,c,rs,cs):
            return (int(round(tx0+xfracs[c]*tw)),int(round(ty0+yfracs[r]*th)),
                    int(round(tx0+xfracs[c+cs]*tw)),int(round(ty0+yfracs[r+rs]*th)))
    else:
        def cell_px(r,c,rs,cs): return None

    occupied=set(); placements=[]
    for r,row in enumerate(rows):
        cpos=0
        for cell in row:
            while (r,cpos) in occupied and cpos<ncols: cpos+=1
            if cpos>=ncols: break
            cs=max(1,min(ncols-cpos,int(cell.get('colspan',1) or 1)))
            rs=max(1,min(nrows-r,int(cell.get('rowspan',1) or 1)))
            pxr=cell_px(r,cpos,rs,cs)
            style=_cell_visual_style(source_image,pxr)
            sat=max(style.get('color',(0,0,0)))-min(style.get('color',(0,0,0)))
            inferred_header=bool(r==0 and nrows>1 and sat>0.12)
            bold=bool(cell.get('header') or cell.get('bold') or inferred_header)
            italic=bool(cell.get('italic'))
            raw_text=''.join(cell.get('text') or []).strip()
            layout=_stable_table_cell_layout(page,source_image,pxr,raw_text,bold=bold,italic=italic) if raw_text else None
            placements.append({'r':r,'c':cpos,'rs':rs,'cs':cs,'cell':cell,'pxr':pxr,
                               'style':style,'bold':bold,'italic':italic,'raw':raw_text,'layout':layout})
            for rr in range(r,min(nrows,r+rs)):
                for cc in range(cpos,min(ncols,cpos+cs)): occupied.add((rr,cc))
            cpos+=cs

    # Robust table-wide sizes.  Header/body may differ, but individual cells no
    # longer determine their own size from a single noisy pixel run.
    body_samples=[]; header_samples=[]
    for p in placements:
        if not p['layout']: continue
        vals=p['layout'].get('size_samples') or []
        (header_samples if p['r']==0 else body_samples).extend(vals)
    # Geometric fallback based on median row height when raster line samples are poor.
    row_heights=[max(1.0,yat(i+1)-yat(i)) for i in range(nrows)]
    median_row=float(np.median(row_heights)) if row_heights else 10.0
    body_fallback=max(4.0,min(11.0,median_row*0.46))
    body_size=_robust_font_median(body_samples,body_fallback)
    # Header should not explode merely because a coloured glyph produced a tall ink run.
    header_size=_robust_font_median(header_samples,body_size)
    header_size=max(body_size*0.82,min(body_size*1.28,header_size))
    diag['body_font_size']=round(body_size,3); diag['header_font_size']=round(header_size,3)

    # Backgrounds first.
    for p in placements:
        cr=fitz.Rect(xat(p['c']),yat(p['r']),xat(p['c']+p['cs']),yat(p['r']+p['rs']))
        bg=p['style'].get('background',(1,1,1))
        if max(bg)-min(bg)<0.08 and sum(bg)/3>0.94: bg=(1.0,1.0,1.0)
        page.draw_rect(cr,color=None,fill=bg,overlay=True)

    ok=True
    for p in placements:
        diag['cells']+=1
        raw_text=p['raw']
        if not raw_text: continue
        cr=fitz.Rect(xat(p['c']),yat(p['r']),xat(p['c']+p['cs']),yat(p['r']+p['rs']))
        layout=p['layout']; style=p['style']; success=False
        raw_clean=_strip_light_markup(raw_text); visible=_latex_to_visible_unicode(raw_text)
        if visible != raw_clean: diag['math_cells']+=1
        color=style.get('color') or (0,0,0)
        if max(color)-min(color)>0.12: diag['colored_cells']+=1
        if style.get('align')=='center': diag['centered_cells']+=1
        base_size=header_size if p['r']==0 else body_size

        if layout and layout.get('geometry') and layout.get('line_texts'):
            geom=layout['geometry']; texts=layout['line_texts']; runs_list=layout['line_runs']
            inserted=0
            for idx,(txt,g) in enumerate(zip(texts,geom)):
                txt=re.sub(r'\s+',' ',str(txt)).strip()
                if not txt: continue
                drect=_pixel_rect_to_rotated_page_rect(page,g['pixel_rect'],source_image.size)
                runs=runs_list[idx] if idx<len(runs_list) and runs_list[idx] else [(txt,'normal')]
                line_color=_sample_foreground_color(source_image,g['pixel_rect'])
                good,_stretch=_insert_scripted_line_fixed_size(page,drect,runs,layout['fontname'],layout['font'],base_size,line_color)
                if good: inserted+=1
            expected=len([t for t in texts if str(t).strip()])
            if expected and inserted==expected:
                success=True; diag['precision_cells']+=1; diag['stable_lines']+=inserted

        if not success:
            # Stable fallback: use the global source-derived font size and preserve
            # cell alignment.  Shrink only when the OCR string genuinely cannot fit.
            text=_script_plain_text(raw_text)
            text=re.sub(r'[ \t]+',' ',text); text=re.sub(r' *\n *','\n',text).strip()
            fontname,font=_font_spec_for_table_cell(page,text,bold=p['bold'],italic=p['italic'])
            pad_x=max(0.55,min(2.0,cr.width*0.018)); pad_y=max(0.35,min(1.2,cr.height*0.055))
            inner=fitz.Rect(cr.x0+pad_x,cr.y0+pad_y,cr.x1-pad_x,cr.y1-pad_y)
            amap={'left':fitz.TEXT_ALIGN_LEFT,'center':fitz.TEXT_ALIGN_CENTER,'right':fitz.TEXT_ALIGN_RIGHT}
            align=amap.get(str(p['cell'].get('align') or style.get('align') or 'left').lower(),fitz.TEXT_ALIGN_LEFT)
            size=base_size
            for _ in range(18):
                try:
                    spare=page.insert_textbox(inner,text,fontsize=size,fontname=fontname,color=color,
                                              align=align,lineheight=1.02,overlay=True)
                    if spare>=-0.01:
                        success=True; break
                except Exception: pass
                size*=0.94
                if size<max(2.7,base_size*0.72): break

        if not success:
            diag['failed_cells']+=1; ok=False

    # Vector rules last.
    grid_color,grid_width=_table_rule_style(source_image,bbox,xfracs,yfracs)
    for p in placements:
        cr=fitz.Rect(xat(p['c']),yat(p['r']),xat(p['c']+p['cs']),yat(p['r']+p['rs']))
        page.draw_rect(cr,color=grid_color,width=grid_width,overlay=True)
    return ok,diag

def _insert_hybrid_fidelity_table_block(
    page: fitz.Page,
    rect: fitz.Rect,
    raw: str,
    *,
    source_image: Image.Image | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> tuple[bool, dict]:
    """Preserve the printed table exactly and add a searchable cell-text layer.

    Dense/low-resolution tables are a poor target for cell-by-cell visible
    re-typesetting: tiny OCR/image-geometry errors can amplify into oversized,
    duplicated or misaligned visible text.  The high-fidelity strategy therefore
    keeps the exact table raster used by Unlimited-OCR as the *visible* table and
    adds OCR cell text invisibly at logical cell positions.  This preserves the
    original colour, font, row height, rule thickness and alignment while keeping
    search / copy / selection available.

    The older vector-table renderer remains available as a fallback when the
    source raster or model bbox is unavailable.
    """
    diag = {
        "cells": 0,
        "searchable_cells": 0,
        "math_cells": 0,
        "source_grid": False,
        "visual_crop": False,
    }
    if source_image is None or bbox is None or rect.is_empty or rect.width <= 4 or rect.height <= 4:
        return False, diag

    rows, _before, _after = _parse_ocr_table_rows(raw)
    if not rows:
        return False, diag

    # First preserve the source table pixels exactly.  Do not redraw visible
    # borders/text: that was the source of the v1.6.0 fidelity regression.
    if not _insert_crop(page, rect, source_image, bbox):
        return False, diag
    diag["visual_crop"] = True

    nrows = len(rows)
    ncols = max(1, max(sum(max(1, int(c.get("colspan", 1) or 1)) for c in row) for row in rows))
    grid = _table_grid_positions(source_image, bbox, nrows, ncols)
    if grid:
        xfracs, yfracs = grid
        diag["source_grid"] = True
    else:
        xfracs = [i / ncols for i in range(ncols + 1)]
        yfracs = [i / nrows for i in range(nrows + 1)]

    def xat(i: int) -> float:
        i = max(0, min(ncols, i))
        return rect.x0 + xfracs[i] * rect.width

    def yat(i: int) -> float:
        i = max(0, min(nrows, i))
        return rect.y0 + yfracs[i] * rect.height

    occupied: set[tuple[int, int]] = set()
    placements: list[tuple[int, int, int, int, dict]] = []
    for r, row in enumerate(rows):
        cpos = 0
        for cell in row:
            while (r, cpos) in occupied and cpos < ncols:
                cpos += 1
            if cpos >= ncols:
                break
            cs = max(1, min(ncols - cpos, int(cell.get("colspan", 1) or 1)))
            rs = max(1, min(nrows - r, int(cell.get("rowspan", 1) or 1)))
            placements.append((r, cpos, rs, cs, cell))
            for rr in range(r, min(nrows, r + rs)):
                for cc in range(cpos, min(ncols, cpos + cs)):
                    occupied.add((rr, cc))
            cpos += cs

    for r, c, rs, cs, cell in placements:
        diag["cells"] += 1
        raw_text = "".join(cell.get("text") or []).strip()
        if not raw_text:
            continue

        # Keep search text portable: use normal ASCII signs for scripted values
        # rather than Unicode superscript-minus glyphs.  The layer is invisible,
        # so semantic/searchability is more important than visual script position.
        text = _script_plain_text(raw_text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text).strip()
        if not text:
            continue
        if text != _strip_light_markup(raw_text):
            diag["math_cells"] += 1

        cr = fitz.Rect(xat(c), yat(r), xat(c + cs), yat(r + rs))
        pad_x = max(0.5, min(2.0, cr.width * 0.018))
        pad_y = max(0.35, min(1.4, cr.height * 0.06))
        inner = fitz.Rect(cr.x0 + pad_x, cr.y0 + pad_y, cr.x1 - pad_x, cr.y1 - pad_y)
        if inner.width <= 1 or inner.height <= 1:
            continue

        fontname, _font = _font_spec_for_table_cell(
            page,
            text,
            bold=bool(cell.get("header") or cell.get("bold")),
            italic=bool(cell.get("italic")),
        )
        amap = {"left": fitz.TEXT_ALIGN_LEFT, "center": fitz.TEXT_ALIGN_CENTER, "right": fitz.TEXT_ALIGN_RIGHT}
        align = amap.get(str(cell.get("align") or "").lower(), fitz.TEXT_ALIGN_LEFT)
        line_count = max(1, text.count("\n") + 1)
        size = max(2.5, min(10.0, (inner.height / line_count) * 0.62))
        inserted = False
        for _ in range(24):
            try:
                spare = page.insert_textbox(
                    inner,
                    text,
                    fontsize=size,
                    fontname=fontname,
                    render_mode=3,
                    overlay=True,
                    align=align,
                    lineheight=1.0,
                )
                if spare >= -0.01:
                    inserted = True
                    break
            except Exception:
                pass
            size *= 0.90
            if size < 1.8:
                break
        if inserted:
            diag["searchable_cells"] += 1

    return True, diag


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
            page.show_pdf_page(rect, src, 0, keep_proportion=False, overlay=True)
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


# ---------------------- precision reconstruction text -----------------------

_RECON_FONT_CACHE: dict[str, fitz.Font] = {}
_RECON_FONT_PATHS: dict[str, str] = {}
_BOLD_CATEGORIES = {"title", "header", "heading", "section", "section_header"}
_ITALIC_CATEGORIES: set[str] = set()

_SUPER_MAP = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUB_MAP = str.maketrans("0123456789+-=()aeioxhklmnpstr", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢₒₓₕₖₗₘₙₚₛₜᵣ")

# Script runs are rendered with a smaller real font and a shifted baseline instead
# of relying on Unicode superscript/subscript glyphs. This is especially important
# for the superscript minus U+207B (⁻), which is missing from a number of common
# Windows PDF fonts and otherwise appears as a square / tofu glyph.
_SCRIPT_SCALE = 0.62
_SUPER_BASELINE_SHIFT = -0.43
_SUB_BASELINE_SHIFT = 0.20


def _latex_to_script_source(text: str) -> str:
    """Normalize lightweight OCR LaTeX while preserving ^{...} / _{...}.

    The returned string contains normal Unicode math symbols but keeps scripts in
    structural form so the PDF renderer can position them using ordinary glyphs.
    """
    s = _strip_light_markup(text)
    if not s:
        return ""
    s = s.replace(r"\(", "").replace(r"\)", "")
    s = s.replace(r"\[", "").replace(r"\]", "")
    s = s.replace("$$", "").replace("$", "")
    s = s.replace(r"\left", "").replace(r"\right", "")
    s = s.replace(r"\,", " " ).replace(r"\;", " " ).replace(r"\:", " " )
    s = s.replace(r"\!", "").replace(r"\quad", "  " ).replace(r"\qquad", "    " )

    for cmd in (r"\text", r"\mathrm", r"\operatorname", r"\textrm", r"\mathit", r"\mathbf", r"\textbf"):
        s = _replace_balanced_command(s, cmd, lambda x: x)

    # A degree sign is already a naturally raised glyph, so do not create an
    # additional scripted run for ^\circ.
    s = re.sub(r"\^\{?\\circ\}?", "°", s)
    s = re.sub(r"\^\{?\\degree\}?", "°", s)

    frac = re.compile(r"\\(?:d?frac)\{([^{}]*)\}\{([^{}]*)\}")
    for _ in range(8):
        new = frac.sub(lambda m: f"{m.group(1)}⁄{m.group(2)}", s)
        if new == s:
            break
        s = new
    s = _replace_balanced_command(s, r"\sqrt", lambda x: f"√({x})")

    for token, symbol in sorted(_LATEX_SYMBOLS.items(), key=lambda kv: -len(kv[0])):
        s = s.replace(token, symbol)

    s = s.replace(r"\%", "%").replace(r"\_", "_").replace(r"\&", "&")
    s = s.replace(r"\\", "\n")
    s = re.sub(r"\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle)\b", "", s)
    # Preserve ^ / _ syntax but remove unsupported formatting commands.
    s = re.sub(r"\\[A-Za-z]+", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"°\s+([CFK])\b", r"°\1", s)
    return s.strip()


def _script_runs(text: str) -> list[tuple[str, str]]:
    """Parse normalized text into (text, normal|sup|sub) runs."""
    s = _latex_to_script_source(text)
    if not s:
        return []
    runs: list[tuple[str, str]] = []
    buf: list[str] = []

    def flush():
        if buf:
            value = "".join(buf)
            if value:
                if runs and runs[-1][1] == "normal":
                    runs[-1] = (runs[-1][0] + value, "normal")
                else:
                    runs.append((value, "normal"))
            buf.clear()

    i = 0
    while i < len(s):
        ch = s[i]
        if ch in "^_":
            mode = "sup" if ch == "^" else "sub"
            j = i + 1
            value = ""
            if j < len(s) and s[j] == "{":
                depth = 1; k = j + 1
                while k < len(s) and depth:
                    if s[k] == "{": depth += 1
                    elif s[k] == "}": depth -= 1
                    k += 1
                if depth == 0:
                    value = s[j + 1:k - 1]
                    i = k
                else:
                    buf.append(ch); i += 1; continue
            elif j < len(s):
                # TeX's unbraced script is one token. OCR commonly emits -1 / -2
                # without braces; treat a leading sign plus following digits as one
                # script because this is the intended engineering-unit notation.
                if s[j] in "+-" and j + 1 < len(s) and s[j + 1].isdigit():
                    k = j + 2
                    while k < len(s) and s[k].isdigit(): k += 1
                    value = s[j:k]; i = k
                else:
                    value = s[j]; i = j + 1
            else:
                buf.append(ch); i += 1; continue
            flush()
            value = value.replace("{", "").replace("}", "").strip()
            if value:
                runs.append((value, mode))
            continue
        if ch in "{}":
            i += 1; continue
        buf.append(ch); i += 1
    flush()

    # Merge adjacent same-mode runs.
    merged: list[tuple[str, str]] = []
    for value, mode in runs:
        if not value:
            continue
        if merged and merged[-1][1] == mode:
            merged[-1] = (merged[-1][0] + value, mode)
        else:
            merged.append((value, mode))
    return merged


def _script_plain_text(text: str) -> str:
    return "".join(value for value, _mode in _script_runs(text))


def _split_script_runs_into_words(text: str) -> list[list[tuple[str, str]]]:
    """Split scripted content into words while retaining each run's mode."""
    runs = _script_runs(text)
    words: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for value, mode in runs:
        parts = re.split(r"(\s+)", value)
        for part in parts:
            if not part:
                continue
            if part.isspace():
                if current:
                    words.append(current); current = []
            else:
                if current and current[-1][1] == mode:
                    current[-1] = (current[-1][0] + part, mode)
                else:
                    current.append((part, mode))
    if current:
        words.append(current)
    return words


def _join_script_words(words: Sequence[list[tuple[str, str]]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for idx, word in enumerate(words):
        if idx:
            out.append((" ", "normal"))
        for value, mode in word:
            if out and out[-1][1] == mode:
                out[-1] = (out[-1][0] + value, mode)
            else:
                out.append((value, mode))
    return out


def _latex_to_visible_unicode(text: str) -> str:
    """Convert common inline OCR LaTeX to compact visible Unicode text.

    Reconstructed PDF tables use real PDF text, so simple math is normalized to
    Unicode instead of exposing raw TeX commands. Complex display equations still
    use the vector equation renderer elsewhere.
    """
    s = _strip_light_markup(text)
    if not s:
        return ""

    # Remove common inline/display math delimiters but keep their content.
    s = s.replace(r"\(", "").replace(r"\)", "")
    s = s.replace(r"\[", "").replace(r"\]", "")
    s = s.replace("$$", "").replace("$", "")
    s = s.replace(r"\left", "").replace(r"\right", "")
    s = s.replace(r"\,", " ").replace(r"\;", " ").replace(r"\:", " ")
    s = s.replace(r"\!", "").replace(r"\quad", "  ").replace(r"\qquad", "    ")

    # Text / style wrappers. Preserve the content, not the command.
    for cmd in (r"\text", r"\mathrm", r"\operatorname", r"\textrm", r"\mathit", r"\mathbf", r"\textbf"):
        s = _replace_balanced_command(s, cmd, lambda x: x)

    # Degree notation is frequently emitted as ^\circ / ^{\circ}. Normalize it
    # before generic superscript handling so it does not become a literal "^°".
    s = re.sub(r"\{\}\s*\^\{?\\circ\}?", "°", s)
    s = re.sub(r"\{\}\s*\^\{?\\degree\}?", "°", s)
    s = re.sub(r"\^\{?\\circ\}?", "°", s)
    s = re.sub(r"\^\{?\\degree\}?", "°", s)

    # Readable Unicode fractions for simple table-cell math.
    frac = re.compile(r"\\(?:d?frac)\{([^{}]*)\}\{([^{}]*)\}")
    for _ in range(8):
        new = frac.sub(lambda m: f"{m.group(1)}⁄{m.group(2)}", s)
        if new == s:
            break
        s = new
    s = _replace_balanced_command(s, r"\sqrt", lambda x: f"√({x})")

    def supers(m):
        val = m.group(1)
        converted = val.translate(_SUPER_MAP)
        return converted if converted != val or all(ch in "0123456789+-=()n" for ch in val) else "^" + val

    def subs(m):
        val = m.group(1)
        converted = val.translate(_SUB_MAP)
        return converted if converted != val or all(ch in "0123456789+-=()aeioxhklmnpstr" for ch in val) else "_" + val

    s = re.sub(r"\^\{([^{}]*)\}", supers, s)
    s = re.sub(r"_\{([^{}]*)\}", subs, s)
    s = re.sub(r"\^([0-9+\-=()n])", lambda m: m.group(1).translate(_SUPER_MAP), s)
    s = re.sub(r"_([0-9aeioxhklmnpstr])", lambda m: m.group(1).translate(_SUB_MAP), s)

    for token, symbol in sorted(_LATEX_SYMBOLS.items(), key=lambda kv: -len(kv[0])):
        s = s.replace(token, symbol)

    s = s.replace(r"\%", "%").replace(r"\_", "_").replace(r"\&", "&")
    s = s.replace(r"\\", "\n")
    s = re.sub(r"\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle)\b", "", s)
    # Unknown formatting commands should not leak as raw TeX in a born-digital PDF.
    s = re.sub(r"\\[A-Za-z]+", "", s)
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"°\s+([CFK])\b", r"°\1", s)
    return s.strip()

def _font_spec_for_reconstruction(page: fitz.Page, text: str, category: str) -> tuple[str, fitz.Font]:
    """Choose a visually closer style while keeping wide Unicode coverage."""
    category = (category or "text").lower()
    # CJK needs PyMuPDF's built-in CJK font for reliable glyph coverage.
    has_cjk = any(
        ("\u3400" <= ch <= "\u4dbf") or ("\u4e00" <= ch <= "\u9fff")
        or ("\u3040" <= ch <= "\u30ff") or ("\uac00" <= ch <= "\ud7af")
        for ch in text
    )
    if has_cjk:
        return _font_spec_for_text(page, text)

    if category in _BOLD_CATEGORIES:
        candidates = [
            ("OCRBold", r"C:\\Windows\\Fonts\\arialbd.ttf"),
            ("OCRBold", r"C:\\Windows\\Fonts\\segoeuib.ttf"),
            ("OCRBold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ]
    elif category in _ITALIC_CATEGORIES:
        candidates = [
            ("OCRItalic", r"C:\\Windows\\Fonts\\ariali.ttf"),
            ("OCRItalic", r"C:\\Windows\\Fonts\\segoeuii.ttf"),
            ("OCRItalic", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
        ]
    else:
        candidates = [
            ("OCRFont", r"C:\\Windows\\Fonts\\arial.ttf"),
            ("OCRFont", r"C:\\Windows\\Fonts\\segoeui.ttf"),
            ("OCRFont", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]

    for fontname, path in candidates:
        if not os.path.exists(path):
            continue
        key = os.path.normcase(os.path.abspath(path))
        try:
            if key not in _RECON_FONT_CACHE:
                _RECON_FONT_CACHE[key] = fitz.Font(fontfile=path)
            page.insert_font(fontname=fontname, fontfile=path)
            _RECON_FONT_PATHS[fontname] = path
            return fontname, _RECON_FONT_CACHE[key]
        except Exception:
            continue
    return _font_spec_for_text(page, text)


def _sample_foreground_color(source_image: Image.Image, pixel_rect: tuple[int, int, int, int]) -> tuple[float, float, float]:
    """Estimate printed glyph colour robustly, including blurry low-res scans."""
    x0,y0,x1,y1=pixel_rect
    try:
        crop=np.asarray(source_image.crop((x0,y0,x1,y1)).convert('RGB'),dtype=np.uint8)
        if crop.size==0: return (0.0,0.0,0.0)
        gray=np.asarray(Image.fromarray(crop).convert('L'),dtype=np.uint8)
        mask=_foreground_mask(gray)
        pixels=crop[mask]
        if pixels.shape[0]<4: return (0.0,0.0,0.0)
        mx=pixels.max(axis=1).astype(float); mn=pixels.min(axis=1).astype(float)
        sat=(mx-mn)/np.maximum(mx,1.0)
        coloured=pixels[sat>=0.18]
        if coloured.shape[0]>=max(3,int(0.10*pixels.shape[0])):
            use=coloured
        else:
            # Median antialiased pixels are too pale on upscaled scans.  Use the
            # darker foreground cluster to approximate the original ink colour.
            lum=pixels.mean(axis=1)
            cut=np.percentile(lum,42)
            use=pixels[lum<=cut]
            if use.shape[0]<3: use=pixels
        med=np.median(use,axis=0)
        if float(np.mean(med))>235: return (0.0,0.0,0.0)
        return tuple(float(v)/255.0 for v in med[:3])
    except Exception:
        return (0.0,0.0,0.0)


def _tight_ink_pixel_rect(
    source_image: Image.Image,
    bbox: tuple[float, float, float, float],
) -> tuple[int, int, int, int] | None:
    """Tighten one model region to the actual visible ink/content bounds."""
    width, height = source_image.size
    px0, py0, px1, py1 = _normalized_bbox_to_pixel_rect(bbox, (width, height))
    try:
        crop = np.asarray(source_image.crop((px0, py0, px1, py1)).convert("L"), dtype=np.uint8)
        if crop.size == 0:
            return None
        mask = _foreground_mask(crop)
        ys, xs = np.where(mask)
        if xs.size < 3 or ys.size < 3:
            return None
        pad = 1
        x0 = max(0, int(xs.min()) - pad)
        y0 = max(0, int(ys.min()) - pad)
        x1 = min(crop.shape[1], int(xs.max()) + pad + 1)
        y1 = min(crop.shape[0], int(ys.max()) + pad + 1)
        return (px0 + x0, py0 + y0, px0 + x1, py0 + y1)
    except Exception:
        return None


def _visible_required_stretch(
    display_rect: fitz.Rect,
    text: str,
    fontname: str,
    font: fitz.Font,
) -> float:
    """Estimate horizontal morph needed to make visible glyph ink fill a target box."""
    rect = fitz.Rect(display_rect)
    font_path = _RECON_FONT_PATHS.get(fontname)
    if font_path and os.path.isfile(font_path):
        try:
            probe = 1000
            pil_font = ImageFont.truetype(font_path, probe)
            bx0, by0, bx1, by1 = pil_font.getbbox(text, anchor="ls")
            ink_w_unit = max(0.001, (bx1 - bx0) / probe)
            ink_h_unit = max(0.001, (by1 - by0) / probe)
            fontsize = max(0.5, rect.height / ink_h_unit)
            return rect.width / max(0.001, ink_w_unit * fontsize)
        except Exception:
            pass
    asc = float(getattr(font, "ascender", 1.0) or 1.0)
    desc = float(getattr(font, "descender", -0.25) or -0.25)
    fontsize = max(0.5, rect.height / max(0.1, asc - desc))
    try:
        natural = float(font.text_length(text, fontsize=fontsize))
    except Exception:
        natural = 0.0
    return rect.width / max(0.001, natural)


def _insert_exact_visible_line(
    page: fitz.Page,
    display_rect: fitz.Rect,
    text: str,
    fontname: str,
    font: fitz.Font,
    color: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[bool, float]:
    """Render visible vector text whose *actual glyph ink* matches the source ink box.

    Invisible OCR only needs the PDF text bbox to match. Visible reconstruction is
    stricter: capitals, descenders and punctuation occupy different fractions of the
    font metric box. When the selected TTF is known, Pillow/FreeType measures the
    text-specific ink bounds relative to the baseline; those bounds drive both the
    font size and baseline so the rendered glyph pixels land on the detected source
    pixels much more closely.
    """
    text = re.sub(r"\s+", " ", _clean_text(text)).strip()
    if not text or display_rect.is_empty or display_rect.width <= 0.5 or display_rect.height <= 0.5:
        return False, 0.0

    # Reconstructed pages are created in displayed orientation (rotation 0), so
    # their coordinate system directly matches the OCR raster.
    rect = fitz.Rect(display_rect)
    font_path = _RECON_FONT_PATHS.get(fontname)
    if font_path and os.path.isfile(font_path):
        try:
            probe = 1000
            pil_font = ImageFont.truetype(font_path, probe)
            bx0, by0, bx1, by1 = pil_font.getbbox(text, anchor="ls")
            ink_w_unit = max(0.001, (bx1 - bx0) / probe)
            ink_h_unit = max(0.001, (by1 - by0) / probe)
            fontsize = max(0.5, rect.height / ink_h_unit)
            natural_ink_w = ink_w_unit * fontsize
            stretch = max(0.01, rect.width / natural_ink_w)
            # PIL's ls anchor is baseline-left. Horizontal bbox offset is affected
            # by the x morph, while vertical bbox offset is not.
            baseline = fitz.Point(
                rect.x0 - (bx0 / probe) * fontsize * stretch,
                rect.y0 - (by0 / probe) * fontsize,
            )
            page.insert_text(
                baseline,
                text,
                fontsize=fontsize,
                fontname=fontname,
                morph=(baseline, fitz.Matrix(stretch, 1.0)),
                color=color,
                render_mode=0,
                overlay=True,
            )
            return True, fontsize
        except Exception:
            pass

    # Maximum-compatibility fallback for built-in/CJK fonts where no TTF path is
    # available. This matches the font metric box rather than text-specific ink.
    asc = float(getattr(font, "ascender", 1.0) or 1.0)
    desc = float(getattr(font, "descender", -0.25) or -0.25)
    metric_h = max(0.1, asc - desc)
    fontsize = max(0.5, rect.height / metric_h)
    try:
        natural = float(font.text_length(text, fontsize=fontsize))
    except Exception:
        natural = float(fitz.get_text_length(text, fontname=fontname, fontsize=fontsize))
    if natural <= 0.01:
        return False, 0.0
    stretch = max(0.01, rect.width / natural)
    point = fitz.Point(rect.x0, rect.y0 + asc * fontsize)
    try:
        page.insert_text(
            point,
            text,
            fontsize=fontsize,
            fontname=fontname,
            morph=(point, fitz.Matrix(stretch, 1.0)),
            color=color,
            render_mode=0,
            overlay=True,
        )
        return True, fontsize
    except Exception:
        return False, 0.0



def _measure_script_runs_pil(
    runs: Sequence[tuple[str, str]],
    font_path: str,
    probe: int = 1000,
) -> tuple[tuple[float, float, float, float], float, list[float]]:
    """Measure scripted runs relative to a common normal-text baseline."""
    base_font = ImageFont.truetype(font_path, probe)
    script_font = ImageFont.truetype(font_path, max(1, int(round(probe * _SCRIPT_SCALE))))
    x = 0.0
    ux0 = uy0 = float("inf")
    ux1 = uy1 = float("-inf")
    advances: list[float] = []
    for value, mode in runs:
        if not value:
            advances.append(0.0); continue
        f = script_font if mode in {"sup", "sub"} else base_font
        shift = (_SUPER_BASELINE_SHIFT * probe if mode == "sup" else _SUB_BASELINE_SHIFT * probe if mode == "sub" else 0.0)
        try:
            bx0, by0, bx1, by1 = f.getbbox(value, anchor="ls")
            adv = float(f.getlength(value))
        except Exception:
            bx0, by0, bx1, by1 = (0, -probe * 0.75, max(1, len(value)) * probe * 0.55, probe * 0.15)
            adv = float(bx1 - bx0)
        ux0 = min(ux0, x + bx0); ux1 = max(ux1, x + bx1)
        uy0 = min(uy0, shift + by0); uy1 = max(uy1, shift + by1)
        advances.append(adv)
        x += adv
    if ux0 == float("inf"):
        return (0.0, -probe * 0.75, max(1.0, x), probe * 0.15), x, advances
    return (ux0, uy0, ux1, uy1), x, advances


def _scripted_required_stretch(
    display_rect: fitz.Rect,
    runs: Sequence[tuple[str, str]],
    fontname: str,
    font: fitz.Font,
) -> float:
    font_path = _RECON_FONT_PATHS.get(fontname)
    rect = fitz.Rect(display_rect)
    if font_path and os.path.isfile(font_path):
        try:
            (x0, y0, x1, y1), _adv, _ = _measure_script_runs_pil(runs, font_path)
            unit_h = max(0.001, (y1 - y0) / 1000.0)
            unit_w = max(0.001, (x1 - x0) / 1000.0)
            fontsize = max(0.5, rect.height / unit_h)
            return rect.width / max(0.001, unit_w * fontsize)
        except Exception:
            pass
    plain = "".join(v for v, _m in runs)
    return _visible_required_stretch(rect, plain, fontname, font)


def _insert_exact_scripted_line(
    page: fitz.Page,
    display_rect: fitz.Rect,
    runs: Sequence[tuple[str, str]],
    fontname: str,
    font: fitz.Font,
    color: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> tuple[bool, float]:
    """Render normal/sup/sub runs as real positioned PDF text.

    Script text uses ordinary glyphs (for example ASCII '-' and '1') at a smaller
    font size and shifted baseline. This avoids missing Unicode superscript glyphs
    while keeping the output searchable and selectable.
    """
    runs = [(str(v), str(m)) for v, m in runs if str(v)]
    if not runs:
        return False, 0.0
    rect = fitz.Rect(display_rect)
    if rect.is_empty or rect.width <= 0.5 or rect.height <= 0.5:
        return False, 0.0

    font_path = _RECON_FONT_PATHS.get(fontname)
    if font_path and os.path.isfile(font_path):
        try:
            probe = 1000
            (ux0, uy0, ux1, uy1), _total_adv, advances = _measure_script_runs_pil(runs, font_path, probe)
            ink_h_unit = max(0.001, (uy1 - uy0) / probe)
            ink_w_unit = max(0.001, (ux1 - ux0) / probe)
            base_size = max(0.5, rect.height / ink_h_unit)
            stretch = max(0.01, rect.width / max(0.001, ink_w_unit * base_size))
            baseline_y = rect.y0 - (uy0 / probe) * base_size
            start_x = rect.x0 - (ux0 / probe) * base_size * stretch
            x_adv = 0.0
            for (value, mode), adv in zip(runs, advances):
                scale = _SCRIPT_SCALE if mode in {"sup", "sub"} else 1.0
                shift = (_SUPER_BASELINE_SHIFT * base_size if mode == "sup" else _SUB_BASELINE_SHIFT * base_size if mode == "sub" else 0.0)
                point = fitz.Point(start_x + (x_adv / probe) * base_size * stretch, baseline_y + shift)
                page.insert_text(
                    point,
                    value,
                    fontsize=base_size * scale,
                    fontname=fontname,
                    morph=(point, fitz.Matrix(stretch, 1.0)),
                    color=color,
                    render_mode=0,
                    overlay=True,
                )
                x_adv += adv
            return True, base_size
        except Exception:
            pass

    # Built-in font fallback: metric-based placement using the same ASCII scripts.
    asc = float(getattr(font, "ascender", 1.0) or 1.0)
    desc = float(getattr(font, "descender", -0.25) or -0.25)
    # Superscripts extend above the normal ascender, so reserve extra line height.
    top_units = max(asc, asc * _SCRIPT_SCALE - _SUPER_BASELINE_SHIFT)
    bottom_units = max(-desc, (-desc) * _SCRIPT_SCALE + _SUB_BASELINE_SHIFT)
    base_size = max(0.5, rect.height / max(0.1, top_units + bottom_units))
    widths = []
    for value, mode in runs:
        fs = base_size * (_SCRIPT_SCALE if mode in {"sup", "sub"} else 1.0)
        try: widths.append(float(font.text_length(value, fontsize=fs)))
        except Exception: widths.append(float(fitz.get_text_length(value, fontname=fontname, fontsize=fs)))
    natural = max(0.001, sum(widths))
    stretch = max(0.01, rect.width / natural)
    baseline_y = rect.y0 + top_units * base_size
    x = rect.x0
    try:
        for (value, mode), width in zip(runs, widths):
            fs = base_size * (_SCRIPT_SCALE if mode in {"sup", "sub"} else 1.0)
            shift = (_SUPER_BASELINE_SHIFT * base_size if mode == "sup" else _SUB_BASELINE_SHIFT * base_size if mode == "sub" else 0.0)
            point = fitz.Point(x, baseline_y + shift)
            page.insert_text(point, value, fontsize=fs, fontname=fontname,
                             morph=(point, fitz.Matrix(stretch, 1.0)), color=color,
                             render_mode=0, overlay=True)
            x += width * stretch
        return True, base_size
    except Exception:
        return False, 0.0


def _script_words_for_allocated_lines(text: str, line_texts: Sequence[str]) -> list[list[tuple[str, str]]]:
    """Map source script-aware words onto the line breaks chosen by geometry."""
    words = _split_script_runs_into_words(text)
    result: list[list[tuple[str, str]]] = []
    pos = 0
    for line in line_texts:
        n = len(re.findall(r"\S+", line or ""))
        if n <= 0:
            result.append([]); continue
        group = words[pos:pos + n]
        pos += n
        result.append(_join_script_words(group))
    if pos < len(words) and result:
        extra = _join_script_words(words[pos:])
        if result[-1] and extra:
            result[-1].append((" ", "normal"))
        result[-1].extend(extra)
    return result


def _insert_precision_visible_text_block(
    page: fitz.Page,
    source_image: Image.Image,
    bbox: tuple[float, float, float, float],
    text: str,
    category: str,
) -> tuple[int, int, float, bool]:
    """Use actual scanned geometry and real positioned super/subscript runs."""
    scripted_words = _split_script_runs_into_words(text)
    visible = " ".join("".join(v for v, _m in word) for word in scripted_words).strip()
    if not visible:
        return 0, 0, 0.0, False
    geometry = _detect_text_line_geometry(source_image, bbox)
    if not geometry:
        return 0, 0, 0.0, False

    fontname, font = _font_spec_for_reconstruction(page, visible, category)
    line_texts = _allocate_text_to_lines(visible, [g["width"] for g in geometry], font)
    if not line_texts or not any(line_texts):
        return 0, 0, 0.0, False

    line_run_groups = _script_words_for_allocated_lines(text, line_texts)
    inserted_lines = 0
    inserted_words = 0
    sizes: list[float] = []
    source_size = source_image.size
    word_pos = 0

    for line_idx, (line_text, item) in enumerate(zip(line_texts, geometry)):
        line_text = re.sub(r"\s+", " ", line_text).strip()
        if not line_text:
            continue
        color = _sample_foreground_color(source_image, item["pixel_rect"])
        words = re.findall(r"\S+", line_text)
        line_word_runs = scripted_words[word_pos:word_pos + len(words)]
        word_pos += len(words)
        word_rects = item.get("word_rects") or []
        use_words = (
            2 <= len(words) <= 50
            and len(word_rects) == len(words)
            and len(line_word_runs) == len(words)
            and all((r[2] - r[0]) >= 2 for r in word_rects)
            and all(all(ord(ch) >= 32 for ch in word) for word in words)
        )

        if use_words:
            viable = True
            for word_runs, px_rect in zip(line_word_runs, word_rects):
                drect = _pixel_rect_to_rotated_page_rect(page, px_rect, source_size)
                if drect.width <= 0.5 or drect.height <= 0.5:
                    viable = False; break
                stretch = _scripted_required_stretch(drect, word_runs, fontname, font)
                if not (0.72 <= stretch <= 1.35):
                    viable = False; break
            if viable:
                ok = 0; local_sizes: list[float] = []
                for word_runs, px_rect in zip(line_word_runs, word_rects):
                    drect = _pixel_rect_to_rotated_page_rect(page, px_rect, source_size)
                    success, fs = _insert_exact_scripted_line(page, drect, word_runs, fontname, font, color)
                    if success:
                        ok += 1; local_sizes.append(fs)
                if ok == len(words):
                    inserted_words += ok; inserted_lines += 1; sizes.extend(local_sizes)
                    continue

        drect = _pixel_rect_to_rotated_page_rect(page, item["pixel_rect"], source_size)
        runs = line_run_groups[line_idx] if line_idx < len(line_run_groups) else [(line_text, "normal")]
        success, fs = _insert_exact_scripted_line(page, drect, runs, fontname, font, color)
        if success:
            inserted_lines += 1; sizes.append(fs)

    avg_size = float(sum(sizes) / len(sizes)) if sizes else 0.0
    return inserted_lines, inserted_words, avg_size, inserted_lines > 0


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
    max_pages: int | None = None,
    page_indices: Sequence[int] | None = None,
) -> tuple[str, dict]:
    """Build a new born-digital PDF from Unlimited-OCR layout output.

    Structured reconstruction:
      * normal blocks -> real visible selectable PDF text
      * inline LaTeX -> proper sub/superscripts and mathematical symbols
      * display formulas -> typeset vector equations
      * HTML tables -> real vector rules + real visible selectable PDF text matched to source geometry
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
    if page_indices is None:
        selected_indices = list(range(len(source_doc)))
        if max_pages is not None:
            selected_indices = selected_indices[: max(0, int(max_pages))]
    else:
        selected_indices = [int(i) for i in page_indices]
    if not selected_indices:
        source_doc.close(); out.close()
        raise ValueError("No pages were selected for reconstructed PDF output.")
    if any(i < 0 or i >= len(source_doc) for i in selected_indices):
        source_doc.close(); out.close()
        raise ValueError("Selected page is outside the source PDF range.")
    page_count = len(selected_indices)

    stats = {
        "pages": page_count, "pages_with_ocr": 0,
        "visible_text_blocks": 0, "table_blocks": 0, "equation_blocks": 0,
        "table_cells": 0, "table_precision_cells": 0, "table_math_cells": 0,
        "table_colored_cells": 0, "table_centered_cells": 0, "table_failed_cells": 0,
        "table_fidelity_blocks": 0, "table_searchable_cells": 0,
        "image_blocks": 0, "image_fallback_blocks": 0,
        "unpositioned_text_blocks": 0, "full_page_fallbacks": 0,
        "precision_text_blocks": 0, "precision_text_lines": 0,
        "precision_text_words": 0, "precision_font_size_avg_pt": 0.0,
        "precision_font_size_samples": 0, "block_fit_fallbacks": 0,
        "tight_structured_blocks": 0,
        "geometry": "precision-reconstruction-v1.7.0-global-auto-table",
    }

    try:
        for output_index, source_page_index in enumerate(selected_indices):
            original_page = source_doc[source_page_index]
            display_rect = original_page.rect
            page = out.new_page(width=display_rect.width, height=display_rect.height)
            raw = page_ocr_texts[output_index] if output_index < len(page_ocr_texts) else ""
            blocks = parse_ocr_layout_blocks(raw)
            if blocks: stats["pages_with_ocr"] += 1

            src = page_sources[output_index] if output_index < len(page_sources) else {}
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
                if category in _RASTER_TYPES or not text.strip():
                    continue

                # Precision reconstruction needs the exact OCR raster: model bboxes
                # locate the coarse region, then source pixels recover actual ink rows.
                if source_image is None:
                    source_image = _open_source_image(src or {}, original_page)
                source_size_actual = source_image.size
                target = _normalized_bbox_to_rotated_page_rect(original_page, bbox, source_size_actual)

                # Tables must retain the full model table rectangle. Generic ink
                # tightening can collapse them around cell text and lose light grid
                # rules (the v1.3.0 regression). Equations may still be tightened.
                is_table = category in _TABLE_TYPES or bool(_TABLE_RE.search(text))
                tight_px = None if is_table else _tight_ink_pixel_rect(source_image, bbox)
                structured_target = (
                    _pixel_rect_to_rotated_page_rect(page, tight_px, source_size_actual)
                    if tight_px else target
                )
                if tight_px:
                    stats["tight_structured_blocks"] += 1

                rendered = False
                if is_table:
                    # v1.7 default: restore the stable whole-table HTML/vector layout
                    # used by the attached v1.1 build.  The entire table is solved as
                    # one layout problem, so row/column wrapping stays coherent even
                    # when scan pixels are blurry.  Source pixels provide only robust
                    # global style hints; they never drive per-cell font geometry.
                    rendered = _insert_table_block(
                        page, target, text, source_image=source_image, bbox=bbox
                    )
                    if rendered:
                        stats["table_fidelity_blocks"] += 1
                    else:
                        # Keep the v1.6.3 vector engine only as a fallback for malformed
                        # HTML or a rare insert_htmlbox failure.
                        rendered, table_diag = _insert_vector_table_block(
                            page, target, text, source_image=source_image, bbox=bbox
                        )
                        for key, stat_key in (
                            ("cells", "table_cells"),
                            ("precision_cells", "table_precision_cells"),
                            ("math_cells", "table_math_cells"),
                            ("colored_cells", "table_colored_cells"),
                            ("centered_cells", "table_centered_cells"),
                            ("failed_cells", "table_failed_cells"),
                        ):
                            stats[stat_key] += int(table_diag.get(key, 0) or 0)
                    if rendered:
                        stats["table_blocks"] += 1
                elif _looks_like_display_math(text, category):
                    rendered = _insert_equation_block(page, structured_target, text)
                    if rendered:
                        stats["equation_blocks"] += 1
                else:
                    lines, words, avg_fs, refined = _insert_precision_visible_text_block(
                        page, source_image, bbox, text, category
                    )
                    if refined:
                        rendered = True
                        stats["visible_text_blocks"] += 1
                        stats["precision_text_blocks"] += 1
                        stats["precision_text_lines"] += lines
                        stats["precision_text_words"] += words
                        if avg_fs > 0:
                            n = max(1, lines)
                            stats["precision_font_size_avg_pt"] += avg_fs * n
                            stats["precision_font_size_samples"] += n
                    else:
                        rendered = _insert_visible_text_block(page, target, text, category)
                        if rendered:
                            stats["visible_text_blocks"] += 1
                            stats["block_fit_fallbacks"] += 1

                if not rendered:
                    if _insert_crop(page, target, source_image, bbox):
                        stats["image_fallback_blocks"] += 1

            if not positioned and unpositioned_texts:
                _insert_flow_fallback(page, unpositioned_texts)
            if not positioned and not unpositioned_texts:
                if source_image is None: source_image = _open_source_image(src or {}, original_page)
                buf = io.BytesIO(); source_image.save(buf, format="JPEG", quality=92)
                page.insert_image(page.rect, stream=buf.getvalue(), keep_proportion=False)
                stats["full_page_fallbacks"] += 1

        samples = int(stats.get("precision_font_size_samples", 0) or 0)
        if samples > 0:
            stats["precision_font_size_avg_pt"] = round(
                float(stats["precision_font_size_avg_pt"]) / samples, 2
            )
        else:
            stats["precision_font_size_avg_pt"] = 0.0

        Path(output_pdf).parent.mkdir(parents=True, exist_ok=True)
        out.save(output_pdf, garbage=3, deflate=True)
    finally:
        out.close(); source_doc.close()

    return output_pdf, stats
