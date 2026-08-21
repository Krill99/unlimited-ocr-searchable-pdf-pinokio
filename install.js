module.exports = {
  requires: {
    bundle: "ai"
  },
  run: [
    {
      method: "shell.run",
      params: {
        venv: "env",
        message: [
          "uv pip install -r app/requirements.txt",
          "uv pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu128",
          "python app/download_model.py",
          "python app/download_katex.py",
          "python -c \"import torch; print('PyTorch:', torch.__version__); print('CUDA available:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NOT DETECTED'); print('VRAM GiB:', round(torch.cuda.get_device_properties(0).total_memory/1024**3, 2) if torch.cuda.is_available() else 'N/A')\""
        ]
      }
    },
    {
      method: "notify",
      params: {
        html: "Installation complete. The model and local KaTeX math renderer are ready. Click <b>Start</b> to launch Unlimited OCR Searchable PDF."
      }
    }
  ]
}
