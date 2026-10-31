"""Write a project's narration with the app's own Claude writer (pipeline._api_chapter + claude.narrate), answered
by Claude Code agents instead of the API. The app builds every request exactly as it would for Claude; this hands
each one over as files (prompt.md plus numbered picture sheets) and waits for answer.json, then lets the app's code
parse and apply it (scenes, narration, chapter summary, narrator label).

Usage: python fork_bridge.py <project id> <bridge dir> [chapter ...] [--new] [--redo] [--workers N] [--in-order]
                             [--label TEXT]
  chapter     a number ("5" for "Chapter 5") or a chapter's whole name ("Side Story 2"); none: every chapter
  --new       only chapters that have pieces but no narration yet (chapters added to a narrated project); each
              picks up from the story so far that the chapter before it left. Chapters with no pieces yet (panels
              not detected) are listed in status.json under "not detected" and skipped
  --redo      rewrite the chosen chapters from scratch (regrouping them), even where narration exists
  --workers   chapters worked on at once (each in its own thread; one agent per chapter answers them)
  --in-order  with --workers N: N chapters get their pictures at once, but each is finished only after the chapter
              before it (when that one is in this run), with the story so far it leaves. Until then its first
              request is a draft: the folder has a "draft" file and no story so far, and answers are set aside as
              answer.early.json. When the chapter before ends, the story so far goes into story_so_far.md and
              prompt.md, and "draft" is removed. (A long chapter's second part is still made after its first.)
Each request folder has chapter.txt; done_<chapter> or failed_<chapter> appears when a chapter ends.
A YouTube-safe project (Panel settings) has the YouTube wording in system.md, and its requests also ask for "cover":
the black boxes over nudity, which the app saves on the pieces as the script writer's boxes.
An answer whose short lines would leave a scrolling video silent for long (scroll_time.py) gets one FIX round;
what's still silent after it is noted in <bridge dir>/silences.txt.
Stop it by creating <bridge dir>/STOP. Writes FINISHED, STOPPED or ERROR in the bridge dir at the end.
Run it with the app's own Python (App\\.venv) and with the app closed: the app's store locks are per process.
The app folder is App, next to this skill's .claude folder (in Recap Studio); set RECAP_APP to use another."""
import argparse
import base64
import json
import os
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

APP = Path(os.environ.get("RECAP_APP") or Path(__file__).resolve().parents[4] / "App")
sys.path.insert(0, str(APP))
from recap import claude, pipeline, scenes, script, store  # noqa: E402
import scroll_time  # noqa: E402  (next to this script)

ap = argparse.ArgumentParser()
ap.add_argument("pid")
ap.add_argument("root", type=Path)
ap.add_argument("chapters", nargs="*")
ap.add_argument("--new", action="store_true")
ap.add_argument("--redo", action="store_true")
ap.add_argument("--workers", type=int, default=1)
ap.add_argument("--in-order", action="store_true")
ap.add_argument("--label", default="Claude Code")  # the chapter's narrator label in the app
args = ap.parse_args()
PID, ROOT, LABEL = args.pid, args.root, args.label
ONLY = {x for n in args.chapters for x in (n, f"Chapter {n}")}  # a number, or a chapter's whole name
COL_W, SHEET_H, COLS, GAP, BAND = 380, 1500, 4, 14, 28

ROOT.mkdir(parents=True, exist_ok=True)
for name in ("FINISHED", "STOPPED", "ERROR"):
    (ROOT / name).unlink(missing_ok=True)
for d in ROOT.glob("req_*"):  # a request left unanswered by an earlier run is asked again under a new number
    if d.is_dir() and not d.name.endswith("_stale") and not (d / "done").exists():
        d.rename(d.with_name(d.name + "_stale"))

_local = threading.local()  # the chapter this thread is writing
_lock = threading.Lock()
ENDED = {}   # --in-order: chapter id -> threading.Event, set when that chapter's thread ends
BEFORE = {}  # --in-order: chapter id -> the id of the chapter before it, when both are in this run
DRAFT_HEAD = ("The story so far isn't here yet: the chapter before this one is still being written. Read the pictures "
              "and draft your scenes now. When it's here, the wait helper prints READY with it, and it's added here.")


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "_", name)


def status(**fields):
    with _lock:
        path = ROOT / "status.json"
        s = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        s.update(fields, time=time.strftime("%Y-%m-%d %H:%M:%S"))
        path.write_text(json.dumps(s, indent=1), encoding="utf-8")


def tiles(n, img):
    """A piece scaled to the column width, cut to fit a sheet, each part under a label band."""
    img = cv2.resize(img, (COL_W, max(1, round(img.shape[0] * COL_W / img.shape[1]))), interpolation=cv2.INTER_AREA)
    step = SHEET_H - BAND
    parts = [img[y:y + step] for y in range(0, img.shape[0], step)]
    out = []
    for k, part in enumerate(parts):
        band = np.full((BAND, COL_W, 3), 255, np.uint8)
        label = f"P{n}" + (f" ({k + 1}/{len(parts)})" if len(parts) > 1 else "")
        cv2.putText(band, label, (4, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 200), 2, cv2.LINE_AA)
        cv2.line(band, (0, 0), (COL_W, 0), (0, 0, 200), 2)
        out.append((n, np.vstack([band, part])))
    return out


def sheets(pieces):
    """Pack the pieces' tiles top to bottom into columns, COLS columns per sheet: [(image, [piece numbers])]."""
    columns, col, h = [], [], 0
    for n, img in pieces:
        for t in tiles(n, img):
            if col and h + t[1].shape[0] > SHEET_H:
                columns.append(col)
                col, h = [], 0
            col.append(t)
            h += t[1].shape[0]
    if col:
        columns.append(col)
    out = []
    for i in range(0, len(columns), COLS):
        group = columns[i:i + COLS]
        height = max(sum(t[1].shape[0] for t in c) for c in group)
        canvas = np.full((height, len(group) * (COL_W + GAP) - GAP, 3), 90, np.uint8)
        nums = []
        for j, c in enumerate(group):
            y = 0
            for n, t in c:
                canvas[y:y + t.shape[0], j * (COL_W + GAP):j * (COL_W + GAP) + COL_W] = t
                y += t.shape[0]
                if n not in nums:
                    nums.append(n)
        out.append((canvas, nums))
    return out


def silence_problem(d, got) -> str | None:
    """A FIX for an answer whose lines are too short for their pictures: the scrolling video then passes the rest
    with no voice (scroll_time.py). Asked once per request; a second answer is taken as it is, and what's still
    silent goes in silences.txt."""
    if _local.fixed:  # the grouping is kept: no scenes to split
        return None
    order = sorted(got, key=lambda s: int(s["first_piece"]))
    found = scroll_time.silences(_local.project, _local.chapter_id, [
        (_local.ids[int(s["first_piece"]) - 1:int(s["last_piece"])], s["narration"], None) for s in order])
    if not found:
        return None

    def span(s):
        a, b = int(s["first_piece"]), int(s["last_piece"])
        return f"P{a}" + (f"-P{b}" if b > a else "")
    lines = []
    for f in found:
        sc = order[f["line"]]
        line = (f"- {span(sc)}: about {f['seconds']:.0f} s with no voice. These pieces take about {f['scroll']:.0f} s "
                f"to scroll past, and their line ({len(sc['narration'].split())} words) is read in about "
                f"{f['read']:.0f} s.")
        if f["quiet"]:
            line += f" After it, {', '.join(span(order[q]) for q in f['quiet'])} (left empty) scroll by too."
        lines.append(line)
    if (d / "silence_asked").exists():
        with _lock, open(ROOT / "silences.txt", "a", encoding="utf-8") as out:
            out.write(f"{_local.chapter} ({d.name}):\n" + "\n".join(lines) + "\n")
        return None
    (d / "silence_asked").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    return ("Long silences. The video scrolls down the strip and reads each scene's line as it passes, no faster "
            "than a set speed, so a line much shorter than its pictures leaves the rest scrolling by with no voice:\n"
            + "\n".join(lines) + "\nSplit those pieces into more scenes, each with its own line (a line of 20 to 35 "
            "words covers about 7 to 11 seconds of scrolling), or give a line more words (never over 45). Empty "
            "narration is still only for title and end cards, credits and blank pieces. In a pressured scene the "
            "extra lines carry what she thinks, says and feels, and the aftermath, never the acts. Then write "
            "answer.json again.")


def hand_over(params) -> str:
    """Save one request for an agent and wait for its answer (JSON text in claude.SCHEMA's shape)."""
    blocks = params["messages"][0]["content"]
    head, tail = blocks[0]["text"], blocks[-1]["text"]
    pieces, cur = {}, None
    for b in blocks[1:-1]:
        if b["type"] == "image":
            data = np.frombuffer(base64.b64decode(b["source"]["data"]), np.uint8)
            pieces[cur]["imgs"].append(cv2.imdecode(data, cv2.IMREAD_COLOR))
            continue
        m = re.match(r"Piece (\d+)", b["text"])
        if m:
            cur = int(m.group(1))
            pieces[cur] = {"imgs": [], "lines": []}
        else:
            pieces[cur]["lines"].append(b["text"])
    n = len(pieces)

    with _lock:
        k = 1 + max((int(d.name[4:8]) for d in ROOT.glob("req_*") if d.is_dir()), default=0)
        d = ROOT / f"req_{k:04d}"
        d.mkdir()
        (d / "chapter.txt").write_text(_local.chapter, encoding="utf-8")
        sys_path = ROOT / "system.md"
        system = params["system"]
        if not sys_path.exists() or sys_path.read_text(encoding="utf-8") != system:
            sys_path.write_text(system, encoding="utf-8")
    on_sheet = {}
    names = []
    for i, (img, nums) in enumerate(sheets([(i, np.vstack(p["imgs"])) for i, p in sorted(pieces.items())]), 1):
        name = f"sheet_{i:02d}.jpg"
        cv2.imwrite(str(d / name), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        names.append(f"{name} (P{nums[0]}-P{nums[-1]})")
        for x in nums:
            on_sheet.setdefault(x, i)
    where = re.search(r"pieces from (.*), in reading order\.", head)
    cover = "cover" in params["output_config"]["format"]["schema"]["properties"]  # a YouTube-safe project

    def write_prompt(head):
        lines = [f"# Request {k}: {where.group(1) if where else ''}", "",
                 f"Instructions (the system prompt): {sys_path}",
                 f"Pictures, in this folder: {', '.join(names)}. Each piece is labelled P<number> in red above it; "
                 "a tall piece continues in the next column.", "", "---", "", head, ""]
        for i, p in sorted(pieces.items()):
            lines.append(f"Piece {i} (sheet {on_sheet[i]}): " + " ".join(p["lines"]))
        lines += ["", tail, "", "---", "",
                  f"Answer: write answer.json in this folder, in the Output shape the instructions give: "
                  f'{{"scenes": [{{"first_piece": 1, "last_piece": 2, "narration": "..."}}, ...], "summary": "..."'
                  + (', "cover": [{"piece": 3, "left": 0.2, "top": 0.4, "right": 0.5, "bottom": 0.6}, ...]' if cover else "")
                  + f"}}. The scenes must cover pieces 1 to {n} in order, each piece exactly once."
                  + (' "cover" lists the black boxes (see Black boxes above); [] when nothing needs covering.' if cover else "")]
        (d / "prompt.md").write_text("\n".join(lines), encoding="utf-8")

    answer = d / "answer.json"
    before, _local.before = getattr(_local, "before", None), None  # (--in-order) only a chapter's first request waits
    if before is not None:
        task = head.rsplit("\n\n", 1)[-1]  # "Narrate the following N pieces from ...", after the story so far
        if not before.is_set():
            (d / "draft").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")  # before prompt.md
            write_prompt(f"{DRAFT_HEAD}\n\n{task}")
            status(**{_local.chapter: f"request {k} drafting since {time.strftime('%H:%M:%S')}"})
            while not before.wait(0.5):
                if (ROOT / "STOP").exists():
                    raise pipeline.Cancelled()
                if answer.exists():  # written before the story so far: kept for the agent, not taken
                    answer.replace(d / "answer.early.json")
        # the story so far as the chapter before left it (this thread loaded the project before it ended)
        context = pipeline._story_context(store.load(PID), {"id": _local.chapter_id})
        (d / "story_so_far.md").write_text(context or "This is the beginning of the story.", encoding="utf-8")
        head = (f"{context}\n\n" if context else "This is the beginning of the story.\n\n") + task  # as claude._content
    write_prompt(head)
    (d / "draft").unlink(missing_ok=True)
    status(**{_local.chapter: f"request {k} waiting since {time.strftime('%H:%M:%S')}"})

    while True:
        if (ROOT / "STOP").exists():
            raise pipeline.Cancelled()
        if answer.exists():
            try:
                time.sleep(0.3)  # let the writer finish the file
                raw = answer.read_text(encoding="utf-8")
                data = json.loads(raw)
                got = data.get("scenes")
                if not isinstance(got, list) or not got:
                    raise ValueError('"scenes" must be a non-empty list')
                if not isinstance(data.get("summary"), str) or not data["summary"].strip():
                    raise ValueError('"summary" must be a non-empty string')
                nxt = 1
                for s in sorted(got, key=lambda s: int(s["first_piece"])):
                    a, b = int(s["first_piece"]), int(s["last_piece"])
                    if not isinstance(s.get("narration"), str):
                        raise ValueError(f"scene {a}-{b}: narration must be a string")
                    if a != nxt or b < a:
                        raise ValueError(f"scenes must cover pieces 1-{n} in order, each once: expected a scene "
                                         f"starting at piece {nxt}, got {a}-{b}")
                    nxt = b + 1
                if nxt != n + 1:
                    raise ValueError(f"scenes end at piece {nxt - 1}, but there are {n} pieces")
                if cover:
                    if not isinstance(data.get("cover"), list):
                        raise ValueError('"cover" must be a list of black boxes ([] when nothing needs covering)')
                    for c in data["cover"]:
                        sides = [c.get(k) if isinstance(c, dict) else None for k in ("left", "top", "right", "bottom")]
                        if (not isinstance(c, dict) or not isinstance(c.get("piece"), int) or not 1 <= c["piece"] <= n
                                or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in sides)
                                or sides[0] >= sides[2] or sides[1] >= sides[3]):
                            raise ValueError(f"cover entry {c!r}: needs piece (1-{n}) and left < right, top < bottom, "
                                             "each a fraction from 0 to 1 of that piece")
                problem = silence_problem(d, got)
                if problem:
                    raise ValueError(problem)
                (d / "error.txt").unlink(missing_ok=True)
                (d / "done").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
                return raw
            except Exception as e:  # noqa: BLE001  (shown to the agent, which writes a corrected answer)
                (d / "error.txt").write_text(f"{type(e).__name__}: {e}", encoding="utf-8")
                answer.rename(d / f"answer.bad_{int(time.time())}.json")
        time.sleep(0.5)


class _Stream:
    def __init__(self, params):
        self.params = params

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        text = hand_over(self.params)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               model="claude-code-fork", usage=None)


API = SimpleNamespace(messages=SimpleNamespace(stream=lambda **params: _Stream(params)))
JOB = {"id": "fork", "project": PID, "step": "script", "only": None, "chapters": None, "mode": "missing",
       "cancel": False, "pause": False, "message": "", "progress": 0.0}


def write_chapter(chapter) -> bool:
    _local.chapter = chapter["name"]
    _local.before = ENDED.get(BEFORE.get(chapter["id"]))  # (--in-order) the chapter to wait for, if any
    for name in ("done_", "failed_"):
        (ROOT / (name + slug(chapter["name"]))).unlink(missing_ok=True)
    try:
        p = store.load(PID)  # earlier chapters' summaries and narration feed this one
        _local.project, _local.chapter_id = p, chapter["id"]  # (the strip's layout, for silence_problem)
        groups = [g for g in scenes.groups(p["panels"]) if g[0]["chapter"] == chapter["id"]]
        write = groups if args.redo else [g for g in groups if not pipeline.script_done(g[0])]
        if write:
            regroup = len(write) == len(groups)
            instructions = script.system_prompt(p["settings"], p["characters"], output=claude.OUTPUT_SCENES)

            def narrate(where, flat, context, fixed, scroll):
                _local.ids, _local.fixed = [x["id"] for x in flat], fixed is not None  # this request's pieces
                return claude.narrate("claude-code-fork", instructions, where, flat, context, fixed=fixed, api=API,
                                      scroll=scroll, cover=bool(p["settings"].get("youtube_safe")))
            pipeline._api_chapter(JOB, p, chapter, groups, None if regroup else write, LABEL, narrate)
        (ROOT / ("done_" + slug(chapter["name"]))).write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        status(**{chapter["name"]: "done"})
        return True
    except pipeline.Cancelled:
        raise
    except Exception:  # noqa: BLE001  (one chapter failing doesn't stop the others)
        (ROOT / ("failed_" + slug(chapter["name"]))).write_text(traceback.format_exc(), encoding="utf-8")
        status(**{chapter["name"]: "failed"})
        return False
    finally:
        if chapter["id"] in ENDED:
            ENDED[chapter["id"]].set()


def main():
    p = store.load(PID)
    chapters = [c for c in p["chapters"] if not ONLY or c["name"] in ONLY]
    unknown = sorted(set(args.chapters) - {n for c in p["chapters"] for n in (c["name"], c["name"].removeprefix("Chapter "))})
    if unknown:
        raise SystemExit(f"No chapter named {', '.join(unknown)} in this project")
    if args.new:  # chapters added to a narrated project: those with pieces, none of them narrated yet
        has = {x["chapter"] for x in p["panels"]}
        status(**{"not detected": [c["name"] for c in chapters if c["id"] not in has]})
        chapters = [c for c in chapters if c["id"] in has and not any(
            pipeline.script_done(g[0]) for g in scenes.groups(p["panels"]) if g[0]["chapter"] == c["id"])]
    if args.in_order:  # each chapter waits for the one before it, when that one is in this run too
        run = {c["id"] for c in chapters}
        ENDED.update({c: threading.Event() for c in run})
        BEFORE.update({b["id"]: a["id"] for a, b in zip(p["chapters"], p["chapters"][1:])
                       if a["id"] in run and b["id"] in run})
    status(started=time.strftime("%H:%M:%S"), chapters=len(chapters), redo=args.redo, workers=args.workers,
           in_order=args.in_order, names=[c["name"] for c in chapters],
           youtube_safe=bool(p["settings"].get("youtube_safe")))
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        results = list(ex.map(write_chapter, chapters))  # started in chapter order
    (ROOT / "FINISHED").write_text(f"{sum(results)} of {len(chapters)} chapters written", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except pipeline.Cancelled:
        (ROOT / "STOPPED").write_text("stopped on request (STOP file)", encoding="utf-8")
    except Exception:  # noqa: BLE001
        (ROOT / "ERROR").write_text(traceback.format_exc(), encoding="utf-8")
        raise
