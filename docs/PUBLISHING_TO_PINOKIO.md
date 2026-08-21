# Publishing this launcher to GitHub and Pinokio

## 1. Create a public GitHub repository

Suggested repository name:

```text
unlimited-ocr-searchable-pdf-pinokio
```

Upload the **contents of this release folder to the repository root**. Do not upload a parent folder containing the repository.

The root should immediately contain `pinokio.js`, `install.js`, `start.js`, `README.md` and `app/`.

### Command-line example

```bash
git init
git add .
git commit -m "Initial public release v1.0.0"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/unlimited-ocr-searchable-pdf-pinokio.git
git push -u origin main

git tag v1.0.0
git push origin v1.0.0
```

## 2. Test installation from the public Git repository

Before announcing the project, test a fresh Pinokio installation from the Git repository rather than from your existing development folder. This catches missing files that may have been accidentally excluded by `.gitignore`.

Confirm:

- Install creates the environment.
- The model downloads successfully.
- Start loads the model.
- Open Web UI stays inside Pinokio.
- Image OCR works.
- PDF OCR works.
- Live line-by-line output works.
- Rendered/Markdown toggle works.
- PDF generation remains opt-in.
- Searchable Scan is the default PDF mode when enabled.
- Reconstructed PDF can be selected instead.

## 3. Share the public Git URL

Pinokio is designed to share scripts over Git. Even before Discover listing, a public Git repository can be shared directly with other users.

Pinokio documentation: https://docs.pinokio.computer/

## 4. Apply for the official Discover directory

Pinokio's current Script Policy states that Discover-listed scripts are manually vetted. The documented process is:

1. Request **Publisher Verification** from the Pinokio admin.
2. After verification, receive an invitation to the official **Pinokio Factory** GitHub organization.
3. Transfer the launcher repository to that organization.
4. Request consideration for the Discover page after the repository is transferred/frozen.

Current policy: https://github.com/pinokiocomputer/pinokio#script-policy

The policy currently directs prospective publishers to the Pinokio admin at:
https://x.com/cocktailpeanut

Do not transfer your repository until Pinokio has invited/verified you.

## 5. Suggested GitHub repository description

> Local Baidu Unlimited-OCR app for Pinokio with live rendered OCR, optional Searchable Scan PDFs, and reconstructed PDFs with text, tables, equations and images.

Suggested topics:

```text
pinokio
ocr
pdf
searchable-pdf
unlimited-ocr
huggingface
computer-vision
document-ai
```
