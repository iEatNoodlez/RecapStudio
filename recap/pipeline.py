"""The processing steps, and a single background worker that runs them."""
import json
import math
import queue
import re
import shutil
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import time
import traceback
import uuid

import cv2
import soundfile as sf

import anthropic

from . import (charscan, claude, cloud, cover, credits, editors, estimates, focus, gpu, magi, naming, ocr,
               online_voices, panels, scenes, package, script, store, textless, tts, video, voiceover, wording,
               youtube)

STEPS = ["panels", "ocr", "characters", "script", "title", "audio", "video"]
STEP_LABELS = {
    "panels": "Detecting panels",
    "ocr": "Reading text",
    "characters": "Finding characters",
    "script": "Writing script",
    "title": "Suggesting a title",
    "audio": "Generating voice",
    "video": "Rendering video",
    "scan": "Scanning for characters",
    "editors": "Exporting for editors",
    "voiceover": "Splitting your recording",
    "package": "Saving the project as a file",
    "credits": "Finding credit pages",
    "names": "Suggesting names",
    "boxes": "Finding what to cover",
}
# run on their own, never part of Run all
EXTRA_STEPS = ["scan", "editors", "voiceover", "package", "credits", "names", "boxes"]

jobs: dict[str, dict] = {}
_queue: "queue.Queue[str]" = queue.Queue()
ACTIVE = ("queued", "running", "paused")  # a job in one of these holds its project


class Cancelled(Exception):
    pass


def submit(pid: str, steps: list[str], only: list[str] | None = None, chapters: list[str] | None = None,
           mode: str = "missing", options: dict | None = None) -> dict:
    """mode "missing" only does work that isn't done yet (Run all, or Continue);
    "redo" redoes the steps for every panel in scope. Selected panels (only) are always redone.
    options: for Write script with Claude Code from its MCP (claude_code.write_script): agents, workers."""
    steps = [s for s in STEPS + EXTRA_STEPS if s in steps]
    job = {
        "id": uuid.uuid4().hex[:10], "project": pid, "steps": steps, "only": only, "chapters": chapters,
        "mode": "redo" if mode == "redo" else "missing", "options": options or {},
        "status": "queued", "step": None, "progress": 0.0, "message": "", "error": None,
        "created": time.time(), "cancel": False, "pause": False, "pause_reason": None,
    }
    jobs[job["id"]] = job
    _queue.put(job["id"])
    return job


def latest_for(pid: str):
    mine = [j for j in jobs.values() if j["project"] == pid]
    return max(mine, key=lambda j: j["created"]) if mine else None


def public(job):
    return {k: v for k, v in job.items() if k != "cancel"} if job else None


def _worker():
    while True:
        job = jobs[_queue.get()]
        if job["cancel"]:
            job["status"] = "cancelled"
            continue
        job["status"] = "running"
        try:
            for step in job["steps"]:
                speed, amount = _start_step(job, step)
                RUNNERS[step](job)
                _learn_speed(job, speed, amount)
            job.update(status="done", progress=1.0,
                       message="Finished" + (f". {job['note']}" if job.get("note") else ""))
        except (Cancelled, InterruptedError):
            job.update(status="cancelled", message="Cancelled")
        except Exception as e:  # report any failure to the UI
            traceback.print_exc()
            job.update(status="error", error=str(e), message=f"{STEP_LABELS.get(job['step'], '')} failed")


def start_worker():
    threading.Thread(target=_worker, daemon=True, name="pipeline").start()


def _start_step(job, step):
    """Note when a step starts and how long it should take (the page shows time left from these)."""
    job.update(step=step, progress=0.0, message=STEP_LABELS[step], step_started=time.time(), paused_for=0.0,
               step_estimate=None, idle=False, join_time=0.0)
    try:
        p = store.load(job["project"])
        amount, _ = estimates.work(step, p, job["chapters"], missing=job["mode"] == "missing")
        speed = estimates.key(step, p["settings"])
        job["step_estimate"] = round(amount * estimates.seconds_per_unit(speed)
                                     + (estimates.join_seconds(p, job["chapters"]) if step == "video" else 0.0))
        return speed, amount
    except Exception:  # an estimate is a nicety; never let it stop the work
        traceback.print_exc()
        return None, 0


def _learn_speed(job, speed, amount):
    """After a step that did its whole share of work, remember how fast this computer did it (the video's
    drawing without the joining, which run_video learns on its own; Finding characters learns its scan's)."""
    if speed and not job["only"] and not job.get("idle") and job["step"] != "characters":
        estimates.learn(speed, amount, time.time() - job["step_started"] - job.get("paused_for", 0.0)
                        - job.get("join_time", 0.0))


def _check(job, until=None):
    """Called between pieces of work: stops a cancelled job, and holds a paused one right here."""
    if job.get("pause") and not job["cancel"]:
        _hold(job, until)
    if job["cancel"]:
        raise Cancelled()


def _hold(job, until=None):
    """Wait until Resume or Cancel (or until() says what it waits for is done, asked every 2 s). Nothing is
    lost: on Resume the job carries on from this point."""
    message, since, asked = job["message"], time.time(), time.time()
    job.update(status="paused", message=job.get("pause_reason") or f"Paused: {message}")
    while job.get("pause") and not job["cancel"]:
        time.sleep(0.3)
        if until and time.time() - asked >= 2:
            asked = time.time()
            if until():
                job["pause"] = False
    job.update(status="running", message=message, pause_reason=None,
               paused_for=job.get("paused_for", 0.0) + time.time() - since)  # not counted in time left


def pause_for(job, reason: str, until=None):
    """Pause because something outside the app needs fixing (credit, API key), until the user resumes, or
    until() says it's done."""
    job.update(pause=True, pause_reason=reason)
    _check(job, until)


def _wait(job, seconds: float, message: str):
    """Wait before trying again, showing a countdown; Pause and Cancel still work meanwhile."""
    end = time.time() + seconds
    while (left := end - time.time()) > 0:
        _check(job)
        job["message"] = message.format(seconds=math.ceil(left))
        time.sleep(min(0.5, left))


def _chapters(job, project):
    """Chapters this job covers, in project order (all when none were chosen)."""
    chosen = set(job["chapters"] or [])
    return [c for c in project["chapters"] if not chosen or c["id"] in chosen]


def _targets(job, project, credits=False):
    """Panels this job should touch: enabled ones in the job's chapters, or just the selected ones.
    credits: also the ones left out as the scan group's pages (Read text: their words tell the
    group's pages from the series' own cards, which are put back)."""
    only = set(job["only"] or [])
    chapters = {c["id"] for c in _chapters(job, project)}
    return [p for p in project["panels"]
            if (p.get("enabled", True) or credits and p.get("credits")) and p["chapter"] in chapters
            and (not only or p["id"] in only)]


def _skip_done(job) -> bool:
    return job["mode"] == "missing" and not job["only"]


def _rewrite(job) -> set:
    """Chapters whose script this job writes again from scratch even when it only does what's missing (Write the
    chapter again, while Voice waits for their wording to be checked)."""
    return set((job.get("options") or {}).get("rewrite") or [])


# what counts as already done, per panel (the interface mirrors these in app.js)
def ocr_done(panel) -> bool:
    return bool(panel.get("ocr_done"))


def script_done(panel) -> bool:
    return bool(panel.get("script_done") or panel.get("narration"))


def audio_done(panel, project_dir) -> bool:
    return bool(panel.get("audio")) and (project_dir / "audio" / panel["audio"]).exists()


def _nothing_to_do(job, what):
    job.update(progress=1.0, message=f"{STEP_LABELS[job['step']]}: {what}", idle=True)


# ---- steps -----------------------------------------------------------------

def run_panels(job):
    pid = job["project"]
    p = store.load(pid)
    d = store.project_dir(pid)
    s = p["settings"]
    chapters = _chapters(job, p)
    if _skip_done(job):  # only chapters with no panels yet, or whose panel images went missing
        has_panels = {x["chapter"] for x in p["panels"]}
        broken = {x["chapter"] for x in p["panels"] if not (d / "panels" / x["file"]).exists()}
        chapters = [c for c in chapters if c["id"] not in has_panels or c["id"] in broken]
        if not chapters:
            return _nothing_to_do(job, "every chapter already has panels")
    total_pages = sum(1 for x in p["pages"] if x["chapter"] in {c["id"] for c in chapters})
    pages_done = panel_count = 0

    for ci, chapter in enumerate(chapters):
        cid = chapter["id"]
        pages = [x for x in p["pages"] if x["chapter"] == cid]
        layout = s["layout"]
        if layout == "auto":
            layout = "webtoon" if panels.is_webtoon([(x["w"], x["h"]) for x in pages]) else "pages"
        found = []
        # new images get fresh file names, so the old panels stay intact until the switch-over
        run_tag = uuid.uuid4().hex[:6]

        def add(page_index, crop, joined=False):
            panel_id = f"{cid}_{len(found):05d}"
            name = f"{panel_id}_{run_tag}.jpg"
            panels.imwrite(d / "panels" / name, crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
            found.append({
                "id": panel_id, "chapter": cid, "page": page_index, "file": name, "joined": joined,
                "text": "", "narration": "", "audio": None, "duration": 0.0, "enabled": True,
            })

        def report(n):
            job["progress"] = (pages_done + n) / max(1, total_pages)
            job["message"] = (f"Detecting panels: {chapter['name']} ({ci + 1}/{len(chapters)}), "
                              f"{panel_count + len(found)} panels")

        try:
            if layout == "webtoon":
                prev_group = None
                for piece in _strip_pieces(p, pages, job, report):
                    # pieces of one detected panel start out joined into one scene
                    add(piece["page"], piece["crop"], joined=piece["group"] == prev_group)
                    found[-1]["y0"], found[-1]["y1"] = piece["y0"], piece["y1"]
                    prev_group = piece["group"]
            else:
                for pi, page in enumerate(pages):
                    _check(job)
                    img = panels.imread(store.page_path(p, page))
                    if img is not None:
                        for x, y, w, h in panels.detect(img, s["min_panel_frac"], s["reading_direction"] == "rtl"):
                            add(pi, img[y:y + h, x:x + w])
                            found[-1]["box"] = [int(x), int(y), int(w), int(h)]  # where it sits on its page
                    report(pi + 1)

            def apply(proj, cid=cid, found=found):
                proj["panels"] = [x for x in proj["panels"] if x["chapter"] != cid] + found
                proj["panels_rev"] = int(time.time())
                for c in proj["chapters"]:  # new pieces: Magi's speakers have to be found again
                    if c["id"] == cid:
                        c.pop("magi_done", None)
            store.update(pid, apply)
        except BaseException:
            # cancelled or failed partway: drop the half-made images, keep the old panels as they were
            for x in found:
                (d / "panels" / x["file"]).unlink(missing_ok=True)
            raise

        # switched over: now remove the chapter's old panel images and voice audio
        new_files = {x["file"] for x in found}
        for x in p["panels"]:
            if x["chapter"] == cid:
                if x["file"] not in new_files:
                    (d / "panels" / x["file"]).unlink(missing_ok=True)
                if x.get("audio"):
                    (d / "audio" / x["audio"]).unlink(missing_ok=True)
        pages_done += len(pages)
        panel_count += len(found)
    _leave_out_credits(job, pid, [c["id"] for c in chapters])


# Automatic worker counts (measured on an RTX 5060 Ti): text reading is about 2x faster
# with 4 workers and no faster beyond; the voice gains little past 2. Each worker holds
# its own copy of the model in video memory, roughly this many MiB.
AUTO_WORKERS = {"ocr": (4, 1600), "voice": (2, 1300)}


def _workers(settings, kind: str) -> int:
    """How many items to work on at once. 0 in settings means automatic: on the GPU, as
    many as fit in the video memory that's free right now (Ollama may be holding some);
    on the CPU just one, since each model already uses every core (measured: 2 workers
    made CPU text reading about 45% slower)."""
    n = int(settings.get("parallel_workers", 0) or 0)
    if n > 0:
        return n
    if not (settings.get("use_gpu", True) and gpu.available()):
        return 1
    most, per_worker = AUTO_WORKERS[kind]
    free = gpu.free_memory_mb()
    if free is None:
        return 2
    return max(1, min(most, (free - 500) // per_worker))


def _in_parallel(job, items, fn, workers):
    """Run fn on items, up to `workers` at a time, yielding (item, result) in the original
    order (so saving, progress and Continue behave as if it ran one by one). Cancelling
    stops new work; the few items already running finish first."""
    if workers <= 1:
        for item in items:
            _check(job)
            yield item, fn(item)
        return
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"{job.get('step', 'work')}-worker")
    pending: deque = deque()
    source = iter(items)

    def fill():
        while len(pending) < workers * 2:  # keep a few ready so the GPU never waits
            item = next(source, _END)
            if item is _END:
                return
            pending.append((item, pool.submit(fn, item)))

    try:
        fill()
        while pending:
            _check(job)
            item, future = pending.popleft()
            result = future.result()
            fill()
            yield item, result
    finally:
        for _, future in pending:
            future.cancel()
        pool.shutdown(wait=True)


_END = object()

STRIP_GROUP = 30  # webtoon pages stitched per detection pass, to bound memory use


def _strip_pieces(p, pages, job=None, report=None, with_groups=False):
    """Detect a webtoon chapter's pieces. Each gets page, crop, group (unique in the
    chapter) and y0/y1: its rows in the chapter strip, with every page scaled to the
    chapter's first page width (the strip the scrolling video shows)."""
    width = pages[0]["w"]
    out, groups, offset, gbase = [], [], 0.0, 0
    for g in range(0, len(pages), STRIP_GROUP):
        if job:
            _check(job)
        chunk = pages[g:g + STRIP_GROUP]
        imgs = [im for im in (panels.imread(store.page_path(p, x)) for x in chunk) if im is not None]
        if imgs:
            scale = width / imgs[0].shape[1]
            res = panels.detect_strip(imgs, with_groups=with_groups)
            pieces, found_groups = res if with_groups else (res, [])
            for page_offset, crop, gi, a, b in pieces:
                out.append({"page": g + page_offset, "crop": crop, "group": gbase + gi,
                            "y0": round(offset + a * scale), "y1": round(offset + b * scale)})
            for gi, a, b, shape in found_groups:
                groups.append({"group": gbase + gi, "y0": round(offset + a * scale),
                               "y1": round(offset + b * scale), "shape": shape})
            gbase += max([x[2] for x in pieces], default=-1) + 1
        offset += sum(x["h"] * width / x["w"] for x in chunk)
        if report:
            report(min(len(pages), g + STRIP_GROUP))
    return (out, groups) if with_groups else out


def recover_strip_positions(p, chapter) -> bool:
    """Give an already-detected webtoon chapter's panels their place in the strip.

    Detection is re-run (read-only) and matched to the stored panel images by order
    and size: first against today's fine pieces, then against whole panels as
    earlier versions cut them. Returns True when every panel got y0/y1."""
    from PIL import Image
    d = store.project_dir(p["id"])
    stored = [x for x in p["panels"] if x["chapter"] == chapter["id"]]
    pages = [x for x in p["pages"] if x["chapter"] == chapter["id"]]
    if not stored or not pages:
        return False
    sizes = []
    for x in stored:
        try:
            with Image.open(d / "panels" / x["file"]) as im:
                sizes.append((im.size[1], im.size[0]))
        except OSError:
            return False
    pieces, groups = _strip_pieces(p, pages, with_groups=True)
    for cand in ([(x["crop"].shape[:2], x["y0"], x["y1"]) for x in pieces],
                 [(tuple(x["shape"]), x["y0"], x["y1"]) for x in groups]):
        if len(cand) == len(stored) and all(tuple(c[0]) == s for c, s in zip(cand, sizes)):
            ranges = {x["id"]: (c[1], c[2]) for x, c in zip(stored, cand)}

            def apply(proj):
                for x in proj["panels"]:
                    if x["id"] in ranges:
                        x["y0"], x["y1"] = ranges[x["id"]]
            store.update(p["id"], apply)
            for x in stored:
                x["y0"], x["y1"] = ranges[x["id"]]
            return True
    return False


def run_ocr(job):
    pid = job["project"]
    p = store.load(pid)
    d = store.project_dir(pid)
    targets = _targets(job, p, credits=True)
    magi_chapters = _magi_chapters(job, p)
    if _skip_done(job):  # detecting panels again resets this
        targets = [x for x in targets if not ocr_done(x)]
        if not targets and not magi_chapters:
            return _nothing_to_do(job, "all panels already read")
    texts = {}
    missing = []  # panels whose image file is gone: left unread so they show up as not done
    use_gpu = p["settings"].get("use_gpu", True)

    english = ocr.use_english(p["settings"])  # the English model, when downloaded and switched on

    def read(panel):
        img = panels.imread(d / "panels" / panel["file"])
        return None if img is None else ocr.read_text(img, use_gpu=use_gpu, english=english)

    for i, (panel, text) in enumerate(_in_parallel(job, targets, read, _workers(p["settings"], "ocr"))):
        if text is None:
            missing.append(panel)
        else:
            texts[panel["id"]] = text
        job["progress"] = (i + 1) / max(1, len(targets))
        job["message"] = f"Reading text: panel {i + 1}/{len(targets)}"
        if (i + 1) % 20 == 0 or i + 1 == len(targets):
            _merge(pid, "text", dict(texts), mark="ocr_done")
            texts.clear()
    if missing:
        names = {c["id"]: c["name"] for c in p["chapters"]}
        where = ", ".join(dict.fromkeys(names.get(x["chapter"], "?") for x in missing))
        raise RuntimeError(f"{len(missing)} panel images are missing ({where}). "
                           "Tick those chapters and run Detect panels again, then Read text.")
    if magi_chapters:
        _run_magi(job, pid, magi_chapters)
    _leave_out_credits(job, pid, list({x["chapter"] for x in targets}))  # the text can find more of them


# ---- the scan group's pages at the start and end of chapters (see credits.py) ----------

def _leave_out_credits(job, pid, chapter_ids, always=False) -> str:
    """Leave out the credits, notices and ads at either end of these chapters (Project settings: on by
    default, or always for Find them now). Returns a note on what was left out."""
    p = store.load(pid)
    if not chapter_ids or not (always or p["settings"].get("leave_out_credits", True)):
        return ""
    job["message"] = "Finding the scan group's pages at the start and end of chapters"
    found = credits.find(p, store.project_dir(pid), chapter_ids, cancelled=lambda: job["cancel"])
    result = {}
    store.update(pid, lambda q: result.update(n=credits.apply(q, found)))
    hit = sum(1 for ids in found.values() if ids)
    note = (f"Left out {result['n']} pieces at the start or end of {hit} chapter{'s' if hit != 1 else ''} "
            "(the scan group's credits, notices and ads)" if result["n"] else "")
    if note:
        job["note"] = ". ".join(x for x in (job.get("note"), note) if x)
    return note


def run_credits(job):
    """Find them now (Project settings): the credits, notices and ads in the job's chapters."""
    pid = job["project"]
    p = store.load(pid)
    chapters = [c["id"] for c in _chapters(job, p) if any(x["chapter"] == c["id"] for x in p["panels"])]
    if not chapters:
        return _nothing_to_do(job, "detect panels first")
    note = _leave_out_credits(job, pid, chapters, always=True)
    job["note"] = note or "No credits or notices found at the start or end of these chapters"


def run_boxes(job):
    """Find boxes now (the Video tab → YouTube): the box finder looks at the pieces used in the job's chapters
    that it hasn't looked at yet, or at all of them again (Find again: its earlier boxes are replaced, yours
    and the writer's stay). See cover.py."""
    pid = job["project"]
    p = store.load(pid)
    if not cover.installed():
        raise RuntimeError("Download the box finder first: Settings → Video → Black boxes")
    looked, boxed = _find_boxes(job, p, _chapters(job, p), redo=job["mode"] == "redo")
    if not looked:
        return _nothing_to_do(job, "the box finder has looked at every piece used in these chapters")
    job["note"] = f"Looked at {looked} piece{'s' if looked != 1 else ''}; {boxed} need{'s' if boxed == 1 else ''} covering"


def _find_boxes(job, p, chapters, redo=False, doing="Finding what to cover") -> tuple[int, int]:
    """Run the box finder on the pieces used in chapters (the ones it hasn't looked at, or all with redo),
    saving chapter by chapter (a Cancel keeps what's done). A webtoon chapter's pieces are searched for along
    its strip (cover.find_strip), and keep where they sit in it; others one by one.
    Returns (pieces looked at, pieces given boxes)."""
    d = store.project_dir(p["id"])
    todo = {}
    for x in p["panels"]:
        if x.get("enabled", True) and (redo or x.get("cover_checked") != cover.VERSION):
            todo.setdefault(x["chapter"], []).append(x)
    work = [(c, todo[c["id"]]) for c in chapters if c["id"] in todo]
    total, done, boxed = sum(len(xs) for _, xs in work), 0, 0
    for c, pieces in work:
        _check(job)
        mine = [x for x in p["panels"] if x["chapter"] == c["id"]]
        pages = [x for x in p["pages"] if x["chapter"] == c["id"]]

        def report(k, n, c=c, before=done, count=len(pieces)):
            job["progress"] = (before + count * k / max(1, n)) / max(1, total)
            job["message"] = f"{doing}: {c['name']} ({min(before + round(count * k / max(1, n)), total)}/{total} pieces)"
            _check(job)
        at = {}
        if pages and all("y0" in x for x in mine):
            found, at = cover.find_strip(p, pages, pieces, STRIP_GROUP, progress=report)
        else:
            found = {}
            for k, x in enumerate(pieces, 1):
                img = panels.imread(d / "panels" / x["file"])
                found[x["id"]] = cover.find(img) if img is not None else []
                report(k, len(pieces))

        def apply(proj, found=found, at=at):
            for x in proj["panels"]:
                if x["id"] in found:
                    cover.set_boxes(x, "auto", found[x["id"]])
                if x["id"] in at:
                    x["at"] = at[x["id"]]
        store.update(p["id"], apply)
        done += len(pieces)
        boxed += sum(1 for v in found.values() if v)
    return total, boxed


# ---- Magi (optional add-on): who says each line --------------------------------------

def _magi_chapters(job, p) -> list[dict]:
    """Chapters for Magi in this Read text job: none unless it's installed and switched on."""
    if job["only"] or not p["settings"].get("use_magi") or not magi.installed():
        return []
    chapters = [c for c in _chapters(job, p) if any(x["chapter"] == c["id"] for x in p["panels"])]
    return [c for c in chapters if not c.get("magi_done")] if _skip_done(job) else chapters


def _magi_bank(p) -> dict:
    """The character pictures Magi matches the people it finds against, each with its name."""
    d = store.project_dir(p["id"]) / "characters"
    bank = {"images": [], "names": []}
    for c in p["characters"]:
        for n in [n for x in [c, *(c.get("looks") or [])] for n in x.get("pictures") or []]:
            if c.get("name", "").strip() and (d / n).is_file():  # other looks count as the character
                bank["images"].append(str(d / n))
                bank["names"].append(c["name"].strip())
    return bank


def _run_magi(job, pid, chapters):
    """Ask Magi who says each line; its guesses go on the pieces as "speakers" for the script writers."""
    bank = _magi_bank(store.load(pid))
    if not bank["images"]:
        job["note"] = ("Magi was skipped: add pictures to your characters (the Characters tab) "
                       "so it can tell who is who.")
        return
    tmp = store.project_dir(pid) / "magi_tmp"
    job.update(progress=0.0, message="Finding speakers: starting Magi")
    session = magi.Session(lambda: _check(job))
    try:
        for ci, chapter in enumerate(chapters):
            _check(job)
            p = store.load(pid)
            pieces = [x for x in p["panels"] if x["chapter"] == chapter["id"]]
            pages = [x for x in p["pages"] if x["chapter"] == chapter["id"]]
            job["progress"] = ci / len(chapters)
            job["message"] = f"Finding speakers with Magi: {chapter['name']} ({ci + 1}/{len(chapters)})"
            shutil.rmtree(tmp, ignore_errors=True)
            tmp.mkdir()
            webtoon = _is_webtoon(p["settings"], pages) and all("y0" in x for x in pieces)
            if webtoon:  # Magi expects pages: cut the strip into page-sized chunks between panels
                strip = magi.Strip(p, pages)
                spans = magi.strip_chunks(strip, sorted(pieces, key=lambda x: x["y0"]))
                files = []
                for k, (_, _, y0, y1) in enumerate(spans):
                    files.append(str(tmp / f"{k:04d}.png"))
                    panels.imwrite(files[-1], strip.rows(y0, y1))
            else:
                files = [str(store.page_path(p, pg)) for pg in pages]
            lines = []
            for k, found in enumerate(session.chapter(files, bank)):
                for ln in found:
                    x0, y0, x1, y1 = ln["box"]
                    if webtoon:
                        core0, core1, top, _ = spans[k]
                        y = top + (y0 + y1) / 2
                        if core0 <= y < core1:  # outside the core it's the neighbouring chunk's line
                            lines.append({**ln, "y": y})
                    else:
                        lines.append({**ln, "page": k, "center": ((x0 + x1) / 2, (y0 + y1) / 2)})
            placed = magi.place_lines(pieces, lines)

            def apply(proj, cid=chapter["id"], placed=placed):
                for x in proj["panels"]:
                    if x["chapter"] == cid:
                        x["speakers"] = placed.get(x["id"], [])
                for c in proj["chapters"]:
                    if c["id"] == cid:
                        c["magi_done"] = True
            store.update(pid, apply)
    finally:
        session.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _scene_targets(job, project) -> list[list[dict]]:
    """Scenes to narrate: those in the job's chapters, or the ones holding selected panels."""
    only = set(job["only"] or [])
    chapters = {c["id"] for c in _chapters(job, project)}
    out = [g for g in scenes.groups(project["panels"])
           if g[0]["chapter"] in chapters and (not only or any(x["id"] in only for x in g))]
    if _skip_done(job):
        rewrite = _rewrite(job)
        out = [g for g in out if not script_done(g[0]) or g[0]["chapter"] in rewrite]
    return out


def _scene_image(d, group):
    return scenes.stack([panels.imread(d / "panels" / x["file"]) for x in group])


def _scene_text(group) -> str:
    text = "\n".join(x["text"] for x in group if x.get("text"))
    guesses = "; ".join(g for g in (magi.describe(x.get("speakers")) for x in group) if g)
    return text + (f"\n(Automatic speaker guesses, often wrong; trust the image: {guesses})" if guesses else "")


def run_script(job):
    p = store.load(job["project"])
    if p["settings"].get("llm_provider") == "claude_code" or job.get("options", {}).get("agents"):
        from . import claude_code  # Claude Code's agents, on the user's Claude plan
        return claude_code.write_script(job, p)
    if p["settings"].get("llm_provider") == "claude":
        return _script_claude(job, p)
    if p["settings"].get("llm_provider") in cloud.PROVIDERS:
        return _script_cloud(job, p, p["settings"]["llm_provider"])
    targets = _scene_targets(job, p)
    if not targets:
        return _nothing_to_do(job, "all scenes already have narration")
    _script_ollama(job, p, targets)


def _script_ollama(job, p, targets, label=None):
    """Narrate scenes with the local Ollama model, a few at a time."""
    pid, s = p["id"], p["settings"]
    d = store.project_dir(pid)
    url, model = s["ollama_url"], s["ollama_model"]
    if not model:
        models = script.list_models(url)
        if not models:
            raise RuntimeError("No Ollama models installed. Run e.g. 'ollama pull gemma3' first.")
        model = models[0]["name"]
    caps = script.capabilities(url, model)
    use_vision = bool(s["use_vision"]) and "vision" in caps
    think = "thinking" in caps
    system = script.system_prompt(s, p["characters"])
    markers = script.chapter_markers(p)
    # context: narration written before the first scene (panels are kept in story order)
    order = {x["id"]: i for i, x in enumerate(p["panels"])}
    first = order[targets[0][0]["id"]] if targets else 0
    history = [x["narration"] for x in p["panels"][:first] if x["narration"]][-6:]
    size = max(1, int(s["panels_per_batch"]))
    done = 0
    for b in range(0, len(targets), size):
        _check(job)
        chunk = targets[b:b + size]
        batch = []
        for n, group in enumerate(chunk, start=1):
            img = _scene_image(d, group) if use_vision else None
            batch.append((n, img, _scene_text(group), markers.get(group[0]["id"], "")))
        result = script.narrate_batch(
            url, model, system, batch, "\n".join(history), use_vision, think, float(s["temperature"])
        )
        lines = {group[0]["id"]: result.get(n, "") for n, group in enumerate(chunk, start=1)}
        _merge(pid, "narration", lines, clear_audio=True, mark="script_done")
        history.extend(t for t in lines.values() if t)
        history = history[-6:]
        done += len(chunk)
        job["progress"] = done / max(1, len(targets))
        job["message"] = f"Writing script with {label or model}: scene {done}/{len(targets)}"


# ---- Claude: a whole chapter per request ----------------------------------------

def _story_context(p, chapter) -> str:
    """Summary of the story before this chapter, plus its most recent narration lines."""
    ids = [c["id"] for c in p["chapters"]]
    idx = ids.index(chapter["id"])
    summary = next((c.get("summary") for c in reversed(p["chapters"][:idx]) if c.get("summary")), "")
    before = set(ids[:idx])
    recent = [x["narration"] for x in p["panels"] if x["chapter"] in before and x.get("narration")][-4:]
    parts = []
    if summary:
        parts.append(f"Story so far (background notes): {summary}")
    if recent:
        parts.append("Most recent narration:\n" + "\n".join(recent))
    return "\n\n".join(parts)


def _script_claude(job, p):
    pid, s = p["id"], p["settings"]
    model = s.get("claude_model") or "claude-haiku-5-5"
    label = dict(claude.MODELS).get(model, model)
    instructions = script.system_prompt(s, p["characters"], output=claude.OUTPUT_SCENES)
    only = set(job["only"] or [])
    api = _claude_api(job)
    chapters = _chapters(job, p)
    worked = False
    for ci, chapter in enumerate(chapters):
        tries = 0
        while True:  # one pass per attempt at this chapter; a wait or pause sends it round again
            _check(job)
            p = store.load(pid)  # earlier chapters' summaries and narration feed this one
            groups = [g for g in scenes.groups(p["panels"]) if g[0]["chapter"] == chapter["id"]]
            if only:
                write = [g for g in groups if any(x["id"] in only for x in g)]
            elif job["mode"] == "redo" or chapter["id"] in _rewrite(job):
                write = groups
            else:
                write = [g for g in groups if not script_done(g[0])]
            if not write:
                break
            worked = True
            # a fresh chapter (or a redo) lets Claude regroup; otherwise keep the grouping and fill gaps
            regroup = not only and len(write) == len(groups)
            job["progress"] = ci / len(chapters)
            job["message"] = f"Writing script with {label}: {chapter['name']} ({ci + 1}/{len(chapters)})"
            def ask(effort=None):
                return lambda where, flat, context, fixed, scroll: claude.narrate(
                    model, instructions, where, flat, context, fixed=fixed, api=api, scroll=scroll, effort=effort,
                    cover=bool(s.get("youtube_safe")))
            try:
                try:
                    _api_chapter(job, p, chapter, groups, None if regroup else write, label, ask())
                except (claude.CutOff, anthropic.APITimeoutError):
                    # Claude ran too long (usually thinking about a big chapter): once more, thinking less
                    job["message"] = f"Writing script with {label}: {chapter['name']} (retrying, thinking less)"
                    _api_chapter(job, p, chapter, groups, None if regroup else write, label, ask("low"))
            except (claude.CutOff, anthropic.APITimeoutError) as e:
                _claude_fallback(job, p, chapter, write, f"Claude ran too long twice: {e}")
            except anthropic.APIError as e:
                problem = _claude_problem(e)
                if problem == "busy" and tries < len(RETRY_WAITS):
                    _wait(job, RETRY_WAITS[tries],
                          f"Claude is busy ({type(e).__name__}); trying {chapter['name']} again in {{seconds}} s")
                    tries += 1
                    continue
                if problem:
                    pause_for(job, PAUSE_REASONS[problem].format(chapter=chapter["name"], error=type(e).__name__))
                    tries = 0
                    api = _claude_api(job)  # the key may have been changed while paused
                    continue
                if not isinstance(e, anthropic.BadRequestError):
                    raise
                _claude_fallback(job, p, chapter, write, f"Claude rejected the request: {e.message}")
            except (claude.Declined, ValueError, KeyError) as e:
                _claude_fallback(job, p, chapter, write, f"{type(e).__name__}: {e}")
            break
    if not worked:
        _nothing_to_do(job, "all scenes already have narration")
    elif note := wording_note(pid, [c["id"] for c in chapters]):
        job["note"] = ". ".join(x for x in (job.get("note"), note) if x)


RETRY_WAITS = (30, 120, 300)  # seconds to wait before trying again while Claude is busy or unreachable
PAUSE_REASONS = {
    "credit": ("Paused: your Anthropic account is out of credit. Add credit at console.anthropic.com "
               "(Settings → Billing), then press Resume to carry on from {chapter}."),
    "key": ("Paused: Claude didn't accept the API key, or none is saved. Fix it in Settings → Script "
            "writer, then press Resume to carry on from {chapter}."),
    "busy": "Paused: Claude is still unavailable after several tries ({error}). Press Resume to try {chapter} again.",
}
CLOUD_PAUSE = {  # the same for every service in cloud.py
    "credit": ("Paused: your {service} account is out of credit. Add credit at {billing}, then press Resume to "
               "carry on from {chapter}."),
    "key": ("Paused: {service} didn't accept the API key, or none is saved. Fix it in Settings → Script writer, "
            "then press Resume to carry on from {chapter}."),
    "busy": ("Paused: {service} is still unavailable after several tries ({error}). Press Resume to try {chapter} "
             "again, or choose another model in Settings → Script writer first."),
    "model": "Paused: choose which {service} model to use in Settings → Script writer, then press Resume.",
    "quota": ("Paused: {service} has no allowance left for {model} on this key ({error}). Check your plan, billing "
              "or limits at {billing}, or choose another model in Settings → Script writer, then press Resume."),
    "unavailable": ("Paused: {service} can't use the model {model} with this key ({error}). Choose another model in "
                    "Settings → Script writer, then press Resume."),
}


def _claude_problem(e: Exception) -> str | None:
    """'credit', 'key' or 'busy' for API errors that adding credit, fixing the key or waiting can fix."""
    if isinstance(e, anthropic.APIStatusError) and "credit" in str(e).lower():
        return "credit"
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return "key"
    if isinstance(e, (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError,
                      anthropic.OverloadedError, anthropic.ServiceUnavailableError,
                      anthropic.DeadlineExceededError)):
        return "busy"
    return None


def _claude_api(job):
    """A client with the saved key; with no key at all, pause until one is added."""
    while not any(claude.key_status().values()):  # saved in Settings, or ANTHROPIC_API_KEY
        pause_for(job, PAUSE_REASONS["key"].format(chapter="where it stopped"))
    return claude.client()


def _claude_fallback(job, p, chapter, write, reason, who="Claude"):
    """The online writer couldn't narrate this chapter: write it with the local model instead."""
    traceback.print_exc()
    p = store.load(p["id"])
    ids = {g[0]["id"] for g in write}
    targets = [g for g in scenes.groups(p["panels"]) if g[0]["id"] in ids]
    _script_ollama(job, p, targets, label=f"the local model ({chapter['name']}: {who} unavailable)")
    _set_chapter(p["id"], chapter["id"], narrator=f"local model (fallback): {reason[:200]}")


def _script_cloud(job, p, provider):
    """Narrate whole chapters with OpenRouter, Google Gemini, DeepSeek, Groq or Mistral (see cloud.py), like Claude:
    busy services are waited for, then the job pauses; key or credit problems pause it straight away;
    a chapter the service can't do is written by the local model."""
    info = cloud.PROVIDERS[provider]
    service, pid = info["name"], p["id"]
    only = set(job["only"] or [])
    chapters = _chapters(job, p)
    worked = False
    for ci, chapter in enumerate(chapters):
        tries = 0
        while True:  # one pass per attempt at this chapter; a wait or pause sends it round again
            _check(job)
            p = store.load(pid)  # earlier chapters' summaries feed this one; settings may change while paused
            s = p["settings"]
            model = s.get(f"{provider}_model") or info["default_model"]
            if not model:
                pause_for(job, CLOUD_PAUSE["model"].format(service=service))
                continue
            if not cloud.has_key(provider):
                pause_for(job, CLOUD_PAUSE["key"].format(service=service, chapter=chapter["name"]))
                continue
            groups = [g for g in scenes.groups(p["panels"]) if g[0]["chapter"] == chapter["id"]]
            if only:
                write = [g for g in groups if any(x["id"] in only for x in g)]
            elif job["mode"] == "redo" or chapter["id"] in _rewrite(job):
                write = groups
            else:
                write = [g for g in groups if not script_done(g[0])]
            if not write:
                break
            worked = True
            regroup = not only and len(write) == len(groups)
            label = f"{service} ({model})"
            instructions = script.system_prompt(s, p["characters"], output=claude.OUTPUT_SCENES)
            job["progress"] = ci / len(chapters)
            job["message"] = f"Writing script with {label}: {chapter['name']} ({ci + 1}/{len(chapters)})"
            try:
                _api_chapter(job, p, chapter, groups, None if regroup else write, label,
                             lambda where, flat, context, fixed, scroll: cloud.narrate(
                                 provider, model, instructions, where, flat, context, fixed, scroll,
                                 cover=bool(s.get("youtube_safe"))),
                             budget=info.get("part_tokens", claude.PART_TOKENS), max_images=info.get("max_images"))
            except cloud.Problem as e:
                if e.kind == "busy" and tries < len(RETRY_WAITS):
                    _wait(job, RETRY_WAITS[tries], f"{service} is busy; trying {chapter['name']} again in {{seconds}} s")
                    tries += 1
                    continue
                if e.kind in ("credit", "key", "busy", "quota", "model"):
                    reason = CLOUD_PAUSE["unavailable" if e.kind == "model" else e.kind]
                    pause_for(job, reason.format(service=service, billing=info["billing"], chapter=chapter["name"],
                                                 model=model, error=str(e)[:120]))
                    tries = 0
                    continue
                _claude_fallback(job, p, chapter, write, f"{service}: {e}", who=service)
            except (claude.CutOff, claude.Declined, ValueError, KeyError) as e:
                _claude_fallback(job, p, chapter, write, f"{type(e).__name__}: {e}", who=service)
            break
    if not worked:
        _nothing_to_do(job, "all scenes already have narration")
    elif note := wording_note(pid, [c["id"] for c in chapters]):
        job["note"] = ". ".join(x for x in (job.get("note"), note) if x)


def _set_chapter(pid, cid, **fields):
    def apply(proj):
        for c in proj["chapters"]:
            if c["id"] == cid:
                c.update(fields)
    store.update(pid, apply)


def _api_chapter(job, p, chapter, groups, write, label, narrate, budget=claude.PART_TOKENS, max_images=None):
    """Send a chapter (split into parts if it is very long) to an online writer (Claude or another
    service) and apply the scenes it returns.

    write is None to let the writer regroup all pieces, or the scenes whose narration is missing.
    narrate(where, pieces, context, fixed, scroll) -> (scenes, summary), as claude.narrate; for a YouTube-safe
    project (scenes, summary, black boxes), and the boxes replace the writer's earlier ones on those pieces.
    budget: input tokens per request; max_images: pictures per request, for services that limit them.
    """
    pid = p["id"]
    d = store.project_dir(pid)
    write_heads = {g[0]["id"] for g in write} if write is not None else None
    pages = [x for x in p["pages"] if x["chapter"] == chapter["id"]]
    scroll = _scrolls(p["settings"], pages)
    items = []  # one entry per scene: its pieces with images and text
    for g in groups:
        pieces = []
        for x in g:
            img = panels.imread(d / "panels" / x["file"])
            if img is None:
                raise ValueError(f"panel image missing: {x['file']}")
            guesses = magi.describe(x.get("speakers"))  # Magi's, when it has run on this chapter
            pieces.append({"id": x["id"], "img": img, "text": x.get("text", ""), "speakers": guesses,
                           "tokens": claude.piece_tokens(img, x.get("text", "") + guesses),
                           "images": claude.piece_images(img)})
        items.append(pieces)
    if write is None:  # the writer regroups the pieces anyway, so a scene too big for one request is cut
        units = []
        for sc in items:
            chunk, t, n = [], 0, 0
            for x in sc:
                if chunk and (t + x["tokens"] > budget or (max_images and n + x["images"] > max_images)):
                    units.append(chunk)
                    chunk, t, n = [], 0, 0
                chunk.append(x)
                t += x["tokens"]
                n += x["images"]
            units.append(chunk)
        items = units
    # parts never split a scene: pack whole scenes up to the token budget (and the picture limit)
    parts, cur, used, pics = [], [], 0, 0
    for scene_pieces in items:
        t = sum(x["tokens"] for x in scene_pieces)
        n = sum(x["images"] for x in scene_pieces)
        if cur and (used + t > budget or (max_images and pics + n > max_images)):
            parts.append(cur)
            cur, used, pics = [], 0, 0
        cur.append(scene_pieces)
        used += t
        pics += n
    if cur:
        parts.append(cur)

    context = _story_context(p, chapter)
    summary = ""
    for k, part in enumerate(parts, 1):
        _check(job)
        flat = [x for sc in part for x in sc]
        fixed = None
        if write_heads is not None:
            fixed, n = [], 1
            heads = {x["id"]: x for x in p["panels"]}
            for sc in part:
                head = heads[sc[0]["id"]]
                fixed.append({"first": n, "last": n + len(sc) - 1, "narration": head.get("narration", ""),
                              "write": sc[0]["id"] in write_heads})
                n += len(sc)
        name = chapter["name"] + (f" (part {k} of {len(parts)})" if len(parts) > 1 else "")
        if len(parts) > 1:
            job["message"] = f"Writing script with {label}: {name}"
        series = p["name"].strip()  # the title is a strong hint about the story; "Untitled" is none
        where = f"{name} of {json.dumps(series)}" if series and series != "Untitled" else name
        result, summary, *marked = narrate(where, flat, context, fixed, scroll)
        ids = [x["id"] for x in flat]

        def apply(proj, result=result, ids=ids, fixed=fixed, marked=marked):
            by_id = {x["id"]: x for x in proj["panels"]}
            if marked:
                for k, pid_ in enumerate(ids, 1):
                    cover.set_boxes(by_id[pid_], "claude", [box for n, box in marked[0] if n == k])
            for first, last, narration in result:
                members = ids[first - 1:last]
                if fixed is not None and not next(sc for sc in fixed if sc["first"] == first)["write"]:
                    continue
                for j, pid_ in enumerate(members):
                    x = by_id[pid_]
                    x["joined"] = j > 0
                    x["narration"] = narration if j == 0 else ""
                    x["script_done"] = True
                    x["audio"], x["duration"] = None, 0.0
        store.update(pid, apply)
        recent = [n for _, _, n in result if n][-4:]
        context = (f"Story so far (background notes): {summary}\n\n" if summary else "") + (
            "Most recent narration:\n" + "\n".join(recent) if recent else "")
    _set_chapter(pid, chapter["id"], summary=summary, narrator=label)
    _check_wording(pid, chapter["id"])


def _check_wording(pid, cid):
    """After a YouTube-safe chapter is written: keep what wording.find flags in it on the chapter (Check wording in
    the chapter list), leaving out lines the user chose to keep."""
    def apply(proj):
        c = next(c for c in proj["chapters"] if c["id"] == cid)
        lines = [g[0].get("narration") or "" for g in scenes.groups(proj["panels"]) if g[0]["chapter"] == cid]
        kept = set(c.get("wording_kept") or [])
        found = [{k: f[k] for k in ("why", "word", "line")} for f in wording.find(lines) if f["line"] not in kept]
        if found and proj["settings"].get("youtube_safe"):
            c["wording"] = found
        else:
            c.pop("wording", None)
    store.update(pid, apply)


def wording_flags(p, c) -> list[dict]:
    """The chapter's flagged lines (_check_wording) that still read the same: an edited line drops out."""
    lines = {g[0].get("narration") for g in scenes.groups(p["panels"]) if g[0]["chapter"] == c["id"]}
    return [w for w in c.get("wording") or [] if w["line"] in lines]


def wording_note(pid, chapter_ids) -> str:
    """For a script job's note: the chapters whose wording to check."""
    p = store.load(pid)
    flagged = [(c["name"], len(wording_flags(p, c))) for c in p["chapters"] if c["id"] in chapter_ids]
    flagged = [(name, n) for name, n in flagged if n]
    if not flagged:
        return ""
    return ("Check the wording of " + ", ".join(f"{name} ({n} line{'s' if n != 1 else ''})" for name, n in flagged)
            + ": it may go further than YouTube-safe allows (Check wording, in the Chapters tab)")


VOICE_PAUSE = {  # online voices (see online_voices.Problem): what to fix before Resume
    "key": "Paused: {service} needs a working API key (Settings → Voice). Add it, then Resume. ({error})",
    "quota": "Paused: your {service} plan has no characters left. Add more, or switch to another voice in "
             "Settings → Voice, then Resume. ({error})",
    "voice": "Paused: {service} can't use the chosen voice. Pick another in Settings → Voice, then Resume. ({error})",
    "busy": "Paused: {service} isn't answering (tried a few times). Check the internet connection, then Resume. "
            "({error})",
    "bad": "Paused: {service} wouldn't voice a line. Edit its narration or switch voices, then Resume. ({error})",
}


def _voice_targets(job, p, d):
    """The panels Voice would voice in this job, and those voiced from your own recording (never replaced)."""
    targets = _targets(job, p)
    recorded = [x for x in targets if from_recording(x, d)]
    targets = [x for x in targets if not from_recording(x, d)]
    if _skip_done(job):  # lines that have narration but no voice yet
        targets = [x for x in targets if x.get("narration", "").strip() and not audio_done(x, d)]
    return targets, recorded


def _wording_waiting(job) -> list[str]:
    """The chapters (with how many lines) whose lines flagged by the wording check this Voice job would voice."""
    p = store.load(job["project"])
    if not p["settings"].get("youtube_safe"):
        return []
    targets, _ = _voice_targets(job, p, store.project_dir(p["id"]))
    out = []
    for c in _chapters(job, p):
        lines = {w["line"] for w in wording_flags(p, c)}
        n = sum(1 for x in targets if x["chapter"] == c["id"] and x.get("narration") in lines)
        if n:
            out.append(f"{c['name']} ({n} line{'s' if n != 1 else ''})")
    return out


def _wording_hold(job):
    """Before voicing a YouTube-safe project, wait while chapters have lines the wording check flagged: until
    each is checked (lines edited, kept as they are, or the chapter written again) or the user presses Resume,
    which voices them as they are."""
    waiting = _wording_waiting(job)
    if not waiting:
        return
    job["waiting_for"] = "wording"
    try:
        pause_for(job, f"Paused before Voice: {', '.join(waiting)} may go further than YouTube-safe allows. "
                       "Check the wording and Voice carries on, or Resume to voice it as it is.",
                  until=lambda: not _wording_waiting(job))
    finally:
        job.pop("waiting_for", None)


def run_audio(job):
    pid = job["project"]
    _wording_hold(job)
    p = store.load(pid)  # (after the wait: lines may have been edited meanwhile)
    s = p["settings"]
    d = store.project_dir(pid)
    targets, recorded = _voice_targets(job, p, d)
    if not targets:
        return _nothing_to_do(job, "every narration line already has a voice"
                              + (f" ({len(recorded)} from your recording)" if recorded else ""))
    results = {}
    provider = s.get("voice_provider", "kokoro")
    service = online_voices.PROVIDERS.get(provider, provider)

    def speak(panel):
        text = panel.get("narration", "").strip()
        if not text:
            return None, 0.0
        while True:
            try:
                samples = tts.speak(text, s)
                break
            except online_voices.Problem as e:  # pause until the key, plan or voice is sorted out, then try again
                pause_for(job, VOICE_PAUSE[e.kind].format(service=service, error=str(e)[:160]))
                now = store.load(pid)  # the voice, or this line, may have been changed meanwhile
                s.update(now["settings"])
                text = next((x.get("narration", "") for x in now["panels"] if x["id"] == panel["id"]), "").strip()
                if not text:
                    return None, 0.0
        name = f"{panel['id']}.wav"
        sf.write(d / "audio" / name, samples, tts.SAMPLE_RATE)
        return name, len(samples) / tts.SAMPLE_RATE

    workers = online_voices.WORKERS[provider] if provider in online_voices.WORKERS else _workers(s, "voice")
    for i, (panel, result) in enumerate(_in_parallel(job, targets, speak, workers)):
        results[panel["id"]] = result
        job["progress"] = (i + 1) / max(1, len(targets))
        job["message"] = f"Generating voice: panel {i + 1}/{len(targets)}"
        if (i + 1) % 20 == 0 or i + 1 == len(targets):
            _save_audio(pid, dict(results))
            results.clear()


def _save_audio(pid, results):
    def apply(proj):
        for panel in proj["panels"]:
            if panel["id"] in results:
                panel["audio"], panel["duration"] = results[panel["id"]]
                panel["audio_rev"] = int(time.time())
                panel.pop("audio_source", None)  # a generated voice, not a recording
                panel.pop("voice_match", None)
    store.update(pid, apply)


def run_package(job):
    """Save the whole project as one file in its output folder (see package.py)."""
    pid = job["project"]
    p = store.load(pid)
    d = store.project_dir(pid)
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", p["name"])[:100].strip(" .") or "project"

    def prog(f):
        job["progress"] = f
        job["message"] = f"Saving the project as a file: {int(f * 100)}%"
    res = package.save(pid, d / "output" / f"{stem}{package.SUFFIX}", prog, lambda: job["cancel"])
    store.update(pid, lambda q: q.update(package={"file": f"output/{res['file'].name}", "size": res["size"],
                                                  "time": time.time()}))
    size = f"{res['size'] / 1e9:.1f} GB" if res["size"] >= 1e9 else f"{max(1, round(res['size'] / 1e6))} MB"
    job["note"] = f"Saved {res['file'].name} ({size}, {res['files']} files) in the output folder"


def from_recording(panel, project_dir) -> bool:
    """A line voiced from the chapter's own recording (see run_voiceover)."""
    return panel.get("audio_source") == "recording" and audio_done(panel, project_dir)


def run_voiceover(job):
    """Split a chapter's own recording (uploaded from the chapter's 🎙 button) into its scenes' lines,
    checked against the script (see voiceover.py). Lines not found in it keep what they had."""
    pid = job["project"]
    p = store.load(pid)
    d = store.project_dir(pid)
    ffmpeg = video.find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Install it or run setup.ps1.")
    if not voiceover.installed():
        raise RuntimeError("The speech recognizer isn't downloaded yet: get it from the chapter's 🎙 button.")
    for k, chapter in enumerate(_chapters(job, p)):
        rec = chapter.get("voiceover") or {}
        if not rec.get("file") or not (d / rec["file"]).exists():
            raise RuntimeError(f"{chapter['name']} has no recording: add one with its 🎙 button.")
        heads = [g[0] for g in scenes.groups(p["panels"])
                 if g[0]["chapter"] == chapter["id"] and (g[0].get("narration") or "").strip()]
        if not heads:
            raise RuntimeError(f"{chapter['name']} has no narration yet: write its script first, then record it.")

        def prog(f, k=k):
            job["progress"] = (k + f) / len(job["chapters"] or [1])
            job["message"] = (f"Splitting your recording of {chapter['name']}: "
                              f"{'listening' if f < 0.85 else 'cutting it into lines'} ({int(f * 100)}%)")
        (d / "audio").mkdir(exist_ok=True)
        stem = lambda x: f"{x['id']}_rec"  # noqa: E731
        res = voiceover.split(d / rec["file"], [(stem(x), x["narration"]) for x in heads], d / "audio", ffmpeg,
                              prog, lambda: job["cancel"])
        by_name = {x["name"]: x for x in res["lines"]}
        now = int(time.time())

        def apply(proj, by_name=by_name, chapter=chapter, res=res):
            check = []
            for x in proj["panels"]:
                got = by_name.get(stem(x))
                if not got:
                    continue
                if got["file"]:
                    x["audio"], x["duration"], x["audio_rev"] = got["file"], round(got["seconds"], 3), now
                    x["audio_source"], x["voice_match"] = "recording", got["heard"]
                if got["heard"] < voiceover.GOOD:
                    check.append({"id": x["id"], "heard": got["heard"], "found": bool(got["file"])})
            for c in proj["chapters"]:
                if c["id"] == chapter["id"]:
                    c["voiceover"] = {**(c.get("voiceover") or {}), "seconds": round(res["seconds"], 1),
                                      "heard": res["heard"], "lines": len(res["lines"]),
                                      "found": sum(1 for x in res["lines"] if x["file"]), "check": check,
                                      "extras": res["extras"][:30], "split": time.time()}
        store.update(pid, apply)
        found = sum(1 for x in res["lines"] if x["file"])
        job["note"] = (f"{chapter['name']}: {found} of {len(res['lines'])} lines found in your recording, "
                       f"{int(res['heard'] * 100)}% of the script's words heard")


def _output_stem(project, chapters) -> str:
    """recap.mp4 for the whole project, otherwise named after the chapter range."""
    if len(chapters) == len(project["chapters"]):
        return "recap"
    names = [chapters[0]["name"]] + ([chapters[-1]["name"]] if len(chapters) > 1 else [])
    stem = "recap - " + " to ".join(names)
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem)[:120].strip(" .")


def video_items(p, d, chapter, recover=None, prepare=None) -> tuple[list, bool]:
    """What a chapter's video shows: the whole strip scrolling (a webtoon set to scroll), or one clip
    per scene. recover(chapter) finds strip positions that are missing (None: show it as scenes).
    Settings → Video can ask for the pictures without their text (textless.py) and for scenes to zoom
    toward their subject (focus.py): what was made before is used, and, given prepare(what, done,
    total) for progress, the rest is made now.
    Returns (items, as planned: False when a chapter meant to scroll is shown as scenes)."""
    cid = chapter["id"]
    pieces = [x for x in p["panels"] if x["chapter"] == cid]
    pages = [x for x in p["pages"] if x["chapter"] == cid]
    if not any(x.get("enabled", True) for x in pieces):  # nothing used (a chapter that's only a notice): no video
        return [], True
    planned = True
    if _scrolls(p["settings"], pages):
        if all("y0" in x for x in pieces) or (recover and recover(chapter)):
            # the whole chapter scrolls; each scene's line plays while it passes the middle
            strip = {"type": "strip",
                     "pages": [(store.page_path(p, x), x["w"], x["h"]) for x in pages],
                     "scenes": [{"y0": min(x["y0"] for x in run), "y1": max(x["y1"] for x in run),
                                 "enabled": run[0].get("enabled", True),
                                 "audio": d / "audio" / run[0]["audio"] if run[0].get("audio") else None,
                                 "narration": run[0].get("narration", "")}
                                for run in _strip_runs(pieces)]}
            if p["settings"].get("youtube_safe"):
                covered = [x for x in pieces if x.get("enabled", True) and cover.boxes(x)]
                _locate(p, pages, [x for x in covered if "at" not in x] if prepare else [])
                strip["covers"] = [r for x in covered if "at" in x for r in cover.in_strip(x)]
            return _prepared(p, d, [strip], prepare), True
        planned = False
    items = [{  # one clip per scene: its pieces stacked, with the head's narration and voice
        "type": "scene",
        "images": [d / "panels" / x["file"] for x in g],
        "audio": d / "audio" / g[0]["audio"] if g[0].get("audio") else None,
        "narration": g[0].get("narration", ""),
        **({"covers": [cover.boxes(x) for x in g]} if p["settings"].get("youtube_safe") else {}),
    } for g in scenes.groups(pieces)]
    return _prepared(p, d, items, prepare), planned


def _locate(p, pages, pieces):
    """Find where webtoon pieces with black boxes sit in their strip (cover.locate), once: it's kept."""
    if not pieces:
        return
    found = cover.locate(p, pages, pieces, STRIP_GROUP)
    for x in pieces:
        if x["id"] in found:
            x["at"] = found[x["id"]]

    def apply(proj):
        for x in proj["panels"]:
            if x["id"] in found:
                x["at"] = found[x["id"]]
    store.update(p["id"], apply)


def _strip_runs(pieces) -> list[list[dict]]:
    """A scrolling chapter's scenes, each cut where its pieces go from left out to used or back: a scene
    whose start is left out (the scan group's credits, say) shows from its first piece used, which is
    where its narration is (the script is written for the pieces used). Scenes all used or all left
    out stay as they are."""
    out = []
    for g in scenes.all_groups(pieces):
        start = len(out)
        for x in g:
            if len(out) > start and out[-1][-1].get("enabled", True) == x.get("enabled", True):
                out[-1].append(x)
            else:
                out.append([x])
    return out


def _prepared(p, d, items, prepare):
    """items with the pictures Settings → Video asks for (see video_items)."""
    s = p["settings"]
    if s.get("remove_text"):  # first, so subjects are found in the pictures as the video shows them
        textless.attach(items, d, compute=prepare is not None, settings=s, workers=_workers(s, "ocr"),
                        progress=prepare and (lambda done, total: prepare("removing the text from the pictures",
                                                                          done, total)))
    if s.get("youtube_safe"):  # after the text is removed, so the boxes are drawn over the cleaned pictures
        cover.attach(items, d, compute=prepare is not None)
    if focus.wanted(s) and video.scene_motion(s):
        focus.attach(items, d, compute=prepare is not None,
                     progress=prepare and (lambda done, total: prepare("finding each scene's subject", done, total)))
    return items


def _video_parts(p, plan, chapters, seconds) -> list:
    """How the chapters are split into videos (Settings → Video → Split into parts), given each
    chapter's exact length: [(file stem, [chapter indexes in plan])]."""
    s = p["settings"]
    mode = s.get("video_split", "none")
    groups = [list(range(len(plan)))]
    if mode == "chapters":
        n = max(1, int(s.get("video_split_chapters") or 1))
        groups = [list(range(i, min(i + n, len(plan)))) for i in range(0, len(plan), n)]
    elif mode == "hours":
        limit = max(0.05, float(s.get("video_split_hours") or 11)) * 3600
        groups, cur, used = [], [], 0.0
        for i, length in enumerate(seconds):
            if cur and used + length > limit:
                groups.append(cur)
                cur, used = [], 0.0
            cur.append(i)
            used += length
        groups.append(cur)
    stem = _output_stem(p, chapters)
    if len(groups) == 1:
        return [(stem, groups[0])]

    def name(k, g):
        span = plan[g[0]]["name"] + (f" to {plan[g[-1]]['name']}" if len(g) > 1 else "")
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", f"{stem} - Part {k} ({span})")[:150].strip(" .")
    return [(name(k, g), g) for k, g in enumerate(groups, 1)]


def _video_plan(job, p, d, chapters, doing):
    """What each chapter's video shows (see video_items): ([{"id", "name", "items", "weight"}],
    names of chapters meant to scroll that are shown as scenes)."""
    if p["settings"].get("youtube_safe") and cover.installed():  # pieces the box finder hasn't looked at yet
        if _find_boxes(job, p, chapters, doing=f"{doing}: finding what to cover")[0]:
            p["panels"] = store.load(p["id"])["panels"]
    plan, not_scrolling = [], []
    for chapter in chapters:
        job["message"] = f"{doing}: preparing {chapter['name']}"

        def prepare(what, done, total, name=chapter["name"]):
            job["message"] = f"{doing}: {what}, {name} ({done}/{total})"
            _check(job)
        items, planned = video_items(p, d, chapter, recover=lambda c: recover_strip_positions(p, c), prepare=prepare)
        if not planned:
            not_scrolling.append(chapter["name"])
        if items:
            plan.append({"id": chapter["id"], "name": chapter["name"], "items": items,
                         "weight": max(1.0, estimates.video_seconds(p, {chapter["id"]}))})
    if not plan:
        raise RuntimeError("No panels to render. Import pages and detect panels first.")
    if p["settings"].get("remove_text") and len(chapters) == len(p["chapters"]):
        textless.prune(d, [it for ch in plan for it in ch["items"]])  # copies of pictures no longer used
    if p["settings"].get("youtube_safe") and len(chapters) == len(p["chapters"]):
        cover.prune(d, [it for ch in plan for it in ch["items"]])
    return plan, not_scrolling


def run_video(job):
    """Render chapter by chapter (reusing chapters already made, see video.render_chapters), then
    join them into one video or several parts."""
    pid = job["project"]
    p = store.load(pid)
    d = store.project_dir(pid)
    chapters = _chapters(job, p)
    plan, not_scrolling = _video_plan(job, p, d, chapters, "Rendering video")
    how = {"note": ""}

    def prog(f):
        job["progress"] = f
        job["message"] = f"Rendering video{how['note']}: {int(f * 100)}%"
    s = p["settings"]  # joining a second of video, as a share of drawing one (for the bar)
    join_cost = estimates.seconds_per_unit("join") / (
        estimates.seconds_per_unit(estimates.key("video", s)) * int(s.get("fps") or 30))
    result = video.render_chapters(plan, lambda seconds: _video_parts(p, plan, chapters, seconds), d / "output",
                                   d / "render", s,
                                   prog, lambda: job["cancel"], status=lambda note: how.update(note=f" ({note})"),
                                   between=lambda: _check(job), join_cost=join_cost)
    job["join_time"] = result["join_time"]  # left out of the drawing speed _learn_speed takes from this step
    estimates.learn("join", result["joined"], result["join_time"])
    outs = result["parts"]

    def apply(proj):
        proj["output"] = {
            "video": f"output/{outs[0]['video'].name}", "srt": f"output/{outs[0]['srt'].name}", "time": time.time(),
            "parts": [{"video": f"output/{o['video'].name}", "srt": f"output/{o['srt'].name}",
                       "chapters": f"output/{o['chapters'].name}", "seconds": round(o["seconds"], 1),
                       "first": o["first"], "last": o["last"]} for o in outs]}
    store.update(pid, apply)
    notes = []
    if result["reused"]:
        notes.append(f"Reused {result['reused']} chapter{'s' if result['reused'] != 1 else ''} already rendered, "
                     f"made {result['made']}")
    if len(outs) > 1:
        notes.append(f"Saved as {len(outs)} parts")
    if not_scrolling:
        notes.append("Shown as scenes instead of scrolling (detect panels again to scroll): "
                     + ", ".join(not_scrolling[:5]) + (" …" if len(not_scrolling) > 5 else ""))
    job["note"] = ". ".join(notes)


def run_editors(job):
    """Export the chapters as a timeline for video editors (Settings: editor_export; see editors.export)."""
    pid = job["project"]
    p = store.load(pid)
    d = store.project_dir(pid)
    chapters = _chapters(job, p)
    kind = p["settings"].get("editor_export", "resolve")
    doing = f"Exporting for {editors.KINDS.get(kind, kind)}"
    plan, not_scrolling = _video_plan(job, p, d, chapters, doing)
    how = {"note": ""}

    def prog(f):
        job["progress"] = f
        job["message"] = f"{doing}{how['note']}: {int(f * 100)}%"
    whole = len(chapters) == len(p["chapters"])
    name = p["name"] if whole else f"{p['name']}: {plan[0]['name']}" + (
        f" to {plan[-1]['name']}" if len(plan) > 1 else "")
    result = editors.export(plan, kind, _output_stem(p, chapters), name, d / "output", d / "editors", p["settings"],
                            prog, lambda: job["cancel"], status=lambda note: how.update(note=f" ({note})"),
                            between=lambda: _check(job))

    def apply(proj):
        proj["editors"] = {"file": f"output/{result['file'].name}", "srt": f"output/{result['srt'].name}",
                           "kind": kind, "time": time.time(), "size": result["size"],
                           "seconds": round(result["seconds"], 1), "clips": result["clips"],
                           "first": plan[0]["name"], "last": plan[-1]["name"]}
    store.update(pid, apply)
    notes = [f"Saved {result['file'].name} ({result['size'] / 1e9:.1f} GB)" if result["size"] >= 1e9
             else f"Saved {result['file'].name} ({max(1, round(result['size'] / 1e6))} MB)"]
    if result["reused"]:
        notes.append(f"Reused {result['reused']} chapter{'s' if result['reused'] != 1 else ''} already rendered "
                     f"for editors, made {result['made']}")
    if not_scrolling:
        notes.append("Shown as scenes instead of scrolling (detect panels again to scroll): "
                     + ", ".join(not_scrolling[:5]) + (" …" if len(not_scrolling) > 5 else ""))
    job["note"] = ". ".join(notes)


def _scrolls(settings, pages) -> bool:
    """Whether a chapter's video scrolls down its strip (the script is then written to follow
    the scroll): a webtoon, set to scroll in the project, with the app's video style on Automatic."""
    return (settings.get("video_style", "auto") == "auto" and settings.get("webtoon_video", "scroll") == "scroll"
            and _is_webtoon(settings, pages))


def _is_webtoon(settings, pages) -> bool:
    layout = settings.get("layout", "auto")
    if layout == "auto":
        return panels.is_webtoon([(x["w"], x["h"]) for x in pages])
    return layout == "webtoon"


def _merge(pid, field, values: dict, clear_audio=False, mark=None):
    def apply(proj):
        for panel in proj["panels"]:
            if panel["id"] in values:
                panel[field] = values[panel["id"]]
                if mark:
                    panel[mark] = True
                if clear_audio:
                    panel["audio"], panel["duration"] = None, 0.0
    store.update(pid, apply)


def run_scan(job):
    """Find the characters in the job's chapters and keep the groups for the user to review."""
    p = store.load(job["project"])
    record = _scan(job, p, [c["id"] for c in _chapters(job, p)])
    n = sum(1 for g in record["groups"] if g["size"] >= 3)
    job["note"] = (f"Found {n} characters (and {len(record['groups']) - n} small groups). "
                   "Review them in the Characters tab." if record["groups"] else "No characters were found.")


def _scan(job, p, chapters) -> dict:
    """Scan these chapters; the new record replaces the project's last scan."""
    pid = p["id"]

    def report(fraction, message):
        job.update(progress=fraction, message=message)

    record = charscan.scan(p, chapters, p["settings"].get("use_gpu", True), lambda: _check(job), report)

    def apply(proj):
        old = (proj.get("scan") or {}).get("id")
        if old:  # the previous scan's snapshots
            for f in (store.project_dir(pid) / "scan").glob(f"{old}_*"):
                f.unlink(missing_ok=True)
        proj["scan"] = record
    store.update(pid, apply)
    return record


# Finding characters (Make the recap, before the script): the narrator writes better knowing who is who
SCAN_FIRST = 5  # chapters scanned, as the scan dialog does by default: the main cast shows up early


def _to_write(project, chapters, missing=True) -> list[str]:
    """The chapters (all when None) whose script the next Write script would write, in project order."""
    chosen = set(chapters or [c["id"] for c in project["chapters"]])
    todo = {g[0]["chapter"] for g in scenes.groups(project["panels"]) if not (missing and script_done(g[0]))}
    return [c["id"] for c in project["chapters"] if c["id"] in chosen and c["id"] in todo]


def characters_to_scan(project, chapters, missing=True) -> list[str]:
    """What Finding characters scans: in a project never scanned and with no named characters, the chapters
    whose script is about to be written (the first SCAN_FIRST, or all as its settings say). Nothing otherwise
    (Scan again is in the Characters tab), or when it's turned off."""
    s = project["settings"]
    if (not s.get("find_characters", True) or project.get("scan")
            or any((c.get("name") or "").strip() for c in project["characters"])):
        return []
    todo = _to_write(project, chapters, missing)
    return todo if s.get("characters_chapters") == "all" else todo[:SCAN_FIRST]


def _add_note(job, note: str):
    job["note"] = ". ".join(x for x in (job.get("note"), note) if x)


def _open_groups(project) -> int:
    """Scanned characters still to review (as the review page counts them: small groups aside)."""
    groups = (project.get("scan") or {}).get("groups", [])
    return sum(1 for g in groups if g["status"] == "open" and g["size"] >= naming.MIN_SIZE)


def run_characters(job):
    """Before the script: scan a project that's never been scanned (characters_to_scan) and suggest names. Then,
    as the project's settings say, wait while the scan has characters to review, once per scan, until each is
    added or ignored (Resume writes the script without them); or trust the suggestions and go on."""
    pid = job["project"]
    p = store.load(pid)
    missing = job["mode"] == "missing"
    if not p["settings"].get("find_characters", True):
        return _nothing_to_do(job, "turned off in the project's settings")
    if not _to_write(p, job["chapters"], missing):
        return _nothing_to_do(job, "every chapter has its script")
    chapters = characters_to_scan(p, job["chapters"], missing)
    if chapters and not charscan.installed():
        _add_note(job, "Characters weren't looked for: download the character scan (the Characters tab) and Make "
                       "the recap finds them first")
        return _nothing_to_do(job, "the character scan isn't downloaded")
    trust = p["settings"].get("characters_review") == "trust"
    if chapters:
        started, paused = time.time(), job.get("paused_for", 0.0)
        _scan(job, p, chapters)
        estimates.learn(estimates.key("scan", p["settings"]),
                        sum(1 for x in p["panels"] if x["chapter"] in chapters and x.get("enabled", True)),
                        time.time() - started - (job.get("paused_for", 0.0) - paused))
        if _open_groups(store.load(pid)):
            try:
                _suggest_names(job, store.load(pid))
            except (Cancelled, InterruptedError):
                raise
            except Exception as e:  # the review works without suggestions
                traceback.print_exc()
                _add_note(job, f"Names weren't suggested ({e})")
        if trust:
            added = _trust_names(pid)
            _add_note(job, f"Added {added} character{'s' if added != 1 else ''} as suggested (check them in the "
                           "Characters tab)" if added else "No names were suggested to add")
    if not trust:
        _characters_hold(job)


def _trust_names(pid) -> int:
    """The suggestions taken as they are, as Add all suggested does: each named group, biggest first, becomes a
    character or joins the one with its name (or the listed one it was matched to); extras are ignored, and
    groups without a name stay for the review. The scan isn't waited for after. Returns characters added."""
    from . import server  # (loaded by then: it runs the jobs)
    p = store.load(pid)
    before = len(p["characters"])
    listed = {c["id"]: c["name"] for c in p["characters"]}
    items = []
    for g in sorted((g for g in p["scan"]["groups"] if g["status"] == "open" and g.get("suggestion")),
                    key=lambda g: -g["size"]):
        sug = g["suggestion"]
        if sug.get("extra"):
            server.scan_ignore(pid, g["id"])
        elif sug.get("name") or sug.get("character") in listed:
            items.append({"group": g["id"], "name": sug.get("name") or listed[sug["character"]],
                          **{k: sug.get(k) or "" for k in ("gender", "aliases", "description")}})
    if items:
        server.scan_add_named(pid, items)

    def apply(q):
        q["scan"]["held"] = True
    store.update(pid, apply)
    return len(store.load(pid)["characters"]) - before


def _characters_hold(job):
    pid = job["project"]
    p = store.load(pid)
    scan, n = p.get("scan") or {}, _open_groups(p)
    if not n or scan.get("held"):
        return
    job["waiting_for"] = "characters"
    try:
        pause_for(job, f"Paused before the script: the character scan found {n} character{'s' if n != 1 else ''} "
                       "to review. Add or ignore each one (Review characters) and the script is written, or Resume "
                       "to write it now.",
                  until=lambda: not _open_groups(store.load(pid)))
    finally:
        job.pop("waiting_for", None)

    def apply(q):  # not asked again for this scan
        if (q.get("scan") or {}).get("id") == scan["id"]:
            q["scan"]["held"] = True
    store.update(pid, apply)


def run_names(job):
    """Ask the script's writer to suggest names for the scan's open groups (see naming.py); the user accepts
    them on the review cards."""
    p = store.load(job["project"])
    _check(job)
    named, total, label = _suggest_names(job, p)
    job["note"] = (f"{label} suggested names for {named} of {total} groups. "
                   "Review them in the Characters tab.")


def _suggest_names(job, p) -> tuple[int, int, str]:
    """Fill in the scan's open groups' suggestions: (how many got a name, how many were asked about, by whom)."""
    pid = p["id"]
    report = lambda fraction, message: job.update(progress=fraction, message=message)  # noqa: E731
    if p["settings"].get("llm_provider") == "claude_code":  # one Claude Code agent answers
        from . import claude_code
        ask = claude_code.NameAsker(job, p)
        try:
            sid, found, label = naming.suggest(p, report, ask=ask)
        except BaseException:
            ask.close(ok=False)
            raise
        ask.close(ok=True)
    else:
        sid, found, label = naming.suggest(p, report)

    def apply(proj):
        rec = proj.get("scan") or {}
        if rec.get("id") != sid:  # scanned again meanwhile: these groups are gone
            return
        for g in rec["groups"]:
            if g["id"] in found and g["status"] == "open":
                g["suggestion"] = found[g["id"]]
    store.update(pid, apply)
    return sum(1 for x in found.values() if x["name"] or x["character"]), len(found), label


def run_title(job):
    """After the script (Make the recap, when the project's settings say so; or Suggest again in the Video tab):
    the writer suggests a YouTube title and the thumbnail's words and picture, and the thumbnail is drawn
    (youtube.py). In Make the recap a failure is only noted: the recap goes on without one."""
    pid = job["project"]
    p = store.load(pid)
    alone = job["steps"] == ["title"]
    if not alone and not p["settings"].get("suggest_title", True):
        return _nothing_to_do(job, "turned off in the project's settings")
    if job["mode"] == "missing" and p.get("youtube"):
        return _nothing_to_do(job, "one was suggested already (Suggest again in the Video tab)")
    try:
        found = _suggest_title(job, p)
    except (Cancelled, InterruptedError):
        raise
    except Exception as e:
        if alone:
            raise
        traceback.print_exc()
        return _add_note(job, f"No title was suggested ({e})")
    file = youtube.draw(p, next(x for x in p["panels"] if x["id"] == found["panel"]), found["text"])
    store.update(pid, lambda q: q.update(youtube={**found, "file": file, "created": time.time()}))


def _suggest_title(job, p) -> dict:
    job["message"] = f"Suggesting a title with {naming.writer_label(p['settings'])}"
    if p["settings"].get("llm_provider") != "claude_code":
        return youtube.suggest(p)
    from . import claude_code
    ask = claude_code.TitleAsker(job, p)  # one Claude Code agent answers
    try:
        found = youtube.suggest(p, ask=ask)
    except BaseException:
        ask.close(ok=False)
        raise
    ask.close(ok=True)
    return found


RUNNERS = {"panels": run_panels, "ocr": run_ocr, "characters": run_characters, "script": run_script,
           "title": run_title, "audio": run_audio, "video": run_video,
           "scan": run_scan, "editors": run_editors, "voiceover": run_voiceover, "package": run_package,
           "credits": run_credits, "names": run_names, "boxes": run_boxes}
