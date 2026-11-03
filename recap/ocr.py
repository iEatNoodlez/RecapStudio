"""Text reading with RapidOCR (open-source PaddleOCR models on ONNX Runtime).

RapidOCR's built-in recognition model reads Chinese and English. On English comic lettering it ran
words together and mixed up letters (SEYOLNG, APAIROFSLIPPERS); its English model, an optional 9 MB
download, cut that from 46 run-together words and 30 known misreads to 12 and 2 on a test chapter.

A comic in Korean, Chinese or Japanese (the project's language) is read with PaddleOCR's PP-OCRv5 models
instead: its text finder, which also finds vertical lines, and a reader for Chinese (simplified and
traditional), Japanese and English, or one for Korean. Each speech bubble is kept with its box (read_bubbles),
for translate.py to translate and typeset.py to put the English in its place.
"""
import logging
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

# The languages a comic can be in (Project settings → Language), and PaddleOCR's PP-OCRv5 models for the three
# that aren't English (Apache 2.0, from PaddlePaddle's own repositories): downloaded into data\addons\ocr_cjk the
# first time a project in one of them is read (about 35 MB), never shipped. Each reader's character list is in
# its inference.yml; a keys file is written from it when the reader is first loaded.
LANGUAGES = {"en": "English", "ko": "Korean", "zh": "Chinese", "ja": "Japanese"}
CJK = addon.Download(store.ADDONS_DIR / "ocr_cjk", {
    "det.onnx": ("PaddlePaddle/PP-OCRv5_mobile_det_onnx", "e6f4fa85f00e168c862bc462aebca69eef9b3d3d",
                 "inference.onnx", 4_826_518, "a431985659dc921974177a95adcfbb90fd9e51989a5e04d70d0b75f597b6e61d"),
    "rec.onnx": ("PaddlePaddle/PP-OCRv5_mobile_rec_onnx", "ed152b8b495f84de93cda5709d768548a9127622",
                 "inference.onnx", 16_534_782, "da72dc72ca4dc220df0dfde68c1dedc31c58d3e76a25871122e5056227d50092"),
    "rec.yml": ("PaddlePaddle/PP-OCRv5_mobile_rec_onnx", "ed152b8b495f84de93cda5709d768548a9127622",
                "inference.yml", 148_345, "5dfeb2777f6d0db8177d8128a8acfcf6e6276dc4ac73ea3bf0dc06d6a5e85d8e"),
    "ko_rec.onnx": ("PaddlePaddle/korean_PP-OCRv5_mobile_rec_onnx", "5c6f574b8e2230adf4287b33e736d71b9fabd28e",
                    "inference.onnx", 13_418_787, "92f0b7785e64fc9090106a241cf4c1eb97472824558272751b88a2a4476d3a08"),
    "ko_rec.yml": ("PaddlePaddle/korean_PP-OCRv5_mobile_rec_onnx", "5c6f574b8e2230adf4287b33e736d71b9fabd28e",
                   "inference.yml", 96_039, "f757fa1c40e99edcf27e9cce879b93eb2a51fa46f5ef39095689b8c37dd75998"),
})


# Languages plugins add (plugins.Plugin.language): code -> {"args": fn() -> the reader's RapidOCR arguments, or
# None to read with the built-in reader; "ready": fn(job) that makes sure its models are there; "fix": fn(text)
# -> a bubble's text put right, or None}
READERS: dict = {}


def language(settings: dict) -> str:
    """The project's language: "en", "ko", "zh" or "ja", or one a plugin added."""
    return settings.get("language") if settings.get("language") in LANGUAGES else "en"


def model_for(settings: dict) -> str:
    """Which reader a project's text is read with: "ko", "cjk" (Chinese and Japanese), "lang:<code>" (a plugin's
    language with a reader of its own), "en" (the English model, when downloaded and switched on) or "built-in"."""
    lang = language(settings)
    if lang in ("ko", "zh", "ja"):
        return "ko" if lang == "ko" else "cjk"
    if lang in READERS:
        return f"lang:{lang}" if READERS[lang].get("args") else "built-in"
    return "en" if use_english(settings) else "built-in"


def _keys(name: str):
    """The keys file (one character per line) for a downloaded reader, written from its inference.yml."""
    out = CJK.path(f"{name}_keys.txt")
    if not out.exists():
        import yaml
        info = yaml.safe_load(CJK.path(f"{name}.yml").read_text(encoding="utf-8"))
        chars = info["PostProcess"]["character_dict"]
        out.write_text("\n".join(str(c) for c in chars) + "\n", encoding="utf-8")
    return out


# RapidOCR's check for upside-down lines (use_cls) is off: lettering is hardly ever upside down, and the check turned
# real lines over and read them as garble ("NO" as "ON", "HOW" as "MOH", Korean as Latin scraps). On real chapters
# it changed or lost 3-6% of the English readers' lines and 16% of the Korean reader's, nearly all for the worse (it
# was right on a handful: a phone screen drawn upside down). Chinese and Japanese keep it until a real raw is tried
# (their vertical columns are read turned a quarter, and the check may matter there); a plugin's reader can ask for it
UPRIGHT = {"use_cls": False}


def _model_args(kind: str) -> dict:
    if kind == "en" and ENGLISH.installed():
        return {"rec_model_path": str(ENGLISH.path("en_rec.onnx")), **UPRIGHT}
    if kind.startswith("lang:"):  # a plugin's reader (every line kept, as for the CJK readers)
        return {"text_score": 0.0, **UPRIGHT, **READERS[kind[5:]]["args"]()}
    if kind in ("ko", "cjk"):
        if not CJK.installed():
            raise RuntimeError("The Korean, Chinese and Japanese text reader isn't downloaded yet")
        rec = "ko_rec" if kind == "ko" else "rec"
        # every line found is kept (read_bubbles decides): one read unsurely still marks where text is, for the
        # translator (who also sees the picture) and for the English to cover it
        return {"det_model_path": str(CJK.path("det.onnx")), "rec_model_path": str(CJK.path(f"{rec}.onnx")),
                "rec_keys_path": str(_keys(rec)), "text_score": 0.0, **(UPRIGHT if kind == "ko" else {})}
    return dict(UPRIGHT)

# a thin piece (a line of lettering: a title, "To be continued") is read with a margin around it: words
# touching its edges came out as "0 e e 0". On 90 such pieces the margin read 3 more words of dialogue
# and every card right; on taller pieces it was a wash, so they're read as they are
THIN, PAD = 0.3, 24    # height under this share of the width; margin in pixels
OVERLAP = 200          # a tall strip's chunks overlap this much (read_lines)
EDGE = 12              # a line this close to a chunk's edge is cut by it (a cut word's box ended 7 px short)

_engine = None       # the most recently built engine (for status)
device = "CPU"       # what it actually runs on
model = "built-in"   # which recognition model the most recent engine uses
_guard = threading.Lock()
_local = threading.local()  # each worker thread reads with its own engine


def use_english(settings: dict) -> bool:
    """Read with the English model: when it is downloaded and not switched off."""
    return bool(settings.get("ocr_english", True)) and ENGLISH.installed()


MODEL_NAMES = {"en": "English", "built-in": "built-in", "cjk": "Chinese and Japanese", "ko": "Korean"}


def _get(use_gpu: bool | None = None, kind: str | None = None):
    """This thread's OCR engine; rebuilt when the GPU or model setting changes (None: keep what's loaded).
    kind: "built-in", "en", "cjk" or "ko" (model_for)."""
    global _engine, device, model
    engine = getattr(_local, "engine", None)
    built_gpu, built_kind = getattr(_local, "gpu", None), getattr(_local, "kind", "built-in")
    if kind is None:
        kind = built_kind if engine is not None else "built-in"
    if kind == "en" and not ENGLISH.installed():
        kind = "built-in"
    if engine is None or (use_gpu is not None and bool(use_gpu) != built_gpu) or kind != built_kind:
        from rapidocr_onnxruntime import RapidOCR
        extra = _model_args(kind)
        with _guard:  # load models one engine at a time
            want = bool(use_gpu) and gpu.available()
            try:
                engine = RapidOCR(det_use_cuda=want, cls_use_cuda=want, rec_use_cuda=want, **extra)
            except Exception:  # GPU failed to start: fall back to the CPU
                want, engine = False, RapidOCR(**extra)
            _local.engine, _local.gpu, _local.kind = engine, bool(use_gpu), kind
            _engine, device = engine, gpu.device_of(engine.text_det.infer.session) if want else "CPU"
            model = MODEL_NAMES.get(kind) or LANGUAGES.get(kind[5:], kind)  # (a plugin's reader: its language)
    return engine


def read_lines(img_bgr: np.ndarray, min_score: float = 0.6, use_gpu: bool = True,
               english: bool | None = None, kind: str | None = None) -> list[tuple]:
    """Text line boxes (x0, y0, x1, y1, text) in an image, read with the English model (english) or the given
    kind of reader (model_for). Very tall strips are read in overlapping chunks so their text isn't shrunk away:
    a line a chunk's edge cuts through is taken from the chunk it's whole in (cut, its top half was read as
    other words, "DE CIEDTA FODMA" over "DE CIERTA FORMA"), and one read whole by both chunks once."""
    kind = kind or ("en" if english else "built-in")
    h, w = img_bgr.shape[:2]
    chunk = max(w * 2, 1600)
    out, y = [], 0
    while y < h:
        before = len(out)  # (the lines the chunks before read: one of this chunk's may be one of them)
        part = img_bgr[y:min(h, y + chunk)]
        tall = part.shape[0]
        above, below = y > 0, y + chunk < h  # (edges the chunk before or after reads across)
        pad = PAD if tall < THIN * w else 0
        if pad:
            part = cv2.copyMakeBorder(part, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        result, _ = _get(use_gpu, kind)(part)
        for box, text, score in result or []:
            if float(score) < min_score or not text.strip():
                continue
            top, bottom = min(p[1] for p in box) - pad, max(p[1] for p in box) - pad
            if (above and top <= EDGE and bottom < OVERLAP - EDGE  # (the chunk before has it whole)
                    or below and bottom >= tall - EDGE and top > tall - OVERLAP + EDGE):  # (the one after)
                continue
            xs = [min(w, max(0, p[0] - pad)) for p in box]
            ys = [min(h, max(0, p[1] - pad + y)) for p in box]
            line = (min(xs), min(ys), max(xs), max(ys), text.strip())
            same = next((i for i in range(before) if _overlap(line, out[i]) > 0.6), None)  # (read by both chunks)
            if same is None:
                out.append(line)
            elif _area(line) > _area(out[same]):  # (the whole of it, not the piece of it the other chunk had)
                out[same] = line
        if y + chunk >= h:
            break
        y += chunk - OVERLAP
    return out


def _area(a) -> float:
    return (a[2] - a[0]) * (a[3] - a[1])


def _overlap(a, b) -> float:
    """How much two line boxes overlap: the shared area's share of the smaller one."""
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    smaller = min(_area(a), _area(b))
    return w * h / smaller if w > 0 and h > 0 and smaller > 0 else 0.0


def _same_row(a, b) -> bool:
    """Two line boxes side by side: they overlap vertically by at least half the smaller one."""
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    return overlap > 0.5 * min(a[3] - a[1], b[3] - b[1])


def groups(lines: list[tuple], vertical_text: bool = False) -> list[list[tuple]]:
    """Text lines grouped into speech bubbles and captions: lines join one when they are stacked
    closely (a gap under 0.6 of a line, overlapping horizontally) or are pieces of one row.
    vertical_text (Chinese and Japanese): a line's size is its thinner side, so the columns of vertical text
    side by side join when they're closer than a character, not than a whole column."""
    n = len(lines)
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def size(line):
        return min(line[3] - line[1], line[2] - line[0]) if vertical_text else line[3] - line[1]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = lines[i], lines[j]
            lh = min(size(a), size(b))
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
    return [b["text"] for b in _ordered(lines)]


def _vertical(g) -> bool:
    """A bubble of vertical columns: most of its lines much taller than wide."""
    return 2 * sum((l[3] - l[1]) > 1.5 * (l[2] - l[0]) for l in g) > len(g)


def _group_text(g, joiner: str, vertical: bool) -> str:
    if vertical:  # columns right to left, each from the top (a column read in two pieces stays together)
        width = float(np.median([l[2] - l[0] for l in g])) or 1.0
        return joiner.join(l[4] for l in sorted(g, key=lambda l: (-round((l[0] + l[2]) / 2 / (0.7 * width)), l[1])))
    rows: list[list] = []
    for line in sorted(g, key=lambda l: (l[1], l[0])):
        row = next((r for r in rows if _same_row(r[0], line)), None)
        if row is None:
            rows.append([line])
        else:
            row.append(line)
    rows.sort(key=lambda r: min(l[1] for l in r))
    return joiner.join(joiner.join(l[4] for l in sorted(r, key=lambda l: l[0])) for r in rows)


def _ordered(lines: list[tuple], joiner: str = " ", rtl: bool = False, vertical_text: bool = False) -> list[dict]:
    """The bubbles [{"box", "text", "vertical"}] in reading order: top to bottom, and two side by side starting
    at about the same height left to right (rtl: right to left, as manga is read)."""
    found = []
    for g in groups(lines, vertical_text):
        vertical = vertical_text and _vertical(g)
        box = [min(l[0] for l in g), min(l[1] for l in g), max(l[2] for l in g), max(l[3] for l in g)]
        found.append({"box": box, "text": _group_text(g, joiner, vertical), "vertical": vertical,
                      "lines": [[int(round(v)) for v in l[:4]] for l in g]})

    def x(b):
        return -b["box"][2] if rtl else b["box"][0]
    found.sort(key=lambda b: (b["box"][1], x(b)))
    ordered: list = []
    for b in found:
        k = len(ordered)
        while k and x(ordered[k - 1]) > x(b) and abs(ordered[k - 1]["box"][1] - b["box"][1]) < 0.5 * min(
                ordered[k - 1]["box"][3] - ordered[k - 1]["box"][1], b["box"][3] - b["box"][1]):
            k -= 1  # starts at about the same height but further along the way it's read: read it first
        ordered.insert(k, b)
    for b in ordered:
        b["box"] = [int(round(v)) for v in b["box"]]
    return ordered


def read_bubbles(img_bgr: np.ndarray, kind: str, joiner: str, rtl: bool = False, min_score: float = 0.2,
                 use_gpu: bool = True) -> list[dict]:
    """A raw comic's speech bubbles, captions and other text, in reading order: [{"box": [x0, y0, x1, y1],
    "text", "vertical", "lines"}], read with kind's reader (model_for). joiner goes between a bubble's lines: ""
    for Chinese and Japanese, " " for Korean. Lines read unsurely are kept (min_score is low): Korean's reader
    read a clear "시작해요?" as "8워" (0.26), and left out, the line would show under the English."""
    return _ordered(read_lines(img_bgr, min_score, use_gpu, kind=kind), joiner, rtl, vertical_text=True)


def put_right(bubbles: list[dict], lang: str) -> list[dict]:
    """What a plugin's language puts right in the text read, the same mistake each time (READERS' "fix")."""
    fix = (READERS.get(lang) or {}).get("fix")
    for b in bubbles if fix else []:
        try:
            b["text"] = fix(b["text"])
        except Exception:
            logging.getLogger("recap.plugins").exception("A language's fix failed")
    return bubbles


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
