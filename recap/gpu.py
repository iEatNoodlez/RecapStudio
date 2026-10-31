"""NVIDIA GPU support for the ONNX models (voice and text reading).

Works with either ONNX Runtime build: with the CPU-only build, or when CUDA can't
be loaded, everything simply runs on the CPU.

The installer and the portable zip carry the CPU-only build in their own Python, which an installed copy can't
change (Program Files is read-only). GPU support is an optional download instead (Settings → Performance, or
first-run setup's first page): the GPU build of ONNX Runtime with NVIDIA's CUDA and cuDNN libraries, the wheels
setup.ps1 installs from source, unpacked into addons\\gpu. activate() puts that folder first on the import path
when the app starts, before anything imports onnxruntime, so the GPU build is the one loaded.
"""
import logging
import platform
import shutil
import sys
import threading
from pathlib import Path

from . import addon, store

CUDA = "CUDAExecutionProvider"
_lock = threading.Lock()
_ready = None

# onnxruntime-gpu[cuda,cudnn]==1.31.0 (CUDA 13, cuDNN 9) for Python 3.12 on 64-bit Windows, as setup.ps1 installs
# it: each wheel pinned with its size and SHA-256 from PyPI, by its folder on PyPI's file server
DOWNLOAD = addon.Download(store.ADDONS_DIR / "gpu", {
    "onnxruntime_gpu-1.31.0-cp312-cp312-win_amd64.whl": (
        "PyPI", "7e/ff/035730b399eea860df5952a76cc489cb34a663e5341eb6f53d892a5f7f2c",
        "onnxruntime_gpu-1.31.0-cp312-cp312-win_amd64.whl", 168098204,
        "e6bd756a3022a1a5a0217f35814e40e6e04fd1e497a5be78f991f6bfefad2fba"),
    "nvidia_cuda_runtime-13.4.92-py3-none-win_amd64.whl": (
        "PyPI", "86/00/d5436004268f049214193659ebc36550b5ef3925c3d13b4cc980e13be6f5",
        "nvidia_cuda_runtime-13.4.92-py3-none-win_amd64.whl", 2778543,
        "08dca5e4aba480c2fd5b55075c0fa71b84ef9dcf0521f2d58baa14a803a7311c"),
    "nvidia_cuda_nvrtc-13.4.92-py3-none-win_amd64.whl": (
        "PyPI", "64/5b/aa91896f64444eff1dd7f189f4db05046c7f0ba08c0f531283c1c1aac581",
        "nvidia_cuda_nvrtc-13.4.92-py3-none-win_amd64.whl", 46972283,
        "6af7ac5372920f6a7a560d0699348560fe44cb52c1d722d6afd3119f8af948c4"),
    "nvidia_cublas-13.8.0.4-py3-none-win_amd64.whl": (
        "PyPI", "a3/df/f1246959833e2c437db8be3e5b477f66b87f8817821ed40de6c7561c9a36",
        "nvidia_cublas-13.8.0.4-py3-none-win_amd64.whl", 423266897,
        "8c5494423bb8a46822cb6b0cb95d7fa4be2d7b96a31155dff083839ec8297910"),
    "nvidia_cufft-12.4.0.43-py3-none-win_amd64.whl": (
        "PyPI", "7b/cc/be7fe31058127336a66c88414a2ecc6beecf680baf85994a43eaf292b202",
        "nvidia_cufft-12.4.0.43-py3-none-win_amd64.whl", 160953139,
        "4ff7075f2d0b5f69291f70938d37a86ec632cbe5747184c74ba1f50f17accacc"),
    "nvidia_curand-10.4.4.72-py3-none-win_amd64.whl": (
        "PyPI", "e2/d4/f59ab342b82ff05ff27d674acd470ba625d788bf71338f7da6c89669d6b9",
        "nvidia_curand-10.4.4.72-py3-none-win_amd64.whl", 56731059,
        "e0bce83e083ef25976ee74f59e8f067c15149a74538f1be6c2462e286e7c9c68"),
    "nvidia_nvjitlink-13.4.92-py3-none-win_amd64.whl": (
        "PyPI", "72/7c/c44bd7277afafed9de262a23baec4dc8dcf7ecbb1b9ecb228821442d1e9b",
        "nvidia_nvjitlink-13.4.92-py3-none-win_amd64.whl", 39053950,
        "b286f3a4f227a9363efdec263c7b91788cef1478d2b8a5fa8bab7f3e82ff82fd"),
    "nvidia_cudnn_cu13-9.27.0.42-py3-none-win_amd64.whl": (
        "PyPI", "87/6a/e55ff0ac26a5c6e2b21f41c9d04ad096b4ed6da593fba7e25845c61b0532",
        "nvidia_cudnn_cu13-9.27.0.42-py3-none-win_amd64.whl", 436469905,
        "7d96f634adafd55c72231eb0500ca77ab109ec8ebff7b33000b76e081bc4558e"),
}, url="https://files.pythonhosted.org/packages/{rev}/{path}", unzip=True)
UNPACKED = 1_900_000_000  # on disk once unpacked
DRIVER = 580              # the oldest NVIDIA driver CUDA 13 runs on
# the wheels are for the app's own Python (3.12, 64-bit Windows); a source copy on another Python uses setup.ps1
SUPPORTED = sys.platform == "win32" and sys.version_info[:2] == (3, 12) and platform.machine().lower() == "amd64"


def activate() -> None:
    """At startup, before anything imports onnxruntime (recap/__main__.py): put the downloaded GPU build first on
    the import path. A folder left without its installed mark (removed while its libraries were loaded, or a download
    cut off by closing the app) is deleted instead, now that nothing has it open."""
    folder = DOWNLOAD.folder
    if "onnxruntime" in sys.modules or not folder.exists():
        return
    if DOWNLOAD.installed():
        sys.path.insert(0, str(folder))
    else:
        shutil.rmtree(folder, ignore_errors=True)


def loaded() -> bool:
    """Whether this run uses the downloaded GPU build (it was there when the app started)."""
    ort = sys.modules.get("onnxruntime")
    try:
        return bool(ort) and Path(ort.__file__).resolve().is_relative_to(DOWNLOAD.folder.resolve())
    except (TypeError, OSError):
        return False


def available() -> bool:
    """True when ONNX Runtime can run models on an NVIDIA GPU."""
    global _ready
    with _lock:
        if _ready is None:
            try:
                import onnxruntime as ort
                if hasattr(ort, "preload_dlls"):
                    ort.preload_dlls()  # CUDA/cuDNN from the nvidia-* packages
                _ready = CUDA in ort.get_available_providers()
            except Exception as e:  # missing or broken CUDA libraries
                logging.warning("GPU not usable, running on the CPU: %s", e)
                _ready = False
        return _ready


_card = None


def _query_card() -> tuple[str, str]:
    """The first NVIDIA card's name and driver version, ("", "") if none."""
    global _card
    if _card is None:
        import subprocess
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=10,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            first = out.stdout.strip().splitlines()[0] if out.returncode == 0 and out.stdout.strip() else ""
            name, _, driver = first.rpartition(",") if "," in first else (first, "", "")
            _card = (name.strip(), driver.strip())
        except Exception:
            _card = ("", "")
    return _card


def nvidia_card() -> str:
    """The NVIDIA card in this PC ("" if none), whether or not ONNX Runtime can use it: the installed app ships
    the CPU-only build, so a card can be here for the video encoder and still not for text reading and voice."""
    return _query_card()[0]


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


# ---- GPU support: the add-on --------------------------------------------------------

def addon_status() -> dict:
    """GPU support for Settings → Performance and first-run setup. offered: there's an NVIDIA card and this copy
    has only the CPU build (or GPU support is here already). restart: downloaded or removed since the app started,
    which takes effect when it's next started."""
    st = DOWNLOAD.status()
    on = loaded()
    card, driver = _query_card()
    try:
        old = int(driver.split(".")[0]) < DRIVER
    except ValueError:
        old = False
    return {**st, "offered": bool(card) and SUPPORTED and (st["installed"] or on or not available()),
            "active": on, "restart": st["installed"] != on, "card": card, "driver": driver, "driver_old": old,
            "driver_needed": DRIVER, "unpacked_gb": round(UNPACKED / 1e9, 1), "folder": str(DOWNLOAD.folder)}


def install() -> dict:
    if not nvidia_card():
        raise ValueError("No NVIDIA graphics card was found")
    if not SUPPORTED:
        raise ValueError("GPU support is made for the app's own Python (3.12, 64-bit Windows); from source, "
                         "setup.ps1 installs it")
    if loaded() and not DOWNLOAD.installed():
        raise ValueError("Restart Recap Studio first: the GPU support you removed is still in use until then")
    if DOWNLOAD.state["state"] != "installing" and not DOWNLOAD.installed():  # (otherwise start says why not)
        DOWNLOAD.folder.parent.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(DOWNLOAD.folder.parent).free
        need = DOWNLOAD.total + UNPACKED
        if free < need:
            raise ValueError(f"GPU support needs about {need / 1e9:.1f} GB free while it unpacks "
                             f"({UNPACKED / 1e9:.1f} GB once done); {DOWNLOAD.folder.anchor} has {free / 1e9:.1f} GB")
    DOWNLOAD.start()
    return addon_status()


def remove() -> dict:
    """Remove GPU support. While this run has its libraries loaded they can't be deleted: then only its installed
    mark goes, so the next start runs on the CPU and deletes the rest (activate)."""
    if loaded():
        DOWNLOAD.marker.unlink(missing_ok=True)
        DOWNLOAD.state.update(state="idle", progress=0.0, message="Removed", error=None)
    else:
        DOWNLOAD.remove()
    return addon_status()
