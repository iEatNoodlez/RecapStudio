"""Named project folders and the projects folder, on two made-up projects in a scratch folder, with the app's server
in this process:
1. at start, folders named by project id take their project's name;
2. new projects get folders named after them (unsafe characters out, (2) for a second of the same name); renaming a
   project renames its folder, and its files still load; Open project file gets a named folder too;
3. Settings → Projects folder: a folder is checked (full path, not inside a project); moving on the same drive
   renames each across; to "another drive" (simulated) copies all, then removes the old; a failure partway puts
   everything back; switching without moving shows the other folder's projects, and switching back shows them again;
4. deleting a project removes its folder; project_status gives the folder.
On two made-up projects (helpers.make_project) in a scratch folder."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from helpers import APP, data_folder, free_port, make_project, scratch, tidy  # noqa: E402

BASE = scratch("folders")
DATA = data_folder(path=BASE / "data")
SAMPLE = make_project(DATA, "Sample Two", pid="a1b2c3d4e5f6", chapters=2)
make_project(DATA, "Sample One", pid="0f1e2d3c4b5a")
os.environ["RECAP_DATA"] = str(DATA)
sys.path.insert(0, str(APP))
import uvicorn  # noqa: E402
from recap import package, server, store  # noqa: E402

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
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, (json.loads(r.read() or b"null") if "json" in r.headers.get("Content-Type", "") else r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def names(root):
    return sorted(p.name for p in Path(root).iterdir() if (p / "project.json").exists())


def wait_move():
    end = time.time() + 120
    while http("GET", "/api/storage")[1]["move"]["state"] == "moving" and time.time() < end:
        time.sleep(0.3)
    return http("GET", "/api/storage")[1]


print("1. folders named at start")
projects = DATA / "projects"
check(names(projects) == ["Sample One", "Sample Two"], f"renamed: {names(projects)}")
_, listed = http("GET", "/api/projects")
check(len(listed) == 2 and all(Path(p["folder"]).parent == projects for p in listed), "both listed, with their folders")

print("2. new, renamed and opened projects")
_, a = http("POST", "/api/projects", {"name": "My: Test/Project?"})
_, b = http("POST", "/api/projects", {"name": "My: Test/Project?"})
check(store.project_dir(a["id"]).name == "My Test Project" and store.project_dir(b["id"]).name == "My Test Project (2)",
      f"named folders: {store.project_dir(a['id']).name}, {store.project_dir(b['id']).name}")
sample = SAMPLE
panel = store.load(sample)["panels"][0]["file"]
http("PATCH", f"/api/projects/{sample}", {"name": "Renamed Sample"})
check(store.project_dir(sample).name == "Renamed Sample", f"renaming renames the folder: {store.project_dir(sample).name}")
code, img = http("GET", f"/files/{sample}/panels/{panel}")
check(code == 200 and len(img) > 1000, "its files still load")
http("PATCH", f"/api/projects/{a['id']}", {"name": "Renamed Sample"})
check(store.project_dir(a["id"]).name == "Renamed Sample (2)", "a name another folder has gets (2)")
(BASE / "pkg").mkdir()
saved = package.save(sample, BASE / "pkg" / "sample.recap")
opened = package.open_file(saved["file"])
check(store.project_dir(opened["id"]).name == "Renamed Sample (3)" and opened["name"] == "Renamed Sample (2)"
      or store.project_dir(opened["id"]).name.startswith("Renamed Sample ("),
      f"an opened project file gets a named folder: {store.project_dir(opened['id']).name} ({opened['name']})")
check(json.loads((store.project_dir(opened["id"]) / "project.json").read_text(encoding="utf-8"))
      and (store.project_dir(opened["id"]) / "project.json").read_text(encoding="utf-8").startswith('{\n "id": "'),
      "its project.json starts with its id")
count = len(http("GET", "/api/projects")[1])

print("3. the projects folder")
st = http("GET", "/api/storage")[1]
check(Path(st["projects"]) == projects and st["synced"] is None, f"storage: {st['projects']}")
code, err = http("POST", "/api/storage/check", {"folder": "relative\\folder"})
check(code == 400 and "full path" in err["detail"], "a relative path is refused")
code, err = http("POST", "/api/storage/check", {"folder": str(store.project_dir(sample) / "inside")})
check(code == 400 and "inside the project folder" in err["detail"], "a folder inside a project is refused")
if os.environ.get("OneDrive"):
    check(store.paths.synced(Path(os.environ["OneDrive"]) / "Recaps") == "OneDrive", "a OneDrive folder is flagged")
st = http("POST", "/api/storage/projects", {"folder": str(BASE / "elsewhere"), "move": True})[1]
st = wait_move()
check(st["move"]["state"] == "idle" and Path(st["projects"]) == BASE / "elsewhere"
      and len(names(BASE / "elsewhere")) == count and names(projects) == [],
      f"moved on the same drive: {st['move']['message']}")
check(len(http("GET", "/api/projects")[1]) == count and http("GET", f"/files/{sample}/panels/{panel}")[0] == 200,
      "every project still opens, files too")
check(store.global_settings()["projects_dir"] == str(BASE / "elsewhere"), "the setting is saved")

target = BASE / "elsewhere2"
target.mkdir()
store._same_drive = lambda a, b: False  # as if elsewhere2 were on another drive
real_copytree, copied = shutil.copytree, []


def failing_copytree(src, dst, *a, **k):
    if Path(dst).parent == target and len(copied) == 2:  # the third project: half copied, then the disk is full
        Path(dst).mkdir()
        (Path(dst) / "pages").mkdir()
        raise OSError("disk full (simulated)")
    if Path(dst).parent == target:
        copied.append(dst)
    return real_copytree(src, dst, *a, **k)


shutil.copytree = failing_copytree
http("POST", "/api/storage/projects", {"folder": str(target), "move": True})
st = wait_move()
check(st["move"]["state"] == "error" and "Nothing was moved" in st["move"]["error"] and names(target) == []
      and len(names(BASE / "elsewhere")) == count and Path(st["projects"]) == BASE / "elsewhere",
      f"a failure partway puts everything back: {st['move']['error']}")
shutil.copytree = real_copytree
http("POST", "/api/storage/projects", {"folder": str(target), "move": True})
st = wait_move()
check(st["move"]["state"] == "idle" and len(names(target)) == count and names(BASE / "elsewhere") == [],
      f"to another drive: copied, then the old ones removed: {st['move']['message']}")
check(len(http("GET", "/api/projects")[1]) == count, "every project still opens")

http("POST", "/api/storage/projects", {"folder": str(BASE / "empty3"), "move": False})
check(http("GET", "/api/projects")[1] == [] and len(names(target)) == count, "switching without moving: the list is empty")
http("POST", "/api/storage/projects", {"folder": str(target), "move": False})
check(len(http("GET", "/api/projects")[1]) == count, "switching back shows them again")

print("4. delete, project_status")
folder = store.project_dir(b["id"])
http("DELETE", f"/api/projects/{b['id']}")
check(not folder.exists() and len(http("GET", "/api/projects")[1]) == count - 1, "deleting removes its folder")
from recap import mcp  # noqa: E402
st = mcp.tool_project_status(sample)
check(Path(st["folder"]) == target / "Renamed Sample", f"project_status gives the folder: {st['folder']}")

srv.should_exit = True
time.sleep(0.5)
print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED: {FAILS}")
tidy(bool(FAILS))
