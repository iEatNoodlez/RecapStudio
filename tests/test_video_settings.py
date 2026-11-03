"""Video settings per project, on two made-up projects in a scratch folder whose settings.json still holds app-wide
video settings, as every copy up to 0.1.1 has:
1. at start, each project gets those app-wide choices as its own, and settings.json lets them go; projects keep
   their place in the list ("updated" untouched);
2. changing one project's video settings leaves the other's alone; app-wide saves don't take video settings;
3. the New project dialog's starting choices are the last project's (not its YouTube-safe); a project made with
   chosen settings has them (types kept); one made without gets the defaults;
4. the env reports the NVIDIA card apart from whether ONNX Runtime can use it.
Usage: python test_video_settings.py <scratch folder>"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from helpers import APP, data_folder, free_port, make_project, scratch, tidy  # noqa: E402

BASE = scratch("video-settings")
DATA = data_folder(path=BASE / "video_data")
make_project(DATA, "Sample One")
make_project(DATA, "Sample Two")
s = json.loads((DATA / "settings.json").read_text(encoding="utf-8"))
s.update(orientation="portrait", resolution=720, fps=24, video_style="stills", transition_seconds=0.8)
(DATA / "settings.json").write_text(json.dumps(s), encoding="utf-8")
before = {}
for d in (DATA / "projects").iterdir():
    p = json.loads((d / "project.json").read_text(encoding="utf-8"))
    before[p["id"]] = p.get("updated")
os.environ["RECAP_DATA"] = str(DATA)
sys.path.insert(0, str(APP))
import uvicorn  # noqa: E402
from recap import server, store  # noqa: E402

PORT = free_port()
srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=PORT, log_level="warning"))
threading.Thread(target=srv.run, daemon=True).start()
while not srv.started:
    time.sleep(0.05)
FAILS = []


def check(ok, what):
    print(("  ok   " if ok else "  FAIL ") + what, flush=True)
    if not ok:
        FAILS.append(what)


def http(method, path, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


print("1. the app-wide video settings move into the projects")
saved = json.loads((DATA / "settings.json").read_text(encoding="utf-8"))
check(not any(k in saved for k in store.VIDEO_KEYS), "settings.json lets them go")
for pid in before:
    own = json.loads((store.project_dir(pid) / "project.json").read_text(encoding="utf-8"))
    check(own["settings"].get("orientation") == "portrait" and own["settings"].get("resolution") == 720
          and own["settings"].get("transition_seconds") == 0.8 and own.get("updated") == before[pid],
          f"{own['name']}: its own portrait 720p, crossfade 0.8, 'updated' unchanged")

print("2. per project")
a, b = list(before)
http("PUT", f"/api/projects/{a}/settings", {"resolution": 1080, "orientation": "landscape"})
check(store.load(a)["settings"]["resolution"] == 1080 and store.load(b)["settings"]["resolution"] == 720,
      "changing one project's leaves the other's alone")
http("PUT", "/api/settings", {"resolution": 360, "video_encoder": "cpu"})
check(store.load(b)["settings"]["resolution"] == 720 and store.global_settings()["video_encoder"] == "cpu"
      and "resolution" not in json.loads((DATA / "settings.json").read_text(encoding="utf-8")),
      "an app-wide save keeps the encoder but takes no video settings")

print("3. new projects")
http("PUT", f"/api/projects/{a}/settings", {"youtube_safe": True})
time.sleep(0.05)
start = http("GET", "/api/projects/new-settings")
check(start["resolution"] == 1080 and start["orientation"] == "landscape" and start["youtube_safe"] is False,
      f"the dialog starts from the last project's choices, YouTube-safe off: {start['resolution']}p {start['orientation']}")
made = http("POST", "/api/projects", {"name": "Wizard Test", "settings": {**start, "layout": "pages",
                                                                         "reading_direction": "rtl", "resolution": "480",
                                                                         "fps": "15", "youtube_safe": True,
                                                                         "theme": "ignored"}})
st = made["settings"]
check(st["layout"] == "pages" and st["reading_direction"] == "rtl" and st["resolution"] == 480 and st["fps"] == 15
      and st["youtube_safe"] is True and "theme" not in json.loads(
          (store.project_dir(made["id"]) / "project.json").read_text(encoding="utf-8"))["settings"],
      "a project made with chosen settings has them, as numbers; unknown keys are dropped")
plain = http("POST", "/api/projects", {"name": "Plain"})
check(plain["settings"]["resolution"] == store.DEFAULT_SETTINGS["resolution"]
      and plain["settings"]["orientation"] == store.DEFAULT_SETTINGS["orientation"],
      "one made without gets the defaults (the old app-wide ones are gone)")

print("4. the GPU")
env = http("GET", "/api/status")
check("card" in env["gpu"], f"env: available {env['gpu']['available']}, card {env['gpu']['card']!r}")

srv.should_exit = True
time.sleep(0.5)
print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
tidy(bool(FAILS))
