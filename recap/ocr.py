"""Text reading with RapidOCR (open-source PaddleOCR models on ONNX Runtime).

RapidOCR's built-in recognition model reads Chinese and English. On English comic lettering it ran
words together and mixed up letters (SEYOLNG, APAIROFSLIPPERS); its English model, an optional 9 MB
download, cut that from 46 run-together words and 30 known misreads to 12 and 2 on a test chapter.
"""
import re
import threading

import cv2
import numpy as np

from . import addon, gpu, store

# RapidOCR's English recognition model (PP-OCRv3, Apache 2.0), from its author's repository;
# downloaded on request into data\addons\ocr_en, never shipped
ENGLISH = addon.Download(store.ADDONS_DIR / "ocr_en", {
    "en_rec.onnx": ("SWHL/RapidOCR", "1cfba2e90fc938db55889873735088de210cc173",
                    "PP-OCRv3/en_PP-OCRv3_rec_infer.onnx", 8_967_018,
                    "ef7abd8bd3629ae57ea2c28b425c1bd258a871b93fd2fe7c433946ade9b5d9ea"),
})

# a thin piece (a line of lettering: a title, "To be continued") is read with a margin around it: words
# touching its edges came out as "0 e e 0". On 90 such pieces the margin read 3 more words of dialogue
# and every card right; on taller pieces it was a wash, so they're read as they are
THIN, PAD = 0.3, 24    # height under this share of the width; margin in pixels

_engine = None       # the most recently built engine (for status)
device = "CPU"       # what it actually runs on
model = "built-in"   # which recognition model the most recent engine uses
_guard = threading.Lock()
_local = threading.local()  # each worker thread reads with its own engine


def use_english(settings: dict) -> bool:
    """Read with the English model: when it is downloaded and not switched off."""
    return bool(settings.get("ocr_english", True)) and ENGLISH.installed()


def _get(use_gpu: bool | None = None, english: bool | None = None):
    """This thread's OCR engine; rebuilt when the GPU or model setting changes (None: keep what's loaded)."""
    global _engine, device, model
    engine = getattr(_local, "engine", None)
    built_gpu, built_en = getattr(_local, "gpu", None), getattr(_local, "english", False)
    if english is None:
        english = built_en if engine is not None else False
    english = bool(english) and ENGLISH.installed()
    if engine is None or (use_gpu is not None and bool(use_gpu) != built_gpu) or english != built_en:
        from rapidocr_onnxruntime import RapidOCR
        extra = {"rec_model_path": str(ENGLISH.path("en_rec.onnx"))} if english else {}
        with _guard:  # load models one engine at a time
            want = bool(use_gpu) and gpu.available()
            try:
                engine = RapidOCR(det_use_cuda=want, cls_use_cuda=want, rec_use_cuda=want, **extra)
            except Exception:  # GPU failed to start: fall back to the CPU
                want, engine = False, RapidOCR(**extra)
            _local.engine, _local.gpu, _local.english = engine, bool(use_gpu), english
            _engine, device = engine, gpu.device_of(engine.text_det.infer.session) if want else "CPU"
            model = "English" if english else "built-in"
    return engine


def read_lines(img_bgr: np.ndarray, min_score: float = 0.6, use_gpu: bool = True,
               english: bool | None = None) -> list[tuple]:
    """Text line boxes (x0, y0, x1, y1, text) in an image. Very tall strips are read in
    overlapping chunks so their text isn't shrunk away."""
    h, w = img_bgr.shape[:2]
    chunk = max(w * 2, 1600)
    out, y = [], 0
    while y < h:
        part = img_bgr[y:min(h, y + chunk)]
        pad = PAD if part.shape[0] < THIN * w else 0
        if pad:
            part = cv2.copyMakeBorder(part, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        result, _ = _get(use_gpu, english)(part)
        for box, text, score in result or []:
            if float(score) < min_score or not text.strip():
                continue
            xs = [min(w, max(0, p[0] - pad)) for p in box]
            ys = [min(h, max(0, p[1] - pad + y)) for p in box]
            line = (min(xs), min(ys), max(xs), max(ys), text.strip())
            if not any(abs(line[1] - o[1]) < 6 and abs(line[0] - o[0]) < 6 for o in out):  # chunk overlap
                out.append(line)
        if y + chunk >= h:
            break
        y += chunk - 200
    return out


def _same_row(a, b) -> bool:
    """Two line boxes side by side: they overlap vertically by at least half the smaller one."""
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    return overlap > 0.5 * min(a[3] - a[1], b[3] - b[1])


def groups(lines: list[tuple]) -> list[list[tuple]]:
    """Text lines grouped into speech bubbles and captions: lines join one when they are stacked
    closely (a gap under 0.6 of a line, overlapping horizontally) or are pieces of one row."""
    n = len(lines)
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        for j in range(i + 1, n):
            a, b = lines[i], lines[j]
            lh = min(a[3] - a[1], b[3] - b[1])
            gap = max(a[1], b[1]) - min(a[3], b[3])                      # vertical; negative if overlapping
            across = min(a[2], b[2]) - max(a[0], b[0])                   # horizontal overlap
            stacked = gap < 0.6 * lh and across > 0.2 * min(a[2] - a[0], b[2] - b[0])
            split_row = _same_row(a, b) and max(a[0], b[0]) - min(a[2], b[2]) < 0.8 * lh
            if stacked or split_row:
                parent[root(i)] = root(j)
    found: dict[int, list] = {}
    for i in range(n):
        found.setdefault(root(i), []).append(lines[i])
    return list(found.values())


def bubbles(lines: list[tuple]) -> list[str]:
    """Group text lines into speech bubbles and captions (see groups), and read them in order.

    Bubbles are read top to bottom; two side by side, starting at about the same height, are read
    left to right. (Merging lines row by row across the whole panel interleaved the sentences of
    neighbouring bubbles.)
    """
    found = []
    for g in groups(lines):
        rows: list[list] = []
        for line in sorted(g, key=lambda l: (l[1], l[0])):
            row = next((r for r in rows if _same_row(r[0], line)), None)
            if row is None:
                rows.append([line])
            else:
                row.append(line)
        rows.sort(key=lambda r: min(l[1] for l in r))
        text = " ".join(" ".join(l[4] for l in sorted(r, key=lambda l: l[0])) for r in rows)
        found.append((min(l[1] for l in g), max(l[3] for l in g), min(l[0] for l in g), text))
    found.sort(key=lambda b: (b[0], b[2]))
    ordered: list = []
    for b in found:
        k = len(ordered)
        while k and ordered[k - 1][2] > b[2] and abs(ordered[k - 1][0] - b[0]) < 0.5 * min(
                ordered[k - 1][1] - ordered[k - 1][0], b[1] - b[0]):
            k -= 1  # starts at about the same height but further left: read it first
        ordered.insert(k, b)
    return [b[3] for b in ordered]


_DIGIT_LOOKALIKES = str.maketrans({"5": "S", "0": "O"})


def tidy(text: str) -> str:
    """Small fixes for comic lettering: text that is nearly all capitals is made all capitals (the
    reader slips into lower case: "THIs is", "iSN't"), and inside words 5 and 0 are read as S and O
    ("YE5", "M5.SHIN"). Numbers, and words like "1ST", are left alone."""
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) >= 0.6 * len(letters):  # ordinary sentence case is ~5-15%
        text = text.upper()

    def word(m):
        w = m.group(0)
        return w if _NUMBERISH.match(w) else w.translate(_DIGIT_LOOKALIKES)
    return re.sub(r"[A-Za-z0-9']*[A-Za-z][A-Za-z0-9']*", word, text)


_NUMBERISH = re.compile(r"^\d+(st|nd|rd|th|s|k|m|am|pm|kg|km|cm)$", re.I)  # 5TH, 1950S, 50K stay as they are


def read_text(img_bgr: np.ndarray, min_score: float = 0.6, use_gpu: bool = True, english: bool | None = None) -> str:
    """The text found in an image, one speech bubble or caption per line, in reading order."""
    return tidy("\n".join(bubbles(read_lines(img_bgr, min_score, use_gpu, english))))
