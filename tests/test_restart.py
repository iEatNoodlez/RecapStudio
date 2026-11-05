"""Restart (Settings → Plugins → Restart now): the app, run as its own process, closes and opens again on the same
address, and a plugin switched on since it started is running in the new copy. While a job runs it asks first (409),
and restarts when told to anyway. On Linux, its data folder (settings, keys) is open to its owner only."""
import json
import os
import shutil
import signal
import subprocess
import sys
import time

import requests

from helpers import APP, check, data_folder, done, free_port, make_project, until

data = data_folder()
port = free_port()
base = f"http://127.0.0.1:{port}"
pid = make_project(data, "Long one", panels=40, settings={"resolution": 360, "fps": 10, "video_style": "stills",
                                                          "transition_seconds": 0.0, "layout": "pages", "shorts": 0})
plugin = data / "plugins" / "hello"
plugin.mkdir(parents=True)
(plugin / "plugin.json").write_text('{"id": "hello", "name": "Hello", "version": "1", "api": 1}', encoding="utf-8")
(plugin / "__init__.py").write_text('def setup(plugin):\n    plugin.router.get("/hi")(lambda: {"hi": "there"})\n',
                                    encoding="utf-8")
(data / "plugins" / "features.json").write_text(json.dumps({"off": ["hello"]}), encoding="utf-8")  # (off at first)


def app_pid():
    try:
        return json.loads((data / "app.json").read_text(encoding="utf-8"))["pid"]
    except (OSError, ValueError, KeyError):
        return None


def answering():
    try:
        return requests.get(base + "/api/plugins", timeout=2).ok
    except requests.RequestException:
        return False


env = {**os.environ, "RECAP_DATA": str(data)}
first = subprocess.Popen([sys.executable, "-m", "recap", "--no-window", "--port", str(port)], cwd=str(APP), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    check("the app starts", until(answering, 60))
    check("its data folder is its owner's only (Linux)", os.name == "nt" or os.stat(data).st_mode & 0o777 == 0o700,
          oct(os.stat(data).st_mode))
    old = app_pid()
    check("Hello is off", not any(p["running"] for p in requests.get(base + "/api/plugins").json()["plugins"]))
    requests.put(base + "/api/plugins/hello", json={"enabled": True})
    check("switched on: a restart is asked for", requests.get(base + "/api/plugins").json()["restart"] is True)

    requests.post(f"{base}/api/projects/{pid}/run", json={"steps": ["video"]})
    until(lambda: (requests.get(f"{base}/api/projects/{pid}").json().get("job") or {}).get("status") == "running", 20)
    r = requests.post(base + "/api/restart", json={})
    check("while a job runs, it asks first", r.status_code == 409 and "1 job is running" in r.json()["detail"], r.text)
    r = requests.post(base + "/api/restart", json={"force": True})
    check("told to anyway, it restarts", r.status_code == 200 and r.json() == {"restarting": True}, r.text)
    check("the old copy closes", until(lambda: first.poll() is not None, 30), first.poll())
    check("a new one answers on the same address", until(lambda: answering() and app_pid() not in (None, old), 60),
          (old, app_pid()))
    st = requests.get(base + "/api/plugins").json()
    check("with Hello running, and nothing left to restart for", st["restart"] is False
          and any(p["id"] == "hello" and p["running"] for p in st["plugins"]), st)
    check("its route answers", requests.get(base + "/api/plugins/hello/hi").json() == {"hi": "there"})
    r = requests.post(base + "/api/restart", json={})
    check("restarts again with nothing running", r.status_code == 200)
    newer = app_pid()
    check("and comes back again", until(lambda: answering() and app_pid() not in (None, newer), 60))
finally:
    first.kill() if first.poll() is None else None
    if app_pid():
        try:
            os.kill(app_pid(), signal.SIGTERM)
        except OSError:
            pass
    time.sleep(1)
    shutil.rmtree(data, ignore_errors=True)
done()
