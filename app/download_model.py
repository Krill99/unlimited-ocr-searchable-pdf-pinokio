from pathlib import Path

from huggingface_hub import snapshot_download

from model_patches import patch_model_files

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "models" / "Unlimited-OCR"
MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)

print("Downloading / verifying baidu/Unlimited-OCR model files…", flush=True)
print(f"Destination: {MODEL_DIR}", flush=True)

snapshot_download(
    repo_id="baidu/Unlimited-OCR",
    local_dir=str(MODEL_DIR),
)

print("Model files ready.", flush=True)
print("Applying local compatibility fixes…", flush=True)
info = patch_model_files(MODEL_DIR)
print(f"  generation: {info['modeling_unlimitedocr']}", flush=True)
print(f"  vision buffer: {info['deepencoder']}", flush=True)
print("Model setup complete.", flush=True)
