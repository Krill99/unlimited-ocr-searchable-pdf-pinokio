# Security

## Local-only design

The application server binds to `127.0.0.1` by default and is intended to be used only from the local computer through Pinokio.

There is no authentication layer. **Do not change the server binding to `0.0.0.0` or expose the port to a LAN/Internet without adding appropriate authentication, TLS and access controls.**

## Document privacy

OCR inference is local after installation. The application does not intentionally upload user documents to a cloud OCR service.

Internet access is required during installation to download Python packages and the `baidu/Unlimited-OCR` model from Hugging Face.

Uploaded pages and generated files are stored temporarily under the local `work/` directory. Old work directories are cleaned on application startup after the configured retention period. Users handling sensitive documents should stop the app and delete `work/` after use if immediate removal is required.

## Model remote code

Unlimited-OCR is loaded with `trust_remote_code=True`, as required by the upstream model. This means Python code downloaded with the model is executed locally. Review the upstream model repository and `docs/MODEL_PATCHES.md` if this matters for your threat model.

## Reporting a vulnerability

For a public fork/repository, please use GitHub's private vulnerability reporting feature if enabled, or contact the repository maintainer privately before posting exploit details in a public issue.
