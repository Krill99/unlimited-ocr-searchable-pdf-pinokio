# Upgrade to v1.7.0

v1.7.0 changes only Reconstructed PDF table rendering. Searchable Scan, OCR inference, KaTeX webpage rendering, existing-text preflight and normal precision text reconstruction are unchanged.

## What changed

- Reconstructed tables default to the stable whole-table auto-layout renderer.
- Per-cell pixel geometry is no longer the default table path.
- Tables remain real vector/text PDF content; no source table crop is used as the visible output.
- The v1.6.3 vector cell renderer remains available internally as a fallback.

No model or environment reinstall is required.
