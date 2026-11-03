"""A project whose name (so its folder) and source folder have a non-ASCII character ("꞉", the colon look-alike
download tools put in folder names), on a test data folder, with the app's server in this process:
1. panel detection writes every panel picture, and each one decodes to its box's size;
2. credit detection reads pages from the source folder;
3. a character picture and an uploaded page are saved into the project folder;
4. panels.imwrite saves .jpg and .png to such a folder and raises when it can't save.
On a scratch data folder of its own."""
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from helpers import APP as _APP, scratch, tidy  # noqa: E402

BASE, APP = scratch("unicode"), str(_APP)
DATA = BASE / "data"
SRC = BASE / "Test Series꞉ A World with a 1꞉99 Ratio"
shutil.rmtree(DATA, ignore_errors=True)
shutil.rmtree(SRC, ignore_errors=True)
DATA.mkdir(parents=True)
(DATA / "settings.json").write_text(json.dumps({"setup_done": True, "check_updates": False,
                                                "projects_dir": str(DATA / "Projects")}), encoding="utf-8")
os.environ["RECAP_DATA"] = str(DATA)
sys.path.insert(0, APP)
import uvicorn  # noqa: E402
from recap import credits, panels, pipeline, server, store  # noqa: E402

PORT = 8781
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
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def page(seed):
    """A white 1100x1563 page with three framed panels of noise, like a manga page."""
    rng = np.random.default_rng(seed)
    img = np.full((1563, 1100, 3), 255, np.uint8)
    for y0, y1 in ((60, 520), (560, 1020), (1060, 1500)):
        img[y0:y1, 50:1050] = rng.integers(0, 256, (y1 - y0, 1000, 3), dtype=np.uint8)
        cv2.rectangle(img, (50, y0), (1049, y1 - 1), (0, 0, 0), 6)
    return img


for ch in ("Chapter 1", "Chapter 2"):
    (SRC / ch).mkdir(parents=True)
    for k in range(1, 4):
        ok, data = cv2.imencode(".jpg", page(k + 10 * len(ch)))
        (SRC / ch / f"{k:02d}.jpg").write_bytes(data.tobytes())

print("1. panel detection")
status, p = http("POST", "/api/projects", {"name": "Test Series꞉ A World with a 1꞉99 Ratio",
                                           "settings": {"layout": "pages"}})
check(status == 200, f"project made ({status})")
pid = p["id"]
d = store.project_dir(pid)
check("꞉" in d.name, f"its folder has the character: {d.name}")
status, r = http("POST", f"/api/projects/{pid}/import-folder", {"path": str(SRC)})
check(status == 200 and r and len(store.load(pid)["pages"]) == 6, f"6 pages imported ({status})")
status, job = http("POST", f"/api/projects/{pid}/run", {"steps": ["panels"]})
check(status == 200, f"panel detection started ({status})")
while pipeline.latest_for(pid)["status"] in pipeline.ACTIVE:
    time.sleep(0.2)
job = pipeline.latest_for(pid)
check(job["status"] == "done", f"panel detection finished: {job['status']} {job.get('error') or ''}")
p = store.load(pid)
check(len(p["panels"]) == 18, f"18 panels found ({len(p['panels'])})")
missing = [x["file"] for x in p["panels"] if not (d / "panels" / x["file"]).is_file()]
check(not missing, f"every panel picture is on disk (missing {len(missing)})")
sizes = [(panels.imread(d / "panels" / x["file"]).shape[:2], (x["box"][3], x["box"][2]))
         for x in p["panels"] if x["file"] not in missing]
check(sizes and all(a == b for a, b in sizes), "each picture decodes to its box's size")

print("2. credit detection")
check(credits._fingerprint(SRC / "Chapter 1" / "01.jpg") is not None, "a page in the source folder is read")

print("3. character picture, uploaded page")
store.update(pid, lambda q: q["characters"].append({"id": "c1test0000", "name": "Ann", "pictures": []}))
q = server._add_picture(pid, "c1test0000", page(99)[60:520, 50:1050], None)
pic = next(c for c in q["characters"] if c["id"] == "c1test0000")["pictures"]
check(len(pic) == 1 and (d / "characters" / pic[0]).is_file(), "the character picture is saved")
(d / "pages").mkdir(exist_ok=True)
ok, data = cv2.imencode(".png", page(7))
wh = server._save_image(data.tobytes(), d / "pages" / "up.jpg")
check(wh == (1100, 1563) and panels.imread(d / "pages" / "up.jpg") is not None, "an uploaded page is saved")

print("4. panels.imwrite")
for ext in (".jpg", ".png"):
    f = d / f"probe꞉{ext}"
    panels.imwrite(f, page(3), [cv2.IMWRITE_JPEG_QUALITY, 92] if ext == ".jpg" else [])
    check(f.is_file() and panels.imread(f).shape == (1563, 1100, 3), f"{ext} saved and read back")
try:
    panels.imwrite(d / "no such folder" / "x.jpg", page(3))
    check(False, "a save into a missing folder raises")
except OSError:
    check(True, "a save into a missing folder raises")

srv.should_exit = True
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
tidy(bool(FAILS))
sys.exit(1 if FAILS else 0)
