"""Updates by edition (updates.py): each copy (Lite or Full Suite, installed or portable, and 0.1.x copies, which have
no EDITION) picks its own edition's file from a release with all six, through a made-up GitHub on this PC; a
release without its edition's file offers none; the label the window, sidebar and Settings show. On Linux, an
AppImage picks its edition's AppImage, and an unpacked copy none; an AppImage's update replaces its file (checked
against its SHA-256 first) and starts it again, and one that doesn't match leaves it alone. Windows' and Linux's
choices are both checked, whichever this runs on."""
import hashlib
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import APP, check, data_folder, done, free_port, scratch

port = free_port()
os.environ["RECAP_DATA"] = str(data_folder())
os.environ["RECAP_UPDATE_URL"] = f"http://127.0.0.1:{port}/latest"
NEW_APPIMAGE = b"#!/bin/sh\necho the new Recap Studio\n" * 50

RELEASE = {"tag_name": "v0.2.1", "html_url": "https://github.com/iEatNoodlez/RecapStudio/releases/tag/v0.2.1",
           "assets": [{"name": n, "browser_download_url": f"https://example.invalid/{n}", "size": 1,
                       "digest": "sha256:" + "0" * 64}
                      for n in ("Recap-Studio-Full-Portable-0.2.1.zip", "Recap-Studio-Full-Setup-0.2.1.exe",
                                "Recap-Studio-Lite-0.2.1-portable.zip", "Recap-Studio-Lite-0.2.1-setup.exe",
                                "Recap-Studio-Full-0.2.1-x86_64.AppImage", "Recap-Studio-Lite-0.2.1-x86_64.AppImage")]}
release = dict(RELEASE)


class GitHub(BaseHTTPRequestHandler):
    def do_GET(self):
        body = NEW_APPIMAGE if self.path == "/file" else json.dumps(release).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream" if self.path == "/file" else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


server = ThreadingHTTPServer(("127.0.0.1", port), GitHub)
threading.Thread(target=server.serve_forever, daemon=True).start()

sys.path.insert(0, str(APP))
from recap import store, system, updates  # noqa: E402  (after RECAP_DATA)

updates.platform.machine = lambda: "x86_64"  # (the AppImages are named for it)
running_as = {"appimage": None}
system.appimage = lambda: running_as["appimage"]


def copy(edition: str | None, version: str | None, installed: bool, on: str = "windows"):
    """Make store.ROOT a made-up copy of the app: VERSION, EDITION and the setup's uninstaller as asked, on Windows,
    on Linux unpacked ("linux") or as an AppImage ("appimage", which is its file)."""
    root = scratch("root")
    if version:
        (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    if edition:
        (root / "EDITION").write_text(edition + "\n", encoding="ascii")
    if installed:
        (root / "unins000.exe").write_bytes(b"")
    store.ROOT = root
    system.WINDOWS = on == "windows"
    running_as["appimage"] = None
    if on == "appimage":
        image = scratch("apps") / f"Recap-Studio-{'Full' if edition == 'full' else 'Lite'}-{version}-x86_64.AppImage"
        image.write_bytes(b"#!/bin/sh\necho the old Recap Studio\n")
        running_as["appimage"] = str(image)
    return root


def picked() -> str | None:
    s = updates.check(force=True)
    return ((s.get("latest") or {}).get("asset") or {}).get("name")


copy(None, None, False)
check("run from source: Lite, not checked", updates.edition() == "lite" and updates.kind() == "source"
      and updates.label() == "Recap Studio Lite" and picked() is None, updates.status())

for edition, installed, want in (("full", True, "Recap-Studio-Full-Setup-0.2.1.exe"),
                                 ("full", False, "Recap-Studio-Full-Portable-0.2.1.zip"),
                                 ("lite", True, "Recap-Studio-Lite-0.2.1-setup.exe"),
                                 ("lite", False, "Recap-Studio-Lite-0.2.1-portable.zip"),
                                 (None, True, "Recap-Studio-Lite-0.2.1-setup.exe"),
                                 (None, False, "Recap-Studio-Lite-0.2.1-portable.zip")):
    copy(edition, "0.2.0" if edition else "0.1.8", installed)
    got = picked()
    check(f"{edition or 'a 0.1.x copy'}, {'installed' if installed else 'portable'}: {want}", got == want, got)

# ---- Linux ----
for edition, want in (("full", "Recap-Studio-Full-0.2.1-x86_64.AppImage"),
                      ("lite", "Recap-Studio-Lite-0.2.1-x86_64.AppImage")):
    copy(edition, "0.2.0", False, on="appimage")
    got = picked()
    check(f"{edition}, an AppImage: {want}", updates.kind() == "appimage" and got == want, (updates.kind(), got))
copy("full", "0.2.0", False, on="linux")
check("an unpacked copy on Linux: nothing to install (its page is offered)",
      updates.kind() == "portable" and picked() is None and updates.status()["available"], updates.status())
release = {**RELEASE, "assets": [a for a in RELEASE["assets"] if not a["name"].endswith(".AppImage")]}
copy("lite", "0.2.0", False, on="appimage")
check("a release without AppImages: nothing for one to install", picked() is None)

copy("full", "0.2.0", True)
s = updates.status()
check("Full Suite's label and status", s["label"] == "Recap Studio Full Suite 0.2.0" and s["edition"] == "full"
      and s["edition_name"] == "Full Suite" and s["available"], s)
copy("lite", "0.2.0", False)
check("Lite's label", updates.label() == "Recap Studio Lite 0.2.0")
copy("something else", "0.2.0", False)
check("an EDITION it doesn't know is Lite", updates.edition() == "lite")

release = {**RELEASE, "assets": [a for a in RELEASE["assets"] if "-Lite-" in a["name"]]}
copy("full", "0.2.0", True)
s = updates.check(force=True)
check("a release with Lite's files only: nothing for Full Suite to install (its page is offered)",
      s["available"] and s["latest"]["asset"] is None, s["latest"])
release = {**RELEASE, "tag_name": "v0.1.9", "assets": [
    {"name": "Recap-Studio-0.1.9-setup.exe", "browser_download_url": "https://example.invalid/x", "size": 1}]}
copy("lite", "0.1.8", True)
check("Lite still takes a release named the old way", picked() == "Recap-Studio-0.1.9-setup.exe")

# ---- an AppImage updating itself ----
restarted = []
updates.restart = lambda: restarted.append(True)


def update_to(digest: str):
    global release
    name = "Recap-Studio-Lite-0.2.1-x86_64.AppImage"
    release = {**RELEASE, "assets": [{"name": name, "browser_download_url": f"http://127.0.0.1:{port}/file",
                                      "size": len(NEW_APPIMAGE), "digest": "sha256:" + digest}]}
    copy("lite", "0.2.0", False, on="appimage")
    updates._state.update(state="idle")  # (the last update stays "installing": its app would have closed)
    updates.check(force=True)
    updates.start()
    end = time.time() + 20
    while time.time() < end and updates._state["state"] in ("downloading", "installing") and not restarted:
        time.sleep(0.1)
    return running_as["appimage"]


image = update_to(hashlib.sha256(NEW_APPIMAGE).hexdigest())
check("an AppImage's update: the new one in its place, under its own name", open(image, "rb").read() == NEW_APPIMAGE
      and image.endswith("Recap-Studio-Lite-0.2.0-x86_64.AppImage"), open(image, "rb").read()[:40])
check("... runnable", os.name == "nt" or os.stat(image).st_mode & 0o111 == 0o111, oct(os.stat(image).st_mode))
check("... started again", restarted == [True], restarted)
check("... nothing left beside it", sorted(os.listdir(os.path.dirname(image))) == [os.path.basename(image)],
      os.listdir(os.path.dirname(image)))
restarted.clear()
image = update_to("1" * 64)
check("a download that doesn't match its checksum: the AppImage left alone, nothing left beside it",
      open(image, "rb").read() == b"#!/bin/sh\necho the old Recap Studio\n" and not restarted
      and updates._state["state"] == "error" and "checksum" in (updates._state["error"] or "")
      and os.listdir(os.path.dirname(image)) == [os.path.basename(image)], (updates._state, os.listdir(os.path.dirname(image))))

server.shutdown()
done()
