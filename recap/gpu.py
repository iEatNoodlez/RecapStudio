"""NVIDIA GPU support for the ONNX models (voice and text reading).

Works with either ONNX Runtime build: with the CPU-only build, or when CUDA can't
be loaded, everything simply runs on the CPU.
"""
import logging
import threading

import onnxruntime as ort

CUDA = "CUDAExecutionProvider"
_lock = threading.Lock()
_ready = None


def available() -> bool:
    """True when ONNX Runtime can run models on an NVIDIA GPU."""
    global _ready
    with _lock:
        if _ready is None:
            try:
                if hasattr(ort, "preload_dlls"):
                    ort.preload_dlls()  # CUDA/cuDNN from the nvidia-* packages
                _ready = CUDA in ort.get_available_providers()
            except Exception as e:  # missing or broken CUDA libraries
                logging.warning("GPU not usable, running on the CPU: %s", e)
                _ready = False
        return _ready


def free_memory_mb() -> int | None:
    """Free video memory on the first NVIDIA GPU, or None if it can't be read."""
    import subprocess
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


def providers(use_gpu: bool) -> list[str]:
    return [CUDA, "CPUExecutionProvider"] if use_gpu and available() else ["CPUExecutionProvider"]


def device_of(session) -> str:
    """'GPU' or 'CPU': what a session actually ended up running on."""
    try:
        return "GPU" if session.get_providers()[0] == CUDA else "CPU"
    except Exception:
        return "CPU"
