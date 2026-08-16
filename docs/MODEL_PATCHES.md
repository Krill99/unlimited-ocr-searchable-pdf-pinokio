# Local model compatibility patches

Unlimited-OCR is downloaded from Hugging Face during installation. The launcher then applies two small, idempotent source patches to the locally downloaded remote-code files.

The patches are implemented in `app/model_patches.py`.

## 1. Explicit generation mask and padding token

The launcher adds an explicit `attention_mask` and `pad_token_id` to the model generation kwargs. This prevents ambiguous padding behaviour and removes the Transformers warning seen when the padding token is the same as the EOS token.

A runtime wrapper around `model.generate()` provides a second safeguard if an upstream source revision no longer matches the text patch pattern.

## 2. Vision `position_ids` buffer

The CLIP-style vision encoder constructs `position_ids` deterministically using `torch.arange(...)`. The launcher marks this buffer as `persistent=False`, preventing Transformers from treating it as a learned checkpoint tensor that should have been present in the safetensors file.

## Backups

Before the first modification, the original downloaded files are saved beside them as:

- `modeling_unlimitedocr.py.pinokio-original`
- `deepencoder.py.pinokio-original`

The patch operation is idempotent and reports `already patched` on later starts.

## Why patch downloaded code instead of vendoring it?

The public launcher intentionally does not redistribute Baidu's model source or weights. It downloads the upstream model, keeps the upstream license notice, and applies narrowly scoped compatibility changes locally.

If the upstream implementation changes and a pattern can no longer be located, the patcher leaves that file unchanged and reports the condition rather than blindly rewriting unknown code.
