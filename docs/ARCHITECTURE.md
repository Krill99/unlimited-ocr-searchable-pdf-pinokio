# Architecture

```text
Pinokio
  │
  ├─ install.js
  │    ├─ creates isolated env
  │    ├─ installs CUDA PyTorch + dependencies
  │    └─ downloads baidu/Unlimited-OCR once
  │
  └─ start.js
       └─ FastAPI server (127.0.0.1)
             │
             ├─ Web UI (app/index.html)
             │
             ├─ OCR engine
             │    ├─ rasterize PDF pages for recognition
             │    ├─ Unlimited-OCR inference
             │    └─ live NDJSON line streaming
             │
             ├─ Searchable Scan builder
             │    └─ original PDF + invisible fitted OCR text
             │
             └─ Reconstructed PDF builder
                  ├─ visible OCR text
                  ├─ structured tables
                  ├─ common math/equations
                  └─ cropped image/figure regions
```

## OCR data flow

PDF pages are rasterized for OCR only. The raster dimensions are retained because Unlimited-OCR reports normalized detection coordinates. The final Searchable Scan is built from the **original PDF**, not from the rasterized OCR screenshots.

## Live OCR

The backend temporarily replaces the upstream streamer class with a callback streamer while a page is being generated. Complete generated lines are sent to the browser over an NDJSON streaming response. The final decoded model output is retained separately so PDF construction uses the exact complete OCR result.

## Searchable Scan

`app/searchable_pdf.py` maps normalized model boxes back to the original PDF coordinate system and inserts invisible text. Text geometry is fitted to the reported OCR rectangles while preserving the original visible page.

## Reconstructed PDF

`app/reconstructed_pdf.py` creates new PDF pages. Text becomes visible PDF text, supported table markup becomes real table structures, common math markup is typeset, and visual regions such as figures/charts are cropped from the OCR raster and placed into the new PDF.
