# Math rendering

Version 1.5.0 uses **KaTeX** for the OCR webpage's rendered math view.

## Why

Unlimited-OCR can emit LaTeX such as:

```text
\[ Q = \frac{\Delta T}{R} \Rightarrow \text{deltaT} = Q R \]
```

Earlier releases used a small custom converter. That was useful for simple Greek letters and fractions, but unsupported commands such as `\Rightarrow`, `\text{}`, matrices, `aligned`, and `cases` could remain visible as raw LaTeX.

## Local KaTeX

The Pinokio installer downloads a pinned official KaTeX release into:

```text
app/vendor/katex/
```

The UI serves these files locally. No CDN is needed while OCR is running.

Pinned KaTeX version: **0.18.4**.

The runtime bundle includes the minified JavaScript, CSS, and WOFF2 math fonts. KaTeX is MIT licensed.

## Supported math

KaTeX handles a broad range of TeX notation including:

- `\Rightarrow`, `\Leftarrow`, `\Leftrightarrow`
- `\text{}`, `\mathrm{}`, `\mathbf{}`, `\operatorname{}`
- fractions and roots
- Greek symbols
- sums, products and integrals
- arrays and matrices
- `aligned` equations
- `cases`
- superscripts and subscripts
- scalable delimiters

The app recognizes `$...$`, `$$...$$`, `\(...\)` and `\[...\]`. It also detects many standalone OCR equation lines that contain LaTeX commands even when the model omitted delimiters.

## OCR repair layer

Before KaTeX receives an expression, the app performs only conservative repairs for common OCR formatting defects, such as:

```text
\text deltaT
```

becoming:

```text
\text{deltaT}
```

and malformed `beginarray/endarray` spellings being normalized.

If KaTeX still cannot parse an expression, the previous lightweight renderer remains as a fallback instead of breaking the OCR page.
