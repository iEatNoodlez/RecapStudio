"""Import a folder tree as chapters.

Every folder that directly contains images becomes one chapter, named after
that folder (or after its path below the chosen folder, when two folders share
a name). Every .zip/.cbz file becomes a chapter too, and so does every file of a
type a plugin reads (FILE_TYPES: an .epub, say), or one chapter for each part it
finds in it (a volume's chapters). Chapters and their pages are sorted naturally,
so "Chapter 2" comes before "Chapter 10".

Images in folders are read in place; archive pages are extracted into the project.
"""
import io
import logging
import os
import re
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

from . import store

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
ARCHIVE_EXT = {".zip", ".cbz"}
FILE_TYPES: dict[str, dict] = {}  # ".epub" -> {"name", "chapters", "pages"}: a plugin's (plugins.Plugin.file_type)
log = logging.getLogger(__name__)


def _path_key(rel: str):
    return [store.natural_key(part) for part in Path(rel).parts]


def _archive_images(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as z:
            names = [i.filename for i in z.infolist()
                     if not i.is_dir() and Path(i.filename).suffix.lower() in IMAGE_EXT]
    except (zipfile.BadZipFile, OSError):
        return []
    return sorted(names, key=store.natural_key)


def _zip_pages(path: Path, members: list[str]):
    with zipfile.ZipFile(path) as z:
        for member in members:
            yield member, z.read(member)


def _file_parts(path: Path, skipped: list | None) -> list[dict]:
    """A plugin's file type: the parts its chapters() finds, [{"name", "pages"}], or [] with why in skipped."""
    ftype = FILE_TYPES[path.suffix.lower()]
    try:
        parts = [x for x in ftype["chapters"](path) or [] if x.get("pages")]
        reason = "no pages found in it"
    except ValueError as e:  # (what the plugin says is wrong with it)
        parts, reason = [], str(e)
    except Exception as e:
        log.exception("%s: %s couldn't be read", ftype["name"], path)
        parts, reason = [], f"it couldn't be read ({type(e).__name__})"
    if not parts and skipped is not None:
        skipped.append({"file": path.name, "reason": reason})
    return parts


def _part_name(name, i: int) -> str:
    return " ".join(str(name or "").replace("/", "-").replace("\\", "-").split())[:120] or f"Part {i}"


def _file_chapters(path: Path, rel: str, skipped: list | None) -> list[dict]:
    """A plugin's file type as chapters: one named after the file, or one a part (named after the part, and kept in
    the file's order)."""
    parts = _file_parts(path, skipped)
    ext = path.suffix.lower()
    if len(parts) == 1:
        return [{"key": rel, "kind": "file", "type": ext, "path": str(path), "source": str(path), "files": parts[0]["pages"]}]
    return [{"key": f"{rel}#{i}", "kind": "file", "type": ext, "path": str(path), "source": f"{path}#{i}",
             "files": x["pages"], "parts": [*Path(rel).parts, _part_name(x.get("name"), i)], "order": (rel, i)}
            for i, x in enumerate(parts, 1)]


def _order(c: dict):
    if "order" in c:  # (a file's parts: in its own order, right where the file is)
        rel, i = c["order"]
        return _path_key(rel) + [store.natural_key(f"\0{i:06d}")]
    return _path_key(c["key"])


def scan(root: str, skipped: list | None = None) -> list[dict]:
    """List the chapters found under root, in reading order (with the files a plugin's type couldn't read, and why,
    added to skipped)."""
    base = Path(root)
    if not base.is_dir():
        raise FileNotFoundError(f"Folder not found: {root}")
    found = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        here = Path(dirpath)
        rel_dir = here.relative_to(base).as_posix()
        images = sorted((f for f in filenames if Path(f).suffix.lower() in IMAGE_EXT), key=store.natural_key)
        if images:
            found.append({
                "key": rel_dir if rel_dir != "." else base.name,
                "kind": "folder",
                "path": str(here),
                "files": images,
            })
        for f in filenames:
            ext = Path(f).suffix.lower()
            rel = (here / f).relative_to(base).with_suffix("").as_posix()
            if ext in ARCHIVE_EXT:
                members = _archive_images(here / f)
                if members:
                    found.append({"key": rel, "kind": "archive", "path": str(here / f), "files": members})
            elif ext in FILE_TYPES:
                found += _file_chapters(here / f, rel, skipped)
    found.sort(key=_order)
    for c, name in zip(found, _names([c.get("parts") or list(Path(c["key"]).parts) for c in found])):
        c["name"] = name
        c["pages"] = len(c["files"])
    return found


GENERIC = {"pages", "page", "images", "image", "img", "imgs", "scans", "raw", "raws"}


def _names(paths: list[list[str]]) -> list[str]:
    """Chapter names from folder paths: the folder's own name, skipping generic or
    shared trailing folders (".../Chapter 3/pages" -> "Chapter 3"), and adding parent
    folders only where needed to tell two chapters apart."""
    paths = [list(p) for p in paths]
    for p in paths:
        while len(p) > 1 and p[-1].lower() in GENERIC:
            p.pop()
    while len(paths) > 1 and all(len(p) > 1 for p in paths) and len({p[-1].lower() for p in paths}) == 1:
        for p in paths:
            p.pop()
    depth = [1] * len(paths)
    while True:
        names = [" / ".join(p[-d:]) for p, d in zip(paths, depth)]
        seen: dict[str, list[int]] = {}
        for i, n in enumerate(names):
            seen.setdefault(n.lower(), []).append(i)
        grew = False
        for group in seen.values():
            if len(group) > 1:
                for i in group:
                    if depth[i] < len(paths[i]):
                        depth[i] += 1
                        grew = True
        if not grew:
            return names


def summary(root: str, project: dict, skipped: list | None = None) -> list[dict]:
    """Scan result for the review screen, flagging chapters already in the project (and with a plugin's file type,
    its name)."""
    sources = {c.get("source") for c in project["chapters"]}
    names = {c["name"] for c in project["chapters"]}
    return [
        {"key": c["key"], "name": c["name"], "kind": c["kind"], "pages": c["pages"],
         "exists": c.get("source", c["path"]) in sources or c["name"] in names}
        | ({"type": FILE_TYPES[c["type"]]["name"]} if c["kind"] == "file" else {})
        for c in scan(root, skipped)
    ]


def read_file(name: str, data: bytes, skipped: list | None = None) -> list[tuple[str | None, list[tuple[str, bytes]]]]:
    """A dropped file of a plugin's type: [(chapter name, [(page name, bytes)])], the name None when the file is one
    chapter (named after it)."""
    ftype = FILE_TYPES[Path(name).suffix.lower()]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(name).name
        path.write_bytes(data)
        parts = _file_parts(path, skipped)
        return [(None if len(parts) == 1 else _part_name(x.get("name"), i), list(ftype["pages"](path, x["pages"])))
                for i, x in enumerate(parts, 1)]


def _size(src) -> tuple[int, int] | None:
    try:
        with Image.open(src) as im:
            return im.size
    except Exception:
        return None


def _thumb(project: dict, page: dict):
    try:
        with Image.open(store.page_path(project, page)) as im:
            return im.convert("L").resize((16, 16)).tobytes()
    except Exception:
        return None


def duplicates(project: dict, chapter_ids) -> list[dict]:
    """Chapters among chapter_ids with the same pages as another chapter (one imported twice, as
    "34-5" and "34.5"): [{"chapter", "same_as"}] by name. Pages of the same size, in the same order,
    with the first, middle and last nearly the same at 16 x 16."""
    pages = {}
    for x in project["pages"]:
        pages.setdefault(x["chapter"], []).append(x)
    names = {c["id"]: c["name"] for c in project["chapters"]}
    out = []
    for cid in chapter_ids:
        mine = pages.get(cid, [])
        for c in project["chapters"]:
            other = pages.get(c["id"], [])
            if c["id"] == cid or not mine or [(x["w"], x["h"]) for x in other] != [(x["w"], x["h"]) for x in mine]:
                continue
            picks = sorted({0, len(mine) // 2, len(mine) - 1})
            a = [_thumb(project, mine[i]) for i in picks]
            b = [_thumb(project, other[i]) for i in picks]
            if all(x and y and sum(abs(p - q) for p, q in zip(x, y)) / len(x) < 3 for x, y in zip(a, b)):
                out.append({"chapter": names[cid], "same_as": c["name"]})
                break
    return out


def _number(name: str) -> float | None:
    """A chapter's number: the first number in its name ("Chapter 45.5" -> 45.5)."""
    m = re.search(r"\d+(?:\.\d+)?", name)
    return float(m.group()) if m else None


def place(project: dict, new_ids: list[str], where: str = "number") -> None:
    """Move the chapters just added (new_ids, at the end of the list) to where they belong: "start", "end",
    right after the chapter with the id where, or by "number": each after the last chapter whose number isn't
    above its own (before the first numbered chapter when all are above; a chapter without a number, or a
    project without numbered chapters, at the end)."""
    added = set(new_ids)
    new = [c for c in project["chapters"] if c["id"] in added]
    order = [c for c in project["chapters"] if c["id"] not in added]
    ids = [c["id"] for c in order]
    if where == "start":
        order = new + order
    elif where in ids:
        i = ids.index(where) + 1
        order = order[:i] + new + order[i:]
    elif where == "number":
        for c in new:
            n = _number(c["name"])
            numbered = [i for i, o in enumerate(order) if _number(o["name"]) is not None]
            if n is None or not numbered:
                order.append(c)
                continue
            below = [i for i in numbered if _number(order[i]["name"]) <= n]
            order.insert(below[-1] + 1 if below else numbered[0], c)
    else:
        order += new
    project["chapters"] = order


def import_into(project: dict, root: str, keys: list[str] | None, where: str = "number") -> dict:
    """Add the chosen chapters (by key, all when keys is None) to the project, in scan order, placed as
    place() says."""
    chosen = scan(root)
    if keys is not None:
        wanted = set(keys)
        chosen = [c for c in chosen if c["key"] in wanted]
    pages_dir = store.project_dir(project["id"]) / "pages"
    added_chapters = added_pages = 0
    new = []
    for c in chosen:
        cid = store.new_chapter_id(project)
        pages = []
        if c["kind"] == "folder":
            for f in c["files"]:
                src = Path(c["path"]) / f
                size = _size(src)
                if size:
                    pages.append({"src": str(src), "name": f, "w": size[0], "h": size[1], "chapter": cid})
        else:
            read = _zip_pages if c["kind"] == "archive" else FILE_TYPES[c["type"]]["pages"]
            for i, (member, data) in enumerate(read(Path(c["path"]), c["files"])):
                size = _size(io.BytesIO(data))
                if not size:
                    continue
                fname = f"{cid}_{i:04d}{Path(member).suffix.lower()}"
                (pages_dir / fname).write_bytes(data)
                pages.append({"file": fname, "name": member, "w": size[0], "h": size[1], "chapter": cid})
        if not pages:
            continue
        project["chapters"].append({"id": cid, "name": c["name"], "source": c.get("source", c["path"])})
        project["pages"].extend(pages)
        new.append(cid)
        added_chapters += 1
        added_pages += len(pages)
    place(project, new, where)
    if added_chapters and project["name"].strip() in ("", "Untitled"):
        project["name"] = Path(root).name  # usually the series title, which Claude is told
    return {"chapters": added_chapters, "pages": added_pages, "duplicates": duplicates(project, new)}
