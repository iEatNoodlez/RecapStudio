"""Start Recap Studio: local server plus a desktop window.

    python -m recap              # desktop window
    python -m recap --browser    # open in your default browser instead
    python -m recap --no-window  # just the server (for testing; Claude Code's MCP still finds it)

The address goes in data\\app.json while it runs, for Claude Code's MCP server (mcp_stdio.py) and the agents the
Claude Code writer starts.
"""
import argparse
import atexit
import base64
import ctypes
import html
import json
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

ICON = Path(__file__).resolve().parent / "web" / "icon.ico"
# What the window shows while the app loads (a few seconds; longer the first time after the PC starts), in the
# page's own colours, then if it couldn't start
STARTING = """<!doctype html><html><head><meta charset="utf-8"><style>
:root { --bg: #f6f6f4; --text: #1d1d1b; --muted: #6d6c67; --line: #dcdad4; --accent: #3d63dd; --danger: #c4372b; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #18181a; --text: #ececea; --muted: #9b9a95; --line: #34343a; --accent: #6d8cf0; --danger: #ec6a5f; }
}
html, body { height: 100%; margin: 0; }
body { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px;
  background: var(--bg); color: var(--text); font: 15px "Segoe UI", system-ui, sans-serif; cursor: progress; }
img { width: 64px; height: 64px; }
.bar { width: 180px; height: 3px; border-radius: 2px; background: var(--line); overflow: hidden; }
.bar::after { content: ""; display: block; width: 40%; height: 100%; border-radius: 2px; background: var(--accent);
  animation: go 1.2s ease-in-out infinite; }
@keyframes go { from { transform: translateX(-100%); } to { transform: translateX(250%); } }
.muted { color: var(--muted); font-size: 13px; max-width: 520px; text-align: center; }
.error { color: var(--danger); }
</style></head><body>{body}</body></html>"""


def _page(*parts: str) -> str:
    try:
        icon = '<img alt="" src="data:image/x-icon;base64,' + base64.b64encode(ICON.read_bytes()).decode() + '">'
    except OSError:
        icon = ""
    return STARTING.replace("{body}", icon + "".join(parts))


def _dark() -> bool:
    """Windows set to dark apps: the window starts dark, with no white flash before its page."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except (ImportError, OSError):
        return False
# Windows' id for the app: the window's taskbar button goes under it, with the app's icon, instead of under
# pythonw.exe with Python's; the installer's shortcuts carry the same id (packaging\installer.iss), so pinning
# the button pins Recap Studio
APP_ID = "RecapStudio.App"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _log_file() -> Path:
    from .paths import DATA
    return DATA / "app.log"


def _log_to_file_without_console():
    # pythonw.exe has no stdout/stderr, which breaks uvicorn's logging setup
    if sys.stdout is None or sys.stderr is None:
        _log_file().parent.mkdir(parents=True, exist_ok=True)
        log = open(_log_file(), "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def _wait_for(pid: int, seconds: float = 30.0) -> None:
    """Restart: wait for the copy that's closing to be gone, so its port and files are free."""
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if handle:
            kernel32.WaitForSingleObject(handle, int(seconds * 1000))
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError):  # (not Windows)
        time.sleep(3)


def _note_address(port: int):
    """data\\app.json: where the running app answers (removed when it quits)."""
    from .store import DATA_ROOT
    info = DATA_ROOT / "app.json"
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    info.write_text(json.dumps({"port": port, "pid": os.getpid(), "started": time.time()}), encoding="utf-8")

    def remove():
        try:
            if json.loads(info.read_text(encoding="utf-8")).get("pid") == os.getpid():
                info.unlink()
        except (OSError, ValueError):
            pass
    atexit.register(remove)


def main():
    _log_to_file_without_console()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("recap").setLevel(logging.INFO)  # e.g. Claude's token use per chapter
    ap = argparse.ArgumentParser(prog="recap")
    ap.add_argument("--browser", action="store_true", help="use the default browser instead of a window")
    ap.add_argument("--no-window", action="store_true", help="only the server, with no window or browser")
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--after", type=int, default=0, help=argparse.SUPPRESS)  # (Restart: the copy closing first)
    args = ap.parse_args()
    if args.after:
        _wait_for(args.after)
    from .store import move_old_layout
    move_old_layout()  # an installed copy that kept its data beside itself: before anything reads its settings
    from . import gpu
    gpu.activate()  # the downloaded GPU build of ONNX Runtime, if any: before anything imports onnxruntime

    port = args.port or _free_port()
    config = uvicorn.Config("recap.server:app", host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    url = f"http://127.0.0.1:{port}/"
    _note_address(port)
    from . import updates
    updates.LAUNCH.update(port=port, mode="no-window" if args.no_window else "browser" if args.browser else "window")

    if args.no_window:
        print(f"Recap Studio running at {url}  (Ctrl+C to stop)")
        server.run()
        return

    if args.browser:
        if not args.after:  # (restarted: the page that asked reloads itself)
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        print(f"Recap Studio running at {url}  (Ctrl+C to stop)")
        server.run()
        return

    import webview

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass  # (not Windows)
    # The window opens at once, saying the app is starting, and goes to the app once its server answers
    window = webview.create_window(updates.label(), html=_page("<div>Starting Recap Studio…</div>",
                                                               '<div class="bar"></div>'),
                                   width=1440, height=920, min_size=(1000, 650),
                                   background_color="#18181A" if _dark() else "#F6F6F4")
    failed = []

    def serve():
        try:
            server.run()
        except BaseException as e:  # (SystemExit too: uvicorn quits when the app won't load)
            failed.append(e)
            raise

    def open_app():  # (webview.start runs it on a thread of its own, once the window is up)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        window.events.loaded.wait(15)  # (the starting page first: drawn later, it would replace the app's)
        while not server.started:
            if not thread.is_alive():
                why = "" if not failed or isinstance(failed[-1], SystemExit) else f"{failed[-1]}"
                window.load_html(_page('<div class="error">Recap Studio couldn\'t start.</div>',
                                       f'<div class="muted">{html.escape(why)}</div>' if why else "",
                                       '<div class="muted">What went wrong is in ' + html.escape(str(_log_file()))
                                       + ". Close this window and open Recap Studio again; if it happens again, "
                                       "send that file with your report.</div>"))
                return
            time.sleep(0.05)
        window.load_url(url)

    def pick_folder():
        """Native folder dialog, called from the page as window.pywebview.api.pick_folder()."""
        result = window.create_file_dialog(webview.FileDialog.FOLDER)
        return result[0] if result else None

    def pick_project_file():
        """Native file dialog for a project file (.recap), called as window.pywebview.api.pick_project_file()."""
        result = window.create_file_dialog(webview.FileDialog.OPEN,
                                           file_types=("Recap Studio project (*.recap)", "All files (*.*)"))
        return result[0] if result else None

    window.expose(pick_folder, pick_project_file)
    webview.start(open_app, icon=str(ICON))  # (without an icon, the window takes pythonw.exe's)
    server.should_exit = True


if __name__ == "__main__":
    main()
