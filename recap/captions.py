"""Captions in the picture (the project's Video settings → Captions), the way short-video apps put them on
(CapCut's auto-captions): the narration a few words at a time, big and bold low in the middle of the
picture, the word being said picked out in colour.

Word times: with the speech recognizer installed (Vosk's small English model, the add-on that lines up your
own recordings), each line's audio is heard and its words lined up with the script's; the ones it didn't hear
are spread between those it did. Without it, each comma and full stop is lined up with a pause in the line's
audio, and the words between are spread over the speech between those pauses, by their length, so none lands
in a pause: TTS voices keep an even pace, so a word is rarely more than a word early or late. Kept next to the line's audio (<audio>.words.json) while the line
and its audio stay the same.

Styles (STYLES): "classic" (white capitals with a black outline, the word being said in colour and popping
up a little, 3 words at a time), "box" (a coloured box behind the word being said, up to 4 words), and
"minimal" (sentence case with a soft shadow, up to 6 words on two lines, nothing moving: for long videos).
Sizes and timings follow CapCut's look as copied by Remotion's caption template, Submagic and Opus Clip.

Each page of words is drawn once with Pillow for each word being said (and each frame of its pop and of the
page coming in), and laid over the frames it's on; drawing processes keep their own copies.
"""
import bisect
import difflib
import json
import re
import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

VERSION = 2  # bump when how captions look or are timed changes, so chapter videos are drawn again
FONTS = {  # in every Windows
    "Arial Black": "ariblk.ttf", "Segoe UI Black": "seguibl.ttf", "Impact": "impact.ttf",
    "Comic Sans": "comicbd.ttf",
}
COLORS = {"yellow": (255, 229, 0), "green": (57, 229, 8), "red": (255, 59, 48), "purple": (124, 58, 237),
          "white": (255, 255, 255)}
# size: of the letters, as a share of the picture's shorter side; words: most on a page; span: a page's
# longest stretch of speech (s); lines: most lines; y: the middle of the captions, down the picture
# (portrait, landscape); pop: how much bigger the word being said gets; box: a box in colour behind it;
# panel: a dark see-through panel behind the page
STYLES = {
    "classic": {"size": 0.075, "words": 3, "span": 1.2, "lines": 2, "y": (0.68, 0.80), "caps": True,
                "outline": 0.11, "shadow": True, "pop": 1.15, "enter": True, "box": False, "panel": False},
    "box": {"size": 0.066, "words": 4, "span": 1.5, "lines": 2, "y": (0.68, 0.80), "caps": True,
            "outline": 0.09, "shadow": False, "pop": 1.0, "enter": True, "box": True, "panel": False},
    "minimal": {"size": 0.05, "words": 6, "span": 2.4, "lines": 2, "y": (0.74, 0.85), "caps": False,
                "outline": 0.0, "shadow": False, "pop": 1.0, "enter": False, "box": False, "panel": True},
}
WIDTH = 0.86        # widest a line can be, as a share of the picture's width
PAUSE = 0.35        # a pause this long (s) starts a new page
HOLD = 0.4          # a page's last word stays this long (s) after it's said, unless the next page starts
POP, ENTER = 0.1, 0.12  # how long (s) a word's pop and a page coming in take
STEPS = 4           # frames drawn for each of those
FRAME = 0.02        # loudness is measured every this many seconds


def style_of(settings) -> dict | None:
    """The project's caption style, or None when captions are off."""
    name = settings.get("captions") or "off"
    return STYLES.get(name)


def signature(settings) -> list:
    """What the caption settings add to a chapter video's fingerprint (nothing when they're off)."""
    if not style_of(settings):
        return []
    return ["captions", VERSION, settings.get("captions"), settings.get("captions_color", "yellow"),
            settings.get("captions_font", "Arial Black"), heard()]


def font_path(settings) -> str:
    name = FONTS.get(settings.get("captions_font"), FONTS["Arial Black"])
    path = Path("C:/Windows/Fonts") / name
    return str(path if path.exists() else Path("C:/Windows/Fonts") / "arialbd.ttf")


# ---- when each word is said --------------------------------------------------------------------------

def _shown(text: str) -> list[str]:
    """A line's words as shown: split at spaces, a dash or a lone mark joined to the word before."""
    out = []
    for w in text.split():
        if out and not re.search(r"[A-Za-z0-9]", w):
            out[-1] += w if w in ",.!?;:…" else " " + w
        else:
            out.append(w)
    return out


def _weight(word: str) -> float:
    return len(re.sub(r"[^A-Za-z0-9]", "", word)) + 1.5


def _speech(audio: np.ndarray, rate: int) -> np.ndarray:
    """For each FRAME of audio: whether it sounds like speech (within 20 dB of the line's speech; short gaps
    inside a word filled in)."""
    n = max(1, int(FRAME * rate))
    k = len(audio) // n
    if not k:
        return np.zeros(0, bool)
    rms = np.sqrt((audio[:k * n].reshape(k, n) ** 2).mean(axis=1))
    loud = float(np.percentile(rms, 90))
    on = rms > 0.1 * loud if loud > 0 else np.ones(k, bool)
    gap = 0
    for i in range(k):  # gaps under 80 ms are inside a word
        if on[i]:
            if 0 < gap <= 4 and i - gap > 0:
                on[i - gap:i] = True
            gap = 0
        else:
            gap += 1
    return on


def _spread(words: list[str], audio: np.ndarray, rate: int, a: float = 0.0, b: float | None = None) -> list[list]:
    """[[word, start, end]] spread over the speech in audio between a and b seconds, by the words' lengths."""
    on = _speech(audio, rate)
    b = len(audio) / rate if b is None else b
    i0, i1 = int(a / FRAME), max(int(a / FRAME) + 1, int(np.ceil(b / FRAME)))
    part = on[i0:i1] if i1 <= len(on) else np.concatenate([on[i0:], np.ones(i1 - max(i0, len(on)), bool)])
    if not part.any():
        part = np.ones(i1 - i0, bool)
    said = np.cumsum(part) * FRAME  # seconds of speech up to the end of each frame
    total = float(said[-1])
    weights = np.array([_weight(w) for w in words], float)
    edges = np.concatenate([[0.0], np.cumsum(weights)]) / max(weights.sum(), 1e-6) * total

    def at(s, end):  # the time s seconds of speech in: a word's start at the first frame of speech after it
        j = int(np.searchsorted(said, s - 1e-9 if end else s + 1e-9))
        j = min(j, len(said) - 1)
        return a + (j + 1) * FRAME if end else a + j * FRAME
    return [[w, round(at(edges[k], False), 3), round(max(at(edges[k], False) + 0.05, at(edges[k + 1], True)), 3)]
            for k, w in enumerate(words)]


def _pauses(audio: np.ndarray, rate: int, least: float = 0.12) -> list[tuple[float, float]]:
    """Pauses of at least `least` seconds between stretches of speech: [(start, end)]."""
    on = _speech(audio, rate)
    idx = np.nonzero(on)[0]
    if not len(idx):
        return []
    out, gap = [], None
    for i in range(idx[0], idx[-1] + 1):
        if not on[i] and gap is None:
            gap = i
        elif on[i] and gap is not None:
            if (i - gap) * FRAME >= least:
                out.append((gap * FRAME, i * FRAME))
            gap = None
    return out


def _by_pauses(words: list[str], audio: np.ndarray, rate: int) -> list[list]:
    """Word times without the recognizer: a voice pauses at commas and full stops, so each of those is lined
    up with a pause in the line's audio (in order, the nearest to where spreading by length puts it, within
    1.5 s), and the words between are spread over the speech between those pauses. On a 10.7 s test line
    spreading alone ran up to 0.8 s late by its end; its four marks matched its pauses exactly."""
    guess = _spread(words, audio, rate)
    marks = [k for k, w in enumerate(words[:-1]) if re.search(r"[,.;:!?…—]['\"’”)]*$", w)]
    pauses = _pauses(audio, rate)
    if not marks or not pauses:
        return guess
    # the best match in order (a mark without a pause costs 1; a pause without a mark, inside a phrase, nothing)
    n, m, far = len(marks), len(pauses), 1.5
    cost = np.full((n + 1, m + 1), np.inf)
    cost[0, :] = 0
    how = {}
    for i in range(1, n + 1):
        cost[i, 0] = i
        for j in range(1, m + 1):
            d = abs(guess[marks[i - 1]][2] - (pauses[j - 1][0] + pauses[j - 1][1]) / 2)
            options = [(cost[i - 1, j] + 1, "mark"), (cost[i, j - 1], "pause")]
            if d <= far:
                options.append((cost[i - 1, j - 1] + d, "both"))
            cost[i, j], how[i, j] = min(options)
    pairs, i, j = [], n, m
    while i and j:
        step = how[i, j]
        if step == "both":
            pairs.append((marks[i - 1], pauses[j - 1]))
        i, j = (i - 1, j - 1) if step == "both" else (i - 1, j) if step == "mark" else (i, j - 1)
    out, k, start = [], 0, 0.0
    for mark, (p0, p1) in sorted(pairs) + [(len(words) - 1, (len(audio) / rate, None))]:
        out += _spread(words[k:mark + 1], audio, rate, start, max(start + 0.05 * (mark + 1 - k), p0))
        k, start = mark + 1, p1
    return out


_vosk = {"model": None}
_vosk_lock = threading.Lock()


def heard() -> bool:
    """Whether word times come from the speech recognizer (installed) or are spread by length."""
    try:
        from . import voiceover
        return voiceover.installed()
    except Exception:
        return False


def _listen(audio: np.ndarray, rate: int) -> list[dict]:
    """What the recognizer hears in audio: [{"word", "start", "end"}]."""
    import vosk

    from . import voiceover
    with _vosk_lock:
        if _vosk["model"] is None:
            vosk.SetLogLevel(-1)
            _vosk["model"] = vosk.Model(str(voiceover.DOWNLOAD.folder / voiceover.MODEL))
        model = _vosk["model"]
    rec = vosk.KaldiRecognizer(model, voiceover.RATE)
    rec.SetWords(True)
    n = int(len(audio) * voiceover.RATE / rate)
    pcm = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio) if n else audio
    rec.AcceptWaveform((np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes())
    return json.loads(rec.FinalResult()).get("result", [])


def _lined_up(words: list[str], audio: np.ndarray, rate: int) -> list[list]:
    """Word times from what the recognizer heard: the words it heard where it heard them, the others spread
    over the speech between."""
    from .voiceover import tokens
    got = _listen(audio, rate)
    script = [(t, k) for k, w in enumerate(words) for t in tokens(w)]
    match = {}
    for x, y, size in difflib.SequenceMatcher(None, [t for t, _ in script], [g["word"] for g in got],
                                              autojunk=False).get_matching_blocks():
        for j in range(size):
            match[x + j] = y + j
    times = {}
    for j, (_, k) in enumerate(script):
        if j in match:
            g = got[match[j]]
            s, e = times.get(k, (g["start"], g["end"]))
            times[k] = (min(s, g["start"]), max(e, g["end"]))
    out = [None] * len(words)
    for k, (s, e) in times.items():
        out[k] = [words[k], round(s, 3), round(e, 3)]
    k, end = 0, len(audio) / rate
    while k < len(words):  # runs not heard: spread between the words heard around them
        if out[k] is not None:
            k += 1
            continue
        j = k
        while j < len(words) and out[j] is None:
            j += 1
        a = out[k - 1][2] if k else 0.0
        b = out[j][1] if j < len(words) else end
        out[k:j] = _spread(words[k:j], audio, rate, a, max(a + 0.1 * (j - k), b))
        k = j
    return out


def words(text: str, audio: np.ndarray, rate: int, path=None, listen: bool = True) -> list[list]:
    """[[word as shown, start, end]] for a narration line read in audio (seconds from its start). path: the
    line's audio file, next to which they're kept. listen=False: never the (English) speech recognizer, for
    narration in another language (the captions_listen setting a plugin can set)."""
    shown = _shown(text)
    if not shown or audio is None or not len(audio):
        return []
    use = heard() and listen
    cache = Path(f"{path}.words.json") if path else None
    key = [VERSION, text, len(audio), use]
    if cache and cache.exists():
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if saved.get("key") == key:
                return saved["words"]
        except (OSError, ValueError):
            pass
    out = None
    if use:
        try:
            out = _lined_up(shown, audio, rate)
        except Exception:  # (the recognizer couldn't load: spread them)
            out = None
    out = out or _by_pauses(shown, audio, rate)
    if cache:
        try:
            cache.write_text(json.dumps({"key": key, "words": out}), encoding="utf-8")
        except OSError:
            pass
    return out


# ---- pages of words, and drawing them ----------------------------------------------------------------

def pages(timed: list[list], style: dict, offset: float = 0.0) -> list[dict]:
    """A line's timed words in pages: a few words each, a new page after a sentence or a pause, or when the
    page would run too long. Times moved by offset. [{"start", "end", "words": [[word, start, end]]}]."""
    out, page = [], []
    for k, (w, s, e) in enumerate(timed):
        if page and (len(page) >= style["words"] or s - page[-1][2] > PAUSE or e - page[0][1] > style["span"]):
            out.append(page)
            page = []
        page.append([w, s + offset, e + offset])
        if re.search(r"[.!?;:…]['\"’”)]*$", w) or (w.endswith(",") and len(page) >= 2):
            out.append(page)
            page = []
    if page:
        out.append(page)
    return [{"start": p[0][1], "end": p[-1][2] + HOLD, "words": p} for p in out]


def track(pages_: list[dict]) -> list[dict]:
    """Pages in order, each shown until the next one starts (or a little after its last word)."""
    pages_ = sorted(pages_, key=lambda p: p["start"])
    for a, b in zip(pages_, pages_[1:]):  # (a short gap is bridged, so the captions don't flicker)
        a["end"] = b["start"] if b["start"] - a["words"][-1][2] < 0.8 else min(a["end"], b["start"])
    return pages_


def _ease(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return 1 - (1 - p) ** 3


class Captions:
    """Draws a chapter's captions over its frames: draw(frame, t)."""

    def __init__(self, pages_: list[dict], settings: dict, W: int, H: int):
        self.pages, self.W, self.H = pages_, W, H
        self.starts = [p["start"] for p in pages_]
        self.style = style_of(settings)
        self.color = COLORS.get(settings.get("captions_color", "yellow"), COLORS["yellow"])
        self.font = font_path(settings)
        self.size = max(12, round(self.style["size"] * min(W, H)))
        self._page, self._sprites, self._fonts = None, {}, {}

    def __getstate__(self):  # sent to drawing processes without what they draw for themselves
        return {**self.__dict__, "_page": None, "_sprites": {}, "_fonts": {}}

    def _f(self, size):
        if size not in self._fonts:
            self._fonts[size] = ImageFont.truetype(self.font, size)
        return self._fonts[size]

    def _grow(self, width):
        """How far a word this wide reaches out on each side when it's the one being said."""
        return (self.style["pop"] - 1) / 2 * width

    def _gaps(self, widths, size):
        """Room after each word but the last: a space, and room for the word being said to grow (or for the
        box behind it)."""
        space = self._f(size).getlength(" ") + (0.3 * size if self.style["box"] else 0)
        return [space + self._grow(max(a, b)) for a, b in zip(widths, widths[1:])]  # (one grows at a time)

    def _width(self, widths, size):
        return sum(widths) + sum(self._gaps(widths, size))

    def _layout(self, words, size):
        """Words in lines that fit the width (at most the style's lines, balanced): [[(word, width)]]."""
        f = self._f(size)
        widths = [f.getlength(w) for w in words]
        most = WIDTH * self.W
        if self._width(widths, size) <= most or len(words) == 1:
            return [list(zip(words, widths))]
        best = None
        for cut in range(1, len(words)):  # two lines, as even as they'll go
            a, b = self._width(widths[:cut], size), self._width(widths[cut:], size)
            if max(a, b) <= most and (best is None or max(a, b) < best[0]):
                best = (max(a, b), cut)
        if best is None:
            return None
        return [list(zip(words[:best[1]], widths[:best[1]])), list(zip(words[best[1]:], widths[best[1]:]))]

    def _sprite(self, page_i: int, active: int, pop: int, enter: int):
        """The page drawn with word `active` picked out: (its colours, their weight, the frame's weight, and x, y
        of where it goes)."""
        key = (active, pop, enter)
        if self._page != page_i:
            self._page, self._sprites = page_i, {}
        if key in self._sprites:
            return self._sprites[key]
        st = self.style
        words = [w for w, _, _ in self.pages[page_i]["words"]]
        if st["caps"]:
            words = [w.upper() for w in words]
        size = self.size
        lines = self._layout(words, size)
        while lines is None and size > 12:  # a long word: smaller letters
            size = int(size * 0.9)
            lines = self._layout(words, size)
        f = self._f(size)
        line_h = round(size * 1.18)
        stroke = max(1, round(st["outline"] * size)) if st["outline"] else 0
        pad = stroke + round(size * 0.35)
        padx = pad + round(self._grow(max(x for line in lines for _, x in line)))  # (a line's first or last word too)
        w = int(max(self._width([x for _, x in line], size) for line in lines)) + 2 * padx
        h = line_h * len(lines) + 2 * pad
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shade = Image.new("RGBA", (w, h), (0, 0, 0, 0)) if st["shadow"] else None
        d = ImageDraw.Draw(img)
        ds = ImageDraw.Draw(shade) if shade else None
        if st["panel"]:
            d.rounded_rectangle([0, 0, w - 1, h - 1], radius=size * 0.4, fill=(0, 0, 0, 150))
        grow = 1 + (st["pop"] - 1) * _ease((pop + 1) / STEPS) if st["pop"] > 1 else 1.0
        k = 0
        for li, line in enumerate(lines):
            gaps = self._gaps([x for _, x in line], size) + [0]
            x = (w - self._width([x for _, x in line], size)) / 2
            cy = pad + li * line_h + line_h / 2
            for (word, ww), gap in zip(line, gaps):
                on = k == active
                fill = (255, 255, 255) if st["box"] or not on else self.color
                wf = self._f(max(8, round(size * grow))) if on and grow > 1 else f
                cx = x + ww / 2
                if on and st["box"]:
                    bw, bh = ww + size * 0.5, size * 1.2
                    d.rounded_rectangle([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], radius=size * 0.25,
                                        fill=(*self.color, 255))
                if ds:
                    o = max(1, round(size * 0.05))
                    ds.text((cx + o, cy + o), word, font=wf, anchor="mm", fill=(0, 0, 0, 170),
                            stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
                d.text((cx, cy), word, font=wf, anchor="mm", fill=(*fill, 255), stroke_width=stroke,
                       stroke_fill=(0, 0, 0, 255))
                x += ww + gap
                k += 1
        if shade:
            img = Image.alpha_composite(shade.filter(ImageFilter.GaussianBlur(max(1, size * 0.06))), img)
        rgba = np.asarray(img)[:, :, [2, 1, 0, 3]].copy()  # (BGR, like the frames)
        if enter < STEPS - 1:  # the page coming in: from a little smaller, rising into place
            s = 0.85 + 0.15 * _ease((enter + 1) / STEPS)
            rgba = cv2.resize(rgba, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
        cy = self.H * st["y"][0 if self.H > self.W else 1]
        rise = (1 - _ease((enter + 1) / STEPS)) * 0.02 * self.H if enter < STEPS - 1 else 0
        x0 = round(self.W / 2 - rgba.shape[1] / 2)
        y0 = round(cy - rgba.shape[0] / 2 + rise)
        a = rgba[:, :, 3].astype(np.float32) / 255  # (the weights cv2.blendLinear takes, made once)
        self._sprites[key] = (np.ascontiguousarray(rgba[:, :, :3]), a, 1 - a, x0, y0)
        return self._sprites[key]

    def draw(self, frame: np.ndarray, t: float) -> np.ndarray:
        i = bisect.bisect_right(self.starts, t) - 1
        if i < 0 or t >= self.pages[i]["end"]:
            return frame
        page = self.pages[i]
        active = max(0, bisect.bisect_right([s for _, s, _ in page["words"]], t) - 1)
        since = t - page["words"][active][1]
        pop = min(STEPS - 1, int(since / POP * STEPS)) if self.style["pop"] > 1 else STEPS - 1
        enter = min(STEPS - 1, int((t - page["start"]) / ENTER * STEPS)) if self.style["enter"] else STEPS - 1
        rgb, a, na, x0, y0 = self._sprite(i, active, pop, enter)
        h, w = rgb.shape[:2]
        fx0, fy0, fx1, fy1 = max(0, x0), max(0, y0), min(self.W, x0 + w), min(self.H, y0 + h)
        if fx1 <= fx0 or fy1 <= fy0:
            return frame
        cut = (slice(fy0 - y0, fy1 - y0), slice(fx0 - x0, fx1 - x0))
        roi = frame[fy0:fy1, fx0:fx1]
        roi[:] = cv2.blendLinear(np.ascontiguousarray(roi), np.ascontiguousarray(rgb[cut]),
                                 np.ascontiguousarray(na[cut]), np.ascontiguousarray(a[cut]))
        return frame


def preview(settings: dict, W: int = 1080, H: int = 1920, background: np.ndarray | None = None) -> np.ndarray:
    """A picture of the captions on a plain background (or on background), for choosing a style."""
    frame = background.copy() if background is not None else np.full((H, W, 3), 40, np.uint8)
    if background is None:  # a soft gradient, like a dim panel
        frame[:] = np.linspace(70, 25, H, dtype=np.float32)[:, None, None].astype(np.uint8)
    H, W = frame.shape[:2]
    timed = [["He", 0.0, 0.2], ["didn't", 0.2, 0.5], ["even", 0.5, 0.7], ["see", 0.7, 0.9], ["it", 0.9, 1.0],
             ["coming.", 1.0, 1.4]]
    style = style_of(settings) or STYLES["classic"]
    caps = Captions(track(pages(timed, style)), {**settings, "captions": settings.get("captions") or "classic"}, W, H)
    return caps.draw(frame, 0.51 + POP)  # ("even" being said, its pop finished)
