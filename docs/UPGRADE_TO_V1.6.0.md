# Upgrade to v1.6.0

Version 1.6.0 improves Reconstructed PDF table fidelity.

## Upgrade

1. Stop the app in Pinokio.
2. Extract the v1.6.0 patch into the existing app root and overwrite included files.
3. Keep `env/`, `models/`, `offload/`, and runtime data.
4. Start the app normally.

No model redownload or dependency reinstall is required.

## Key changes

- correct inline LaTeX/Greek/superscript rendering inside table cells;
- source-matched table cell text size and position;
- source text colour and alignment detection;
- bold/italic/alignment markup hints;
- sampled cell background and grid colour;
- additional table reconstruction diagnostics.
