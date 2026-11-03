"""Feature plugins: folders that add to the app itself (steps, API routes, the page), unlike source plugins
(sources.py), which only get chapters and series details from sites and run walled off in a process of their own.

A feature plugin runs inside the app, with its full access, so only two places are read:
- the app's own plugins folder (BUNDLED_DIR): the ones Full Suite comes with, updated with the app;
- data\\plugins\\<id>\\ (INSTALLED_DIR): installed from Recap Studio's own plugin list (OFFICIAL_LIST), never from a
  file or another list. Source plugins are the .py files in that same folder (sources.py only reads those).
When both places have a plugin, the newer version is used.

A plugin is a folder named after its id:
  plugin.json      {"id", "name", "version", "api": 1, "summary"}
  __init__.py      optional: setup(plugin) registers what it adds (see Plugin)
  web\\plugin.js    optional: loaded by the page after app.js, with window.RecapStudio (app.js) to add to it
  web\\plugin.css   optional
Installing, switching on or off and removing take effect the next time the app starts: a loaded module can't be
taken out of a running app.
"""
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path

from fastapi import APIRouter

from . import store

API = 1                                        # the plugin interface this app gives (plugin.json's "api")
BUNDLED_DIR = store.ROOT / "plugins"
INSTALLED_DIR = store.DATA_ROOT / "plugins"
STATE_FILE = INSTALLED_DIR / "features.json"   # which are switched off ({"off": [ids]})
DATA_DIR = store.DATA_ROOT / "plugin-data"     # each plugin's own files (Plugin.data_dir)
OFFICIAL_LIST = (os.environ.get("RECAP_PLUGIN_LIST")
                 or "https://raw.githubusercontent.com/iEatNoodlez/RecapStudio-Plugins/main/index.json")
MAX_ZIP = 20 << 20                             # a plugin's zip, at most
MAX_UNPACKED = 60 << 20                        # and what's in it
ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_lock = threading.Lock()

loaded: dict[str, dict] = {}    # id -> its entry in found(), for the plugins this run of the app loaded
_handlers: dict[str, list] = {}  # event -> [(plugin id, function)]
changed = False                 # something was installed, switched or removed since the app started


class PluginError(Exception):
    """Something a member can act on (shown as it is)."""


def _version(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-+]", str(v))[:4])


def read_info(folder: Path, name: str | None = None) -> dict:
    """A plugin's plugin.json, checked (its id against the folder's name, or name). Raises PluginError when it isn't
    one this app can run."""
    try:
        info = json.loads((folder / "plugin.json").read_text(encoding="utf-8-sig"))
    except OSError:
        raise PluginError("It has no plugin.json")
    except ValueError as e:
        raise PluginError(f"Its plugin.json isn't valid JSON ({e})")
    if not isinstance(info, dict):
        raise PluginError("Its plugin.json isn't a plugin's details")
    pid = info.get("id")
    if not isinstance(pid, str) or not ID.fullmatch(pid):
        raise PluginError("Its plugin.json has no id (lower-case letters, digits, - and _)")
    if pid != (name or folder.name):
        raise PluginError(f"The list says {name}, but the plugin's id is {pid}" if name
                          else f"Its folder is named {folder.name}, but its id is {pid}")
    if not isinstance(info.get("name"), str) or not info["name"].strip():
        raise PluginError("Its plugin.json has no name")
    api = info.get("api")
    if not isinstance(api, int) or api < 1:
        raise PluginError("Its plugin.json doesn't say which plugin version it's written for (\"api\": 1)")
    if api > API:
        raise PluginError(f"It needs a newer Recap Studio (plugin version {api}; this one gives {API})")
    return {"id": pid, "name": info["name"].strip()[:80], "version": str(info.get("version") or "1")[:20],
            "summary": str(info.get("summary") or "").strip()[:300], "api": api}


def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _off() -> set:
    return set(_state().get("off") or [])


def found() -> list[dict]:
    """Every feature plugin on this PC, one entry per id (the newer copy when both places have one), with what's
    wrong with it when it can't be used."""
    off = _off()
    best: dict[str, dict] = {}
    for where, root in (("bundled", BUNDLED_DIR), ("installed", INSTALLED_DIR)):
        for folder in sorted(root.iterdir()) if root.is_dir() else []:
            if not folder.is_dir() or folder.name.startswith((".", "_")) or not (folder / "plugin.json").exists():
                continue
            entry = {"id": folder.name, "name": folder.name, "version": "", "summary": "", "where": where,
                     "folder": folder}
            try:
                entry.update(read_info(folder))
            except PluginError as e:
                entry["error"] = str(e)
            had = best.get(entry["id"])
            if had and not had.get("error") and (entry.get("error") or _version(had["version"]) >= _version(entry["version"])):
                continue
            best[entry["id"]] = entry
    for entry in best.values():
        entry["enabled"] = entry["id"] not in off and not entry.get("error")
        entry["web"] = (entry["folder"] / "web" / "plugin.js").is_file()
    return sorted(best.values(), key=lambda e: e["name"].lower())


def public() -> list[dict]:
    """found(), for the page: with whether each one is running now."""
    out = []
    for e in found():
        run = loaded.get(e["id"])
        out.append({k: v for k, v in e.items() if k != "folder"}
                   | {"running": bool(run) and not run.get("error"), "web": bool(run) and run.get("web", False),
                      "error": (run or {}).get("error") or e.get("error")})
    return out


# ---- what a plugin is given ----------------------------------------------------------------------------------------

class Plugin:
    """What a plugin's setup(plugin) is given, to register what it adds:
      plugin.router                    a FastAPI APIRouter, served under /api/plugins/<id>/
      plugin.step(name, label, run)    a step of its own, run on its own like the extra steps: run(job), with
                                       job["project"] the project's id; named "<id>.<name>"
      plugin.on(event, function)       called with keyword arguments: "project_created" (project),
                                       "job_done" (job: its status is done, error or cancelled)
      plugin.every(seconds, function)  function() again and again, that many seconds apart, on a thread of its own
      plugin.make_recap(project_id)    start Make the recap for a project (what isn't done yet); its job
      plugin.run(project_id, name)     start one of its own steps for a project, as a job of its own; its job
      plugin.job(project_id)           the project's latest job (status, step, progress, pause_reason), or None
      plugin.project_setting(name, default)
                                       a setting each project keeps, "<id>.<name>" (New project, project files)
      plugin.settings_filter(function) function(settings) -> {key: value} it changes for a project (app-wide keys
                                       too, such as the voice); never saved
      plugin.script_instructions(function)
                                       function(settings) -> more instructions for the narration's writer
      plugin.title_instructions(function)
                                       function(settings) -> more instructions for the YouTube title and thumbnail
      plugin.language(code, name, names=None, reader=None, ready=None, fix=None)
                                       a language a comic can be in (read, then translated into English): names, how
                                       its names are written; reader() -> RapidOCR's arguments for its own models
                                       (None: the built-in reader); ready(job) makes sure those are downloaded;
                                       fix(text) -> a bubble's text read, with what its reader always gets wrong
                                       put right
      plugin.scenes(function)          function(project, chapter, items) -> the chapter's scenes to render (order,
                                       length, what's shown), or None to leave them
      plugin.overlay(function)         function(settings) -> a picture laid over every frame (video.Overlay), or None
      plugin.file_type(ext, name, chapters, pages)
                                       a kind of file chapters are imported from (".epub"), wherever the app takes
                                       them (Import folder, dropped files): chapters(path) -> [{"name", "pages"}], one
                                       entry a chapter ("name" None: named after the file), "pages" a list of strings
                                       of its own (raise ValueError with why it can't be read); pages(path, pages) ->
                                       (name, bytes) for each page's picture, in order
      plugin.data_dir                  a folder for its own files (data\\plugin-data\\<id>)
      plugin.folder, plugin.id, plugin.name, plugin.version
    Anything else it needs, it imports from the app (from recap import store, importer); those can change between
    versions, so the plugin list's plugins are tested with each release.
    """

    def __init__(self, info: dict, folder: Path):
        self.id, self.name, self.version, self.folder = info["id"], info["name"], info["version"], folder
        self.router = APIRouter(prefix=f"/api/plugins/{self.id}")
        self.undo = []  # what takes its registrations back, if its setup fails partway
        self.timers = []  # (seconds, function) from every()

    def _add(self, hooks: list, function) -> None:
        hooks.append(function)
        self.undo.append(lambda: hooks.remove(function) if function in hooks else None)

    def project_setting(self, name: str, default) -> str:
        if not re.fullmatch(r"[a-z0-9_]{1,40}", name):
            raise PluginError(f"{name} isn't a setting's name (lower-case letters, digits and _)")
        key = f"{self.id}.{name}"
        had = key in store.DEFAULT_SETTINGS
        store.DEFAULT_SETTINGS[key] = default
        store.NEW_PROJECT_KEYS.add(key)
        if not had:
            self.undo.append(lambda: (store.DEFAULT_SETTINGS.pop(key, None), store.NEW_PROJECT_KEYS.discard(key)))
        return key

    def settings_filter(self, function) -> None:
        self._add(store.SETTINGS_FILTERS, function)

    def script_instructions(self, function) -> None:
        from . import script
        self._add(script.EXTRA, function)

    def title_instructions(self, function) -> None:
        from . import youtube
        self._add(youtube.EXTRA, function)

    def language(self, code: str, name: str, names: str | None = None, reader=None, ready=None, fix=None) -> None:
        from . import ocr, translate
        if not re.fullmatch(r"[a-z]{2,3}(-[a-z0-9]{2,8})?", code) or code in ("en", "ko", "zh", "ja"):
            raise PluginError(f"{code} isn't a language code a plugin can add (like es or pt-br)")
        ocr.LANGUAGES[code], ocr.READERS[code] = name, {"args": reader, "ready": ready, "fix": fix}
        if names:
            translate.NAMES[code] = names
        self.undo.append(lambda: (ocr.LANGUAGES.pop(code, None), ocr.READERS.pop(code, None),
                                  translate.NAMES.pop(code, None)))

    def scenes(self, function) -> None:
        from . import pipeline
        self._add(pipeline.SCENE_HOOKS, function)

    def overlay(self, function) -> None:
        from . import video
        self._add(video.OVERLAYS, function)

    def file_type(self, ext: str, name: str, chapters, pages) -> None:
        from . import importer
        ext = ext.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,10}", ext) or ext in importer.IMAGE_EXT | importer.ARCHIVE_EXT:
            raise PluginError(f"{ext} isn't a file type a plugin can add (like .epub)")
        if ext in importer.FILE_TYPES:
            raise PluginError(f"Another plugin reads {ext} files already")
        importer.FILE_TYPES[ext] = {"name": name, "chapters": chapters, "pages": pages}
        self.undo.append(lambda: importer.FILE_TYPES.pop(ext, None))

    @property
    def data_dir(self) -> Path:
        d = DATA_DIR / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def step(self, name: str, label: str, run) -> str:
        from . import pipeline  # (pipeline tells plugins when jobs end)
        full = f"{self.id}.{name}"
        pipeline.register_step(full, label, run)
        self.undo.append(lambda: pipeline.unregister_step(full))
        return full

    def on(self, event: str, function) -> None:
        _handlers.setdefault(event, []).append((self.id, function))

    def every(self, seconds: float, function) -> None:
        self.timers.append((seconds, function))  # (started once its setup has gone through)

    def _start_timers(self) -> None:
        for seconds, function in self.timers:
            def loop(seconds=seconds, function=function):
                while True:
                    time.sleep(seconds)
                    try:
                        function()
                    except Exception:  # logged; it runs again next time
                        print(f"Plugin {self.id}:", file=sys.stderr)
                        traceback.print_exc()
            threading.Thread(target=loop, daemon=True, name=f"plugin-{self.id}").start()

    @staticmethod
    def make_recap(pid: str) -> dict:
        from . import pipeline
        job = pipeline.latest_for(pid)
        if job and job["status"] in pipeline.ACTIVE:
            raise PluginError("A job is already running for this project")
        return pipeline.public(pipeline.submit(pid, list(pipeline.STEPS), mode="missing"))

    def run(self, pid: str, name: str) -> dict:
        from . import pipeline
        job = pipeline.latest_for(pid)
        if job and job["status"] in pipeline.ACTIVE:
            raise PluginError("A job is already running for this project")
        return pipeline.public(pipeline.submit(pid, [f"{self.id}.{name}"]))

    @staticmethod
    def job(pid: str) -> dict | None:
        from . import pipeline
        return pipeline.public(pipeline.latest_for(pid))


def load(app) -> None:
    """Load every feature plugin that's switched on (once, as the app starts)."""
    for entry in found():
        if not entry["enabled"]:
            continue
        folder, pid = entry["folder"], entry["id"]
        run = {"web": entry["web"]}
        plugin = Plugin(entry, folder)
        try:
            init = folder / "__init__.py"
            if init.is_file():
                name = "recap_plugin_" + pid.replace("-", "_")
                spec = importlib.util.spec_from_file_location(name, init, submodule_search_locations=[str(folder)])
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                if callable(getattr(module, "setup", None)):
                    module.setup(plugin)
                if plugin.router.routes:
                    app.include_router(plugin.router)
                plugin._start_timers()
        except Exception as e:  # a broken plugin is listed with its error; the app runs without it
            traceback.print_exc()
            run["error"] = f"It failed to start: {type(e).__name__}: {e}"
            for handlers in _handlers.values():
                handlers[:] = [h for h in handlers if h[0] != pid]
            for undo in reversed(plugin.undo):  # its hooks, settings, steps and language: taken back
                undo()
        loaded[pid] = {**entry, **run}


def web_file(pid: str, path: str) -> Path | None:
    """A file from a running plugin's web folder, for the page."""
    run = loaded.get(pid)
    if not run or run.get("error"):
        return None
    base = (run["folder"] / "web").resolve()
    target = (base / path).resolve()
    return target if base in target.parents and target.is_file() else None


def emit(event: str, **data) -> None:
    """Tell the plugins listening for an event; one that fails is logged, not passed on."""
    for pid, function in list(_handlers.get(event, [])):
        try:
            function(**data)
        except Exception:
            print(f"Plugin {pid} failed on {event}:", file=sys.stderr)
            traceback.print_exc()


# ---- switching, installing and removing (from the next start) ------------------------------------------------------

def set_enabled(pid: str, on: bool) -> None:
    global changed
    with _lock:
        state = _state()
        off = set(state.get("off") or [])
        (off.discard if on else off.add)(pid)
        state["off"] = sorted(off)
        INSTALLED_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")
        changed = True


def install_zip(pid: str, data: bytes, sha256: str) -> dict:
    """Install (or update) a feature plugin from its zip, after checking it against the list's checksum."""
    global changed
    if not ID.fullmatch(pid):
        raise PluginError(f"{pid} isn't a plugin id")
    if len(data) > MAX_ZIP:
        raise PluginError("That plugin is too big (over 20 MB)")
    if hashlib.sha256(data).hexdigest() != sha256.lower():
        raise PluginError("It doesn't match the list's checksum, so it wasn't installed")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PluginError("The plugin's file isn't a zip")
    files = [i for i in z.infolist() if not i.is_dir()]
    if sum(i.file_size for i in files) > MAX_UNPACKED:
        raise PluginError("That plugin unpacks to over 60 MB")
    with _lock:
        INSTALLED_DIR.mkdir(parents=True, exist_ok=True)
        new, old, dest = INSTALLED_DIR / f".{pid}.new", INSTALLED_DIR / f".{pid}.old", INSTALLED_DIR / pid
        shutil.rmtree(new, ignore_errors=True)
        try:
            for i in files:
                parts = i.filename.replace("\\", "/").split("/")
                if i.filename.startswith(("/", "\\")) or ":" in i.filename or any(p in ("", ".", "..") for p in parts):
                    raise PluginError(f"The plugin's zip has a file it can't hold ({i.filename})")
                target = new.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(i) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
            new.mkdir(exist_ok=True)
            info = read_info(new, pid)  # checked before it replaces anything
            if dest.exists():
                _swap(new, old, dest)
            else:
                new.rename(dest)
        except BaseException:
            shutil.rmtree(new, ignore_errors=True)
            raise
        changed = True
        return info


def _swap(new: Path, old: Path, dest: Path) -> None:
    """Put the new copy where the old one was (keeping the old one until the new one is in place)."""
    shutil.rmtree(old, ignore_errors=True)
    dest.rename(old)
    try:
        new.rename(dest)
    except OSError:
        old.rename(dest)
        raise
    shutil.rmtree(old, ignore_errors=True)


def remove(pid: str) -> None:
    """Remove an installed plugin (Full Suite's own stay; they can be switched off)."""
    global changed
    folder = INSTALLED_DIR / pid
    if not ID.fullmatch(pid) or not (folder / "plugin.json").exists():
        raise PluginError("Only plugins installed from Recap Studio's list can be removed; switch this one off instead")
    with _lock:
        shutil.rmtree(folder)
        changed = True
