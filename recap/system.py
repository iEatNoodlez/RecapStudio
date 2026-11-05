"""What differs between Windows and Linux, in one place: showing a folder, starting a process that outlives the app
or shows no console, waiting for another copy to close, opening a terminal, and the command that starts the app.
Standard library only, so mcp_stdio.py can use it without the app. (Not named platform.py: a script run from this
folder would shadow Python's own module.)"""
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

WINDOWS = os.name == "nt"
APP = Path(__file__).resolve().parent.parent
# (each 0 off Windows)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # the app runs without a console: no flashing windows
_DETACHED = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def appimage() -> str | None:
    """The AppImage file this copy runs from (Linux), if it does: its runtime says so in APPIMAGE."""
    path = os.environ.get("APPIMAGE")
    return path if path and os.environ.get("APPDIR") and APP.is_relative_to(Path(os.environ["APPDIR"])) else None


def outside_env() -> dict:
    """The environment for other programs (a file manager, a terminal): the member's own, without what the app's
    Python set for itself."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "RECAP_APPRUN_"))}


def detached() -> dict:
    """Popen's arguments for a process that outlives the app (and on Windows shows no console)."""
    if WINDOWS:
        return {"creationflags": _DETACHED, "close_fds": True}
    return {"start_new_session": True, "close_fds": True}


def app_command(windowless: bool = True) -> list[str]:
    """The command that starts Recap Studio again: the AppImage, or this Python with -m recap (pythonw.exe on
    Windows when windowless, so no console shows)."""
    if appimage():
        return [appimage()]
    python = Path(sys.executable)
    if WINDOWS and windowless and python.with_name("pythonw.exe").exists():
        python = python.with_name("pythonw.exe")
    return [str(python), "-m", "recap"]


def open_folder(path) -> None:
    """Show a folder in the file manager: File Explorer on Windows, the desktop's own elsewhere (xdg-open)."""
    path = str(path)
    if WINDOWS:
        os.startfile(path)
        return
    if sys.platform == "darwin":
        cmd = ["open", path]
    elif shutil.which("xdg-open"):
        cmd = ["xdg-open", path]
    elif shutil.which("gio"):
        cmd = ["gio", "open", path]
    else:
        raise OSError(f"No file manager was found to show {path} (xdg-open isn't installed).")
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     env=outside_env(), start_new_session=True)


def keep_private(folder: Path) -> None:
    """Linux: the data folder (settings, API keys, sign-ins, plugins' tokens) open to its owner only. A new folder is
    often readable by every account on the computer there; Windows keeps it in the user's own profile."""
    if WINDOWS:
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if folder.stat().st_mode & 0o077:
            folder.chmod(0o700)
    except OSError:
        pass


def file_manager() -> str:
    """What the app calls the file manager, in buttons and messages."""
    return "File Explorer" if WINDOWS else "Finder" if sys.platform == "darwin" else "your file manager"


# ---- the AppImage in the desktop's applications menu (Linux) ----

def _data_home() -> Path:
    value = os.environ.get("XDG_DATA_HOME")
    return Path(value) if value and os.path.isabs(value) else Path.home() / ".local" / "share"


MENU_ENTRY = "recap-studio.desktop"  # (the window's app id and class are recap-studio too: __main__._qt_app)


def _menu_files() -> tuple[Path, Path]:
    data = _data_home()
    return data / "applications" / MENU_ENTRY, data / "icons" / "hicolor" / "256x256" / "apps" / "recap-studio.png"


def _exec_quoted(path: str) -> str:
    """A path for a desktop entry's Exec line: quoted, with the characters it gives meaning to escaped (and then a
    desktop entry's own escaping of backslashes)."""
    inner = "".join("\\" + c if c in '"`$\\' else c for c in path)
    return f'"{inner}"'.replace("\\", "\\\\")


def menu_status() -> dict:
    """Whether the AppImage is in the applications menu: offered only for an AppImage, which has nowhere else to be
    found (its file can be anywhere)."""
    entry, _ = _menu_files()
    image = appimage()
    try:
        text = entry.read_text(encoding="utf-8")
    except OSError:
        text = ""
    return {"offered": bool(image), "added": bool(image) and f"Exec={_exec_quoted(image)}" in text,
            "elsewhere": bool(text) and bool(image) and f"Exec={_exec_quoted(image)}" not in text,
            "entry": str(entry)}


def add_to_menu() -> dict:
    """Put the AppImage in the desktop's applications menu (~/.local/share/applications), with its icon. It updates
    itself in place, so the entry stays right; moving the file means adding it again."""
    image = appimage()
    if not image:
        raise ValueError("Only the AppImage is added to the menu: run from source, start it with run.sh.")
    entry, icon = _menu_files()
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(APP / "recap-studio.png", icon)
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("\n".join([
        "[Desktop Entry]", "Type=Application", "Name=Recap Studio", "GenericName=Recap video maker",
        "Comment=Turn comic and webtoon chapters into narrated recap videos", f"Exec={_exec_quoted(image)}",
        "Icon=recap-studio", "Terminal=false", "Categories=AudioVideo;Video;", "StartupWMClass=recap-studio",
        "StartupNotify=true", ""]), encoding="utf-8")
    tool = shutil.which("update-desktop-database")
    if tool:  # (most desktops see the new entry by themselves; this makes sure)
        subprocess.run([tool, str(entry.parent)], capture_output=True, timeout=20, env=outside_env())
    return menu_status()


def remove_from_menu() -> dict:
    entry, icon = _menu_files()
    entry.unlink(missing_ok=True)
    icon.unlink(missing_ok=True)
    return menu_status()


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False  # (someone else's process: there)
    except OSError:
        return True
    try:  # a zombie (closed, not yet reaped by its parent) is as good as gone
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat[stat.rindex(")") + 2] == "Z"
    except (OSError, ValueError, IndexError):
        return False


def wait_for(pid: int, seconds: float = 30.0) -> None:
    """Restart: wait for the copy that's closing to be gone, so its port and files are free."""
    if WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if handle:
                kernel32.WaitForSingleObject(handle, int(seconds * 1000))
                kernel32.CloseHandle(handle)
            return
        except (AttributeError, OSError):
            time.sleep(3)
            return
    end = time.time() + seconds
    while time.time() < end and not _gone(pid):
        time.sleep(0.1)


def web_view_can_start() -> bool:
    """Linux: whether Chromium's sandbox, inside Qt's web view, can start, tried as Chromium does: a new user
    namespace, and new PID and network namespaces in it, in a child process. It can't as root, nor where the
    distribution keeps user namespaces from programs it doesn't know (Ubuntu 24.04 and later, through AppArmor;
    a profile for the app lets it: docs/linux.md). Chromium would end the whole app then, so it opens in the
    browser instead. True on Windows and macOS, and when the member chose to run the web view without its sandbox
    (QTWEBENGINE_DISABLE_SANDBOX, Qt's own setting) or it isn't Qt's (GTK's WebKit has a sandbox of its own)."""
    if not sys.platform.startswith("linux") or os.environ.get("QTWEBENGINE_DISABLE_SANDBOX"):
        return True
    gui = os.environ.get("PYWEBVIEW_GUI", "").lower()
    if gui and gui != "qt":
        return True
    if not gui:
        try:
            import gi  # noqa: F401  (from source with the distro's WebKitGTK: pywebview uses that)
            return True
        except ImportError:
            pass
    if os.geteuid() == 0:
        return False
    if not hasattr(os, "unshare"):
        return True
    pid = os.fork()
    if pid == 0:  # (the child tries, and only says how it went)
        try:
            os.unshare(os.CLONE_NEWUSER)
            os.unshare(os.CLONE_NEWPID | os.CLONE_NEWNET)
            os._exit(0)
        except BaseException:  # noqa: BLE001
            os._exit(1)
    try:
        return os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) == 0
    except OSError:
        return True


# Terminals, in the order tried, with what goes before the command: the desktop's own choice first
# (xdg-terminal-exec, Debian's x-terminal-emulator), then the common ones
TERMINALS = [
    ("xdg-terminal-exec", []), ("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("ptyxis", ["--"]),
    ("kgx", ["--"]), ("konsole", ["-e"]), ("xfce4-terminal", ["-x"]), ("mate-terminal", ["--"]),
    ("tilix", ["-e"]), ("lxterminal", ["-e"]), ("qterminal", ["-e"]), ("terminator", ["-x"]), ("kitty", []),
    ("alacritty", ["-e"]), ("foot", []), ("wezterm", ["start", "--"]), ("xterm", ["-e"]),
]


def in_terminal(cmd: list[str], env: dict | None = None) -> bool:
    """Run a command where the member can see it and type: a console window on Windows, a terminal elsewhere. False
    when no terminal was found (the app then shows the command to run)."""
    if WINDOWS:
        subprocess.Popen(cmd, env=env, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        return True
    env = {**outside_env(), **({} if env is None else {k: v for k, v in env.items() if not k.startswith("PYTHON")})}
    # the terminal closes when the command ends: wait for Enter first, so its last words can be read
    script = shlex.join(cmd) + '; echo; read -r -p "Press Enter to close this window." _'
    for name, before in TERMINALS:
        found = shutil.which(name)
        if found:
            try:
                subprocess.Popen([found, *before, "bash", "-c", script], env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                return True
            except OSError:
                continue
    return False
