"""A project as one file (.recap), to keep as a backup or move to another computer, and opening one.

The file is a zip: project.json, a manifest (recap-project.json) and the project's own files: its pages
(including pages imported from a folder, which the app otherwise reads where they are, so the file
doesn't depend on that folder), panels, voice lines, character pictures, the character scan, recordings
and the scenes' subjects. What the app makes again from those is left out: the rendered video and its
pieces, the videos for editors and the pictures without text. Opening a file makes a new project
(the original, if it's still here, is left alone).
"""
import json
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from . import store

FORMAT, VERSION = "recap-studio-project", 1
MANIFEST = "recap-project.json"
FOLDERS = ("pages", "panels", "audio", "characters", "scan", "voiceover")  # kept as they are
FILES = ("focus.json",)
SUFFIX = ".recap"


def _raw(pid: str) -> dict:
    """project.json as stored (without the app-wide settings that load() adds)."""
    return json.loads((store.project_dir(pid) / "project.json").read_text(encoding="utf-8"))


def save(pid: str, dest: Path, progress=None, cancelled=None) -> dict:
    """Write the project to dest (a .recap file). Returns {"file", "size", "files"}."""
    d = store.project_dir(pid)
    p = _raw(pid)
    entries = []  # (name in the file, file on disk)
    taken = set()
    for page in p["pages"]:  # pages read from a folder are copied in, and from then on are the project's own
        if page.get("src"):
            src = Path(page["src"])
            name = f"{page['chapter']}_{len(taken):05d}{src.suffix.lower() or '.jpg'}"
            page.pop("src")
            page["file"] = name
        else:
            src, name = d / "pages" / page["file"], page["file"]
        taken.add(name)
        entries.append((f"pages/{name}", src))
    for folder in FOLDERS[1:]:
        if (d / folder).is_dir():
            entries += [(f"{folder}/{f.name}", f) for f in sorted((d / folder).iterdir()) if f.is_file()]
    entries += [(name, d / name) for name in FILES if (d / name).is_file()]
    missing = [str(src) for _, src in entries if not src.is_file()]
    if missing:
        raise RuntimeError(f"{len(missing)} of the project's files are missing, e.g. {missing[0]}")
    p["output"] = None  # the rendered video isn't in the file, nor the output folder's other files
    p.pop("editors", None)
    p.pop("package", None)
    total = sum(src.stat().st_size for _, src in entries) or 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    done = 0
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
            z.writestr(MANIFEST, json.dumps({"format": FORMAT, "version": VERSION, "name": p["name"],
                                             "saved": time.time(), "files": len(entries)}), zipfile.ZIP_DEFLATED)
            z.writestr("project.json", json.dumps(p, ensure_ascii=False), zipfile.ZIP_DEFLATED)
            for name, src in entries:
                if cancelled and cancelled():
                    raise InterruptedError("cancelled")
                z.write(src, name)  # pictures and sound are already packed: stored as they are
                done += src.stat().st_size
                if progress:
                    progress(done / total)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
    return {"file": dest, "size": dest.stat().st_size, "files": len(entries)}


def _check(z: zipfile.ZipFile) -> dict:
    """The file's manifest, after making sure it's a project file whose contents stay in their folders."""
    try:
        info = json.loads(z.read(MANIFEST))
    except (KeyError, ValueError):
        raise ValueError("This isn't a Recap Studio project file") from None
    if info.get("format") != FORMAT:
        raise ValueError("This isn't a Recap Studio project file")
    if int(info.get("version", 0)) > VERSION:
        raise ValueError("This project file was saved by a newer Recap Studio; update the app to open it")
    for name in z.namelist():
        parts = Path(name).parts
        if (Path(name).is_absolute() or ".." in parts or ":" in name or not parts
                or not (name in (MANIFEST, "project.json", *FILES) or (len(parts) == 2 and parts[0] in FOLDERS))):
            raise ValueError(f"The project file holds something it shouldn't: {name}")
    return info


def open_file(path: str | Path) -> dict:
    """Make a new project from a .recap file. Returns the new project (as store.load gives it)."""
    path = Path(str(path).strip().strip('"'))
    if not path.is_file():
        raise FileNotFoundError(f"No such file: {path}")
    with zipfile.ZipFile(path) as z:
        _check(z)
        p = json.loads(z.read("project.json"))
        pid = uuid.uuid4().hex[:12]
        final = store.project_dir(pid)
        tmp = final.with_name(pid + ".part")
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            z.extractall(tmp)
            (tmp / MANIFEST).unlink(missing_ok=True)
            for sub in ("pages", "panels", "audio", "output"):
                (tmp / sub).mkdir(exist_ok=True)
            names = {x["name"] for x in store.list_all()}
            name, n = p.get("name") or path.stem, 2
            while name in names:  # beside a project of the same name (e.g. the one it was saved from)
                name, n = f"{p.get('name') or path.stem} ({n})", n + 1
            p.update(id=pid, name=name, updated=time.time(), output=None)
            (tmp / "project.json").write_text(json.dumps(p, indent=1, ensure_ascii=False), encoding="utf-8")
            tmp.rename(final)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return store.load(pid)
