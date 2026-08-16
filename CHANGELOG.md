# Changelog

All notable changes to the public release are documented here.

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
