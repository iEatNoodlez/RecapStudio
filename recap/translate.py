"""A raw comic (Korean, Chinese or Japanese: the project's language) translated into English by the script
writer, a chapter at a time (the Translate step, after Read text).

The writer sees each piece with text, with its text boxes outlined and numbered on the picture (12.3 is piece
12's third box), and the text read from each box (ocr.read_bubbles; it may be misread, so the picture is there
to check it). For every box it answers the English and what kind of text it is: speech, a thought, narration,
a sign (part of the scene: signs, screens, messages, titles), a sound effect, or none (not text, a watermark).

Each panel keeps its boxes with their English and kind (typeset.py puts the English in the pictures, but not
sound effects); its text becomes the English, for the script, the character names and the title, and
source_text keeps the original. A panel's translation stands while its boxes read the same (key)."""
import base64
import hashlib
import json
import math
import re

import cv2
import numpy as np

from . import claude, naming, ocr, panels, store

KINDS = ("speech", "thought", "narration", "sign", "sfx", "none")
WRITTEN = ("speech", "thought", "narration", "sign")  # what the script writer reads
WIDTH = 1000          # pictures are sent this wide at most (small lettering stays readable)
SLICE = 1440          # and cut into slices this tall
PART_TOKENS = 60_000  # input tokens per request, about

SCHEMA = {
    "type": "object",
    "properties": {
        "boxes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "box": {"type": "string", "description": 'the box\'s number as on the picture, e.g. "12.3"'},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "english": {"type": "string"},
                },
                "required": ["box", "kind", "english"],
            },
        },
    },
    "required": ["boxes"],
}

INSTRUCTIONS = """You translate a {language} comic into English, as it would read in a good English edition.

Each request is part of a chapter: its pieces in reading order, each picture with its text boxes outlined in \
red and numbered (12.3 is piece 12's third box), then the text a text reader read from each box. That reading \
can be wrong (a misread or missing character, two lines run together): read the picture itself wherever it \
looks wrong, and translate what the picture says.

For every box, answer:
- kind: "speech" (said aloud), "thought" (thinking, an inner voice), "narration" (a caption box or a \
narrator), "sign" (text that's part of the scene: signs, screens, phone messages, letters, labels, a chapter \
title), "sfx" (a sound effect or other onomatopoeia drawn as part of the art), or "none" (not text at all, a \
watermark, a scan group's or website's name, or a scrap of a line cut off at the picture's edge whose whole \
line is in another box).
- english: the translation. Natural English that sounds like the character and the scene; keep the jokes, \
insults, slang and tone. Keep it about as short as the original, since it goes back in the same bubble. For \
"sfx", a short English sound word (THUD, CRASH, ba-dump); for "none", "".

Names and words:
- A character on the character list: use that spelling. Otherwise romanize the name the usual way ({names}).
- Translate honorifics and forms of address into natural English (Mr. Kim, sir, senior, big brother), unless \
it's what a character is called.
- Use the same English for a name, place, title or made-up term every time it appears.
- Keep a speaker's gender as the pictures show it; don't guess "he" for someone the scene shows is a woman.

Answer for every box, in order, even when it's unclear ("none" only when it really isn't text)."""

NAMES = {"ko": "Revised Romanization, family name first, as in Kim Dokja",
         "zh": "pinyin, family name first, as in Lin Feng",
         "ja": "Hepburn, family name first, as in Tanaka Hiroshi"}


def instructions(settings: dict) -> str:
    lang = ocr.language(settings)
    return INSTRUCTIONS.format(language=ocr.LANGUAGES[lang], names=NAMES.get(lang, "the usual way"))


def key(panel: dict) -> str:
    """Fingerprint of a panel's text boxes as read: its translation stands while they read the same."""
    texts = [b.get("text", "") for b in panel.get("bubbles") or []]
    return hashlib.sha1(json.dumps(texts, ensure_ascii=False).encode()).hexdigest()[:12]


def writer_text(bubbles: list[dict]) -> str:
    """What the script writer reads for a translated panel: each box's English (not sound effects), thoughts,
    captions and signs marked as such."""
    tag = {"thought": "(thinking) ", "narration": "(caption) ", "sign": "(sign) "}
    return "\n".join(tag.get(b.get("kind"), "") + b["english"].strip() for b in bubbles
                     if (b.get("english") or "").strip() and b.get("kind") in WRITTEN)


def _marked(img: np.ndarray, bubbles: list[dict], n: int) -> np.ndarray:
    """The picture with each text box outlined and numbered (n.1, n.2, ...)."""
    out = img.copy()
    w = out.shape[1]
    scale = max(0.5, min(1.6, w / 800))
    thick = max(2, round(2 * scale))
    for k, b in enumerate(bubbles, 1):
        x0, y0, x1, y1 = b["box"]
        cv2.rectangle(out, (x0 - 3, y0 - 3), (x1 + 3, y1 + 3), (0, 0, 220), thick)
        label = f"{n}.{k}"
        (tw, th), base = cv2.getTextSize(label, cv2.FONT_HERSHEY_DUPLEX, 0.6 * scale, max(1, thick - 1))
        lx, ly = max(0, x0 - 3), max(th + base + 6, y0 - 4)
        cv2.rectangle(out, (lx, ly - th - base - 6), (lx + tw + 8, ly), (0, 0, 220), -1)
        cv2.putText(out, label, (lx + 4, ly - base - 3), cv2.FONT_HERSHEY_DUPLEX, 0.6 * scale, (255, 255, 255),
                    max(1, thick - 1), cv2.LINE_AA)
    return out


def _encode(img: np.ndarray) -> list[str]:
    """The picture as JPEG slices (base64), at most WIDTH wide, top to bottom."""
    h, w = img.shape[:2]
    s = min(1.0, WIDTH / w)
    if s < 1.0:
        img = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    h = img.shape[0]
    n = max(1, math.ceil(h / SLICE))
    out = []
    for i in range(n):
        y0, y1 = max(0, round(i * h / n) - (48 if i else 0)), round((i + 1) * h / n)
        ok, buf = cv2.imencode(".jpg", img[y0:y1], [cv2.IMWRITE_JPEG_QUALITY, 85])
        out.append(base64.b64encode(buf.tobytes()).decode())
    return out


def _tokens(img: np.ndarray, bubbles) -> int:
    h, w = img.shape[:2]
    s = min(1.0, WIDTH / w)
    return math.ceil(w * s / 28) * math.ceil(h * s / 28) + sum(len(b.get("text", "")) for b in bubbles) * 2 + 40


def _cast(project) -> str:
    named = [c for c in project["characters"] if (c.get("name") or "").strip()]
    return "\n".join(
        f"- {c['name']}" + (f" (also: {c['aliases']})" if (c.get("aliases") or "").strip() else "")
        + (f": {c['description'].strip()}" if (c.get("description") or "").strip() else "")
        for c in named)


def _content(project, chapter, part, first: bool) -> list[dict]:
    lang = ocr.LANGUAGES[ocr.language(project["settings"])]
    cast = _cast(project)
    blocks = [{"type": "text", "text": (
        f'The comic: "{project["name"]}", in {lang}. '
        + (f"Characters on the list:\n{cast}\n\n" if cast else "No characters are on the list yet.\n\n")
        + f"{chapter['name']}{'' if first else ', continued'}: {len(part)} pieces with text, in reading order.")}]
    for it in part:
        slices = _encode(it["img"])
        blocks.append({"type": "text", "text": f"Piece {it['n']}:" if len(slices) == 1 else
                       f"Piece {it['n']} (tall, shown in {len(slices)} slices from top to bottom):"})
        blocks += [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}
                   for data in slices]
        read = "\n".join(f"[{it['n']}.{k}] {json.dumps(b.get('text', ''), ensure_ascii=False)}"
                         + (" (vertical)" if b.get("vertical") else "") for k, b in enumerate(it["bubbles"], 1))
        blocks.append({"type": "text", "text": f"Text read from piece {it['n']}'s boxes:\n{read}"})
    ids = [f"{it['n']}.{k}" for it in part for k in range(1, len(it["bubbles"]) + 1)]
    blocks.append({"type": "text", "text": f"Answer for all {len(ids)} boxes: {', '.join(ids)}."})
    return blocks


def _read(data) -> dict:
    """{"12.3": {"kind", "english"}} from an answer."""
    out = {}
    for b in (data or {}).get("boxes") or []:
        if not isinstance(b, dict):
            continue
        box = re.sub(r"[^\d.]", "", str(b.get("box", "")))
        kind = str(b.get("kind", "")).strip().lower()
        out[box] = {"kind": kind if kind in KINDS else "speech", "english": str(b.get("english", "")).strip()}
    return out


def problem(data) -> str | None:
    """What's wrong with an answer's shape (for Claude Code's agent, which is asked to fix it)."""
    boxes = (data or {}).get("boxes")
    if not isinstance(boxes, list):
        return '"boxes" must be a list, one entry per box'
    for b in boxes:
        missing = [k for k in ("box", "kind", "english") if not isinstance(b, dict) or k not in b]
        if missing:
            return f"box entry {str(b)[:80]} is missing {', '.join(missing)}"
    return None


def _parts(items, budget, max_images):
    parts, cur, used, imgs = [], [], 0, 0
    for it in items:
        n = math.ceil(it["img"].shape[0] * min(1.0, WIDTH / it["img"].shape[1]) / SLICE)
        if cur and (used + it["tokens"] > budget or (max_images and imgs + n > max_images)):
            parts.append(cur)
            cur, used, imgs = [], 0, 0
        cur.append(it)
        used += it["tokens"]
        imgs += n
    return parts + ([cur] if cur else [])


def chapter(project: dict, chap: dict, pieces: list[dict], ask=None, report=None) -> tuple[dict, int]:
    """Translate these panels of a chapter (each with its "bubbles"), in order. Returns ({panel id: [{"kind",
    "english"}, one per box]}, how many boxes got no answer, even when asked again). ask(instructions,
    blocks) answers instead of the project's writer (Claude Code's agent); report(done, total) as it goes."""
    s = project["settings"]
    d = store.project_dir(project["id"])
    items = []
    for n, x in enumerate(pieces, 1):
        img = panels.imread(d / "panels" / x["file"])
        if img is None:
            raise ValueError(f"panel image missing: {x['file']}")
        marked = _marked(img, x["bubbles"], n)
        items.append({"n": n, "id": x["id"], "img": marked, "bubbles": x["bubbles"],
                      "tokens": _tokens(marked, x["bubbles"])})
    rules = instructions(s)

    def answer(part, first):
        blocks = _content(project, chap, part, first)
        data = ask(rules, blocks) if ask else naming._ask(s, rules, blocks, SCHEMA, "recap_translation")
        return _read(data)
    found = {}
    parts = _parts(items, PART_TOKENS, naming._max_images(s))
    for k, part in enumerate(parts):
        found.update(answer(part, k == 0))
        if report:
            report(k + 1, len(parts))
    left = [it for it in items if any(f"{it['n']}.{j}" not in found for j in range(1, len(it["bubbles"]) + 1))]
    if left:  # asked once more, for just the pieces with boxes left out
        for part in _parts(left, PART_TOKENS, naming._max_images(s)):
            found.update(answer(part, False))
    out, missed = {}, 0
    for it in items:
        boxes = []
        for j in range(1, len(it["bubbles"]) + 1):
            got = found.get(f"{it['n']}.{j}")
            if got is None:
                missed += 1
                got = {"kind": "speech", "english": ""}
            boxes.append(got)
        out[it["id"]] = boxes
    return out, missed
