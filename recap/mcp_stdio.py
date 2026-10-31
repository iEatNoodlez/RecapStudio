"""Recap Studio's MCP server for Claude Code, over stdio (registered in .mcp.json, or with `claude mcp add`). It
passes each tool call to the open app's /mcp (mcp.py), which it finds through data\\app.json, and answers the
handshake and the tool list itself, so Claude Code connects even while the app is closed; open_app starts it.

Run it with the app's Python: .venv\\Scripts\\python.exe recap\\mcp_stdio.py from source, or
python\\python.exe recap\\mcp_stdio.py in an installed copy.
Tool calls are answered in their own threads, since Claude Code's agents can wait in next_request side by side."""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from recap import mcp  # noqa: E402  (imports nothing else from the app)

DATA = Path(os.environ.get("RECAP_DATA") or APP / "data")
_out = threading.Lock()


def _url() -> str:
    info = json.loads((DATA / "app.json").read_text(encoding="utf-8"))
    return f"http://127.0.0.1:{int(info['port'])}/mcp"


def _post(msg: dict, timeout: float) -> dict:
    req = urllib.request.Request(_url(), data=json.dumps(msg).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _is_open() -> bool:
    try:
        return "result" in _post({"jsonrpc": "2.0", "id": 0, "method": "ping"}, 5)
    except (OSError, ValueError, KeyError):
        return False


def _open_app(mid):
    if _is_open():
        return mcp.text_result(mid, "Recap Studio is already open.")
    # the windowless Python next to the one running this: the app's .venv, or the Python the installer bundles
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        pythonw = Path(sys.executable)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(pythonw), "-m", "recap"], cwd=APP, creationflags=flags, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    end = time.time() + 60
    while time.time() < end:
        time.sleep(1)
        if _is_open():
            return mcp.text_result(mid, "Recap Studio is open.")
    return mcp.text_result(mid, "Recap Studio was started but isn't answering yet; try again in a moment.", error=True)


def _reply(msg):
    if msg.get("method") == "tools/call" and msg.get("id") is not None:
        if (msg.get("params") or {}).get("name") == "open_app":
            return _open_app(msg["id"])
        try:
            return _post(msg, mcp.WAIT_LIMIT + 60)
        except (OSError, ValueError, KeyError):
            return mcp.text_result(msg["id"], "Recap Studio isn't open. Call open_app to start it, then try again.",
                                   error=True)
    return mcp.handle(msg)  # the handshake, the tool list, pings and notifications


def _answer(msg):
    reply = _reply(msg)
    if reply is not None:
        with _out:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    calls = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            with _out:
                sys.stdout.write(json.dumps(mcp.error(None, -32700, "Parse error")) + "\n")
                sys.stdout.flush()
            continue
        if isinstance(msg, dict) and msg.get("method") == "tools/call":
            calls = [t for t in calls if t.is_alive()]
            calls.append(threading.Thread(target=_answer, args=(msg,), daemon=True))
            calls[-1].start()
        else:
            _answer(msg)
    for t in calls:  # the input closed: finish the calls still going
        t.join(timeout=mcp.WAIT_LIMIT + 60)


if __name__ == "__main__":
    main()
