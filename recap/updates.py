"""Updates from GitHub: whether a newer release of Recap Studio is out, and installing it.

The version is in VERSION next to the app, and the edition (Lite, or Full Suite with Recap Studio's plugins built
in) in EDITION; packaging\\build.ps1 writes both. A copy run from source has neither and isn't checked: git pull
updates it. Once per start (unless switched off in Settings), and whenever Check now is pressed, the app asks
GitHub's API for the repository's latest release: one request, with nothing about you in it. Updating downloads
that release's file for this copy's edition, checks it against the SHA-256 GitHub lists for it, and then:
- an installed copy (unins000.exe next to it) runs the new setup quietly; it updates the same folder, keeping the
  data and models folders, and starts the app again;
- a portable copy unpacks the new zip and leaves a small PowerShell helper (powershell.exe comes with Windows) to
  swap the program files in once the app has closed, putting the old ones back if anything fails, and then start
  it again. The data and models folders are never touched;
- an AppImage (Linux) downloads the new one beside itself, then puts it in its own place (same name, so menu
  entries and shortcuts keep working) and starts it. Its data is in the XDG folders, which it never touches.
RECAP_UPDATE_URL points the check at another address (testing)."""
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

from . import store, system

REPO = "iEatNoodlez/RecapStudio"
PAGE = f"https://github.com/{REPO}/releases/latest"
API = os.environ.get("RECAP_UPDATE_URL") or f"https://api.github.com/repos/{REPO}/releases/latest"
WORK = store.DATA_ROOT / "updates"  # downloads and the portable copy's unpacked update, removed on the next start
NO_WINDOW = system.NO_WINDOW
DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

_state = {"state": "idle", "checked": None, "latest": None, "available": False, "progress": 0.0, "message": "",
          "error": None}
_lock = threading.Lock()
_cleaned = False


def version() -> str | None:
    try:
        return (store.ROOT / "VERSION").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def kind() -> str:
    """"installed" (by the setup), "portable" (the zip), "appimage" (Linux) or "source" (run from a clone, or the
    dev copy)."""
    if not version():
        return "source"
    if system.appimage():
        return "appimage"
    return "installed" if (store.ROOT / "unins000.exe").exists() else "portable"


EDITIONS = {"lite": "Lite", "full": "Full Suite"}


def edition() -> str:
    """"full" for Full Suite, else "lite". A copy without EDITION (0.1.x, or run from source) is Lite."""
    try:
        e = (store.ROOT / "EDITION").read_text(encoding="utf-8").strip().lower()
    except OSError:
        return "lite"
    return e if e in EDITIONS else "lite"


def label() -> str:
    """The app's name as the window, the sidebar and Settings show it: "Recap Studio Full Suite 0.2.0"."""
    v = version()
    return f"Recap Studio {EDITIONS[edition()]}" + (f" {v}" if v else "")


def _key(v: str):
    """A version to compare: 1.2.3 < 1.2.4, and 1.3.0-beta.1 < 1.3.0. None if it isn't one."""
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?", (v or "").strip())
    if not m:
        return None
    pre = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in (m.group(4) or "").split(".") if p)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, pre)


def newer(latest: str, current: str) -> bool:
    a, b = _key(latest), _key(current)
    return bool(a and b and a > b)


def _asset_for(assets: list) -> dict | None:
    """The release file this copy updates from: its edition's setup for an installed copy, or zip for a portable one.
    Lite's names end the way 0.1.x copies look for (Recap-Studio-Lite-0.2.0-setup.exe, -portable.zip), so those
    stay Lite; Full Suite's don't (Recap-Studio-Full-Setup-0.2.0.exe, Recap-Studio-Full-Portable-0.2.0.zip). None
    when the release has no file for this edition (it can still be downloaded from its page). Linux's are
    AppImages: Recap-Studio-Lite-0.3.0-x86_64.AppImage and Recap-Studio-Full-0.3.0-x86_64.AppImage."""
    k = kind()
    installed = k == "installed"
    if k == "appimage":
        name = "Full" if edition() == "full" else "Lite"
        pattern = rf"Recap-Studio-{name}-.+-{re.escape(platform.machine())}\.AppImage"
    elif not system.WINDOWS:
        return None  # (an unpacked copy on Linux: there's nothing to swap it with)
    elif edition() == "full":
        pattern = r"Recap-Studio-Full-Setup-.+\.exe" if installed else r"Recap-Studio-Full-Portable-.+\.zip"
    else:
        pattern = r".+-setup\.exe" if installed else r".+-portable\.zip"
    for a in assets:
        if re.fullmatch(pattern, a.get("name", "")):
            digest = a.get("digest") or ""
            return {"name": a["name"], "url": a["browser_download_url"], "size": int(a.get("size") or 0),
                    "sha256": digest[7:].lower() if digest.startswith("sha256:") else None}
    return None


def _cleanup():
    """What an earlier update left: the downloaded file, or the portable copy's old program files."""
    global _cleaned
    if not _cleaned:
        _cleaned = True
        if WORK.is_dir():
            shutil.rmtree(WORK, ignore_errors=True)


def status() -> dict:
    _cleanup()
    s = store.global_settings()
    latest = _state["latest"]
    return {**_state, "version": version(), "kind": kind(), "edition": edition(), "edition_name": EDITIONS[edition()],
            "label": label(), "page": (latest or {}).get("url") or PAGE,
            "check_updates": bool(s.get("check_updates", True)),
            "skipped": bool(latest and s.get("skip_version") == latest["version"])}


def check(force: bool = False) -> dict:
    """Ask GitHub for the latest release (once per start, unless forced). Never raises: problems go in "error"."""
    if kind() == "source" or _state["state"] in ("downloading", "installing"):
        return status()
    if not force and (_state["checked"] or not store.global_settings().get("check_updates", True)):
        return status()
    with _lock:
        _state.update(state="checking", error=None)
        try:
            req = urllib.request.Request(API, headers={"Accept": "application/vnd.github+json",
                                                       "User-Agent": f"RecapStudio/{version()}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                rel = json.loads(r.read().decode("utf-8"))
            tag = rel.get("tag_name") or ""
            latest = {"version": tag.lstrip("v"), "tag": tag, "name": rel.get("name") or tag,
                      "notes": (rel.get("body") or "").strip()[:8000], "url": rel.get("html_url") or PAGE,
                      "published": rel.get("published_at"), "asset": _asset_for(rel.get("assets") or [])}
            _state.update(state="idle", checked=time.time(), latest=latest,
                          available=newer(latest["version"], version() or ""), message="")
        except Exception as e:  # offline, rate-limited, no release yet: say so, and try again next start
            _state.update(state="idle", checked=time.time(), error=f"Couldn't check GitHub: {e}")
    return status()


def check_in_background():
    if not _state["checked"] and _state["state"] == "idle":
        threading.Thread(target=check, daemon=True, name="update-check").start()


def skip(ver: str):
    store.update_global({"skip_version": ver})


def open_page():
    """The release's page in the default browser (only GitHub's pages for this repository)."""
    url = (_state["latest"] or {}).get("url") or PAGE
    webbrowser.open(url if url.startswith(f"https://github.com/{REPO}/") else PAGE)


def start() -> dict:
    """Download and install the latest release, then close the app (the update starts it again)."""
    latest, k = _state["latest"], kind()
    if k == "source":
        raise ValueError("This copy runs from source: update it with git pull.")
    if not (_state["available"] and latest and latest.get("asset")):
        raise ValueError("There's no newer release to install. Check again first.")
    if not latest["asset"]["sha256"]:
        raise ValueError("GitHub didn't list a checksum for this release's file, so it can't be checked. Download "
                         "it from the release's page instead.")
    if k == "appimage" and not os.access(Path(system.appimage()).parent, os.W_OK):
        raise ValueError(f"Recap Studio can't replace {system.appimage()}: its folder isn't yours to change. "
                         "Download the new AppImage from the release's page instead.")
    with _lock:
        if _state["state"] in ("downloading", "installing"):
            raise ValueError("The update is already under way.")
        _state.update(state="downloading", progress=0.0, message="Starting the download", error=None)
    threading.Thread(target=_run, args=(latest, k), daemon=True, name="update").start()
    return status()


def _run(latest, k):
    asset, file, part = latest["asset"], None, None
    try:
        shutil.rmtree(WORK, ignore_errors=True)
        WORK.mkdir(parents=True)
        file = WORK / asset["name"]
        if k == "appimage":  # (beside it, so it's put in place by a rename, never a copy)
            file = Path(system.appimage()).with_name(f".{asset['name']}.update")
        part = file.with_name(file.name + ".part")
        digest, got = hashlib.sha256(), 0
        req = urllib.request.Request(asset["url"], headers={"User-Agent": f"RecapStudio/{version()}"})
        with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                _state.update(progress=got / max(1, asset["size"]),
                              message=f"Downloading {latest['version']}: {got / 1e6:.0f} of {asset['size'] / 1e6:.0f} MB")
        if digest.hexdigest() != asset["sha256"] or (asset["size"] and got != asset["size"]):
            raise RuntimeError("The download doesn't match the checksum GitHub lists for it, so it wasn't used.")
        part.replace(file)
        _state.update(state="installing", progress=1.0,
                      message=f"Installing {latest['version']}: Recap Studio closes and opens again by itself.")
        if k == "appimage":
            # the running copy keeps its own (open, mounted) file; the new one takes its name and starts once this
            # one has closed
            os.chmod(file, 0o755)
            os.replace(file, system.appimage())
            time.sleep(1.5)  # time for the page to hear it
            restart()
            return
        if k == "installed":
            # the setup updates the folder it installed (same app id), keeps data and models, and starts the app
            # again (/update=1)
            subprocess.Popen([str(file), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
                              "/update=1"], creationflags=DETACHED, close_fds=True)
        else:
            _stage_portable(file)
        threading.Timer(1.5, _quit).start()  # time for the page to hear it
    except Exception as e:
        shutil.rmtree(WORK, ignore_errors=True)
        if k == "appimage":
            for left in (file, part):
                try:
                    if left:
                        left.unlink(missing_ok=True)
                except OSError:
                    pass
        _state.update(state="error", progress=0.0, message="The update failed", error=str(e))


def _stage_portable(zip_file: Path):
    """Unpack the new portable copy, and start the helper that swaps it in once this app has closed."""
    # unpacked straight into WORK (data\updates\Recap Studio), on the app's own drive so the swap only renames,
    # and with as short a path as can be: the app's deepest files are about 140 characters below its folder
    with zipfile.ZipFile(zip_file) as z:
        root = WORK.resolve()
        for member in z.namelist():
            if not (root / member).resolve().is_relative_to(root):
                raise RuntimeError(f"The update holds a file outside its folder: {member}")
        z.extractall(WORK)
    zip_file.unlink()
    app = WORK / "Recap Studio"
    if not (app / "Recap Studio.exe").exists() or not (app / "python" / "pythonw.exe").exists():
        raise RuntimeError("The update's zip isn't a Recap Studio portable copy.")
    script = WORK / "swap.ps1"
    script.write_text(SWAP, encoding="utf-8-sig")  # (Windows PowerShell reads UTF-8 only with its mark)
    # a hidden console of its own (PowerShell doesn't run at all as a DETACHED_PROCESS), outliving this app
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File",
                      str(script), "-AppPid", str(os.getpid()), "-Root", str(store.ROOT), "-New", str(app),
                      "-Log", str(store.DATA_ROOT / "update.log")],
                     creationflags=NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0), close_fds=True)


LAUNCH = {"port": 0, "mode": "window"}  # how this copy was started (__main__), for restart()


def restart() -> None:
    """Start the app again, the way this copy was started (a new copy that waits for this one to close, on the same
    port), then close this one. Plugins installed or switched since this copy started are loaded by the new one.
    An AppImage starts its file again: this copy's own files go when it closes."""
    cmd = system.app_command(windowless=False)
    if LAUNCH["mode"] != "window":
        cmd.append(f"--{LAUNCH['mode']}")
    if LAUNCH["port"]:
        cmd += ["--port", str(LAUNCH["port"])]
    cmd += ["--after", str(os.getpid())]
    subprocess.Popen(cmd, cwd=str(store.ROOT), env=os.environ.copy(), stdin=subprocess.DEVNULL, **system.detached())
    threading.Timer(0.8, _quit).start()  # (after the page has its answer)


def _quit():
    """Close the app for the update: the window if there is one, then the process."""
    try:
        info = store.DATA_ROOT / "app.json"
        if json.loads(info.read_text(encoding="utf-8")).get("pid") == os.getpid():
            info.unlink()
    except (OSError, ValueError):
        pass
    webview = sys.modules.get("webview")
    if webview:
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:  # noqa: BLE001  (closing anyway)
                pass
    time.sleep(1)
    os._exit(0)


# The portable copy's swap, run by Windows PowerShell after the app has closed: each program file or folder of the
# new copy replaces the old one (moved aside first, and put back if anything fails); data and models stay. The old
# ones are removed on the app's next start (WORK). Then the app is started again, updated or not.
SWAP = r"""param([int]$AppPid, [string]$Root, [string]$New, [string]$Log)
function Say($text) { Add-Content -Path $Log -Value ("{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $text) }
$end = (Get-Date).AddSeconds(60)
while ((Get-Process -Id $AppPid -ErrorAction SilentlyContinue) -and (Get-Date) -lt $end) { Start-Sleep -Milliseconds 300 }
Start-Sleep -Seconds 1
$old = Join-Path (Split-Path $New -Parent) "old"
$moved = @(); $placed = @()
try {
    $ErrorActionPreference = "Stop"
    New-Item -ItemType Directory -Force $old | Out-Null
    foreach ($item in Get-ChildItem -Force $New | Where-Object { $_.Name -notin @("data", "models") }) {
        $dest = Join-Path $Root $item.Name
        if (Test-Path -LiteralPath $dest) { Move-Item -LiteralPath $dest (Join-Path $old $item.Name); $moved += $item.Name }
        Move-Item -LiteralPath $item.FullName $dest
        $placed += $item.Name
    }
    Say "updated $Root"
} catch {
    Say "update failed, putting the old files back: $_"
    foreach ($name in $placed) { Remove-Item -Recurse -Force -LiteralPath (Join-Path $Root $name) -ErrorAction SilentlyContinue }
    foreach ($name in $moved) { Move-Item -LiteralPath (Join-Path $old $name) (Join-Path $Root $name) -ErrorAction SilentlyContinue }
}
Start-Process -FilePath (Join-Path $Root "Recap Studio.exe") -WorkingDirectory $Root
"""
