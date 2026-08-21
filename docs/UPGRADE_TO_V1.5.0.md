# Upgrade to v1.5.0

Version 1.5.0 replaces the webpage's primary lightweight math converter with a local KaTeX renderer.

## Existing Pinokio installation

1. Stop the app.
2. Extract the v1.5.0 patch into the existing app directory and overwrite the included files.
3. Start the app normally.
4. On the first start after the patch, `start.js` attempts to download the pinned KaTeX runtime assets if they are missing.
5. If that optional download fails, the OCR UI still starts with the previous lightweight math fallback. Use **Reinstall / Repair** later to install KaTeX properly.

The 6.7 GB Unlimited-OCR model is not redownloaded if it is already present; the model downloader verifies/reuses the existing files.

## Expected improvement

OCR output such as:

```text
\[Q = \frac{\Delta T}{R} \Rightarrow \text{deltaT} = Q R\]
```

is rendered by KaTeX instead of exposing unsupported commands such as `\Rightarrow` or `\text{}`.

See `docs/MATH_RENDERING.md` for details.
