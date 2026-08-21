# Third-party notices

This repository is an independent community launcher and document-processing UI. It is **not affiliated with, endorsed by, or an official release of Baidu or Pinokio**.

## Baidu Unlimited-OCR

The OCR model and its upstream remote-code implementation are provided by Baidu:

- GitHub: https://github.com/baidu/Unlimited-OCR
- Model: https://huggingface.co/baidu/Unlimited-OCR
- Paper: https://arxiv.org/abs/2606.23050

Unlimited-OCR is distributed under the MIT License. A copy of the upstream license is included at `licenses/BAIDU_UNLIMITED_OCR_LICENSE`.

The model weights are **not bundled in this repository**. The Pinokio installer downloads them from Hugging Face during installation.

This launcher applies two small, local compatibility patches to the downloaded model source at install/start time. The original downloaded files are backed up before modification. See `docs/MODEL_PATCHES.md`.

### Upstream citation

```bibtex
@misc{yin2026unlimitedocrworks,
  title={Unlimited OCR Works},
  author={Youyang Yin and Huanhuan Liu and YY and Qunyi Xie and Chaorun Liu and Shiqi Yang and Shaohua Wang and Zhanlong Liu and Hao Zou and Jinyue Chen and Shu Wei and Jingjing Wu and Mingxin Huang and Zhen Wu and Guibin Wang and Tengyu Du and Lei Jia},
  year={2026},
  eprint={2606.23050},
  archivePrefix={arXiv},
  primaryClass={cs.CV},
  url={https://arxiv.org/abs/2606.23050}
}
```

## Pinokio

Pinokio is maintained separately by Pinokio Computer:

- https://github.com/pinokiocomputer/pinokio
- https://docs.pinokio.computer/

This repository contains Pinokio launcher scripts but does not redistribute the Pinokio application itself.

## Python dependencies

Runtime dependencies are installed from their respective package indexes and remain subject to their own licenses. See `app/requirements.txt` for the dependency list.

## KaTeX

The rendered OCR webpage optionally uses **KaTeX 0.18.4** for local TeX/LaTeX math typesetting. Runtime assets are downloaded from the official KaTeX GitHub release during installation and served locally.

Project: https://github.com/KaTeX/KaTeX

License: MIT

Copyright (c) 2013-2020 Khan Academy and other contributors.

The full KaTeX license is written to `app/vendor/katex/LICENSE` when the runtime assets are installed.
