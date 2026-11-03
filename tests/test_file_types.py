"""Plugin file types (plugins.Plugin.file_type, importer.FILE_TYPES), with a test plugin reading a made-up ".twopart" file
(a zip whose parts.json says what its chapters are):
- Import folder: a file of the type is one chapter named after it, or a chapter for each of its parts, named after them
  and kept in the file's order, among the folders' chapters; the files it can't read are listed with why;
- importing: the pages extracted, each part remembered as its own source (so it's "already imported" next time);
- dropping files on a project: the same chapters, and the same reasons for what's left out;
- the page is told the types (the drop zone's words and the file picker);
- a plugin can't take .cbz or a type another plugin reads, and what its setup added is taken back then."""
import io
import json
import textwrap
import zipfile

import cv2
import numpy as np

from helpers import App, check, data_folder, done, scratch

data = data_folder()
plugin = data / "plugins" / "file-types"
plugin.mkdir(parents=True)
(plugin / "plugin.json").write_text('{"id": "file-types", "name": "File types", "version": "1", "api": 1}', encoding="utf-8")
(plugin / "__init__.py").write_text(textwrap.dedent('''
    import json
    import zipfile


    def chapters(path):
        with zipfile.ZipFile(path) as z:
            parts = json.loads(z.read("parts.json"))
        if isinstance(parts, dict) and parts.get("error"):
            raise ValueError(parts["error"])
        if isinstance(parts, dict):
            raise RuntimeError("crashed on purpose")
        return parts


    def pages(path, wanted):
        with zipfile.ZipFile(path) as z:
            for name in wanted:
                yield name, z.read(name)


    def setup(plugin):
        plugin.file_type(".twopart", "Two-part", chapters, pages)
    '''), encoding="utf-8")
for pid, name, code in [("file-types-taken", "File types, taken", 'plugin.file_type(".cbz", "Mine", None, None)'),
                        ("file-types-twice", "File types, twice",
                         'plugin.project_setting("left_over", "x")\n    plugin.file_type(".twopart", "Again", None, None)')]:
    (data / "plugins" / pid).mkdir()
    (data / "plugins" / pid / "plugin.json").write_text(json.dumps({"id": pid, "name": name, "version": "1", "api": 1}),
                                                       encoding="utf-8")
    (data / "plugins" / pid / "__init__.py").write_text(f"def setup(plugin):\n    {code}\n", encoding="utf-8")


def picture(w, h, colour):
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = colour
    return cv2.imencode(".png", img)[1].tobytes()


def twopart(parts, pictures=()) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("parts.json", json.dumps(parts))
        for name, data in pictures:
            z.writestr(name, data)
    return out.getvalue()


VOL1 = twopart([{"name": "Prologue", "pages": ["p1.png", "p2.png"]},
                {"name": "Chapter 1", "pages": ["p3.png", "p4.png", "p5.png"]},
                {"name": "Side A/B", "pages": ["p6.png"]}],
               [(f"p{i}.png", picture(200 + i, 300, (0, 40 * i, 0))) for i in range(1, 7)])
VOL2 = twopart([{"name": "Whatever it's called", "pages": ["q1.png", "q2.png"]}],
               [(f"q{i}.png", picture(150, 220, (90, 0, 0))) for i in (1, 2)])
BROKEN = twopart({"error": "it's broken on purpose."})
comics = scratch("comics")
(comics / "Vol 1.twopart").write_bytes(VOL1)
(comics / "Vol 2.twopart").write_bytes(VOL2)
(comics / "broken.twopart").write_bytes(BROKEN)
(comics / "crash.twopart").write_bytes(twopart({"crash": True}))
(comics / "empty.twopart").write_bytes(twopart([]))
(comics / "Extras").mkdir()
(comics / "Extras" / "1.png").write_bytes(picture(100, 100, (0, 0, 200)))

app = App(data)
store, plugins = app.store, app.plugins
from recap import importer  # noqa: E402

check("the plugin loads", plugins.loaded["file-types"].get("error") is None, plugins.loaded["file-types"])
check(".cbz can't be taken", "isn't a file type a plugin can add" in (plugins.loaded["file-types-taken"].get("error") or ""),
      plugins.loaded["file-types-taken"])
check("nor a type another plugin reads", "Another plugin reads .twopart" in (plugins.loaded["file-types-twice"].get("error") or ""),
      plugins.loaded["file-types-twice"])
check("and what its setup added is taken back", "file-types-twice.left_over" not in store.DEFAULT_SETTINGS
      and list(importer.FILE_TYPES) == [".twopart"] and importer.FILE_TYPES[".twopart"]["name"] == "Two-part")
check("the page is told the types", app.get("/api/plugins").json()["file_types"] == [{"ext": ".twopart", "name": "Two-part"}])

pid = app.post("/api/projects", json={"name": "File types", "settings": store.new_project_settings()}).json()["id"]
r = app.post(f"/api/projects/{pid}/scan-folder", json={"path": str(comics)}).json()
names = [c["name"] for c in r["chapters"]]
check("a file's parts: a chapter each, named after them, in the file's order, among the folders",
      names == ["Extras", "Prologue", "Chapter 1", "Side A-B", "Vol 2"], names)
check("its pages counted", [c["pages"] for c in r["chapters"]] == [1, 2, 3, 1, 2], r["chapters"])
check("tagged with the type's name", [c.get("type") for c in r["chapters"]] == [None, "Two-part", "Two-part", "Two-part", "Two-part"]
      and r["chapters"][1]["kind"] == "file")
skipped = {x["file"]: x["reason"] for x in r["skipped"]}
check("the files it can't read are listed, with why", skipped == {
    "broken.twopart": "it's broken on purpose.", "crash.twopart": "it couldn't be read (RuntimeError)",
    "empty.twopart": "no pages found in it"}, r["skipped"])

r = app.post(f"/api/projects/{pid}/import-folder", json={"path": str(comics), "keys": None}).json()
check("imported", r["chapters"] == 5 and r["pages"] == 9, r)
p = store.load(pid)
chapters = {c["name"]: c for c in p["chapters"]}
check("each part is a source of its own", chapters["Prologue"]["source"] == str(comics / "Vol 1.twopart") + "#1"
      and chapters["Side A-B"]["source"] == str(comics / "Vol 1.twopart") + "#3"
      and chapters["Vol 2"]["source"] == str(comics / "Vol 2.twopart"), [c["source"] for c in p["chapters"]])
pages = [x for x in p["pages"] if x["chapter"] == chapters["Chapter 1"]["id"]]
check("its pages extracted, in order", [(x["name"], x["w"], x["h"]) for x in pages]
      == [("p3.png", 203, 300), ("p4.png", 204, 300), ("p5.png", 205, 300)]
      and all(store.page_path(p, x).exists() for x in pages), pages)
r = app.post(f"/api/projects/{pid}/scan-folder", json={"path": str(comics)}).json()
check("and they're all already imported next time", all(c["exists"] for c in r["chapters"]), r["chapters"])

drop = app.post("/api/projects", json={"name": "Dropped", "settings": store.new_project_settings()}).json()["id"]
r = app.post(f"/api/projects/{drop}/pages", files=[("files", ("Vol 1.twopart", VOL1)), ("files", ("broken.twopart", BROKEN))]).json()
check("dropped: a chapter for each part", r["added"] == 6
      and [c["name"] for c in store.load(drop)["chapters"]] == ["Prologue", "Chapter 1", "Side A-B"], r)
check("and what's left out, with why", r["skipped"] == [{"file": "broken.twopart", "reason": "it's broken on purpose."}], r)
r = app.post(f"/api/projects/{drop}/pages", files=[("files", ("Vol 2.twopart", VOL2))]).json()
check("a file of one part: named after it", r["added"] == 2 and "Vol 2" in [c["name"] for c in store.load(drop)["chapters"]], r)
done()
