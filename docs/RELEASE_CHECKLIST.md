# Public release checklist

Before making the GitHub repository public:

- [ ] `VERSION` is correct.
- [ ] `CHANGELOG.md` has a matching release entry.
- [ ] `LICENSE` and `THIRD_PARTY_NOTICES.md` are present.
- [ ] Baidu's upstream MIT license is present in `licenses/`.
- [ ] `models/`, `env/`, `offload/` and `work/` are absent from Git.
- [ ] No model weights (`*.safetensors`, `*.bin`, `*.pt`) are committed.
- [ ] No user PDFs or confidential documents are committed.
- [ ] No local Windows paths such as `C:\\Users\\...` or `E:\\pinokio\\...` are hard-coded.
- [ ] `python -m compileall -q app` passes.
- [ ] `node --check pinokio.js install.js start.js` passes (run individually if your Node version requires it).
- [ ] Fresh install from the Git repository succeeds.
- [ ] OCR has been tested on at least one image and one multi-page PDF.
- [ ] Searchable Scan output is searchable/selectable.
- [ ] Reconstructed PDF has been checked with normal text, a table and an equation.
- [ ] README limitations are still accurate.
- [ ] Create Git tag `v1.0.0`.
