# Upgrade to v1.5.1

This is a cumulative repair release for v1.5.x.

## Why

v1.5.0 could show `ModuleNotFoundError: No module named app.searchable_pdf; app is not a package` when the first sibling-module import failed. This was especially likely when v1.5.0 was applied directly over a pre-v1.4 installation that did not yet contain `app/pdf_text_analysis.py`.

## Upgrade

1. Stop the app in Pinokio.
2. Copy the patch contents over the app root and overwrite existing files.
3. Start the app again.

No model redownload is required.
