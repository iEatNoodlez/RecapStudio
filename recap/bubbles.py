"""Speech bubbles, captions and sound effects found by a trained model, so a webtoon's strip isn't cut through them
(panels.detect_strip). Optional add-on: comic-text-and-bubble-detector by ogkalu (RT-DETR-v2, Apache 2.0), trained on
about 11,000 manga, webtoon, manhua and western comic pictures to find speech bubbles, the text inside them and text
outside them. Its small version (v4-s, in 8-bit numbers): an 11.1 MB download into data\\addons\\bubbles.

The lettering check (panels._lettering) keeps cuts out of the lines of text, but not out of the box or bubble around
them: a cut between the outline and the text left a sliver of the box on one piece and the rest, open at the top, on
the next. On 11 test chapters (10 English webtoons, 1 Korean) 467 of 1,885 cuts did; with the model's boxes as rows
not to cut, 7: 4 along flat rows (a borderless or doubtful bubble's empty edge, nothing shows) and 3 in pieces over
panels.THROUGH_BUBBLES widths tall with no other place to split.

The strip is looked at in windows 1.5 of its widths tall, each squeezed to the model's 640 x 640 (it was trained on
pictures resized that way, tall webtoons split), one after another without overlap: a bubble a window's edge cuts is
found in both halves, and their rows together are its rows. Square windows overlapping by half found the same in three
times as long. About 0.13 s for each width of strip on the CPU (0.2 s for strips 1,000 pixels wide or more); the 8-bit
model runs no faster on an NVIDIA GPU, so it stays on the CPU.
"""
import threading

import cv2
import numpy as np

from . import addon, store

DOWNLOAD = addon.Download(store.ADDONS_DIR / "bubbles", {
    "detector.onnx": ("ogkalu/comic-text-and-bubble-detector", "16e8a622f91fabc6b5b65c96d32d1183f8843546",
                      "detector-v4-s_int8.onnx", 11120765,
                      "5fe9e4f576e49d4e7e8b0e029d6d3cdc252abd4694113e1cae120e62c931ea79"),
})
SIZE = 640        # the model's input
THRESHOLD = 0.3   # a find's least score
TALL = 1.5        # a window's height, in strip widths
MARGIN = 4        # rows kept clear above and below each find
BUBBLE, TEXT_IN_BUBBLE, TEXT_FREE = 0, 1, 2
_session = None
_lock = threading.Lock()


def installed() -> bool:
    return DOWNLOAD.installed()


def _model():
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort
            _session = ort.InferenceSession(str(DOWNLOAD.path("detector.onnx")), providers=["CPUExecutionProvider"])
        return _session


def forget() -> None:
    """Close the model (before its file is removed)."""
    global _session
    with _lock:
        _session = None


def find(img: np.ndarray) -> list[tuple[float, float, float, float, int]]:
    """One picture's finds: (left, top, right, bottom in its pixels, kind: BUBBLE, TEXT_IN_BUBBLE or TEXT_FREE)."""
    h, w = img.shape[:2]
    x = cv2.resize(img, (SIZE, SIZE), interpolation=cv2.INTER_AREA)[..., ::-1]  # RGB
    x = np.ascontiguousarray(x.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
    labels, boxes, scores = _model().run(None, {"images": x, "orig_target_sizes": np.array([[w, h]], np.int64)})
    keep = scores[0] >= THRESHOLD
    return [(*map(float, b), int(k)) for b, k in zip(boxes[0][keep], labels[0][keep])]


def zones(pages: list[np.ndarray], cancelled=None) -> tuple[np.ndarray, np.ndarray]:
    """For a strip of pages of one width (top to bottom): the rows not to cut where a band is mostly one colour
    (bubbles and text), and the rows not to cut even where flat (text: a borderless caption's lines have flat rows
    between them)."""
    W = pages[0].shape[1]
    offsets = np.cumsum([0] + [im.shape[0] for im in pages])
    H = int(offsets[-1])
    keep, text = np.zeros(H, bool), np.zeros(H, bool)
    side = int(W * TALL)
    for top in range(0, H, side):
        if cancelled:
            cancelled()
        bottom = min(H, top + side)
        if bottom - top < W // 4 and top:  # a sliver at the end: the last window ends there instead
            top = max(0, bottom - side)
        window = np.vstack([im[max(top, offsets[k]) - offsets[k]:min(bottom, offsets[k + 1]) - offsets[k]]
                            for k, im in enumerate(pages) if offsets[k] < bottom and offsets[k + 1] > top])
        for _, y0, _, y1, kind in find(window):
            rows = slice(max(0, top + int(y0) - MARGIN), min(H, top + int(np.ceil(y1)) + MARGIN))
            keep[rows] = True
            if kind != BUBBLE:
                text[rows] = True
    return keep, text
