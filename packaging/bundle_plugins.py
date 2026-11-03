"""Full Suite's plugins: the feature plugins on Recap Studio's own plugin list (RecapStudio-Plugins), at the commit
packaging\\plugins.json pins, unpacked into the app's plugins folder (recap\\plugins.py, BUNDLED_DIR). build.ps1
-Edition full runs it with the build's own Python:

    python -I packaging\\bundle_plugins.py "dist\\Recap Studio"
    python -I packaging\\bundle_plugins.py "dist\\Recap Studio" --from ..\\Plugins   # a checkout of the list instead

Each plugin's zip is checked against the SHA-256 the list's index.json gives it, and unpacked with the same checks
the app makes when it installs one. A plugin written for a newer plugin version than the app gives stops the build.
Source plugins (AniList, Kitsu, MangaUpdates) aren't bundled: the app reads those from the data folder only
(recap\\sources.py), so members install them from Settings → Plugins."""
import argparse
import hashlib
import io
import json
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

PIN = Path(__file__).resolve().parent / "plugins.json"
ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")  # (recap\plugins.py's ID)
MAX_ZIP = 20 << 20                            # (and its MAX_ZIP)


def fetch(base, path: str) -> bytes:
    """A file of the list: from GitHub at the pinned commit, or from a checkout."""
    if isinstance(base, Path):
        return (base / path).read_bytes()
    req = urllib.request.Request(f"{base}/{path}", headers={"User-Agent": "RecapStudio-build"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def unpack(data: bytes, folder: Path, name: str) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for i in z.infolist():
            if i.is_dir():
                continue
            parts = i.filename.replace("\\", "/").split("/")
            if i.filename.startswith(("/", "\\")) or ":" in i.filename or any(p in ("", ".", "..") for p in parts):
                sys.exit(f"{name} holds a file it can't ({i.filename}).")
            target = folder.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(i))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("app", help='the built app\'s folder ("dist\\Recap Studio")')
    ap.add_argument("--from", dest="checkout", help="a checkout of the plugin list, instead of GitHub")
    args = ap.parse_args()
    app = Path(args.app)
    api = int(re.search(r"^API = (\d+)", (app / "recap" / "plugins.py").read_text(encoding="utf-8"), re.M).group(1))
    if args.checkout:
        base = Path(args.checkout)
        print(f"Plugins from {base}")
    else:
        pin = json.loads(PIN.read_text(encoding="utf-8"))
        if not re.fullmatch(r"[0-9a-f]{40}", pin.get("commit", "")):
            sys.exit("packaging\\plugins.json needs the plugin list's whole commit id (40 characters).")
        base = f"https://raw.githubusercontent.com/{pin['repository']}/{pin['commit']}"
        print(f"Plugins from {pin['repository']} at {pin['commit'][:7]}")
    index = json.loads(fetch(base, "index.json"))
    dest = app / "plugins"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir()
    for p in index["plugins"]:
        if p.get("kind") != "feature":
            continue
        pid, name = p["id"], f"{p['name']} {p['version']}"
        if not ID.fullmatch(pid):
            sys.exit(f"{pid!r} isn't a plugin id.")
        if p.get("api", 1) > api:
            sys.exit(f"{name} is written for plugin version {p['api']}; this app gives {api}.")
        data = fetch(base, p["file"])
        if len(data) > MAX_ZIP or hashlib.sha256(data).hexdigest() != p["sha256"].lower():
            sys.exit(f"{p['file']} doesn't match its checksum in index.json.")
        unpack(data, dest / pid, p["file"])
        info = json.loads((dest / pid / "plugin.json").read_text(encoding="utf-8-sig"))
        if info.get("id") != pid or str(info.get("version")) != p["version"]:
            sys.exit(f"{p['file']} isn't the plugin index.json says ({info.get('id')} {info.get('version')}).")
        print(f"  {name}")


if __name__ == "__main__":
    main()
