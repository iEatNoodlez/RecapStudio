"""Sources end to end, on a fresh test data folder (RECAP_DATA), against the made-up site (fakesite.py) on this PC,
with the app's server in its own process: plugins added from a file and from a plugin list, a link, search, lists,
covers, getting chapters into a project, new chapters, skipping, a cancelled and a failed download, and plugins
that are broken, hang or crash. No real site is visited.

    python tests/run.py sources"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from helpers import APP, free_port, scratch, tidy

HERE = Path(__file__).parent
PY = sys.executable
DATA = scratch("sources")
REPO = scratch("sources-list")  # the made-up site's plugin list
SITE, PORT = free_port(), free_port()
SITE_URL, BASE = f"http://127.0.0.1:{SITE}", f"http://127.0.0.1:{PORT}"
failures = []


def check(ok, what):
    print(("  ok   " if ok else "  FAIL ") + what, flush=True)
    if not ok:
        failures.append(what)


def api(path, method="GET", body=None, raw=False, files=None):
    data, headers = None, {}
    if files:
        boundary = "----recaptest"
        name, content = files
        data = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                f"Content-Type: text/x-python\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data, headers = json.dumps(body).encode(), {"Content-Type": "application/json"}
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            content = r.read()
            return (r.status, content, r.headers.get("Content-Type")) if raw else json.loads(content or b"null")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        if raw:
            return e.code, detail, None
        return {"http": e.code, "detail": json.loads(detail).get("detail") if detail.startswith("{") else detail}


def wait_job(pid, timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        job = api(f"/api/projects/{pid}")["job"]
        if job and job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.3)
    return None


def plugin_text(base=SITE_URL, extra=""):
    text = (APP / "docs" / "plugin-example.py").read_text(encoding="utf-8")
    text = text.replace('BASE = "https://example.com"', f'BASE = "{base}"')
    text = text.replace('SITES = ["example.com"]', 'SITES = ["127.0.0.1"]')
    text = text.replace('HEADERS = {"Referer": "https://example.com/"}', f'HEADERS = {{"Referer": "{base}/"}}')
    return text + extra


def main():
    shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(parents=True)
    (DATA / "settings.json").write_text(json.dumps({"setup_done": True}), encoding="utf-8")
    env = {**os.environ, "RECAP_DATA": str(DATA)}
    log = open(DATA / "server-output.log", "w")  # (what the app printed, kept with the rest when a check fails)
    procs = [subprocess.Popen([PY, str(HERE / "fakesite.py"), str(SITE), str(REPO)]),
             subprocess.Popen([PY, "-m", "recap", "--no-window", "--port", str(PORT)], env=env, cwd=str(APP),
                              stdout=log, stderr=subprocess.STDOUT)]
    try:
        for _ in range(80):
            try:
                urllib.request.urlopen(BASE + "/api/sources", timeout=2)
                urllib.request.urlopen(SITE_URL + "/series/red-river", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        run()
    finally:
        for p in procs:  # (with what they started: the venv's python.exe runs the real one as its child)
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
            else:
                p.terminate()
        for p in procs:
            p.wait(10)
        log.close()
    print("\nALL PASS" if not failures else f"\n{len(failures)} FAILED: {failures}")
    tidy(bool(failures))


def run():
    print("1. nothing set up")
    st = api("/api/sources")
    check(st["sources"] == [] or all(s["kind"] != "plugin" for s in st["sources"]), "no plugins at first")
    check(st["suwayomi"]["ok"] is False, f"Suwayomi not connected: {st['suwayomi'].get('error')}")
    check("found" in st["gallery_dl"], f"gallery-dl status: {st['gallery_dl']}")

    print("2. adding plugins from a file")
    r = api("/api/sources/plugins", "POST", files=("example.py", plugin_text().encode()))
    check(r.get("id") == "example" and r.get("name") == "Example Comics", f"added: {r.get('id')} {r.get('name')}")
    r = api("/api/sources/plugins", "POST", files=("notes.py", b"print('hi')\n"))
    check(r.get("http") == 400 and "isn't a Recap Studio plugin" in r["detail"], f"a plain file is refused: {r.get('detail')}")
    r = api("/api/sources/plugins", "POST", files=("half.py", b"RECAP_PLUGIN = 1\nNAME = 'Half'\ndef series(u): pass\n"))
    check(r.get("http") == 400 and "pages()" in r["detail"], f"one without pages() is refused: {r.get('detail')}")
    r = api("/api/sources/plugins", "POST", files=("future.py", b"RECAP_PLUGIN = 2\nNAME='F'\ndef series(u): pass\ndef pages(u): pass\n"))
    check(r.get("http") == 400 and "version 2" in r["detail"], f"a newer interface is refused: {r.get('detail')}")
    st = api("/api/sources")
    keys = [s["key"] for s in st["sources"]]
    check("plugin:example" in keys and len(st["plugins"]) == 1, f"listed: {keys}")
    ex = next(s for s in st["sources"] if s["key"] == "plugin:example")
    check(ex["search"] and ex["lists"] == [{"id": "popular", "name": "Popular"}] and ex["sites"] == ["127.0.0.1"],
          "its search, lists and sites read from the file")

    print("3. a link, search, lists, covers")
    link = f"{SITE_URL}/series/moon-tower"
    check(api(f"/api/sources/link?url={link}")["key"] == "plugin:example", "a link on its site opens with it")
    r = api("/api/sources/link?url=https://elsewhere.test/x")
    check(r.get("key") in (None, "gallery-dl"), f"a link elsewhere: {r}")
    r = api("/api/sources/search", "POST", {"key": "plugin:example", "query": "moon", "page": 1})
    titles = [x["title"] for x in r.get("items", [])]
    check(titles == ["The Moon Tower", "Moonlight Cafe"] and r["more"] is False, f"search: {titles}")
    status, data, kind = api(r["items"][0]["cover"], raw=True)
    check(status == 200 and data[:4] == b"\x89PNG", f"its cover through the app, with the plugin's Referer ({status})")
    p1 = api("/api/sources/browse", "POST", {"key": "plugin:example", "list": "popular", "page": 1})
    p2 = api("/api/sources/browse", "POST", {"key": "plugin:example", "list": "popular", "page": 2})
    check(len(p1["items"]) == 2 and p1["more"] and len(p2["items"]) == 1 and not p2["more"],
          f"the popular list a page at a time: {len(p1['items'])} + {len(p2['items'])}")
    r = api("/api/sources/browse", "POST", {"key": "plugin:example", "list": "nope", "page": 1})
    check(r.get("http") == 400, f"an unknown list: {r.get('detail')}")
    s = api("/api/sources/series", "POST", {"key": "plugin:example", "ref": link})
    names = [c["name"] for c in s["chapters"]]
    check(s["title"] == "The Moon Tower" and names == ["Chapter 1", "Chapter 2", "Chapter 3", "Chapter 3.5", "Chapter 4"],
          f"the series, first chapter first: {names}")

    print("4. getting chapters into a project")
    proj = api("/api/projects", "POST", {"name": "Untitled"})
    pid = proj["id"]
    first3 = s["chapters"][:3]
    job = api(f"/api/projects/{pid}/download", "POST", {"key": "plugin:example", "series": s, "chapters": first3})
    check(job.get("step") is None or job.get("status") in ("queued", "running"), "a job started")
    job = wait_job(pid)
    check(job and job["status"] == "done", f"done: {job and (job['message'], job.get('error'))}")
    p = api(f"/api/projects/{pid}")
    pages = [x for x in p["pages"]]
    check([c["name"] for c in p["chapters"]] == ["Chapter 1", "Chapter 2", "Chapter 3"], "3 chapters, in order")
    check(len(pages) == 9 and all((DATA / "projects").glob(f"*/pages/{x['file']}") for x in pages), "9 pages, in the project")
    check([(x["w"], x["h"]) for x in pages[:3]] == [(720, 1300), (720, 1400), (720, 1500)], "their sizes")
    gif = next(x for x in pages if x["chapter"] == p["chapters"][1]["id"] and x["name"].startswith("003"))
    check(gif["file"].endswith(".png"), f"a GIF page saved as PNG: {gif['file']}")
    check(p["name"] == "The Moon Tower", f"the project took the series' title: {p['name']}")
    check(p["source"]["key"] == "plugin:example" and p["source"]["ref"] == link, "it remembers the series")

    print("5. new chapters, skipping, a chapter added at the site")
    r = api(f"/api/projects/{pid}/source/new")
    new = [c["name"] for c in r["series"]["chapters"]]
    check(new == ["Chapter 3.5", "Chapter 4"], f"new: {new}")
    check(api("/api/sources/new").get(pid) == 2, "counted for the sidebar")
    ch4 = [c for c in r["series"]["chapters"] if c["name"] == "Chapter 4"]
    skip = [c["ref"] for c in r["series"]["chapters"] if c["name"] == "Chapter 3.5"]
    api(f"/api/projects/{pid}/download", "POST", {"key": r["key"], "series": r["series"], "chapters": ch4, "skip": skip})
    job = wait_job(pid)
    p = api(f"/api/projects/{pid}")
    check(job["status"] == "done" and [c["name"] for c in p["chapters"]][-1] == "Chapter 4", "chapter 4 added at the end")
    check(api(f"/api/projects/{pid}/source/new")["series"]["chapters"] == [], "nothing new (3.5 passed over)")
    urllib.request.urlopen(f"{SITE_URL}/admin/add?slug=moon-tower&n=5")
    r = api(f"/api/projects/{pid}/source/new")
    check([c["name"] for c in r["series"]["chapters"]] == ["Chapter 5"], "a chapter the site added is new")
    again = api(f"/api/projects/{pid}/download", "POST", {"key": r["key"], "series": r["series"],
                                                          "chapters": [s["chapters"][0]]})
    wait_job(pid)
    check(len(api(f"/api/projects/{pid}")["chapters"]) == 4, "a chapter the project has isn't got twice")

    print("6. a site that sends an error page instead of pictures")
    urllib.request.urlopen(f"{SITE_URL}/admin/broken?on=1")
    api(f"/api/projects/{pid}/download", "POST", {"key": r["key"], "series": r["series"], "chapters": r["series"]["chapters"]})
    job = wait_job(pid)
    check(job["status"] == "error" and "wasn't a picture" in (job.get("error") or ""), f"failed: {job.get('error')}")
    check(len(api(f"/api/projects/{pid}")["chapters"]) == 4, "no half chapter added")
    urllib.request.urlopen(f"{SITE_URL}/admin/broken?on=0")
    api(f"/api/projects/{pid}/download", "POST", {"key": r["key"], "series": r["series"], "chapters": r["series"]["chapters"]})
    job = wait_job(pid)
    check(job["status"] == "done" and len(api(f"/api/projects/{pid}")["chapters"]) == 5, "run again, it's added")
    check(not any((DATA / "projects").glob("*/downloading")), "nothing left in the download folder")

    print("7. a plugin list: all its plugins installed, adult ones off, kept up to date")
    good = plugin_text().replace('NAME = "Example Comics"', 'NAME = "Listed Comics"').replace('VERSION = "1.0.0"', 'VERSION = "2.0.0"')
    adult = plugin_text().replace('NAME = "Example Comics"', 'NAME = "Adult Comics"').replace("NSFW = False", "NSFW = True")
    (REPO / "listed.py").write_text(good, encoding="utf-8", newline="\n")
    (REPO / "adult.py").write_text(adult, encoding="utf-8", newline="\n")
    (REPO / "bad.py").write_text(good + "\n# changed after the list was made\n", encoding="utf-8", newline="\n")
    sha = lambda name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
    index = {"name": "Test list", "plugins": [
        {"id": "listed", "name": "Listed Comics", "version": "2.0.0", "file": "listed.py", "sha256": sha("listed.py"),
         "sites": ["127.0.0.1"], "language": "en", "summary": "From a list"},
        {"id": "adult", "name": "Adult Comics", "version": "1.0.0", "file": "adult.py", "sha256": sha("adult.py"), "nsfw": True},
        {"id": "example", "name": "Example Comics", "version": "9.0.0", "file": "listed.py", "sha256": sha("listed.py")},
        {"id": "bad", "name": "Bad", "version": "1", "file": "bad.py", "sha256": sha("listed.py")}]}
    (REPO / "index.json").write_text(json.dumps(index), encoding="utf-8")
    repo = f"{SITE_URL}/repo/index.json"
    r = api("/api/sources/repos", "POST", {"url": f"{SITE_URL}/series/red-river"})
    check(r.get("http") == 400, f"a page that isn't a list is refused: {r.get('detail')}")
    r = api("/api/sources/repos", "POST", {"url": repo})
    synced = r.get("synced") or {}
    check(r.get("name") == "Test list" and [x["id"] for x in r["plugins"]] == ["listed", "adult", "example", "bad"], "the list added")
    check(synced.get("installed") == ["Listed Comics", "Adult Comics"], f"adding it installs its plugins: {synced}")
    check(synced.get("off") == ["Adult Comics"], "the adult one switched off")
    check(len(synced.get("failed", [])) == 1 and "checksum" in synced["failed"][0], f"one that doesn't match its checksum isn't: {synced.get('failed')}")
    listed = {x["id"]: x for x in r["plugins"]}
    check(listed["example"]["elsewhere"] and listed["example"]["installed"] == "1.0.0",
          "a plugin of the member's own by the same id is left alone (not replaced by the list's)")
    st = api("/api/sources")
    plugins = {x["id"]: x for x in st["plugins"]}
    check(st["repos"] == [repo] and st["lists"][repo]["name"] == "Test list", "kept in settings, with when it was checked")
    check(plugins["listed"]["repo"] == repo and plugins["listed"]["enabled"] and not plugins["adult"]["enabled"],
          "each remembers its list; the listed one is on, the adult one off")
    check(plugins["example"]["repo"] is None and plugins["example"]["version"] == "1.0.0", "the member's own is untouched")
    r = api("/api/sources/repos/install", "POST", {"url": repo, "id": "bad"})
    check(r.get("http") == 400 and "checksum" in r["detail"], f"installing it by hand is refused too: {r.get('detail')}")

    # removed by the member: not put back by the list, until installed again
    api("/api/sources/plugins/listed", "DELETE")
    r = api("/api/sources/repos/sync", "POST", {"url": repo})
    check("listed" not in [x["id"] for x in api("/api/sources")["plugins"]] and r["synced"]["installed"] == [],
          "a plugin the member removed isn't put back when the list is checked")
    check({x["id"]: x for x in r["plugins"]}["listed"]["left_out"], "the list shows it as removed")
    r = api("/api/sources/repos/install", "POST", {"url": repo, "id": "listed"})
    check(r.get("name") == "Listed Comics", "Install puts it back")
    r = api("/api/sources/repos/sync", "POST", {"url": repo})
    check(not {x["id"]: x for x in r["plugins"]}["listed"]["left_out"], "and it's no longer left out")

    # a new version: got when the list is checked, each plugin keeping whether it's on
    api("/api/sources/plugins/listed", "PUT", {"enabled": False})
    (REPO / "listed.py").write_text(good.replace('VERSION = "2.0.0"', 'VERSION = "2.1.0"'), encoding="utf-8", newline="\n")
    index["plugins"][0].update(version="2.1.0", sha256=sha("listed.py"))
    newer = plugin_text().replace('NAME = "Example Comics"', 'NAME = "Newer Comics"')
    (REPO / "newer.py").write_text(newer, encoding="utf-8", newline="\n")
    index["plugins"].append({"id": "newer", "name": "Newer Comics", "version": "1.0.0", "file": "newer.py", "sha256": sha("newer.py")})
    (REPO / "index.json").write_text(json.dumps(index), encoding="utf-8")
    r = api("/api/sources/repos/sync", "POST", {"url": repo})
    check(r["synced"]["updated"] == ["Listed Comics"] and r["synced"]["installed"] == ["Newer Comics"],
          f"checking the list gets the new version and the new plugin: {r['synced']}")
    plugins = {x["id"]: x for x in api("/api/sources")["plugins"]}
    check(plugins["listed"]["version"] == "2.1.0" and not plugins["listed"]["enabled"], "updated, and still off")
    check(plugins["example"]["version"] == "1.0.0", "the member's own still untouched")

    # several at once (a list's All on)
    r = api("/api/sources/plugins", "PUT", {"ids": ["listed", "adult", "newer"], "enabled": True})
    check(all(x["enabled"] for x in r["plugins"] if x["id"] in ("listed", "adult", "newer")), "All on switches them all on")
    r = api("/api/sources/plugins", "PUT", {"ids": ["adult", "newer"], "enabled": False})
    on = {x["id"]: x["enabled"] for x in r["plugins"]}
    check(on["listed"] and not on["adult"] and not on["newer"], "and off again, just those")

    # a list removed with its plugins, and one whose plugins stay
    (REPO / "second.json").write_text(json.dumps({"name": "Second list", "plugins": [
        {"id": "second", "name": "Second Comics", "version": "1", "file": "newer.py", "sha256": sha("newer.py")}]}), encoding="utf-8")
    second = f"{SITE_URL}/repo/second.json"
    r = api("/api/sources/repos", "POST", {"url": second})
    check(r["synced"]["installed"] == ["Second Comics"], "a second list's plugin installed")
    r = api("/api/sources/repos", "DELETE", {"url": second, "plugins": True})
    ids = [x["id"] for x in api("/api/sources")["plugins"]]
    check(r["removed"] == ["second"] and "second" not in ids and "listed" in ids, "removing a list with its plugins takes only its own")
    api("/api/sources/repos", "DELETE", {"url": repo})
    st = api("/api/sources")
    check(st["repos"] == [] and "listed" in [x["id"] for x in st["plugins"]], "removing one and keeping its plugins keeps them")
    for pid in ("adult", "newer"):
        api(f"/api/sources/plugins/{pid}", "DELETE")

    print("8. switching off and removing")
    api("/api/sources/plugins/listed", "PUT", {"enabled": False})
    check("plugin:listed" not in [s["key"] for s in api("/api/sources")["sources"]], "switched off: not offered")
    api("/api/sources/plugins/listed", "DELETE")
    check("listed" not in [x["id"] for x in api("/api/sources")["plugins"]], "removed")

    print("9. plugins that print, crash or hang")
    loud = plugin_text(extra="\nprint('loading, loudly')\n_old = series\ndef series(url):\n    print('fetching', url)\n    return _old(url)\n")
    api("/api/sources/plugins", "POST", files=("loud.py", loud.encode()))
    s2 = api("/api/sources/series", "POST", {"key": "plugin:loud", "ref": f"{SITE_URL}/series/red-river"})
    check(s2.get("title") == "Red River Days", "a plugin that prints still answers")
    log = (DATA / "plugins" / "loud.log").read_text(encoding="utf-8")
    check("fetching" in log, "what it printed is in its log")
    crash = plugin_text(extra="\nimport os\ndef series(url):\n    os._exit(3)\n")
    api("/api/sources/plugins", "POST", files=("crash.py", crash.encode()))
    r = api("/api/sources/series", "POST", {"key": "plugin:crash", "ref": f"{SITE_URL}/series/red-river"})
    check(r.get("http") == 400 and "stopped" in r["detail"], f"a crash is reported: {r.get('detail')}")
    r = api("/api/sources/search", "POST", {"key": "plugin:crash", "query": "red", "page": 1})
    check(r.get("items") and r["items"][0]["title"] == "Red River Days", "and the plugin starts again for the next call")
    oops = plugin_text(extra="\ndef series(url):\n    raise ValueError('That page needs a login')\n")
    api("/api/sources/plugins", "POST", files=("oops.py", oops.encode()))
    r = api("/api/sources/series", "POST", {"key": "plugin:oops", "ref": "x"})
    check(r.get("http") == 400 and "needs a login" in r["detail"], f"its own message is shown: {r.get('detail')}")
    broken = b"RECAP_PLUGIN = 1\nNAME = 'Broken'\nimport no_such_module\ndef series(u): pass\ndef pages(u): pass\n"
    api("/api/sources/plugins", "POST", files=("broken.py", broken))
    r = api("/api/sources/series", "POST", {"key": "plugin:broken", "ref": "x"})
    check(r.get("http") == 400 and "didn't load" in r["detail"], f"one that doesn't load: {r.get('detail')}")

    print("10. a call that hangs is stopped (in this process, with a short limit)")
    os.environ["RECAP_DATA"] = str(DATA)
    sys.path.insert(0, str(APP))
    from recap import sources
    (DATA / "plugins" / "hang.py").write_text(plugin_text(extra="\nimport time\ndef series(url):\n    time.sleep(60)\n"),
                                              encoding="utf-8")
    t = time.time()
    try:
        sources._host("hang").call("series", "x", timeout=3)
        check(False, "a hanging call is stopped")
    except sources.SourceError as e:
        check(time.time() - t < 10 and "was stopped" in str(e), f"a hanging call is stopped: {e}")
    s3 = sources._host("hang").call("search", "moon", 1, timeout=20)
    check(len(s3["items"]) == 2, "and it starts again")
    import threading
    hung = {}

    def busy():
        try:
            sources._host("hang").call("series", "x", timeout=60)
        except sources.SourceError as e:
            hung["error"] = str(e)
    threading.Thread(target=busy, daemon=True).start()
    time.sleep(1.5)
    t = time.time()
    sources.set_enabled("hang", False)
    check(time.time() - t < 2, f"switching off a plugin in the middle of a call doesn't wait for it ({time.time() - t:.1f} s)")
    deadline = time.time() + 10
    while "error" not in hung and time.time() < deadline:
        time.sleep(0.1)
    check("stopped" in hung.get("error", ""), f"and that call ends: {hung.get('error')}")
    sources.set_enabled("hang", True)
    sources.stop_all()

    # a Suwayomi server that isn't running: asked once, not on every drawing of Settings → Sources
    t = time.time()
    first = sources.suwayomi.status()
    slow = time.time() - t
    t = time.time()
    again = sources.suwayomi.status(), sources.available()
    check(not first["ok"] and time.time() - t < 0.5, f"a Suwayomi that isn't answering isn't asked again straight away "
          f"({slow:.1f} s, then {time.time() - t:.2f} s)")

    print("11. a site's check for a person (the made-up site's stand-in for Cloudflare's)")
    site_check(sources)


def get_pass(ua: str) -> str:
    """The made-up site's pass (cf_clearance), as a browser named ua gets it when its check's script runs."""
    import http.client
    c = http.client.HTTPConnection("127.0.0.1", SITE, timeout=10)
    c.request("GET", "/cdn-cgi/pass?to=/", headers={"User-Agent": ua})
    r = c.getresponse()
    cookies = [v.split(";")[0].split("=", 1) for k, v in r.getheaders() if k.lower() == "set-cookie"]
    c.close()
    return dict(cookies)["cf_clearance"]


def site_check(sources):
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
    urllib.request.urlopen(f"{SITE_URL}/admin/guard?on=1&reset=1", timeout=5)
    try:
        # through the app's server: the error, and what tells the page (X-Site-Check)
        req = urllib.request.Request(BASE + "/api/sources/search", method="POST", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"key": "plugin:example", "query": "moon", "page": 1}).encode())
        try:
            urllib.request.urlopen(req, timeout=60)
            check(False, "a site that asks for its check is an error")
        except urllib.error.HTTPError as e:
            detail = json.loads(e.read()).get("detail", "")
            told = json.loads(e.headers.get("X-Site-Check") or "null") or {}
            check(e.code == 400 and "wants to check you're a person" in detail and "Site check plugin" in detail,
                  f"the search says the site wants its check, and which plugin passes it: {detail}")
            check(told.get("host") == "127.0.0.1" and told.get("source") == "Example Comics" and told.get("refused") is False
                  and told.get("url", "").startswith(SITE_URL), f"the page is told the site, its page and the source: {told}")
        r = api("/api/sources/series", "POST", {"key": "plugin:example", "ref": f"{SITE_URL}/series/red-river"})
        check(r.get("http") == 400 and "check" in r["detail"], f"so does a series: {r.get('detail')}")

        # in this process: a pass, as the Site check plugin gives one
        p = sources._plugin("example")
        host = sources._host("example")
        try:
            host.call("search", "moon", 1, source=p)
            check(False, "no pass: the check")
        except sources.SiteCheck as e:
            check(e.check["host"] == "127.0.0.1" and not e.check["refused"], f"no pass: the check ({e})")
        sources.set_pass("127.0.0.1", {"cf_clearance": get_pass(ua)}, ua, time.time() + 600)
        found = host.call("search", "moon", 1, source=p)
        check([x["title"] for x in found["items"]] == ["The Moon Tower", "Moonlight Cafe"],
              "with the pass, the plugin's requests get through (its own browser name replaced by the pass's)")
        s = sources.series("plugin:example", f"{SITE_URL}/series/red-river")
        check(s["title"] == "Red River Days" and len(s["chapters"]) == 2, "a series too")
        folder = DATA / "pass-pictures"
        folder.mkdir(exist_ok=True)
        got = sources._fetch("plugin:example", s["chapters"][0]["ref"], folder, lambda i, n: None, lambda: None)
        check(len(got) == 3 and all(f.exists() for f, w, h in got), f"and the app's picture downloads ({len(got)} pages)")
        data, kind = sources.fetch_image(sources.image_link(f"{SITE_URL}/cover/red-river.png", {"Referer": SITE_URL + "/"})
                                         .rsplit("/", 1)[1])
        check(data[:4] == b"\x89PNG", "and covers")
        check(sources._with_pass("http://elsewhere.test/x", {"A": "1"}) == ({"A": "1"}, None),
              "a pass isn't sent to other sites")
        check(list(sources.site_passes()) == ["127.0.0.1"] and "cookies" not in sources.site_passes()["127.0.0.1"],
              "the passes in use are listed (without their cookies)")

        # passes that don't work
        sources.set_pass("127.0.0.1", {"cf_clearance": get_pass(ua)}, "Some other browser", time.time() + 600)
        try:
            host.call("search", "moon", 1, source=p)
            check(False, "a pass got with another browser name is refused")
        except sources.SiteCheck as e:
            check(e.check["refused"] and "didn't take the pass" in str(e), f"a pass got with another browser name: {e}")
        sources.set_pass("127.0.0.1", {"cf_clearance": get_pass(ua)}, ua, time.time() - 1)
        check(sources._pass_for("127.0.0.1") is None, "a pass that has run out isn't used")
        try:
            host.call("search", "moon", 1, source=p)
            check(False, "and the site asks again")
        except sources.SiteCheck as e:
            check(not e.check["refused"], f"and the site asks again, as for no pass ({e})")
        sources.set_pass("127.0.0.1", {"cf_clearance": get_pass(ua)}, ua, time.time() + 600)
        urllib.request.urlopen(f"{SITE_URL}/admin/guard?on=1&strict=1", timeout=5)
        try:
            host.call("search", "moon", 1, source=p)
            check(False, "a site that looks at more than the pass refuses it")
        except sources.SiteCheck as e:
            check(e.check["refused"], f"a site that looks at more than the pass refuses it: {e}")
        sources.forget_pass("127.0.0.1")
        check(sources.site_passes() == {}, "a pass can be forgotten")
        for bad in (("", {}, ua), ("127.0.0.1", {}, "")):
            try:
                sources.set_pass(*bad)
                check(False, f"a pass without {'a site' if not bad[0] else 'a browser name'} is refused")
            except sources.SourceError:
                check(True, f"a pass without {'a site' if not bad[0] else 'a browser name'} is refused")
    finally:
        urllib.request.urlopen(f"{SITE_URL}/admin/guard?on=0&reset=1", timeout=5)
        sources.stop_all()
    r = api("/api/sources/search", "POST", {"key": "plugin:example", "query": "moon", "page": 1})
    check(len(r.get("items", [])) == 2, "with the check off again, the site answers as before")


if __name__ == "__main__":
    main()
