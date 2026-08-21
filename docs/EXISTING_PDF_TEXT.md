# Existing PDF Text Handling

Version 1.4.0 adds a preflight check before OCR for PDF inputs.

## What is checked

Each page is inspected with PyMuPDF for extractable words and characters. A page is marked **usable** when it contains enough plausible text and the extracted encoding is clean enough that adding a second OCR layer would likely create duplicates. Pages can be classified as:

- `usable` - a credible existing searchable/native text layer is present;
- `partial` - some text exists but it is sparse or looks unreliable;
- `none` - no meaningful extractable text was found.

This is intentionally a heuristic. It does not claim that the existing text is semantically perfect.

## Preserve Existing Text

This is the default Searchable Scan policy.

- A page with **usable** native text is copied from the original PDF and receives no additional Unlimited-OCR text layer.
- A page without usable native text receives the normal Unlimited-OCR invisible text layer.
- Sparse/partial legacy text is preserved if present; the OCR layer may also be added because the existing layer was not judged usable.

This policy maximizes original-PDF fidelity and avoids duplicate text on pages with good existing text.

## Replace with Unlimited-OCR

When a page contains any existing extractable text, it is first visually flattened. The old text layer therefore disappears as a searchable PDF object while the visible page appearance is retained as a raster image. The Unlimited-OCR detection text is then inserted as the new searchable layer.

Pages with no existing text are not rasterized; they remain original PDF pages and receive the OCR layer normally.

### Trade-offs

Replacing is the reliable way to remove an old text layer without erasing the visible words that were drawn by that layer. The cost is that replaced pages become rasterized:

- original vector/native text on those pages is no longer vector content;
- file size can increase;
- very high zoom may look less sharp than the original born-digital page.

The app prefers the exact raster already used for Unlimited-OCR so OCR geometry and the flattened page share the same visual coordinate space. If that raster is unavailable, it renders the source page at 300 DPI.

## Mixed PDFs

The decision is page-by-page. For example:

```text
Page 1  usable native text  -> Preserve: keep native / Replace: flatten + OCR
Page 2  scan only           -> original page + OCR
Page 3  sparse old OCR      -> Preserve: keep + OCR / Replace: flatten + OCR
```

Changing Preserve/Replace after OCR has completed rebuilds the PDF from cached OCR results; the model does not rerun.
