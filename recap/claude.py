"""Narration with Claude: a whole chapter per request.

Claude sees every piece of the chapter in order (image plus OCR text), groups
consecutive pieces into scenes (fixing pieces the detector cut too finely),
writes one narration line per scene, and returns a running story summary that
is carried into the next chapter.
"""
import base64
import json
import logging
import math
import os

import anthropic
import cv2
import httpx2
import numpy as np

from .store import DATA_ROOT

log = logging.getLogger(__name__)  # token use per request; the app writes it to data/app.log
KEY_FILE = DATA_ROOT / "claude_key.txt"
MODELS = [
    ("claude-haiku-5-5", "Claude Haiku 5.5"),
    ("claude-sonnet-5-5", "Claude Sonnet 5.5"),
    ("claude-opus-5-5", "Claude Opus 5.5"),
]
# Shown under the model picker. Costs and results measured on a 105-piece webtoon chapter
# (about 77K input tokens), each model given the same request.
MODEL_NOTES = {
    "claude-haiku-5-5": ("About 1¢ per chapter. Cheapest and fastest, but in testing it mixed up who was "
                         "speaking and told a daydream as if it really happened."),
    "claude-sonnet-5-5": ("About 20¢ per chapter. Read the story correctly in testing, with a few small "
                          "slips over who said what."),
    "claude-opus-5-5": ("About 40¢ per chapter. The best reader in testing: kept track of who was narrating, "
                        "who said what, and what was only imagined."),
}
# models that can re-run a declined request on another Claude model server-side
SERVER_FALLBACK = {"claude-sonnet-5-5", "claude-opus-5-5"}
# Each piece is sent at most this wide, and tall pieces are cut into slices at most this tall, so
# text and small details stay legible (shrinking a whole 4000 px piece to fit sent it ~260 px wide).
MAX_WIDTH = 720
MAX_SLICE = 1440         # well under the 2000 px many-image limit
SLICE_OVERLAP = 48       # repeated at the top of the next slice, so a line of text cut in two is whole in one
PART_TOKENS = 85_000     # keep each request under 100K input tokens (Haiku 5.5's price steps up above that)


class Declined(Exception):
    """Claude declined the request (stop_reason "refusal")."""


class CutOff(Exception):
    """Claude's answer hit the output limit before it was complete."""


# ---- API key ----------------------------------------------------------------

def saved_key() -> str | None:
    try:
        key = KEY_FILE.read_text(encoding="utf-8").strip()
        return key or None
    except OSError:
        return None


def save_key(key: str) -> None:
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_text(key.strip(), encoding="utf-8")


def delete_key() -> None:
    KEY_FILE.unlink(missing_ok=True)


def key_status() -> dict:
    return {"saved": saved_key() is not None, "env": bool(os.environ.get("ANTHROPIC_API_KEY"))}


def client() -> anthropic.Anthropic:
    key = saved_key()
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()


def check_key() -> str:
    """A free call that fails if the key is wrong; returns a short status."""
    client().models.retrieve("claude-haiku-5-5")
    return "ok"


# ---- request ----------------------------------------------------------------

OUTPUT_SCENES = (
    "Output: a JSON object with \"scenes\" and \"summary\". \"scenes\" lists the scenes in order; "
    "each has \"first_piece\" and \"last_piece\" (the piece numbers it covers, inclusive) and "
    "\"narration\" (an empty string for a scene that adds nothing). \"summary\" is a short summary "
    "of the story so far, including this part, written to help narrate what comes next: who the "
    "characters are, how they relate, and what is unresolved. Write the summary in the same tense "
    "and person as the narration."
)

# Said after the pieces, right before Claude answers. The story-so-far summary at the top can
# pull the narration into its own tense (seen with present-tense summaries), so the rules get
# the last word.
CHECK_RULES = ("Before answering, check every narration line against the rules and style in your "
               "instructions, including the tense and point of view they ask for; the story-so-far "
               "notes above are background only and don't set the tense.")

# How to read a comic from these pieces. Said after the pieces, with the task.
READING = (
    "How to read the pieces:\n"
    "- The text after each piece is a raw OCR transcription of everything in it, run together. It "
    "doesn't say who is speaking or what kind of text each line is, so work that out from the image.\n"
    "- Rectangular caption boxes are a narrator's voice, often one of the characters looking back. "
    "Notice whose voice it is; it can change partway through a chapter.\n"
    "- A speech bubble belongs to the character its tail points to. Cloud-shaped or spiky thought "
    "bubbles, and text floating beside a character, are unspoken thoughts: narrate them as thoughts, "
    "not as something said aloud.\n"
    "- Sound effects, and voices or sounds coming from a TV, phone, speaker or video, are not lines "
    "spoken by the characters in the scene.\n"
    "- Read the whole part before writing. Flashbacks, dreams, daydreams, fantasies and imagined "
    "scenes are often marked only by a change in tint or style, a visual transition, or a caption or "
    "reaction afterwards that shows what really happened. Narrate them as memories or imagination, "
    "never as events that really happened."
)
# Added to READING when Magi (the optional add-on) has guessed speakers; it was right about 6 times in 10.
GUESSES = ("\n- Some pieces come with speaker guesses from an automatic tool. They are often wrong, especially "
           "when the speaker isn't drawn in that piece, for caption boxes, and for sounds from devices; when "
           "a guess and the image disagree, go by the image.")

SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "first_piece": {"type": "integer"},
                    "last_piece": {"type": "integer"},
                    "narration": {"type": "string"},
                },
                "required": ["first_piece", "last_piece", "narration"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["scenes", "summary"],
    "additionalProperties": False,
}

# YouTube-safe projects (see cover.py): the writer marks what the video's black boxes cover, alongside its
# narration. The box finder add-on is more exact where it finds something; this catches what it misses.
COVER = (
    "Black boxes: this video goes on YouTube, with black boxes over nudity, and you mark where they go. In "
    "\"cover\", list every spot in a piece that shows exposed nipples, genitals, or a bare bottom or groin in a "
    "sexual moment: the piece's number, and the spot's left, top, right and bottom edges as fractions (0 to 1) "
    "of that piece's width and height (for a tall piece shown in slices, of the whole piece, top to bottom). "
    "Make each box generous, with a margin all round; one box may cover a few spots close together. Leave "
    "\"cover\" empty when nothing needs covering."
)
SCHEMA_COVER = {**SCHEMA, "properties": {**SCHEMA["properties"], "cover": {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {k: {"type": "integer" if k == "piece" else "number"}
                       for k in ("piece", "left", "top", "right", "bottom")},
        "required": ["piece", "left", "top", "right", "bottom"],
        "additionalProperties": False,
    },
}}, "required": ["scenes", "summary", "cover"]}

GROUPING = (
    "The pages were cut into pieces automatically, so a piece may be a whole panel, part of a "
    "panel, a speech bubble or caption cut off from its art, or occasionally two panels. Group "
    "consecutive pieces that show the same moment into one scene (usually one panel together with "
    "its bubbles) and write one narration line per scene. Every piece belongs to exactly one "
    "scene, in order. Keep separate panels in separate scenes even when they are short."
)


GROUPING_SCROLL = (
    "This chapter is one continuous vertical strip, cut automatically into consecutive pieces from "
    "top to bottom. The video scrolls down the strip, and each narration line plays while its scene "
    "passes the middle of the screen. Group consecutive pieces into scenes so that each line covers "
    "what it describes: a scene can be one panel with its bubbles, or a few quick panels that one or "
    "two sentences cover together. Don't give a tiny fragment its own line. Every piece belongs to "
    "exactly one scene, in order."
)


def _slice_bounds(w: int, h: int) -> tuple[float, list[tuple[int, int]]]:
    """Scale for a piece and the row ranges (in the original) of the slices it is sent as."""
    s = min(1.0, MAX_WIDTH / w)
    n = max(1, math.ceil(h * s / MAX_SLICE))
    cuts = [round(i * h / n) for i in range(n + 1)]
    pad = round(SLICE_OVERLAP / s)
    return s, [(max(0, cuts[i] - (pad if i else 0)), cuts[i + 1]) for i in range(n)]


def _encode(img: np.ndarray) -> list[str]:
    """The piece as one or more JPEG slices (base64), top to bottom."""
    h, w = img.shape[:2]
    s, bounds = _slice_bounds(w, h)
    if s < 1.0:
        img = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    out = []
    for y0, y1 in bounds:
        ok, buf = cv2.imencode(".jpg", img[round(y0 * s):round(y1 * s)], [cv2.IMWRITE_JPEG_QUALITY, 85])
        out.append(base64.b64encode(buf.tobytes()).decode())
    return out


def piece_tokens(img: np.ndarray, text: str) -> int:
    """Rough input tokens for one piece: image patches (28x28 px each) plus its text."""
    h, w = img.shape[:2]
    s, bounds = _slice_bounds(w, h)
    return sum(math.ceil(w * s / 28) * math.ceil((y1 - y0) * s / 28) for y0, y1 in bounds) + len(text) // 3 + 30


def piece_images(img: np.ndarray) -> int:
    """How many pictures a piece is sent as (tall ones are cut into slices)."""
    h, w = img.shape[:2]
    return len(_slice_bounds(w, h)[1])


def split_parts(pieces: list[dict], budget: int = PART_TOKENS) -> list[list[dict]]:
    """Split a chapter's pieces into requests that each stay under the token budget."""
    parts, cur, used = [], [], 0
    for p in pieces:
        t = p.get("tokens") or piece_tokens(p["img"], p["text"])
        if cur and used + t > budget:
            parts.append(cur)
            cur, used = [], 0
        cur.append(p)
        used += t
    if cur:
        parts.append(cur)
    return parts


def _content(chapter: str, pieces: list[dict], context: str, fixed: list[dict] | None,
             scroll: bool = False, cover: bool = False) -> list[dict]:
    blocks = [{"type": "text", "text": (
        f"{context}\n\n" if context else "This is the beginning of the story.\n\n") +
        f"Narrate the following {len(pieces)} pieces from {chapter}, in reading order."}]
    for i, p in enumerate(pieces, 1):
        slices = _encode(p["img"])
        blocks.append({"type": "text", "text": f"Piece {i}:" if len(slices) == 1 else
                       f"Piece {i} (tall, shown in {len(slices)} slices from top to bottom):"})
        for data in slices:
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        text = (p["text"] or "").strip()
        blocks.append({"type": "text", "text": (
            f"Text read from piece {i} (OCR, may contain errors): {json.dumps(text)}" if text
            else f"No text was read from piece {i}.")})
        if p.get("speakers"):
            blocks.append({"type": "text", "text": f"Speaker guesses for piece {i}: {p['speakers']}"})
    if fixed is None:
        task = GROUPING_SCROLL if scroll else GROUPING
    else:
        lines = []
        for k, sc in enumerate(fixed, 1):
            span = f"pieces {sc['first']}-{sc['last']}" if sc["last"] > sc["first"] else f"piece {sc['first']}"
            if sc["write"]:
                lines.append(f"- Scene {k} ({span}): WRITE narration")
            else:
                lines.append(f"- Scene {k} ({span}): already narrated as {json.dumps(sc['narration'])}")
        task = ("These pieces are already grouped into scenes, listed below. Keep this grouping exactly, "
                "and write narration only for the scenes marked WRITE; return the other scenes with "
                "their existing narration unchanged.\n" + "\n".join(lines))
    reading = READING + (GUESSES if any(p.get("speakers") for p in pieces) else "")
    blocks.append({"type": "text", "text": f"{reading}\n\n{task}\n\n" + (f"{COVER}\n\n" if cover else "") + CHECK_RULES})
    return blocks


def narrate(model: str, instructions: str, chapter: str, pieces: list[dict], context: str,
            fixed: list[dict] | None = None, api=None, scroll: bool = False,
            effort: str | None = None, cover: bool = False) -> tuple:
    """Narrate one part of a chapter.

    pieces: [{"img": BGR image, "text": OCR text}] in reading order.
    fixed: None to let Claude group the pieces, or the existing scenes
           [{"first", "last", "narration", "write"}] (1-based) to only fill some in.
    cover: also mark what the video's black boxes cover (a YouTube-safe project).
    Returns ([(first_piece, last_piece, narration)], summary) with scenes that cover
    every piece exactly once, and with cover a third item: read_covers's boxes.
    """
    data = ask(model, instructions, _content(chapter, pieces, context, fixed, scroll, cover),
               SCHEMA_COVER if cover else SCHEMA, api=api, effort=effort)
    return read_answer(data, len(pieces), fixed) + ((read_covers(data, len(pieces)),) if cover else ())


def ask(model: str, instructions: str, content: list[dict], schema: dict, max_tokens: int = 32000, api=None,
        effort: str | None = None) -> dict:
    """One request (content: text and image blocks) whose answer is JSON in schema's shape."""
    api = api or client()
    output_config = {"format": {"type": "json_schema", "schema": schema}}
    if effort:  # "low" means less thinking before answering: faster, and less likely to run long
        output_config["effort"] = effort
    params = dict(
        model=model, max_tokens=max_tokens, system=instructions,
        messages=[{"role": "user", "content": content}],
        output_config=output_config,
    )
    if model in SERVER_FALLBACK:
        stream = api.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
    else:
        stream = api.messages.stream(**params)
    try:
        with stream as s:
            msg = s.get_final_message()
    except httpx2.TransportError as e:  # the connection dropped mid-reply; the SDK only retries before one starts
        try:
            request = e.request
        except RuntimeError:  # not attached to a request
            request = httpx2.Request("POST", str(api.base_url))
        raise anthropic.APIConnectionError(message=f"The connection dropped mid-reply ({e})", request=request) from e
    u = getattr(msg, "usage", None)
    log.info("Claude %s (answered by %s): %s input + %s output tokens, stop %s",
                 model, getattr(msg, "model", model), getattr(u, "input_tokens", "?"),
                 getattr(u, "output_tokens", "?"), msg.stop_reason)
    if msg.stop_reason == "refusal":
        details = getattr(msg, "stop_details", None)
        raise Declined(getattr(details, "explanation", None) or "Claude declined this part")
    if msg.stop_reason == "max_tokens":
        raise CutOff("Claude's answer was cut off (too long)")
    text = next((b.text for b in msg.content if b.type == "text"), "")
    return json.loads(text)


def read_answer(data: dict, n: int, fixed: list[dict] | None) -> tuple[list[tuple[int, int, str]], str]:
    """The scenes and summary from an answer in SCHEMA's shape (from Claude or another service)."""
    scenes = normalize(data.get("scenes", []), n)
    if fixed is not None:  # keep our grouping; take narration only for the scenes we asked for
        by_first = {sc[0]: sc[2] for sc in scenes}
        scenes = [(sc["first"], sc["last"], by_first.get(sc["first"], "") if sc["write"] else sc["narration"])
                  for sc in fixed]
    return scenes, (data.get("summary") or "").strip()


def read_covers(data: dict, n: int) -> list[tuple[int, list[float]]]:
    """The black boxes an answer marks: [(piece number, [left, top, right, bottom] as fractions)]."""
    from .cover import clean
    out = []
    for c in data.get("cover") or []:
        try:
            piece = int(c.get("piece", 0))
        except (AttributeError, TypeError, ValueError):
            continue
        box = clean([c.get(k) for k in ("left", "top", "right", "bottom")])
        if 1 <= piece <= n and box:
            out.append((piece, box))
    return out


def normalize(scenes: list[dict], n: int) -> list[tuple[int, int, str]]:
    """Make scenes cover pieces 1..n exactly once, in order: overlaps are trimmed and
    pieces left out are added to the scene before them."""
    out: list[list] = []
    nxt = 1
    for sc in sorted(scenes, key=lambda s: (int(s.get("first_piece", 0)), int(s.get("last_piece", 0)))):
        first = max(int(sc.get("first_piece", 0)), nxt)
        last = min(int(sc.get("last_piece", 0)), n)
        if last < first:
            continue
        if first > nxt:
            if out:
                out[-1][1] = first - 1
            else:
                out.append([nxt, first - 1, ""])
        out.append([first, last, str(sc.get("narration", "")).strip()])
        nxt = last + 1
    if nxt <= n:
        if out:
            out[-1][1] = n
        else:
            out.append([1, n, ""])
    return [tuple(x) for x in out]
