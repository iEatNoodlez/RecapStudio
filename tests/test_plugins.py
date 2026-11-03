"""Feature plugins end to end (plugins.py), with the app as its own process on a scratch data folder and a made-up
"Recap Studio's own list" served on this PC (RECAP_PLUGIN_LIST):
- loading: a plugin's routes, steps, events and page files; a broken one, one for a newer app, one in the wrong folder;
- the list: both kinds shown; another list's feature plugins hidden and refused; installing; a restart asked for;
- after a restart: running; switched off; removed;
- installing a zip: paths that climb out, a bad checksum, a wrong id and a broken file refused; an update replacing
  the old copy, and a bad update keeping it."""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import requests

from helpers import APP, check, data_folder, done, free_port, scratch, until

DOCS_EXAMPLE = APP / "docs" / "plugin-example.py"


def plugin(folder: Path, info: dict, init: str = "", js: str = ""):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.json").write_text(json.dumps(info), encoding="utf-8")
    if init:
        (folder / "__init__.py").write_text(init, encoding="utf-8")
    if js:
        (folder / "web").mkdir(exist_ok=True)
        (folder / "web" / "plugin.js").write_text(js, encoding="utf-8")


def zipped(files: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return b.getvalue()


TEST_INIT = '''
def setup(plugin):
    @plugin.router.get("/hello")
    def hello():
        return {"hi": plugin.name}

    def run(job):
        job["note"] = "test step ran"
    plugin.step("noop", "Testing a plugin step", run)

    def done(job):
        with open(plugin.data_dir / "jobs.txt", "a") as f:
            f.write(job["status"] + "\\n")
    plugin.on("job_done", done)
    plugin.on("project_created", lambda project: (plugin.data_dir / "created.txt").write_text(project["name"]))
'''

data = data_folder()
pdir = data / "plugins"
plugin(pdir / "test-feature", {"id": "test-feature", "name": "Test feature", "version": "1.0", "api": 1}, TEST_INIT,
       "window.__testPlugin = true;")
plugin(pdir / "broken-one", {"id": "broken-one", "name": "Broken", "version": "1", "api": 1},
       "def setup(plugin):\n    raise RuntimeError('on purpose')\n")
plugin(pdir / "too-new", {"id": "too-new", "name": "Too new", "version": "1", "api": 99})
plugin(pdir / "wrong-name", {"id": "something-else", "name": "Wrong", "version": "1", "api": 1})

# ---- the made-up lists: Recap Studio's own (a feature plugin and a source), and someone else's ----
lists = scratch("lists")
own, other = lists / "own", lists / "other"
own.mkdir()
other.mkdir()
hello_zip = zipped({"plugin.json": json.dumps({"id": "hello", "name": "Hello", "version": "2.0", "api": 1}),
                    "web/plugin.js": "window.__hello = 1;"})
(own / "hello-2.0.zip").write_bytes(hello_zip)
example = DOCS_EXAMPLE.read_bytes()
(own / "example.py").write_bytes(example)
(own / "index.json").write_text(json.dumps({"name": "Recap Studio's plugins (test)", "plugins": [
    {"id": "hello", "kind": "feature", "name": "Hello", "version": "2.0", "api": 1, "file": "hello-2.0.zip",
     "sha256": hashlib.sha256(hello_zip).hexdigest()},
    {"id": "example", "kind": "source", "name": "Example Comics", "version": "1.0.0", "file": "example.py",
     "sha256": hashlib.sha256(example).hexdigest(), "sites": ["example.com"]}]}), encoding="utf-8")
(other / "hello.zip").write_bytes(hello_zip)
(other / "other.json").write_text(json.dumps({"name": "Someone's list", "plugins": [
    {"id": "hello", "kind": "feature", "name": "Hello", "version": "9", "api": 1, "file": "hello.zip",
     "sha256": hashlib.sha256(hello_zip).hexdigest()}]}), encoding="utf-8")
LIST_PORT, OTHER_PORT, PORT = free_port(), free_port(), free_port()
OFFICIAL = f"http://127.0.0.1:{LIST_PORT}/index.json"
BASE = f"http://127.0.0.1:{PORT}"
servers = [subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(d)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
           for port, d in ((LIST_PORT, own), (OTHER_PORT, other))]
app_proc = None


def start_app():
    global app_proc
    env = {**os.environ, "RECAP_DATA": str(data), "RECAP_PLUGIN_LIST": OFFICIAL}
    app_proc = subprocess.Popen([sys.executable, "-m", "recap", "--no-window", "--port", str(PORT)], cwd=str(APP), env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    until(lambda: _answers(), 60)


def _answers():
    try:
        return requests.get(BASE + "/api/plugins", timeout=1).ok
    except requests.RequestException:
        return False


def stop_app():
    if app_proc and app_proc.poll() is None:
        app_proc.kill()
        app_proc.wait()


def j(method, path, **kw):
    r = requests.request(method, BASE + path, timeout=60, **kw)
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.content)


try:
    start_app()
    st = j("GET", "/api/plugins")[1]
    by = {p["id"]: p for p in st["plugins"]}
    check("a plugin running, with its page script", by["test-feature"]["running"] and by["test-feature"]["web"], by.get("test-feature"))
    check("a broken one listed with its error", "on purpose" in (by["broken-one"].get("error") or ""), by.get("broken-one"))
    check("one for a newer app refused", "newer Recap Studio" in (by["too-new"].get("error") or ""), by.get("too-new"))
    check("one in a folder not named after it refused", "something-else" in (by["wrong-name"].get("error") or ""))
    check("its list is the test one", st["list"] == OFFICIAL, st["list"])
    check("its route answers", j("GET", "/api/plugins/test-feature/hello") == (200, {"hi": "Test feature"}))
    check("a broken one has no route", j("GET", "/api/plugins/broken-one/hello")[0] == 404)
    check("its page file served", j("GET", "/plugin-web/test-feature/plugin.js")[0] == 200)
    check("a page path can't climb out", j("GET", "/plugin-web/test-feature/..%2Fplugin.json")[0] == 404
          and j("GET", "/plugin-web/test-feature/../__init__.py")[0] == 404)
    code, p = j("POST", "/api/projects", json={"name": "Plugin test"})
    created = data / "plugin-data" / "test-feature" / "created.txt"
    check("project_created heard", code == 200 and until(created.exists, 5) and created.read_text() == "Plugin test")
    code, job = j("POST", f"/api/projects/{p['id']}/run", json={"steps": ["test-feature.noop"]})
    check("its step accepted", code == 200 and job["steps"] == ["test-feature.noop"], job)
    jobs_txt = data / "plugin-data" / "test-feature" / "jobs.txt"
    check("its step ran, job_done heard", until(lambda: jobs_txt.exists() and "done" in jobs_txt.read_text(), 20))

    lst = j("GET", "/api/plugins/list")[1]
    ids = {x["id"]: x for x in lst["plugins"]}
    check("the own list shows both kinds", ids.get("hello", {}).get("kind") == "feature" and ids.get("example", {}).get("kind") == "source"
          and lst["official"], lst)
    code, r = j("GET", f"/api/sources/repo?url=http://127.0.0.1:{OTHER_PORT}/other.json")
    check("another list's feature plugins aren't shown", r.get("plugins") == [], r)
    code, r = j("POST", "/api/sources/repos/install", json={"url": f"http://127.0.0.1:{OTHER_PORT}/other.json", "id": "hello"})
    check("nor installed from it", code == 400 and "own list" in r.get("detail", "") and not (pdir / "hello").exists(), (code, r))
    for pid in ("hello", "example"):
        code, r = j("POST", "/api/plugins/install", json={"id": pid})
        check(f"{pid} installs", code == 200 and r.get("id") == pid, (code, r))
    check("the feature plugin unpacked; the source saved as a file", (pdir / "hello" / "web" / "plugin.js").is_file()
          and (pdir / "example.py").is_file())
    st = j("GET", "/api/plugins")[1]
    hello = next(x for x in st["plugins"] if x["id"] == "hello")
    check("a restart asked for; not running yet", st["restart"] and hello["enabled"] and not hello["running"])
    check("the list says installed", all(x["installed"] and not x["update"] for x in j("GET", "/api/plugins/list")[1]["plugins"]))
    j("PUT", "/api/plugins/test-feature", json={"enabled": False})

    stop_app()
    start_app()
    by = {p["id"]: p for p in j("GET", "/api/plugins")[1]["plugins"]}
    check("after the restart: running", by["hello"]["running"] and by["hello"]["web"], by["hello"])
    check("the one switched off isn't", not by["test-feature"]["running"] and not by["test-feature"]["enabled"])
    check("nor its route", j("GET", "/api/plugins/test-feature/hello")[0] == 404)
    code, r = j("DELETE", "/api/plugins/hello")
    check("an installed one removed", code == 200 and not (pdir / "hello").exists(), (code, r))
    code, r = j("DELETE", "/api/plugins/nothing-here")
    check("removing one that isn't installed is refused", code == 400, (code, r))
finally:
    stop_app()
    for s in servers:
        s.kill()

# ---- installing a zip, in this process (its own data folder) ----
os.environ["RECAP_DATA"] = str(data_folder())
sys.path.insert(0, str(APP))
from recap import plugins  # noqa: E402


def info(version, pid="good"):
    return json.dumps({"id": pid, "name": "Good", "version": version, "api": 1})


def refused(what, data_, pid="good", sha=None):
    try:
        plugins.install_zip(pid, data_, sha or hashlib.sha256(data_).hexdigest())
        check(what, False, "installed")
    except plugins.PluginError as e:
        check(what, True, str(e))


refused("a path climbing out (..)", zipped({"plugin.json": info("1"), "../evil.txt": "x"}))
refused("an absolute path", zipped({"plugin.json": info("1"), "/evil.txt": "x"}))
refused("a drive letter", zipped({"plugin.json": info("1"), "C:/evil.txt": "x"}))
refused("a checksum that doesn't match", zipped({"plugin.json": info("1")}), sha="0" * 64)
refused("a different id than the list's", zipped({"plugin.json": info("1", "other")}))
refused("no plugin.json", zipped({"readme.txt": "x"}))
refused("not a zip", b"hello")
root = plugins.INSTALLED_DIR
check("nothing left behind", not root.exists() or not any(root.iterdir()))
for version, js in (("1.0", "old"), ("1.1", "new")):
    z = zipped({"plugin.json": info(version), "web/plugin.js": js})
    plugins.install_zip("good", z, hashlib.sha256(z).hexdigest())
found = next(e for e in plugins.found() if e["id"] == "good")
check("an update replaces the old copy", found["version"] == "1.1" and (root / "good" / "web" / "plugin.js").read_text() == "new"
      and sorted(p.name for p in root.iterdir()) == ["good"])
refused("a bad update", zipped({"plugin.json": info("2", "other")}))
check("keeps the good copy", (root / "good" / "web" / "plugin.js").read_text() == "new")
shutil.rmtree(data, ignore_errors=True)
done()
