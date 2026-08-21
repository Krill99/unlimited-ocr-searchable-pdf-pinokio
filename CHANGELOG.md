## 1.9.0 - PDF page and range selection

- Add **Pages to OCR** controls for PDFs, with **All** as the default.
- Support one page (`7`), one range (`3-12`), and mixed selections (`1-3, 7, 10-15`).
- Validate selections against the source PDF page count before OCR starts.
- Rasterize and OCR **only the selected pages**, reducing preprocessing and model time for partial-document jobs.
- Keep source page numbers visible in live OCR output while also showing progress within the selected set.
- Generated Searchable Scan / Reconstructed PDF outputs contain only the selected pages, in original ascending order.
- Preserve graceful STOP / partial export semantics within the selected workload; e.g. stopping after 3 of 8 selected pages exports those 3 completed selected pages only.
- Add selected-page metadata and filename labels such as `report_pages_3-7_searchable.pdf`.

## 1.8.0 - Graceful stop and partial PDF export

- Make **STOP** on multi-page PDF OCR graceful: the current page is allowed to finish so its final OCR text and detection boxes are not lost, then processing stops before the next page.
- Keep all completed page OCR results in memory after a stop.
- Allow users to enable **Generate PDF** after stopping and build from the cached completed pages without rerunning OCR.
- If PDF generation was already enabled, automatically build a partial Searchable Scan or Reconstructed PDF after the current page finishes.
- Partial outputs contain only completed pages, e.g. `report_partial_17of80_searchable.pdf`; unprocessed pages are not appended.
- Add partial-export metadata to the PDF API and both PDF builders while preserving full-document behaviour.

## 1.7.0 - Stable global table auto-layout

- Restore the attached v1.1-style whole-table renderer as the default for Reconstructed PDF tables.
- Lay out and scale the complete OCR table as one coherent HTML/vector object using `table-layout:auto`, natural row heights and global shrink-to-fit.
- Stop using per-cell pixel geometry as the default table renderer; this removes cascading font-size, wrapping and overlap errors on dense or low-resolution scans.
- Keep real visible/selectable PDF text, vector borders, `rowspan` / `colspan`, inline LaTeX cleanup and portable superscript/subscript rendering.
- Use source pixels only for conservative global styling: table-rule colour and an obviously coloured first/header row.
- Preserve the v1.6.3 geometry-aware vector table engine as a fallback only when the global HTML/table renderer cannot render malformed OCR markup.
- Preserve explicit `<br>` line breaks inside OCR table cells, including cells containing bare LaTeX.

## 1.6.3 - Stable vector table typography

- Restore real visible/selectable PDF text and vector rules as the default Reconstructed PDF table output; source table crops are no longer used as the visible table.
- Use the source raster only as a measurement reference for grid boundaries, text-line positions, colours, alignment and background.
- Estimate a robust table-wide body font size and separate header font size from all reliable cell-line samples instead of independently fitting every cell to noisy pixels.
- Keep vertical font size fixed and permit only conservative horizontal morphing, preventing oversized, tiny or duplicated text on dense/low-resolution tables.
- Improve table-grid recovery when OCR/model bboxes clip the final right/bottom rule and preserve unequal header/body row heights.
- Improve low-resolution line grouping so one printed line is not accidentally split into multiple reconstructed lines.

# Changelog

## 1.6.3 - Table fidelity regression fix

- Replaced aggressive visible per-cell re-typesetting with a high-fidelity hybrid table path.
- Reconstructed PDF tables now preserve the exact source table crop for visible appearance.
- OCR cell text is overlaid invisibly so tables remain searchable/selectable/copyable.
- Preserves original fonts, colours, row heights, grid lines and alignment without geometry amplification on dense/low-resolution tables.
- Vector table rendering remains as a fallback when the source raster is unavailable.

## 1.6.1 - Portable superscript/subscript rendering

- Fix missing-glyph squares for negative superscripts such as `C^{-1}` and `m^{-2}` in Reconstructed PDF.
- Stop depending on the Unicode superscript-minus character `⁻` for visible reconstruction.
- Render superscripts/subscripts as real PDF text runs using ordinary glyphs at a smaller font size with shifted baselines.
- Keep scripted units searchable/selectable while preserving visual exponent placement.
- Normalize OCR degree forms including `{}^\circ` / `^\circ` to a proper degree sign without leaking raw TeX delimiters.
- Apply the portable script renderer to precision paragraph text and geometry-aware table cells; HTML fallback also uses ordinary `-` inside `<sup>`.

## 1.6.0 - Table cell fidelity

- Reconstructed PDF table cells now normalize inline LaTeX such as `\(\lambda\)`, `^\circ`, superscripts and Greek symbols into real visible/selectable PDF text.
- Detect actual printed text-line geometry inside each source table cell and fit PDF text to those ink bounds for closer font size and x/y positioning.
- Sample per-cell text colour from the original raster, including coloured headers such as red text.
- Infer left/center/right and top/middle/bottom placement from the source cell when explicit HTML alignment is unavailable.
- Preserve `<b>/<strong>`, `<i>/<em>`, `align`, `valign`, `rowspan`, and `colspan` hints from OCR table markup.
- Sample light cell backgrounds and table-rule colour from the source raster while keeping the table as vector PDF geometry.
- Add reconstruction diagnostics for precision, math-normalized, coloured and centered table cells.

## 1.5.1 - Import/upgrade repair

- Fixed startup failure when Pinokio launches the backend with `python app/app.py`.
- Removed a broad `ImportError` fallback that could mask the real missing module and report misleading `app is not a package` errors.
- The v1.5.1 patch is cumulative for the backend modules required by v1.4+ / v1.5, including `pdf_text_analysis.py`.
- No model or environment reinstall is required when upgrading an otherwise working installation.

## 1.5.0 - Local KaTeX math rendering

- Replaced the webpage's primary lightweight math converter with locally served KaTeX.
- Added proper rendering for `\Rightarrow`, `\text{}`, matrices, aligned equations, cases, sums, integrals, fractions and broader LaTeX syntax.
- Added conservative OCR repair for malformed `\text word`, `beginarray/endarray`, and common environment spellings.
- Added detection of standalone LaTeX equation lines even when OCR omitted `$...$` or `\[...\]` delimiters.
- Kept the previous lightweight renderer as a fallback when KaTeX is missing or an OCR expression is too malformed to parse.
- KaTeX assets are downloaded once during installation and served locally; no runtime CDN is required.

## 1.4.0 - Existing PDF text preflight and replacement control

- Analyze the existing PDF text layer immediately when a PDF is selected.
- Report whether searchable text is usable on all, some, or none of the pages.
- Add a **Preserve Existing Text** policy for Searchable Scan. Usable native PDF text is retained and a duplicate Unlimited-OCR layer is not added on those pages.
- Add a **Replace with Unlimited-OCR** policy. Pages containing an existing text layer are visually flattened first, removing that text layer, then the Unlimited-OCR detection text becomes the searchable layer.
- Mixed PDFs are handled page-by-page. Pages with no existing text remain original PDF pages and receive the OCR layer normally.
- Rebuilding a Searchable Scan after switching the policy reuses cached OCR results; recognition does not run again.
- Add output statistics for native-text preservation/replacement, skipped OCR overlays and rasterized pages.

## 1.3.1 - Table rendering regression fix

- Fix reconstructed tables becoming tiny or disappearing after v1.3.0 precision tightening.
- Preserve the full Unlimited-OCR table bounding box.
- Render tables as explicit PDF vector grids with selectable cell text.
- Recover printed row/column boundaries from the source raster when strong table rules are present.
- Fall back to equal logical cells for borderless or ambiguous tables.
- Keep the prior HTML table renderer as a compatibility fallback for unusual markup.

All notable changes to the public release are documented here.

## 1.1.0 — 2026-08-16

### Rendered OCR
- OCR-detected figures/images from PDF pages are now cropped from the exact raster sent to Unlimited-OCR and displayed inline in the rendered OCR view.
- Clicking an extracted figure opens the full crop in the local Pinokio Web UI.
- Added rendering for LaTeX `array` environments, including `\begin{array}...\end{array}` and common `beginarray/endarray` OCR variants.
- Expanded Greek-letter rendering across inline math, display math and bare OCR Markdown commands.
- Added support for single-dollar inline math such as `$\alpha + \beta$`.
- Image regions are cached locally per page so switching between Rendered and Markdown views does not repeatedly recrop the source page.

## 1.0.0 — 2026-08-16

Initial public/community release.

### OCR
- Local Baidu Unlimited-OCR inference through Transformers.
- Live line-by-line OCR streaming while a page is being generated.
- Rendered Markdown view with a raw Markdown toggle.
- Image and multi-page PDF input.

### PDF output
- PDF generation is opt-in; OCR text-only is the default workflow.
- **Searchable Scan** is the default PDF mode when PDF generation is enabled.
- Searchable Scan preserves the original PDF page and adds a precisely positioned invisible text layer.
- **Reconstructed PDF** is available as an alternative output using visible OCR text and extracted visual regions.
- Reconstructed PDF renders supported HTML tables and common LaTeX/math structures instead of printing markup literally.

### Pinokio / hardware
- UI opens inside Pinokio rather than launching a separate browser tab.
- Automatic GPU/CPU offloading for constrained NVIDIA VRAM.
- Known working configuration: GeForce RTX 3050 8 GB with 16 GB system RAM using CPU offload.
- Local model download is performed once during installation.

### Packaging
- Runtime/model files excluded from Git.
- Upstream Baidu attribution and MIT license included.
- Public release, security, troubleshooting and Pinokio publishing documentation added.
