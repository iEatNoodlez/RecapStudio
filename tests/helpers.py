"""What the app's tests share: check(), a scratch data folder (never your projects), made-up projects that can be read,
narrated and rendered (coloured panels, a tone for each narration line), and the app's server in the test's process.
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

APP = Path(__file__).resolve().parent.parent  # the app's folder (with recap/ in it)
TESTS = Path(__file__).resolve().parent
fails: list[str] = []
made: list[Path] = []  # the scratch folders made (scratch)


def check(what: str, ok, extra="") -> bool:
    note = "" if ok or extra is None or isinstance(extra, str) and not extra else f"  [{extra}]"
    print(("PASS " if ok else "FAIL ") + what + note, flush=True)
    if not ok:
        fails.append(what)
    return bool(ok)


def until(cond, seconds: float = 30) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def done() -> None:
    print("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}", flush=True)
    tidy(bool(fails))
    os._exit(1 if fails else 0)  # (the server's threads don't hold it open)


def tidy(failed: bool) -> None:
    """The scratch folders: removed when every check passed, named to look at when one failed."""
    for path in made:
        if failed:
            print(f"(its files: {path})", flush=True)
        else:
            shutil.rmtree(path, ignore_errors=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def scratch(name: str = "data") -> Path:
    """A new folder in the temp folder, removed by done() when every check passed."""
    made.append(Path(tempfile.mkdtemp(prefix=f"recap-test-{name}-")))
    return made[-1]


def data_folder(settings: dict | None = None, path: Path | None = None) -> Path:
    """A data folder of its own (RECAP_DATA), with the first-run setup and the update check off."""
    data = path or scratch()
    data.mkdir(parents=True, exist_ok=True)
    (data / "settings.json").write_text(json.dumps({"setup_done": True, "check_updates": False, **(settings or {})}),
                                        encoding="utf-8")
    return data


def make_project(data: Path, name: str, pid: str | None = None, chapters: int = 1, panels: int = 2,
                 seconds: float = 1.0, settings: dict | None = None) -> str:
    """A made-up project in data/projects/<pid>/ (its folder named by id, as before folders had names): each chapter
    a page and that many coloured panels, each narrated, with a tone as its voice."""
    import cv2
    import numpy as np
    import soundfile as sf
    pid = pid or os.urandom(6).hex()
    d = data / "projects" / pid
    for sub in ("pages", "panels", "audio"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    p = {"id": pid, "name": name, "created": time.time(), "updated": time.time(), "settings": dict(settings or {}),
         "characters": [], "chapters": [], "pages": [], "panels": [], "output": None}
    rate = 24000
    for c in range(1, chapters + 1):
        cid = f"c{c:04d}"
        p["chapters"].append({"id": cid, "name": f"Chapter {c}", "source": None})
        page = np.full((900, 600, 3), 240, np.uint8)
        cv2.imwrite(str(d / "pages" / f"{cid}_0000.png"), page)
        p["pages"].append({"file": f"{cid}_0000.png", "name": "001.png", "w": 600, "h": 900, "chapter": cid})
        for k in range(panels):
            colour = [(60 * (k + c)) % 255, (90 * k) % 255, 200 - 40 * k]
            img = np.full((400, 600, 3), colour, np.uint8)
            cv2.putText(img, f"{c}.{k}", (40, 220), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 8)
            xid = f"{cid}_{k:05d}"
            cv2.imwrite(str(d / "panels" / f"{xid}.png"), img)
            t = np.arange(int(rate * seconds)) / rate
            sf.write(d / "audio" / f"{xid}.wav", (0.2 * np.sin(2 * np.pi * (330 + 40 * k) * t)).astype(np.float32), rate)
            p["panels"].append({"id": xid, "chapter": cid, "page": 0, "file": f"{xid}.png", "joined": False,
                                "text": f"Line {k} of chapter {c}.", "narration": f"Panel {k} of chapter {c} happens.",
                                "audio": f"{xid}.wav", "duration": seconds, "enabled": True, "box": [0, 0, 600, 400],
                                "ocr_done": True, "script_done": True})
    (d / "project.json").write_text(json.dumps(p, indent=1), encoding="utf-8")
    return pid


class App:
    """The app, in this process, on a data folder (made with data_folder()). Import nothing from recap before this."""

    def __init__(self, data: Path, env: dict | None = None):
        self.data = data
        os.environ.update(RECAP_DATA=str(data), **(env or {}))
        sys.path.insert(0, str(APP))
        import uvicorn
        from recap import pipeline, plugins, server, store
        self.pipeline, self.plugins, self.server, self.store = pipeline, plugins, server, store
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=self.port, log_level="warning"))
        threading.Thread(target=self._srv.run, daemon=True).start()
        while not self._srv.started:
            time.sleep(0.05)
        pipeline.start_worker()

    def get(self, path, **kw):
        return requests.get(self.base + path, timeout=120, **kw)

    def post(self, path, **kw):
        return requests.post(self.base + path, timeout=120, **kw)

    def put(self, path, **kw):
        return requests.put(self.base + path, timeout=120, **kw)

    def patch(self, path, **kw):
        return requests.patch(self.base + path, timeout=120, **kw)

    def delete(self, path, **kw):
        return requests.delete(self.base + path, timeout=120, **kw)

    def wait_job(self, pid: str, seconds: float = 300) -> dict:
        """The project's job once it's no longer queued or running."""
        until(lambda: (self.pipeline.latest_for(pid) or {}).get("status") not in ("queued", "running"), seconds)
        return self.pipeline.public(self.pipeline.latest_for(pid)) or {}
