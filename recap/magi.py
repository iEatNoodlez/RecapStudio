"""Magi v2 speaker detection: an optional add-on the user downloads from inside the app.

Magi v2 (Ragav Sachdeva, Gyungin Shin and Andrew Zisserman, University of Oxford) reads comic pages
and guesses who says each line, matching characters to pictures the user gives it. It needs PyTorch,
so it gets its own Python environment under data\\addons\\magi, apart from the app's, and nothing of
it is shipped with the app. Its license allows personal, research, non-commercial and not-for-profit
use only, so the user agrees to that before it's downloaded.

In testing on a colour webtoon chapter it named the right speaker for about 6 lines in 10, so the
script writers are told its answers are guesses.
"""
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from . import gpu, panels, store

ADDON_DIR = store.ADDONS_DIR / "magi"
ENV_DIR = ADDON_DIR / "env"
HF_DIR = ADDON_DIR / "hf"
STATE_FILE = ADDON_DIR / "installed.json"
LOG_FILE = ADDON_DIR / "worker.log"
WORKER = Path(__file__).with_name("magi_worker.py")
LICENSE = ("Magi v2 is by Ragav Sachdeva, Gyungin Shin and Andrew Zisserman (University of Oxford). Its license "
           "allows personal, research, non-commercial and not-for-profit use only; any other use needs the "
           "author's permission (huggingface.co/ragavsachdeva/magiv2).")
# pinned to the versions it was tested with
TORCH = {"cuda": (["torch==2.11.0+cu128", "torchvision==0.26.0+cu128"], "https://download.pytorch.org/whl/cu128",
                  2_762_800_000),
         "cpu": (["torch==2.11.0+cpu", "torchvision==0.26.0+cpu"], "https://download.pytorch.org/whl/cpu",
                 118_700_000)}
PACKAGES = ["transformers==4.46.3", "timm==1.0.30", "einops==0.8.2", "shapely==2.2.0", "pulp==2.9.0",
            "scipy==1.18.1", "networkx==3.7", "matplotlib==3.11.2", "pillow<13", "numpy<2.3"]
MODEL_BYTES = 2_065_300_000  # Magi v2's weights plus the text reader's tokenizer files
OTHER_BYTES = 110_000_000    # the other packages
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # the app runs without a console: no flashing windows

_status = {"state": "idle", "stage": "", "progress": 0.0, "message": "", "error": None}
_lock = threading.Lock()
_cancel = threading.Event()
_sessions = 0  # Magi processes running for jobs; the add-on can't be removed meanwhile


class Cancelled(Exception):
    pass


def env_python() -> Path:
    return ENV_DIR / "Scripts" / "python.exe" if os.name == "nt" else ENV_DIR / "bin" / "python"


def _repoint() -> None:
    """Linux: the add-on's environment points at the app's own Python, which in an AppImage moves on every start
    (its mount's folder is new each time). Point it at this run's: its pyvenv.cfg, and its python links."""
    if os.name == "nt":
        return
    cfg = ENV_DIR / "pyvenv.cfg"
    try:
        lines = cfg.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    base = Path(_base_python())
    want = {"home": str(base.parent), "executable": str(base)}
    out = []
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() in want and value.strip() != want[key.strip()]:
            line = f"{key.strip()} = {want[key.strip()]}"
        out.append(line)
    try:
        if out != lines:
            cfg.write_text("\n".join(out) + "\n", encoding="utf-8")
        for link in (ENV_DIR / "bin").iterdir():  # (python links to the base; python3 and python3.12 to python)
            if link.is_symlink() and os.path.isabs(os.readlink(link)) and Path(os.readlink(link)) != base:
                link.unlink()
                link.symlink_to(base)
    except OSError:
        pass


def installed() -> dict | None:
    """What was installed (device, size), or None when Magi isn't ready to use."""
    try:
        info = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    _repoint()
    return info if env_python().exists() else None


def _device() -> str:
    return "cuda" if gpu.free_memory_mb() is not None else "cpu"  # an NVIDIA card is all PyTorch needs


def download_bytes(device: str) -> int:
    return TORCH[device][2] + MODEL_BYTES + OTHER_BYTES


def status() -> dict:
    info = installed()
    device = info["device"] if info else _device()
    return {**_status, "installed": bool(info), "device": device, "size_gb": info.get("size_gb") if info else None,
            "download_gb": round(download_bytes(device) / 1e9, 1), "license": LICENSE, "in_use": _sessions > 0}


def _set(**kw):
    _status.update(kw)


def _base_python() -> str:
    """A console python.exe to build the add-on environment with (the app itself may run as pythonw)."""
    exe = Path(sys.base_prefix) / ("python.exe" if os.name == "nt" else "bin/python3")
    return str(exe if exe.exists() else sys.executable)


def _env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV"))}
    env.update(HF_HOME=str(HF_DIR), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", **extra)
    return env


def _run(cmd: list[str], on_line=None, env: dict | None = None):
    """Run a step, feeding each output line to on_line; Cancel kills it."""
    tail = deque(maxlen=25)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                            env=env or _env())

    def watch():
        while proc.poll() is None:
            if _cancel.wait(0.5):
                proc.kill()
                return

    threading.Thread(target=watch, daemon=True).start()
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        if on_line:
            on_line(line)
    code = proc.wait()
    if _cancel.is_set():
        raise Cancelled()
    if code:
        raise RuntimeError(f"{Path(cmd[0]).name} {' '.join(cmd[1:3])} failed (exit code {code}):\n" + "\n".join(tail))


def install(accepted_license: bool) -> dict:
    if not accepted_license:
        raise ValueError("Agree to Magi's license (personal, non-commercial use only) first")
    with _lock:
        if _status["state"] in ("installing", "removing"):
            raise ValueError("Magi is already being installed or removed")
        if installed():
            raise ValueError("Magi is already installed")
        _cancel.clear()
        _set(state="installing", stage="Starting", progress=0.0, message="", error=None)
    threading.Thread(target=_install, daemon=True, name="magi-install").start()
    return status()


def cancel_install():
    if _status["state"] == "installing":
        _cancel.set()


def _install():
    device = _device()
    names, index, torch_bytes = TORCH[device]
    total = download_bytes(device)
    py = str(env_python())
    try:
        ADDON_DIR.mkdir(parents=True, exist_ok=True)
        _set(stage="Creating its Python environment")
        _run([_base_python(), "-m", "venv", str(ENV_DIR)])
        _set(stage="Updating the package installer")
        _run([py, "-m", "pip", "install", "-q", "--disable-pip-version-check", "pip>=24.1"])  # for raw progress

        files = {}  # download size by file, from pip's "Progress <done> of <total>" lines

        def torch_progress(line):
            m = re.match(r"Progress (\d+) of (\d+)", line)
            if m:
                files[m.group(2)] = int(m.group(1))
                done = min(sum(files.values()), torch_bytes)
                _set(progress=done / total, message=f"{done / 1e9:.2f} of {torch_bytes / 1e9:.2f} GB")

        _set(stage=f"Downloading PyTorch ({'NVIDIA GPU' if device == 'cuda' else 'CPU'} build, "
                   f"{torch_bytes / 1e9:.2f} GB)", message="")
        _run([py, "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "raw",
              "--index-url", index, *names], on_line=torch_progress)
        _set(stage="Downloading the other packages", progress=torch_bytes / total, message="")
        _run([py, "-m", "pip", "install", "-q", "--disable-pip-version-check", *PACKAGES])

        def model_progress(line):
            try:
                n = json.loads(line).get("progress")
            except ValueError:
                return
            if n is not None:
                _set(progress=min(1.0, (torch_bytes + OTHER_BYTES + n) / total),
                     message=f"{n / 1e9:.2f} of {MODEL_BYTES / 1e9:.2f} GB")

        _set(stage=f"Downloading the Magi v2 model ({MODEL_BYTES / 1e9:.2f} GB)", message="")
        _run([py, str(WORKER), "download"], on_line=model_progress)
        _set(stage="Checking that it runs", progress=1.0, message="")
        result = {}

        def checked(line):
            try:
                result.update(json.loads(line))
            except ValueError:
                pass

        _run([py, str(WORKER), "check", "--device", device], on_line=checked)
        if not result.get("ok"):
            raise RuntimeError("Magi didn't start correctly; see the details below.")
        size = sum(f.stat().st_size for f in ADDON_DIR.rglob("*") if f.is_file())
        STATE_FILE.write_text(json.dumps({"device": device, "installed_at": time.time(), "size_gb": round(size / 1e9, 1),
                                          "check": result}), encoding="utf-8")
        _set(state="idle", stage="", message="Installed", progress=1.0)
    except Cancelled:
        _remove_files()
        _set(state="idle", stage="", message="Download cancelled; nothing was kept", progress=0.0)
    except Exception as e:  # report it in Settings; leave nothing half-installed
        _remove_files()
        _set(state="error", stage="", error=str(e)[-3000:], message="The download or install failed", progress=0.0)


def _remove_files():
    for _ in range(5):  # files of a just-killed process can stay locked for a moment
        shutil.rmtree(ADDON_DIR, ignore_errors=True)
        if not ADDON_DIR.exists():
            return
        time.sleep(1)


def remove() -> dict:
    with _lock:
        if _status["state"] == "installing":
            raise ValueError("Cancel the download first")
        if _sessions:
            raise ValueError("Magi is in use by a running job")
        _set(state="removing", stage="Removing", message="")
    try:
        _remove_files()
        _set(state="idle", stage="", message="Removed" if not ADDON_DIR.exists() else
             f"Some files couldn't be deleted; remove {ADDON_DIR} by hand", progress=0.0)
    finally:
        if _status["state"] == "removing":
            _set(state="idle")
    return status()


# ---- running Magi during a job ----------------------------------------------------

class Session:
    """The Magi model in its own process, loaded once and kept for the whole job.

    check is called while waiting (the job's pause/cancel check); if it raises, the process is stopped.
    """

    def __init__(self, check):
        global _sessions
        info = installed()
        if not info:
            raise RuntimeError("Magi isn't installed (Settings → Speaker detection)")
        self.check = check
        self.lines: "queue.Queue[str | None]" = queue.Queue()
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.log = open(LOG_FILE, "a", encoding="utf-8")
        self.proc = subprocess.Popen([str(env_python()), str(WORKER), "serve", "--device", info["device"]],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True,
                                     encoding="utf-8", errors="replace", creationflags=NO_WINDOW, env=_env())
        _sessions += 1
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self._answer(timeout=600)  # the ready line
        except BaseException:
            self.close()
            raise

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _answer(self, timeout: float) -> dict:
        end = time.time() + timeout
        while True:
            try:
                self.check()
            except BaseException:
                self.proc.kill()
                raise
            try:
                line = self.lines.get(timeout=0.3)
            except queue.Empty:
                if time.time() > end:
                    self.proc.kill()
                    raise RuntimeError("Magi stopped responding")
                continue
            if line is None:
                raise RuntimeError(f"Magi stopped unexpectedly (details in {LOG_FILE})")
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # stray library output
            if "error" in msg:
                raise RuntimeError(f"Magi failed: {msg['error']}")
            return msg

    def chapter(self, pages: list[str], bank: dict) -> list[list[dict]]:
        self.proc.stdin.write(json.dumps({"pages": pages, "bank": bank}) + "\n")
        self.proc.stdin.flush()
        return self._answer(timeout=1800)["pages"]

    def close(self):
        global _sessions
        try:
            if self.proc.poll() is None:
                self.proc.stdin.write(json.dumps({"quit": True}) + "\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=10)
        except Exception:
            pass
        if self.proc.poll() is None:
            self.proc.kill()
        self.log.close()
        _sessions = max(0, _sessions - 1)


# ---- pages for Magi, and its lines back onto our pieces -------------------------------

CHUNK_TARGET, CHUNK_MIN, CHUNK_MAX = 1550, 700, 2600  # Magi expects manga pages, about 1080 x 1550
BLANK = 6.0    # a row whose pixels vary less than this is a gutter or flat background
OVERLAP = 300  # rows shared by two chunks cut through artwork: taller than most bubbles


class Strip:
    """A webtoon chapter's pages stacked into one strip (scaled to the first page's width, as in
    panel detection), read a slice at a time instead of held whole in memory."""

    def __init__(self, project: dict, pages: list[dict]):
        self.project, self.pages = project, pages
        self.width = pages[0]["w"]
        self.tops, y = [], 0.0
        for pg in pages:  # the same running offset panel detection gives the pieces' y0/y1
            self.tops.append(round(y))
            y += pg["h"] * self.width / pg["w"]
        self.height = round(y)

    def _page(self, i):
        img = panels.imread(store.page_path(self.project, self.pages[i]))
        if img is None:
            raise RuntimeError(f"page image missing: {self.pages[i].get('name')}")
        h = self.tops[i + 1] - self.tops[i] if i + 1 < len(self.pages) else self.height - self.tops[i]
        return img if img.shape[:2] == (h, self.width) else cv2.resize(img, (self.width, h), interpolation=cv2.INTER_AREA)

    def rows(self, y0: int, y1: int) -> np.ndarray:
        parts = []
        for i, top in enumerate(self.tops):
            bottom = self.tops[i + 1] if i + 1 < len(self.tops) else self.height
            if bottom > y0 and top < y1:
                parts.append(self._page(i)[max(0, y0 - top):min(bottom, y1) - top])
        return np.vstack(parts)

    def row_spread(self) -> np.ndarray:
        """How busy each row is (std of its pixels); near 0 for empty gutters and flat backgrounds."""
        return np.concatenate([cv2.cvtColor(self._page(i), cv2.COLOR_BGR2GRAY).std(axis=1)
                               for i in range(len(self.pages))])


def strip_chunks(strip: Strip, pieces: list[dict]) -> list[tuple[int, int, int, int]]:
    """Cut the strip into page-sized chunks for Magi: in the empty gap between two pieces where
    possible, otherwise at a blank row. Where a cut has to go through artwork (it might slice a
    speech bubble), the two chunks overlap by OVERLAP rows so the bubble is whole in one of them.

    Returns (core_y0, core_y1, y0, y1) per chunk: the image is rows y0..y1; Magi's lines count
    only when their middle is in the core, so a line seen twice in an overlap is kept once.
    """
    gaps = np.array(sorted((a["y1"] + b["y0"]) // 2 for a, b in zip(pieces, pieces[1:]) if b["y0"] - a["y1"] >= 4))
    spread = strip.row_spread()
    cuts, clean = [0], [True]
    while strip.height - cuts[-1] > CHUNK_MAX:
        a, b, target = cuts[-1] + CHUNK_MIN, cuts[-1] + CHUNK_MAX, cuts[-1] + CHUNK_TARGET
        near = gaps[(gaps > a) & (gaps < b)] if len(gaps) else gaps
        quiet = np.where(spread[a:b] < BLANK)[0] + a
        if len(near):
            y, ok = near[np.argmin(np.abs(near - target))], True
        elif len(quiet):
            y, ok = quiet[np.argmin(np.abs(quiet - target))], True
        else:
            y, ok = a + int(np.argmin(spread[a:b])), False
        cuts.append(int(y))
        clean.append(ok)
    cuts.append(strip.height)
    clean.append(True)
    return [(c0, c1, c0 if clean[i] else max(0, c0 - OVERLAP), c1 if clean[i + 1] else min(strip.height, c1 + OVERLAP))
            for i, (c0, c1) in enumerate(zip(cuts, cuts[1:]))]


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']{2,}", s.lower()))


def place_lines(pieces: list[dict], lines: list[dict]) -> dict[str, list[dict]]:
    """Give each of Magi's lines to the piece it belongs to.

    pieces: the chapter's pieces, in order. lines: {"text", "speaker", "essential", and either "y"
    (strip row, webtoon) or "page" and "center" (x, y on the page)}. Pieces with a known position
    (strip rows, or a box on the page) are matched by position; otherwise by shared words with the
    piece's own text on the same page.
    """
    out: dict[str, list[dict]] = {}
    for ln in lines:
        if not ln["essential"]:
            continue
        if "y" in ln:
            inside = [p for p in pieces if "y0" in p and p["y0"] <= ln["y"] < p["y1"]]
            target = inside[0] if inside else min((p for p in pieces if "y0" in p),
                                                  key=lambda p: min(abs(p["y0"] - ln["y"]), abs(p["y1"] - ln["y"])),
                                                  default=None)
        else:
            same = [p for p in pieces if p["page"] == ln["page"]]
            x, y = ln["center"]
            boxed = [p for p in same if p.get("box") and p["box"][0] <= x < p["box"][0] + p["box"][2]
                     and p["box"][1] <= y < p["box"][1] + p["box"][3]]
            if boxed:
                target = boxed[0]
            else:
                words = _words(ln["text"])
                target = max(same, key=lambda p: len(words & _words(p.get("text", ""))), default=None)
        if target is not None:
            out.setdefault(target["id"], []).append({"who": ln["speaker"], "text": ln["text"]})
    return out


def describe(speakers: list[dict] | None) -> str:
    """Magi's guesses for a piece, as one line for a script writer."""
    return "; ".join(f"{s['who']}: {json.dumps(s['text'])}" for s in speakers or [])
