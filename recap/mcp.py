"""Recap Studio's MCP server, so Claude Code works with the app while it's open (no closing it to change a project).

The app answers MCP's JSON-RPC messages at POST /mcp (server.py). Claude Code reaches it through mcp_stdio.py
(set up in .mcp.json next to the app folder), which finds the open app's address in data\\app.json; the agents the app starts
itself (claude_code.py) are given the address directly. Everything a tool does goes through the app's own code
and store, as a button in the app would.

Tools: the projects and how far each chapter is; running app steps (Detect panels, Read text, Write script, Voice,
Render video, the character scan and Suggest names); writing the script with Claude Code agents (started by the
app, or the calling session's own); the agents' side of that (next_request, submit_answer); and reading and
correcting narration lines.

This module imports nothing from the app at load time, so mcp_stdio.py can answer the handshake and the tool list
while the app is closed.
"""
import json
import traceback

SERVER = "recap-studio"
VERSION = "1.0"
PROTOCOL = "2025-06-18"
WAIT_LIMIT = 120  # claude_code.WAIT_LIMIT: the longest next_request waits
INSTRUCTIONS = (
    "Recap Studio (the user's app) turns comic chapters into narrated recap videos. These tools work on the open "
    "app, so it never needs closing: changes go through its own code, as its buttons would. Projects can be named "
    "by name or id; chapters by name (\"Chapter 5\") or number. A project's steps, in order: panels (Detect "
    "panels), ocr (Read text), script (Write script), audio (Voice), video (Render video); scan and names find "
    "and name the characters. If the app is closed, open_app starts it.")

STEPS = ["panels", "ocr", "script", "audio", "video", "scan", "names", "credits", "boxes", "editors"]


def _tool(name, description, props=None, required=()):
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": props or {}, "required": list(required),
                            "additionalProperties": False}}


_PROJECT = {"type": "string", "description": "The project's name or id."}
_CHAPTERS = {"type": "array", "items": {"type": "string"},
             "description": "Chapters by name or number; leave out for all of them."}
TOOLS = [
    _tool("list_projects", "List the projects: id, name, chapters, pages and pieces."),
    _tool("project_status", "One project's settings that matter here (script writer, YouTube-safe) and, per "
          "chapter, how far it is: pieces, text read, scenes narrated, lines voiced, whether it has a summary, and "
          "for a YouTube-safe project any lines flagged by the wording check. Also its characters and the latest "
          "job.", {"project": _PROJECT}, ["project"]),
    _tool("run_steps", "Run app steps on a project, as its buttons do: panels, ocr, script, audio, video, scan "
          "(find the characters), names (suggest names for the scan's groups, with the script writer), credits, "
          "boxes, editors. Only work not done yet is done, unless redo. One job at a time per project; check it "
          "with job_status.",
          {"project": _PROJECT, "steps": {"type": "array", "items": {"type": "string", "enum": STEPS}},
           "chapters": _CHAPTERS, "redo": {"type": "boolean", "description": "Redo work already done."}},
          ["project", "steps"]),
    _tool("write_script", "Write the narration with Claude Code agents, whatever the app's script writer is set "
          "to. agents \"app\" (the default): the app starts a fresh Claude Code agent per chapter, a few at a "
          "time, with the narration skill's brief. agents \"session\": the app makes the requests and waits; you "
          "start one agent per chapter yourself (as the recap-studio-narration skill says) and each uses "
          "next_request and submit_answer. Chapters already narrated are skipped unless redo.",
          {"project": _PROJECT, "chapters": _CHAPTERS,
           "redo": {"type": "boolean", "description": "Rewrite chapters that already have narration."},
           "agents": {"type": "string", "enum": ["app", "session"]},
           "workers": {"type": "integer", "minimum": 1, "maximum": 8,
                       "description": "Chapters worked on at once (default: the app's setting, 4)."}},
          ["project"]),
    _tool("detect_characters", "Find the characters in chapters (the app's character scan) and then have the "
          "script writer suggest names for the groups it finds, for the user to accept in the app (the "
          "Characters tab). With Claude Code as the writer, a Claude Code agent suggests the names.",
          {"project": _PROJECT, "chapters": _CHAPTERS,
           "suggest_names": {"type": "boolean", "description": "Also suggest names (default true)."}},
          ["project"]),
    _tool("job_status", "The project's latest job: step, progress, message, any error, and for a Claude Code run "
          "each chapter's state.", {"project": _PROJECT}, ["project"]),
    _tool("cancel_job", "Cancel the project's running job.", {"project": _PROJECT}, ["project"]),
    _tool("next_request", "For an agent writing one chapter (or the character names) for a Claude Code run: wait "
          "for the app's next request for it. Answers NEXT (a request to answer, with the files to read), DRAFT "
          "(read and draft, the story so far isn't here yet), READY (the story so far, for the drafted request), "
          "FIX (the last answer wasn't taken, and why), WAITING (call again), CHAPTER DONE, CHAPTER FAILED or "
          "STOPPED.",
          {"chapter": {"type": "string", "description": "The chapter's name, e.g. \"Chapter 5\" (\"character "
                       "names\" for Suggest names)."},
           "wait_seconds": {"type": "integer", "minimum": 0, "maximum": WAIT_LIMIT,
                            "description": "How long to wait for something new (default 60)."}},
          ["chapter"]),
    _tool("submit_answer", "Answer a request from next_request. The answer is checked at once: SAVED, or FIX with "
          "what to correct (then submit again).",
          {"request": {"type": "string", "description": "The request's id, as next_request gave it."},
           "answer": {"type": "object", "description": "The answer, in the shape the request's prompt.md gives."}},
          ["request", "answer"]),
    _tool("get_narration", "A chapter's narration: each scene's first piece (its id), how many pieces it has and "
          "its line, plus the chapter's summary and who wrote it.",
          {"project": _PROJECT, "chapter": {"type": "string"}}, ["project", "chapter"]),
    _tool("set_narration", "Replace one scene's line (by its first piece's id, from get_narration). Its voice is "
          "cleared, so Voice reads it again.",
          {"project": _PROJECT, "piece": {"type": "string"}, "narration": {"type": "string"}},
          ["project", "piece", "narration"]),
    _tool("open_app", "Start Recap Studio if it isn't open (its window opens on the user's screen)."),
]


class ToolError(Exception):
    pass


# ---- JSON-RPC ------------------------------------------------------------------------

def result(mid, value):
    return {"jsonrpc": "2.0", "id": mid, "result": value}


def text_result(mid, text: str, error: bool = False):
    return result(mid, {"content": [{"type": "text", "text": text}], "isError": error})


def error(mid, code: int, message: str):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def handle(msg) -> dict | None:
    """The reply to one JSON-RPC message, or None for a notification."""
    if not isinstance(msg, dict):
        return error(None, -32600, "Invalid request")
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None or method is None:  # a notification (initialized, cancelled), or a reply to us
        return None
    if method == "initialize":
        return result(mid, {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                            "capabilities": {"tools": {}}, "serverInfo": {"name": SERVER, "version": VERSION},
                            "instructions": INSTRUCTIONS})
    if method == "ping":
        return result(mid, {})
    if method == "tools/list":
        return result(mid, {"tools": TOOLS})
    if method == "tools/call":
        return call(mid, params.get("name"), params.get("arguments") or {})
    return error(mid, -32601, f"Method not found: {method}")


def call(mid, name, args: dict):
    fn = globals().get(f"tool_{name}")
    if fn is None or not any(t["name"] == name for t in TOOLS):
        return error(mid, -32602, f"Unknown tool: {name}")
    try:
        out = fn(**args)
    except TypeError as e:
        return text_result(mid, f"Bad arguments for {name}: {e}", error=True)
    except Exception as e:  # noqa: BLE001  (shown to the caller)
        if type(e).__name__ not in ("ToolError", "RuntimeError", "KeyError", "ValueError", "HTTPException"):
            traceback.print_exc()
        return text_result(mid, str(getattr(e, "detail", None) or e) or type(e).__name__, error=True)
    return text_result(mid, out if isinstance(out, str) else json.dumps(out, indent=1, ensure_ascii=False))


# ---- finding things ----------------------------------------------------------------------

def _project(ref: str) -> dict:
    from . import store
    ref = str(ref).strip()
    projects = store.list_all()
    hit = ([x for x in projects if x["id"] == ref] or [x for x in projects if x["name"].lower() == ref.lower()]
           or [x for x in projects if ref.lower() in x["name"].lower()])
    if len(hit) != 1:
        names = "; ".join(f"{x['name']} ({x['id']})" for x in projects)
        raise ToolError(f"{'No' if not hit else 'More than one'} project matches {ref!r}. Projects: {names}")
    return store.load(hit[0]["id"])


def _chapter_ids(p: dict, refs) -> list[str] | None:
    if not refs:
        return None
    out = []
    for ref in refs:
        ref = str(ref).strip()
        c = next((c for c in p["chapters"] if c["name"].lower() in (ref.lower(), f"chapter {ref}".lower())
                  or c["id"] == ref), None)
        if c is None:
            raise ToolError(f"No chapter {ref!r} in {p['name']}")
        out.append(c["id"])
    return out


def _submit(p: dict, steps: list[str], chapters=None, mode="missing", options=None) -> dict:
    from . import pipeline
    current = pipeline.latest_for(p["id"])
    if current and current["status"] in pipeline.ACTIVE:
        raise ToolError(f"A job is already running for {p['name']} ({current['step']}: {current['message']}). "
                        "Wait for it (job_status) or cancel it (cancel_job).")
    return pipeline.public(pipeline.submit(p["id"], steps, None, chapters, mode, options=options))


# ---- tools ---------------------------------------------------------------------------------

def tool_list_projects():
    from . import store
    return store.list_all()


def tool_project_status(project):
    from . import pipeline, scenes
    p = _project(project)
    s = p["settings"]
    groups = scenes.groups(p["panels"])
    chapters = []
    for c in p["chapters"]:
        pieces = [x for x in p["panels"] if x["chapter"] == c["id"] and x.get("enabled", True)]
        mine = [g for g in groups if g[0]["chapter"] == c["id"]]
        lines = [g for g in mine if g[0].get("narration")]
        chapters.append({"name": c["name"], "pages": sum(1 for x in p["pages"] if x["chapter"] == c["id"]),
                         "pieces": len(pieces), "text read": sum(1 for x in pieces if pipeline.ocr_done(x)),
                         "scenes": len(mine), "narrated": sum(1 for g in mine if pipeline.script_done(g[0])),
                         "voiced": sum(1 for g in lines if g[0].get("audio")), "summary": bool(c.get("summary")),
                         "narrator": c.get("narrator", "")})
        flagged = pipeline.wording_flags(p, c)
        if flagged:  # YouTube-safe: lines that may go too far (Check wording in the app)
            chapters[-1]["check wording"] = [f"{w['why']}: {w['line']}" for w in flagged]
    job = pipeline.public(pipeline.latest_for(p["id"]))
    return {"id": p["id"], "name": p["name"], "script writer": s.get("llm_provider"),
            "youtube_safe": bool(s.get("youtube_safe")), "characters": [c["name"] for c in p["characters"]],
            "chapters": chapters,
            "job": {k: job.get(k) for k in ("id", "status", "step", "message", "error")} if job else None}


def tool_run_steps(project, steps, chapters=None, redo=False):
    bad = [x for x in steps if x not in STEPS]
    if bad:
        raise ToolError(f"Unknown steps {bad}: use {STEPS}")
    p = _project(project)
    job = _submit(p, steps, _chapter_ids(p, chapters), "redo" if redo else "missing")
    return f"Started job {job['id']}: {', '.join(job['steps'])}. Follow it with job_status."


def tool_write_script(project, chapters=None, redo=False, agents="app", workers=None):
    from . import claude_code
    p = _project(project)
    ids = _chapter_ids(p, chapters)
    mode = "redo" if redo else "missing"
    todo = claude_code.plan({"only": None, "chapters": ids, "mode": mode}, p)
    if not todo:
        return "Nothing to write: those chapters already have narration (use redo to write them again)."
    job = _submit(p, ["script"], ids, mode, options={"agents": "session" if agents == "session" else "app",
                                                     "workers": workers})
    names = ", ".join(c["name"] for c, _ in todo)
    if agents == "session":
        return (f"Started job {job['id']}. Chapters to write: {names}. Start one agent per chapter: each calls "
                "next_request with its chapter's name and answers with submit_answer, following the narration "
                "skill's brief. Follow the run with job_status.")
    return (f"Started job {job['id']}: the app starts a Claude Code agent per chapter for {names}. Follow it with "
            "job_status.")


def tool_detect_characters(project, chapters=None, suggest_names=True):
    p = _project(project)
    job = _submit(p, ["scan", "names"] if suggest_names else ["scan"], _chapter_ids(p, chapters))
    return (f"Started job {job['id']}: the character scan" + (", then Suggest names" if suggest_names else "")
            + ". The user reviews the groups in the app (the Characters tab). Follow it with job_status.")


def tool_job_status(project):
    from . import claude_code, pipeline
    p = _project(project)
    job = pipeline.latest_for(p["id"])
    if not job:
        return f"No job has run for {p['name']} since the app started."
    out = {k: job.get(k) for k in ("id", "status", "steps", "step", "progress", "message", "error", "note")}
    run = claude_code.RUNS.get(job.get("claude_code_run") or "")
    if run:
        out["claude code"] = run.summary()
    return out


def tool_cancel_job(project):
    from . import pipeline
    p = _project(project)
    job = pipeline.latest_for(p["id"])
    if not job or job["status"] not in pipeline.ACTIVE:
        return "No job is running for this project."
    job["cancel"] = True
    return f"Cancelling job {job['id']}."


def tool_next_request(chapter, wait_seconds=60):
    from . import claude_code
    return claude_code.next_request(chapter, wait_seconds)


def tool_submit_answer(request, answer):
    from . import claude_code
    return claude_code.submit_answer(request, answer)


def tool_get_narration(project, chapter):
    from . import scenes
    p = _project(project)
    cid = _chapter_ids(p, [chapter])[0]
    c = next(c for c in p["chapters"] if c["id"] == cid)
    return {"chapter": c["name"], "summary": c.get("summary", ""), "narrator": c.get("narrator", ""),
            "scenes": [{"piece": g[0]["id"], "pieces": len(g), "narration": g[0].get("narration", "")}
                       for g in scenes.groups(p["panels"]) if g[0]["chapter"] == cid]}


def tool_set_narration(project, piece, narration):
    from . import store
    p = _project(project)
    x = next((x for x in p["panels"] if x["id"] == piece), None)
    if x is None:
        raise ToolError(f"No piece {piece!r} in {p['name']}")
    if x.get("joined"):
        raise ToolError(f"{piece} is inside a scene, not its first piece: use the piece get_narration gives.")

    def apply(proj):
        y = next(y for y in proj["panels"] if y["id"] == piece)
        y.update(narration=narration.strip(), script_done=True, audio=None, duration=0.0)
    store.update(p["id"], apply)
    return f"Saved. {piece}'s voice was cleared: Voice reads it again."


def tool_open_app():
    return "Recap Studio is open."
