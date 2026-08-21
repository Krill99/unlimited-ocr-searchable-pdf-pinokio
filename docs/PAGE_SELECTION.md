# PDF page and range selection

Starting with v1.9.0, users can choose which source PDF pages are rasterized and sent to Unlimited-OCR.

## Syntax

- `7` — page 7 only
- `3-12` — pages 3 through 12
- `1-3, 7, 10-15` — pages 1, 2, 3, 7, 10, 11, 12, 13, 14 and 15

Whitespace is allowed around commas and hyphens. Duplicate pages are removed and selections are processed in ascending order. Reversed or out-of-range ranges are rejected before OCR starts.

## Performance

The backend receives the normalized selected page list and rasterizes only those source pages at the OCR DPI. Unselected pages are not converted to images and are not sent to the model.

## PDF output

Searchable Scan and Reconstructed PDF outputs contain only the selected source pages, in source-page order. Filenames identify a subset where practical, for example `report_pages_3-7_searchable.pdf`.

## STOP / partial export

Graceful STOP is measured against the selected workload. If pages `2, 5, 8, 11` were selected and the user stops after pages 2 and 5 complete, the partial PDF contains those two source pages only. Completed OCR results remain cached so either PDF output mode can be built without rerunning OCR.
