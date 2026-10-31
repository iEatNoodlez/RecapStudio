"""Where a scene's main subject is, so pan and zoom move toward it instead of the middle (Settings →
Video → Pan and zoom toward). Optional add-on: U2-Net-p, a small salient-object model (Apache 2.0,
by Xuebin Qin et al.), as converted to ONNX by the rembg project, downloaded on request (4.6 MB).

The model marks the parts of a picture that stand out; the subject is the middle and the extent of
those parts. Results are kept per scene in the project's focus.json, so each scene is looked at once.
"""
import hashlib
import json
import threading
from pathlib import Path

import cv2
import numpy as np

from . import addon, gpu, store

DOWNLOAD = addon.Download(store.DATA_ROOT / "addons" / "focus", {
    "u2netp.onnx": ("", "v0.0.0", "u2netp.onnx", 4574861,
                    "309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8"),
}, url="https://github.com/danielgatis/rembg/releases/download/{rev}/{path}")
SIZE = 320                     # the model's input
MEAN, STD = np.array([0.485, 0.456, 0.406]), np.array([0.229, 0.224, 0.225])
THRESHOLD = 0.5                # of the map scaled to 0-1: what counts as the subject
SMALLEST, LARGEST = 0.003, 0.85  # share of the picture: less is noise, more is no subject at all
VERSION = 1                    # bump when how the subject is found changes, so scenes are looked at again
_session = None
_lock = threading.Lock()


def installed() -> bool:
    return DOWNLOAD.installed()


def wanted(settings) -> bool:
    """Whether scenes shown one at a time aim their pan and zoom at the subject."""
    return settings.get("motion_focus", "center") == "subject" and installed()


def _model():
    """The model, on the NVIDIA GPU when there is one (about 180 ms a picture on 12 CPU threads)."""
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort
            use_gpu = store.global_settings().get("use_gpu", True)
            try:
                _session = ort.InferenceSession(str(DOWNLOAD.path("u2netp.onnx")), providers=gpu.providers(use_gpu))
            except Exception:  # the GPU session didn't start: the CPU
                _session = ort.InferenceSession(str(DOWNLOAD.path("u2netp.onnx")), providers=["CPUExecutionProvider"])
        return _session


def forget() -> None:
    """Close the model (before its file is removed)."""
    global _session
    with _lock:
        _session = None


def find(img_bgr: np.ndarray) -> list | None:
    """The subject of a picture as [center x, center y, width, height], fractions of the picture's size,
    or None when nothing stands out (or everything does)."""
    rgb = cv2.cvtColor(cv2.resize(img_bgr, (SIZE, SIZE), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
    x = rgb.astype(np.float64) / max(1, int(rgb.max()))
    x = ((x - MEAN) / STD).transpose(2, 0, 1)[None].astype(np.float32)
    session = _model()
    m = session.run(None, {session.get_inputs()[0].name: x})[0][0, 0]
    lo, hi = float(m.min()), float(m.max())
    if hi - lo < 1e-3:
        return None
    m = (m - lo) / (hi - lo)
    ys, xs = np.nonzero(m > THRESHOLD)
    if not SMALLEST <= len(xs) / m.size <= LARGEST:
        return None
    w = m[ys, xs]
    x0, x1 = np.percentile(xs, [5, 95]) / SIZE
    y0, y1 = np.percentile(ys, [5, 95]) / SIZE
    cx, cy = float((xs * w).sum() / w.sum() + 0.5) / SIZE, float((ys * w).sum() / w.sum() + 0.5) / SIZE
    return [round(cx, 3), round(cy, 3), round(float(max(1 / SIZE, x1 - x0)), 3), round(float(max(1 / SIZE, y1 - y0)), 3)]


def _key(images) -> str:
    stats = []
    for p in images:
        try:
            st = Path(p).stat()
            stats.append([str(p), st.st_size, st.st_mtime_ns])
        except OSError:
            stats.append([str(p), None, None])
    return hashlib.sha1(json.dumps([VERSION, stats]).encode()).hexdigest()[:20]


def attach(items: list, project_dir: Path, compute: bool = False, progress=None) -> None:
    """Give each scene item its subject ("focus", see find) from the project's focus.json; with compute,
    scenes not looked at yet are looked at now and remembered. progress(i, n) as it goes."""
    from .panels import imread
    from .scenes import stack
    path = project_dir / "focus.json"
    try:
        known = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    scenes_ = [it for it in items if it.get("type") == "scene"]
    added = False
    for i, it in enumerate(scenes_):
        k = _key(it["images"])
        if k not in known and compute:
            pics = [im for im in (imread(p) for p in it["images"]) if im is not None]
            known[k] = find(stack(pics)) if pics else None
            added = True
            if progress:
                progress(i + 1, len(scenes_))
        if known.get(k):
            it["focus"] = known[k]
    if added:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(known), encoding="utf-8")
        tmp.replace(path)
