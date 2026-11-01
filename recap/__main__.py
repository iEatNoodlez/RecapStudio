"""Start Recap Studio: local server plus a desktop window.

    python -m recap              # desktop window
    python -m recap --browser    # open in your default browser instead
    python -m recap --no-window  # just the server (for testing; Claude Code's MCP still finds it)

The address goes in data\\app.json while it runs, for Claude Code's MCP server (mcp_stdio.py) and the agents the
Claude Code writer starts.
"""
import argparse
import atexit
import ctypes
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
# Windows' id for the app: the window's taskbar button goes under it, with the app's icon, instead of under
# pythonw.exe with Python's; the installer's shortcuts carry the same id (packaging\installer.iss), so pinning
# the button pins Recap Studio
APP_ID = "RecapStudio.App"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _log_to_file_without_console():
    # pythonw.exe has no stdout/stderr, which breaks uvicorn's logging setup
    if sys.stdout is None or sys.stderr is None:
        from .paths import DATA
        DATA.mkdir(parents=True, exist_ok=True)
        log = open(DATA / "app.log", "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


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
    args = ap.parse_args()
    from .store import move_old_layout
    move_old_layout()  # an installed copy that kept its data beside itself: before anything reads its settings
    from . import gpu
    gpu.activate()  # the downloaded GPU build of ONNX Runtime, if any: before anything imports onnxruntime

    port = args.port or _free_port()
    config = uvicorn.Config("recap.server:app", host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    url = f"http://127.0.0.1:{port}/"
    _note_address(port)

    if args.no_window:
        print(f"Recap Studio running at {url}  (Ctrl+C to stop)")
        server.run()
        return

    if args.browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        print(f"Recap Studio running at {url}  (Ctrl+C to stop)")
        server.run()
        return

    import webview

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass  # (not Windows)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    window = webview.create_window("Recap Studio", url, width=1440, height=920, min_size=(1000, 650))

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
    webview.start(icon=str(ICON))  # (without one, the window takes pythonw.exe's)
    server.should_exit = True


if __name__ == "__main__":
    main()
