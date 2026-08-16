# Changelog

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
