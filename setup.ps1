# One-time setup: Python environment, packages, and the Kokoro voice model.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating Python environment..."
    python -m venv .venv
}
Write-Host "Installing packages..."
& .venv\Scripts\python.exe -m pip install --upgrade pip
& .venv\Scripts\python.exe -m pip install -r requirements.txt

# NVIDIA GPU: swap in the GPU build of ONNX Runtime (with its CUDA/cuDNN libraries) so
# text reading and the voice run on the GPU; the app falls back to the CPU if it can't.
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "NVIDIA GPU found; installing GPU support (about 1.3 GB)..."
    & .venv\Scripts\python.exe -m pip install "onnxruntime-gpu[cuda,cudnn]==1.31.0"
    & .venv\Scripts\python.exe -m pip uninstall -y onnxruntime
    & .venv\Scripts\python.exe -m pip install --force-reinstall --no-deps onnxruntime-gpu==1.31.0
}

New-Item -ItemType Directory -Force models | Out-Null
$base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
foreach ($f in @("kokoro-v1.0.onnx", "voices-v1.0.bin")) {
    if (-not (Test-Path "models\$f")) {
        Write-Host "Downloading $f..."
        Invoke-WebRequest "$base/$f" -OutFile "models\$f.part"
        Move-Item "models\$f.part" "models\$f"
    }
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Host "ffmpeg not on PATH; installing a bundled copy..."
    & .venv\Scripts\python.exe -m pip install imageio-ffmpeg
}
Write-Host "Done. Start the app with run.bat"
