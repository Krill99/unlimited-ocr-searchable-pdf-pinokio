# Hardware and platform notes

## Supported accelerator

This launcher currently targets **NVIDIA CUDA GPUs**. The runtime explicitly checks `torch.cuda.is_available()` before loading Unlimited-OCR.

## Known working configuration

A full end-to-end workflow has been exercised on:

- Windows 11
- NVIDIA GeForce RTX 3050
- 8 GB VRAM
- 16 GB system RAM
- GPU + CPU model offloading enabled automatically

On 8 GB VRAM, part of the model remains in system RAM. This is slower than keeping the full model on the GPU but allows the application to run within the available VRAM.

## Practical guidance

- **8 GB VRAM:** known to work with CPU offload; close other GPU-heavy applications.
- **12 GB+ VRAM:** preferable for lower offload overhead.
- **16 GB system RAM:** workable but relatively tight when CPU offload is active.
- **24–32 GB system RAM:** recommended for more headroom with large documents and other applications open.
- **Disk:** allow roughly 12–15 GB of free space for the model, Python environment, packages and temporary working files.

These are launcher/community observations rather than official Baidu minimum requirements.

## Upstream tested software stack

The upstream Unlimited-OCR Transformers instructions currently list a tested stack including Python 3.12.3, Torch 2.10.0, torchvision 0.25.0, Transformers 4.57.1, Pillow 12.1.1 and PyMuPDF 1.27.2.2. This launcher pins the key versions to remain close to that stack.

Upstream reference: https://github.com/baidu/Unlimited-OCR
