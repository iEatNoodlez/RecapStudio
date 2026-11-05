"""Where Recap Studio keeps its files. Standard library only, so mcp_stdio.py can use it without the app.

An installed copy (unins000.exe next to the app; the app's own folder may be read-only, as in Program Files)
uses Windows' places for each kind of file:
  %APPDATA%\\Recap Studio        DATA: settings, API keys, logs, the running app's address (app.json)
  %LOCALAPPDATA%\\Recap Studio   LOCAL: the voice model and the add-ons, gigabytes kept out of roaming profiles
  Documents\\Recap Studio        projects, one folder each, named after the project (Settings → Projects folder
                                moves them anywhere)
On Linux, an installed copy is the AppImage (INSTALLED inside it; its own folder is read-only), with the XDG places:
  ~/.local/share/recap-studio   DATA, the voice model and the add-ons ($XDG_DATA_HOME)
  ~/Documents/Recap Studio      projects (the XDG Documents folder, as xdg-user-dir finds it)
A portable copy, or one run from source, keeps everything next to itself: data, models, and data\\projects.
RECAP_DATA puts the data, add-ons and projects in that folder instead (testing)."""
import ctypes
import os
import uuid
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
NAME = "Recap Studio"
INSTALLED = ((APP / "unins000.exe").exists() or (APP / "INSTALLED").exists()) and not os.environ.get("RECAP_DATA")
WINDOWS = os.name == "nt"


def _known_folder(guid: str) -> Path | None:
    """A Windows known folder (Documents follows a OneDrive or other redirect, unlike ~\\Documents)."""
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("data", ctypes.c_ubyte * 16)]
        g = GUID.from_buffer_copy(uuid.UUID(guid).bytes_le)
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
            return None
        try:
            return Path(out.value)
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except (AttributeError, OSError, ValueError):
        return None


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value and os.path.isabs(value) else Path.home() / default


def _xdg_documents() -> Path | None:
    """The XDG Documents folder, from ~/.config/user-dirs.dirs as xdg-user-dir reads it (it follows the desktop's
    language: ~/Dokumente, ~/Documents...). None when it isn't set, or is the home folder itself (meaning none)."""
    try:
        lines = (_xdg("XDG_CONFIG_HOME", ".config") / "user-dirs.dirs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        name, _, value = line.strip().partition("=")
        if name == "XDG_DOCUMENTS_DIR":
            value = value.strip().strip('"').replace("$HOME", str(Path.home()))
            path = Path(value)
            return path if path.is_absolute() and path != Path.home() else None
    return None


def documents() -> Path:
    if not WINDOWS:
        return _xdg_documents() or Path.home() / "Documents"
    return _known_folder("FDD39AD0-238F-46AF-ADB4-6C85480369C7") or Path.home() / "Documents"


if os.environ.get("RECAP_DATA"):
    DATA = Path(os.environ["RECAP_DATA"])
    ADDONS, MODELS, DEFAULT_PROJECTS = DATA / "addons", APP / "models", DATA / "projects"
elif INSTALLED and not WINDOWS:
    DATA = _xdg("XDG_DATA_HOME", ".local/share") / "recap-studio"
    ADDONS, MODELS, DEFAULT_PROJECTS = DATA / "addons", DATA / "models", documents() / NAME
elif INSTALLED:
    DATA = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / NAME
    LOCAL = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / NAME
    ADDONS, MODELS, DEFAULT_PROJECTS = LOCAL / "addons", LOCAL / "models", documents() / NAME
else:
    DATA = APP / "data"
    ADDONS, MODELS, DEFAULT_PROJECTS = DATA / "addons", APP / "models", DATA / "projects"


def synced(folder: Path) -> str | None:
    """The cloud service that syncs this folder (OneDrive, Dropbox, Google Drive, iCloud), if one does: projects
    there are uploaded as they grow, gigabytes per recap."""
    roots = [(os.environ.get(k), "OneDrive") for k in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")]
    try:
        f = folder.resolve()
    except OSError:
        f = folder
    for root, name in roots:
        if root and f.is_relative_to(Path(root)):
            return name
    parts = [p.lower() for p in f.parts]
    for marker, name in (("onedrive", "OneDrive"), ("dropbox", "Dropbox"), ("google drive", "Google Drive"),
                         ("icloud drive", "iCloud"), ("iclouddrive", "iCloud")):
        if any(p == marker or p.startswith(marker + " -") for p in parts):
            return name
    return None
