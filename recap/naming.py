"""Suggest names for the character scan's groups (charscan.py). The writer chosen for the script (Claude,
another online service or the local model) sees each group's pictures, on labelled sheets, and what is said
where the group is seen, and suggests a name, other names and a short description, or says the group is an
extra, the same character as another group, or a character already on the list. Suggestions are kept on the
scan's groups for the user to accept on the review cards; nothing is added to the character list here.

Why the writer: matching names in the text to groups by itself (a name said near a group's appearances more
often than chance) was tried on a 55-chapter series and got 4 of its 5 main characters, and no one else.
"""
import base64
import json
import re

import cv2
import numpy as np
import requests

from . import claude, cloud, panels, script, store

MAX_GROUPS = 30   # the biggest groups still to review; the rest are mostly people seen once or twice
MIN_SIZE = 3      # as the review page: smaller groups are hidden there unless asked for
ROWS = 4          # groups per picture sheet
SNAPS = 4         # pictures per group on a sheet
CELL_W, ROW_H, LABEL_W, GAP = 240, 220, 170, 6
EXCERPTS = 18     # what is said where a group is seen, per group
EXCERPT_CHARS = 220
# lines likely to hold a name: honorifics after a name, titles before one, introductions, and a profile
# card's "Choi Haeun" (lettering is mostly capitals, so two capitalised words stand out)
NAME_CUE = re.compile(r"(?i)\b[a-z]{2,}[- ]?(ssi|nim|san|kun|chan|sama|senpai|sunbae|oppa|unnie|noona|hyung)\b"
                      r"|\b(mr|ms|mrs|miss|dr)\.? [a-z]{2,}|\bmy name\b|\bname is\b|\bcall me\b"
                      r"|(?-i:\b[A-Z][a-z]+ [A-Z][a-z]+\b)")

SCHEMA = {
    "type": "object",
    "properties": {"groups": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "group": {"type": "string"}, "name": {"type": "string"}, "aliases": {"type": "string"},
            "gender": {"type": "string", "enum": ["female", "male", ""]}, "description": {"type": "string"},
            "existing": {"type": "string"}, "same_as": {"type": "string"},
            "extra": {"type": "boolean"}, "sure": {"type": "boolean"},
        },
        "required": ["group", "name", "aliases", "gender", "description", "existing", "same_as", "extra", "sure"],
        "additionalProperties": False}}},
    "required": ["groups"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You help name the characters of a comic (a webtoon, manga or manhwa) for a narrated recap. An \
automatic scan grouped the drawings of people by how they look. Each group should be one character, but one \
character can be split over several groups (other clothes, hairstyles or lighting), and a group can take in \
someone who looks alike. You get each group's pictures, on sheets with one labelled row per group, and the text \
read from panels where the group is seen (by OCR: often all capitals, with mistakes).

For each group, work out who it is, and answer with these fields:
- group: the group's number, as labelled.
- name: the character's name as the story gives it, in its most common spelling, with the family name when the \
story gives one (for example "Choi Ha-eun"). Normal capitalisation, not the lettering's capitals, and no \
honorifics or suffixes (-ssi, -nim, -san, -kun, -chan, -sunbae). Empty when the text doesn't make it clear: \
never invent a name.
- aliases: other names they go by in the text, separated by commas: other spellings, nicknames, titles used as \
a name (Manager, Doctor), fake names, online handles. Empty if none.
- gender: "female", "male" or "".
- description: one or two plain sentences on who they are in the story, useful to a narrator: their role or \
job and how they relate to the others. Nothing explicit. When the name is unknown, say what they are (for \
example: a client of the agency, the team leader at her office).
- existing: when the group is a character already on the list below (or one of their looks), that character's \
name exactly as listed; otherwise "".
- same_as: when the group is the same character as another group here, that group's number; point to the \
bigger group (the lower number). Otherwise "".
- extra: true for background people, crowds and anyone seen only in passing who doesn't matter to the story.
- sure: true when the text and pictures make the name clear, false for a likely guess.

Reading the text: a name in a line is usually the person being spoken to, and speech in a panel is often said \
to the person shown rather than by them. Panels where a group is seen with others are marked with the other \
groups. Weigh many lines rather than one. When two groups get the same name, decide which is the main one and \
mark the other same_as it. Answer every group you are given."""


def suggest(project: dict, report, ask=None) -> tuple[str, dict, str]:
    """Ask the script's writer about the scan's open groups (the biggest MAX_GROUPS of at least MIN_SIZE).
    report(fraction, message) gives progress; ask(instructions, blocks) -> answer, for a writer that isn't asked
    here (Claude Code: claude_code.NameAsker). Returns (the scan's id, {group id: suggestion}, who answered)."""
    rec = project.get("scan") or {}
    groups = sorted((g for g in rec.get("groups", []) if g["status"] == "open" and g["size"] >= MIN_SIZE),
                    key=lambda g: -g["size"])[:MAX_GROUPS]
    if not groups:
        raise RuntimeError("No scanned characters are left to name: scan for characters first, or reopen a group.")
    s = project["settings"]
    d = store.project_dir(project["id"])
    label = writer_label(s)
    per = ROWS * (_max_images(s) or 10 ** 6)  # groups per request (some services take few pictures)
    batches = [groups[i:i + per] for i in range(0, len(groups), per)]
    said = _said(project)
    found = {}
    for k, batch in enumerate(batches):
        report(k / len(batches), f"Suggesting names with {label}"
               + (f": part {k + 1} of {len(batches)}" if len(batches) > 1 else ""))
        content = _content(project, d, batch, said)
        data = ask(INSTRUCTIONS, content) if ask else _ask(s, INSTRUCTIONS, content)
        found.update(_read(data, batch, rec["groups"], project["characters"], label))
    report(1.0, f"Suggesting names with {label}")
    return rec["id"], found, label


def writer_label(s: dict) -> str:
    provider = s.get("llm_provider", "ollama")
    if provider == "claude":
        model = s.get("claude_model") or "claude-haiku-5-5"
        return dict(claude.MODELS).get(model, model)
    if provider == "claude_code":
        model = s.get("claude_code_model") or "opus"
        return f"Claude Code ({model.capitalize()})"
    if provider in cloud.PROVIDERS:
        info = cloud.PROVIDERS[provider]
        return f"{info['name']} ({s.get(f'{provider}_model') or info['default_model']})"
    return f"the local model ({s.get('ollama_model') or 'Ollama'})"


def _max_images(s):
    return (cloud.PROVIDERS.get(s.get("llm_provider")) or {}).get("max_images")


# ---- what the writer is shown ------------------------------------------------------

def _said(project) -> dict:
    """For finding what is said where a group is seen: pieces in story order (left-out ones skipped), each
    piece's text and chapter, and which groups are seen in it."""
    pieces = [x for x in project["panels"] if x.get("enabled", True)]
    chapters = {c["id"]: c["name"] for c in project["chapters"]}
    who = [set() for _ in pieces]
    at = {x["id"]: i for i, x in enumerate(pieces)}
    for g in (project.get("scan") or {}).get("groups", []):
        if g["status"] != "merged":  # a merged group's members are in the group it went into
            for m in g["members"]:
                if m[0] in at:
                    who[at[m[0]]].add(g["id"])
    return {"at": at, "who": who, "chapter": [chapters.get(x["chapter"], "") for x in pieces],
            "text": [re.sub(r"\s+", " ", x.get("text") or "").strip() for x in pieces]}


def _spread(items, n):
    """n items spread evenly over the list (all of them when there are no more than n)."""
    if len(items) <= n:
        return list(items)
    return [items[round(k * (len(items) - 1) / (n - 1))] for k in range(n)] if n > 1 else items[:n]


def _excerpts(g, said) -> list[str]:
    """What is said where the group is seen: the text of each piece and the ones either side of it (speech
    often sits in the next piece of a strip), spread over the story. Lines with a likely name come first,
    then pieces where the group is seen alone, then the rest."""
    text, who = said["text"], said["who"]

    def around(i):
        return " / ".join(text[j] for j in (i - 1, i, i + 1) if 0 <= j < len(text) and text[j])
    seen = sorted({said["at"][m[0]] for m in g["members"] if m[0] in said["at"]})
    seen = [i for i in seen if around(i)]
    alone = {i for i in seen if who[i] == {g["id"]}}
    cue = {i for i in seen if NAME_CUE.search(around(i))}
    pick = []
    for pool, most in (([i for i in seen if i in cue and i in alone], 8), ([i for i in seen if i in cue], 4),
                       ([i for i in seen if i in alone], EXCERPTS), (seen, EXCERPTS)):
        left = [i for i in pool if i not in set(pick)]
        pick += _spread(left, min(most, EXCERPTS - len(pick)))
    out = []
    for i in sorted(pick):
        line = around(i)
        if len(line) > EXCERPT_CHARS:
            line = line[:EXCERPT_CHARS].rsplit(" ", 1)[0] + " …"
        others = sorted(int(x[1:]) for x in who[i] - {g["id"]})
        also = f" (also seen: group{'s' if len(others) > 1 else ''} {', '.join(map(str, others))})" if others else ""
        out.append(f"- {said['chapter'][i]}{also}: {line}")
    return list(dict.fromkeys(out))


def _content(project, d, groups, said) -> list[dict]:
    """The request: the series, the characters already listed, the story so far (once the script has been
    written), then the picture sheets and each group's details and lines."""
    named = [c for c in project["characters"] if (c.get("name") or "").strip()]
    cast = "\n".join(
        f"- {c['name']}" + (f" (also: {c['aliases']})" if (c.get("aliases") or "").strip() else "")
        + (f": {c['description'].strip()}" if (c.get("description") or "").strip() else "")
        + "".join(f"\n  - another look, {lk.get('label') or 'unnamed'}" for lk in c.get("looks") or [])
        for c in named)
    story = next((c["summary"] for c in reversed(project["chapters"]) if (c.get("summary") or "").strip()), "")
    head = (f'The comic: "{project["name"]}".\n\n'
            + (f"Characters already on the list:\n{cast}\n\n" if cast else "No characters are on the list yet.\n\n")
            + (f"The story so far, as summarised by the narrator:\n{story}\n\n" if story else ""))
    blocks = [{"type": "text", "text": head + f"{len(groups)} groups to name follow: the picture sheets first, "
                                              "then each group's details and what is said where it is seen."}]
    for k in range(0, len(groups), ROWS):
        rows = groups[k:k + ROWS]
        blocks.append({"type": "text", "text": "Sheet with groups " + ", ".join(g["id"][1:] for g in rows) + ":"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": _sheet(d, rows)}})
    for g in groups:
        traits = ", ".join(t["label"] for t in g.get("traits") or [])
        lines = _excerpts(g, said)
        chapters = g["chapters"]
        blocks.append({"type": "text", "text": (
            f"Group {g['id'][1:]}: seen {g['size']} times in {len(chapters)} chapter"
            f"{'s' if len(chapters) != 1 else ''} ({chapters[0]} to {chapters[-1]})"
            + (f"; the scan's guess: {g['gender']}" if g.get("gender") else "")
            + (f"; looks: {traits}" if traits else "") + ".\n"
            + ("What is said where it is seen:\n" + "\n".join(lines) if lines else "No text near it was read."))})
    blocks.append({"type": "text", "text": f"Answer for all {len(groups)} groups: "
                                           + ", ".join(g["id"][1:] for g in groups) + "."})
    return blocks


def _sheet(d, groups) -> str:
    """One row per group: its number in red, then its snapshots. A JPEG, base64."""
    rows = []
    for g in groups:
        label = np.full((ROW_H, LABEL_W, 3), 255, np.uint8)
        cv2.putText(label, f"Group {g['id'][1:]}", (10, ROW_H // 2 + 10), cv2.FONT_HERSHEY_DUPLEX, 0.95,
                    (0, 0, 220), 2, cv2.LINE_AA)
        cells = [label]
        for snap in g["snapshots"][:SNAPS]:
            img = panels.imread(d / snap["file"])
            if img is None:
                continue
            h, w = img.shape[:2]
            s = min(CELL_W / w, ROW_H / h)
            img = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
            cell = np.full((ROW_H, CELL_W + GAP, 3), 255, np.uint8)
            y, x = (ROW_H - img.shape[0]) // 2, (CELL_W - img.shape[1]) // 2
            cell[y:y + img.shape[0], x:x + img.shape[1]] = img
            cells.append(cell)
        rows.append(np.hstack(cells))
    width = max(r.shape[1] for r in rows)
    line = np.full((GAP, width, 3), 180, np.uint8)
    sheet = np.vstack([part for r in rows for part in
                       (np.pad(r, ((0, 0), (0, width - r.shape[1]), (0, 0)), constant_values=255), line)][:-1])
    ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return base64.b64encode(buf.tobytes()).decode()


# ---- asking, and reading the answer --------------------------------------------------

def _ask(s, instructions, blocks) -> dict:
    provider = s.get("llm_provider", "ollama")
    if provider == "claude":
        return claude.ask(s.get("claude_model") or "claude-haiku-5-5", instructions, blocks, SCHEMA, max_tokens=16000)
    if provider in cloud.PROVIDERS:
        info = cloud.PROVIDERS[provider]
        model = s.get(f"{provider}_model") or info["default_model"]
        if not model:
            raise RuntimeError(f"Choose a {info['name']} model in Settings → Script first.")
        if not cloud.has_key(provider):
            raise RuntimeError(f"Add your {info['name']} key in Settings → Script first.")
        return cloud.ask(provider, model, instructions, blocks, SCHEMA, "recap_names")
    return _ask_ollama(s, instructions, blocks)


def _ask_ollama(s, instructions, blocks) -> dict:
    """The local model: everything in one message (as script.narrate_batch does), pictures when it reads them."""
    url, model = s["ollama_url"], s["ollama_model"]
    if not model:
        models = script.list_models(url)
        if not models:
            raise RuntimeError("No Ollama models installed. Run e.g. 'ollama pull gemma3' first.")
        model = models[0]["name"]
    caps = script.capabilities(url, model)
    vision = bool(s.get("use_vision")) and "vision" in caps
    texts = [b["text"] for b in blocks if b["type"] == "text"]
    if not vision:
        texts.insert(0, "(The picture sheets can't be shown to this model: work from the text.)")
    msg = {"role": "user", "content": instructions + "\n\n" + "\n\n".join(texts)}
    if vision:
        msg["images"] = [b["source"]["data"] for b in blocks if b["type"] == "image"]
    body = {"model": model, "messages": [msg], "stream": False, "format": SCHEMA,
            "options": {"temperature": 0.2, "num_ctx": 32768}}
    if "thinking" in caps:
        body["think"] = False
    r = requests.post(f"{url.rstrip('/')}/api/chat", json=body, timeout=900)
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


def _read(data, batch, all_groups, characters, label) -> dict:
    """{group id: suggestion} from the answer, for the groups asked about. The suggestion's character is the
    listed character it names as existing; same_as is another group of this scan still open."""
    asked = {g["id"] for g in batch}
    open_ids = {g["id"] for g in all_groups if g["status"] == "open"}
    listed = {}
    for c in characters:
        for n in [c.get("name") or "", *(c.get("aliases") or "").split(",")]:
            if n.strip():
                listed.setdefault(n.strip().lower(), c["id"])
    out = {}
    for a in data.get("groups") or []:
        gid = "g" + re.sub(r"\D", "", str(a.get("group", "")))
        if gid not in asked:
            continue
        same = "g" + re.sub(r"\D", "", str(a.get("same_as") or ""))
        out[gid] = {
            "name": (a.get("name") or "").strip(),
            "aliases": (a.get("aliases") or "").strip(),
            "gender": a.get("gender") if a.get("gender") in ("female", "male") else "",
            "description": (a.get("description") or "").strip(),
            "character": listed.get((a.get("existing") or "").strip().lower()),
            "same_as": same if same in open_ids and same != gid else None,
            "extra": bool(a.get("extra")),
            "sure": bool(a.get("sure")),
            "by": label,
        }
    return out
