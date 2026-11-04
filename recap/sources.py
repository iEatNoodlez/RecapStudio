"""Getting chapters from websites, through sources members bring: the app ships no scrapers and no links to any
site's chapters.

Three kinds of source, all optional:
- plugins: a Python file per site (docs/sources.md says how to write one), added from a file or installed from a
  plugin list: a link to an index of plugins, each with its checksum, that a member hosts. A plugin is code and runs on
  this PC with the member's permissions; each runs in a process of its own (plugin_host.py). A plugin with
  CHAPTERS = False only gives series' details (and characters(), when it has one): Recap Studio's own list
  (plugins.OFFICIAL_LIST) has those for services with public APIs, such as AniList, and no scrapers.
- gallery-dl: the command-line downloader, when the member has installed it (never part of the app: it's GPL).
- Suwayomi: a Suwayomi-Server the member runs, which runs Mihon/Tachiyomi extensions; reached over its GraphQL API.

A source can be asked for a series (from a link, from search results or from one of its lists: popular, latest, a
plugin's own), a series' chapters and a chapter's pages. Getting chapters is a job (pipeline.run_download): each
chapter's pages are downloaded into the project's pages folder and the chapter remembers where it came from, so
Check for new chapters knows what's new.
"""
import ast
import hashlib
import io
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image

from . import importer, store
from . import plugins as plugins_  # feature plugins (plugins() here lists the source plugins)
from .plugin_host import is_check, on_site

PLUGINS_DIR = store.DATA_ROOT / "plugins"
STATE_FILE = PLUGINS_DIR / "plugins.json"
API = 1                      # RECAP_PLUGIN: the plugin interface this app speaks
META = {"RECAP_PLUGIN", "NAME", "VERSION", "SITES", "LANGUAGE", "NSFW", "LISTS", "HEADERS", "DELAY", "CHAPTERS"}
CALL_TIMEOUT = 90            # seconds a plugin call may take
MAX_FILE = 1 << 20           # a plugin file or a plugin list, at most
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
IMAGE_WORKERS = 4            # pages downloaded at once
KEEP_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}  # others are saved as PNG (OpenCV reads these)
_lock = threading.Lock()


class SourceError(Exception):
    """Something a member can act on (shown as it is)."""


# ---- plugins: files in data\plugins, read without running them ---------------------------------------------------

def plugin_id(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", Path(name).stem.lower()).strip("-")[:64] or "plugin"


def read_plugin(text: str) -> dict:
    """A plugin's details, read from its source without running it. Raises SourceError when it isn't one."""
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        raise SourceError(f"It isn't valid Python (line {e.lineno}: {e.msg})")
    meta, funcs = {}, set()
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in META):
            try:
                meta[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                raise SourceError(f"{node.targets[0].id} has to be written out as a plain value (text, a number, "
                                  "a list or a dict)")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.add(node.name)
    if "RECAP_PLUGIN" not in meta:
        raise SourceError("It isn't a Recap Studio plugin (those start with RECAP_PLUGIN = 1)")
    if meta["RECAP_PLUGIN"] != API:
        raise SourceError(f"It's written for plugin version {meta['RECAP_PLUGIN']}; this app takes version {API}")
    chapters = meta.get("CHAPTERS", True) is not False  # False: a series' details only (AniList, say), no chapters
    missing = [f"{f}()" for f in ("series", "pages") if f not in funcs and (f == "series" or chapters)]
    if missing:
        raise SourceError(f"It has no {' or '.join(missing)}")
    name = meta.get("NAME")
    if not isinstance(name, str) or not name.strip():
        raise SourceError("It has no NAME")
    sites = meta.get("SITES", [])
    lists = meta.get("LISTS", [])
    headers = meta.get("HEADERS", {})
    if not (isinstance(sites, list) and all(isinstance(s, str) for s in sites)):
        raise SourceError("SITES has to be a list of site names, like [\"example.com\"]")
    if not (isinstance(lists, list) and all(isinstance(x, dict) and isinstance(x.get("id"), str)
                                            and isinstance(x.get("name"), str) for x in lists)):
        raise SourceError("LISTS has to be a list like [{\"id\": \"popular\", \"name\": \"Popular\"}]")
    if lists and "browse" not in funcs:
        raise SourceError("It has LISTS but no browse()")
    if not (isinstance(headers, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())):
        raise SourceError("HEADERS has to be a dict of text, like {\"Referer\": \"https://example.com/\"}")
    doc = ast.get_docstring(tree) or ""
    return {
        "name": name.strip()[:80], "version": str(meta.get("VERSION", "1"))[:20],
        "sites": [s.lower().removeprefix("www.") for s in sites], "language": str(meta.get("LANGUAGE", ""))[:10],
        "nsfw": bool(meta.get("NSFW", False)), "lists": [{"id": x["id"], "name": x["name"]} for x in lists],
        "headers": headers, "delay": float(meta.get("DELAY", 0) or 0), "search": "search" in funcs,
        "chapters": chapters, "characters": "characters" in funcs,
        "about": doc.strip().split("\n\n")[0].replace("\n", " ")[:300],
    }


def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def plugins() -> list[dict]:
    """The plugins in data\\plugins (files starting with _ are helpers plugins share), with their details or what's
    wrong with them."""
    state = _state()
    out = []
    for f in sorted(PLUGINS_DIR.glob("*.py")) if PLUGINS_DIR.exists() else []:
        if f.name.startswith("_"):
            continue
        pid = plugin_id(f.name)
        mine = state.get(pid, {})
        entry = {"id": pid, "file": f.name, "enabled": mine.get("enabled", True), "repo": mine.get("repo")}
        try:
            entry.update(read_plugin(f.read_text(encoding="utf-8-sig", errors="replace")))
        except SourceError as e:
            entry.update(error=str(e), name=f.name, enabled=False)
        out.append(entry)
    return out


def _plugin(pid: str) -> dict:
    p = next((x for x in plugins() if x["id"] == pid), None)
    if not p:
        raise SourceError(f"There's no plugin {pid} any more")
    if p.get("error"):
        raise SourceError(f"{p['file']}: {p['error']}")
    return p


def add_plugin(filename: str, data: bytes, repo: str | None = None) -> dict:
    """Save a plugin (replacing one with the same file name) after checking it is one."""
    if len(data) > MAX_FILE:
        raise SourceError("That file is too big for a plugin (over 1 MB)")
    info = read_plugin(data.decode("utf-8-sig", errors="replace"))
    pid = plugin_id(filename)
    with _lock:
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        (PLUGINS_DIR / f"{pid}.py").write_bytes(data)
        state = _state()
        state[pid] = {"enabled": state.get(pid, {}).get("enabled", True), "repo": repo, "added": time.time(),
                      "sha256": hashlib.sha256(data).hexdigest()}
        _save_state(state)
    _stop_host(pid)  # the next call starts the new version
    return {"id": pid, **info}


def remove_plugin(pid: str) -> None:
    _stop_host(pid)
    with _lock:
        (PLUGINS_DIR / f"{pid}.py").unlink(missing_ok=True)
        (PLUGINS_DIR / f"{pid}.log").unlink(missing_ok=True)
        state = _state()
        state.pop(pid, None)
        _save_state(state)


def set_enabled(pid: str, on: bool) -> None:
    with _lock:
        state = _state()
        state.setdefault(pid, {})["enabled"] = bool(on)
        _save_state(state)
    if not on:
        _stop_host(pid)


# ---- running a plugin: one process each, kept while the app runs -------------------------------------------------

class _Host:
    def __init__(self, pid: str):
        self.pid, self.proc, self.lock = pid, None, threading.Lock()
        self.lines: queue.Queue = queue.Queue()
        self.met: set = set()  # sites that asked this plugin's requests for their check (their passes go with it too)

    def _start(self):
        path = PLUGINS_DIR / f"{self.pid}.py"
        log = open(PLUGINS_DIR / f"{self.pid}.log", "w", encoding="utf-8", errors="replace")  # what it printed
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-X", "utf8", str(Path(__file__).with_name("plugin_host.py")), str(path)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, cwd=str(PLUGINS_DIR),
            encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log.close()
        self.lines = queue.Queue()
        threading.Thread(target=self._read, args=(self.proc, self.lines), daemon=True,
                         name=f"plugin-{self.pid}").start()

    @staticmethod
    def _read(proc, lines):
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    def call(self, name: str, *args, timeout: float = CALL_TIMEOUT, source: dict | None = None):
        """A plugin function's answer. source: the plugin's details (plugins()), for the passes its sites have."""
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            sites = list((source or {}).get("sites") or []) + sorted(self.met)
            passes = [{"domain": d, "cookies": p["cookies"], "user_agent": p["user_agent"]}
                      for d, p in _live_passes().items() if any(on_site(d, s) or on_site(s, d) for s in sites)]
            try:
                self.proc.stdin.write(json.dumps({"call": name, "args": list(args), "passes": passes}) + "\n")
                self.proc.stdin.flush()
                line = self.lines.get(timeout=timeout)
            except queue.Empty:
                self.stop()
                raise SourceError(f"The plugin took over {timeout:.0f} s to answer {name}() and was stopped")
            except OSError:
                line = None
            if line is None:
                self.stop()
                raise SourceError(f"The plugin stopped while answering {name}(); its log is in data\\plugins\\"
                                  f"{self.pid}.log")
            reply = json.loads(line)
            check = reply.get("check")
            if isinstance(check, dict) and check.get("host"):  # (even when it answered: what it got was the check)
                self.met.add(str(check["host"]))
                raise site_check(str(check["host"]), str(check.get("url") or ""), (source or {}).get("name", ""))
            if not reply.get("ok"):
                raise SourceError(f"The plugin failed in {name}(): {reply.get('error')}")
            return reply.get("result")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None


_hosts: dict[str, _Host] = {}


def _host(pid: str) -> _Host:
    with _lock:
        return _hosts.setdefault(pid, _Host(pid))


def _stop_host(pid: str) -> None:
    with _lock:
        h = _hosts.pop(pid, None)
    if h:
        with h.lock:
            h.stop()


def stop_all() -> None:
    for pid in list(_hosts):
        _stop_host(pid)


# ---- a site's check for a person (Cloudflare) ------------------------------------------------------------------------
# Some sites answer programs with Cloudflare's check (a page that runs a script in a browser, sometimes with a box to
# tick) instead of their pages. The app doesn't get past it by itself: a feature plugin (Site check) opens the site in
# a window where the member passes it, and gives the pass here (plugins.Plugin.site_pass): the site's cookies and that
# window's browser name, which the pass is tied to. Plugins' requests to that site (plugin_host.py) and the app's
# picture downloads carry them until they run out. Each time a site asks for its check, feature plugins are told
# ("site_check": host, url, source, refused) and the error says so (SiteCheck.check, for the page).

_passes: dict[str, dict] = {}  # domain: {"cookies": {name: value}, "user_agent", "expires"}


class SiteCheck(SourceError):
    """A site answered with its check for a person instead of its page. .check: {"host", "url", "source",
    "refused"} (refused: it had a pass, which it didn't take)."""

    def __init__(self, message: str, check: dict):
        super().__init__(message)
        self.check = check


def site_check(host: str, url: str, source: str = "") -> SiteCheck:
    """The error for a site that asked for its check (feature plugins are told)."""
    host = host.lower()
    refused = _pass_for(host) is not None
    check = {"host": host, "url": url, "source": source, "refused": refused}
    message = (f"{host} didn't take the pass you got in its window (it may have run out early, or the site looks at "
               f"more than the pass)" if refused else f"{host} wants to check you're a person first (Cloudflare's check)")
    if not plugins_._handlers.get("site_check"):
        message += ". The Site check plugin (Settings → Plugins) lets you pass it in a window"
    plugins_.emit("site_check", **check)
    return SiteCheck(message, check)


def set_pass(domain: str, cookies: dict, user_agent: str, expires: float | None = None) -> None:
    """A site's pass (from a window the member passed its check in): sent with requests to domain and the sites under
    it until expires (a time; None: until the app closes or it's forgotten)."""
    domain = str(domain or "").strip().lower().lstrip(".")
    if not domain or not re.fullmatch(r"[a-z0-9.-]+", domain):
        raise SourceError(f"{domain or 'Nothing'} isn't a site's name")
    if not isinstance(user_agent, str) or not user_agent.strip():
        raise SourceError("A pass needs the browser name it was got with")
    cookies = {str(k): str(v) for k, v in (cookies or {}).items() if str(k)}
    with _lock:
        _passes[domain] = {"cookies": cookies, "user_agent": user_agent.strip(), "expires": expires}


def forget_pass(domain: str) -> None:
    with _lock:
        _passes.pop(str(domain or "").strip().lower().lstrip("."), None)


def _live_passes() -> dict:
    now = time.time()
    with _lock:
        return {d: dict(p) for d, p in _passes.items() if not p["expires"] or p["expires"] > now}


def site_passes() -> dict:
    """The passes in use: {domain: {"expires", "user_agent"}} (not their cookies)."""
    return {d: {"expires": p["expires"], "user_agent": p["user_agent"]} for d, p in _live_passes().items()}


def _pass_for(host: str) -> dict | None:
    """The pass for a site (the one for the narrowest domain it's on), or None."""
    found = [(d, p) for d, p in _live_passes().items() if on_site(host, d)]
    if not found:
        return None
    d, p = max(found, key=lambda x: len(x[0]))
    return {"domain": d, **p}


def _with_pass(url: str, headers: dict) -> tuple[dict, object]:
    """The headers and cookies for a request the app makes itself (a picture), with the site's pass when it has one."""
    mine = _pass_for((urlparse(url).hostname or "").lower())
    if not mine:
        return headers, None
    jar = requests.cookies.RequestsCookieJar()
    for name, value in mine["cookies"].items():
        jar.set_cookie(requests.cookies.create_cookie(name, value, domain="." + mine["domain"]))
    return {**{k: v for k, v in headers.items() if k.lower() != "user-agent"}, "User-Agent": mine["user_agent"]}, jar


# ---- pictures the page shows (covers): fetched through the app, with the source's headers ------------------------

_images: "OrderedDict[str, tuple[str, dict]]" = OrderedDict()


def image_link(url, headers: dict | None = None, base: str = "") -> str | None:
    """A link the app's page can show a cover through (sites often refuse pictures asked for by other pages)."""
    if not isinstance(url, str) or not url:
        return None
    url = urljoin(base, url) if base else url
    if urlparse(url).scheme not in ("http", "https"):
        return None
    token = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    with _lock:
        _images[token] = (url, dict(headers or {}))
        _images.move_to_end(token)
        while len(_images) > 3000:
            _images.popitem(last=False)
    return f"/api/sources/image/{token}"


def fetch_image(token: str) -> tuple[bytes, str]:
    with _lock:
        found = _images.get(token)
    if not found:
        raise SourceError("That picture isn't known any more")
    url, headers = found
    headers, cookies = _with_pass(url, {"User-Agent": USER_AGENT, **headers})
    r = requests.get(url, headers=headers, cookies=cookies, timeout=20)
    if is_check(r):
        raise site_check(urlparse(r.url).hostname or "", r.url)
    r.raise_for_status()
    return r.content[:8 << 20], r.headers.get("Content-Type", "image/jpeg")


# ---- the sources: one way to ask any of them ----------------------------------------------------------------------

def _text(x, most=300) -> str:
    return str(x).strip()[:most] if x is not None else ""


def _number(x) -> float | None:
    try:
        return float(x) if x is not None and x != "" else None
    except (TypeError, ValueError):
        return None


KINDS = ("webtoon", "manga", "comic")  # what kind of comic a source can say a series is


def _extra(x: dict) -> dict:
    """The details a source may add to a series: its kind (one of KINDS) and the year it started."""
    year = _number(x.get("year"))
    return {"kind": x.get("kind") if x.get("kind") in KINDS else "",
            "year": int(year) if year and 1800 < year < 3000 else None}


def _items(result, headers=None, base="") -> dict:
    """Search or list results as {"items": [{"ref", "title", "cover", "summary", "kind", "year"}], "more"}."""
    more = False
    if isinstance(result, dict):
        more, result = bool(result.get("more")), result.get("items", [])
    items = []
    for x in result if isinstance(result, list) else []:
        if isinstance(x, dict) and (x.get("url") or x.get("ref")) and x.get("title"):
            items.append({"ref": _text(x.get("ref") or x.get("url"), 2000), "url": _text(x.get("url"), 2000),
                          "title": _text(x["title"], 200), "summary": _text(x.get("summary"), 600),
                          "cover": image_link(x.get("cover"), headers, base), **_extra(x)})
    return {"items": items[:200], "more": more}


ROLES = ("main", "supporting", "background")


def _characters(found, headers=None) -> list[dict]:
    """A series' characters, as a source gives them: [{"name", "aliases", "gender", "role", "description",
    "picture"}], the main ones first."""
    out = []
    for x in found if isinstance(found, list) else []:
        if not (isinstance(x, dict) and _text(x.get("name"), 100)):
            continue
        aliases = x.get("aliases") or []
        aliases = [a for a in (aliases.split(",") if isinstance(aliases, str) else aliases) if isinstance(a, str)]
        name = _text(x["name"], 100)
        aliases = [a for a in dict.fromkeys(_text(a, 100) for a in aliases) if a and a.lower() != name.lower()][:12]
        role = str(x.get("role") or "").lower()
        out.append({"name": name, "aliases": aliases,
                    "gender": x.get("gender") if x.get("gender") in ("female", "male") else "",
                    "role": role if role in ROLES else "", "description": _text(x.get("description"), 600),
                    "picture": image_link(x.get("picture"), headers)})
    order = {r: i for i, r in enumerate(ROLES)}
    out.sort(key=lambda c: order.get(c["role"], len(ROLES)))  # (a stable sort: the source's order within a role)
    return out[:200]


def _chapters(found) -> list[dict]:
    """A series' chapters, first chapter first: sorted by number when every one has a number."""
    out = []
    for x in found if isinstance(found, list) else []:
        if isinstance(x, dict) and (x.get("url") or x.get("ref")):
            number = _number(x.get("number"))
            name = _text(x.get("name"), 200)
            if not name:
                name = f"Chapter {number:g}" if number is not None else "Chapter"
            elif number is not None and not re.search(r"\d", name):
                name = f"Chapter {number:g} - {name}"  # placed by the number in its name (importer.place)
            out.append({"ref": _text(x.get("ref") or x.get("url"), 2000), "url": _text(x.get("url"), 2000),
                        "name": name, "number": number, "date": _text(x.get("date"), 40)})
    if out and all(c["number"] is not None for c in out):
        out.sort(key=lambda c: c["number"])
    return out


def _series(found, ref, headers=None, base="") -> dict:
    if not isinstance(found, dict):
        raise SourceError("The source didn't give the series' details")
    chapters = _chapters(found.get("chapters"))
    return {"ref": ref, "url": _text(found.get("url") or (ref if ref.startswith("http") else ""), 2000),
            "title": _text(found.get("title"), 200) or "Untitled", "summary": _text(found.get("summary"), 1500),
            "cover": image_link(found.get("cover"), headers, base), "chapters": chapters, **_extra(found)}


def available() -> list[dict]:
    """Every source that can be used now: enabled plugins, gallery-dl when found, Suwayomi's sources when its
    server answers."""
    out = []
    for p in plugins():
        if p["enabled"] and not p.get("error"):
            out.append({"key": f"plugin:{p['id']}", "kind": "plugin", "name": p["name"], "language": p["language"],
                        "nsfw": p["nsfw"], "sites": p["sites"], "search": p["search"], "lists": p["lists"],
                        "link": p["chapters"], "chapters": p["chapters"], "characters": p["characters"]})
    if gallery_dl.program():
        out.append({"key": "gallery-dl", "kind": "gallery-dl", "name": "gallery-dl (any site it supports)",
                    "language": "", "nsfw": False, "sites": [], "search": False, "lists": [], "link": True,
                    "chapters": True, "characters": False})
    try:
        out += suwayomi.sources()
    except SourceError:
        pass  # not running: Settings says so
    return out


def _on_site(host: str, site: str) -> bool:
    return host == site or host.endswith("." + site)


def for_link(url: str) -> dict:
    """What opens a series' link: {"key", "ref"} for the source that takes it (a plugin for its site; a Suwayomi
    source for a series the server has seen; else gallery-dl when it's installed), or {"key", "search"} to search a
    Suwayomi source for it instead (its server can't open links), or {} when nothing takes it."""
    url = url.strip()
    if url.startswith("generic:"):  # gallery-dl's way of asking for any page's pictures
        return {"key": "gallery-dl", "ref": url} if gallery_dl.program() else {}
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return {}
    for p in plugins():
        if p["enabled"] and not p.get("error") and p["chapters"] and any(_on_site(host, s) for s in p["sites"]):
            return {"key": f"plugin:{p['id']}", "ref": url}
    try:
        found = suwayomi.for_link(url, host)
        if found:
            return found
    except SourceError:
        pass
    return {"key": "gallery-dl", "ref": url} if gallery_dl.program() else {}


def _kind(key: str) -> tuple[str, str]:
    kind, _, rest = key.partition(":")
    if kind not in ("plugin", "suwayomi", "gallery-dl"):
        raise SourceError(f"Unknown source {key}")
    return kind, rest


def search(key: str, query: str, page: int = 1) -> dict:
    kind, rest = _kind(key)
    if kind == "plugin":
        p = _plugin(rest)
        if not p["search"]:
            raise SourceError(f"{p['name']} can't search")
        return _items(_host(rest).call("search", query, page, source=p), p["headers"])
    if kind == "suwayomi":
        return suwayomi.browse(rest, "SEARCH", page, query)
    raise SourceError("gallery-dl can't search: paste a series' link instead")


def browse(key: str, list_id: str, page: int = 1) -> dict:
    kind, rest = _kind(key)
    if kind == "plugin":
        p = _plugin(rest)
        if list_id not in {x["id"] for x in p["lists"]}:
            raise SourceError(f"{p['name']} has no list {list_id}")
        return _items(_host(rest).call("browse", list_id, page, source=p), p["headers"])
    if kind == "suwayomi":
        return suwayomi.browse(rest, list_id, page)
    raise SourceError("gallery-dl has no lists: paste a series' link instead")


def series(key: str, ref: str) -> dict:
    kind, rest = _kind(key)
    if kind == "plugin":
        p = _plugin(rest)
        return _series(_host(rest).call("series", ref, source=p), ref, p["headers"])
    if kind == "suwayomi":
        return suwayomi.series(rest, ref)
    return gallery_dl.series(ref)


def characters(key: str, ref: str) -> list[dict]:
    """A series' characters, from a plugin that has characters() (Find the series fills a project's from them)."""
    kind, rest = _kind(key)
    p = _plugin(rest) if kind == "plugin" else None
    if not p or not p["characters"]:
        raise SourceError(f"{p['name'] if p else key} doesn't list characters")
    return _characters(_host(rest).call("characters", ref, source=p), p["headers"])


def _pages(key: str, ref: str) -> tuple[list[dict], float]:
    """A chapter's pictures to download ([{"url", "headers"}]) and the wait between them."""
    kind, rest = _kind(key)
    if kind == "plugin":
        p = _plugin(rest)
        found = _host(rest).call("pages", ref, source=p)
        out = []
        for x in found if isinstance(found, list) else []:
            if isinstance(x, str):
                url, extra = x, {}
            elif isinstance(x, dict) and isinstance(x.get("url"), str):
                url, extra = x["url"], x.get("headers") or {}
            else:
                continue
            out.append({"url": urljoin(ref, url) if ref.startswith("http") else url, "headers": {**p["headers"], **extra}})
        return out, max(0.0, p["delay"])
    return suwayomi.pages(rest, ref), 0.0


# ---- getting chapters into a project ----------------------------------------------------------------------------

def _get(session, url, headers, check, source: str = "") -> bytes:
    last = None
    for attempt in range(3):
        check()
        try:
            sent, cookies = _with_pass(url, headers)
            r = session.get(url, headers=sent, cookies=cookies, timeout=40)
            if is_check(r):
                raise site_check(urlparse(r.url).hostname or "", r.url, source)
            if r.status_code in (429, 503) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.content
        except requests.RequestException as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise SourceError(f"A page didn't download ({url[:120]}): {last}")


def _save_picture(data: bytes, dest: Path) -> tuple[Path, int, int]:
    """Check it's a picture and save it as one the app reads (JPEG, PNG and WebP as they are, the rest as PNG)."""
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise SourceError("A page wasn't a picture (the site may have sent an error page or asked for a login)")
    ext = KEEP_FORMATS.get(im.format)
    if ext:
        path = dest.with_suffix(ext)
        path.write_bytes(data)
    else:
        path = dest.with_suffix(".png")
        im.convert("RGBA" if "A" in im.getbands() else "RGB").save(path)
    return path, im.size[0], im.size[1]


def _fetch(key: str, ref: str, folder: Path, report, check) -> list[tuple[Path, int, int]]:
    """A chapter's pictures, in order, saved into folder."""
    if key == "gallery-dl":
        files = gallery_dl.download(ref, folder, report, check)
        out = []
        for i, f in enumerate(files):
            data = f.read_bytes()
            f.unlink()
            out.append(_save_picture(data, folder / f"{i:04d}"))
        return out
    pages, delay = _pages(key, ref)
    if not pages:
        raise SourceError("The source found no pages in this chapter")
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    done = [0]
    name = _plugin(key.partition(":")[2])["name"] if key.startswith("plugin:") else ""

    def one(i):
        if delay:
            time.sleep(delay * (i % IMAGE_WORKERS))
        got = _save_picture(_get(session, pages[i]["url"], pages[i]["headers"], check, name), folder / f"{i:04d}")
        done[0] += 1
        report(done[0], len(pages))
        return got

    workers = 1 if delay else IMAGE_WORKERS
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="download") as pool:
        return list(pool.map(one, range(len(pages))))


def download(pid: str, key: str, found: dict, chapters: list[dict], where: str, skip: list[str], progress, check) -> dict:
    """Get the chapters (from series(key, ...): ref, name, url) into the project, one at a time, each placed as
    importer.place says. A chapter the project already has from this source is passed over. The project remembers
    the series, for Check for new chapters (skip: its chapters not to offer again)."""
    d = store.project_dir(pid)
    tmp = d / "downloading"
    added, pages_added = [], 0

    def remember(p):
        src = p.get("source") if (p.get("source") or {}).get("ref") == found["ref"] else None
        known = set((src or {}).get("skipped", [])) | set(skip)
        p["source"] = {"key": key, "ref": found["ref"], "url": found.get("url", ""), "title": found.get("title", ""),
                       "skipped": sorted(known)}

    store.update(pid, remember)
    for n, ch in enumerate(chapters):
        check()
        project = store.load(pid)
        if any(c.get("source_ref") == ch["ref"] for c in project["chapters"]):
            continue
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        label = f"Getting {ch['name']} ({n + 1}/{len(chapters)})"
        progress(n / len(chapters), label)
        files = _fetch(key, ch["ref"], tmp, lambda i, total, n=n: progress(
            (n + i / max(1, total)) / len(chapters), f"{label}: page {i} of {total}"), check)

        def apply(p, ch=ch, files=files):
            cid = store.new_chapter_id(p)
            pages = []
            for i, (f, w, h) in enumerate(files):
                name = f"{cid}_{i:04d}{f.suffix}"
                f.replace(d / "pages" / name)
                pages.append({"file": name, "name": f"{i + 1:03d}{f.suffix}", "w": w, "h": h, "chapter": cid})
            p["chapters"].append({"id": cid, "name": ch["name"], "source_ref": ch["ref"], "source_url": ch.get("url", "")})
            p["pages"].extend(pages)
            importer.place(p, [cid], where)
            if p["name"].strip() in ("", "Untitled") and found.get("title"):
                p["name"] = found["title"]
            added.append(cid)
        store.update(pid, apply)
        pages_added += len(files)
    shutil.rmtree(tmp, ignore_errors=True)
    project = store.load(pid)
    return {"chapters": len(added), "pages": pages_added, "duplicates": importer.duplicates(project, added)}


def new_chapters(project: dict) -> dict:
    """The chapters of the project's series it doesn't have yet (and wasn't told to pass over)."""
    src = project.get("source")
    if not src:
        raise SourceError("This project wasn't made from a source")
    found = series(src["key"], src["ref"])
    have = {c.get("source_ref") for c in project["chapters"]} | set(src.get("skipped", []))
    found["chapters"] = [c for c in found["chapters"] if c["ref"] not in have]
    return {"key": src["key"], "series": found}


new_counts: dict[str, int] = {}  # project id: new chapters found by the check when the app started


def check_all() -> None:
    """Settings → Sources → Check for new chapters when the app starts: count each project's new chapters."""
    for entry in store.list_all():
        try:
            p = store.load(entry["id"])
            if p.get("source"):
                new_counts[p["id"]] = len(new_chapters(p)["series"]["chapters"])
        except Exception:  # one source being down doesn't stop the others
            continue


# ---- plugin lists (repositories) ----------------------------------------------------------------------------------

def _fetch_file(url: str, most: int = MAX_FILE) -> bytes:
    if urlparse(url).scheme not in ("http", "https"):
        raise SourceError("A plugin list's link starts with http:// or https://")
    try:
        r = requests.get(url, headers={"User-Agent": "RecapStudio"}, timeout=30, stream=True)
        r.raise_for_status()
        data = r.raw.read(most + 1, decode_content=True)
    except requests.RequestException as e:
        raise SourceError(f"Couldn't get {url}: {e}")
    if len(data) > most:
        raise SourceError(f"{url} is over {most >> 20} MB")
    return data


def _index(url: str) -> dict:
    try:
        return json.loads(_fetch_file(url).decode("utf-8-sig", errors="replace"))
    except ValueError:
        raise SourceError("That link isn't a plugin list (it should give JSON)")


def repo(url: str) -> dict:
    """A plugin list: {"name", "url", "official", "plugins": [...]} with what's installed and what has an update.
    Feature plugins ("kind": "feature", plugins.py) are only listed from Recap Studio's own list."""
    index = _index(url)
    if not isinstance(index, dict) or not isinstance(index.get("plugins"), list):
        raise SourceError("That link isn't a plugin list (it has no \"plugins\")")
    official = url == plugins_.OFFICIAL_LIST
    mine = {"source": {p["id"]: p for p in plugins()}, "feature": {p["id"]: p for p in plugins_.found()}}
    out = []
    for x in index["plugins"]:
        if not (isinstance(x, dict) and isinstance(x.get("id"), str) and isinstance(x.get("file"), str)
                and isinstance(x.get("sha256"), str)):
            continue
        kind = "feature" if x.get("kind") == "feature" else "source"
        if kind == "feature" and not official:
            continue
        pid = plugin_id(x["id"])
        have = mine[kind].get(pid)
        version = _text(x.get("version"), 20)
        out.append({"id": pid, "kind": kind, "name": _text(x.get("name") or pid, 80), "version": version,
                    "summary": _text(x.get("summary"), 300), "language": _text(x.get("language"), 10),
                    "nsfw": bool(x.get("nsfw")), "sites": [str(s) for s in x.get("sites", [])][:20],
                    "too_new": kind == "feature" and isinstance(x.get("api"), int) and x["api"] > plugins_.API,
                    "installed": have.get("version") if have else None,
                    "update": bool(have) and have.get("version") != version})
    return {"name": _text(index.get("name") or urlparse(url).hostname, 80), "url": url, "official": official,
            "plugins": out}


def install(url: str, pid: str) -> dict:
    """Install (or update) a plugin from a plugin list, checked against the list's checksum. A feature plugin only
    from Recap Studio's own list."""
    index = _index(url)
    entry = next((x for x in index.get("plugins", []) if isinstance(x, dict) and isinstance(x.get("file"), str)
                  and plugin_id(str(x.get("id"))) == pid), None)
    if not entry:
        raise SourceError(f"The list has no plugin {pid}")
    if entry.get("kind") == "feature":
        if url != plugins_.OFFICIAL_LIST:
            raise SourceError("Plugins that add features only install from Recap Studio's own list")
        try:
            info = plugins_.install_zip(pid, _fetch_file(urljoin(url, entry["file"]), plugins_.MAX_ZIP),
                                        str(entry.get("sha256", "")))
        except plugins_.PluginError as e:
            raise SourceError(f"{entry.get('name') or pid}: {e}")
        return {**info, "kind": "feature"}
    data = _fetch_file(urljoin(url, entry["file"]))
    if hashlib.sha256(data).hexdigest() != str(entry.get("sha256", "")).lower():
        raise SourceError(f"{entry['file']} doesn't match the list's checksum, so it wasn't installed")
    return {**add_plugin(f"{pid}.py", data, repo=url), "kind": "source"}


# ---- gallery-dl: the member's own copy, run as a program --------------------------------------------------------

# gallery-dl's --dump-json messages (gallery_dl/extractor/message.py)
DIRECTORY, URL, QUEUE = 2, 3, 6


class _GalleryDL:
    """gallery-dl (github.com/mikf/gallery-dl, GPL-2.0), when the member has installed it: Settings → Sources →
    gallery-dl gives its program, else it's looked for on PATH. It opens links on the many sites it supports: a
    series' link lists its chapters (--dump-json), and a chapter is downloaded by gallery-dl itself, with its own
    cookies, headers and waits."""

    def __init__(self):
        self._version = {}

    def program(self) -> str | None:
        chosen = (store.global_settings().get("gallery_dl_path") or "").strip().strip('"')
        if chosen:
            return chosen if Path(chosen).is_file() else None
        return shutil.which("gallery-dl")

    def _run(self, args: list[str], timeout: float, check=None) -> subprocess.CompletedProcess:
        prog = self.program()
        if not prog:
            raise SourceError("gallery-dl isn't installed (Settings → Sources)")
        with _Spool() as out, _Spool() as err:
            proc = subprocess.Popen([prog, *args], stdout=out, stderr=err,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            t = time.time()
            while proc.poll() is None:
                if check:
                    try:
                        check()
                    except BaseException:
                        proc.kill()
                        raise
                if time.time() - t > timeout:
                    proc.kill()
                    raise SourceError(f"gallery-dl took over {timeout:.0f} s and was stopped")
                time.sleep(0.2)
            return subprocess.CompletedProcess(args, proc.returncode, out.text(), err.text())

    def status(self) -> dict:
        prog = self.program()
        if not prog:
            chosen = store.global_settings().get("gallery_dl_path")
            return {"found": False, "error": f"Not found: {chosen}" if chosen else None}
        if self._version.get("path") != prog:
            try:
                v = self._run(["--version"], 20).stdout.strip()
            except (OSError, SourceError) as e:
                return {"found": False, "error": f"{prog} didn't run: {e}"}
            self._version.update(path=prog, version=v)
        return {"found": True, "path": prog, "version": self._version["version"]}

    def _messages(self, url: str) -> list:
        """What gallery-dl finds at a link, without downloading: one JSON list of messages, written at the end. A
        failure is a [-1, {"error", "message"}] message (and the exit code is still 0)."""
        r = self._run(["--dump-json", url], 180)
        try:
            found = json.loads(r.stdout)
        except ValueError:
            found = None
        if not isinstance(found, list):
            raise SourceError(f"gallery-dl couldn't read that link: {_tail(r)}")
        failed = next((m[1] for m in found if isinstance(m, list) and len(m) > 1 and m[0] == -1
                       and isinstance(m[1], dict)), None)
        if failed and not any(isinstance(m, list) and m and m[0] in (URL, QUEUE) for m in found):
            raise SourceError(f"gallery-dl: {failed.get('message') or failed.get('error')}")
        return found

    def series(self, url: str) -> dict:
        found = [m for m in self._messages(url) if isinstance(m, list) and m]
        chapters, title = [], ""
        for m in found:
            meta = m[-1] if isinstance(m[-1], dict) else {}
            title = title or _text(meta.get("manga") or meta.get("series") or meta.get("title"))
            if m[0] == QUEUE and len(m) >= 3:
                number = _number(meta.get("chapter"))
                if number is not None and meta.get("chapter_minor"):
                    number = _number(f"{int(number)}{meta['chapter_minor']}") or number
                name = _text(meta.get("title"))
                label = f"Chapter {number:g}" if number is not None else ""
                chapters.append({"url": m[1], "number": number, "date": _text(meta.get("date"), 10),
                                 "name": f"{label} - {name}" if label and name else label or name or m[1]})
        if not chapters and any(m[0] == URL for m in found):  # the link was a chapter's
            meta = next((m[-1] for m in found if m[0] == DIRECTORY and isinstance(m[-1], dict)), {})
            last = urlparse(url.removeprefix("generic:")).path.rstrip("/").rsplit("/", 1)[-1]
            chapters = [{"url": url, "number": _number(meta.get("chapter")) or _number(last), "name": ""}]
        return _series({"title": title or urlparse(url).path.strip("/").split("/")[-1], "chapters": chapters}, url)

    def download(self, url: str, folder: Path, report, check) -> list[Path]:
        """A chapter downloaded by gallery-dl into folder (-D: no subfolders), its files named by page number (a
        chapter's "page", or "num" for other galleries). A run that ends with an error keeps nothing: a chapter is
        added only whole."""
        report(0, 0)
        r = self._run(["-D", str(folder), "-f", "{page|num:>04}.{extension}", "--no-mtime", url], 1800, check)
        files = sorted(f for f in folder.iterdir() if f.is_file() and not f.name.endswith(".part"))
        if r.returncode != 0:
            raise SourceError(f"gallery-dl stopped with an error (code {r.returncode}): {_tail(r)}")
        if not files:
            raise SourceError(f"gallery-dl got no pictures from that chapter: {_tail(r)}")
        report(len(files), len(files))
        return files


def _tail(r: subprocess.CompletedProcess) -> str:
    """The last of what a program printed (its error, usually)."""
    return ((r.stderr or "").strip() or (r.stdout or "").strip())[-300:] or "nothing printed"


class _Spool:
    """A temporary file for a program's output (a pipe left unread can fill up and stall it)."""

    def __enter__(self):
        import tempfile
        self.f = tempfile.TemporaryFile()
        return self

    def fileno(self):
        return self.f.fileno()

    def text(self) -> str:
        self.f.seek(0)
        return self.f.read().decode("utf-8", "replace")

    def __exit__(self, *exc):
        self.f.close()


gallery_dl = _GalleryDL()


# ---- Suwayomi: the member's own server, which runs Mihon/Tachiyomi extensions -----------------------------------

LOGIN_FILE = store.DATA_ROOT / "suwayomi_login.json"  # its user name and password, when it asks for a login


class _Suwayomi:
    """A Suwayomi-Server (github.com/Suwayomi/Suwayomi-Server, MPL-2.0) the member runs, reached over its GraphQL API
    (POST <server>/api/graphql; read from its source at v2.4.2366). Its sources (the extensions installed there, and
    its Local source) are sources here: Popular and Latest lists, search, a series' chapters and their pages, which are
    downloaded through the server (page and cover links are paths on it).

    A login, when the server asks for one (its authMode): basic auth; or the UI login, a token from its login
    mutation; or the simple login, a cookie from /login.html. Each is tried in turn with the user name and password
    given in Settings → Sources. Its source ids are 64-bit numbers sent as text (LongString); series and chapter ids
    are numbers."""

    def __init__(self):
        self._cache, self._lock = {}, threading.Lock()
        self.session, self._for = requests.Session(), None

    def url(self) -> str:
        return (store.global_settings().get("suwayomi_url") or "http://127.0.0.1:4567").strip().rstrip("/")

    def link(self, path: str | None) -> str | None:
        """A path on the server as a link (kept under a sub-path the server's address may have)."""
        if not path:
            return None
        return path if urlparse(path).scheme else self.url() + "/" + path.lstrip("/")

    def login(self) -> dict:
        try:
            found = json.loads(LOGIN_FILE.read_text(encoding="utf-8"))
            return found if found.get("user") else {}
        except (OSError, ValueError):
            return {}

    def set_login(self, user: str, password: str) -> None:
        if user:
            LOGIN_FILE.write_text(json.dumps({"user": user, "password": password}), encoding="utf-8")
        else:
            LOGIN_FILE.unlink(missing_ok=True)
        self._fresh()

    def _fresh(self):
        with self._lock:
            self.session, self._for, self._cache = requests.Session(), None, {}

    def headers(self) -> dict:
        """What a picture asked for from the server needs: the login, as a header."""
        out = {}
        if isinstance(self.session.auth, tuple):
            import base64
            out["Authorization"] = "Basic " + base64.b64encode(":".join(self.session.auth).encode()).decode()
        elif self.session.headers.get("Authorization"):
            out["Authorization"] = self.session.headers["Authorization"]
        cookies = "; ".join(f"{c.name}={c.value}" for c in self.session.cookies)
        if cookies:
            out["Cookie"] = cookies
        return out

    def _post(self, body: dict, timeout: float):
        try:
            return self.session.post(f"{self.url()}/api/graphql", json=body, timeout=timeout)
        except requests.RequestException as e:
            raise SourceError(f"Suwayomi isn't answering at {self.url()} ({e.__class__.__name__})")

    def _sign_in(self, basic: bool) -> None:
        """Log in the way the server asks: basic auth when it answered 401; else both its UI login's token and its
        simple login's cookie (its login mutation gives a token under either, but the simple login honours only the
        cookie)."""
        who = self.login()
        if not who:
            raise SourceError("Suwayomi asks for a login: give its user name and password in Settings → Sources")
        if basic:
            self.session.auth = (who["user"], who.get("password", ""))
            return
        r = self._post({"query": "mutation($u: String!, $p: String!) { login(input: {username: $u, password: $p}) "
                                 "{ accessToken } }", "variables": {"u": who["user"], "p": who.get("password", "")}}, 30)
        try:
            token = ((r.json().get("data") or {}).get("login") or {}).get("accessToken")
        except ValueError:
            token = None
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        try:
            self.session.post(f"{self.url()}/login.html", data={"user": who["user"], "pass": who.get("password", "")},
                              timeout=30, allow_redirects=False)
        except requests.RequestException as e:
            raise SourceError(f"Couldn't log in to Suwayomi: {e}")

    def _q(self, query: str, variables: dict | None = None, timeout: float = 60, partial: bool = False) -> dict:
        """A GraphQL request's data. Its first error is raised, unless partial and some data came back."""
        if self._for != self.url():
            self._fresh()
            self._for = self.url()
        body = {"query": query, "variables": variables or {}}
        for attempt in range(3):
            r = self._post(body, timeout)
            if r.status_code == 401:
                if attempt:
                    raise SourceError("Suwayomi didn't take the user name and password (Settings → Sources)")
                self._sign_in(basic=True)
                continue
            try:
                found = r.json()
            except ValueError:
                raise SourceError(f"{self.url()} isn't a Suwayomi server (HTTP {r.status_code})")
            errors = found.get("errors") or []
            if errors and any("unauthorized" in str(e.get("message", "")).lower() for e in errors):
                if attempt == 2 or (attempt and not self.login()):
                    raise SourceError("Suwayomi didn't take the user name and password (Settings → Sources)")
                self._sign_in(basic=False)
                continue
            data = found.get("data") or {}
            if errors and not (partial and any(data.values())):
                raise SourceError(f"Suwayomi: {errors[0].get('message')}")
            return data
        raise SourceError("Couldn't log in to Suwayomi")

    def _first(self, queries: list[str], variables: dict | None = None, **kw) -> dict:
        """The first of these that this server's version understands (newer fields first)."""
        for i, q in enumerate(queries):
            try:
                return self._q(q, variables, **kw)
            except SourceError as e:
                text = str(e)
                if i == len(queries) - 1 or not any(w in text for w in ("Validation", "undefined", "Cannot query",
                                                                         "FieldUndefined", "not defined")):
                    raise

    def sources(self) -> list[dict]:
        now = time.time()
        with self._lock:
            if self._cache.get("url") == self.url() and now - self._cache.get("at", 0) < 60:
                return self._cache["sources"]
        nodes = (self._first([
            "query { sources { nodes { id name displayName lang isNsfw supportsLatest homeUrl } } }",
            "query { sources { nodes { id name displayName lang isNsfw supportsLatest baseUrl } } }",
        ], timeout=5).get("sources") or {}).get("nodes", [])
        out = []
        for s in nodes:
            lists = [{"id": "POPULAR", "name": "Popular"}]
            if s.get("supportsLatest"):
                lists.append({"id": "LATEST", "name": "Latest"})
            home = s.get("homeUrl") or s.get("baseUrl") or ""
            out.append({"key": f"suwayomi:{s['id']}", "kind": "suwayomi",
                        "name": f"{s.get('displayName') or s.get('name')} (Suwayomi)",
                        "language": "" if s.get("lang") == "localsourcelang" else s.get("lang", ""),
                        "nsfw": bool(s.get("isNsfw")), "search": True, "lists": lists, "link": bool(home),
                        "chapters": True, "characters": False,
                        "sites": [(urlparse(home).hostname or "").lower().removeprefix("www.")] if home else []})
        with self._lock:
            self._cache.update(url=self.url(), at=now, sources=out)
        return out

    def status(self) -> dict:
        try:
            about = self._q("query { aboutServer { name version } }", timeout=5).get("aboutServer") or {}
            return {"ok": True, "url": self.url(), "version": about.get("version", ""), "sources": len(self.sources()),
                    "user": self.login().get("user", "")}
        except SourceError as e:
            return {"ok": False, "url": self.url(), "error": str(e), "user": self.login().get("user", "")}

    def for_link(self, url: str, host: str) -> dict | None:
        """A link to a series on one of its sources' sites: the series when the server has it already (its database
        only knows series it has shown), else a search of that source for the link's last words."""
        mine = [s for s in self.sources() if any(_on_site(host, site) for site in s["sites"])]
        if not mine:
            return None
        key = mine[0]["key"]
        path = urlparse(url).path or "/"
        for q, v in (("query($u: String!) { mangas(filter: {realUrl: {equalTo: $u}}) { nodes { id } } }", {"u": url}),
                     ("query($s: LongString!, $p: String!) { mangas(condition: {sourceId: $s, url: $p}) { nodes { id } } }",
                      {"s": key.split(":", 1)[1], "p": path})):
            try:
                nodes = (self._q(q, v, timeout=15).get("mangas") or {}).get("nodes") or []
            except SourceError:
                continue
            if nodes:
                return {"key": key, "ref": str(nodes[0]["id"])}
        words = re.sub(r"[-_]+", " ", path.rstrip("/").rsplit("/", 1)[-1]).strip()
        words = re.sub(r"\b\d{3,}\b|\.html?$", "", words).strip()
        return {"key": key, "search": words} if words else None

    def browse(self, source: str, kind: str, page: int, query: str = "") -> dict:
        found = self._q("mutation($input: FetchSourceMangaInput!) { fetchSourceManga(input: $input) { "
                        "mangas { id title thumbnailUrl description } hasNextPage } }",
                        {"input": {"source": source, "type": kind, "page": page,
                                   **({"query": query} if kind == "SEARCH" else {})}}).get("fetchSourceManga") or {}
        return _items({"items": [{"ref": str(m["id"]), "title": m.get("title"), "cover": self.link(m.get("thumbnailUrl")),
                                  "summary": m.get("description")} for m in found.get("mangas", [])],
                       "more": found.get("hasNextPage")}, self.headers())

    def series(self, source: str, ref: str) -> dict:
        mid = int(ref)
        try:
            data = self._q("mutation($id: Int!) { fetchMangaAndChapters(input: {id: $id, fetchManga: true, "
                           "fetchChapters: true}) { manga { id title description thumbnailUrl realUrl } chapters { id "
                           "name chapterNumber uploadDate sourceOrder } } }", {"id": mid}, timeout=120,
                           partial=True).get("fetchMangaAndChapters") or {}
        except SourceError as e:
            if "fetchMangaAndChapters" not in str(e) and "Validation" not in str(e):
                raise
            data = {"manga": (self._q("mutation($id: Int!) { fetchManga(input: {id: $id}) { manga { id title "
                                      "description thumbnailUrl realUrl } } }", {"id": mid}).get("fetchManga") or {}).get("manga"),
                    "chapters": (self._q("mutation($id: Int!) { fetchChapters(input: {mangaId: $id}) { chapters { id "
                                         "name chapterNumber uploadDate sourceOrder } } }", {"id": mid}, timeout=120)
                                 .get("fetchChapters") or {}).get("chapters")}
        manga, chapters = data.get("manga") or {}, data.get("chapters") or []
        chapters = sorted(chapters, key=lambda c: int(c.get("sourceOrder") or 0))  # 1 is the oldest
        return _series({"title": manga.get("title"), "summary": manga.get("description"), "url": manga.get("realUrl"),
                        "cover": self.link(manga.get("thumbnailUrl")),
                        "chapters": [{"ref": str(c["id"]), "name": c.get("name"),
                                      "number": c["chapterNumber"] if _number(c.get("chapterNumber")) not in (None, -1.0) else None,
                                      "date": _date(c.get("uploadDate"))} for c in chapters]}, ref, self.headers())

    def pages(self, source: str, ref: str) -> list[dict]:
        found = self._q("mutation($id: Int!) { fetchChapterPages(input: {chapterId: $id}) { pages } }",
                        {"id": int(ref)}, timeout=180).get("fetchChapterPages") or {}
        headers = self.headers()
        return [{"url": self.link(u), "headers": headers} for u in found.get("pages", [])]


def _date(ms) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.localtime(int(ms) / 1000)) if ms and int(ms) > 0 else ""
    except (TypeError, ValueError):
        return ""


suwayomi = _Suwayomi()
