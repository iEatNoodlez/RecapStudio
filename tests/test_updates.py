"""Updates by edition (updates.py): each copy (Lite or Full Suite, installed or portable, and 0.1.x copies, which have
no EDITION) picks its own edition's file from a release with all four, through a made-up GitHub on this PC; a
release without its edition's file offers none; the label the window, sidebar and Settings show."""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import APP, check, data_folder, done, free_port, scratch

port = free_port()
os.environ["RECAP_DATA"] = str(data_folder())
os.environ["RECAP_UPDATE_URL"] = f"http://127.0.0.1:{port}/latest"

RELEASE = {"tag_name": "v0.2.1", "html_url": "https://github.com/iEatNoodlez/RecapStudio/releases/tag/v0.2.1",
           "assets": [{"name": n, "browser_download_url": f"https://example.invalid/{n}", "size": 1,
                       "digest": "sha256:" + "0" * 64}
                      for n in ("Recap-Studio-Full-Portable-0.2.1.zip", "Recap-Studio-Full-Setup-0.2.1.exe",
                                "Recap-Studio-Lite-0.2.1-portable.zip", "Recap-Studio-Lite-0.2.1-setup.exe")]}
release = dict(RELEASE)


class GitHub(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(release).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


server = ThreadingHTTPServer(("127.0.0.1", port), GitHub)
threading.Thread(target=server.serve_forever, daemon=True).start()

sys.path.insert(0, str(APP))
from recap import store, updates  # noqa: E402  (after RECAP_DATA)


def copy(edition: str | None, version: str | None, installed: bool):
    """Make store.ROOT a made-up copy of the app: VERSION, EDITION and the setup's uninstaller as asked."""
    root = scratch("root")
    if version:
        (root / "VERSION").write_text(version + "\n", encoding="utf-8")
    if edition:
        (root / "EDITION").write_text(edition + "\n", encoding="ascii")
    if installed:
        (root / "unins000.exe").write_bytes(b"")
    store.ROOT = root


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

server.shutdown()
done()
