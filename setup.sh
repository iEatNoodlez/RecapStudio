#!/usr/bin/env bash
# One-time setup on Linux, from source: the Python environment, the packages (with Qt for the window) and the
# Kokoro voice model. Needs Python 3.12 or newer, with its venv module (Ubuntu and Debian: python3.12-venv).
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  PY=python3.12
  command -v "$PY" >/dev/null 2>&1 || PY=python3
fi
if ! "$PY" -c 'import sys; sys.exit(sys.version_info[:2] < (3, 12))' 2>/dev/null; then
  echo "Recap Studio needs Python 3.12 or newer, and $PY isn't. Install python3.12 (with python3.12-venv on Ubuntu"
  echo "and Debian), or run this again with PYTHON=/path/to/python3.12 ./setup.sh"
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creating Python environment..."
  "$PY" -m venv .venv
fi
echo "Installing packages..."
.venv/bin/python -m pip install --upgrade pip
# the window: Qt's web view (Chromium, like Windows' Edge window), all from pip
.venv/bin/python -m pip install -r requirements.txt "pywebview[qt]"

# NVIDIA GPU: swap in the GPU build of ONNX Runtime (with its CUDA/cuDNN libraries) so text reading and the voice
# run on the GPU; the app falls back to the CPU if it can't.
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  echo "NVIDIA GPU found; installing GPU support (about 1.6 GB)..."
  .venv/bin/python -m pip install "onnxruntime-gpu[cuda,cudnn]==1.31.0"
  .venv/bin/python -m pip uninstall -y onnxruntime
  .venv/bin/python -m pip install --force-reinstall --no-deps onnxruntime-gpu==1.31.0
fi

mkdir -p models
base=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  if [ ! -f "models/$f" ]; then
    echo "Downloading $f..."
    .venv/bin/python -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' \
      "$base/$f" "models/$f.part"
    mv "models/$f.part" "models/$f"
  fi
done

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg isn't installed; installing a bundled copy (it can't use the graphics card to encode). Your"
  echo "distribution's ffmpeg is better: sudo apt install ffmpeg (Ubuntu, Debian), or from RPM Fusion on Fedora."
  .venv/bin/python -m pip install imageio-ffmpeg
fi
echo "Done. Start the app with ./run.sh"
