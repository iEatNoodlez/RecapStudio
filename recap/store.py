"""Project storage: one folder per project, named after it, with a project.json file. Where everything lives
(settings, add-ons, models, projects) is in paths.py."""
import copy
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from . import paths

ROOT = paths.APP           # the app's own folder (read-only when installed in Program Files)
DATA_ROOT = paths.DATA     # settings, API keys, logs
ADDONS_DIR = paths.ADDONS  # the optional downloads (text reading, character scan, Magi, ...)
MODELS_DIR = paths.MODELS  # the Kokoro voice model
log = logging.getLogger("recap.store")

DEFAULT_SETTINGS = {
    # panel detection
    "layout": "auto",                # "auto", "webtoon" (one long strip) or "pages" (comic/manga pages)
    "reading_direction": "ltr",      # "ltr" (western/webtoon) or "rtl" (manga)
    "min_panel_frac": 0.08,          # smallest panel side, as a fraction of page width
    "webtoon_video": "scroll",       # webtoon chapters: "scroll" the whole strip, or show "scenes" one by one
    "leave_out_credits": True,       # leave out the scan group's credits, notices and ads at chapters' ends (credits.py)
    # YouTube-safe: milder sex scenes in the script (script.YOUTUBE) and black boxes over nudity in the video (cover.py)
    "youtube_safe": False,
    # script
    "ollama_url": "http://localhost:11434",
    "ollama_model": "",
    "use_vision": True,
    "panels_per_batch": 6,
    "temperature": 0.7,
    # "ollama" (local) or an online service writing a whole chapter per request: "claude" (Anthropic),
    # "openrouter", "gemini" (Google), "deepseek", "groq" or "mistral" (see cloud.py); or "claude_code": Claude
    # Code's agents on the user's Claude plan, one per chapter (claude_code.py)
    "llm_provider": "ollama",
    "claude_model": "claude-haiku-5-5",
    "claude_code_model": "opus",     # Claude Code's name for the model: "opus" or "sonnet"
    "claude_code_workers": 4,        # chapters Claude Code's agents work on at once
    "openrouter_model": "",          # chosen from OpenRouter's list (e.g. "google/gemini-3.8-flash")
    "gemini_model": "gemini-3.8-flash",
    "deepseek_model": "deepseek-flash",
    "groq_model": "qwen/qwen3.8-27b",
    "mistral_model": "mistral-medium-latest",
    # narration prompt (see DEFAULT_PROMPT) and a free-form note for the next run
    "note": "",
    # voice: "kokoro" (on this computer), "edge" (Microsoft Edge's online voices) or "elevenlabs" (see online_voices.py)
    "voice_provider": "kokoro",
    "voice": "af_heart",             # Kokoro's
    "edge_voice": "en-US-AndrewNeural",
    "elevenlabs_voice": "",          # a voice id from the key's voice list
    "elevenlabs_model": "eleven_multilingual_v2",
    "speed": 1.0,
    # video
    "orientation": "landscape",      # "landscape" or "portrait"
    "resolution": 1080,              # 1080, 720, 480 or 360: the short side (1080 -> 1920x1080 or 1080x1920)
    "fps": 30,
    # "auto": webtoon chapters scroll when the project's webtoon_video says so, other scenes pan and
    # zoom (unless motion is off); "motion": every scene on its own with pan and zoom; "stills": no movement
    "video_style": "auto",
    # one video, or a new one every N chapters ("chapters") or at most N hours each ("hours")
    "video_split": "none",
    "video_split_chapters": 20,
    "video_split_hours": 11,         # YouTube takes up to 12 hours per video
    "editor_export": "resolve",      # Export for editors: "resolve" (keyframes) or "baked" (rendered clips)
    # transitions in the Resolve export: "dissolve" (like the video), "slide", "push", "blur" (Blur Dissolve),
    # "smooth" (Smooth Cut) or "mixed" (see editors.RESOLVE_TRANSITIONS)
    "editor_transition": "dissolve",
    "min_panel_seconds": 2.0,
    "pad_seconds": 0.35,
    "transition_seconds": 0.4,
    "motion": True,
    # scenes that zoom aim at "center", or at the "subject" found by the optional U2-Net-p add-on (focus.py)
    "motion_focus": "center",
    "remove_text": False,            # paint the text in speech bubbles and captions out of the pictures (textless.py)
    # run text reading and the voice on the NVIDIA GPU when available (falls back to the CPU)
    "use_gpu": True,
    "parallel_workers": 0,           # panels / lines worked on at once; 0 = automatic
    "video_encoder": "auto",         # "auto": NVIDIA's encoder when ffmpeg and the card have it, else the CPU; "cpu"
    "video_workers": 0,              # processes drawing video frames at once; 0 = automatic
    # optional Magi v2 add-on: guess who says each line while reading text (see magi.py)
    "use_magi": False,
    # read text with RapidOCR's English model when it has been downloaded (see ocr.py)
    "ocr_english": True,
    # first-run setup finished (or skipped); Settings → Run setup again opens it any time
    "setup_done": False,
    # updates (updates.py): ask GitHub for a newer release when the app starts; a release you chose to skip
    "check_updates": True,
    "skip_version": "",
    # where projects are saved; empty: the default (paths.DEFAULT_PROJECTS)
    "projects_dir": "",
}

# App-wide settings, shared by every project (data/settings.json): who writes the
# script, the voice and the video format. Everything else is per project.
GLOBAL_KEYS = {
    "llm_provider", "claude_model", "claude_code_model", "claude_code_workers", "ollama_url", "ollama_model", "use_vision", "panels_per_batch",
    "temperature", "voice", "voice_provider", "edge_voice", "elevenlabs_voice", "elevenlabs_model", "speed",
    "use_gpu", "parallel_workers", "use_magi", "ocr_english", "video_encoder", "video_workers",
    "openrouter_model", "gemini_model", "deepseek_model", "groq_model", "mistral_model", "setup_done",
    "check_updates", "skip_version", "projects_dir",
}
# How each project's video looks: chosen when it's created (the New project dialog), changed in its Video tab.
# App-wide until 0.1.1 (move_video_settings copies those choices into the projects).
VIDEO_KEYS = (
    "orientation", "resolution", "fps", "video_style", "motion", "motion_focus", "remove_text", "min_panel_seconds",
    "pad_seconds", "transition_seconds", "video_split", "video_split_chapters", "video_split_hours", "editor_export",
    "editor_transition",
)
# what the New project dialog sets, besides those
NEW_PROJECT_KEYS = set(VIDEO_KEYS) | {"layout", "reading_direction", "webtoon_video", "youtube_safe"}
GLOBAL_FILE = DATA_ROOT / "settings.json"

DEFAULT_PROMPT = {
    "persona": (
        "You narrate recap videos of comics, manga and webtoons. Your voice is warm, clear and "
        "lightly playful, with a quiet sense of drama, like a friend retelling a story they loved. "
        "You keep the audience oriented: who is acting, what they want, what pressure they are "
        "under, and what just changed. You make the turning points land."
    ),
    "rules": [
        "Write in third person, past tense.",
        "Retell dialogue in your own words. Never quote it or use quotation marks.",
        "Describe only what the panels and their text show. Do not invent events, names or motives.",
        "If the images and the text seem to disagree, trust the images for what physically happens "
        "and the text for what characters think, feel or want.",
        "Use names from the character list when you recognize a character. Otherwise describe "
        "them briefly, for example 'the tall stranger'.",
        "Write numbers as words, for example 'twenty-three' instead of '23', so the voice reads them correctly.",
        "Never mention panels, pages, images, speech bubbles or sound effects.",
        "Leave the narration empty for panels that add nothing new, such as repeated reactions.",
    ],
    "styles": [
        "Keep each panel's narration to one or two sentences.",
        "Focus on what drives the characters: their goals, what is at stake, and their inner conflicts.",
        "Fold small actions into one flowing sentence instead of narrating every step.",
        "Vary how sentences start, and use pronouns instead of repeating names.",
        "Link panels with natural transitions. When the story moves to another place or time, "
        "bridge it, for example with 'Meanwhile' or 'Later that night'.",
        "Keep each character's personality consistent from scene to scene.",
        "Let tension and emotion come through in the wording instead of stating them outright.",
        "When a chapter ends, close on its strongest moment of tension.",
    ],
}
DEFAULT_SETTINGS.update(DEFAULT_PROMPT)

# earlier built-in defaults: projects still using them exactly get the current ones
_PREVIOUS_PROMPTS = [{
    "persona": (
        "You narrate recap videos of comics, manga and webtoons. Your voice is warm, clear "
        "and quietly dramatic, like a friend retelling a story they loved. You keep the "
        "audience oriented: who is acting, what they want, and what just changed."
    ),
    "rules": [
        "Write in third person, past tense.",
        "Retell dialogue in your own words. Never quote it or use quotation marks.",
        "Describe only what the panels and their text show. Do not invent events, names or motives.",
        "Use names from the character list when you recognize a character. Otherwise describe "
        "them briefly, for example 'the tall stranger'.",
        "Write numbers as words, for example 'twenty-three' instead of '23', so the voice reads them correctly.",
        "Never mention panels, pages, images, speech bubbles or sound effects.",
        "Leave the narration empty for panels that add nothing new, such as repeated reactions.",
    ],
    "styles": [
        "Keep each panel's narration to one or two sentences.",
        "Vary how sentences start, and use pronouns instead of repeating names.",
        "Link panels with natural transitions so the story reads as one continuous telling.",
        "Let tension and emotion come through in the wording instead of stating them outright.",
    ],
}]

# default of the single "style" box that the structured prompt replaced
_OLD_STYLE_DEFAULT = (
    "You are narrating a story recap video. Retell what happens in each panel "
    "in vivid, natural third-person past tense, as one continuous story. "
    "Do not mention panels, pages, images or speech bubbles. Keep each line "
    "to one or two sentences. Use an empty string for panels that add nothing."
)

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock(pid: str) -> threading.RLock:
    # reentrant: update() holds it while calling load()
    with _locks_guard:
        return _locks.setdefault(pid, threading.RLock())


def natural_key(s: str):
    """Sort key where numbers compare by value, decimals included ("144" < "144.5" < "145")."""
    parts = re.split(r"(\d+(?:\.\d+)?)", s)
    return [float(t) if i % 2 else t.lower() for i, t in enumerate(parts)]


# ---- where projects live: the projects folder (Settings → Projects folder), one folder each, named after the
# project ("Smart Life", "Smart Life (2)"); a project is found by the id in its project.json, so a folder can be
# renamed or moved by hand while the app is closed ----

_index: dict[str, Path] = {}  # project id -> its folder
_index_lock = threading.RLock()
_projects_root: Path | None = None
PROJECT_ID = re.compile(r"[a-f0-9]{12}")


def projects_root() -> Path:
    global _projects_root
    if _projects_root is None:
        chosen = (global_settings().get("projects_dir") or "").strip()
        _projects_root = Path(chosen) if chosen else paths.DEFAULT_PROJECTS
    return _projects_root


def _read_id(folder: Path) -> str | None:
    """The project id in a folder's project.json, read from its start (the id is saved first)."""
    path = folder / "project.json"
    try:
        with open(path, "rb") as f:
            head = f.read(2048).decode("utf-8", "ignore")
        m = re.match(r'\s*\{\s*"id":\s*"([a-f0-9]{12})"', head)
        return m.group(1) if m else json.loads(path.read_text(encoding="utf-8")).get("id")
    except (OSError, ValueError, AttributeError):
        return None


def _scan():
    found = {}
    root = projects_root()
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if d.is_dir() and not d.name.endswith(".part"):
            pid = _read_id(d)
            if pid and PROJECT_ID.fullmatch(pid) and pid not in found:
                found[pid] = d
    with _index_lock:
        _index.clear()
        _index.update(found)


def project_dir(pid: str) -> Path:
    if not PROJECT_ID.fullmatch(pid):
        raise KeyError(pid)
    with _index_lock:
        d = _index.get(pid)
        if d is None or not d.is_dir():
            _scan()
            d = _index.get(pid)
    if d is None:
        raise KeyError(pid)
    return d


def folder_name(name: str) -> str:
    """A project's name as a folder name Windows accepts."""
    s = re.sub(r"\s+", " ", re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name)).strip()[:60].rstrip(". ") or "Untitled"
    return s + "_" if re.fullmatch(r"(?i)(con|prn|aux|nul|com\d|lpt\d)(\..*)?", s) else s


def _free_folder(root: Path, name: str, keep: Path | None = None) -> Path:
    """root/<name>, or <name> (2), (3)... if that's taken (keep: a folder that may keep its own name)."""
    base, n = folder_name(name), 2
    d = root / base
    taken = {p.resolve() for p in _index.values()}
    while (d.exists() or d.resolve() in taken) and d != keep:
        d, n = root / f"{base} ({n})", n + 1
    return d


def new_folder(pid: str, name: str) -> Path:
    """The folder for a new project, named after it, remembered as its folder (not made yet)."""
    root = projects_root()
    root.mkdir(parents=True, exist_ok=True)
    with _index_lock:
        d = _free_folder(root, name)
        _index[pid] = d
    return d


def rename_folder(pid: str) -> Path:
    """Give a project's folder its current name. If something has a file in it open, it keeps the old one."""
    with _lock(pid), _index_lock:
        d = project_dir(pid)
        try:
            name = json.loads((d / "project.json").read_text(encoding="utf-8"))["name"]
            target = _free_folder(d.parent, name, keep=d)
            if target != d:
                d.rename(target)
                _index[pid] = d = target
        except (OSError, ValueError, KeyError) as e:
            log.info("project folder %s keeps its name: %s", d, e)
        return d


def name_folders():
    """Folders still named by project id (as before folders had names) take their project's name."""
    _scan()
    for pid, d in list(_index.items()):
        if d.name == pid:
            rename_folder(pid)


def load(pid: str) -> dict:
    path = project_dir(pid) / "project.json"
    if not path.exists():
        raise KeyError(pid)
    # read under the project's lock: on Windows a reader holding project.json open
    # makes the atomic replace in save() fail with "Access is denied"
    with _lock(pid):
        data = json.loads(path.read_text(encoding="utf-8"))
    stored = data.get("settings", {})
    old_style = stored.pop("style", None)
    if old_style and old_style != _OLD_STYLE_DEFAULT and "persona" not in stored:
        stored["persona"] = old_style  # keep a customized old prompt as the persona
    for key in DEFAULT_PROMPT:  # untouched old defaults -> current defaults; edits are kept
        if any(stored.get(key) == old[key] for old in _PREVIOUS_PROMPTS):
            stored.pop(key)
    # (video settings a project doesn't have yet: the app-wide ones from before they were per project)
    data["settings"] = {**copy.deepcopy(DEFAULT_SETTINGS), **_saved_video(), **stored, **global_settings()}
    data.setdefault("characters", [])
    if "chapters" not in data:  # projects from before chapters existed
        data["chapters"] = []
        if data["pages"]:
            data["chapters"].append({"id": "c0001", "name": "Chapter 1", "source": None})
            for x in data["pages"] + data["panels"]:
                x["chapter"] = "c0001"
    return data


def new_chapter_id(project: dict) -> str:
    n = max((int(c["id"][1:]) for c in project["chapters"]), default=0) + 1
    return f"c{n:04d}"


def page_path(project: dict, page: dict) -> Path:
    """Pages imported from a folder are read in place; uploaded ones live in the project."""
    return Path(page["src"]) if page.get("src") else project_dir(project["id"]) / "pages" / page["file"]


def sort_by_chapter(project: dict) -> None:
    """Keep pages and panels in chapter order (the list order of project['chapters'])."""
    rank = {c["id"]: i for i, c in enumerate(project["chapters"])}
    project["pages"].sort(key=lambda x: rank.get(x["chapter"], 1e9))  # stable within a chapter
    project["panels"].sort(key=lambda x: (rank.get(x["chapter"], 1e9), x["id"]))


_global_lock = threading.Lock()


def global_settings() -> dict:
    """App-wide settings. The first time, they start from the most recently used
    project's choices, so nothing set before this split is lost."""
    with _global_lock:
        try:
            saved = json.loads(GLOBAL_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = None
        if saved is None:
            saved = {}
            newest = 0.0
            old = paths.DEFAULT_PROJECTS  # (where projects were before this split)
            for f in old.glob("*/project.json") if old.exists() else []:
                try:
                    p = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if p.get("updated", 0) > newest:
                    newest = p.get("updated", 0)
                    saved = {k: v for k, v in p.get("settings", {}).items() if k in GLOBAL_KEYS}
            _write_global(saved)
        return {**{k: DEFAULT_SETTINGS[k] for k in GLOBAL_KEYS}, **{k: v for k, v in saved.items() if k in GLOBAL_KEYS}}


def _write_global(values: dict) -> None:
    GLOBAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = GLOBAL_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(values, indent=1), encoding="utf-8")
    tmp.replace(GLOBAL_FILE)


def update_global(changes: dict) -> dict:
    global _projects_root
    current = global_settings()
    current.update({k: v for k, v in changes.items() if k in GLOBAL_KEYS})
    with _global_lock:
        _write_global(current)
    if "projects_dir" in changes:  # (set through move_projects, which checks the folder)
        with _index_lock:
            _projects_root = None
            _index.clear()
    return current


def save(project: dict) -> None:
    sort_by_chapter(project)
    # app-wide settings live in settings.json, not in each project
    project["settings"] = {k: v for k, v in project["settings"].items() if k not in GLOBAL_KEYS}
    d = project_dir(project["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "project.json.tmp"
    tmp.write_text(json.dumps(project, indent=1, ensure_ascii=False), encoding="utf-8")
    # something outside the app (antivirus, search indexer, a second copy of the app)
    # can still have the file open for a moment, so retry before giving up
    for attempt in range(20):
        try:
            tmp.replace(d / "project.json")
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.05 * (attempt + 1))


def update(pid: str, fn):
    """Load, mutate with fn(project), and save under a per-project lock."""
    with _lock(pid):
        p = load(pid)
        result = fn(p)
        p["updated"] = time.time()
        save(p)
        return result if result is not None else p


def _saved_video() -> dict:
    """The video settings settings.json still holds from when they were app-wide (until 0.1.1)."""
    try:
        saved = json.loads(GLOBAL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: saved[k] for k in VIDEO_KEYS if k in saved}


def move_video_settings():
    """Video settings were app-wide until 0.1.1: each project gets the ones it was made with (the app-wide
    choices), then settings.json lets them go."""
    old = _saved_video()
    if not old:
        return
    _scan()
    for pid in list(_index):
        try:
            with _lock(pid):
                save(load(pid))  # load fills in the old app-wide ones, save keeps them (and not "updated")
        except (KeyError, ValueError, OSError) as e:
            log.warning("couldn't give project %s its video settings: %s", pid, e)
    with _global_lock:
        _write_global({k: v for k, v in json.loads(GLOBAL_FILE.read_text(encoding="utf-8")).items()
                       if k not in VIDEO_KEYS})


def new_project_settings() -> dict:
    """The New project dialog's starting choices: the last project's (it's often the same kind of comic and
    video), else the old app-wide ones, else the defaults."""
    out = {k: DEFAULT_SETTINGS[k] for k in NEW_PROJECT_KEYS}
    out.update(_saved_video())
    newest = max(list_all(), key=lambda x: x["updated"], default=None)
    if newest:
        s = load(newest["id"])["settings"]
        out.update({k: s[k] for k in NEW_PROJECT_KEYS if k in s and k != "youtube_safe"})
    return out


def create(name: str, settings: dict | None = None) -> dict:
    """A new project; settings: the New project dialog's choices (NEW_PROJECT_KEYS), each of its default's type."""
    pid = uuid.uuid4().hex[:12]
    p = {
        "id": pid,
        "name": name.strip() or "Untitled",
        "created": time.time(),
        "updated": time.time(),
        "settings": copy.deepcopy(DEFAULT_SETTINGS),
        "characters": [],
        "chapters": [],
        "pages": [],
        "panels": [],
        "output": None,
    }
    for k, v in (settings or {}).items():
        if k in NEW_PROJECT_KEYS:
            kind = type(DEFAULT_SETTINGS[k])
            p["settings"][k] = (v in (True, "true", 1, "1")) if kind is bool else kind(v)
    d = new_folder(pid, p["name"])
    for sub in ("pages", "panels", "audio", "output"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    save(p)
    return load(pid)


def list_all() -> list[dict]:
    _scan()  # (folders added or removed by hand show up too)
    out = []
    for pid in list(_index):
        try:
            p = load(pid)
        except (KeyError, ValueError):
            continue
        out.append({
            "id": p["id"], "name": p["name"], "updated": p.get("updated", 0), "folder": str(project_dir(pid)),
            "chapters": len(p["chapters"]), "pages": len(p["pages"]), "panels": len(p["panels"]),
        })
    return sorted(out, key=lambda x: -x["updated"])


def delete(pid: str) -> None:
    d = project_dir(pid)
    shutil.rmtree(d, ignore_errors=True)
    with _index_lock:
        _index.pop(pid, None)


# ---- the projects folder moving (Settings → Projects folder) ----

MOVE = {"state": "idle", "done": 0, "total": 0, "message": "", "error": None}


def check_folder(folder: str) -> Path:
    """A folder the projects can go in: a full path, writable, and not inside the app or a project."""
    path = Path(folder.strip().strip('"')).expanduser()
    if not path.is_absolute():
        raise ValueError("Give the full path of a folder, such as D:\\Recaps.")
    path = path.resolve()
    if path.is_relative_to(ROOT.resolve()) and paths.INSTALLED:
        raise ValueError("That's inside Recap Studio's own folder, which updates replace. Pick another folder.")
    for d in _index.values():
        if path.is_relative_to(d.resolve()):
            raise ValueError(f"That's inside the project folder {d}. Pick another folder.")
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".recap-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise ValueError(f"Recap Studio can't save in {path}: {e.strerror or e}")
    return path


def move_projects(folder: str, move: bool) -> dict:
    """Use another projects folder. move: take every project there, each in its own folder (on the same drive
    each is renamed across; to another drive all are copied first, and the old copies go only once every one
    has arrived, so a failure leaves them all where they were). Otherwise the old folder keeps its projects,
    and they show again if it's chosen again."""
    new = check_folder(folder)
    old = projects_root()
    if new == old.resolve():
        return MOVE
    _scan()
    if not move or not _index:
        update_global({"projects_dir": str(new)})
        MOVE.update(state="idle", done=0, total=0, message=f"Projects folder: {new}", error=None)
        return MOVE
    if MOVE["state"] == "moving":
        raise ValueError("The projects are already being moved.")
    MOVE.update(state="moving", done=0, total=len(_index), message="Moving the projects", error=None)
    threading.Thread(target=_move_all, args=(old, new), daemon=True, name="move-projects").start()
    return MOVE


def _same_drive(a: Path, b: Path) -> bool:
    return os.stat(a).st_dev == os.stat(b).st_dev


def _move_all(old: Path, new: Path):
    folders = dict(_index)
    done: list[tuple[Path, Path]] = []
    same_drive, copying = True, None
    try:
        same_drive = _same_drive(old, new)
        for pid, d in folders.items():
            with _lock(pid):
                target = new / d.name
                n = 2
                while target.exists():
                    target, n = new / f"{d.name} ({n})", n + 1
                MOVE["message"] = f"Moving {d.name}"
                if same_drive:
                    d.rename(target)
                else:
                    copying = target  # (half a copy is removed if it fails)
                    shutil.copytree(d, target)
                    copying = None
                done.append((d, target))
                MOVE["done"] = len(done)
        if not same_drive:
            for d, _ in done:
                shutil.rmtree(d, ignore_errors=True)
        update_global({"projects_dir": str(new)})
        MOVE.update(state="idle", message=f"Moved {len(done)} projects to {new}")
    except Exception as e:  # put back what moved (or remove the copies): everything stays where it was
        log.exception("moving the projects")
        if isinstance(e, shutil.Error) and e.args and e.args[0]:  # copytree's list of (from, to, why)
            e = OSError(f"{e.args[0][0][2]} (and {len(e.args[0]) - 1} more)" if len(e.args[0]) > 1 else e.args[0][0][2])
        if copying is not None:
            shutil.rmtree(copying, ignore_errors=True)
        for d, target in reversed(done):
            try:
                if same_drive:
                    target.rename(d)
                else:
                    shutil.rmtree(target, ignore_errors=True)
            except OSError:
                pass
        with _index_lock:
            _index.clear()
        MOVE.update(state="error", error=f"{e}. Nothing was moved; the projects are still in {old}.")


# ---- an installed copy from before the standard places (0.1.0 kept everything next to itself) ----

def move_old_layout():
    """Move an installed copy's data and models folders from beside the app to Windows' places (paths.py):
    settings and keys to %APPDATA%, add-ons and models to %LOCALAPPDATA%, projects to the projects folder."""
    old, old_models = ROOT / "data", ROOT / "models"
    if not paths.INSTALLED or not (old.is_dir() or old_models.is_dir()):
        return
    log.info("moving %s and %s to the standard places", old, old_models)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)

    def move(src: Path, dest: Path):
        try:
            if src.exists() and not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
        except OSError as e:
            log.warning("couldn't move %s to %s: %s", src, dest, e)
    if old.is_dir():
        for f in old.iterdir():
            if f.name in ("projects", "addons", "updates", "app.json"):
                continue
            move(f, DATA_ROOT / f.name)
        move(old / "addons", ADDONS_DIR)
        root = projects_root()
        root.mkdir(parents=True, exist_ok=True)
        for d in sorted((old / "projects").iterdir()) if (old / "projects").is_dir() else []:
            if d.is_dir() and (d / "project.json").exists():
                pid = _read_id(d) or d.name
                try:
                    name = json.loads((d / "project.json").read_text(encoding="utf-8")).get("name") or d.name
                except (OSError, ValueError):
                    name = d.name
                with _index_lock:
                    target = _free_folder(root, name)
                move(d, target)
                if target.exists():
                    with _index_lock:
                        _index[pid] = target
        shutil.rmtree(old / "updates", ignore_errors=True)
        (old / "app.json").unlink(missing_ok=True)
        for d in (old / "projects", old):
            try:
                d.rmdir()  # only if empty: anything that couldn't move stays
            except OSError:
                pass
    if old_models.is_dir():
        if not MODELS_DIR.exists():
            move(old_models, MODELS_DIR)
        else:
            for f in old_models.iterdir():
                move(f, MODELS_DIR / f.name)
            try:
                old_models.rmdir()
            except OSError:
                pass
