"""A translated comic's pictures with the English in place of the original text (Video settings → Put the
English in the pictures; the boxes and their English come from ocr.read_bubbles and translate.py).

Speech, thoughts and narration on a plain background (a bubble, a caption box) are painted out (as
textless.py does, a bubble's lines together) and the English is lettered in, fitted to the bubble: its shape is
found by filling from where the text was, and each line of English is as wide as the bubble is at that height,
centred where the original text was. The letters take the original's colour. Signs get the English in a small
caption box over them; speech drawn over the art, lettering drawn big (a shout) and sound effects are left as
drawn.

A scene's pictures are panels, whose boxes are known. A scrolling webtoon shows whole pages: each piece's boxes
are placed in the strip where the piece was cut from (its "at", found once by cover.locate), and each page is
lettered with a little of the pages either side, so a bubble across a page break is lettered on both halves.
A scan site's watermark (a box the translator called "none" that reads like a web address) is painted out.
Copies are kept in the project's typeset folder, under a fingerprint of the picture and its English, and reused.
"""
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import fonts, textless
from .panels import imread

VERSION = 14
LETTERED = ("speech", "thought", "narration")  # put back in their bubble (speech over the art stays as drawn)
CAPTIONED = ("sign",)                            # given a caption box
BIG = 0.11    # lettering taller than this share of the picture's width (and 60 px) is part of the art: left.
              # (A Korean webtoon 720 px wide: dialogue 40-55 px, a shout drawn into the art 170 px)
LOOK = "comic-bold"  # Comic Sans MS Bold, the usual comic look (fonts.py: Linux's stand-ins)
# a scan site's watermark: a web address, or "site" in Korean (가장 빠른 웹툰 제공 사이트, "the fastest webtoon site")
STAMP = re.compile(r"https?|\.(c[o0]m|net|org)\b|\bwww\.|newt[o0]k[i1]|사이트|webt[o0]{2}n", re.I)
MARGIN = 0.25  # of a page's height, taken from the pages either side when it's lettered


def _font(size: int) -> ImageFont.FreeTypeFont:
    return fonts.load(LOOK, size)


def _rows(mask: np.ndarray, cx=None) -> list:
    """For each row of a bubble's mask, its filled span (left, right), or None: the part of the row around
    column cx (a burst bubble's spikes cut its rows into pieces), or else all of it."""
    out = []
    for row in mask:
        xs = np.flatnonzero(row)
        if not len(xs):
            out.append(None)
            continue
        if cx is None:
            out.append((int(xs[0]), int(xs[-1])))
            continue
        cut = np.flatnonzero(np.diff(xs) > 3)
        spans = list(zip(xs[np.r_[0, cut + 1]], xs[np.r_[cut, len(xs) - 1]]))
        a, b = min(spans, key=lambda s: 0 if s[0] <= cx <= s[1] else min(abs(s[0] - cx), abs(s[1] - cx)))
        out.append((int(a), int(b)))
    return out


def _bubble(out: np.ndarray, box, bg) -> tuple:
    """The bubble the erased text sat in: (x0, y0, mask, closed) of its inside, filled from the text's box in its
    background colour; or a rectangle a little bigger than the box when the fill runs off (closed: False)."""
    h, w = out.shape[:2]
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    m = max(bw, bh)
    X0, Y0, X1, Y1 = max(0, x0 - m), max(0, y0 - m), min(w, x1 + m), min(h, y1 + m)
    region = out[Y0:Y1, X0:X1]
    near = (np.abs(region.astype(np.int16) - bg.astype(np.int16)).max(axis=2) < textless.NEAR).astype(np.uint8)
    # opened first, so the fill doesn't run out through a thin gap in the outline (a see-through bubble's faint
    # edge, on a test webtoon: its fill ran into the wall behind it at twice the bubble's size)
    round5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    n, labels = cv2.connectedComponents(cv2.morphologyEx(near, cv2.MORPH_OPEN, round5), connectivity=4)
    inside = labels[y0 - Y0:y1 - Y0, x0 - X0:x1 - X0]
    ids, counts = np.unique(inside[inside > 0], return_counts=True)
    if len(ids):
        blob = cv2.dilate((labels == ids[np.argmax(counts)]).astype(np.uint8), round5).astype(bool) & near.astype(bool)
        ys, xs = np.nonzero(blob)
        touches = (xs.min() == 0) + (ys.min() == 0) + (xs.max() == X1 - X0 - 1) + (ys.max() == Y1 - Y0 - 1)
        # a closed bubble or caption box; not the page's background (a burst bubble's inside runs out between
        # its spikes: on a test webtoon to 16 times its text's box; bubbles there were 2-5 times theirs)
        if touches < 2 and len(xs) <= 10 * max(1, bw * bh):
            return X0, Y0, blob, True
    # (not much wider than the text: around it may be the art, a burst bubble's spikes)
    pad_x, pad_y = round(max(0.05 * bw + 6, 0.5 * bh)), round(0.2 * bh) + 6
    a0, b0, a1, b1 = max(0, x0 - pad_x), max(0, y0 - pad_y), min(w, x1 + pad_x), min(h, y1 + pad_y)
    return a0, b0, np.ones((b1 - b0, a1 - a0), bool), False


def _shared(a, b) -> float:
    """How much of two bubbles ((x0, y0, mask) each) is the same, as a share of the smaller."""
    (ax, ay, am), (bx, by, bm) = a, b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + am.shape[1], bx + bm.shape[1]), min(ay + am.shape[0], by + bm.shape[0])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    both = am[y0 - ay:y1 - ay, x0 - ax:x1 - ax] & bm[y0 - by:y1 - by, x0 - bx:x1 - bx]
    return float(both.sum()) / max(1, min(int(am.sum()), int(bm.sum())))


def _joined(a, b) -> tuple:
    (ax, ay, am), (bx, by, bm) = a, b
    x0, y0 = min(ax, bx), min(ay, by)
    m = np.zeros((max(ay + am.shape[0], by + bm.shape[0]) - y0, max(ax + am.shape[1], bx + bm.shape[1]) - x0), bool)
    m[ay - y0:ay - y0 + am.shape[0], ax - x0:ax - x0 + am.shape[1]] |= am
    m[by - y0:by - y0 + bm.shape[0], bx - x0:bx - x0 + bm.shape[1]] |= bm
    return x0, y0, m


def _beside(a, b, size, vertical) -> bool:
    """Two text boxes of one bubble: one under the other (beside it, for columns of vertical text) with less than
    a line between them, and centred together, as a bubble's lines are. (Bubbles drawn touching can be as close,
    but sit apart: on a test webtoon two chained bubbles' text was 0.6 of a line apart and 183 px off centre, a
    bubble cut in two by the panel cutter 27 px.)"""
    gap_y = max(a[1], b[1]) - min(a[3], b[3])
    gap_x = max(a[0], b[0]) - min(a[2], b[2])
    if vertical:
        return gap_x < size and abs((a[1] + a[3]) - (b[1] + b[3])) / 2 < 1.2 * size
    return gap_y < size and abs((a[0] + a[2]) - (b[0] + b[2])) / 2 < 1.2 * size


def _bbox(lines, w, h) -> tuple:
    """The box around text lines, in whole pixels inside the picture (w, h)."""
    return (max(0, int(min(l[0] for l in lines))), max(0, int(min(l[1] for l in lines))),
            min(w, int(np.ceil(max(l[2] for l in lines)))), min(h, int(np.ceil(max(l[3] for l in lines)))))


def _runs(todo) -> list:
    """Boxes to letter ((box, lines, vertical, size, english) each) in runs of the lines of one bubble: each one
    beside the run before it (see _beside)."""
    runs = []
    for t in sorted(todo, key=lambda t: (t[0][1], t[0][0])):
        box, vertical, size = t[0], t[2], t[3]
        r = next((r for r in runs if r["vertical"] == vertical and _beside(r["box"], box, size, vertical)), None)
        if r:
            r["box"] = (min(r["box"][0], box[0]), min(r["box"][1], box[1]), max(r["box"][2], box[2]),
                        max(r["box"][3], box[3]))
            r["items"].append(t)
        else:
            runs.append({"box": box, "vertical": vertical, "items": [t]})
    return [r["items"] for r in runs]


def _within(mask, x0, y0, box) -> bool:
    """Is the middle of box in the mask (at x0, y0)?"""
    cx, cy = (box[0] + box[2]) // 2 - x0, (box[1] + box[3]) // 2 - y0
    return 0 <= cy < mask.shape[0] and 0 <= cx < mask.shape[1] and bool(mask[cy, cx])


def _around(at, box, size) -> tuple:
    """The part of a bubble (x0, y0, mask) near the text box: its own, when bubbles drawn touching share an inside."""
    x0, y0, mask = at
    bx0, by0, bx1, by1 = box
    mx, my = max(2.5 * size, 0.3 * (bx1 - bx0)), max(2.0 * size, 0.4 * (by1 - by0))
    a0, b0 = max(x0, int(bx0 - mx)), max(y0, int(by0 - my))
    a1, b1 = min(x0 + mask.shape[1], int(bx1 + mx)), min(y0 + mask.shape[0], int(by1 + my))
    if a1 <= a0 or b1 <= b0:
        return at
    return a0, b0, mask[b0 - y0:b1 - y0, a0 - x0:a1 - x0]


def _nearer(at, own, other) -> np.ndarray:
    """Where in a bubble (x0, y0, mask) is nearer its own text box than another's: how a chain's shared inside
    is split between its bubbles."""
    x0, y0, mask = at
    ys, xs = np.mgrid[y0:y0 + mask.shape[0], x0:x0 + mask.shape[1]]

    def far(b):
        return np.hypot(np.maximum(np.maximum(b[0] - xs, xs - b[2]), 0), np.maximum(np.maximum(b[1] - ys, ys - b[3]), 0))
    return far(own) <= far(other)


def _filled(mask: np.ndarray) -> np.ndarray:
    """A bubble's inside with its holes (whatever was drawn in it) filled."""
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.zeros(mask.shape, np.uint8)
    cv2.drawContours(out, contours, -1, 1, -1)
    return out.astype(bool)


def _layout(words, rows, top_y, size, margin):
    """Lines of words for a block starting at top_y, each as wide as the bubble is at its height; None if
    the words don't fit in the bubble's height."""
    font = _font(size)
    lh = round(size * 1.12)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    lines, i, y = [], 0, top_y
    while i < len(words):
        band = [r for r in rows[max(0, y):max(0, y + lh)] if r] if 0 <= y < len(rows) else []
        if y < 0 or y + lh > len(rows) or len(band) < lh * 0.9:
            return None
        left, right = max(r[0] for r in band) + margin, min(r[1] for r in band) - margin
        width = right - left
        if width < size:
            return None
        line = words[i]
        if probe.textlength(line, font=font) > width:
            return None  # a word wider than the bubble at this size
        i += 1
        while i < len(words) and probe.textlength(f"{line} {words[i]}", font=font) <= width:
            line += f" {words[i]}"
            i += 1
        lines.append((line, (left + right) / 2, y))
        y += lh
    return lines


def _fit(text, mask, max_size, middle=None, cx=None):
    """The biggest size the text fits in the bubble mask at: (size, [(line, centre x, top y)]), centred on row
    middle (where the original text's middle was), or else on the bubble's; kept a little off the bubble's top
    and bottom, and on each row to its part around column cx (see _rows)."""
    seen = {}

    def rows_at(gap):  # the mask's rows with gap rows off its top and bottom edges
        if gap not in seen:
            seen[gap] = _rows(cv2.erode(mask.astype(np.uint8), np.ones((2 * gap + 1, 1), np.uint8)), cx)
        return seen[gap]
    if not mask.any():
        return None
    words = text.split()
    for size in range(max_size, 9, -1):
        lh = round(size * 1.12)
        margin = max(3, round(size * 0.35))
        rows = rows_at(max(2, round(size * 0.25)))
        filled = [i for i, r in enumerate(rows) if r]
        if not filled:
            continue
        for cy in [c for c in (middle, (filled[0] + filled[-1]) / 2) if c is not None]:
            for n in range(1, len(words) + 1):
                top = round(cy - n * lh / 2)
                if top < filled[0]:
                    break
                lines = _layout(words, rows, top, size, margin)
                if lines is not None and len(lines) <= n:
                    if len(lines) < n:  # fewer lines than planned: centre them again
                        lines = _layout(words, rows, round(cy - len(lines) * lh / 2), size, margin) or lines
                    return size, lines
    return None


def _letter(canvas: Image.Image, x0, y0, mask, text, ink, max_size, box):
    """Draw the English in the bubble (mask at x0, y0) in the ink colour, centred where the original text (box)
    was; if it doesn't fit the bubble, smaller in the original text's place. False if it can't fit."""
    bx0, by0, bx1, by1 = (round(v) for v in box)
    middle = (by0 + by1) / 2 - y0
    got = _fit(text, mask, max_size, middle, (bx0 + bx1) / 2 - x0)
    if got is None:
        place = np.zeros(mask.shape, bool)
        place[max(0, by0 - y0 - 4):max(0, by1 - y0 + 4), max(0, bx0 - x0 - 4):max(0, bx1 - x0 + 4)] = True
        got = _fit(text, place, max_size, middle) or _fit(text, np.ones(mask.shape, bool), max_size, middle)
        if got is None:
            return False
    size, lines = got
    draw = ImageDraw.Draw(canvas)
    font = _font(size)
    for line, cx, top in lines:
        draw.text((x0 + cx, y0 + top), line, font=font, fill=tuple(int(v) for v in ink[::-1]), anchor="ma")
    return True


def _caption(canvas: Image.Image, box, text):
    """The English in a small white caption box over (or at) the text it translates."""
    w, h = canvas.size
    x0, y0, x1, y1 = box
    size = max(13, min(28, round(w / 40)))
    font = _font(size)
    draw = ImageDraw.Draw(canvas)
    width = max(x1 - x0, round(w * 0.28))
    lines, line = [], ""
    for word in text.split():
        if line and draw.textlength(f"{line} {word}", font=font) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    lh = round(size * 1.15)
    tw = max(draw.textlength(ln, font=font) for ln in lines)
    pw, ph = tw + 2 * round(size * 0.5), len(lines) * lh + round(size * 0.6)
    pw, ph = max(pw, x1 - x0 + 10), max(ph, y1 - y0 + 8)  # at least over the original text, hiding it
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    bx = min(max(4, cx - pw / 2), w - pw - 4)
    by = min(max(4, cy - ph / 2), h - ph - 4)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rounded_rectangle((bx, by, bx + pw, by + ph), radius=round(size * 0.35),
                                              fill=(255, 255, 255, 250), outline=(30, 30, 30, 255), width=2)
    canvas.alpha_composite(overlay)
    top = by + (ph - len(lines) * lh) / 2 + round(size * 0.05)
    for k, ln in enumerate(lines):
        draw.text((bx + pw / 2, top + k * lh), ln, font=font, fill=(20, 20, 20, 255), anchor="ma")


def letter(img: np.ndarray, boxes: list[dict]) -> tuple[np.ndarray, int]:
    """The picture with each box's English in place of its text: boxes [{"lines": [[x0, y0, x1, y1]],
    "vertical", "kind", "english"}] as translated. Returns (picture, how many boxes were lettered)."""
    out = img.copy()
    stamps = [b["lines"] for b in boxes if b.get("kind") == "none" and b.get("lines") and STAMP.search(b.get("text") or "")]
    if stamps:  # a scan site's watermark: filled in from around it
        mask = np.zeros(out.shape[:2], np.uint8)
        for lines in stamps:
            for x0, y0, x1, y1 in lines:
                p = max(3, round(0.25 * (y1 - y0)))
                cv2.rectangle(mask, (int(x0) - p, int(y0) - p), (int(x1) + p, int(y1) + p), 255, -1)
        out = cv2.inpaint(out, mask, 7, cv2.INPAINT_TELEA)
        img = out.copy()
    h, w = out.shape[:2]
    big = max(60.0, BIG * w)
    todo, captions = [], []
    for b in boxes:
        english = (b.get("english") or "").strip()
        if not english or b.get("kind") not in LETTERED + CAPTIONED or not b.get("lines"):
            continue
        box = _bbox(b["lines"], w, h)
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        if b["kind"] in LETTERED:
            size = float(np.median([(l[2] - l[0]) if b.get("vertical") else (l[3] - l[1]) for l in b["lines"]]))
            if size <= big:  # (lettering drawn big as part of the art, a shout: left as drawn)
                todo.append((box, b["lines"], bool(b.get("vertical")), size, english))
            continue
        captions.append((box, english))
    # a bubble's lines read as boxes of their own are erased together (a line's surroundings hold the next line's
    # letters). Speech drawn over the art, with no plain bubble around it, is left as drawn
    erased, left = [], []
    for k, run in enumerate(_runs(todo)):
        both = textless.erase_group(out, img, [l for t in run for l in t[1]], run[0][2]) if len(run) > 1 else None
        for box, lines, vertical, size, english in run:
            info = both or textless.erase_group(out, img, lines, vertical)
            if info:
                erased.append(({**info, "vertical": vertical, "run": k if both else None}, box, size, english))
            else:
                left.append((box, english))
    # the bubbles: boxes next to each other in one bubble (erased together, or its lines read apart, or cut into
    # two pieces) are lettered together. Bubbles drawn touching (a chain) share their inside, so being in it
    # isn't enough
    groups = []
    for info, box, size, english in sorted(erased, key=lambda e: (e[1][1], e[1][0])):
        x0, y0, mask, closed = _bubble(out, box, info["bg"])
        g = next((g for g in groups if info["run"] is not None and g["info"]["run"] == info["run"]), None) or \
            next((g for g in groups if _shared(g["at"], (x0, y0, mask)) > 0.5
                  and _beside(g["box"], box, size, info["vertical"])), None)
        if g:  # (a misread scrap can be smaller than the bubble's lettering: the biggest is the size)
            g.update(at=_joined(g["at"], (x0, y0, mask)), size=max(g["size"], size), closed=g["closed"] and closed,
                     box=(min(g["box"][0], box[0]), min(g["box"][1], box[1]), max(g["box"][2], box[2]),
                          max(g["box"][3], box[3])))
            g["texts"].append((box[1], box[0], english))
        else:
            groups.append({"at": (x0, y0, mask), "texts": [(box[1], box[0], english)], "size": size,
                           "closed": closed, "info": info, "box": box})
    for g in groups:  # each lettered in the part of the bubble around its own text (a chain's others have theirs)
        g["at"] = _around(g["at"], g["box"], g["size"])
    near = [[o["box"] for o in groups if o is not g and _shared(g["at"], o["at"]) > 0] for g in groups]
    for g, others in zip(groups, near):  # a chain's shared inside: each bubble the part nearest its text
        x0, y0, mask = g["at"]
        for box in others:
            mask = mask & _nearer(g["at"], g["box"], box)
        g["at"] = (x0, y0, mask)
    for g in groups:  # a closed bubble is cleared inside (a mark past the reader's box, say), unless much is in it
        if g["closed"]:
            x0, y0, mask = g["at"]
            filled = _filled(mask)
            if (filled & ~mask).sum() < 0.35 * filled.sum():
                region = out[y0:y0 + mask.shape[0], x0:x0 + mask.shape[1]]
                bg = g["info"]["bg"]
                stray = filled & (np.abs(region.astype(np.int16) - bg.astype(np.int16)).max(axis=2) >= textless.NEAR)
                seen = region[mask]
                if len(seen) and (np.percentile(seen, 95, axis=0) - np.percentile(seen, 5, axis=0)).max() < 12:
                    region[filled] = np.median(seen, axis=0).astype(np.uint8)  # one colour inside: all of it, faint
                    # leftovers of the erased letters too
                elif stray.any():
                    if g["info"]["flat"]:
                        region[stray] = bg.astype(np.uint8)
                    else:
                        region[:] = cv2.inpaint(region, stray.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)
                g["at"] = (x0, y0, filled)
                for t in [t for t in left if _within(filled, x0, y0, t[0])]:  # a box the eraser left, cleared
                    g["texts"].append((t[0][1], t[0][0], t[1]))                # with its bubble: its English too
                    left.remove(t)
    canvas = Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB)).convert("RGBA")
    done = 0
    for g in groups:
        x0, y0, mask = g["at"]
        ink, bg = g["info"]["ink"], g["info"]["bg"]
        if abs(float(np.mean(ink)) - float(np.mean(bg))) < 60:  # too close to read: black or white
            ink = np.zeros(3) if float(np.mean(bg)) > 128 else np.full(3, 255.0)
        # the English about as big as the original lettering, or smaller to fit
        text = " ".join(t[2] for t in sorted(g["texts"], key=lambda t: t[:2])).upper()
        if _letter(canvas, x0, y0, mask, text, ink, max(12, min(48, round(1.05 * g["size"]))), g["box"]):
            done += len(g["texts"])
    for box, english in captions:
        _caption(canvas, box, english)
        done += 1
    return cv2.cvtColor(np.array(canvas.convert("RGB")), cv2.COLOR_RGB2BGR), done


# ---- the video's pictures ------------------------------------------------------------

def _strip_boxes(pieces: list[dict], project_dir: Path) -> list[dict]:
    """A scrolling chapter's boxes to letter or paint out, their lines in the strip's rows (every page at the
    first page's width), each piece placed where it was cut from (its "at": x, y, width, height in the strip)."""
    out = []
    for x in pieces:
        boxes = [b for b in x.get("bubbles") or [] if b.get("lines") and (b.get("english") or b.get("kind") == "none")]
        if "at" not in x or not boxes:
            continue
        try:
            with Image.open(project_dir / "panels" / x["file"]) as im:
                cw, ch = im.size
        except OSError:
            continue
        ax, ay, aw, ah = x["at"]
        fx, fy = aw / cw, ah / ch
        out += [{**b, "lines": [[ax + l[0] * fx, ay + l[1] * fy, ax + l[2] * fx, ay + l[3] * fy] for l in b["lines"]]}
                for b in boxes]
    return out


def _page_boxes(pages, i, strip_boxes) -> tuple[list[dict], float]:
    """The boxes that touch page i of a strip ([(path, width, height)]), and where page i starts in the strip."""
    base = pages[0][1]
    tops, top = [], 0.0
    for _, pw, ph in pages:
        tops.append(top)
        top += ph * base / pw
    start, end = tops[i], tops[i] + pages[i][2] * base / pages[i][1]
    return [b for b in strip_boxes if any(l[1] < end and l[3] > start for l in b["lines"])], tops


def _letter_page(pages, i, boxes, tops, page) -> np.ndarray:
    """Page i of a strip (page: its picture) with its boxes lettered: done with a margin of the pages either side
    (in page i's pixels), so a bubble across a page break comes out whole on both, then cut back to the page."""
    base = pages[0][1]
    _, pw, ph = pages[i]
    k = pw / base  # strip -> page i's pixels
    margin = round(MARGIN * ph)
    parts, above = [], 0
    if i > 0:
        prev = imread(pages[i - 1][0])
        if prev is not None:
            prev = cv2.resize(prev, (page.shape[1], max(1, round(prev.shape[0] * page.shape[1] / prev.shape[1]))))
            above = min(margin, prev.shape[0])
            parts.append(prev[prev.shape[0] - above:])
    parts.append(page)
    if i + 1 < len(pages):
        nxt = imread(pages[i + 1][0])
        if nxt is not None:
            nxt = cv2.resize(nxt, (page.shape[1], max(1, round(nxt.shape[0] * page.shape[1] / nxt.shape[1]))))
            parts.append(nxt[:margin])
    window = np.vstack(parts)
    y_top = tops[i] - above / k  # the window's first row, in the strip
    placed = [{**b, "lines": [[l[0] * k, (l[1] - y_top) * k, l[2] * k, (l[3] - y_top) * k] for l in b["lines"]]}
              for b in boxes]
    done, _ = letter(window, placed)
    return done[above:above + page.shape[0]]


def _fingerprint(src: Path, boxes) -> str:
    try:
        st = src.stat()
        stat = [str(src), st.st_size, st.st_mtime_ns]
    except OSError:
        stat = [str(src), None, None]
    want = [[b.get("text"), b.get("kind"), b.get("english"), [[round(v) for v in l] for l in b.get("lines") or []]]
            for b in boxes]
    return hashlib.sha1(json.dumps([VERSION, stat, want], ensure_ascii=False).encode()).hexdigest()[:16]


def _copy_name(src: Path, boxes) -> str:
    ext = src.suffix.lower() if src.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") else ".png"
    return f"{src.stem[:40]}-{_fingerprint(src, boxes)}{ext}"


def attach(items: list, project: dict, project_dir: Path, chapter_panels: list[dict], compute: bool = False,
           workers: int = 1, progress=None) -> None:
    """Point each item's pictures at their lettered copies (as textless.attach does): a scene's panels with
    their own boxes; a strip's pages with the boxes of the pieces that fall on them (each piece needs its "at",
    see _strip_boxes). With compute the missing copies are made now (workers at a time; progress(done, total)
    as it goes)."""
    folder = project_dir / "typeset"
    by_file = {(project_dir / "panels" / x["file"]).resolve(): x.get("bubbles") or [] for x in chapter_panels}
    todo = {}  # picture -> (copy, how to make it)
    for it in items:
        if it.get("type") == "scene":
            for p in it["images"]:
                boxes = by_file.get(Path(p).resolve())
                if boxes and any(b.get("english") or b.get("kind") == "none" for b in boxes):
                    todo[Path(p)] = (folder / _copy_name(Path(p), boxes), lambda img, boxes=boxes: letter(img, boxes)[0])
        else:
            pages = [(Path(p), pw, ph) for p, pw, ph in it.get("pages", [])]
            strip_boxes = _strip_boxes(chapter_panels, project_dir)
            for i, (p, _, _) in enumerate(pages):
                boxes, tops = _page_boxes(pages, i, strip_boxes)
                if boxes:
                    todo[p] = (folder / _copy_name(p, boxes),
                               lambda img, i=i, boxes=boxes, tops=tops, pages=pages: _letter_page(pages, i, boxes, tops, img))
    missing = [src for src, (dst, _) in todo.items() if not dst.exists()]
    if missing and compute:
        folder.mkdir(parents=True, exist_ok=True)

        def one(src):
            img = imread(src)
            if img is None:
                return
            dst, make = todo[src]
            done = make(img)
            if done is not None:
                textless._save(dst, done)
        pool = ThreadPoolExecutor(max(1, workers))
        try:
            for i, f in enumerate([pool.submit(one, src) for src in missing], 1):
                f.result()
                if progress:
                    progress(i, len(missing))
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
    for it in items:
        if it.get("type") == "scene":
            it["images"] = [todo[Path(p)][0] if Path(p) in todo and todo[Path(p)][0].exists() else p
                            for p in it["images"]]
        else:
            it["pages"] = [(todo[Path(p)][0] if Path(p) in todo and todo[Path(p)][0].exists() else p, pw, ph)
                           for p, pw, ph in it["pages"]]


def prune(project_dir: Path, items: list) -> None:
    """Delete the lettered copies none of items uses any more. Call it with every chapter's items."""
    folder = project_dir / "typeset"
    used = {Path(x).name for it in items for x in (it.get("images") or [q for q, _, _ in it.get("pages", [])])}
    for f in folder.glob("*") if folder.exists() else []:
        if f.name not in used:
            f.unlink(missing_ok=True)
