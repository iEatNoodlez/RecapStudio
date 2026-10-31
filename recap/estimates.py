"""How long each step will take: the work it would do (pages, panels, chapters, narration lines,
video frames) times how fast this computer does that work.

Speeds start from measurements made while building the app (an RTX 5060 Ti and 12 CPU threads)
and are replaced by this computer's own as steps finish (data/speeds.json), so estimates get
closer with use. The work is counted the same way the steps decide what's left to do, so an
estimate and the speed learned from the same run always match up.
"""
import json
import threading

from . import gpu, scenes, store, video

SPEEDS_FILE = store.DATA_ROOT / "speeds.json"
_lock = threading.Lock()

# seconds per unit of work, measured on the reference PC
DEFAULTS = {
    "panels": 0.08,       # per page (145 chapters, 3,166 pages in about 4 minutes)
    "ocr:gpu": 0.14,      # per panel, 4 workers on the GPU
    "ocr:cpu": 0.81,
    "scan:gpu": 0.065,    # per panel (12,024 panels in about 13 minutes)
    "scan:cpu": 0.5,
    "script:api": 45.0,   # per chapter, an online writer (Claude Opus averaged 45 s)
    "script:ollama": 4.0,  # per scene, a local model
    "audio:gpu": 0.33,    # per narration line, 2 workers on the GPU
    "audio:cpu": 2.1,
    "audio:edge": 0.3,    # per line, 4 at a time over the internet (Microsoft Edge's voices: 8 lines in 1.9-2.4 s)
    "audio:elevenlabs": 1.2,  # a guess (2 at a time); replaced by this computer's own once it has run
    "editors:resolve": 0.045,  # per scene, its pictures saved for DaVinci Resolve (186 scenes at 1080p in 7.6 s)
    "join": 0.022,        # per second of video, joining the chapters into one file (5 h 37 min in about 7 min 25 s,
                          # mostly encoding the narration on one CPU thread)
    "names": 2.2,         # per scan group, suggesting names (Claude Opus 5.5: 30 groups in 65 s)
}
# video frames per second, by encoder and resolution (two scrolling chapters, 2 drawing processes)
VIDEO_FPS = {("gpu", 1080): 255, ("gpu", 720): 471, ("gpu", 480): 608, ("gpu", 360): 673,
             ("cpu", 1080): 169, ("cpu", 720): 313, ("cpu", 480): 403, ("cpu", 360): 446}
STILLS_SPEEDUP = 1.3      # stills are drawn once per scene
CHARS_PER_SECOND = 17.5   # the voice at speed 1.0, measured over 3,843 lines
# before a chapter has a script: about one narration line per three panels, ~179 characters each
LINES_PER_PANEL, CHARS_PER_LINE = 0.32, 179
UNITS = {"panels": "page", "ocr": "panel", "scan": "panel", "audio": "narration line", "video": "frame"}


def _speeds() -> dict:
    try:
        return json.loads(SPEEDS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _device(settings) -> str:
    return "gpu" if settings.get("use_gpu", True) and gpu.available() else "cpu"


def _video_key(settings) -> str:
    enc = "gpu" if video.is_hardware(video.chapter_encoder(settings)) else "cpu"
    return f"video:{enc}:{int(settings.get('resolution') or 1080)}:{settings.get('video_style', 'auto')}"


def key(step: str, settings: dict) -> str:
    """Which measured speed applies to this step with these settings."""
    if step == "audio" and settings.get("voice_provider", "kokoro") != "kokoro":
        return f"audio:{settings['voice_provider']}"  # an online voice: the computer's device doesn't matter
    if step in ("ocr", "scan", "audio"):
        return f"{step}:{_device(settings)}"
    if step == "script":
        provider = settings.get("llm_provider", "ollama")
        if provider == "ollama":
            return "script:ollama"
        model = settings.get(f"{provider}_model") or ""
        return f"script:{provider}:{model}"
    if step == "video":
        return _video_key(settings)
    if step == "editors":  # rendered for any editor: drawn like the video
        baked = settings.get("editor_export", "resolve") == "baked"
        return f"editors:baked:{_video_key(settings)}" if baked else "editors:resolve"
    return step


def seconds_per_unit(k: str) -> float:
    learned = _speeds().get(k)
    if learned:
        return learned
    if k.startswith("script:") and k != "script:ollama":
        return DEFAULTS["script:api"]
    if k.startswith("editors:baked:"):  # until an export has been timed: as fast as the video
        return seconds_per_unit(k.removeprefix("editors:baked:"))
    if k.startswith("video:"):
        _, enc, res, style = k.split(":")
        fps = VIDEO_FPS.get((enc, int(res)), VIDEO_FPS[(enc, 1080)])
        return 1 / (fps * (STILLS_SPEEDUP if style == "stills" else 1))
    return DEFAULTS.get(k, 1.0)


def learn(k: str, units: float, seconds: float) -> None:
    """Remember how fast a finished step went (blended with what was known, so one odd run
    doesn't swing the next estimate too far)."""
    if units <= 0 or seconds < 3:
        return
    measured = seconds / units
    with _lock:
        speeds = _speeds()
        old = speeds.get(k)
        speeds[k] = measured if not old else 0.5 * old + 0.5 * measured
        SPEEDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SPEEDS_FILE.write_text(json.dumps(speeds, indent=1), encoding="utf-8")


def work(step: str, project: dict, chapters: list[str] | None, missing: bool,
         scripted: bool = False) -> tuple[float, str]:
    """How much work the step would do in these chapters (all when None): (amount, unit).
    missing: only what isn't done yet, as Run all and Continue do.
    scripted: the script is written earlier in the same run, so chapters with no narration yet
    are counted as having a typical amount (for the voice and video estimates)."""
    from . import pipeline  # the steps' own 'already done' checks
    s = project["settings"]
    chosen = set(chapters or [c["id"] for c in project["chapters"]])
    panels = [x for x in project["panels"] if x["chapter"] in chosen and x.get("enabled", True)]
    d = store.project_dir(project["id"])
    if step == "panels":
        if missing:
            has = {x["chapter"] for x in project["panels"]}
            chosen = {c for c in chosen if c not in has}
        return sum(1 for x in project["pages"] if x["chapter"] in chosen), "page"
    if step == "ocr":  # (the scan group's pages left out are read too: see pipeline.run_ocr)
        read = panels + [x for x in project["panels"] if x["chapter"] in chosen and x.get("credits")
                         and not x.get("enabled", True)]
        return sum(1 for x in read if not (missing and pipeline.ocr_done(x))), "panel"
    if step == "scan":
        return len(panels), "panel"
    if step == "script":
        groups = [g for g in scenes.groups(project["panels"]) if g[0]["chapter"] in chosen]
        todo = [g for g in groups if not (missing and pipeline.script_done(g[0]))]
        if s.get("llm_provider", "ollama") == "ollama":
            return len(todo), "scene"
        return len({g[0]["chapter"] for g in todo}), "chapter"
    if step == "audio":
        heads = [x for x in panels if (x.get("narration") or "").strip()]
        lines = sum(1 for x in heads if not (missing and pipeline.audio_done(x, d)))
        if scripted:
            lines += sum(LINES_PER_PANEL * n for n in _unscripted(project, chosen).values())
        return lines, "narration line"
    if step == "video":  # chapters already rendered as they are now are reused, not made again
        encoder = video.chapter_encoder(s)
        todo = set()
        for c in project["chapters"]:
            if c["id"] in chosen:
                items, _ = pipeline.video_items(project, d, c)
                base = d / "render" / f"{c['id']}-{video.piece_signature(items, s, encoder)}" if items else None
                if not (base and video.piece_done(base)):
                    todo.add(c["id"])
        return video_seconds(project, todo, scripted) * int(s.get("fps") or 30), "frame"
    if step == "editors":
        groups = [g for g in scenes.groups(project["panels"]) if g[0]["chapter"] in chosen]
        if s.get("editor_export", "resolve") != "baked":
            return len(groups), "scene"
        from . import editors  # chapters already rendered for editors, as they are now, are reused
        encoder = video.chapter_encoder(s)
        todo = set()
        for c in project["chapters"]:
            if c["id"] in chosen:
                items, _ = pipeline.video_items(project, d, c)
                kept = d / "editors" / f"{c['id']}-{editors.reel_signature(items, s, encoder)}.mp4" if items else None
                if kept and not kept.exists():
                    todo.add(c["id"])
        fps = int(s.get("fps") or 30)
        tails = sum(1 for g in groups if g[0]["chapter"] in todo) * float(s.get("transition_seconds", 0.4)) * fps
        return video_seconds(project, todo) * fps + tails, "frame"
    if step == "names":  # the scan's groups the writer is asked about (see naming.suggest)
        from . import naming
        groups = (project.get("scan") or {}).get("groups", [])
        todo = sum(1 for g in groups if g["status"] == "open" and g["size"] >= naming.MIN_SIZE)
        return min(naming.MAX_GROUPS, todo), "group"
    return 0, ""


def _unscripted(project, chapters) -> dict:
    """Chapters with no narration at all yet: {chapter: enabled panels}."""
    counts, narrated = {}, set()
    for x in project["panels"]:
        if x["chapter"] in chapters and x.get("enabled", True):
            counts[x["chapter"]] = counts.get(x["chapter"], 0) + 1
            if (x.get("narration") or "").strip():
                narrated.add(x["chapter"])
    return {c: n for c, n in counts.items() if c not in narrated}


def video_seconds(project: dict, chapters: set[str], scripted: bool = False) -> float:
    """Roughly how long the video of these chapters runs: each line's voice (or, before there is
    one, its length in characters at the voice's pace) plus the pauses around it."""
    s = project["settings"]
    speed = float(s.get("speed") or 1.0)
    pad = float(s.get("pad_seconds", 0.35))
    min_scene = float(s.get("min_panel_seconds", 2.0))
    scroll = s.get("video_style", "auto") == "auto" and s.get("webtoon_video", "scroll") == "scroll"
    total = 0.0
    for g in scenes.groups(project["panels"]):
        head = g[0]
        if head["chapter"] not in chapters or not any(x.get("enabled", True) for x in g):
            continue
        text = (head.get("narration") or "").strip()
        voice = head.get("duration") or (len(text) / (CHARS_PER_SECOND * speed) if text else 0.0)
        if scroll:  # scrolling: lines play back to back; quiet scenes scroll past
            total += voice + pad + 0.12 if text else 0.5
        else:  # one scene at a time: each is shown at least min_scene seconds
            total += max(min_scene, voice + pad + float(s.get("transition_seconds", 0.4)) * 0.5)
    if scripted:  # lines still to be written, at a typical length
        line = CHARS_PER_LINE / (CHARS_PER_SECOND * speed) + pad + 0.12
        total += sum(LINES_PER_PANEL * n * line for n in _unscripted(project, chapters).values())
    return total + (1.8 * len(chapters) if scroll else 0.0)


def join_seconds(project: dict, chapters: list[str] | None, scripted: bool = False) -> float:
    """About how long Render video takes to join these chapters (all when None) into the final video,
    after drawing them; it joins them all again whenever it runs."""
    chosen = set(chapters or [c["id"] for c in project["chapters"]])
    return video_seconds(project, chosen, scripted) * seconds_per_unit("join")


def estimate(project: dict, chapters: list[str] | None, steps: list[str], missing: bool = True) -> dict:
    """{step: {"seconds", "amount", "unit"}} for the steps, plus "total" seconds. When the script
    step is among them, the voice and video estimates allow for the narration it will write."""
    out, total = {}, 0.0
    for step in steps:
        amount, unit = work(step, project, chapters, missing, scripted="script" in steps)
        secs = amount * seconds_per_unit(key(step, project["settings"]))
        if step == "video" and amount:  # the chapters are then joined into the video
            secs += join_seconds(project, chapters, scripted="script" in steps)
        out[step] = {"seconds": round(secs), "amount": round(amount), "unit": unit}
        total += secs
    out["total"] = round(total)
    return out
