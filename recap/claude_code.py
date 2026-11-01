"""Claude Code as the script writer (Settings → Script writer → Claude Code): chapters are narrated, and the
character scan's groups named, by Claude Code agents on the user's Claude plan instead of through the API.

The app's own writer code (pipeline._api_chapter with claude.narrate, and naming.suggest) builds every request
exactly as it would for Claude, with a stand-in for the API client. Each request is saved as a folder (prompt.md
and the pictures on labelled sheets) under the project's claude_code\\<run> folder, and an agent gets it through
the app's MCP tools (mcp.py): next_request hands it over, and submit_answer checks the answer at once and gives
it to the app's code, which saves it as it would an API answer. All of it runs inside the app, which stays open.

The app starts one fresh agent per chapter (`claude -p`, the Claude Code command line, logged in to the user's
Claude account), a few chapters at a time, with the narration skill's brief as its instructions
(recap-studio-narration in .claude\\skills next to the app folder: a series' saved brief when there is one, else the
template). A Claude Code session can answer with its own agents instead (mcp.py's write_script, agents
"session"), as the skill describes.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from . import claude, panels, pipeline, scenes, script, store

SERVER = "recap-studio"   # the MCP server's name: its tools are mcp__recap-studio__<tool> in Claude Code
MODEL_ID = "claude-code"  # what claude.ask is told the model is (the agents' model is the setting below)
MODELS = [("opus", "Opus"), ("sonnet", "Sonnet")]  # Claude Code's names for the latest of each
NAMES = "character names"  # the "chapter" the naming agent asks for
TITLE = "youtube title"  # and the title's
TRANSLATION = "translation"  # and the translating agent's (a raw comic's text, chapter by chapter)
# the narration skill: inside the app's folder when installed or cloned from GitHub, next to it in the dev setup
SKILL = next((d for d in (store.ROOT / ".claude" / "skills" / "recap-studio-narration",
                          store.ROOT.parent / ".claude" / "skills" / "recap-studio-narration") if d.is_dir()),
             store.ROOT / ".claude" / "skills" / "recap-studio-narration")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # the app runs without a console: no flashing windows
WAIT_LIMIT = 120          # longest a next_request call waits for something new, in seconds
COL_W, SHEET_H, COLS, GAP, BAND = 380, 1500, 4, 14, 28  # picture sheets: column width, height, columns, gaps
DRAFT_HEAD = ("The story so far isn't here yet: the chapter before this one is still being written. Read the pictures "
              "and draft your scenes now. When it's here, next_request says READY with it, and it's added here.")
# what an agent's last words say when it stopped for something the user has to fix
TROUBLE = {
    "login": re.compile(r"(?i)not logged in|please run /login|/login|invalid api key|authentication_error|"
                        r"oauth token has expired"),
    "limit": re.compile(r"(?i)usage limit|limit reached|hit your limit|out of extra usage|credit balance|"
                        r"rate_limit_error"),
}
PAUSE = {
    "missing": ("Paused: Claude Code wasn't found on this computer. Install it (claude.com/code) or the Claude "
                "desktop app, then press Resume."),
    "login": ("Paused: Claude Code isn't logged in. In Settings → Script writer, press Log in to Claude Code and "
              "finish in the browser, then press Resume."),
    "limit": ("Paused: your Claude plan's usage limit was reached ({detail}). Press Resume once it resets, or switch "
              "writers in Settings → Script writer."),
}

RUNS: dict[str, "Run"] = {}  # run id -> run, the latest few kept so late agents still hear how it ended
_status = {"at": 0.0, "value": None}


class AgentGone(Exception):
    """A chapter's agent stopped before its chapter was saved, twice."""


class ToolError(Exception):
    """An MCP tool call that can't be done: the message is shown to the agent."""


# ---- the claude command ----------------------------------------------------------

def find_cli() -> str | None:
    """The claude command: on PATH, from Claude Code's own installer, or the copy inside the Claude desktop app
    (the newest version there)."""
    found = shutil.which("claude")
    if found:
        return found
    native = Path.home() / ".local" / "bin" / "claude.exe"
    if native.exists():
        return str(native)
    bundled = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude-code"

    def version(exe):
        try:
            return tuple(int(x) for x in exe.parent.parent.name.split("."))
        except ValueError:
            return ()
    exes = sorted(bundled.glob("*/*/claude.exe"), key=version) if bundled.is_dir() else []
    return str(exes[-1]) if exes else None


def _env() -> dict:
    """The agents' environment: no API key or address from the environment (they run on the Claude plan, with
    Claude Code's own login), and nothing from a Claude Code session the app may have been started from."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith("ANTHROPIC_") and not (k.startswith("CLAUDE") and k != "CLAUDE_CONFIG_DIR")}


def status(refresh: bool = False) -> dict:
    """Whether Claude Code is on this computer and logged in (asked at most once a minute unless refresh)."""
    if not refresh and _status["value"] and time.time() - _status["at"] < 60:
        return _status["value"]
    cli = find_cli()
    out = {"found": bool(cli), "path": cli or "", "version": "", "logged_in": False, "method": "",
           "models": [{"id": m, "name": n} for m, n in MODELS], "skill": SKILL.is_dir()}
    if cli:
        try:
            r = subprocess.run([cli, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=30, env=_env(), creationflags=NO_WINDOW)
            out["version"] = (r.stdout.strip().split() or [""])[0]
            r = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=30, env=_env(), creationflags=NO_WINDOW)
            info = json.loads(r.stdout)
            out["logged_in"] = bool(info.get("loggedIn"))
            out["method"] = info.get("authMethod") or ""
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            out["error"] = str(e)[:200]
    _status.update(at=time.time(), value=out)
    return out


def login() -> dict:
    """Open a console window running `claude auth login` (it opens the browser to sign in to the Claude account)."""
    cli = find_cli()
    if not cli:
        raise RuntimeError("Claude Code wasn't found on this computer.")
    subprocess.Popen([cli, "auth", "login"], env=_env(), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    _status["value"] = None
    return {"started": True}


def app_url() -> str:
    """The running app's address, from data\\app.json (written when the app starts)."""
    try:
        info = json.loads((store.DATA_ROOT / "app.json").read_text(encoding="utf-8"))
        return f"http://127.0.0.1:{int(info['port'])}"
    except (OSError, ValueError, KeyError) as e:
        raise RuntimeError("The app's address isn't known (data\\app.json): start the app with run.bat.") from e


# ---- requests ----------------------------------------------------------------------

def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "_", name)


def _tiles(n, img):
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


def _sheets(pieces):
    """Pack the pieces' tiles top to bottom into columns, COLS columns per sheet: [(image, [piece numbers])]."""
    columns, col, h = [], [], 0
    for n, img in pieces:
        for t in _tiles(n, img):
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


class Request:
    """One request for an agent. state: "draft" (handed out to read before the story so far is here), "open"
    (to answer) or "done"."""

    def __init__(self, run, chapter, folder):
        self.id = f"{run.id}/{folder.name}"
        self.chapter, self.folder = chapter, folder
        self.state = "open"
        self.drafted = False   # handed out as a draft; READY once the story so far is here
        self.story = ""        # the story so far, for READY
        self.problem = None    # what was wrong with the last answer (FIX)
        self.answer = None     # the answer taken (JSON text)
        self.check = None      # data -> problem or None
        self.reading = ""      # what to read, for the agent
        self.event = threading.Event()


class Chapter:
    """A chapter (or the naming) as one agent works on it."""

    def __init__(self, chapter):
        self.name, self.id = chapter["name"], chapter["id"]
        self.state = "waiting"   # waiting, writing, done or failed
        self.detail = ""
        self.before = None       # the chapter before's ended event, when this one waits for it (in order)
        self.ended = threading.Event()
        self.project = None      # as loaded when it started (the strip's layout, for the silence check)
        self.ids, self.fixed = [], False  # the request being made: its pieces, and whether the grouping is kept
        self.kept = set()        # and its scenes kept as they are (their first pieces), when the grouping is kept
        self.proc, self.logs, self.launches = None, [], 0


def _span(scene) -> str:
    """A scene's pieces as the agent sees them: P3, or P3-P7."""
    a, b = int(scene["first_piece"]), int(scene["last_piece"])
    return f"P{a}" + (f"-P{b}" if b > a else "")


class _Stream:
    """What claude.ask gets from the stand-in client: the answer comes from an agent."""

    def __init__(self, ask, params):
        self.ask, self.params = ask, params

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        text = self.ask(self.params)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               model=MODEL_ID, usage=None)


def _stand_in(ask):
    """An API client whose messages.stream hands the request to ask(params) -> the answer's JSON text."""
    return SimpleNamespace(messages=SimpleNamespace(stream=lambda **params: _Stream(ask, params)))


class Run:
    """One job's requests and agents, in <project>\\claude_code\\<run id>."""

    def __init__(self, job, project, launch, kind="script"):
        self.job, self.pid, self.launch, self.kind = job, project["id"], launch, kind
        self.series = project["name"].strip()
        s = project["settings"]
        self.model = s.get("claude_code_model") or "opus"
        self.label = f"Claude Code ({dict(MODELS).get(self.model, self.model)})"
        self.id = time.strftime("%Y%m%d-%H%M%S") + ("" if kind == "script" else "-" + kind)
        self.dir = store.project_dir(self.pid) / "claude_code" / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.requests: list[Request] = []
        self.chapters: dict[str, Chapter] = {}
        self.lock = threading.Lock()
        self.stopped = False
        self.finished = 0.0  # when it ended: an agent asking afterwards still hears how its chapter ended
        self.cli = None
        self.collectors = []
        RUNS[self.id] = self
        for old in list(RUNS)[:-6]:  # keep the latest few
            RUNS.pop(old, None)

    # -- agents ----------------------------------------------------------------------

    def ready(self, brief: str):
        """Find Claude Code and check it's logged in (pausing the job until it is), then write the agents' brief
        and MCP settings."""
        while True:
            st = status(refresh=True)
            if not st["found"]:
                pipeline.pause_for(self.job, PAUSE["missing"])
            elif not st["logged_in"]:
                pipeline.pause_for(self.job, PAUSE["login"])
            else:
                break
        self.cli = st["path"]
        (self.dir / "brief.md").write_text(brief, encoding="utf-8")
        (self.dir / "mcp.json").write_text(json.dumps(
            {"mcpServers": {SERVER: {"type": "http", "url": app_url() + "/mcp"}}}, indent=1), encoding="utf-8")

    def _launch(self, ch: Chapter):
        """Start a fresh agent for this chapter: Claude Code with only the Read tool and this app's MCP tools."""
        ch.launches += 1
        log = self.dir / f"agent_{slug(ch.name)}_{ch.launches}.log"
        what = "Your chapter" if self.kind == "script" else "Your job"
        prompt = (f'{what}: "{ch.name}" of "{self.series}". Your instructions say how to work: start by calling '
                  f'the {SERVER} tool next_request with chapter "{ch.name}".')
        cmd = [self.cli, "-p", prompt, "--model", self.model, "--tools", "Read",
               "--allowedTools", f"Read,mcp__{SERVER}", "--permission-mode", "dontAsk",
               "--mcp-config", str(self.dir / "mcp.json"), "--strict-mcp-config",
               "--append-system-prompt-file", str(self.dir / "brief.md"),
               "--disable-slash-commands", "--no-session-persistence", "--output-format", "text"]
        with open(log, "wb") as f:
            ch.proc = subprocess.Popen(cmd, cwd=self.dir, stdout=f, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, env=_env(), creationflags=NO_WINDOW)
        ch.logs.append(log)

    def _said(self, ch: Chapter) -> str:
        try:
            return ch.logs[-1].read_text(encoding="utf-8", errors="replace").strip()
        except (OSError, IndexError):
            return ""

    def _watch(self, ch: Chapter):
        """While a chapter waits for its agent: if the agent stopped, find out why, and start another (pausing
        first when the user has something to fix: logging in, the plan's usage limit)."""
        if not self.launch or ch.proc is None or ch.proc.poll() is None:
            return
        said = self._said(ch)
        trouble = next((k for k, rx in TROUBLE.items() if rx.search(said[-2000:])), None)
        if trouble:
            pipeline.pause_for(self.job, PAUSE[trouble].format(detail=said[-160:].strip()))
            ch.launches = 0  # not the agent's fault
            if trouble == "login":
                self.cli = status(refresh=True)["path"] or self.cli
        elif ch.launches >= 2:
            raise AgentGone(f"its agent stopped before finishing, twice: {said[-300:] or 'no output'}")
        with self.lock:  # the new agent gets what the old one had, from the start
            for r in self.requests:
                if r.chapter == ch.name and r.state != "done":
                    r.drafted = False
        self._launch(ch)

    def _collect(self, ch: Chapter):
        """After a chapter: give its agent time to write its report, then keep the report."""
        if ch.proc is None:
            return
        try:
            ch.proc.wait(timeout=180)
        except subprocess.TimeoutExpired:
            ch.proc.terminate()
        with self.lock, open(self.dir / "reports.md", "a", encoding="utf-8") as out:
            out.write(f"## {ch.name} ({ch.state})\n\n{self._said(ch) or '(no report)'}\n\n")

    def close(self):
        """End the run: wait a little for the last reports, then stop any agent still running."""
        for t in self.collectors:
            t.join(timeout=200 if not self.job["cancel"] else 0)
        for ch in self.chapters.values():
            if ch.proc is not None and ch.proc.poll() is None:
                ch.proc.terminate()
        self.stopped = self.stopped or bool(self.job["cancel"])
        self.finished = time.time()

    # -- handing requests over -----------------------------------------------------

    def _new_request(self, ch: Chapter) -> Request:
        with self.lock:
            folder = self.dir / f"req_{len(self.requests) + 1:04d}"
            folder.mkdir()
            r = Request(self, ch.name, folder)
            r.state = "pending"  # not handed out until its files are written
            self.requests.append(r)
        (folder / "chapter.txt").write_text(ch.name, encoding="utf-8")
        return r

    def _wait(self, ch: Chapter, r: Request) -> str:
        while not r.event.wait(0.5):
            pipeline._check(self.job)
            self._watch(ch)
        return r.answer

    def ask_chapter(self, ch: Chapter, params) -> str:
        """claude.narrate's request for part of a chapter, answered by its agent (the answer's JSON text)."""
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
        r = self._new_request(ch)
        system = self.dir / "system.md"
        with self.lock:
            if not system.exists() or system.read_text(encoding="utf-8") != params["system"]:
                system.write_text(params["system"], encoding="utf-8")
        on_sheet, names = {}, []
        for i, (img, nums) in enumerate(_sheets([(i, np.vstack(p["imgs"])) for i, p in sorted(pieces.items())]), 1):
            name = f"sheet_{i:02d}.jpg"
            panels.imwrite(r.folder / name, img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            names.append(f"{name} (P{nums[0]}-P{nums[-1]})")
            for x in nums:
                on_sheet.setdefault(x, i)
        where = re.search(r"pieces from (.*), in reading order\.", head)
        cover = "cover" in params["output_config"]["format"]["schema"]["properties"]  # a YouTube-safe project
        r.reading = (f"Read these with the Read tool, every one:\n- the request: {r.folder / 'prompt.md'}\n"
                     f"- the instructions (the system prompt): {system}\n- the pictures, in {r.folder}: "
                     + ", ".join(names) + ". Each piece is labelled P<number> in red above it; a tall piece "
                     "continues in the next column.\nThen answer with submit_answer (request " + json.dumps(r.id) + ").")

        def write_prompt(head):
            lines = [f"# Request {r.id}: {where.group(1) if where else ch.name}", "",
                     f"Instructions (the system prompt): {system}",
                     f"Pictures, in this folder: {', '.join(names)}. Each piece is labelled P<number> in red above "
                     "it; a tall piece continues in the next column.", "", "---", "", head, ""]
            for i, p in sorted(pieces.items()):
                lines.append(f"Piece {i} (sheet {on_sheet[i]}): " + " ".join(p["lines"]))
            lines += ["", tail, "", "---", "",
                      f"Answer: call submit_answer with request {json.dumps(r.id)} and your answer, in the Output "
                      f'shape the instructions give: {{"scenes": [{{"first_piece": 1, "last_piece": 2, '
                      f'"narration": "..."}}, ...], "summary": "..."'
                      + (', "cover": [{"piece": 3, "left": 0.2, "top": 0.4, "right": 0.5, "bottom": 0.6}, ...]'
                         if cover else "")
                      + f"}}. The scenes must cover pieces 1 to {n} in order, each piece exactly once."
                      + (' "cover" lists the black boxes (see Black boxes above); [] when nothing needs covering.'
                         if cover else "")]
            (r.folder / "prompt.md").write_text("\n".join(lines), encoding="utf-8")

        r.check = lambda data: self._check_chapter(ch, r, data, n, cover)
        before, ch.before = ch.before, None  # only a chapter's first request waits for the chapter before
        if before is not None:
            task = head.rsplit("\n\n", 1)[-1]  # "Narrate the following N pieces from ...", after the story so far
            if not before.is_set():
                write_prompt(f"{DRAFT_HEAD}\n\n{task}")
                r.state = "draft"
                while not before.wait(0.5):
                    pipeline._check(self.job)
                    self._watch(ch)
            # the story so far as the chapter before left it
            context = pipeline._story_context(store.load(self.pid), {"id": ch.id})
            r.story = context or "This is the beginning of the story."
            (r.folder / "story_so_far.md").write_text(r.story, encoding="utf-8")
            head = (f"{context}\n\n" if context else "This is the beginning of the story.\n\n") + task  # as claude._content
        write_prompt(head)
        r.state = "open"
        return self._wait(ch, r)

    def ask(self, ch: Chapter, params, check) -> str:
        """Any other request (Suggest names): its text in prompt.md, its pictures as sheets, the answer checked
        by check(data) -> problem or None."""
        r = self._new_request(ch)
        (r.folder / "system.md").write_text(params["system"], encoding="utf-8")
        lines, names = [f"# Request {r.id}", "", f"Instructions (the system prompt): {r.folder / 'system.md'}", "",
                        "---", ""], []
        for b in params["messages"][0]["content"]:
            if b["type"] == "image":
                ext = {"image/png": ".png", "image/webp": ".webp"}.get(b["source"].get("media_type"), ".jpg")
                name = f"sheet_{len(names) + 1:02d}{ext}"
                (r.folder / name).write_bytes(base64.b64decode(b["source"]["data"]))
                names.append(name)
                lines.append(f"[Picture: {name}]")
            else:
                lines.append(b["text"])
            lines.append("")
        schema = params["output_config"]["format"]["schema"]
        lines += ["---", "", f"Answer: call submit_answer with request {json.dumps(r.id)} and your answer, a JSON "
                  f"object in this shape (JSON schema):", "", json.dumps(schema, indent=1)]
        (r.folder / "prompt.md").write_text("\n".join(lines), encoding="utf-8")
        r.reading = (f"Read these with the Read tool, every one:\n- the request: {r.folder / 'prompt.md'}\n"
                     f"- the instructions (the system prompt): {r.folder / 'system.md'}\n"
                     + (f"- the pictures, in {r.folder}: {', '.join(names)}\n" if names else "")
                     + f"Then answer with submit_answer (request {json.dumps(r.id)}).")
        r.check = check
        r.state = "open"
        return self._wait(ch, r)

    def _check_chapter(self, ch, r, data, n, cover) -> str | None:
        """What's wrong with a chapter's answer, as fork_bridge.py checks it: every piece in one scene, in order, a
        summary, well-formed black boxes, and no long silences in a scrolling video; for a YouTube-safe project
        (cover), wording that seems to go too far. Silences and wording are asked about once each."""
        got = data.get("scenes")
        if not isinstance(got, list) or not got:
            return '"scenes" must be a non-empty list'
        if not isinstance(data.get("summary"), str) or not data["summary"].strip():
            return '"summary" must be a non-empty string'
        try:
            order = sorted(got, key=lambda s: int(s["first_piece"]))
            nxt = 1
            for s in order:
                a, b = int(s["first_piece"]), int(s["last_piece"])
                if not isinstance(s.get("narration"), str):
                    return f"scene {a}-{b}: narration must be a string"
                if a != nxt or b < a:
                    return (f"scenes must cover pieces 1-{n} in order, each once: expected a scene starting at piece "
                            f"{nxt}, got {a}-{b}")
                nxt = b + 1
        except (KeyError, TypeError, ValueError, AttributeError):
            return 'each scene needs "first_piece" and "last_piece" (numbers) and "narration"'
        if nxt != n + 1:
            return f"scenes end at piece {nxt - 1}, but there are {n} pieces"
        if cover:
            if not isinstance(data.get("cover"), list):
                return '"cover" must be a list of black boxes ([] when nothing needs covering)'
            for c in data["cover"]:
                sides = [c.get(k) if isinstance(c, dict) else None for k in ("left", "top", "right", "bottom")]
                if (not isinstance(c, dict) or not isinstance(c.get("piece"), int) or not 1 <= c["piece"] <= n
                        or not all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in sides)
                        or sides[0] >= sides[2] or sides[1] >= sides[3]):
                    return (f"cover entry {c!r}: needs piece (1-{n}) and left < right, top < bottom, each a fraction "
                            "from 0 to 1 of that piece")
        return (self._wording(ch, r, order) if cover else None) or self._silences(ch, r, order)

    def _wording(self, ch, r, order) -> str | None:
        """A FIX for a YouTube-safe answer with lines that seem to go further than it allows (wording.find). Asked
        once per request; a second answer is taken as it is, and the chapter keeps what's still flagged (Check
        wording in the chapter list)."""
        from . import wording
        found = [f for f in wording.find([s["narration"] for s in order])
                 if int(order[f["index"]]["first_piece"]) not in ch.kept]
        if not found or (r.folder / "wording_asked").exists():
            return None
        (r.folder / "wording_asked").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        lines = [f'- {_span(order[f["index"]])}, {f["why"]}, "{f["word"]}": {f["line"]}' for f in found]
        return ("YouTube-safe wording. A word check found lines that seem to go further than a YouTube-safe recap "
                "can (the Sex scenes and wording section of your instructions):\n" + "\n".join(lines) + "\nIn a "
                "forced or pressured scene the act is said once, plainly, and taking her clothes off belongs in that "
                "one sentence; his excitement gets a few words at most, and her body never reacts or seems to enjoy "
                "it. Give those lines what she feels, says and thinks, what he says, and the aftermath instead, and "
                "keep the scenes, pieces and black boxes as they are. Then submit the whole answer again. The check "
                "is only a word search: a line that's fine where it is (a willing scene, someone who came home) can "
                "stay as it is, since this is asked once. Say in your report which flagged lines you kept, and why.")

    def _silences(self, ch, r, order) -> str | None:
        """A FIX for an answer whose lines are too short for their pictures: the scrolling video then passes the
        rest with no voice (scroll_time.py). Asked once per request; a second answer is taken as it is, and what's
        still silent goes in silences.txt."""
        from . import scroll_time
        if ch.fixed:  # the grouping is kept: no scenes to split
            return None
        found = scroll_time.silences(ch.project, ch.id, [
            (ch.ids[int(s["first_piece"]) - 1:int(s["last_piece"])], s["narration"], None) for s in order])
        if not found:
            return None
        lines = []
        for f in found:
            sc = order[f["line"]]
            line = (f"- {_span(sc)}: about {f['seconds']:.0f} s with no voice. These pieces take about "
                    f"{f['scroll']:.0f} s to scroll past, and their line ({len(sc['narration'].split())} words) is "
                    f"read in about {f['read']:.0f} s.")
            if f["quiet"]:
                line += f" After it, {', '.join(_span(order[q]) for q in f['quiet'])} (left empty) scroll by too."
            lines.append(line)
        if (r.folder / "silence_asked").exists():
            with self.lock, open(self.dir / "silences.txt", "a", encoding="utf-8") as out:
                out.write(f"{ch.name} ({r.folder.name}):\n" + "\n".join(lines) + "\n")
            return None
        (r.folder / "silence_asked").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        return ("Long silences. The video scrolls down the strip and reads each scene's line as it passes, no faster "
                "than a set speed, so a line much shorter than its pictures leaves the rest scrolling by with no "
                "voice:\n" + "\n".join(lines) + "\nSplit those pieces into more scenes, each with its own line (a line "
                "of 20 to 35 words covers about 7 to 11 seconds of scrolling), or give a line more words (never over "
                "45). Empty narration is still only for title and end cards, credits and blank pieces. In a pressured "
                "scene the extra lines carry what she thinks, says and feels, and the aftermath, never the acts. Then "
                "submit the whole answer again.")

    # -- what the MCP tools see ------------------------------------------------------

    def next_for(self, chapter: str) -> str | None:
        """What next_request says to the agent for this chapter now, or None to keep waiting."""
        with self.lock:
            if self.stopped or self.job.get("cancel"):
                return "STOPPED: the job was cancelled in the app. Stop here and report it."
            for r in self.requests:
                if r.chapter != chapter or r.state in ("done", "pending"):
                    continue
                if r.state == "draft":
                    if r.drafted:
                        continue
                    r.drafted = True
                    return (f"DRAFT {r.id}\nThe chapter before yours is still being written, so this request has no "
                            "story so far yet. Read the request and every picture sheet now and draft your scenes, "
                            "but don't submit yet. Then call next_request again: it says READY with the story so "
                            f"far.\n\n{r.reading}")
                if r.problem:
                    return f"FIX {r.id}\nYour last answer wasn't taken:\n{r.problem}\n\n{r.reading}"
                if r.drafted:
                    return (f"READY {r.id}\nThe story so far is here (below, and at the top of prompt.md). Check your "
                            f"draft against it as your brief says, then submit your answer.\n\n{r.story}")
                return f"NEXT {r.id}\n{r.reading}"
            ch = self.chapters.get(chapter)
            if ch is None:
                return None
            if ch.state == "done":
                return "CHAPTER DONE: all of it is saved. Give your report and stop."
            if ch.state == "failed":
                return f"CHAPTER FAILED: {ch.detail}\nStop and report this."
            return None

    def submit(self, r: Request, answer) -> str:
        if isinstance(answer, str):
            try:
                answer = json.loads(answer)
            except ValueError as e:
                raise ToolError(f"answer isn't JSON: {e}")
        if not isinstance(answer, dict):
            raise ToolError("answer must be a JSON object")
        with self.lock:
            if r.state == "done":
                return "Already saved. Call next_request for what's next."
            if r.state == "draft":
                (r.folder / "answer.early.json").write_text(json.dumps(answer, ensure_ascii=False, indent=1),
                                                            encoding="utf-8")
                return ("NOT YET: the story so far isn't here, so this answer wasn't taken. Call next_request: it "
                        "says READY with the story so far. Check your draft against it, then submit.")
        try:
            problem = r.check(answer)
        except Exception as e:  # noqa: BLE001  (the check itself failed: let the agent see why)
            traceback.print_exc()
            problem = f"{type(e).__name__}: {e}"
        text = json.dumps(answer, ensure_ascii=False, indent=1)
        with self.lock:
            if problem:
                r.problem = problem
                (r.folder / f"answer.bad_{int(time.time())}.json").write_text(text, encoding="utf-8")
                return f"FIX: this answer wasn't taken.\n{problem}\nCorrect it and call submit_answer again."
            r.problem, r.answer, r.state = None, text, "done"
            (r.folder / "answer.json").write_text(text, encoding="utf-8")
            for sheet in r.folder.glob("sheet_*.*"):  # the pictures aren't needed again
                sheet.unlink(missing_ok=True)
        r.event.set()
        return ("SAVED. Call next_request again: a long chapter comes in parts, and when all of it is saved it says "
                "CHAPTER DONE.")

    def summary(self) -> dict:
        return {"run": self.id, "folder": str(self.dir), "agents": "app" if self.launch else "session",
                "stopped": self.stopped,
                "chapters": {c.name: c.state + (f": {c.detail}" if c.detail else "") for c in self.chapters.values()},
                "open requests": [r.id for r in self.requests if r.state in ("open", "draft")]}

    # -- writing chapters ------------------------------------------------------------

    def _progress(self):
        if self.kind != "script":
            return
        total = len(self.chapters)
        done = sum(c.state == "done" for c in self.chapters.values())
        writing = [c.name for c in self.chapters.values() if c.state == "writing"]
        self.job.update(progress=done / max(1, total),
                        message=f"Writing script with {self.label}: {done} of {total} chapters done"
                        + (f"; writing {', '.join(writing[:4])}" if writing else ""))

    def write(self, plan, workers: int, in_order: bool) -> list[bool]:
        """Narrate the planned chapters [(chapter, heads of the scenes to write, or None to regroup)], workers at a
        time; in order: each chapter is finished only after the one before it (when it's in the plan)."""
        for chapter, _ in plan:
            self.chapters[chapter["name"]] = Chapter(chapter)
        if in_order:
            by_id = {c.id: c for c in self.chapters.values()}
            ids = [c["id"] for c in store.load(self.pid)["chapters"]]
            for a, b in zip(ids, ids[1:]):
                if a in by_id and b in by_id:
                    by_id[b].before = by_id[a].ended
        self._progress()
        try:
            with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
                return list(ex.map(lambda item: self._chapter(*item), plan))  # started in chapter order
        finally:
            self.close()

    def _chapter(self, chapter, heads) -> bool:
        ch = self.chapters[chapter["name"]]
        try:
            pipeline._check(self.job)
            p = store.load(self.pid)  # earlier chapters' summaries and narration feed this one
            groups = [g for g in scenes.groups(p["panels"]) if g[0]["chapter"] == chapter["id"]]
            write = None if heads is None else [g for g in groups if g[0]["id"] in heads]
            ch.project = p
            s = p["settings"]
            instructions = script.system_prompt(s, p["characters"], output=claude.OUTPUT_SCENES)
            api = _stand_in(lambda params: self.ask_chapter(ch, params))

            def narrate(where, flat, context, fixed, scroll):
                ch.ids, ch.fixed = [x["id"] for x in flat], fixed is not None
                ch.kept = {sc["first"] for sc in fixed or [] if not sc["write"]}
                return claude.narrate(MODEL_ID, instructions, where, flat, context, fixed=fixed, api=api,
                                      scroll=scroll, cover=bool(s.get("youtube_safe")))
            ch.state = "writing"
            self._progress()
            if self.launch:
                self._launch(ch)
            pipeline._api_chapter(self.job, p, chapter, groups, write, self.label, narrate)
            ch.state = "done"
            return True
        except pipeline.Cancelled:
            ch.state, ch.detail = "failed", "cancelled"
            raise
        except Exception as e:  # noqa: BLE001  (one chapter failing doesn't stop the others)
            traceback.print_exc()
            ch.state, ch.detail = "failed", f"{type(e).__name__}: {e}"[:400]
            return False
        finally:
            ch.ended.set()
            self._progress()
            if ch.proc is not None:
                t = threading.Thread(target=self._collect, args=(ch,), daemon=True)
                t.start()
                self.collectors.append(t)


# ---- the writer's entry points ----------------------------------------------------------

def plan(job, p) -> list[tuple[dict, set | None]]:
    """The chapters a script job writes, as pipeline._script_claude picks them: [(chapter, heads of the scenes to
    write, or None when the whole chapter is written and regrouped)]."""
    only = set(job["only"] or [])
    out = []
    for chapter in pipeline._chapters(job, p):
        groups = [g for g in scenes.groups(p["panels"]) if g[0]["chapter"] == chapter["id"]]
        if only:
            write = [g for g in groups if any(x["id"] in only for x in g)]
        elif job["mode"] == "redo" or chapter["id"] in pipeline._rewrite(job):
            write = groups
        else:
            write = [g for g in groups if not pipeline.script_done(g[0])]
        if write:
            regroup = not only and len(write) == len(groups)
            out.append((chapter, None if regroup else {g[0]["id"] for g in write}))
    return out


def write_script(job, p):
    """Write script with Claude Code (pipeline.run_script): one agent per chapter, a few at a time. A first
    narration goes in order (each chapter gets the story so far the one before it left, as the agents draft from
    the pictures meanwhile); chapters whose previous chapter already has its summary are written side by side."""
    opts = job.get("options") or {}
    s = p["settings"]
    launch = opts.get("agents", "app") != "session"
    workers = max(1, min(8, int(opts.get("workers") or s.get("claude_code_workers") or 4)))
    todo = plan(job, p)
    if not todo:
        return pipeline._nothing_to_do(job, "all scenes already have narration")
    chapters = {c["id"]: c for c in p["chapters"]}
    ids = [c["id"] for c in p["chapters"]]
    planned = {c["id"] for c, _ in todo}
    in_order = workers > 1 and any(
        ids.index(c["id"]) > 0 and ids[ids.index(c["id"]) - 1] in planned
        and not chapters[ids[ids.index(c["id"]) - 1]].get("summary") for c, _ in todo)
    run = Run(job, p, launch)
    job["claude_code_run"] = run.id
    if launch:
        run.ready(chapter_brief(p))
    results = run.write(todo, workers, in_order)
    failed = [c for c in run.chapters.values() if c.state == "failed"]
    note = f"{run.label} wrote {sum(results)} of {len(todo)} chapters."
    if failed:
        note += " Not written: " + "; ".join(f"{c.name} ({c.detail[:160]})" for c in failed[:3]) + "."
    wording = pipeline.wording_note(p["id"], [c["id"] for c, _ in todo])
    if wording:
        note += f" {wording}."
    if launch:
        note += f" The agents' reports: {run.dir / 'reports.md'}"
    job["note"] = note
    if not any(results):
        raise RuntimeError(note)


class Asker:
    """ask(instructions, blocks) for a request that isn't a chapter's, with Claude Code: one agent answers every
    part, as its brief says (naming.suggest's Suggest names, youtube.suggest's title)."""

    def __init__(self, job, project, kind, chapter, brief, schema, check):
        self.schema = schema
        self.run = Run(job, project, launch=True, kind=kind)
        self.ch = Chapter({"id": kind, "name": chapter})
        self.run.chapters[chapter] = self.ch
        self.run.ready(brief.format(series=project["name"].strip(), server=SERVER, chapter=chapter))
        self.api = _stand_in(lambda params: self.run.ask(self.ch, params, check))
        self.ch.state = "writing"
        self.run._launch(self.ch)

    def __call__(self, instructions, blocks) -> dict:
        return claude.ask(MODEL_ID, instructions, blocks, self.schema, api=self.api)

    def close(self, ok: bool):
        self.ch.state = "done" if ok else "failed"
        self.ch.ended.set()
        t = threading.Thread(target=self.run._collect, args=(self.ch,), daemon=True)
        t.start()
        self.run.collectors.append(t)
        self.run.close()


def NameAsker(job, project) -> Asker:
    return Asker(job, project, "names", NAMES, NAMES_BRIEF, _names_schema(), _check_names)


def TitleAsker(job, project) -> Asker:
    from . import youtube
    return Asker(job, project, "title", TITLE, TITLE_BRIEF, youtube.SCHEMA, youtube.problem)


def TranslateAsker(job, project) -> Asker:
    from . import translate
    return Asker(job, project, "translation", TRANSLATION, TRANSLATE_BRIEF, translate.SCHEMA, translate.problem)


def _names_schema():
    from . import naming
    return naming.SCHEMA


def _check_names(data) -> str | None:
    groups = data.get("groups")
    if not isinstance(groups, list):
        return '"groups" must be a list, one entry per group'
    need = _names_schema()["properties"]["groups"]["items"]["required"]
    for g in groups:
        missing = [k for k in need if not isinstance(g, dict) or k not in g]
        if missing:
            return f"group entry {str(g)[:80]} is missing {', '.join(missing)}"
    return None


NAMES_BRIEF = """You suggest names for the characters that Recap Studio's character scan found in the comic "{series}". \
Recap Studio is the user's app that turns comics into narrated recap videos. It hands you its request through its \
MCP tools (server "{server}"):

1. Call next_request with chapter "{chapter}". It answers with one of:
   - NEXT <request>: a request to answer. It names the files to read.
   - FIX <request> and a problem: your answer wasn't taken. Correct it and submit again.
   - WAITING: nothing yet; call it again.
   - CHAPTER DONE: everything is saved. Give a one-line report and stop.
   - CHAPTER FAILED or STOPPED: stop, and report what it said.
2. Answer a request: read prompt.md and the instructions (system.md) it names, and EVERY picture sheet, several \
per message, in parallel. Each sheet has one labelled row per group. If a sheet doesn't show as a picture (for \
example "[media removed]"), don't answer from the text alone: stop and report PICTURES MISSING.
   Then call submit_answer with the request and your answer, a JSON object in the shape prompt.md gives. It's \
checked at once: SAVED, or FIX with what to correct.
3. Call next_request again: there may be another part.

Follow the instructions exactly; never invent a name the text doesn't give. Don't do anything else in the app.
"""

TRANSLATE_BRIEF = """You translate the text of the comic "{series}" into English, for Recap Studio's narrated recap of it. \
Recap Studio is the user's app that turns comics into narrated recap videos. It hands you its requests through \
its MCP tools (server "{server}"):

1. Call next_request with chapter "{chapter}". It answers with one of:
   - NEXT <request>: a request to answer: part of a chapter. It names the files to read.
   - FIX <request> and a problem: your answer wasn't taken. Correct it and submit again.
   - WAITING: nothing yet; call it again.
   - CHAPTER DONE: everything is saved. Give a one-line report and stop.
   - CHAPTER FAILED or STOPPED: stop, and report what it said.
2. Answer a request: read prompt.md and the instructions (system.md) it names, and EVERY picture, several per \
message, in parallel: each shows a piece of the comic with its text boxes outlined and numbered. If a picture \
doesn't show (for example "[media removed]"), don't answer from the text alone: stop and report PICTURES MISSING.
   Then call submit_answer with the request and your answer, a JSON object in the shape prompt.md gives, with \
every box. It's checked at once: SAVED, or FIX with what to correct.
3. Call next_request again: there are usually more parts, one chapter after another.

Follow the instructions exactly. Don't do anything else in the app.
"""

TITLE_BRIEF = """You suggest the YouTube title and thumbnail for Recap Studio's narrated recap of the comic "{series}". \
Recap Studio is the user's app that turns comics into narrated recap videos. It hands you its request through its \
MCP tools (server "{server}"):

1. Call next_request with chapter "{chapter}". It answers with one of:
   - NEXT <request>: the request to answer. It names the files to read.
   - FIX <request> and a problem: your answer wasn't taken. Correct it and submit again.
   - WAITING: nothing yet; call it again.
   - CHAPTER DONE: it's saved. Give a one-line report and stop.
   - CHAPTER FAILED or STOPPED: stop, and report what it said.
2. Answer the request: read prompt.md and the instructions (system.md) it names, and EVERY picture sheet, in \
parallel. Each sheet holds numbered pictures to choose the thumbnail from. If a sheet doesn't show as a picture \
(for example "[media removed]"), don't answer from the text alone: stop and report PICTURES MISSING.
   Then call submit_answer with the request and your answer, a JSON object in the shape prompt.md gives. It's \
checked at once: SAVED, or FIX with what to correct.
3. Call next_request again until CHAPTER DONE.

Follow the instructions exactly. Don't do anything else in the app.
"""


# ---- the agents' brief ------------------------------------------------------------------

def _split(text: str) -> list[tuple[str, str]]:
    """A brief's "## " sections: [(title, the section's text with its heading)], the part before them titled ""."""
    out = []
    for part in re.split(r"(?m)^(?=## )", text):
        m = re.match(r"## (.+)", part)
        out.append(((m.group(1).strip() if m else ""), part))
    return out


DEFAULT_NAMES = ("Use the names in the character list (in the instructions), in its spelling, and the other names "
                 "it gives. The text is read by OCR, which often misspells names or prints them in capitals: go by "
                 "the list. Someone not on the list gets the name the story gives them; never invent one.")


def chapter_brief(p) -> str:
    """The per-chapter agents' brief, from the narration skill: the series' saved brief when it has one (with the
    template's sections on getting the chapter from the app), else the template with the app's defaults, and the
    YouTube-safe wording for a YouTube-safe project."""
    template_file = SKILL / "references" / "brief_template.md"
    if not template_file.exists():
        raise RuntimeError(f"The recap-studio-narration skill wasn't found ({template_file}): Claude Code's agents "
                           "work from its brief.")
    template = re.sub(r"(?s)<!--.*?-->\s*", "", template_file.read_text(encoding="utf-8"))
    common = dict(_split(template))
    saved = sorted((SKILL / "series").glob(f"{p['id']}-*.md"))
    if saved:  # the series' own brief: names, story and its tuned rules
        out = []
        for title, part in _split(saved[0].read_text(encoding="utf-8")):
            if title in ("How you get your chapter", "Paths"):  # (Paths and Loop: an older brief, for the file bridge)
                part = common["How you get your chapter"]
            elif title == "Loop":
                continue
            elif title in ("Don't", "Report (when CHAPTER DONE)") and title in common:
                part = common[title]
            out.append(part)
        return "".join(out)
    youtube = {}
    if p["settings"].get("youtube_safe"):
        yt = re.sub(r"(?s)<!--.*?-->\s*", "", (SKILL / "references" / "youtube_wording.md").read_text(encoding="utf-8"))
        youtube = dict(_split(yt))
    out = []
    for title, part in _split(template):
        if title == "Names and story":
            part = f"## Names and story\n{DEFAULT_NAMES}\n\n"
        elif title == "Sex scenes and wording" and youtube:
            part = youtube["Sex scenes and wording"] + youtube.get("Black boxes", "")
        out.append(part)
    text = "".join(out).replace("<SERIES>", p["name"].strip())
    return re.sub(r"(?s)\n<[^<>]*>(?=\n)", "", text)  # the template's notes to whoever fills it in


# ---- the MCP tools' side ------------------------------------------------------------------

def _run_for(chapter: str, finished: bool = False) -> "Run | None":
    """The latest run going on with this chapter (or, with finished, the latest that had it)."""
    return next((r for r in reversed(list(RUNS.values())) if chapter in r.chapters and (finished or not r.finished)),
                None)


def next_request(chapter: str, wait: float = 60) -> str:
    asked = time.time()
    end = asked + min(max(float(wait), 0.0), WAIT_LIMIT)
    while True:
        run = _run_for(chapter)
        if run is None:
            ended = _run_for(chapter, finished=True)
            if ended is not None and (ended.finished >= asked or time.time() >= end):
                # how it ended, for its agent waiting on it (a run that ended before answers only once no new one comes)
                return ended.next_for(chapter) or "WAITING: call next_request again."
            if time.time() < end:  # the job may be about to start: wait for it like for anything else
                time.sleep(0.5)
                continue
            names = sorted({n for r in RUNS.values() if not r.finished for n in r.chapters})
            return (f"NO RUN: no Claude Code run has a chapter named {json.dumps(chapter)}."
                    + (f" Chapters in runs now: {', '.join(names)}." if names else ""))
        text = run.next_for(chapter)
        if text:
            return text
        if time.time() >= end:
            return "WAITING: nothing new yet (the chapter before yours may still be being written). Call next_request again."
        time.sleep(0.5)


def submit_answer(request: str, answer) -> str:
    run = RUNS.get(str(request).split("/")[0])
    r = next((x for x in run.requests if x.id == request), None) if run else None
    if r is None:
        raise ToolError(f"No request {request!r}: use the id next_request gave.")
    return run.submit(r, answer)
