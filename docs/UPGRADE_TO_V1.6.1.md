# Upgrade to v1.6.1

Version 1.6.1 fixes portable superscript/subscript rendering in **Reconstructed PDF**.

## What changed

- Negative exponents such as `C^{-1}` and `m^{-2}` no longer rely on Unicode superscript-minus `⁻`.
- The renderer writes ordinary `-1`, `-2`, etc. as smaller PDF text spans with a raised baseline.
- Degree notation such as `{}^\circ C` is normalized to `°C`.
- The same behavior is used in normal precision text and reconstructed table cells.

## Upgrade

1. Stop the app in Pinokio.
2. Extract the v1.6.1 patch into the existing app root and overwrite included files.
3. Keep `env/`, `models/`, and `offload/`.
4. Start the app. No model redownload is required.
