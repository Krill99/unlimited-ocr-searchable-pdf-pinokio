# Searchable Scan geometry

Version 1.2.0 adds an image-guided refinement stage to the invisible text layer used by **Searchable Scan**.

## Why this exists

Unlimited-OCR commonly returns one detection rectangle for an entire paragraph. A PDF text layer created directly from that block rectangle can be searchable, but selection highlights may occupy the whole paragraph box rather than the physical printed lines.

## Refinement pipeline

For each normal text block with a detection box:

1. The app uses the exact 200-DPI page raster that was sent to Unlimited-OCR.
2. The model's `0..999` detection rectangle is mapped to that raster.
3. A lightweight image projection step detects the physical rows containing printed ink. No second OCR engine is used.
4. The recognized paragraph is split across those detected rows using observed line widths and font metrics.
5. Each hidden PDF line is fitted to the detected line rectangle.
6. If the number of detected word regions exactly matches the words assigned to a line, each word is fitted individually for more natural selection geometry.

The original PDF artwork is never replaced or redrawn. Only invisible PDF text is added.

## Conservative fallback

Image-guided refinement is deliberately skipped for region types where pixel projections are unlikely to represent ordinary reading lines, including tables, equations, figures, charts and images. It also falls back when segmentation is ambiguous. Those regions use the prior precise block-fit method.

This conservative fallback is important: a slightly broader but correct searchable region is preferable to a confidently misplaced text layer.

## What improves

- Drag-selection follows printed lines more closely.
- Search highlights are less likely to span empty vertical space in a paragraph.
- Paragraphs returned by the model as one long string can still receive multiple physical PDF text lines.
- Word selection can be substantially tighter when word segmentation is reliable.

## Remaining limitation

The refinement is image geometry, not a second OCR pass. It cannot invent word/line structure that is visually ambiguous, and the recognized wording itself still comes entirely from Unlimited-OCR.
