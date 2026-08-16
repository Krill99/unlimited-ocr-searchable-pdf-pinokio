# Contributing

Contributions are welcome.

## Before opening a pull request

1. Keep the application local-first: the server should remain bound to `127.0.0.1` unless a secure remote-access design is explicitly implemented.
2. Do not commit model weights, virtual environments, user PDFs, OCR outputs or temporary work files.
3. Preserve Baidu Unlimited-OCR attribution and its MIT license notice.
4. Keep Searchable Scan and Reconstructed PDF as separate output paths; changes to one should not silently alter the other.
5. Test at least the static checks below.

## Static checks

From the repository root:

```bash
python -m compileall -q app
node --check pinokio.js
node --check install.js
node --check start.js
```

## Mock backend test

The backend supports a mock mode that avoids loading the GPU model:

```bash
# Windows CMD
set UOCR_MOCK=1
python app/app.py

# PowerShell
$env:UOCR_MOCK="1"
python app/app.py
```

Mock mode is for development only and is never enabled by the Pinokio launcher.

## Pull requests

Please explain:
- what changed;
- why it is needed;
- which input types/output modes were tested;
- GPU/VRAM/RAM configuration for inference-related changes.
