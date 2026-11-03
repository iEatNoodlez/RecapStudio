"""Narration script generation with a local Ollama model."""
import base64
import json
import logging

import cv2
import numpy as np
import requests

from . import scenes


def list_models(base_url: str) -> list[dict]:
    r = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=5)
    r.raise_for_status()
    return [{"name": m["name"], "size": m.get("size", 0)} for m in r.json().get("models", [])]


def capabilities(base_url: str, model: str) -> list[str]:
    try:
        r = requests.post(f"{base_url.rstrip('/')}/api/show", json={"model": model}, timeout=10)
        r.raise_for_status()
        return r.json().get("capabilities", []) or []
    except requests.RequestException:
        return []


def _jpeg_b64(img_bgr: np.ndarray, max_side: int = 896) -> str:
    h, w = img_bgr.shape[:2]
    s = min(1.0, max_side / max(h, w))
    if s < 1.0:
        img_bgr = cv2.resize(img_bgr, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf.tobytes()).decode()


SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"panel": {"type": "integer"}, "narration": {"type": "string"}},
                "required": ["panel", "narration"],
            },
        }
    },
    "required": ["lines"],
}


def _clean(items) -> list[str]:
    return [str(x).strip() for x in items or [] if str(x).strip()]


OUTPUT_LINES = (
    "Output: a JSON object with a \"lines\" array holding exactly one entry per panel you are "
    "given, each with the panel's number (\"panel\") and its narration (\"narration\"). "
    "Use an empty string for a panel you skip."
)


# YouTube-safe (the Video tab): how sex scenes are told in a recap YouTube will age-restrict. As far as such
# a video can go and no further: what YouTube takes down is narration that reads as meant to arouse.
YOUTUBE = (
    "YouTube-safe narration. This recap goes on YouTube as an age-restricted video, so sex scenes are told the "
    "way an R-rated film shows them: frankly, but without explicit detail. This overrides anything else in "
    "these instructions about how detailed sex scenes may be.\n"
    "- Say plainly that they kissed, undressed, were naked, and had sex or slept together. The build-up, "
    "passionate kissing (lips, neck, shoulders), clothes and underwear coming off, bodies pressed together, who "
    "pulls whom into bed or climbs on top, gasps and moans, how intense or tender it feels, and the afterglow "
    "are all fine.\n"
    "- Don't name or describe specific sex acts or positions beyond that (no oral sex, no penetration, no 'from "
    "behind'), and don't mention genitals or nipples, or hands or mouths on breasts or between legs. No "
    "orgasms or climaxing, no bodily fluids, no crude or slang words, and no lines that linger on a body as "
    "something to look at.\n"
    "- Give the sex itself a line or two. Fill the rest of the scene's lines with what they say, think and "
    "feel, what it means between them, and what's at stake.\n"
    "- Sex that is forced or pressured, or that happens while someone can't agree (drunk, drugged, asleep, "
    "blackmailed): say what happened in one plain, non-sexual sentence, then stay with the fear, anger or "
    "shock of the person it happens to, the aftermath and the consequences. Never make it sound sexy.\n"
    "- Nudity outside sex (a bath, getting changed) is said in passing, without describing the body.\n"
    "- Swearing and violence keep their real words."
)


def system_prompt(settings: dict, characters: list[dict], output: str = OUTPUT_LINES) -> str:
    """The instructions shared by every request: persona, rules, style, YouTube-safe wording, cast, run note,
    output format."""
    parts = [settings.get("persona", "").strip()]
    rules = _clean(settings.get("rules"))
    if rules:
        parts.append("Rules:\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1)))
    styles = _clean(settings.get("styles"))
    if styles:
        parts.append("Style:\n" + "\n".join(f"- {s}" for s in styles))
    if settings.get("youtube_safe"):
        parts.append(YOUTUBE)
    cast = []
    for c in characters:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        line = f"- {name}"
        extra = [x for x in [(c.get("gender") or "").strip()] if x]
        aliases = _clean((c.get("aliases") or "").split(","))
        if aliases:
            extra.append("may appear in text as " + ", ".join(aliases))
        if extra:
            line += f" ({'; '.join(extra)})"
        if (c.get("description") or "").strip():
            line += f": {c['description'].strip()}"
        looks = []
        for lk in c.get("looks") or []:  # an alter ego, disguise, transformation, younger self...
            label, note = (lk.get("label") or "").strip(), (lk.get("note") or "").strip()
            if label or note:
                looks.append(f"{label} ({note})" if label and note else label or note)
        if looks:
            line += f"{'' if line.endswith('.') else '.'} Also appears as: {'; '.join(looks)}."
        cast.append(line)
    if cast:
        parts.append("Characters (use these names when you recognize them; when one appears in another of "
                     "their looks, it is still them, so narrate it as them):\n" + "\n".join(cast))
    note = (settings.get("note") or "").strip()
    if note:
        parts.append(f"Note for this run (important, follow it):\n{note}")
    parts += extra_instructions(EXTRA, settings)  # plugins' (the language to write in, say)
    parts.append(output)
    return "\n\n".join(p for p in parts if p)


EXTRA: list = []  # plugins.Plugin.script_instructions: fn(settings) -> more instructions for the narration


def extra_instructions(hooks: list, settings: dict) -> list[str]:
    """What the plugins add to a request's instructions; one that fails is logged and left out."""
    out = []
    for fn in hooks:
        try:
            text = (fn(settings) or "").strip()
        except Exception:
            logging.getLogger("recap.plugins").exception("A plugin's instructions failed")
            continue
        if text:
            out.append(text)
    return out


def chapter_markers(project: dict) -> dict[str, str]:
    """Scene head id -> note for the model where a chapter starts or ends (enabled panels only)."""
    names = {c["id"]: c["name"] for c in project["chapters"]}
    by_chapter: dict[str, list[str]] = {}
    for group in scenes.groups(project["panels"]):
        by_chapter.setdefault(group[0]["chapter"], []).append(group[0]["id"])
    marks: dict[str, str] = {}
    for cid, ids in by_chapter.items():
        name = names.get(cid, "a new chapter")
        marks[ids[0]] = f"start of {name}"
        marks[ids[-1]] = f"start and end of {name}" if len(ids) == 1 else f"end of {name}"
    return marks


def user_prompt(batch_texts: list[tuple], story_so_far: str, use_vision: bool) -> str:
    """The per-request part: story so far plus the text read from each panel.

    batch_texts: (number, ocr_text) or (number, ocr_text, chapter_marker) per panel.
    """
    lines = []
    for item in batch_texts:
        n, text, mark = (tuple(item) + ("",))[:3]
        lines.append(f"Panel {n}{f' ({mark})' if mark else ''}: {json.dumps(text or '')}")
    if story_so_far:
        context = f"Story so far (most recent narration):\n{story_so_far}\n\nContinue naturally from where it left off. "
    else:
        context = "This is the beginning of the story.\n\n"
    return (
        context
        + f"Narrate the next {len(batch_texts)} panels"
        + (", attached as images in this order" if use_vision else "")
        + ". Text read from each panel (by OCR, so it may contain errors):\n"
        + "\n".join(lines)
    )


def narrate_batch(base_url, model, system, batch, story_so_far, use_vision, think_capable, temperature):
    """batch: list of (number, image_bgr, ocr_text, chapter_marker). Returns {number: narration}.

    The instructions go in the same message as the panels rather than a separate system
    message: in testing, gemma via Ollama ignored rules (like past tense) given as a
    system message but followed them reliably when they were part of the request.
    """
    content = system + "\n\n" + user_prompt([(n, t, m) for n, _, t, m in batch], story_so_far, use_vision)
    msg = {"role": "user", "content": content}
    if use_vision:
        msg["images"] = [_jpeg_b64(img) for _, img, _, _ in batch]
    body = {
        "model": model,
        "messages": [msg],
        "stream": False,
        "format": SCHEMA,
        "options": {"temperature": temperature, "num_ctx": 16384},
    }
    if think_capable:
        body["think"] = False
    r = requests.post(f"{base_url.rstrip('/')}/api/chat", json=body, timeout=600)
    r.raise_for_status()
    content = r.json()["message"]["content"]
    data = json.loads(content)
    out = {}
    for line in data.get("lines", []):
        try:
            out[int(line["panel"])] = str(line.get("narration", "")).strip()
        except (KeyError, TypeError, ValueError):
            continue
    return out
