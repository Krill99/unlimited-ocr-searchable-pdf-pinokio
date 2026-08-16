# Troubleshooting

## Installation appears to stop after downloading the model

If the terminal has returned to a command prompt after `Model setup complete.`, the download command itself has finished. Return to the Pinokio app page and refresh if the menu has not updated.

## `CUDA available: False`

This launcher requires an NVIDIA CUDA GPU. Update the NVIDIA driver and use **Reinstall / Repair**. Confirm that the environment's PyTorch build can see the GPU.

## Model load is slow on an 8 GB GPU

This is expected when GPU + CPU offloading is required. During startup, the console prints total VRAM, system RAM, GPU model budget and CPU offload budget. Close other GPU/RAM-heavy programs if memory pressure is high.

## CUDA out of memory during OCR

- Close other GPU applications.
- Try **Base** mode if available for the problematic page.
- Process a smaller document or page image.
- Restart the app to release fragmented GPU memory.

## OCR text is correct but Searchable Scan selection geometry is imperfect

Unlimited-OCR detection coordinates are primarily block/layout boxes, not guaranteed word-level boxes. The Searchable Scan builder fits invisible text to those reported regions as closely as possible, but word-by-word selection may still differ from conventional word-level OCR engines.

## Reconstructed PDF does not exactly match the scan

Reconstructed PDF is a born-digital approximation. OCR provides content and layout regions but does not provide the original font family, kerning or every typesetting property. Use **Searchable Scan** when visual fidelity to the original page is the priority.

## Complex tables or equations

Reconstructed PDF supports common HTML tables and common LaTeX/math structures. Extremely complex, malformed or unsupported markup may use a readable fallback rather than reproducing the source typesetting exactly.

## The Web UI does not appear

Open the running app in Pinokio and click **Open Web UI**. The server URL is printed as `PINOKIO_URL=http://127.0.0.1:...` in the terminal.
