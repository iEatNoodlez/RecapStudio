"""A YouTube title and thumbnail for the recap, once the script is written (Make the recap, when the project's
settings say so; or Suggest again in the Video tab). The script's writer reads the story as the narrator
summarised it and picks from numbered pictures of it: a clickbait title, a few others, the thumbnail's words and
its picture. The thumbnail is drawn here, 1280x720: the picture over a blurred, darkened copy of itself, and the
words in big outlined letters beside or under it. Pictures with black boxes (cover.py) aren't offered for a
YouTube-safe project, and the writer is told to pick one without nudity in any case.
"""
import base64
import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from . import cover, naming, panels, store

W, H = 1280, 720
CANDIDATES = 24      # pictures offered, spread over the story
POOL = 160           # looked at (their size) to find those
COLS, ROWS = 4, 3    # pictures per sheet
CELL_W, CELL_H = 300, 225
STORY_CHARS = 24000  # the summaries sent, at most
FONTS = [r"C:\Windows\Fonts\impact.ttf", r"C:\Windows\Fonts\ariblk.ttf", r"C:\Windows\Fonts\arialbd.ttf"]
WHITE, YELLOW = (255, 255, 255), (255, 210, 31)

SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "alternatives": {"type": "array", "items": {"type": "string"}},
        "thumbnail_text": {"type": "string"},
        "picture": {"type": "integer"},
    },
    "required": ["title", "alternatives", "thumbnail_text", "picture"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You write the YouTube title and thumbnail for a narrated recap video of a comic (a webtoon, manga \
or manhwa). You get the story as the narrator summarised it, chapter by chapter, its characters, and numbered \
pictures from it.

Answer with:
- title: a clickbait title in the style of popular manhwa and manga recap channels. It sells the premise or the \
biggest hook (a second life, a betrayal, a hidden power, an impossible situation) and leaves a question open, so \
people want to click. Under 90 characters, with the hook first (YouTube cuts long titles). One or two words in \
capitals for emphasis are fine; no hashtags, emojis or quotation marks. It must be true to the story: nothing \
that doesn't happen. Don't give away the ending.
- alternatives: three other titles in the same spirit, each from a different angle.
- thumbnail_text: the words on the thumbnail, 2 to 5, read at a glance: a hook that adds to the title rather \
than repeating it (for example: HE WAS NEVER WEAK, SHE KNOWS HIS SECRET).
- picture: the number of the picture for the thumbnail: the most striking one, best a character's face with \
strong emotion or a dramatic moment, clear when small. Not a picture of mostly text. YouTube doesn't allow \
sexual thumbnails: never a picture with nudity or sex."""

SAFE = """

This recap is an age-restricted video of an adult comic, kept within YouTube's rules: nothing sexual or \
suggestive in the title, the other titles or the thumbnail's words."""


def suggest(project: dict, ask=None) -> dict:
    """Ask the script's writer (ask(instructions, blocks) -> answer, for one that isn't asked here: Claude Code's
    TitleAsker). Returns {"title", "alternatives", "text", "panel", "candidates", "by"}."""
    s = project["settings"]
    cands = candidates(project)
    if not cands:
        raise RuntimeError("There's nothing narrated to choose a picture from: write the script first.")
    instructions = INSTRUCTIONS + (SAFE if s.get("youtube_safe") else "")
    blocks = _content(project, cands, _max_images(s))
    data = ask(instructions, blocks) if ask else naming._ask(s, instructions, blocks, SCHEMA, "recap_title")
    missing = problem(data)
    if missing:
        raise RuntimeError(f"{naming.writer_label(s)} answered without {missing}")
    shown = len(cands) if not _max_images(s) else min(len(cands), _max_images(s) * COLS * ROWS)
    k = int(data["picture"]) - 1
    title = _clean(data["title"])
    return {"title": title, "alternatives": [a for a in dict.fromkeys(_clean(a) for a in data["alternatives"])
                                             if a and a != title][:4],
            "text": _clean(data["thumbnail_text"])[:60], "panel": cands[k if 0 <= k < shown else 0]["id"],
            "candidates": [x["id"] for x in cands[:shown]], "by": naming.writer_label(s)}


def problem(data) -> str | None:
    """What a usable answer lacks (Claude Code's answer is checked with this as it comes in)."""
    if not isinstance(data, dict) or not _clean(data.get("title")):
        return '"title", a non-empty string'
    if not isinstance(data.get("alternatives"), list):
        return '"alternatives", a list of titles'
    if not _clean(data.get("thumbnail_text")):
        return '"thumbnail_text", a few words'
    if not isinstance(data.get("picture"), int):
        return '"picture", a picture\'s number'
    return None


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().strip('"“”').strip()[:100]


def _max_images(s):
    return naming._max_images(s)


# ---- what the writer is shown ------------------------------------------------------

def candidates(project: dict) -> list[dict]:
    """Narrated pieces spread over the story, big enough and not too thin for a thumbnail (for a YouTube-safe
    project, none with black boxes)."""
    d = store.project_dir(project["id"])
    safe = project["settings"].get("youtube_safe")
    scripted = {x["chapter"] for x in project["panels"] if (x.get("narration") or "").strip()}
    pool = [x for x in project["panels"] if x.get("enabled", True) and x["chapter"] in scripted
            and not (safe and cover.boxes(x))]
    good, there = [], []
    for x in naming._spread(pool, POOL):
        try:
            with Image.open(d / "panels" / x["file"]) as im:
                w, h = im.size
        except OSError:
            continue
        there.append(x)
        if w * h >= 300 * 300 and 0.4 <= w / h <= 2.6:
            good.append(x)
    return naming._spread(good if len(good) >= CANDIDATES // 2 else there, CANDIDATES)


def _story(project) -> str:
    """Each chapter's summary (or, without one, the start of its narration), spread out if there's a lot."""
    narration = {}
    for x in project["panels"]:
        if (x.get("narration") or "").strip() and x.get("enabled", True):
            narration.setdefault(x["chapter"], []).append(x["narration"].strip())
    parts = []
    for c in project["chapters"]:
        text = (c.get("summary") or "").strip() or " ".join(narration.get(c["id"], []))[:500]
        if text:
            parts.append(f"{c['name']}: {text}")
    while len(parts) > 2 and sum(len(p) for p in parts) > STORY_CHARS:
        parts = naming._spread(parts, len(parts) * 3 // 4)  # (the first and last chapters stay)
    return "\n\n".join(parts)


def _content(project, cands, max_images) -> list[dict]:
    named = [c for c in project["characters"] if (c.get("name") or "").strip()]
    cast = "\n".join(f"- {c['name']}" + (f": {c['description'].strip()}" if (c.get("description") or "").strip() else "")
                     for c in named)
    d = store.project_dir(project["id"])
    per = COLS * ROWS
    sheets = [cands[k:k + per] for k in range(0, len(cands), per)][:max_images or None]
    blocks = [{"type": "text", "text": f'The comic: "{project["name"]}".\n\n'
                                       + (f"Its characters:\n{cast}\n\n" if cast else "")
                                       + f"The story, as the narrator summarised it:\n\n{_story(project)}"}]
    for k, sheet in enumerate(sheets):
        first = k * per + 1
        blocks.append({"type": "text", "text": f"Pictures {first} to {first + len(sheet) - 1}:"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": _sheet(d, sheet, first)}})
    n = sum(len(s) for s in sheets)
    blocks.append({"type": "text", "text": f"Answer with the title, three other titles, the thumbnail's words and "
                                           f"the number of its picture (1 to {n})."})
    return blocks


def _sheet(d: Path, pieces, first: int) -> str:
    """The pictures in a grid, each numbered in its corner. A JPEG, base64."""
    sheet = np.full((ROWS * CELL_H, COLS * CELL_W, 3), 40, np.uint8)
    for i, x in enumerate(pieces):
        img = panels.imread(d / "panels" / x["file"])
        r, c = divmod(i, COLS)
        if img is not None:
            h, w = img.shape[:2]
            s = min((CELL_W - 8) / w, (CELL_H - 8) / h)
            img = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
            y, xx = r * CELL_H + (CELL_H - img.shape[0]) // 2, c * CELL_W + (CELL_W - img.shape[1]) // 2
            sheet[y:y + img.shape[0], xx:xx + img.shape[1]] = img
        label = str(first + i)
        cv2.rectangle(sheet, (c * CELL_W + 4, r * CELL_H + 4), (c * CELL_W + 16 + 22 * len(label), r * CELL_H + 40),
                      (255, 255, 255), -1)
        cv2.putText(sheet, label, (c * CELL_W + 10, r * CELL_H + 33), cv2.FONT_HERSHEY_DUPLEX, 1.0, (0, 0, 220), 2,
                    cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return base64.b64encode(buf.tobytes()).decode()


# ---- the thumbnail -----------------------------------------------------------------

def draw(project: dict, panel: dict, text: str) -> str:
    """Draw the thumbnail into the project's output folder; returns its path in the project."""
    d = store.project_dir(project["id"])
    img = panels.imread(d / "panels" / panel["file"])
    if img is None:
        raise RuntimeError("That picture couldn't be read")
    h, w = img.shape[:2]
    for left, top, right, bottom in cover.boxes(panel) if project["settings"].get("youtube_safe") else []:
        img[round(top * h):round(bottom * h), round(left * w):round(right * w)] = 0
    pic = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    canvas = ImageEnhance.Brightness(ImageOps.fit(pic, (W, H)).filter(ImageFilter.GaussianBlur(24))).enhance(0.45)
    m = 28
    wide = pic.width / pic.height > 1.25
    room = (W - 2 * m, round(H * 0.62)) if wide else (round(W * 0.56), H - 2 * m)
    s = min(room[0] / pic.width, room[1] / pic.height)
    front = ImageOps.expand(pic.resize((max(1, round(pic.width * s)), max(1, round(pic.height * s))),
                                       Image.LANCZOS), border=6, fill=WHITE)
    if wide:  # the picture at the top, the words under it
        canvas.paste(front, ((W - front.width) // 2, m))
        area = (m, m + front.height + 10, W - m, H - m)
    else:  # the picture on the right, the words beside it
        canvas.paste(front, (W - m - front.width, (H - front.height) // 2))
        area = (m, m, W - 2 * m - front.width, H - m)
    _words(ImageDraw.Draw(canvas), (text or "").upper().split(), area)
    out = d / "output" / "thumbnail.jpg"
    out.parent.mkdir(exist_ok=True)
    canvas.save(out, quality=92)
    return "output/thumbnail.jpg"


def _font(size):
    for f in FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size)


def _lines(draw, words, font, width) -> list[str]:
    lines = []
    for word in words:
        if lines and draw.textlength(f"{lines[-1]} {word}", font=font) <= width:
            lines[-1] += f" {word}"
        else:
            lines.append(word)
    return lines


def _words(draw, words, area):
    """The words as big as fit the area in at most three lines, centred, outlined; the last line yellow."""
    if not words:
        return
    x0, y0, x1, y1 = area
    width, height = x1 - x0, y1 - y0
    for size in range(170, 35, -6):
        font = _font(size)
        lines = _lines(draw, words, font, width)
        step = round(size * 1.05)
        if len(lines) <= 3 and step * len(lines) <= height and all(
                draw.textlength(ln, font=font) <= width for ln in lines):
            break
    y = y0 + (height - step * len(lines)) // 2
    for i, line in enumerate(lines):
        lw = draw.textlength(line, font=font)
        draw.text((x0 + (width - lw) / 2, y), line, font=font, fill=YELLOW if i == len(lines) - 1 and len(lines) > 1
                  else WHITE, stroke_width=max(3, size // 14), stroke_fill=(0, 0, 0))
        y += step
