"""Black boxes over nudity, for a project set to YouTube-safe (the Video tab → YouTube).

Each piece keeps its boxes in "cover": [{"box": [left, top, right, bottom], "by": ...}], the sides as
fractions of the piece's picture, so they hold at any size. "by" says where a box came from:
- "auto": the box finder, an optional add-on: anime_censor_detection v1.0 (YOLOv8s, MIT, by deepghs),
  trained on drawn art to find exposed nipples, penises and vulvas; a 44.6 MB download into
  data\\addons\\boxes, not part of the app. Find boxes now (and rendering, when it's installed) looks at
  each piece once (about 0.05 s on an NVIDIA GPU, 0.3 s on the CPU). A webtoon chapter is searched along
  its whole strip, and each piece gets its share of what's found there: looking at the pieces one by one
  missed what a cut between two pieces split, and thin pieces gave false finds.
- "claude": marked by the script writer while it narrated a YouTube-safe project. Rougher, so where the
  finder boxed the same spot, the finder's box is used.
- "you": drawn in the app (click a piece, drag over what to cover).

The video shows copies of the pictures with the boxes painted on, kept in the project's covered folder,
named after the picture and its boxes and reused while neither changes, so the scrolling strip, scenes
shown one at a time and the export for editors all get them.
"""
import hashlib
import json
import threading
from pathlib import Path

import cv2
import numpy as np

from . import addon, gpu, store
from .panels import imread
from .textless import _save

DOWNLOAD = addon.Download(store.DATA_ROOT / "addons" / "boxes", {
    "model.onnx": ("deepghs/anime_censor_detection", "0cf62fd6b28213b40ae0c0055f92e7ae6a96bdc2",
                   "censor_detect_v1.0_s/model.onnx", 44586353,
                   "2c2524824d7d320c5619a0a73702a2e2186f619067c823d211e94f7cfe489cba"),
})
SIZE = 640          # the model's input
CLASSES = 3         # nipple_f, penis, pussy
# The model card's best F1 is at 0.238. Lower misses less: on one test webtoon a nipple scored 0.16 to 0.35 as
# the window moved a few pixels, and a genital close-up 0.15 to 0.19, as did a few chins and mouths in kisses.
# A stray small box costs less on YouTube than a missed spot, so 0.15, with four looks at each spot.
THRESHOLD = 0.15
OVERLAP = 0.45      # boxes of one spot overlapping more than this are one find
# each side moved out by this share of the box's size, by class: a censor box, not a tight crop. Genitals get
# more: in a close-up the model boxes only part of them
GROW = (0.3, 0.6, 0.6)
MIN_SIDE = 0.04     # ... and at least this share of the picture's shorter side
STRIP_STEP = 0.5    # a webtoon strip's windows overlap by half: what one window's edge cuts, the next sees whole
VERSION = 1         # bump when how boxes are found changes, so pieces are looked at again
_session = None
_lock = threading.Lock()


def installed() -> bool:
    return DOWNLOAD.installed()


def _model():
    """The model, on the NVIDIA GPU when there is one."""
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort
            use_gpu = store.global_settings().get("use_gpu", True)
            try:
                _session = ort.InferenceSession(str(DOWNLOAD.path("model.onnx")), providers=gpu.providers(use_gpu))
            except Exception:  # the GPU session didn't start: the CPU
                _session = ort.InferenceSession(str(DOWNLOAD.path("model.onnx")), providers=["CPUExecutionProvider"])
        return _session


def forget() -> None:
    """Close the model (before its file is removed)."""
    global _session
    with _lock:
        _session = None


# ---- finding ------------------------------------------------------------------

def _windows(h: int, w: int) -> list[tuple[int, int, int, int]]:
    """Square windows (x, y, w, h) over a tall or wide picture, overlapping by a fifth, so small parts of a
    long one aren't shrunk away to nothing. A picture the model takes whole without much shrinking is one."""
    side = min(h, w)
    if max(h, w) <= max(side, SIZE) * 1.3:
        return [(0, 0, w, h)]
    step = max(1, int(side * 0.8))
    if h > w:
        return [(0, y, w, side) for y in sorted(set(list(range(0, h - side, step)) + [h - side]))]
    return [(x, 0, side, h) for x in sorted(set(list(range(0, w - side, step)) + [w - side]))]


def _look(session, img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """One window, looked at as it is and mirrored (the model misses some spots one way round that it finds
    the other): boxes (x0, y0, x1, y1 in its pixels, then the class) and their scores. Never enlarged: blown-up
    scraps of a picture are where the model sees things that aren't there."""
    h, w = img.shape[:2]
    r = min(1.0, SIZE / max(h, w))
    small = cv2.resize(img, (max(1, round(w * r)), max(1, round(h * r))), interpolation=cv2.INTER_AREA)
    canvas = np.full((2, SIZE, SIZE, 3), 114, np.uint8)
    canvas[0, :small.shape[0], :small.shape[1]] = small
    canvas[1, :small.shape[0], :small.shape[1]] = small[:, ::-1]
    x = canvas[..., ::-1].astype(np.float32).transpose(0, 3, 1, 2) / 255.0  # RGB, channels first
    boxes, scores = [], []
    for k in range(2):
        out = session.run(None, {session.get_inputs()[0].name: x[k:k + 1]})[0][0]
        if out.shape[0] == 4 + CLASSES:  # (4 + classes, candidates), as YOLOv8 exports it
            out = out.T
        sc = out[:, 4:4 + CLASSES].max(axis=1)
        keep = sc >= THRESHOLD
        cx, cy, bw, bh = (out[keep, j] / r for j in range(4))
        if k:
            cx = w - cx
        cls = out[keep, 4:4 + CLASSES].argmax(axis=1).astype(np.float32)
        boxes.append(np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2, cls], axis=1))
        scores.append(sc[keep])
    return np.concatenate(boxes), np.concatenate(scores)


def find(img: np.ndarray) -> list[list[float]]:
    """What to cover in a picture: boxes [left, top, right, bottom] as fractions of it, grown into censor
    boxes and joined where they touch."""
    session = _model()
    h, w = img.shape[:2]
    boxes, scores = [], []
    for x, y, ww, hh in _windows(h, w):
        b, s = _look(session, img[y:y + hh, x:x + ww])
        boxes.append(b + [x, y, x, y, 0])
        scores.append(s)
    return [[round(a / w, 4), round(b / h, 4), round(c / w, 4), round(d / h, 4)]
            for a, b, c, d in _censor(boxes, scores, w, h, MIN_SIDE * min(h, w))]


def _censor(boxes: list, scores: list, w: float, h: float, least: float) -> list[list[float]]:
    """Finds from overlapping windows (in one picture's pixels) as censor boxes: one per spot, grown by a
    margin (at least least pixels a side), inside the w x h picture, joined where they touch."""
    boxes, scores = np.concatenate(boxes), np.concatenate(scores)
    if not len(boxes):
        return []
    keep = cv2.dnn.NMSBoxes([[float(a), float(b), float(c - a), float(d - b)] for a, b, c, d, _ in boxes],
                            scores.tolist(), THRESHOLD, OVERLAP)
    grown = []
    for a, b, c, d, k in boxes[np.array(keep, dtype=int).reshape(-1)]:
        gx = max(GROW[int(k)] * (c - a), (least - (c - a)) / 2, 0)
        gy = max(GROW[int(k)] * (d - b), (least - (d - b)) / 2, 0)
        grown.append([max(0.0, a - gx), max(0.0, b - gy), min(w, c + gx), min(h, d + gy), int(k)])
    return [b[:4] for b in _join(grown + _bridges(grown))]


def find_strip(project: dict, pages: list[dict], pieces: list[dict], strip_group: int,
               progress=None) -> tuple[dict, dict]:
    """What to cover in a webtoon chapter's pieces, searched for along the strip they were cut from, in
    square windows the strip's width: ({piece id: boxes as fractions of that piece}, {piece id: where it sits
    in the strip, as locate gives it}). Only the stretches around pieces are searched.
    progress(done, total) after each window."""
    strip = _Strip(project, pages)
    at = locate(project, pages, pieces, strip_group, strip)
    session = _model()
    side = strip.base
    step = max(1, int(side * STRIP_STEP))
    tops = sorted(set(list(range(0, max(1, strip.height - side), step)) + [max(0, strip.height - side)]))
    spans = [(y, y + h) for _, y, _, h in at.values()]
    tops = [t for t in tops if any(a < t + side and b > t for a, b in spans)]
    boxes, scores = [], []
    for i, t in enumerate(tops, 1):
        b, s = _look(session, strip.rows(t, min(strip.height, t + side)))
        boxes.append(b + [0, t, 0, t, 0])
        scores.append(s)
        if progress:
            progress(i, len(tops))
    found = _censor(boxes, scores, side, strip.height, MIN_SIDE * side) if boxes else []
    out = {}
    for x in pieces:
        if x["id"] not in at:
            continue
        px, py, pw, ph = at[x["id"]]
        mine = []
        for l, t, r, b in found:  # its share of each box that falls on it
            l, t, r, b = max(l, px), max(t, py), min(r, px + pw), min(b, py + ph)
            if r - l >= 1 and b - t >= 1:
                mine.append([round((l - px) / pw, 4), round((t - py) / ph, 4), round((r - px) / pw, 4),
                             round((b - py) / ph, 4)])
        out[x["id"]] = mine
    return out, at


def _touch(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _bridges(boxes: list[list[float]]) -> list[list[float]]:
    """Boxes over the gap between two genital boxes [x0, y0, x1, y1, class] close together: in a close-up the
    model often finds parts of one spot, and the gap is the rest of it. Only pairs no further apart than the
    smaller is across, and together at most twice the bigger's size (so finds far apart stay apart)."""
    out = []
    g = [b for b in boxes if b[4] > 0]
    for i in range(len(g)):
        for j in range(i + 1, len(g)):
            a, b = g[i], g[j]
            u = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]), 1]
            sides = [max(a[2] - a[0], a[3] - a[1]), max(b[2] - b[0], b[3] - b[1])]
            gap = max(a[0] - b[2], b[0] - a[2], a[1] - b[3], b[1] - a[3])
            if 0 < gap <= min(sides) and max(u[2] - u[0], u[3] - u[1]) <= 2 * max(sides):
                out.append(u)
    return out


def _join(boxes: list[list[float]]) -> list[list[float]]:
    """Boxes [x0, y0, x1, y1, class] that overlap merged into one around both, until none overlap."""
    boxes = [list(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if _touch(boxes[i], boxes[j]):
                    a, b = boxes[i], boxes.pop(j)
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]), max(a[4], b[4])]
                    merged = True
                    break
            if merged:
                break
    return boxes


# ---- a piece's boxes ----------------------------------------------------------

def clean(box) -> list[float] | None:
    """[left, top, right, bottom] as fractions, put in order and inside the picture; None if it isn't one."""
    try:
        l, t, r, b = (min(1.0, max(0.0, float(v))) for v in box)
    except (TypeError, ValueError):
        return None
    l, r = sorted((l, r))
    t, b = sorted((t, b))
    return [round(l, 4), round(t, 4), round(r, 4), round(b, 4)] if r - l >= 0.005 and b - t >= 0.005 else None


def boxes(panel: dict) -> list[list[float]]:
    """The boxes the video draws on a piece: yours and the finder's, and Claude's where the finder found
    nothing overlapping them (the finder's box is the more exact one for that spot)."""
    cover = panel.get("cover") or []
    auto = [c["box"] for c in cover if c.get("by") == "auto"]
    return [c["box"] for c in cover if not (c.get("by") == "claude" and any(_touch(c["box"], a) for a in auto))]


def set_boxes(panel: dict, by: str, found: list[list[float]]) -> None:
    """Replace the piece's boxes from one source (the others stay)."""
    keep = [c for c in panel.get("cover") or [] if c.get("by") != by]
    panel["cover"] = keep + [{"box": b, "by": by} for b in found]
    if not panel["cover"]:
        panel.pop("cover")
    if by == "auto":
        panel["cover_checked"] = VERSION


# ---- where a webtoon piece sits in its strip ------------------------------------

class _Strip:
    """A webtoon chapter's pages as one strip, every page at the first page's width (the strip the video
    scrolls), read as needed and top to bottom: pages well above the rows asked for last are let go."""

    def __init__(self, project: dict, pages: list[dict]):
        self.project, self.pages = project, pages
        self.base = pages[0]["w"]
        self.heights = [max(1, round(x["h"] * self.base / x["w"])) for x in pages]
        self.offsets = np.cumsum([0] + self.heights)
        self.height = int(self.offsets[-1])
        self.cache: dict[int, np.ndarray] = {}

    def page(self, k: int) -> np.ndarray:
        if k not in self.cache:
            img = imread(store.page_path(self.project, self.pages[k]))
            self.cache[k] = (np.zeros((self.heights[k], self.base, 3), np.uint8) if img is None else
                             cv2.resize(img, (self.base, self.heights[k]), interpolation=cv2.INTER_AREA))
        return self.cache[k]

    def rows(self, a: int, b: int) -> np.ndarray:
        o = self.offsets
        for k in [k for k in self.cache if o[k + 1] < a - 2 * self.base]:
            del self.cache[k]
        return np.vstack([self.page(k)[max(a, o[k]) - o[k]:min(b, o[k + 1]) - o[k]]
                          for k in range(len(self.pages)) if max(a, o[k]) < min(b, o[k + 1])])


def locate(project: dict, pages: list[dict], pieces: list[dict], strip_group: int,
           strip: "_Strip | None" = None) -> dict[str, list[float]]:
    """Where each of pieces (a webtoon chapter's, cut from its strip) sits in the strip the video scrolls:
    {id: [x, y, w, h]}, the strip having every page at the first page's width. Detection trims a piece's flat
    margins without keeping where, so the piece is found again in its rows (y0, y1) by matching the picture."""
    strip = strip or _Strip(project, pages)
    base, offsets, rows = strip.base, strip.offsets, strip.rows
    d = store.project_dir(project["id"])
    out = {}
    for x in sorted(pieces, key=lambda x: x["y0"]):  # top to bottom, as the strip is read
        crop = imread(d / "panels" / x["file"])
        if crop is None:
            continue
        s = base / pages[min(len(pages) - 1, x.get("page", 0) // strip_group * strip_group)]["w"]
        if abs(s - 1) > 1e-3:
            crop = cv2.resize(crop, (max(1, round(crop.shape[1] * s)), max(1, round(crop.shape[0] * s))),
                              interpolation=cv2.INTER_AREA)
        ch, cw = crop.shape[:2]
        a = max(0, int(x["y0"] - 12 * s))
        b = min(int(offsets[-1]), max(a + ch, int(np.ceil(x["y1"] + 12 * s))))
        band = rows(a, b) if b > a else None
        if band is None or band.shape[0] < ch or band.shape[1] < cw:
            out[x["id"]] = [round((base - cw) / 2, 1), float(x["y0"]), float(cw), float(ch)]
            continue
        g_band, g_crop = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY), cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        # roughly at a quarter of the size, then exactly near there
        f = 0.25 if min(ch, cw) >= 64 else 1.0
        small_band = cv2.resize(g_band, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        small_crop = cv2.resize(g_crop, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        if small_crop.shape[0] > small_band.shape[0] or small_crop.shape[1] > small_band.shape[1]:
            small_band, small_crop, f = g_band, g_crop, 1.0
        _, _, (mx, my), _ = cv2.minMaxLoc(cv2.matchTemplate(small_band, small_crop, cv2.TM_SQDIFF))
        mx, my = round(mx / f), round(my / f)
        x0, y0 = max(0, mx - 6), max(0, my - 6)
        x1, y1 = min(g_band.shape[1], mx + cw + 6), min(g_band.shape[0], my + ch + 6)
        _, _, (fx, fy), _ = cv2.minMaxLoc(cv2.matchTemplate(g_band[y0:y1, x0:x1], g_crop, cv2.TM_SQDIFF))
        out[x["id"]] = [float(x0 + fx), float(a + y0 + fy), float(cw), float(ch)]
    return out


def in_strip(panel: dict) -> list[list[float]]:
    """A located webtoon piece's boxes in its strip's rows (see locate)."""
    x, y, w, h = panel["at"]
    return [[x + l * w, y + t * h, x + r * w, y + b * h] for l, t, r, b in boxes(panel)]


# ---- the pictures with their boxes ------------------------------------------------

def _copy_name(src: Path, rects: list) -> str:
    try:
        st = src.stat()
        stat = [str(src), st.st_size, st.st_mtime_ns]
    except OSError:
        stat = [str(src), None, None]
    digest = hashlib.sha1(json.dumps([VERSION, stat, rects]).encode()).hexdigest()[:16]
    ext = src.suffix.lower() if src.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") else ".png"
    return f"{src.stem[:40]}-{digest}{ext}"


def attach(items: list, project_dir: Path, compute: bool = False) -> None:
    """Point each item's pictures that have boxes at copies with the boxes painted on: copies made before,
    and with compute the rest too. A scene's "covers" holds each picture's boxes (fractions of it), a
    strip's its boxes in the strip's rows (see in_strip)."""
    folder = project_dir / "covered"
    for it in items:
        covers = it.get("covers")
        if not covers:
            continue
        if it.get("type") == "scene":
            todo = [(i, Path(p), [[l, t, r, b] for l, t, r, b in bx]) for i, (p, bx) in enumerate(zip(it["images"], covers)) if bx]
            fractions = True
        else:  # each page gets the boxes that fall on it, in its own pixels
            base = it["pages"][0][1]
            todo, top = [], 0
            for i, (p, pw, ph) in enumerate(it["pages"]):
                h = max(1, round(ph * base / pw))
                k = pw / base
                rects = [[round(l * k), round((t - top) * k), round(r * k), round((b - top) * k)]
                         for l, t, r, b in covers if t < top + h and b > top]
                if rects:
                    todo.append((i, Path(p), rects))
                top += h
            fractions = False
        for i, src, rects in todo:
            dst = folder / _copy_name(src, rects)
            if not dst.exists() and compute:
                img = imread(src)
                if img is None:
                    continue
                ih, iw = img.shape[:2]
                for l, t, r, b in rects:
                    if fractions:
                        l, t, r, b = l * iw, t * ih, r * iw, b * ih
                    cv2.rectangle(img, (max(0, int(l)), max(0, int(t))), (min(iw, int(np.ceil(r))) - 1,
                                  min(ih, int(np.ceil(b))) - 1), (0, 0, 0), -1)
                folder.mkdir(parents=True, exist_ok=True)
                _save(dst, img)
            if dst.exists():
                if it.get("type") == "scene":
                    it["images"][i] = dst
                else:
                    _, pw, ph = it["pages"][i]
                    it["pages"][i] = (dst, pw, ph)


def prune(project_dir: Path, items: list) -> None:
    """Delete the covered copies that none of items uses any more. Call it with every chapter's items."""
    folder = project_dir / "covered"
    used = {Path(x).name for it in items for x in (it.get("images") or [q for q, _, _ in it.get("pages", [])])}
    for f in folder.glob("*") if folder.exists() else []:
        if f.name not in used:
            f.unlink(missing_ok=True)
