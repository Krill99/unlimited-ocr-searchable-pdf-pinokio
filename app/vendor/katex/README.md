# KaTeX runtime assets

The installer downloads the pinned KaTeX release into this directory so the OCR UI can render LaTeX locally without a CDN.

Pinned version: **0.18.4**

Runtime files (`katex.min.js`, `katex.min.css`, and `fonts/*.woff2`) are intentionally not committed here. They are downloaded from the official KaTeX GitHub release during Pinokio installation. If they are missing, `start.js` makes one optional repair attempt before launching the app.
