# Unlimited OCR – Searchable PDF for Pinokio

A local Pinokio app powered by **Baidu Unlimited-OCR** for OCR'ing images and PDFs, viewing OCR output live as rendered Markdown, and optionally generating searchable or reconstructed PDFs.

> **Independent community project.** This launcher is not affiliated with or endorsed by Baidu or Pinokio. The Unlimited-OCR model is downloaded separately from Hugging Face during installation.

## Features

- **Local OCR** using `baidu/Unlimited-OCR`.
- **Live line-by-line output** while each page is being generated.
- **Rendered Markdown / raw Markdown toggle** in the Web UI.
- **Inline extracted figures/images** in the rendered OCR view for OCR-detected PDF visual regions.
- **Math rendering enhancements** including LaTeX `array` / `beginarray` structures and Greek alphabet commands.
- **PDF generation is opt-in** — OCR text-only is the default workflow.
- **Searchable Scan** is the default PDF option when PDF generation is enabled.
- **Reconstructed PDF** alternative with visible text, extracted images/figures, structured tables and common mathematical notation.
- **Original-page preservation** in Searchable Scan mode.
- **8 GB VRAM support through GPU + CPU offloading**.
- **Pinokio internal Web UI** — no separate Chrome/Edge tab is required.
- Local server binds to **`127.0.0.1`**.

## Output modes

### OCR text only — default

Leave **Generate PDF** unticked. OCR text is streamed to the page as it is produced. The UI renders the Markdown by default, and the source can be viewed with the **MARKDOWN** toggle.

### Searchable Scan — default PDF mode

When **Generate PDF** is enabled, Searchable Scan is selected by default.

The original PDF pages are kept visually intact while an invisible searchable/selectable text layer is fitted to Unlimited-OCR's detected regions.

Best when **visual fidelity to the original scan matters most**.

### Reconstructed PDF

Creates a new born-digital PDF using visible OCR text and detected visual regions.

The reconstruction engine supports:

- visible text and headings;
- supported HTML/Markdown tables rendered as PDF tables;
- common LaTeX/math notation rendered as mathematical typography;
- figures, charts, diagrams and image regions cropped from the OCR page raster.

Best when **clean visible text and document reflow are more important than pixel-identical appearance**.

## Requirements

- Pinokio.
- NVIDIA CUDA-capable GPU.
- Internet connection for the one-time installation/model download.
- Sufficient disk space for the ~6.7 GB model plus Python/PyTorch environment and working files.

### Known working low-VRAM configuration

The launcher has been exercised successfully on an **NVIDIA GeForce RTX 3050 with 8 GB VRAM and 16 GB system RAM** using automatic GPU + CPU model offloading.

For lower offload overhead, 12 GB+ VRAM and additional system RAM are preferable. See [`docs/HARDWARE.md`](docs/HARDWARE.md).

## Install with Pinokio

### From a public Git repository

Once this source is published to GitHub, install the repository through Pinokio. Pinokio will use the launcher files at the repository root.

1. Open the app in Pinokio.
2. Click **Install**.
3. Wait for dependencies and the Unlimited-OCR model to download.
4. Confirm the install log reports `CUDA available: True`.
5. Click **Start**.
6. When loading finishes, click **Open Web UI**. The interface opens inside Pinokio.

### Local development folder

Copy the repository folder into Pinokio's `api` directory, refresh/restart Pinokio, then use **Install** and **Start** in the same way.

## Basic workflow

1. Upload an image or PDF.
2. For PDFs, choose whether to enable **Generate PDF**.
3. If generating a PDF, leave **Searchable Scan** selected or choose **Reconstructed PDF**.
4. Click **START OCR**.
5. Watch OCR text appear progressively in the output panel.
6. Switch between **RENDERED** and **MARKDOWN** at any time. In Rendered mode, detected figures are shown inline and supported math/arrays are typeset.
7. If PDF generation is enabled, download the generated PDF when processing finishes.

You can also enable PDF generation or switch PDF mode after OCR finishes; the cached OCR result can be reused without rerunning recognition.

## Installation details

`install.js`:

1. creates Pinokio's isolated `env`;
2. installs the application dependencies;
3. installs CUDA PyTorch / torchvision;
4. downloads `baidu/Unlimited-OCR` into `models/Unlimited-OCR`;
5. applies small local compatibility patches to the downloaded model source;
6. prints a CUDA/GPU verification summary.

The model is **not downloaded again on every Start**.

The pinned dependency versions intentionally stay close to Baidu's published Transformers inference stack. See the upstream project for its current reference environment:
https://github.com/baidu/Unlimited-OCR

## Screenshots

### Main OCR Interface

Upload images or PDFs and view OCR results live as they are generated.

![Unlimited OCR main interface](screenshots/main-interface.png)

### Rendered Markdown OCR

OCR output can be viewed as rendered Markdown or switched to the raw Markdown source.

![Rendered Markdown OCR output](screenshots/rendered-markdown.png)

### PDF Output Options

PDF generation is optional. When enabled, users can choose between Searchable Scan and Reconstructed PDF.

![PDF output options](screenshots/pdf-options.png)

## Repository layout

```text
.
├── app/
│   ├── app.py                 # FastAPI backend + OCR streaming
│   ├── index.html             # local Web UI
│   ├── searchable_pdf.py      # Searchable Scan builder
│   ├── reconstructed_pdf.py   # reconstructed PDF builder
│   ├── model_patches.py       # local upstream compatibility patches
│   ├── download_model.py      # one-time Hugging Face model download
│   └── requirements.txt
├── docs/
├── licenses/
├── icon.png
├── install.js
├── start.js
├── pinokio.js
├── pinokio.json
├── LICENSE
├── THIRD_PARTY_NOTICES.md
├── CHANGELOG.md
└── VERSION
```

## Privacy and security

After installation, OCR is performed locally. User documents are not intentionally sent to a cloud OCR API. The server binds to `127.0.0.1` and has no authentication, so it should not be exposed directly to a network.

Temporary uploaded/generated files are stored under `work/`. See [`SECURITY.md`](SECURITY.md) for details.

## Limitations

- This launcher currently requires NVIDIA CUDA.
- Unlimited-OCR's detection boxes are generally layout/block-level; Searchable Scan text selection may therefore be less word-precise than a word-level OCR engine even when search works correctly.
- Reconstructed PDF cannot recover the exact original font family, kerning or every layout property from a scan.
- The Web UI renders OCR-detected image/figure regions; a visual region the upstream model does not identify cannot be extracted automatically.
- Complex or malformed tables/equations may use a readable fallback.
- OCR accuracy remains dependent on the upstream model and source image quality.

## Model compatibility patches

For transparency, the launcher patches two behaviours in the locally downloaded remote-code model: explicit generation padding/masking and the deterministic vision `position_ids` buffer. Original downloaded source files are backed up before modification.

See [`docs/MODEL_PATCHES.md`](docs/MODEL_PATCHES.md).

## Troubleshooting

See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

## Sharing / Pinokio Discover

See [`docs/PUBLISHING_TO_PINOKIO.md`](docs/PUBLISHING_TO_PINOKIO.md) for a GitHub release checklist and Pinokio's current Discover submission process.

## License and attribution

The launcher/application code in this repository is released under the **MIT License**. See [`LICENSE`](LICENSE).

Baidu Unlimited-OCR is also MIT-licensed; the upstream copyright/license notice is preserved in [`licenses/BAIDU_UNLIMITED_OCR_LICENSE`](licenses/BAIDU_UNLIMITED_OCR_LICENSE). Model weights are downloaded separately and are not included in this repository.

Full third-party notices and the upstream paper citation are in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
