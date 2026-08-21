from __future__ import annotations

import argparse
import base64
import hashlib
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

KATEX_VERSION = "0.18.4"
KATEX_URL = f"https://github.com/KaTeX/KaTeX/releases/download/v{KATEX_VERSION}/katex.zip"
EXPECTED_SHA384_B64 = {
    "katex.min.js": "ykMNcWQhhTUb0YV9SPpPUFURHZ+tWmubkakGBP+OgNK/UXdO2gtzglWx0Rj9hnO3",
    "katex.min.css": "u1zONI5gPXUx0UKI62c75/zww972y0v2rSK5ZYlVdS6xEuWDeZWUI66v6t1gvlXJ",
}
KATEX_LICENSE = """The MIT License (MIT)

Copyright (c) 2013-2020 Khan Academy and other contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the \"Software\"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

APP_DIR = Path(__file__).resolve().parent
DEST = APP_DIR / "vendor" / "katex"


def _valid_existing() -> bool:
    js = DEST / "katex.min.js"
    css = DEST / "katex.min.css"
    fonts = DEST / "fonts"
    return js.is_file() and css.is_file() and fonts.is_dir() and any(fonts.glob("*.woff2"))


def _sha384_b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")


def install_katex() -> None:
    print(f"Downloading KaTeX {KATEX_VERSION} for local math rendering…", flush=True)
    req = urllib.request.Request(KATEX_URL, headers={"User-Agent": "Unlimited-OCR-Pinokio/1.5"})
    with urllib.request.urlopen(req, timeout=120) as response:
        archive = response.read()

    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        names = set(zf.namelist())
        prefix = "katex/"
        required = [prefix + "katex.min.js", prefix + "katex.min.css"]
        missing = [name for name in required if name not in names]
        if missing:
            raise RuntimeError(f"KaTeX release archive is missing required files: {missing}")

        extracted: dict[str, bytes] = {
            "katex.min.js": zf.read(prefix + "katex.min.js"),
            "katex.min.css": zf.read(prefix + "katex.min.css"),
        }
        for filename, expected in EXPECTED_SHA384_B64.items():
            actual = _sha384_b64(extracted[filename])
            if actual != expected:
                raise RuntimeError(f"KaTeX integrity check failed for {filename}")

        tmp = DEST.with_name("katex.tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        (tmp / "fonts").mkdir(parents=True, exist_ok=True)
        for filename, data in extracted.items():
            (tmp / filename).write_bytes(data)

        font_names = [n for n in names if n.startswith(prefix + "fonts/") and n.endswith(".woff2")]
        if not font_names:
            raise RuntimeError("KaTeX release archive contains no WOFF2 fonts")
        for name in font_names:
            (tmp / "fonts" / Path(name).name).write_bytes(zf.read(name))

        (tmp / "LICENSE").write_text(KATEX_LICENSE, encoding="utf-8")
        (tmp / "VERSION").write_text(KATEX_VERSION + "\n", encoding="utf-8")
        if DEST.exists():
            shutil.rmtree(DEST)
        tmp.rename(DEST)

    print(f"KaTeX {KATEX_VERSION} ready: {DEST}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--if-missing", action="store_true")
    parser.add_argument("--optional", action="store_true")
    args = parser.parse_args()

    if args.if_missing and _valid_existing():
        print(f"KaTeX {KATEX_VERSION}: local assets already present.", flush=True)
        return 0
    try:
        install_katex()
        return 0
    except Exception as exc:
        if args.optional:
            print(f"WARNING: Could not install local KaTeX assets: {exc}", flush=True)
            print("The app will start with its lightweight math fallback renderer.", flush=True)
            return 0
        print(f"ERROR: Could not install KaTeX: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
