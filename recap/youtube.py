"""A YouTube title and thumbnail for the recap, once the script is written (Make the recap, when the project's
settings say so; or Suggest again in the Video tab). The script's writer reads the story as the narrator
summarised it and picks from numbered pictures of it: a clickbait title, a few others, one or two pictures for
the thumbnail and what goes on them, the way manhwa recap channels make theirs: short yellow labels with arrows
on the characters (MC, ROOMMATE), a "?!" and a short speech bubble.

The thumbnail is drawn here, 1280x720. The pictures fill it (two side by side, or one), each cropped around the
characters it points at and away from the comic's own speech bubbles, with site watermarks painted out. A
character is found by name where the character scan named them in that picture, else by where the writer says
they are; the scan's head finder, when it's installed, puts the arrows on faces. When no label can be placed,
the hook words go across the bottom instead. Pictures with black boxes (cover.py) aren't offered for a
YouTube-safe project, nor pictures with swearing in their text, and the writer is told to pick ones without
nudity in any case.

Redraw with AI (Settings → Thumbnail redraw; Redraw with AI in the Video tab, or after every suggestion when the
project says so): the drawn thumbnail and its pictures go to an image model, Google's Gemini or one on
OpenRouter, on the user's own key. It repaints them as one glossy illustration with the same characters and
words, saved beside it as thumbnail_ai.jpg.
"""
import base64
import io
import logging
import math
import re
import time
from pathlib import Path

import cv2
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from . import charscan, cloud, cover, naming, ocr, panels, script, store

log = logging.getLogger(__name__)

W, H = 1280, 720
GUTTER = 10          # between two pictures
CANDIDATES = 24      # pictures offered, spread over the story
POOL = 400           # looked at (their size) to find those
COLS, ROWS = 4, 3    # pictures per sheet
CELL_W, CELL_H = 300, 225
STORY_CHARS = 24000  # the summaries sent, at most
MAX_UP = 1.7         # a picture is enlarged at most this much (more looks soft)
LABELS, LINE_CHARS = 4, 40
FONTS = [r"C:\Windows\Fonts\impact.ttf", r"C:\Windows\Fonts\ariblk.ttf", r"C:\Windows\Fonts\arialbd.ttf"]
LABEL_FONTS = [r"C:\Windows\Fonts\seguibli.ttf", r"C:\Windows\Fonts\seguibl.ttf", *FONTS[1:]]
BUBBLE_FONTS = [r"C:\Windows\Fonts\seguibl.ttf", *FONTS[1:]]
WHITE, YELLOW, BLACK = (255, 255, 255), (255, 222, 23), (0, 0, 0)
SWEARING = re.compile(r"fuck|shit|bitch|cunt|\bdick|\bass(hole)?\b|bastard|\bdamn|\bpiss|\bslut|\bwhore", re.I)
# a site's name stamped on the art (TOOMICS.com, read as "TOMICS.CHM"), a web address, a scan group's tag
WATERMARK = re.compile(r"t[o0]{1,2}m[i1lr]c[s5]|\.(c[o0h]m|net|org)\b|\bwww\.|webt[o0]{2}n|lezhin|tapas|\bscans?\b",
                       re.I)
KINDS = ("label", "reaction", "line")

MARK = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(KINDS)},
        "picture": {"type": "integer"},
        "who": {"type": "string"},
        "where": {"type": "string", "enum": ["left", "middle", "right", ""]},
        "text": {"type": "string"},
    },
    "required": ["kind", "picture", "who", "where", "text"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "alternatives": {"type": "array", "items": {"type": "string"}},
        "pictures": {"type": "array", "items": {"type": "integer"}},
        "marks": {"type": "array", "items": MARK},
        "thumbnail_text": {"type": "string"},
    },
    "required": ["title", "alternatives", "pictures", "marks", "thumbnail_text"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You write the YouTube title and thumbnail for a narrated recap video of a comic (a webtoon, manga \
or manhwa). You get the story as the narrator summarised it, chapter by chapter, its characters, and numbered \
pictures from it, with the characters the app found in each.

Answer with:
- title: a clickbait title in the style of popular manhwa and manga recap channels. It sells the premise or the \
biggest hook (a second life, a betrayal, a hidden power, an impossible situation) and leaves a question open, so \
people want to click. Often two beats joined by an ellipsis, the setup and then the twist ("They Laughed at His \
BROKEN Maid… Until She Solo'd An Entire SSS-Rank GUILD!", "I Hid My Billionaire Sisters to Live Like a Broke Kid… \
Then a Poor Girl Fell for Me!"); it may be told by the main character (I, me). One or two words in capitals for \
emphasis; no hashtags, emojis or quotation marks. End it with " | Manhwa Recap" (or Manga, Manhua or Webtoon \
Recap, whichever this comic is) when the whole title stays under 100 characters. The hook comes first: YouTube \
cuts long titles in its lists. It must be true to the story: nothing that doesn't happen. Don't give away the \
ending.
- alternatives: three other titles in the same spirit, each from a different angle.
- pictures: the numbers of the thumbnail's pictures. Two, side by side (each fills half the thumbnail, cropped \
around its characters: best for tall webtoon panels), or one wide picture that fills it. Pick the most striking: \
characters' faces with strong emotion, a dramatic moment, the story's main relationships; clear when small, with \
little text. YouTube doesn't allow sexual thumbnails: never a picture with nudity or sex.
- marks: what's drawn on the pictures, as the big recap channels do:
  - "label": a 1 to 3 word tag in yellow with an arrow to a character, naming their role rather than their name \
(MC, ROOMMATE, ICE PRINCESS, BILLIONAIRE SISTERS, HIS BOSS). Two to four labels in all; the main character's is \
"MC".
  - "reaction": at most one "?!" (or "?!?" or "!!") beside a shocked or flustered character.
  - "line": at most one speech bubble of 1 to 4 words said by a character (I like you~, mine., YOU'LL BE MINE!), \
true to the story.
  Each has: kind; picture (one of the pictures you chose); who: the character's name exactly as listed for that \
picture, or for someone not listed there, a few words saying who it is; where: left, middle or right in that \
picture (needed for someone not listed; may be empty for a listed character); and text.
- thumbnail_text: 2 to 5 words read at a glance (HE WAS NEVER WEAK, SHE KNOWS HIS SECRET), shown across the \
thumbnail instead when the labels can't be placed."""

SAFE = """

This recap is an age-restricted video of an adult comic, kept within YouTube's rules: nothing sexual or \
suggestive in the title, the other titles or the thumbnail's words."""


EXTRA: list = []  # plugins.Plugin.title_instructions: fn(settings) -> more instructions for the title


def suggest(project: dict, ask=None) -> dict:
    """Ask the script's writer (ask(instructions, blocks) -> answer, for one that isn't asked here: Claude Code's
    TitleAsker). Returns {"title", "alternatives", "text", "pictures", "panel" (the first), "marks",
    "candidates", "by"}; a mark is {"kind", "picture" (a piece's id), "who", "where", "text"}."""
    s = project["settings"]
    cands = candidates(project)
    if not cands:
        raise RuntimeError("There's nothing narrated to choose a picture from: write the script first.")
    max_images = _max_images(s)
    shown = cands if not max_images else cands[:max_images * COLS * ROWS]
    instructions = "\n\n".join([INSTRUCTIONS + (SAFE if s.get("youtube_safe") else ""),
                                *script.extra_instructions(EXTRA, s)])  # plugins' (the language, say)
    data = ask(instructions, _content(project, shown)) if ask else naming._ask(
        s, instructions, _content(project, shown), SCHEMA, "recap_title")
    missing = problem(data)
    if missing:
        raise RuntimeError(f"{naming.writer_label(s)} answered without {missing}")
    number = {k + 1: x["id"] for k, x in enumerate(shown)}
    pictures = [number[k] for k in dict.fromkeys(data["pictures"]) if k in number][:2] or [shown[0]["id"]]
    title = _title(data["title"])
    return {"title": title, "alternatives": [a for a in dict.fromkeys(_title(a) for a in data["alternatives"])
                                             if a and a != title][:4],
            "text": _clean(data["thumbnail_text"])[:60], "pictures": pictures, "panel": pictures[0],
            "marks": tidy_marks([{**m, "picture": number.get(m.get("picture"))} for m in data["marks"]], pictures),
            "candidates": [x["id"] for x in shown], "by": naming.writer_label(s)}


def problem(data) -> str | None:
    """What a usable answer lacks (Claude Code's answer is checked with this as it comes in)."""
    if not isinstance(data, dict) or not _clean(data.get("title")):
        return '"title", a non-empty string'
    if not isinstance(data.get("alternatives"), list):
        return '"alternatives", a list of titles'
    pics = data.get("pictures")
    if not isinstance(pics, list) or not pics or not all(isinstance(k, int) for k in pics):
        return '"pictures", a list of one or two picture numbers'
    marks = data.get("marks")
    if not isinstance(marks, list) or not all(isinstance(m, dict) and m.get("kind") in KINDS and _clean(m.get("text"))
                                              for m in marks):
        return '"marks", a list of labels, a reaction and a line, each with kind, picture, who, where and text'
    if not _clean(data.get("thumbnail_text")):
        return '"thumbnail_text", a few words'
    return None


def tidy_marks(marks, pictures) -> list[dict]:
    """Marks on the chosen pictures (one with no picture of its own goes on the only one), within the limits."""
    out, count = [], {k: 0 for k in KINDS}
    for m in marks:
        kind, text = m.get("kind"), _clean(m.get("text"))
        pic = m.get("picture") if m.get("picture") in pictures else pictures[0] if len(pictures) == 1 else None
        if kind not in KINDS or not text or not pic or count[kind] >= (LABELS if kind == "label" else 1):
            continue
        count[kind] += 1
        out.append({"kind": kind, "picture": pic, "who": _clean(m.get("who"))[:60],
                    "where": m.get("where") if m.get("where") in ("left", "middle", "right") else "",
                    "text": text.upper()[:24] if kind == "label" else text[:6] if kind == "reaction" else
                    text[:LINE_CHARS]})
    return out


def _text(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().strip('"“”').strip()


def _clean(text) -> str:
    return _text(text)[:100]


def _title(text) -> str:
    """A title within YouTube's 100 characters, never ending in a cut word or a cut "| Manga Recap": a longer
    ending (the Spanish "| Resumen de manga") can take it past 100, and a plain cut left "…| Resumen de m"."""
    return fit_title(_text(text))


def fit_title(title: str, room: int = 100) -> str:
    """The title cut to fit in room characters: the "| … Recap" ending left out if it's what doesn't fit, else cut
    after a whole word."""
    if len(title) <= room:
        return title
    bar = title.rfind("|")
    if 0 < bar <= room and len(title) - bar <= 30:  # (the ending, not a long second half)
        return title[:bar].rstrip(" -–—:|")
    space = title.rfind(" ", 0, room + 1)
    return title[:space if space > 0 else room].rstrip(" ,;:-–—|")


def _max_images(s):
    return naming._max_images(s)


# ---- what the writer is shown ------------------------------------------------------

def _scan_people(project) -> dict:
    """{piece id: [(character or None, box)]}: who the character scan found in each piece, where its group was
    added as a character (else None)."""
    chars = {c["id"]: c for c in project["characters"]}
    out = {}
    for g in (project.get("scan") or {}).get("groups", []):
        c = chars.get(g.get("character")) if g.get("status") == "added" else None
        for piece, *box in g.get("members", []):
            out.setdefault(piece, []).append((c, box))
    return out


def _names(c) -> list[str]:
    return [n for n in [(c.get("name") or "").strip(), *(a.strip() for a in (c.get("aliases") or "").split(","))] if n]


def candidates(project: dict) -> list[dict]:
    """Narrated pieces spread over the story, big enough and not too thin for a thumbnail, those with named
    characters first (for a YouTube-safe project, none with black boxes; never one with swearing in its text)."""
    d = store.project_dir(project["id"])
    safe = project["settings"].get("youtube_safe")
    scripted = {x["chapter"] for x in project["panels"] if (x.get("narration") or "").strip()}
    pool = [x for x in project["panels"] if x.get("enabled", True) and x["chapter"] in scripted
            and not (safe and cover.boxes(x)) and not SWEARING.search(x.get("text") or "")]
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
    if len(good) < CANDIDATES // 2:
        return naming._spread(there, CANDIDATES)
    people = _scan_people(project)
    named = [x for x in good if any(c for c, _ in people.get(x["id"], []))]
    if len(named) >= CANDIDATES:
        return naming._spread(named, CANDIDATES)
    order = {x["id"]: k for k, x in enumerate(project["panels"])}
    rest = naming._spread([x for x in good if x not in named], CANDIDATES - len(named))
    return sorted(named + rest, key=lambda x: order[x["id"]])


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


def _who_where(project, shown) -> list[str]:
    """For each picture, the named characters the scan found in it and whereabouts: "Picture 3: Noah (middle)"."""
    d = store.project_dir(project["id"])
    people = _scan_people(project)
    out = []
    for k, x in enumerate(shown):
        found = {}
        for c, box in people.get(x["id"], []):
            if c and _names(c) and _names(c)[0] not in found:
                try:
                    with Image.open(d / "panels" / x["file"]) as im:
                        w = im.width
                except OSError:
                    break
                third = min(2, int(3 * (box[0] + box[2]) / 2 / max(1, w)))
                found[_names(c)[0]] = ("left", "middle", "right")[third]
        if found:
            out.append(f"Picture {k + 1}: " + ", ".join(f"{n} ({where})" for n, where in found.items()))
    return out


def _content(project, shown) -> list[dict]:
    named = [c for c in project["characters"] if (c.get("name") or "").strip()]
    cast = "\n".join(f"- {c['name']}" + (f": {c['description'].strip()}" if (c.get("description") or "").strip() else "")
                     for c in named)
    d = store.project_dir(project["id"])
    per = COLS * ROWS
    sheets = [shown[k:k + per] for k in range(0, len(shown), per)]
    blocks = [{"type": "text", "text": f'The comic: "{project["name"]}".\n\n'
                                       + (f"Its characters:\n{cast}\n\n" if cast else "")
                                       + f"The story, as the narrator summarised it:\n\n{_story(project)}"}]
    for k, sheet in enumerate(sheets):
        first = k * per + 1
        blocks.append({"type": "text", "text": f"Pictures {first} to {first + len(sheet) - 1}:"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": _sheet(d, sheet, first)}})
    listed = _who_where(project, shown)
    blocks.append({"type": "text", "text": (
        "Who the app found in the pictures (named by its character scan; left, middle or right in the picture):\n"
        + "\n".join(listed) if listed else
        "The app doesn't know who is in each picture: give where in the picture each character you mark is.")})
    blocks.append({"type": "text", "text": f"Answer with the title, three other titles, the numbers of the "
                                           f"thumbnail's pictures (1 to {len(shown)}), its marks and its words."})
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


# ---- finding the characters in a picture ---------------------------------------------

_head_finder = None  # the character scan's head detector, loaded the first time it's needed (False: it wouldn't)


def _heads(img) -> list[list[float]]:
    """Head boxes in a picture, when the character scan's models are installed."""
    global _head_finder
    if not charscan.installed() or _head_finder is False:
        return []
    try:
        if _head_finder is None:
            import onnxruntime as ort
            _head_finder = charscan._Detector(ort.InferenceSession(str(charscan.ADDON_DIR / "head.onnx"),
                                                                   providers=["CPUExecutionProvider"]),
                                              charscan.HEAD_CONF, 0.7)
        found = _head_finder(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    except Exception as e:  # a model that won't load: the arrows aim at where heads usually are instead
        log.warning("Couldn't look for heads: %s", e)
        if _head_finder is None:
            _head_finder = False  # (not tried again until the app restarts)
        return []
    return [[float(v) for v in f[:4]] for f in found if min(f[2] - f[0], f[3] - f[1]) >= 24]


def _people(img, members) -> list[dict]:
    """Who is in a picture: [{"names": [...] (none when unnamed), "head": box}], from the scan's boxes for it and
    the heads found in it. A scan box without a head found in it gets one where heads usually are; a head found
    is kept inside its scan box (in a close-up the head finder's box can take in the whole panel)."""
    heads = _heads(img)
    out, used = [], set()
    for c, box in members:
        inside = [i for i, hd in enumerate(heads)
                  if box[0] <= (hd[0] + hd[2]) / 2 <= box[2] and box[1] <= (hd[1] + hd[3]) / 2 <= box[3]]
        if inside:
            i = max(inside, key=lambda i: _area(heads[i]))
            used.add(i)
            out.append({"names": _names(c) if c else [], "head": _clip(heads[i], box), "found": True})
        else:
            bw, bh = box[2] - box[0], box[3] - box[1]
            side, cx = min(bw * 0.55, bh * 0.4), (box[0] + box[2]) / 2
            out.append({"names": _names(c) if c else [], "found": False,
                        "head": [cx - side / 2, box[1] + side * 0.1, cx + side / 2, box[1] + side * 1.1]})
    out += [{"names": [], "head": hd, "found": True} for i, hd in enumerate(heads) if i not in used]
    best = {}  # one each: someone found twice keeps their head that was found, the bigger one
    for k, p in enumerate(out):
        key = p["names"][0].lower() if p["names"] else k
        if key not in best or (p["found"], _area(p["head"])) > (best[key]["found"], _area(best[key]["head"])):
            best[key] = p
    return list(best.values())


def _target(found, m, w) -> dict | None:
    """The person a mark points at: by name, else the unnamed one where the writer says."""
    who = m.get("who", "").strip().lower()
    for p in found:
        for n in (n.lower() for n in p["names"]):
            if who and (who == n or who.startswith(n + " ") or n.startswith(who + " ")):
                return p
    unnamed = [p for p in found if not p["names"]]
    spot = {"left": w / 6, "middle": w / 2, "right": w * 5 / 6}.get(m.get("where"))
    if spot is None:
        return unnamed[0] if len(unnamed) == 1 else None
    near = [p for p in unnamed if abs((p["head"][0] + p["head"][2]) / 2 - spot) < w / 3]
    return min(near, key=lambda p: abs((p["head"][0] + p["head"][2]) / 2 - spot)) if near else None


def _text_lines(img, s) -> list[tuple]:
    try:
        return ocr.read_lines(img, 0.5, s.get("use_gpu", True), ocr.use_english(s))
    except Exception as e:  # text reading not installed: no bubbles to keep out of the crop
        log.warning("Couldn't read the picture's text: %s", e)
        return []


# ---- the thumbnail -----------------------------------------------------------------

def draw(project: dict, pictures: list[dict], marks: list[dict], text: str) -> dict:
    """Draw the thumbnail into the project's output folder: {"file" (its path in the project), "placed" (how
    many marks found their character)}."""
    d = store.project_dir(project["id"])
    s = project["settings"]
    people = _scan_people(project)
    half = (W - GUTTER) // 2
    slots = [(0, 0, W, H)] if len(pictures) == 1 else [(0, 0, half, H), (W - half, 0, W, H)]
    canvas = Image.new("RGB", (W, H), BLACK)
    heads, avoid, targets = [], [], []  # in the thumbnail: every head, the comic's text left in, (mark, head, slot)
    for pic, slot in zip(pictures, slots):
        img = panels.imread(d / "panels" / pic["file"])
        if img is None:
            raise RuntimeError("That picture couldn't be read")
        h, w = img.shape[:2]
        for left, top, right, bottom in cover.boxes(pic) if s.get("youtube_safe") else []:
            img[round(top * h):round(bottom * h), round(left * w):round(right * w)] = 0
        found = _people(img, people.get(pic["id"], []))
        hits = [(m, _target(found, m, w)) for m in marks if m["picture"] == pic["id"]]
        lines = _text_lines(img, s)
        stamps = [ln for ln in lines if WATERMARK.search(ln[4])]
        bubbles = [_pad(_bound(g), 0.6 * float(np.median([ln[3] - ln[1] for ln in g])))
                   for g in ocr.groups([ln for ln in lines if ln not in stamps])]
        aimed = [p["head"] for _, p in hits if p]
        sw, sh = slot[2] - slot[0], slot[3] - slot[1]
        x0, y0, x1, y1 = _window(w, h, sw / sh, sw, _union(aimed or [p["head"] for p in found]), bubbles)
        if stamps:
            img = _paint_out(img, stamps)
        pic_img = Image.fromarray(cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)).resize((sw, sh), Image.LANCZOS)
        pic_img = ImageEnhance.Contrast(ImageEnhance.Color(pic_img).enhance(1.2)).enhance(1.06)
        canvas.paste(pic_img, slot[:2])
        k = sw / (x1 - x0)

        def place(b):
            return [slot[0] + (b[0] - x0) * k, slot[1] + (b[1] - y0) * k, slot[0] + (b[2] - x0) * k,
                    slot[1] + (b[3] - y0) * k]
        window = (x0, y0, x1, y1)
        heads += [place(b) for b in (_shown(p["head"], window, 0.5) for p in found) if b]
        avoid += [place(b) for b in bubbles if _overlap(b, window)]
        shown = [(m, p and _shown(p["head"], window, 0.4)) for m, p in hits]
        targets += [(m, place(b) if b else None, slot) for m, b in shown]
    dr = ImageDraw.Draw(canvas)
    if len(slots) == 2:
        dr.rectangle((half, 0, W - half - 1, H), fill=WHITE)
        dr.line((half, 0, half, H), fill=BLACK, width=3)
        dr.line((W - half - 1, 0, W - half - 1, H), fill=BLACK, width=3)
    placed = _marks(canvas, targets, heads, avoid)
    if not any(m["kind"] == "label" for m in placed):
        _caption(canvas, text)
    out = d / "output" / "thumbnail.jpg"
    out.parent.mkdir(exist_ok=True)
    canvas.save(out, quality=92)
    return {"file": "output/thumbnail.jpg", "placed": len(placed)}


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1]) if b else 0.0


def _overlap(a, b) -> float:
    if not a or not b:
        return 0.0
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _clip(a, b):
    """The part of box a inside box b."""
    return [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]


def _shown(head, window, share):
    """The part of a head the crop shows, when enough of it does: that share of the head, or a quarter of the
    crop (a face too big for it, in a close-up); else None."""
    seen = _overlap(head, window)
    return _clip(head, window) if seen > share * _area(head) or seen > 0.25 * _area(window) else None


def _union(boxes):
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _bound(lines):
    return [min(ln[0] for ln in lines), min(ln[1] for ln in lines), max(ln[2] for ln in lines), max(ln[3] for ln in lines)]


def _pad(b, p):
    return [b[0] - p, b[1] - p, b[2] + p, b[3] + p]


def _window(w, h, aspect, slot_w, focus, avoid) -> tuple[int, int, int, int]:
    """The part of a w x h picture to show in a slot of this aspect: big enough around the focus (the heads
    pointed at) for their bodies to show, enlarged no more than MAX_UP, with the focus a little above the
    middle, and as little of the comic's text as it can."""
    full = h * aspect if w / h > aspect else w
    if focus:
        fw, fh = focus[2] - focus[0], focus[3] - focus[1]
        ww = min(full, max(fw * 1.6, fh * 2.6 * aspect, slot_w / MAX_UP))
        fx, fy = (focus[0] + focus[2]) / 2, (focus[1] + focus[3]) / 2
    else:
        ww, fx, fy = full, w / 2, h * 0.42
    hh = ww / aspect
    best = None
    for i in range(13):
        x = (w - ww) * i / 12
        for j in range(25):
            y = (h - hh) * j / 24
            win = (x, y, x + ww, y + hh)
            cut = 1 - _overlap(focus, win) / max(1.0, _area(focus)) if focus else 0
            text = sum(_overlap(b, win) for b in avoid) / (ww * hh)
            off = abs(x + ww / 2 - fx) / ww + abs(y + hh * 0.42 - fy) / hh
            score = 6 * cut + 3 * text + off
            if best is None or score < best[0]:
                best = (score, x, y)
    _, x, y = best
    return round(x), round(y), round(min(w, x + ww)), round(min(h, y + hh))


def _paint_out(img, lines):
    """A site's watermark stamped on the art, filled in from around it."""
    mask = np.zeros(img.shape[:2], np.uint8)
    for x0, y0, x1, y1, _ in lines:
        p = max(3, round(0.25 * (y1 - y0)))
        cv2.rectangle(mask, (int(x0) - p, int(y0) - p), (int(x1) + p, int(y1) + p), 255, -1)
    return cv2.inpaint(img, mask, 7, cv2.INPAINT_TELEA)


def _font(size, fonts=FONTS):
    for f in fonts:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size)


def _tile(text, font, fill, stroke, angle=0.0) -> Image.Image:
    """Outlined words on a transparent tile, turned a little."""
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    l, t, r, b = probe.multiline_textbbox((0, 0), text, font=font, stroke_width=stroke, align="center")
    tile = Image.new("RGBA", (r - l + 16, b - t + 16))
    ImageDraw.Draw(tile).multiline_text((8 - l, 8 - t), text, font=font, fill=fill, stroke_width=stroke,
                                        stroke_fill=BLACK, align="center")
    return tile.rotate(angle, expand=True, resample=Image.BICUBIC) if angle else tile


def _paste_shadowed(canvas, tile, x, y):
    shade = Image.new("RGBA", tile.size, (0, 0, 0, 0))
    shade.putalpha(tile.getchannel("A").point(lambda a: int(a * 0.6)))
    shade = shade.filter(ImageFilter.GaussianBlur(5))
    canvas.paste(shade, (round(x) + 5, round(y) + 6), shade)
    canvas.paste(tile, (round(x), round(y)), tile)


def _spot(head, size, slot, heads, taken, avoid, sides, room=24.0):
    """Where a mark of this size goes beside a head, at least room away (a label leaves room for its arrow): the
    first of the sides tried (below, left, above-right ...) that keeps off every face, the other marks and the
    comic's text, inside the picture."""
    hx, hy = (head[0] + head[2]) / 2, (head[1] + head[3]) / 2
    hw, hh = head[2] - head[0], head[3] - head[1]
    lw, lh = size
    best = None
    for far in (1.0, 2.2):
        gap = max(room, 0.3 * max(hw, hh)) * far
        centers = {
            "below": (hx, hy + hh / 2 + gap + lh / 2),
            "below-left": (hx - hw / 2 - lw / 2 + hw * 0.25, hy + hh / 2 + gap + lh / 2),
            "below-right": (hx + hw / 2 + lw / 2 - hw * 0.25, hy + hh / 2 + gap + lh / 2),
            "left": (hx - hw / 2 - gap - lw / 2, hy),
            "right": (hx + hw / 2 + gap + lw / 2, hy),
            "above-left": (hx - hw / 2 - lw / 2 + hw * 0.25, hy - hh / 2 - gap - lh / 2),
            "above-right": (hx + hw / 2 + lw / 2 - hw * 0.25, hy - hh / 2 - gap - lh / 2),
            "above": (hx, hy - hh / 2 - gap - lh / 2),
        }
        for k, side in enumerate(sides):
            cx, cy = centers[side]
            x = min(max(cx - lw / 2, slot[0] + 8), slot[2] - 8 - lw)
            y = min(max(cy - lh / 2, slot[1] + 8), slot[3] - 8 - lh)
            box = (x, y, x + lw, y + lh)
            a = lw * lh
            score = (10 * sum(_overlap(box, b) for b in heads) / a + 10 * sum(_overlap(box, b) for b in taken) / a
                     + 2 * sum(_overlap(box, b) for b in avoid) / a + 0.12 * k + 0.4 * (far > 1)
                     + 0.5 * (abs(x - (cx - lw / 2)) + abs(y - (cy - lh / 2))) / max(lw, lh))
            if best is None or score < best[0]:
                best = (score, box)
    return best[1]


def _edge_point(box, toward):
    """Where a line from the box's centre toward a point leaves the box."""
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    dx, dy = toward[0] - cx, toward[1] - cy
    if not dx and not dy:
        return cx, cy
    t = min((box[2] - box[0]) / 2 / abs(dx) if dx else math.inf, (box[3] - box[1]) / 2 / abs(dy) if dy else math.inf)
    return cx + dx * min(t, 1), cy + dy * min(t, 1)


def _arrow(dr, start, end, width):
    """A yellow arrow outlined in black, from start to its point at end."""
    (x0, y0), (x1, y1) = start, end
    ang = math.atan2(y1 - y0, x1 - x0)
    head = width * 2.6
    bx, by = x1 - head * math.cos(ang), y1 - head * math.sin(ang)
    px, py = -math.sin(ang), math.cos(ang)
    for color, grow in ((BLACK, 6), (YELLOW, 0)):
        tip = (x1 + grow * 1.9 * math.cos(ang), y1 + grow * 1.9 * math.sin(ang))
        wing = head * 0.62 + grow * 1.9
        back = (bx - grow * math.cos(ang), by - grow * math.sin(ang))
        dr.line((x0, y0, bx, by), fill=color, width=round(width + 2 * grow))
        r = width / 2 + grow
        dr.ellipse((x0 - r, y0 - r, x0 + r, y0 + r), fill=color)
        dr.polygon([tip, (back[0] + px * wing, back[1] + py * wing), (back[0] - px * wing, back[1] - py * wing)],
                   fill=color)


def _marks(canvas, targets, heads, avoid) -> list[dict]:
    """Draw the labels (with arrows), then the speech bubble, then the reaction; returns those placed."""
    dr = ImageDraw.Draw(canvas)
    taken, placed = [], []
    order = {"label": 0, "line": 1, "reaction": 2}
    labels = 0
    for m, head, slot in sorted(targets, key=lambda t: order[t[0]["kind"]]):
        if head is None:
            continue
        hw, hh = head[2] - head[0], head[3] - head[1]
        if m["kind"] == "label":
            text = m["text"]
            size = 92 if len(text) <= 3 else 76 if len(text) <= 7 else 62 if len(text) <= 12 else 50
            while True:
                font = _font(size, LABEL_FONTS)
                tile = _tile(text, font, YELLOW, max(4, size // 9), angle=(4 if labels % 2 else -4))
                if tile.width <= 0.9 * (slot[2] - slot[0]) or size <= 30:
                    break
                size -= 6
            box = _spot(head, tile.size, slot, heads, taken, avoid,
                        ["below", "below-left", "below-right", "left", "right", "above-left", "above-right", "above"],
                        room=70.0)
            _paste_shadowed(canvas, tile, box[0], box[1])
            aim = ((head[0] + head[2]) / 2, (head[1] + head[3]) / 2)
            start = _edge_point(box, aim)
            dist = math.hypot(aim[0] - start[0], aim[1] - start[1])
            stop = min(0.45 * min(hw, hh), dist - 10)
            if dist - stop > 28:
                end = (aim[0] - (aim[0] - start[0]) * stop / dist, aim[1] - (aim[1] - start[1]) * stop / dist)
                gap = 6 / dist
                _arrow(dr, (start[0] + (aim[0] - start[0]) * gap, start[1] + (aim[1] - start[1]) * gap), end,
                       max(10, size // 6))
            labels += 1
        elif m["kind"] == "reaction":
            tile = _tile(m["text"], _font(112, LABEL_FONTS), YELLOW, 12, angle=-10)
            box = _spot(head, tile.size, slot, heads, taken, avoid,
                        ["above-right", "right", "above-left", "left", "above"])
            _paste_shadowed(canvas, tile, box[0], box[1])
        else:
            box = _bubble(canvas, dr, m["text"], head, slot, heads, taken, avoid)
        taken.append(box)
        placed.append(m)
    return placed


def _bubble_text(dr, text, width) -> tuple:
    """The bubble's font and its words in lines this wide: the biggest size that fits them in two lines, else
    as many lines as they take at the smallest. Every word is kept."""
    for size in (40, 36, 32, 28):
        font = _font(size, BUBBLE_FONTS)
        lines = _lines(dr, text.split(), font, width)
        if len(lines) <= 2:
            break
    return font, "\n".join(lines)


def _bubble(canvas, dr, text, head, slot, heads, taken, avoid):
    """A white speech bubble with its tail toward the head."""
    font, body = _bubble_text(dr, text, 0.4 * (slot[2] - slot[0]))
    l, t, r, b = dr.multiline_textbbox((0, 0), body, font=font, align="center")
    pw, ph = 30, 22
    size = (r - l + 2 * pw, b - t + 2 * ph)
    box = _spot(head, (size[0] + 16, size[1] + 40), slot, heads, taken, avoid,
                ["above-left", "above-right", "above", "left", "right"])
    x, y = box[0] + 8, box[1] + 8
    ell = (x, y, x + size[0], y + size[1])
    hx, hy = (head[0] + head[2]) / 2, head[1] + 0.3 * (head[3] - head[1])
    cx, cy = (ell[0] + ell[2]) / 2, (ell[1] + ell[3]) / 2
    ang = math.atan2(hy - cy, hx - cx)
    reach = min(60.0, max(20.0, math.hypot(hx - cx, hy - cy) - size[1] / 2 - 0.4 * (head[3] - head[1])))
    edge = (cx + math.cos(ang) * size[0] / 2 * 0.8, cy + math.sin(ang) * size[1] / 2 * 0.8)
    tip = (edge[0] + math.cos(ang) * reach, edge[1] + math.sin(ang) * reach)
    px, py = -math.sin(ang) * 16, math.cos(ang) * 16
    tail = [(edge[0] + px, edge[1] + py), tip, (edge[0] - px, edge[1] - py)]
    dr.ellipse((ell[0] - 5, ell[1] - 5, ell[2] + 5, ell[3] + 5), fill=BLACK)
    dr.polygon([(tail[0][0] + px * 0.4, tail[0][1] + py * 0.4), (tip[0] + math.cos(ang) * 7, tip[1] + math.sin(ang) * 7),
                (tail[2][0] - px * 0.4, tail[2][1] - py * 0.4)], fill=BLACK)
    dr.ellipse(ell, fill=WHITE)
    dr.polygon(tail, fill=WHITE)
    dr.multiline_text((x + pw - l, y + ph - t), body, font=font, fill=BLACK, align="center")
    return (ell[0] - 5, ell[1] - 5, ell[2] + 5, max(ell[3] + 5, tip[1]))


def _caption(canvas, text):
    """The hook words big across the bottom, over a dark fade (when no label could be placed)."""
    words = (text or "").upper().split()
    if not words:
        return
    fade = Image.new("L", (1, H))
    for y in range(H):
        fade.putpixel((0, y), int(200 * max(0.0, (y - H * 0.45) / (H * 0.55))))
    canvas.paste(Image.new("RGB", (W, H), BLACK), (0, 0), fade.resize((W, H)))
    _words(ImageDraw.Draw(canvas), words, (40, round(H * 0.55), W - 40, H - 28))


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


# ---- Redraw with AI ------------------------------------------------------------------

REDRAW = {  # the services that can paint it, on the same keys as the script writers' (cloud.py)
    "gemini": {"name": "Google Gemini", "default_model": "gemini-nano-banana-2.1",
               "url": "https://generativelanguage.googleapis.com/v1beta/interactions"},
    "openrouter": {"name": "OpenRouter", "default_model": "", "url": "https://openrouter.ai/api/v1/images"},
}
REDRAW_TIMEOUT = (30, 300)
REDRAW_PROMPT = """Redraw this YouTube thumbnail (the first image) as one polished, high-detail anime \
illustration with vivid colours, clean line art and soft glossy lighting, like the thumbnails of the most popular \
manhwa recap channels. It's for a recap of the comic "{series}"; the other images are the comic's own pictures it \
was made from.

- Keep the layout: the same characters in the same places, with the same faces, hair, eyes, clothes, expressions \
and poses, so they're clearly the comic's characters.{split}
- Keep every word added on top of it, exactly as written, as bold, clean thumbnail lettering: {words}.
- Remove all the other text: the comic's own speech bubbles and captions, sound effects, watermarks and logos.
- Wide 16:9, sharp and easy to read when small.
- It must be allowed as a YouTube thumbnail: no nudity and nothing sexual."""


def redraw_setup(settings) -> tuple[str, str]:
    """(service, model) for Redraw with AI; raises RuntimeError saying what's missing."""
    provider = settings.get("redraw_provider") or ""
    if provider not in REDRAW:
        raise RuntimeError("Choose a service for Redraw with AI in Settings → Thumbnail redraw")
    if not cloud.has_key(provider):
        raise RuntimeError(f"Add your {REDRAW[provider]['name']} key in Settings → Thumbnail redraw")
    model = (settings.get("redraw_model") or REDRAW[provider]["default_model"]).strip()
    if not model:
        raise RuntimeError("Choose a model for Redraw with AI in Settings → Thumbnail redraw")
    return provider, model


def image_models(provider: str) -> list[dict]:
    """The service's models that make pictures: [{"id", "name"}], Google's first."""
    if provider == "gemini":
        data = cloud._call("gemini", "GET", "/models", timeout=cloud.QUICK).get("data", [])
        ids = [str(m.get("id", "")).removeprefix("models/") for m in data]
        out = [{"id": i, "name": i} for i in ids if re.search(r"image|banana", i) and not re.search(r"imagen|veo|embed", i)]
    elif provider == "openrouter":
        data = cloud._call("openrouter", "GET", "/models?output_modalities=image", timeout=cloud.QUICK).get("data", [])
        out = [{"id": m["id"], "name": m.get("name") or m["id"]} for m in data if m.get("id")]
    else:
        raise RuntimeError("Unknown service")
    out.sort(key=lambda m: (not re.search(r"google|gemini|banana", m["id"]), m["name"].lower()))
    return out


def redraw(project: dict) -> dict:
    """Have an image model repaint the thumbnail; returns {"file", "created", "model", "by"}."""
    provider, model = redraw_setup(project["settings"])
    yt = project.get("youtube") or {}
    d = store.project_dir(project["id"])
    src = d / (yt.get("file") or "output/thumbnail.jpg")
    if not src.is_file():
        raise RuntimeError("There's no thumbnail to redraw yet")
    images = [_jpeg(Image.open(src), 1280)]
    byid = {x["id"]: x for x in project["panels"]}
    for pid in yt.get("pictures") or [yt.get("panel")]:
        x = byid.get(pid)
        img = panels.imread(d / "panels" / x["file"]) if x else None
        if img is None:
            continue
        h, w = img.shape[:2]
        for left, top, right, bottom in cover.boxes(x) if project["settings"].get("youtube_safe") else []:
            img[round(top * h):round(bottom * h), round(left * w):round(right * w)] = 0
        images.append(_jpeg(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), 1024))
    marks = yt.get("marks") or []
    words = [f'"{m["text"]}" ({"a yellow label with an arrow" if m["kind"] == "label" else "in a speech bubble" if m["kind"] == "line" else "beside a face"})'
             for m in marks] or [f'"{(yt.get("text") or "").upper()}" across the bottom']
    prompt = REDRAW_PROMPT.format(
        series=project["name"].strip(), words=", ".join(words),
        split=(" Where it's two pictures side by side, keep them as a split screen, or join them into one scene if "
               "they happen in the same place." if len(yt.get("pictures") or []) > 1 else ""))
    raw = _redraw_request(provider, model, prompt, images)
    out = d / "output" / "thumbnail_ai.jpg"
    out.parent.mkdir(exist_ok=True)
    ImageOps.fit(Image.open(io.BytesIO(raw)).convert("RGB"), (W, H), Image.LANCZOS).save(out, quality=92)
    return {"file": "output/thumbnail_ai.jpg", "created": time.time(), "model": model, "by": REDRAW[provider]["name"]}


def _jpeg(img: Image.Image, side: int) -> bytes:
    img = img.convert("RGB")
    img.thumbnail((side, side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def _redraw_request(provider, model, prompt, images) -> bytes:
    """One request to the service; the picture it made, as image file bytes."""
    name = REDRAW[provider]["name"]
    b64 = [base64.b64encode(b).decode() for b in images]
    if provider == "gemini":
        headers = {"x-goog-api-key": cloud.saved_key("gemini") or cloud._env_key("gemini") or "",
                   "Content-Type": "application/json"}
        body = {"model": model, "input": [{"type": "text", "text": prompt}]
                + [{"type": "image", "mime_type": "image/jpeg", "data": d} for d in b64],
                "response_format": {"type": "image", "aspect_ratio": "16:9"}}
    else:
        headers = cloud._headers("openrouter")
        body = {"model": model, "prompt": prompt, "aspect_ratio": "16:9",
                "input_references": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{d}"}}
                                     for d in b64]}
    try:
        r = requests.post(REDRAW[provider]["url"], headers=headers, json=body, timeout=REDRAW_TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f"Couldn't reach {name}: {e}") from e
    try:
        data = r.json()
    except ValueError:
        data = {}
    if isinstance(data, list):
        data = data[0] if data and isinstance(data[0], dict) else {}
    err = data.get("error") if isinstance(data, dict) else None
    if r.status_code >= 400 or err:
        message = (err.get("message") if isinstance(err, dict) else str(err or "")) or r.text[:300]
        e = cloud._problem(r.status_code, message)
        why = {"key": f"{name} rejected the key", "credit": f"{name} says the account is out of credit",
               "quota": f"this {name} key has no allowance left for {model}",
               "model": f"{model} isn't available to this {name} key",
               "declined": f"{name} declined to draw it", "busy": f"{name} is busy"}.get(e.kind, f"{name} said no")
        raise RuntimeError(f"{why}: {message[:300]}")
    found = _find_image(data, set(b64))
    if not found:
        said = " ".join(_texts(data))[:300]
        log.warning("%s answered without a picture: %s", name, str(data)[:1000])
        raise RuntimeError(f"{name} answered without a picture" + (f": {said}" if said else
                                                                  " (it may have declined this one)"))
    return base64.b64decode(found)


_SENT = {"input", "inputs", "input_references", "contents", "messages", "prompt"}  # where a reply repeats the request


def _find_image(obj, sent: set) -> str | None:
    """The first picture's base64 in a reply, wherever the service puts it (b64_json, inlineData, an image
    block's data, a data: URL), and not one of the pictures sent."""
    if isinstance(obj, dict):
        cands = [obj.get("b64_json")]
        inline = obj.get("inlineData") or obj.get("inline_data")
        if isinstance(inline, dict):
            cands.append(inline.get("data"))
        if obj.get("type") == "image" or str(obj.get("mime_type") or obj.get("mimeType") or "").startswith("image/"):
            cands.append(obj.get("data"))
        url = obj.get("url")
        if isinstance(url, str) and url.startswith("data:image/"):
            cands.append(url.split(",", 1)[-1])
        for c in cands:
            if isinstance(c, str) and len(c) > 200 and c not in sent:
                return c
        for k, v in obj.items():
            if k not in _SENT:
                found = _find_image(v, sent)
                if found:
                    return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_image(v, sent)
            if found:
                return found
    return None


def _texts(obj) -> list[str]:
    """What a reply says in words (why it made no picture), and its reasons for stopping."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _SENT:
                continue
            if k in ("text", "finishReason", "finish_reason", "blockReason", "status_details", "refusal") \
                    and isinstance(v, str) and v.strip():
                out.append(v.strip())
            else:
                out += _texts(v)
    elif isinstance(obj, list):
        for v in obj:
            out += _texts(v)
    return out
