"""Local HTTP API and static UI. Binds to 127.0.0.1 only."""
import copy
import io
import os
import re
import time
import uuid
import zipfile
from pathlib import Path

import cv2
import numpy as np
import soundfile as sf
from fastapi import Body, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import anthropic

from . import (charscan, claude, claude_code, cloud, cover, credits, editors, estimates, focus, gpu, importer, magi,
               mcp, ocr, online_voices, package, panels, pipeline, scenes, script, store, tts, updates, video,
               voiceover, youtube)

WEB_DIR = Path(__file__).resolve().parent / "web"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}

app = FastAPI(title="Recap Studio")


@app.middleware("http")
async def _fresh_page(request, call_next):
    """The page itself (index.html, app.js, style.css) is checked for a newer copy on every load, so a browser never
    mixes an old script with a new page after an update."""
    response = await call_next(request)
    if not request.url.path.startswith(("/api/", "/files/", "/mcp")):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


@app.on_event("startup")
def _startup():
    store.move_old_layout()  # an installed copy that kept its data beside itself (__main__ does it first)
    store.name_folders()     # folders still named by project id take their project's name
    store.move_video_settings()  # video settings were app-wide until 0.1.1: each project keeps its own
    pipeline.start_worker()


def _project(pid: str) -> dict:
    try:
        return store.load(pid)
    except KeyError:
        raise HTTPException(404, "Project not found")


# ---- projects --------------------------------------------------------------

@app.get("/api/projects")
def list_projects():
    return store.list_all()


@app.post("/api/projects")
def create_project(name: str = Body("Untitled", embed=True), settings: dict | None = Body(None, embed=True)):
    """A new project, with the New project dialog's choices (the kind of comic, how its video looks)."""
    try:
        return store.create(name, settings)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"Those settings don't fit: {e}")


@app.get("/api/projects/new-settings")
def new_project_settings():
    """The New project dialog's starting choices: the last project's."""
    return store.new_project_settings()


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = _project(pid)
    p["job"] = pipeline.public(pipeline.latest_for(pid))
    return p


@app.patch("/api/projects/{pid}")
def rename_project(pid: str, name: str = Body(..., embed=True)):
    """The project's folder takes the new name too, unless a job is using it (then on the next start)."""
    _project(pid)
    p = store.update(pid, lambda p: p.update(name=name.strip() or p["name"]))
    job = pipeline.latest_for(pid)
    if not (job and job["status"] in pipeline.ACTIVE):
        store.rename_folder(pid)
    return p


@app.post("/api/projects/{pid}/show-folder")
def show_project_folder(pid: str):
    _project(pid)
    os.startfile(store.project_dir(pid))
    return {"ok": True}


# ---- where projects are saved (Settings → Projects folder) ----

@app.get("/api/storage")
def storage():
    root = store.projects_root()
    return {"projects": str(root), "default": str(store.paths.DEFAULT_PROJECTS), "synced": store.paths.synced(root),
            "data": str(store.DATA_ROOT), "models": str(store.MODELS_DIR), "addons": str(store.ADDONS_DIR),
            "installed": store.paths.INSTALLED, "move": store.MOVE}


@app.post("/api/storage/show")
def storage_show():
    root = store.projects_root()
    root.mkdir(parents=True, exist_ok=True)
    os.startfile(root)
    return {"ok": True}


@app.post("/api/storage/check")
def storage_check(folder: str = Body(..., embed=True)):
    """Whether projects can go in this folder, and whether a cloud service syncs it (before choosing it)."""
    try:
        path = store.check_folder(folder)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"folder": str(path), "synced": store.paths.synced(path)}


@app.post("/api/storage/projects")
def storage_projects(folder: str = Body(..., embed=True), move: bool = Body(True, embed=True)):
    """Use another projects folder, moving the projects there (move) or leaving them where they are."""
    if any(j["status"] in pipeline.ACTIVE for j in pipeline.jobs.values()):
        raise HTTPException(409, "A job is running. Let it finish, or cancel it, before moving the projects.")
    try:
        store.move_projects(folder, move)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return storage()


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    """Removes the project folder only; folders that chapters were imported from are not touched."""
    _project(pid)
    _no_running_job(pid)
    store.delete(pid)
    return {"ok": True}


@app.post("/api/projects/open-file")
def open_project_file(path: str = Body(..., embed=True)):
    """A new project from a project file (.recap) saved by Save as file."""
    try:
        p = package.open_file(path)
    except (FileNotFoundError, ValueError, zipfile.BadZipFile) as e:
        raise HTTPException(400, str(e) if not isinstance(e, zipfile.BadZipFile) else "That file isn't a zip archive")
    return {"id": p["id"], "name": p["name"]}


@app.get("/api/update")
def update_status():
    """This copy's version, and whether GitHub has a newer release (asked once per start, in the background)."""
    updates.check_in_background()
    return updates.status()


@app.post("/api/update/check")
def update_check():
    return updates.check(force=True)


@app.post("/api/update/install")
def update_install():
    """Download the latest release and install it; the app closes, and the update opens it again."""
    if any(j["status"] in pipeline.ACTIVE for j in pipeline.jobs.values()):
        raise HTTPException(409, "A job is running. Let it finish, or cancel it, before updating.")
    try:
        return updates.start()
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/api/update/skip")
def update_skip(version: str = Body(..., embed=True)):
    updates.skip(version)
    return updates.status()


@app.post("/api/update/page")
def update_page():
    """The release's page on GitHub, in the default browser."""
    updates.open_page()
    return {"opened": True}


@app.get("/api/settings")
def get_global_settings():
    """App-wide settings (writer, voice, video) shared by every project."""
    return store.global_settings()


@app.put("/api/settings")
def update_global_settings(settings: dict = Body(...)):
    return store.update_global(settings)


@app.put("/api/projects/{pid}/settings")
def update_settings(pid: str, settings: dict = Body(...)):
    """Per-project settings; app-wide keys sent here are saved to the global settings."""
    _project(pid)
    clean = {k: v for k, v in settings.items() if k in store.DEFAULT_SETTINGS}
    for key in ("rules", "styles"):
        if key in clean:
            if not isinstance(clean[key], list):
                raise HTTPException(400, f"{key} must be a list")
            clean[key] = [str(x) for x in clean[key]]
    shared = {k: v for k, v in clean.items() if k in store.GLOBAL_KEYS}
    if shared:
        store.update_global(shared)
    own = {k: v for k, v in clean.items() if k not in store.GLOBAL_KEYS}
    return store.update(pid, lambda p: p["settings"].update(own))


@app.post("/api/projects/{pid}/reset-prompt")
def reset_prompt(pid: str):
    """Put persona, rules and styles back to the built-in defaults (the run note is kept)."""
    _project(pid)
    return store.update(pid, lambda p: p["settings"].update(copy.deepcopy(store.DEFAULT_PROMPT)))


@app.put("/api/projects/{pid}/characters")
def save_characters(pid: str, characters: list[dict] = Body(..., embed=True)):
    _project(pid)
    fields = ("name", "gender", "aliases", "description")
    folder = store.project_dir(pid) / "characters"

    def pictures(x):
        return [n for n in x.get("pictures") or [] if _picture_name(n) and (folder / n).is_file()]
    clean = [
        {"id": str(c.get("id") or uuid.uuid4().hex[:8]), **{f: str(c.get(f) or "") for f in fields},
         "pictures": pictures(c),
         # other looks: an alter ego, disguise, transformation, younger self... (see script.system_prompt)
         "looks": [{"id": str(lk.get("id") or uuid.uuid4().hex[:8]), "label": str(lk.get("label") or ""),
                    "note": str(lk.get("note") or ""), "pictures": pictures(lk)} for lk in c.get("looks") or []]}
        for c in characters
    ]
    p = store.update(pid, lambda p: p.update(characters=clean))
    kept = {n for c in clean for x in [c, *c["looks"]] for n in x["pictures"]}
    for f in folder.glob("*.png") if folder.is_dir() else []:  # pictures of removed characters
        if f.name not in kept:
            f.unlink(missing_ok=True)
    return p


# Pictures of each character, for Magi's speaker detection (it matches the people it finds to these).

def _picture_name(name: str) -> bool:
    return bool(re.fullmatch(r"[a-f0-9]{8}_[a-f0-9]{8}\.png", name))


def _add_picture(pid: str, cid: str, img, look: str | None = None) -> dict:
    """Save a picture of a character, or of one of its other looks."""
    if img is None or img.size == 0:
        raise HTTPException(400, "That picture couldn't be read")
    folder = store.project_dir(pid) / "characters"
    folder.mkdir(exist_ok=True)
    h, w = img.shape[:2]
    s = min(1.0, 800 / max(h, w))  # plenty for recognising a face; keeps the project small
    if s < 1:
        img = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    name = f"{cid[:8]}_{uuid.uuid4().hex[:8]}.png"

    def apply(p):
        c = next((c for c in p["characters"] if c["id"] == cid), None)
        if c is None:
            raise HTTPException(404, "Character not found (save it first)")
        if look:
            c = next((lk for lk in c.get("looks") or [] if lk["id"] == look), None)
            if c is None:
                raise HTTPException(404, "That look was not found (save it first)")
        panels.imwrite(folder / name, img)
        c.setdefault("pictures", []).append(name)
    return store.update(pid, apply)


@app.post("/api/projects/{pid}/characters/{cid}/pictures")
async def upload_character_pictures(pid: str, cid: str, files: list[UploadFile], look: str | None = None):
    _project(pid)
    p = None
    for f in files:
        data = np.frombuffer(await f.read(), np.uint8)
        p = _add_picture(pid, cid, cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None, look)
    return p or _project(pid)


@app.post("/api/projects/{pid}/characters/{cid}/pictures/from-panel")
def character_picture_from_panel(pid: str, cid: str, panel: str = Body(..., embed=True),
                                 box: list[float] | None = Body(None, embed=True),
                                 look: str | None = Body(None, embed=True)):
    """A picture cut from a panel: box is [x0, y0, x1, y1] in the panel image's pixels (None = all of it)."""
    p = _project(pid)
    x = next((x for x in p["panels"] if x["id"] == panel), None)
    if x is None:
        raise HTTPException(404, "Panel not found")
    img = panels.imread(store.project_dir(pid) / "panels" / x["file"])
    if img is not None and box:
        h, w = img.shape[:2]
        x0, y0, x1, y1 = (int(max(0, min(v, lim))) for v, lim in zip(box, (w, h, w, h)))
        img = img[min(y0, y1):max(y0, y1), min(x0, x1):max(x0, x1)]
    return _add_picture(pid, cid, img, look)


@app.get("/api/projects/{pid}/estimate")
def estimate(pid: str, chapters: str = ""):
    """How long each step would take on these chapters (comma-separated ids; all when empty):
    doing what's left ("missing", as Run all and Continue do) or redoing everything ("redo")."""
    p = _project(pid)
    chosen = [c for c in chapters.split(",") if c] or None
    steps = ["panels", "ocr", "script", "audio", "video"] + (["scan"] if charscan.installed() else [])
    each = {mode: {s: estimates.estimate(p, chosen, [s], mode == "missing")[s] for s in steps}
            for mode in ("missing", "redo")}
    # Export for editors, either way (the dialog shows both)
    kinds = {kind: estimates.estimate({**p, "settings": {**p["settings"], "editor_export": kind}}, chosen,
                                      ["editors"])["editors"] for kind in editors.KINDS}
    # Run all: what's left, step after step (the voice and video allow for the script it writes)
    return {**each, "editors": kinds, "run_all": estimates.estimate(p, chosen, pipeline.STEPS, True)}


@app.get("/api/projects/{pid}/prompt-preview")
def prompt_preview(pid: str):
    """What the model receives first: the first batch of scenes (Ollama) or of a chapter (Claude)."""
    p = _project(pid)
    s = p["settings"]
    if s.get("llm_provider") in ("claude", "claude_code", *cloud.PROVIDERS):  # a whole chapter per request
        chapter = p["chapters"][0]["name"] if p["chapters"] else "the first chapter"
        pieces = [x for x in p["panels"] if x.get("enabled", True) and p["chapters"]
                  and x["chapter"] == p["chapters"][0]["id"]]
        listing = "\n".join(f"Piece {n}: [image]  Text read from piece {n}: {x.get('text', '')[:80]!r}"
                            for n, x in enumerate(pieces[:6], 1))
        more = f"\n… and {len(pieces) - 6} more pieces" if len(pieces) > 6 else ""
        return {
            "system": script.system_prompt(s, p["characters"], output=claude.OUTPUT_SCENES),
            "user": (f"This is the beginning of the story.\n\nNarrate the following {len(pieces)} pieces "
                     f"from {chapter}, in reading order.\n{listing}{more}\n\n{claude.GROUPING}"),
        }
    first = scenes.groups(p["panels"])[: max(1, int(s["panels_per_batch"]))]
    marks = script.chapter_markers(p)
    sample = ([(n, "\n".join(x.get("text", "") for x in g if x.get("text")), marks.get(g[0]["id"], ""))
               for n, g in enumerate(first, 1)] or [(1, "(text read from the panel)")])
    return {
        "system": script.system_prompt(s, p["characters"]),
        "user": script.user_prompt(sample, "", bool(s["use_vision"])),
    }


# ---- pages -----------------------------------------------------------------

def _save_image(data: bytes, dest: Path) -> tuple[int, int] | None:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    panels.imwrite(dest, img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return img.shape[1], img.shape[0]


@app.post("/api/projects/{pid}/pages")
async def upload_pages(pid: str, files: list[UploadFile], place: str = Form("number")):
    """Each dropped .zip/.cbz becomes a chapter; loose images together become one chapter. They're placed
    as importer.place says (by chapter number unless told otherwise)."""
    _project(pid)
    pages_dir = store.project_dir(pid) / "pages"
    groups: list[tuple[str | None, list[tuple[str, bytes]]]] = []
    loose: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read()
        name = os.path.basename(f.filename or "page")
        if name.lower().endswith((".zip", ".cbz")):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                imgs = [(i.filename, z.read(i)) for i in z.infolist()
                        if not i.is_dir() and Path(i.filename).suffix.lower() in IMAGE_EXT]
            groups.append((Path(name).stem, imgs))
        elif Path(name).suffix.lower() in IMAGE_EXT:
            loose.append((name, data))
    if loose:
        groups.append((None, loose))

    def apply(p):
        added, new = 0, []
        for title, imgs in groups:
            imgs.sort(key=lambda x: store.natural_key(x[0]))
            cid = store.new_chapter_id(p)
            pages = []
            for i, (orig, data) in enumerate(imgs):
                fname = f"{cid}_{i:04d}.jpg"
                size = _save_image(data, pages_dir / fname)
                if size:
                    pages.append({"file": fname, "name": orig, "w": size[0], "h": size[1], "chapter": cid})
            if pages:
                p["chapters"].append({"id": cid, "name": title or f"Chapter {len(p['chapters']) + 1}", "source": None})
                p["pages"].extend(pages)
                new.append(cid)
                added += len(pages)
        importer.place(p, new, place)
        return {"added": added, "total": len(p["pages"]), "duplicates": importer.duplicates(p, new)}
    return store.update(pid, apply)


@app.post("/api/projects/{pid}/scan-folder")
def scan_folder(pid: str, path: str = Body(..., embed=True)):
    p = _project(pid)
    try:
        return {"path": path, "chapters": importer.summary(path, p)}
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))


@app.post("/api/projects/{pid}/import-folder")
def import_folder(pid: str, path: str = Body(..., embed=True), keys: list[str] | None = Body(None, embed=True),
                  place: str = Body("number", embed=True)):
    """Chapters can be added while a job runs: the job goes on with the chapters it started with."""
    _project(pid)
    try:
        return store.update(pid, lambda p: importer.import_into(p, path, keys, place))
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))


def _remove_chapter_files(p: dict, chapter_ids: set[str]) -> None:
    d = store.project_dir(p["id"])
    for x in p["pages"]:
        if x["chapter"] in chapter_ids and x.get("file"):
            (d / "pages" / x["file"]).unlink(missing_ok=True)
    for x in p["panels"]:
        if x["chapter"] in chapter_ids:
            (d / "panels" / x["file"]).unlink(missing_ok=True)
            if x.get("audio"):
                (d / "audio" / x["audio"]).unlink(missing_ok=True)
    for cid in chapter_ids:  # the chapter's rendered video pieces, its video for editors, its recording
        for f in [*(d / "render").glob(f"{cid}-*"), *(d / "editors").glob(f"{cid}-*"), *(d / "voiceover").glob(f"{cid}.*")]:
            f.unlink(missing_ok=True)


def _no_running_job(pid: str):
    job = pipeline.latest_for(pid)
    if job and job["status"] in pipeline.ACTIVE:
        raise HTTPException(409, "Wait for the running job to finish first")


@app.delete("/api/projects/{pid}/pages")
def clear_pages(pid: str):
    _project(pid)
    _no_running_job(pid)

    def apply(p):
        _remove_chapter_files(p, {c["id"] for c in p["chapters"]})
        p["chapters"], p["pages"], p["panels"], p["output"] = [], [], [], None
    return store.update(pid, apply)


# ---- chapters --------------------------------------------------------------

@app.patch("/api/projects/{pid}/chapters/{cid}")
def edit_chapter(pid: str, cid: str, changes: dict = Body(...)):
    """Rename a chapter ({"name"}), or keep its narration as it is after the YouTube-safe wording check
    ({"wording": []}: the flagged lines aren't flagged again)."""
    _project(pid)

    def apply(p):
        for c in p["chapters"]:
            if c["id"] == cid:
                if isinstance(changes.get("name"), str):
                    c["name"] = changes["name"].strip() or c["name"]
                if changes.get("wording") == []:
                    c["wording_kept"] = list(dict.fromkeys(
                        (c.get("wording_kept") or []) + [w["line"] for w in c.pop("wording", None) or []]))
                return
        raise HTTPException(404, "Chapter not found")
    return store.update(pid, apply)


@app.delete("/api/projects/{pid}/chapters/{cid}")
def delete_chapter(pid: str, cid: str):
    _project(pid)
    _no_running_job(pid)

    def apply(p):
        _remove_chapter_files(p, {cid})
        p["chapters"] = [c for c in p["chapters"] if c["id"] != cid]
        p["pages"] = [x for x in p["pages"] if x["chapter"] != cid]
        p["panels"] = [x for x in p["panels"] if x["chapter"] != cid]
    return store.update(pid, apply)


# ---- your own recorded voice-over, per chapter (see voiceover.py) ----

@app.post("/api/projects/{pid}/chapters/{cid}/voiceover")
async def upload_voiceover(pid: str, cid: str, file: UploadFile):
    """Keep the chapter's recording (replacing one sent before) and split it into its lines."""
    p = _project(pid)
    _no_running_job(pid)
    if not any(c["id"] == cid for c in p["chapters"]):
        raise HTTPException(404, "Chapter not found")
    name = os.path.basename(file.filename or "recording")
    ext = Path(name).suffix.lower()
    if ext not in voiceover.FORMATS:
        raise HTTPException(400, f"That isn't an audio file this can read ({', '.join(voiceover.FORMATS)})")
    if not voiceover.installed():
        raise HTTPException(409, "Download the speech recognizer first")
    folder = store.project_dir(pid) / "voiceover"
    folder.mkdir(exist_ok=True)
    for old in folder.glob(f"{cid}.*"):
        old.unlink(missing_ok=True)
    dest = folder / f"{cid}{ext}"
    with open(dest.with_name(dest.name + ".part"), "wb") as out:
        while chunk := await file.read(1 << 20):
            out.write(chunk)
    dest.with_name(dest.name + ".part").replace(dest)

    def apply(q):
        for c in q["chapters"]:
            if c["id"] == cid:
                c["voiceover"] = {"file": f"voiceover/{dest.name}", "name": name, "size": dest.stat().st_size,
                                  "time": time.time()}
    store.update(pid, apply)
    return pipeline.public(pipeline.submit(pid, ["voiceover"], None, [cid], "redo"))


@app.delete("/api/projects/{pid}/chapters/{cid}/voiceover")
def remove_voiceover(pid: str, cid: str):
    """Forget the chapter's recording: its lines go back to having no voice, for Voice to read them."""
    _project(pid)
    _no_running_job(pid)
    d = store.project_dir(pid)

    def apply(p):
        for x in p["panels"]:
            if x["chapter"] != cid:
                continue
            (d / "audio" / f"{x['id']}_rec.wav").unlink(missing_ok=True)  # also a clip whose line was edited since
            if x.get("audio_source") == "recording":
                if x.get("audio") == f"{x['id']}_rec.wav":
                    x["audio"], x["duration"] = None, 0.0
                x.pop("audio_source", None)
                x.pop("voice_match", None)
        for c in p["chapters"]:
            if c["id"] == cid and c.get("voiceover"):
                (d / c["voiceover"]["file"]).unlink(missing_ok=True)
                c.pop("voiceover")
    return store.update(pid, apply)


@app.get("/api/addons/vosk")
def vosk_status():
    return {**voiceover.DOWNLOAD.status(), "installed": voiceover.installed()}


@app.post("/api/addons/vosk/install")
def vosk_install():
    try:
        voiceover.DOWNLOAD.start()
    except ValueError as e:
        raise HTTPException(400, str(e))
    return vosk_status()


@app.post("/api/addons/vosk/cancel")
def vosk_cancel():
    voiceover.DOWNLOAD.cancel()
    return vosk_status()


@app.delete("/api/addons/vosk")
def vosk_remove():
    for job in pipeline.jobs.values():
        if job["status"] in pipeline.ACTIVE and job.get("step") == "voiceover":
            raise HTTPException(409, "A recording is being split right now; try again when that's done")
    try:
        voiceover.DOWNLOAD.remove()
    except ValueError as e:
        raise HTTPException(409, str(e))
    return vosk_status()


@app.put("/api/projects/{pid}/chapters/order")
def reorder_chapters(pid: str, ids: list[str] = Body(..., embed=True)):
    _project(pid)

    def apply(p):
        by_id = {c["id"]: c for c in p["chapters"]}
        if set(ids) != set(by_id):
            raise HTTPException(400, "Order must list every chapter once")
        p["chapters"] = [by_id[i] for i in ids]
    return store.update(pid, apply)


# ---- panels ----------------------------------------------------------------

@app.patch("/api/projects/{pid}/panels/{panel_id}")
def edit_panel(pid: str, panel_id: str, changes: dict = Body(...)):
    _project(pid)

    def apply(p):
        for panel in p["panels"]:
            if panel["id"] == panel_id:
                if "narration" in changes and changes["narration"] != panel["narration"]:
                    panel["narration"] = str(changes["narration"])
                    panel["audio"], panel["duration"] = None, 0.0  # (a recorded line too: it no longer matches)
                    panel["script_done"] = True  # a hand-written (or deliberately emptied) line counts as done
                if "text" in changes:
                    panel["text"] = str(changes["text"])
                if "enabled" in changes:
                    panel["enabled"] = bool(changes["enabled"])
                    if panel.get("credits"):  # left out as credits: your choice stands when they're found again
                        panel["credits_kept"] = panel["enabled"]
                return panel
        raise HTTPException(404, "Panel not found")
    return store.update(pid, apply)


@app.put("/api/projects/{pid}/panels/{panel_id}/cover")
def set_cover(pid: str, panel_id: str, boxes: list = Body(..., embed=True)):
    """A piece's black boxes, as drawn in the app: [[left, top, right, bottom], ...] as fractions of its
    picture. They replace the ones you drew before, and the finder's and the writer's that you kept or
    removed (sent with by "auto" or "claude", those keep their source)."""
    _project(pid)

    def apply(p):
        for panel in p["panels"]:
            if panel["id"] == panel_id:
                kept = []
                for b in boxes:
                    box, by = (b.get("box"), b.get("by", "you")) if isinstance(b, dict) else (b, "you")
                    box = cover.clean(box) if box is not None else None
                    if box:
                        kept.append({"box": box, "by": by if by in ("auto", "claude") else "you"})
                panel["cover"] = kept
                if not kept:
                    panel.pop("cover")
                return panel
        raise HTTPException(404, "Panel not found")
    return store.update(pid, apply)


@app.get("/api/projects/{pid}/panels/{panel_id}/same")
def same_picture(pid: str, panel_id: str):
    """The pieces at the start or end of other chapters with the same picture (the scan group's page or the
    series' card, repeated), so a choice on one can be made on them too."""
    p = _project(pid)
    return {"ids": credits.same(p, store.project_dir(pid), panel_id)}


@app.post("/api/projects/{pid}/panels/choose")
def choose_panels(pid: str, ids: list[str] = Body(..., embed=True), enabled: bool = Body(..., embed=True)):
    """Use or leave out these pieces, as your choice (see credits.choose). Returns the pieces changed."""
    _project(pid)
    return {"panels": store.update(pid, lambda p: copy.deepcopy(credits.choose(p, ids, enabled)))}


@app.post("/api/projects/{pid}/panels/{panel_id}/join")
def join_panel(pid: str, panel_id: str, joined: bool = Body(..., embed=True)):
    """Join a scene onto the scene above it, or split a scene so this piece starts a new one."""
    _project(pid)
    _no_running_job(pid)

    def apply(p):
        enabled = [x for x in p["panels"] if x.get("enabled", True)]
        i = next((k for k, x in enumerate(enabled) if x["id"] == panel_id), None)
        if i is None:
            raise HTTPException(404, "Panel not found")
        panel = enabled[i]
        if joined:
            if i == 0 or enabled[i - 1]["chapter"] != panel["chapter"]:
                raise HTTPException(400, "The first scene of a chapter can't be joined to the one above")
            head = next(x for x in reversed(enabled[:i]) if not x.get("joined"))
            text = " ".join(t for t in (head.get("narration", "").strip(), panel.get("narration", "").strip()) if t)
            head["narration"], head["script_done"] = text, bool(text) or head.get("script_done", False)
            for x in (head, panel):
                x["audio"], x["duration"] = None, 0.0
            panel["joined"], panel["narration"], panel["script_done"] = True, "", True
        elif panel.get("joined"):
            # a new scene: it needs its own narration, which Run all will write
            panel["joined"], panel["narration"], panel["script_done"] = False, "", False
            panel["audio"], panel["duration"] = None, 0.0
    return store.update(pid, apply)


# ---- jobs ------------------------------------------------------------------

@app.post("/api/projects/{pid}/run")
def run(pid: str, steps: list[str] = Body(..., embed=True),
        only: list[str] | None = Body(None, embed=True),
        chapters: list[str] | None = Body(None, embed=True),
        mode: str = Body("missing", embed=True),
        rewrite: list[str] | None = Body(None, embed=True)):
    """rewrite: chapters whose script is written again from scratch, even when the job only does what's missing
    (Write the chapter again while Voice waits for its wording to be checked)."""
    _project(pid)
    current = pipeline.latest_for(pid)
    if current and current["status"] in pipeline.ACTIVE:
        raise HTTPException(409, "A job is already running for this project")
    return pipeline.public(pipeline.submit(pid, steps, only, chapters, mode,
                                           options={"rewrite": rewrite} if rewrite else None))


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str):
    job = pipeline.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job["cancel"] = True
    return pipeline.public(job)


@app.post("/api/jobs/{job_id}/pause")
def pause(job_id: str):
    """Hold the job after the item it's working on; Resume carries on from there."""
    job = pipeline.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] in ("queued", "running"):
        job["pause"] = True
    return pipeline.public(job)


@app.post("/api/jobs/{job_id}/resume")
def resume(job_id: str):
    job = pipeline.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job["pause"] = False
    return pipeline.public(job)


# ---- environment -----------------------------------------------------------

@app.get("/api/status")
def status(ollama_url: str = "http://localhost:11434"):
    try:
        models = script.list_models(ollama_url)
        ollama_err = None
    except Exception as e:
        models, ollama_err = [], str(e)
    return {
        "ollama_models": models,
        "ollama_error": ollama_err,
        "tts_ready": tts.available(),
        "voices": tts.voices() if tts.available() else [],
        "ffmpeg": video.find_ffmpeg() is not None,
        # what Automatic means for video export on this computer (Settings → Performance)
        "video": {"nvenc": video.hardware_encoder(video.find_ffmpeg()) is not None,  # any GPU encoder
                  "encoder": video.HARDWARE.get(video.hardware_encoder(video.find_ffmpeg()), ("",))[0],
                  "cores": os.cpu_count(),
                  "auto_gpu": video.drawing_processes({}, True), "auto_cpu": video.drawing_processes({}, False)},
        "charscan": {"installed": charscan.installed()},  # shows the Characters step
        "ocr_english": ocr.ENGLISH.installed(),
        # devices are what the loaded engines run on ("" until first used)
        "gpu": {"available": gpu.available(), "card": gpu.nvidia_card(),
                "voice": tts.device if tts._engine is not None else "",
                "ocr": ocr.device if ocr._engine is not None else "",
                "addon": gpu.addon_status()},  # GPU support, the download (Settings → Performance)
        "claude": {**claude.key_status(),
                   "models": [{"id": m, "name": n, "note": claude.MODEL_NOTES.get(m, "")} for m, n in claude.MODELS]},
        "writers": {k: {**cloud.key_status(k), "name": v["name"], "about": v["about"], "keys_url": v["keys_url"],
                        "default_model": v["default_model"]} for k, v in cloud.PROVIDERS.items()},
        "redraw": {k: {"name": v["name"], "default_model": v["default_model"]} for k, v in youtube.REDRAW.items()},
        "elevenlabs": online_voices.key_status(),
    }


# ---- GPU support (ONNX Runtime's GPU build, with CUDA and cuDNN): downloaded on request (never shipped) ----

@app.get("/api/addons/gpu")
def gpu_addon_status():
    return gpu.addon_status()


@app.post("/api/addons/gpu/install")
def gpu_addon_install():
    try:
        return gpu.install()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/addons/gpu/cancel")
def gpu_addon_cancel():
    gpu.DOWNLOAD.cancel()
    return gpu.addon_status()


@app.delete("/api/addons/gpu")
def gpu_addon_remove():  # (in use, it's only marked removed: the next start deletes it)
    try:
        return gpu.remove()
    except ValueError as e:
        raise HTTPException(409, str(e))


# ---- English text-reading model: downloaded on request (never shipped) ----

# ---- the voice model (Kokoro): fetched by setup.ps1, or here when it's missing ----

SAMPLE_TEXT = ("Previously, a shy new designer moved into the company house, and nothing went quite the way "
               "she had planned.")


@app.get("/api/addons/voice")
def voice_status():
    return {**tts.DOWNLOAD.status(), "installed": tts.available()}


@app.post("/api/addons/voice/install")
def voice_install():
    if not tts.available():
        try:
            tts.DOWNLOAD.start()
        except ValueError as e:
            raise HTTPException(409, str(e))
    return voice_status()


@app.post("/api/addons/voice/cancel")
def voice_cancel():
    tts.DOWNLOAD.cancel()
    return voice_status()


@app.post("/api/voice/sample")
def voice_sample(voice: str = Body(...), speed: float = Body(1.0), provider: str = Body("kokoro"),
                 model: str = Body("")):
    """A short spoken sample, for choosing a voice (an ElevenLabs sample uses about 110 characters of its plan)."""
    speed = max(0.5, min(2.0, speed))
    try:
        if provider == "edge":
            samples = online_voices.edge_speak(SAMPLE_TEXT, voice, speed)
        elif provider == "elevenlabs":
            samples = online_voices.eleven_speak(SAMPLE_TEXT, voice, model, speed)
        else:
            if not tts.available():
                raise HTTPException(409, "The voice model isn't downloaded yet")
            if voice not in tts.voices():
                raise HTTPException(400, "Unknown voice")
            samples = tts.synthesize(SAMPLE_TEXT, voice, speed, use_gpu=store.global_settings().get("use_gpu", True))
    except online_voices.Problem as e:
        raise HTTPException(502, str(e))
    buf = io.BytesIO()
    sf.write(buf, samples, tts.SAMPLE_RATE, format="WAV")
    return Response(buf.getvalue(), media_type="audio/wav")


# ---- online voices: Microsoft Edge's (free) and ElevenLabs (paid, key stored only on this PC) ----

@app.get("/api/voices/edge")
def edge_voice_list():
    try:
        return {"voices": online_voices.edge_voices()}
    except online_voices.Problem as e:
        raise HTTPException(502, str(e))


@app.get("/api/voices/elevenlabs")
def elevenlabs_lists():
    """The key's voices and the text-to-speech models (needs a key)."""
    try:
        return {"voices": online_voices.eleven_voices(), "models": online_voices.eleven_models()}
    except online_voices.Problem as e:
        raise HTTPException(401 if e.kind == "key" else 502, str(e))


@app.put("/api/voices/elevenlabs/key")
def set_elevenlabs_key(key: str = Body(..., embed=True)):
    key = key.strip()
    if len(key) < 16 or " " in key:
        raise HTTPException(400, "That doesn't look like an API key")
    online_voices.save_key(key)
    return check_elevenlabs_key()


@app.delete("/api/voices/elevenlabs/key")
def delete_elevenlabs_key():
    online_voices.delete_key()
    return online_voices.key_status()


@app.post("/api/voices/elevenlabs/check")
def check_elevenlabs_key():
    try:
        sub = online_voices.eleven_check()
        left = (f": {sub['used']:,} of {sub['limit']:,} characters used this month"
                if isinstance(sub.get("used"), int) and isinstance(sub.get("limit"), int) else "")
        return {**online_voices.key_status(), "ok": True, "message": f"Key works{left}", **sub}
    except online_voices.Problem as e:
        why = "The key was rejected by ElevenLabs" if e.kind == "key" else f"Couldn't check the key: {e}"
        return {**online_voices.key_status(), "ok": False, "message": why}


@app.get("/api/video-speed")
def video_speed(resolution: int = 1080, fps: int = 30, video_style: str = "auto", video_encoder: str = "auto"):
    """About how long an hour of video takes to make with these settings, on this computer (drawing it,
    then joining the chapters)."""
    s = {"resolution": resolution, "fps": fps, "video_style": video_style, "video_encoder": video_encoder}
    return {"seconds_per_hour": round((estimates.seconds_per_unit(estimates.key("video", s)) * fps
                                       + estimates.seconds_per_unit("join")) * 3600)}


# ---- the subject finder for pan and zoom (U2-Net-p): downloaded on request (never shipped) ----

@app.get("/api/addons/focus")
def focus_status():
    return focus.DOWNLOAD.status()


@app.post("/api/addons/focus/install")
def focus_install():
    try:
        return focus.DOWNLOAD.start()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/addons/focus/cancel")
def focus_cancel():
    focus.DOWNLOAD.cancel()
    return focus.DOWNLOAD.status()


@app.delete("/api/addons/focus")
def focus_remove():
    for job in pipeline.jobs.values():  # the model is open while a video is planned
        if job["status"] in pipeline.ACTIVE and job.get("step") in ("video", "editors"):
            raise HTTPException(409, "A video is being made right now; try again when that's done")
    focus.forget()
    try:
        return focus.DOWNLOAD.remove()
    except ValueError as e:
        raise HTTPException(409, str(e))


# ---- the box finder for black boxes (anime_censor_detection): downloaded on request (never shipped) ----

@app.get("/api/addons/boxes")
def boxes_status():
    return cover.DOWNLOAD.status()


@app.post("/api/addons/boxes/install")
def boxes_install():
    try:
        return cover.DOWNLOAD.start()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/addons/boxes/cancel")
def boxes_cancel():
    cover.DOWNLOAD.cancel()
    return cover.DOWNLOAD.status()


@app.delete("/api/addons/boxes")
def boxes_remove():
    for job in pipeline.jobs.values():  # the model is open while boxes are found
        if job["status"] in pipeline.ACTIVE and job.get("step") in ("boxes", "video", "editors"):
            raise HTTPException(409, "Boxes are being found or a video made right now; try again when that's done")
    cover.forget()
    try:
        return cover.DOWNLOAD.remove()
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/addons/ocr-en")
def ocr_en_status():
    return {**ocr.ENGLISH.status(), "in_use": ocr.model == "English"}


@app.post("/api/addons/ocr-en/install")
def ocr_en_install():
    try:
        return ocr.ENGLISH.start()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/addons/ocr-en/cancel")
def ocr_en_cancel():
    ocr.ENGLISH.cancel()
    return ocr.ENGLISH.status()


@app.delete("/api/addons/ocr-en")
def ocr_en_remove():
    for job in pipeline.jobs.values():  # the files are open while text is being read
        if job["status"] in pipeline.ACTIVE and job.get("step") == "ocr":
            raise HTTPException(409, "Text is being read right now; try again when that's done")
    try:
        return ocr.ENGLISH.remove()
    except ValueError as e:
        raise HTTPException(409, str(e))


# ---- character scan: models downloaded on request (never shipped); review of the results ----

@app.get("/api/addons/charscan")
def charscan_status():
    return charscan.status()


@app.post("/api/addons/charscan/install")
def charscan_install():
    try:
        return charscan.install()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/addons/charscan/cancel")
def charscan_cancel():
    charscan.cancel_install()
    return charscan.status()


@app.delete("/api/addons/charscan")
def charscan_remove():
    try:
        return charscan.remove()
    except ValueError as e:
        raise HTTPException(409, str(e))


def _scan_group(p: dict, gid: str) -> dict:
    g = next((g for g in (p.get("scan") or {}).get("groups", []) if g["id"] == gid), None)
    if g is None:
        raise HTTPException(404, "That scan result is gone; scan again")
    return g


def _set_group(pid: str, gid: str, **fields) -> dict:
    def apply(p):
        _scan_group(p, gid).update(fields)
    return store.update(pid, apply)


@app.post("/api/projects/{pid}/scan/groups/{gid}/add")
def scan_add(pid: str, gid: str, name: str = Body("", embed=True), gender: str = Body("", embed=True),
             description: str = Body("", embed=True), character: str | None = Body(None, embed=True),
             look: str | None = Body(None, embed=True), new_look: bool = Body(False, embed=True),
             aliases: str = Body("", embed=True)):
    """Make a scanned group a character, or add it to an existing one: to the character itself, to one
    of its other looks (look), or as a new look (new_look, named `name`, noted with `description`).
    The group's best views become its pictures."""
    return _add_group(pid, gid, name, gender, description, character, look, new_look, aliases)


@app.post("/api/projects/{pid}/scan/add-named")
def scan_add_named(pid: str, items: list[dict] = Body(..., embed=True)):
    """Add several groups at once (Add all suggested): items [{"group", "name", "gender", "aliases",
    "description"}], biggest group first. A group whose name is already on the list (as a name or another
    name), or whose other names include a listed name, or named like an item before it, is added to that
    character; the others become new characters. Items without a name are skipped."""
    for it in items:
        p = _project(pid)
        g = next((g for g in (p.get("scan") or {}).get("groups", []) if g["id"] == it.get("group")), None)
        name = (it.get("name") or "").strip()
        if not g or g["status"] != "open" or not name:
            continue
        aliases = [a.strip() for a in (it.get("aliases") or "").split(",") if a.strip()]
        known = {}
        for c in p["characters"]:
            for n in [c.get("name") or "", *(c.get("aliases") or "").split(",")]:
                known.setdefault(n.strip().lower(), c["id"])
        named = {(c.get("name") or "").strip().lower(): c["id"] for c in p["characters"]}
        cid = known.get(name.lower()) or next((named[a.lower()] for a in aliases if a.lower() in named), None)
        _add_group(pid, g["id"], name, it.get("gender") or "", it.get("description") or "", cid,
                   aliases=", ".join(([name] if cid else []) + aliases))
    return _project(pid)


def _add_group(pid, gid, name="", gender="", description="", character=None, look=None, new_look=False, aliases=""):
    p = _project(pid)
    g = _scan_group(p, gid)
    lid = None
    if character:
        c = next((c for c in p["characters"] if c["id"] == character), None)
        if c is None:
            raise HTTPException(404, "Character not found")
        cid = character
        if new_look:
            lid = uuid.uuid4().hex[:8]

            def add_look(q):
                next(c for c in q["characters"] if c["id"] == cid).setdefault("looks", []).append(
                    {"id": lid, "label": name.strip() or "another look", "note": description.strip(), "pictures": []})
            store.update(pid, add_look)
        elif look:
            if not any(lk["id"] == look for lk in c.get("looks") or []):
                raise HTTPException(404, "That look was not found")
            lid = look
        else:
            def fill(q):  # an existing character keeps its own words; an empty description gets the suggestion
                c = next(c for c in q["characters"] if c["id"] == cid)
                if not c.get("description", "").strip() and description.strip():
                    c["description"] = description.strip()
                have = [a.strip() for a in (c.get("aliases") or "").split(",") if a.strip()]
                new = [a.strip() for a in aliases.split(",") if a.strip()]  # other names it goes by, kept
                new = [a for a in dict.fromkeys(new) if a.lower() not in {x.lower() for x in [c["name"], *have]}]
                if new:
                    c["aliases"] = ", ".join(have + new)
            store.update(pid, fill)
    else:
        if not name.strip():
            raise HTTPException(400, "Give the character a name first")
        cid = uuid.uuid4().hex[:8]
        store.update(pid, lambda q: q["characters"].append({
            "id": cid, "name": name.strip(), "gender": gender.strip(), "aliases": aliases.strip(),
            "description": description.strip(), "pictures": [], "looks": []}))
    by_id = {x["id"]: x for x in p["panels"]}
    for snap in g["snapshots"][:4]:  # full-resolution crops of the views shown for review
        x = by_id.get(snap["piece"])
        img = panels.imread(store.project_dir(pid) / "panels" / x["file"]) if x else None
        if img is not None:
            x0, y0, x1, y1 = snap["box"]
            _add_picture(pid, cid, img[y0:y1, x0:x1], lid)
    return _set_group(pid, gid, status="added", character=cid, look=lid)


@app.post("/api/projects/{pid}/scan/groups/{gid}/ignore")
def scan_ignore(pid: str, gid: str):
    _project(pid)
    return _set_group(pid, gid, status="ignored")


@app.post("/api/projects/{pid}/scan/groups/{gid}/reopen")
def scan_reopen(pid: str, gid: str):
    _project(pid)
    return _set_group(pid, gid, status="open", why="reopened")  # (not set aside as credits again)


@app.post("/api/projects/{pid}/scan/groups/{gid}/merge")
def scan_merge(pid: str, gid: str, into: str = Body(..., embed=True)):
    _project(pid)
    try:
        return store.update(pid, lambda p: charscan.merge_groups(p, gid, into))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/projects/{pid}/scan")
def scan_clear(pid: str):
    _project(pid)
    return store.update(pid, charscan.clear)


# ---- Magi v2 add-on (speaker detection): downloaded on request, never shipped ----

@app.get("/api/addons/magi")
def magi_status():
    return magi.status()


@app.post("/api/addons/magi/install")
def magi_install(accept_license: bool = Body(False, embed=True)):
    try:
        return magi.install(accept_license)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/addons/magi/cancel")
def magi_cancel():
    magi.cancel_install()
    return magi.status()


@app.delete("/api/addons/magi")
def magi_remove():
    try:
        return magi.remove()
    except ValueError as e:
        raise HTTPException(409, str(e))


# ---- Claude API key (stored only on this PC, never sent back to the page) ----

@app.put("/api/claude/key")
def set_claude_key(key: str = Body(..., embed=True)):
    key = key.strip()
    if not key.startswith("sk-ant-"):
        raise HTTPException(400, "That doesn't look like an Anthropic API key (they start with sk-ant-)")
    claude.save_key(key)
    return check_claude_key()


@app.delete("/api/claude/key")
def delete_claude_key():
    claude.delete_key()
    return claude.key_status()


@app.post("/api/claude/check")
def check_claude_key():
    try:
        claude.check_key()
        return {**claude.key_status(), "ok": True, "message": "Key works"}
    except anthropic.AuthenticationError:
        return {**claude.key_status(), "ok": False, "message": "The key was rejected by Anthropic"}
    except Exception as e:
        return {**claude.key_status(), "ok": False, "message": f"Couldn't check the key: {e}"}


# ---- Claude Code as the script writer (claude_code.py): on the user's Claude plan, logged in through its own command ----

@app.get("/api/claude-code")
def claude_code_status(refresh: bool = False):
    return claude_code.status(refresh)


@app.post("/api/claude-code/login")
def claude_code_login():
    try:
        return claude_code.login()
    except RuntimeError as e:
        raise HTTPException(404, str(e))


# ---- MCP (mcp.py): Claude Code works with the open app through these tools ----

@app.post("/mcp")
def mcp_message(request: Request, message: dict | list = Body(...)):
    """MCP's JSON-RPC over HTTP (replies as plain JSON, no event stream). Only this computer's own programs reach
    the app; a web page's request (it carries an Origin) is refused."""
    origin = request.headers.get("origin")
    if origin and not re.match(r"https?://(127\.0\.0\.1|localhost)(:\d+)?$", origin):
        raise HTTPException(403, "Not from a web page")
    replies = [r for m in message if (r := mcp.handle(m)) is not None] if isinstance(message, list) else mcp.handle(message)
    return JSONResponse(replies) if replies else Response(status_code=202)


@app.get("/mcp")
def mcp_stream():
    return Response(status_code=405)  # no server-sent events: every reply comes back with its request


# ---- OpenRouter, Google Gemini, DeepSeek, Groq and Mistral: keys (stored only on this PC) and model lists ----

def _writer(provider: str) -> str:
    if provider not in cloud.PROVIDERS:
        raise HTTPException(404, "Unknown script writer")
    return provider


@app.put("/api/writers/{provider}/key")
def set_writer_key(provider: str, key: str = Body(..., embed=True)):
    key = key.strip()
    if len(key) < 16 or " " in key:
        raise HTTPException(400, "That doesn't look like an API key")
    cloud.save_key(_writer(provider), key)
    return check_writer_key(provider)


@app.delete("/api/writers/{provider}/key")
def delete_writer_key(provider: str):
    cloud.delete_key(_writer(provider))
    return cloud.key_status(provider)


@app.post("/api/writers/{provider}/check")
def check_writer_key(provider: str):
    name = cloud.PROVIDERS[_writer(provider)]["name"]
    try:
        cloud.check_key(provider)
        return {**cloud.key_status(provider), "ok": True, "message": "Key works"}
    except cloud.Problem as e:
        why = f"The key was rejected by {name}" if e.kind == "key" else f"Couldn't check the key: {e}"
        return {**cloud.key_status(provider), "ok": False, "message": why}


@app.post("/api/writers/{provider}/test")
def test_writer_model(provider: str, model: str = Body(..., embed=True)):
    """Send the model one tiny request: shows straight away whether this key can use it."""
    return cloud.test_model(_writer(provider), model.strip())


@app.get("/api/writers/{provider}/models")
def writer_models(provider: str):
    try:
        return {"models": cloud.list_models(_writer(provider))}
    except cloud.Problem as e:
        raise HTTPException(502, f"Couldn't get the list of models: {e}")


@app.post("/api/projects/{pid}/open-folder")
def open_folder(pid: str):
    _project(pid)
    os.startfile(store.project_dir(pid) / "output")
    return {"ok": True}


# ---- the YouTube title and thumbnail (youtube.py; suggested by pipeline.run_title) ----------------

@app.patch("/api/projects/{pid}/youtube")
def edit_title(pid: str, title: str = Body(..., embed=True)):
    if not _project(pid).get("youtube"):
        raise HTTPException(404, "No title has been suggested yet")
    return store.update(pid, lambda p: p["youtube"].update(title=title.strip()[:100]))


@app.post("/api/projects/{pid}/youtube/thumbnail")
def redraw_thumbnail(pid: str, text: str | None = Body(None, embed=True), panel: str | None = Body(None, embed=True),
                     pictures: list[str] | None = Body(None, embed=True), marks: list[dict] | None = Body(None, embed=True)):
    """Draw the thumbnail again: with other words or marks, or other pictures from those it was chosen from (one
    or two; panel: just that one). Marks on a picture taken out move to the one put in."""
    p = _project(pid)
    yt = p.get("youtube")
    if not yt:
        raise HTTPException(404, "No title has been suggested yet")
    before = yt.get("pictures") or [yt["panel"]]
    pics = list(dict.fromkeys(pictures or ([panel] if panel else before)))
    byid = {x["id"]: x for x in p["panels"]}
    if not 1 <= len(pics) <= 2 or any(i not in byid for i in pics):
        raise HTTPException(409, "That picture is gone (panels were detected again): suggest again")
    text = (yt.get("text") or "") if text is None else text.strip()[:60]
    added = [i for i in pics if i not in before]
    marks = [{**m, "picture": m.get("picture") if m.get("picture") in pics or not added else added[0]}
             for m in (yt.get("marks") or [] if marks is None else marks) if isinstance(m, dict)]
    marks = youtube.tidy_marks(marks, pics)
    try:
        drawn = youtube.draw(p, [byid[i] for i in pics], marks, text)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return store.update(pid, lambda q: q["youtube"].update(text=text, pictures=pics, panel=pics[0], marks=marks,
                                                          file=drawn["file"], created=time.time()))


@app.post("/api/projects/{pid}/youtube/redraw")
def redraw_with_ai(pid: str):
    """Redraw with AI (youtube.redraw): an image model repaints the thumbnail as drawn now. Takes a minute."""
    p = _project(pid)
    if not p.get("youtube"):
        raise HTTPException(404, "No title has been suggested yet")
    try:
        ai = youtube.redraw(p)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return store.update(pid, lambda q: q["youtube"].update(ai=ai))


@app.get("/api/redraw/{provider}/models")
def redraw_models(provider: str):
    if provider not in youtube.REDRAW:
        raise HTTPException(404, "Unknown service")
    try:
        return {"models": youtube.image_models(provider)}
    except cloud.Problem as e:
        raise HTTPException(502, f"Couldn't get the list of models: {e}")


# ---- files -----------------------------------------------------------------

@app.get("/files/{pid}/{path:path}")
def project_file(pid: str, path: str):
    _project(pid)
    base = store.project_dir(pid).resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404)
    return FileResponse(target, headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
