"""Project storage: one folder per project with a project.json file."""
import copy
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.environ.get("RECAP_DATA") or ROOT / "data")  # RECAP_DATA: use another data folder (testing)
DATA_DIR = DATA_ROOT / "projects"
MODELS_DIR = ROOT / "models"

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
}

# App-wide settings, shared by every project (data/settings.json): who writes the
# script, the voice and the video format. Everything else is per project.
GLOBAL_KEYS = {
    "llm_provider", "claude_model", "claude_code_model", "claude_code_workers", "ollama_url", "ollama_model", "use_vision", "panels_per_batch",
    "temperature", "voice", "voice_provider", "edge_voice", "elevenlabs_voice", "elevenlabs_model", "speed",
    "orientation", "fps", "min_panel_seconds", "pad_seconds",
    "transition_seconds", "motion", "motion_focus", "remove_text", "use_gpu", "parallel_workers", "use_magi", "ocr_english",
    "video_encoder", "video_workers", "resolution", "video_style",
    "openrouter_model", "gemini_model", "deepseek_model", "groq_model", "mistral_model", "setup_done",
    "video_split", "video_split_chapters", "video_split_hours", "editor_export", "editor_transition",
    "check_updates", "skip_version",
}
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


def project_dir(pid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", pid):
        raise KeyError(pid)
    return DATA_DIR / pid


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
    data["settings"] = {**copy.deepcopy(DEFAULT_SETTINGS), **stored, **global_settings()}
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
            for f in DATA_DIR.glob("*/project.json") if DATA_DIR.exists() else []:
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
    current = global_settings()
    current.update({k: v for k, v in changes.items() if k in GLOBAL_KEYS})
    with _global_lock:
        _write_global(current)
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


def create(name: str) -> dict:
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
    for sub in ("pages", "panels", "audio", "output"):
        (project_dir(pid) / sub).mkdir(parents=True, exist_ok=True)
    save(p)
    return load(pid)


def list_all() -> list[dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for d in DATA_DIR.iterdir():
        try:
            p = load(d.name)
        except (KeyError, ValueError):
            continue
        out.append({
            "id": p["id"], "name": p["name"], "updated": p.get("updated", 0),
            "chapters": len(p["chapters"]), "pages": len(p["pages"]), "panels": len(p["panels"]),
        })
    return sorted(out, key=lambda x: -x["updated"])


def delete(pid: str) -> None:
    shutil.rmtree(project_dir(pid), ignore_errors=True)
