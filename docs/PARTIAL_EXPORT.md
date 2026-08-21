# Graceful Stop and Partial PDF Export

Starting with v1.8.0, multi-page PDF OCR can be stopped without discarding completed work.

## Behaviour

When **STOP** is pressed during a PDF page:

1. The current page is allowed to finish so the final Unlimited-OCR output and detection boxes are complete.
2. The app stops before starting the next page.
3. OCR results for all completed pages remain cached in the browser session.
4. OCR text remains copyable.
5. A Searchable Scan or Reconstructed PDF can be generated from only the completed pages.

Example: if a document has 80 pages and STOP is pressed during page 17, the app finishes page 17 and can create `document_partial_17of80_searchable.pdf`. The output PDF contains pages 1 through 17 only.

## Why STOP waits for the current page

Aborting the live HTTP stream in the middle of model generation can lose the final OCR detection tokens and bounding boxes for that page. A page-boundary stop preserves a complete and reliable result.

## PDF generation

If **Generate PDF** was enabled before OCR started, the partial PDF is built automatically after the stop. If it was disabled, it can be enabled after stopping; the app reuses cached OCR results and does not rerun recognition.

Both **Searchable Scan** and **Reconstructed PDF** support partial export.


## Interaction with page selection

From v1.9.0 onward, partial progress is relative to the selected page set. A job selecting 10 pages from a 100-page PDF reports progress as completed/10 while retaining each original source page number. Partial PDF output includes only completed selected pages.
