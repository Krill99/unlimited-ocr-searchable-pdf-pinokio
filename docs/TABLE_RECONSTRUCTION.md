# Table reconstruction

Version 1.7.0 uses a **global auto-layout vector/text strategy** for Reconstructed PDF tables.

The design is intentionally based on the stable table path from the earlier v1.1 build. Dense and low-resolution tables are treated as one layout problem rather than dozens of independent cell-geometry problems.

For every Unlimited-OCR table block the app:

1. keeps the full model table bounding box;
2. parses the OCR HTML table, including rows, cells, `rowspan`, `colspan`, bold/italic hints and line breaks;
3. normalizes common inline OCR math such as `\lambda`, degrees and superscripts/subscripts into visible/selectable PDF text;
4. passes the **whole table** to PyMuPDF's HTML/Story layout engine with `table-layout:auto`;
5. lets the layout engine determine column widths, text wrapping and natural row heights across the table as a single coherent system;
6. globally shrinks the complete table when needed to fit the Unlimited-OCR table rectangle instead of independently stretching individual cells;
7. keeps table rules as vector PDF geometry and cell content as real visible/selectable PDF text;
8. samples the source raster only for conservative whole-table styling such as rule colour and a clearly coloured first/header row;
9. falls back to the v1.6.3 geometry-aware vector renderer only if the global HTML renderer cannot render malformed table markup.

## Why this is more stable

The v1.6.x per-cell renderer could amplify a one- or two-pixel segmentation error into a large font-size or baseline error. On a dense scan, different cells could therefore appear oversized, tiny, duplicated or vertically misaligned.

The v1.7.0 renderer avoids that failure mode by keeping typography and wrapping globally consistent. This may be less mathematically "pixel-fitted" per cell, but it is usually visually closer to the original table as a whole.

## Fidelity limits

Unlimited-OCR does not provide the original font file, exact kerning or source application styles. The reconstructed table therefore uses locally available fonts and a global PDF layout. The goal is stable structural and typographic similarity while preserving true PDF text—not a raster screenshot of the original table.
