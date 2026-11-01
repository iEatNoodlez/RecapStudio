"""Phone access (Settings → Phone): Recap Studio's phone page on a phone on the same Wi-Fi, to see how the recaps
are going and why one stopped, pause it, or run it again (after topping up a script writer's credit, say).

When it's on, a second listener serves the app on this PC's network address (the app's own stays on 127.0.0.1;
the second one runs without the app's startup, so there's still one job worker). A request from another device
gets through only:
- from a private network address (home Wi-Fi; never the internet, and not Tailscale's 100.x addresses);
- with the phone cookie: the random key the QR code in Settings carries, kept by the phone when it opens the
  link, so it scans once. Forget phones makes a new key: phones that had the old one have to scan again;
- for the phone page and what it uses (ALLOWED): each project's status, running, pausing and cancelling its
  jobs, two of its settings, and its output (the video and thumbnail). Settings, API keys and the PC's folders
  stay on the PC.
"""
import ipaddress
import json
import re
import secrets
import socket
import threading
import time

from . import store

FILE = store.DATA_ROOT / "phone.json"
PORT = 8790
COOKIE = "recap_phone"
KEEP_DAYS = 400  # how long a phone keeps the cookie (as long as browsers allow)
ALLOWED = [(method, re.compile(path)) for method, path in (
    ("GET", r"/phone"), ("GET", r"/phone\.js"), ("GET", r"/icon\.ico"),
    ("GET", r"/api/m/projects(/[0-9a-f]+)?"),
    ("PUT", r"/api/m/projects/[0-9a-f]+/settings"),
    ("POST", r"/api/projects/[0-9a-f]+/run"),
    ("POST", r"/api/jobs/[0-9a-f]+/(pause|resume|cancel)"),
    ("GET", r"/files/[0-9a-f]+/output/[^/]+"),
)]

# home networks (Python's is_private would also take documentation and other special ranges)
HOME = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
                                          "fc00::/7", "fe80::/10")]

_lock = threading.Lock()
_server = None   # the second listener, while it runs
_error = ""      # why it couldn't start


def settings() -> dict:
    """{"enabled", "port", "key"}, with a key made the first time."""
    try:
        s = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = {}
    if not s.get("key"):
        s = {"enabled": False, "port": PORT, **s, "key": secrets.token_urlsafe(18)}
        _save(s)
    return {"enabled": bool(s.get("enabled")), "port": int(s.get("port") or PORT), "key": s["key"]}


def _save(s: dict):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(s, indent=1), encoding="utf-8")


def lan_ip() -> str:
    """This PC's address on the home network (the one a packet to a private address would leave from)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.254.254.254", 1))  # (nothing is sent: it only picks the network card)
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def link(s: dict | None = None) -> str:
    """What the QR code opens: the phone page, with the key for the cookie."""
    s = s or settings()
    return f"http://{lan_ip()}:{s['port']}/phone?key={s['key']}"


def status() -> dict:
    s = settings()
    running = _server is not None and _server.started
    return {"enabled": s["enabled"], "running": running, "url": link(s) if running else "",
            "address": f"http://{lan_ip()}:{s['port']}/phone", "error": _error}


def local(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check(method: str, path: str, host: str, cookie: str | None, key: str | None) -> str:
    """For a request from another device: "ok", "pair" (the QR code's link: set the cookie), or why it's refused:
    "not-home" (not a home network address), "no-key", "not-here" (something the phone page doesn't do)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "not-home"
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if not any(ip.version == n.version and ip in n for n in HOME):
        return "not-home"
    want = settings()["key"]
    if path == "/phone" and key:
        return "pair" if secrets.compare_digest(key, want) else "no-key"
    if not cookie or not secrets.compare_digest(cookie, want):
        return "no-key"
    if not any(m == method and rx.fullmatch(path) for m, rx in ALLOWED):
        return "not-here"
    return "ok"


def start(app) -> dict:
    """Open the phone listener (and remember it's on)."""
    global _server, _error
    with _lock:
        s = settings()
        _save({**s, "enabled": True})
        if _server is not None:
            return status()
        import uvicorn
        port = s["port"] if _free(s["port"]) else _any_port()
        if port != s["port"]:
            _save({**s, "enabled": True, "port": port})
        server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning", lifespan="off"))
        threading.Thread(target=server.run, daemon=True, name="phone").start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        if server.started:
            _server, _error = server, ""
        else:
            server.should_exit = True
            _error = f"Couldn't open port {port} for the phone"
    return status()


def stop() -> dict:
    """Close the phone listener (and remember it's off)."""
    global _server
    with _lock:
        _save({**settings(), "enabled": False})
        if _server is not None:
            _server.should_exit = True
            _server = None
    return status()


def new_key() -> dict:
    """Forget phones: a new key, so the phones that scanned the old one have to scan again."""
    _save({**settings(), "key": secrets.token_urlsafe(18)})
    return status()


def restore(app):
    """At the app's start: open the phone listener when it was on."""
    if settings()["enabled"]:
        start(app)


def qr_png() -> bytes:
    """The QR code of the link, as a PNG (OpenCV makes it)."""
    import cv2
    import numpy as np
    code = cv2.QRCodeEncoder.create().encode(link())
    code = cv2.copyMakeBorder(code, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)  # the quiet zone around it
    img = cv2.resize(code, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    ok, buf = cv2.imencode(".png", np.asarray(img))
    return buf.tobytes()


# ---- what the phone page shows ------------------------------------------------------------

PHONE_SETTINGS = {"background_music": bool, "typeset": bool}  # what it can change in a project
MAKE = ["panels", "ocr", "translate", "characters", "script", "title", "audio", "video"]  # Make the recap's steps


def _job(job) -> dict | None:
    from . import pipeline
    if not job:
        return None
    keep = ("id", "status", "step", "message", "progress", "error", "note", "waiting_for", "steps", "chapters",
            "pause", "created", "step_started", "step_estimate")
    return {**{k: job.get(k) for k in keep}, "label": pipeline.STEP_LABELS.get(job.get("step"), "")}


def _chapters(p: dict) -> list[dict]:
    """Each chapter's progress, in one word, as the app's chapter list shows it."""
    from . import pipeline, scenes
    stats = {c["id"]: {"panels": 0, "untranslated": 0, "scenes": 0, "script": 0, "lines": 0, "voice": 0}
             for c in p["chapters"]}
    for x in p["panels"]:
        s = stats.get(x["chapter"])
        if s is None or not x.get("enabled", True):
            continue
        s["panels"] += 1
        if any("english" not in b for b in x.get("bubbles") or []):
            s["untranslated"] += 1
    for g in scenes.groups(p["panels"]):
        s = stats.get(g[0]["chapter"])
        if s is None:
            continue
        s["scenes"] += 1
        s["script"] += pipeline.script_done(g[0])
        if (g[0].get("narration") or "").strip():
            s["lines"] += 1
            s["voice"] += bool(g[0].get("audio"))
    out = []
    for c in p["chapters"]:
        s = stats[c["id"]]
        if not s["panels"]:
            kind, text = "new", "Not started"
        elif s["untranslated"] and not s["script"]:
            kind, text = "todo", "Needs translation"
        elif s["script"] < s["scenes"]:
            kind, text = "todo", f"Script {s['script']} of {s['scenes']}" if s["script"] else "Needs script"
        elif s["voice"] < s["lines"]:
            kind, text = "ready", f"Voice {s['voice']} of {s['lines']}" if s["voice"] else "Script ready"
        else:
            kind, text = "done", "Voiced"
        out.append({"id": c["id"], "name": c["name"], "kind": kind, "text": text})
    return out


def summary(p: dict, full: bool = False) -> dict:
    """A project as the phone page shows it: its job (what it's doing, or why it stopped), how far its chapters
    are, its video and title; with full, each chapter and the settings the phone can change."""
    from . import ocr, pipeline
    job = pipeline.latest_for(p["id"])
    chapters = _chapters(p)
    out_ = p.get("output") or {}
    seconds = sum(x.get("seconds") or 0 for x in out_.get("parts") or [])
    counts = {k: sum(c["kind"] == k for c in chapters) for k in ("new", "todo", "ready", "done")}
    if job and job["status"] in ("queued", "running"):
        state = {"kind": "running", "text": job.get("message") or "Starting"}
    elif job and job["status"] == "paused":
        state = {"kind": "paused", "text": job.get("message") or "Paused"}
    elif job and job["status"] == "error":
        state = {"kind": "stopped", "text": f"Stopped: {job.get('error') or job.get('message')}"}
    elif not chapters:
        state = {"kind": "new", "text": "No chapters yet"}
    elif counts["done"] == len(chapters) and out_.get("video"):
        state = {"kind": "done", "text": f"Video made · {round(seconds / 60)} min"}
    else:
        state = {"kind": "todo", "text": f"{counts['done']} of {len(chapters)} chapters voiced"}
    yt = p.get("youtube") or {}
    data = {"id": p["id"], "name": p["name"], "updated": p.get("updated", 0), "state": state, "job": _job(job),
            "chapters": len(chapters), "counts": counts,
            "video": {"file": out_.get("video"), "seconds": round(seconds), "time": out_.get("time")} if out_.get("video") else None,
            "title": yt.get("title"), "thumbnail": yt.get("file"), "thumbnail_time": yt.get("created")}
    if full:
        s = p["settings"]
        data["chapter_list"] = chapters
        data["settings"] = {"background_music": bool(s.get("background_music")), "tracks": len(p.get("music") or []),
                            "typeset": bool(s.get("typeset")), "language": ocr.language(s)}
        data["make"] = MAKE
    return data


def _free(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def _any_port() -> int:
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        return s.getsockname()[1]
