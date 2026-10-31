"""The pictures without their text (Settings → Video → Remove the text from the pictures), so the video
shows the art while the narration tells the story.

Text is found by the same text reader as Read text (ocr.py: RapidOCR's line finder, Apache 2.0, no
other model) and grouped into speech bubbles and captions. Where a group sits on a plain background
(the band just around it is nearly all one colour: a bubble or a caption box), its letters are painted
over in that colour, or filled in from around them where the background shades. Text drawn over the
artwork (sound effects, signs) is left alone: painting it out would leave a smudge.

Cleaned copies are kept in the project's textless folder, named after the picture they come from, and
reused while that picture doesn't change.
"""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from . import ocr
from .panels import imread

VERSION = 1       # bump when the cleaning changes, so pictures are cleaned again
MIN_SCORE = 0.5   # text lines the reader is at least this sure of
NEAR = 40         # colour difference (0-255, any channel) that still counts as the background
PLAIN = 0.85      # share of the band around a bubble in its background colour, to count as plain
FLAT = 0.97       # ... and to be painted flat (otherwise the letters are filled in from around them)
INSIDE = 0.5      # share of the text's own box in that colour too (letters are thin): else the band
                  # was something else, e.g. the panel around a bubble the text sits at the edge of


def _erase(img: np.ndarray, lines: list[tuple]) -> tuple[np.ndarray, int]:
    """The picture with the text on plain backgrounds painted over, and how many bubbles that was."""
    out = img.copy()
    h, w = img.shape[:2]
    done = 0
    for g in ocr.groups(lines):
        lh = float(np.median([l[3] - l[1] for l in g]))
        pad = max(2, round(0.12 * lh))
        ring = max(3, round(0.3 * lh))
        x0, y0 = max(0, int(min(l[0] for l in g)) - pad), max(0, int(min(l[1] for l in g)) - pad)
        x1, y1 = min(w, int(np.ceil(max(l[2] for l in g))) + pad), min(h, int(np.ceil(max(l[3] for l in g))) + pad)
        X0, Y0, X1, Y1 = max(0, x0 - ring), max(0, y0 - ring), min(w, x1 + ring), min(h, y1 + ring)
        if x1 <= x0 or y1 <= y0:
            continue
        around = np.ones((Y1 - Y0, X1 - X0), bool)
        around[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = False
        band = img[Y0:Y1, X0:X1][around]
        if not len(band):
            continue
        bg = np.median(band, axis=0)
        plain = float((np.abs(band.astype(np.int16) - bg).max(axis=1) < NEAR).mean())
        if plain < PLAIN:
            continue  # text over the artwork: left as it is
        box = out[y0:y1, x0:x1]
        letters = (np.abs(box.astype(np.int16) - bg).max(axis=2) >= NEAR).astype(np.uint8)
        if 1 - letters.mean() < INSIDE:
            continue
        letters = cv2.dilate(letters, np.ones((3, 3), np.uint8), iterations=max(1, round(lh / 12)))
        if plain >= FLAT:
            box[letters > 0] = bg.astype(np.uint8)
        else:  # a shaded background: fill the letters in from what's around them
            region = out[Y0:Y1, X0:X1]
            mask = np.zeros(region.shape[:2], np.uint8)
            mask[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0] = letters
            out[Y0:Y1, X0:X1] = cv2.inpaint(region, mask, 3, cv2.INPAINT_TELEA)
        done += 1
    return out, done


def clean(img: np.ndarray, use_gpu: bool = True, english: bool | None = None) -> tuple[np.ndarray, int]:
    """img without the text on plain backgrounds, and how many bubbles and captions were cleared."""
    return _erase(img, ocr.read_lines(img, MIN_SCORE, use_gpu, english))


def _copy_name(src: Path) -> str:
    try:
        st = src.stat()
        stat = [str(src), st.st_size, st.st_mtime_ns]
    except OSError:
        stat = [str(src), None, None]
    digest = hashlib.sha1(json.dumps([VERSION, stat]).encode()).hexdigest()[:16]
    ext = src.suffix.lower() if src.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") else ".png"
    return f"{src.stem[:40]}-{digest}{ext}"


def _save(path: Path, img: np.ndarray):
    params = [cv2.IMWRITE_JPEG_QUALITY, 95] if path.suffix in (".jpg", ".jpeg") else (
        [cv2.IMWRITE_WEBP_QUALITY, 95] if path.suffix == ".webp" else [])
    ok, data = cv2.imencode(path.suffix, img, params)
    if not ok:
        raise RuntimeError(f"Couldn't save {path.name}")
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data.tobytes())
    tmp.replace(path)


def attach(items: list, project_dir: Path, compute: bool = False, settings: dict | None = None,
           workers: int = 1, progress=None) -> None:
    """Point each item's pictures (a scene's images, a strip's pages) at their cleaned copies: the ones
    made before, and with compute the rest too (workers at a time; progress(done, total) as it goes)."""
    folder = project_dir / "textless"
    s = settings or {}
    english = ocr.use_english(s)
    todo = {}
    for it in items:
        for src in ([Path(p) for p in it["images"]] if it.get("type") == "scene" else
                    [Path(p) for p, _, _ in it.get("pages", [])]):
            todo[src] = folder / _copy_name(src)
    missing = [src for src, dst in todo.items() if not dst.exists()]
    if missing and compute:
        folder.mkdir(parents=True, exist_ok=True)

        def one(src):
            img = imread(src)
            if img is not None:
                _save(todo[src], clean(img, s.get("use_gpu", True), english)[0])
        pool = ThreadPoolExecutor(max(1, workers))
        try:
            for i, f in enumerate([pool.submit(one, src) for src in missing], 1):
                f.result()
                if progress:
                    progress(i, len(missing))  # may stop here (Cancel): what isn't started yet is dropped
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
    for it in items:
        if it.get("type") == "scene":
            it["images"] = [todo[Path(p)] if todo[Path(p)].exists() else p for p in it["images"]]
        else:
            it["pages"] = [(todo[Path(p)] if todo[Path(p)].exists() else p, pw, ph) for p, pw, ph in it["pages"]]


def prune(project_dir: Path, items: list) -> None:
    """Delete the cleaned copies that none of items uses any more (pictures changed, panels detected
    again). Call it with every chapter's items."""
    folder = project_dir / "textless"
    used = {Path(x).name for it in items for x in (it.get("images") or [q for q, _, _ in it.get("pages", [])])}
    for f in folder.glob("*") if folder.exists() else []:
        if f.name not in used:
            f.unlink(missing_ok=True)
