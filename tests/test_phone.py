"""The phone page's share of the app (phone.py): what a linked phone on the home network may call, and what a
project's summary tells it:
- the allow-list: the phone page's own calls, a new project, the character review's actions and its snapshots, the
  sources' search, lists and series and getting chapters; not the app's other calls (settings, plugins, a source
  installed from the phone), not from outside the home network, not without the key;
- the summary of a project whose scan stopped to ask: its open groups biggest first, their suggestions and
  snapshots, the small ones marked, who's named already; its source and the chapters it has;
- a project made the way the phone makes one (the dialog's starting choices plus the kind and language)."""
import os
import sys

from helpers import APP, check, data_folder, done, make_project

data = data_folder()
os.environ["RECAP_DATA"] = str(data)  # (before the app is imported: it reads RECAP_DATA then)
sys.path.insert(0, str(APP))
from recap import phone, store  # noqa: E402

# ---- the allow-list ----
key = phone.settings()["key"]
ok = lambda method, path: phone.check(method, path, "192.168.1.20", key, None)  # noqa: E731
allowed = [("GET", "/phone"), ("GET", "/api/m/projects"), ("GET", "/api/m/projects/0123abcd"),
           ("POST", "/api/projects/0123abcd/run"), ("POST", "/api/jobs/0123abcd/resume"),
           ("GET", "/api/projects/new-settings"), ("POST", "/api/projects"),
           ("GET", "/files/0123abcd/scan/g1_0.jpg"), ("POST", "/api/projects/0123abcd/scan/groups/g1/add"),
           ("POST", "/api/projects/0123abcd/scan/groups/g1/ignore"), ("POST", "/api/projects/0123abcd/scan/add-named"),
           ("GET", "/api/sources"), ("GET", "/api/sources/new"), ("POST", "/api/sources/search"), ("POST", "/api/sources/browse"),
           ("POST", "/api/sources/series"), ("GET", "/api/sources/image/abc123"),
           ("POST", "/api/projects/0123abcd/download"), ("GET", "/api/projects/0123abcd/source/new")]
check("a linked phone may call what its page uses", all(ok(m, p) == "ok" for m, p in allowed),
      [(m, p, ok(m, p)) for m, p in allowed if ok(m, p) != "ok"])
refused = [("GET", "/api/settings"), ("PUT", "/api/settings"), ("POST", "/api/sources/plugins"), ("POST", "/api/sources/repos/install"),
           ("GET", "/api/plugins"), ("DELETE", "/api/projects/0123abcd"), ("POST", "/api/projects/0123abcd/scan/groups/g1/merge"),
           ("GET", "/files/0123abcd/project.json"), ("GET", "/api/projects/0123abcd"), ("GET", "/")]
check("... and nothing else: no settings, no code installed, no deleting", all(ok(m, p) == "not-here" for m, p in refused),
      [(m, p, ok(m, p)) for m, p in refused if ok(m, p) != "not-here"])
check("not from outside the home network", phone.check("GET", "/api/m/projects", "8.8.8.8", key, None) == "not-home")
check("not without the key", phone.check("GET", "/api/m/projects", "192.168.1.20", "wrong", None) == "no-key")

# ---- a project's summary, with a scan stopped to ask ----
pid = make_project(data, "Moon's Tower", chapters=2)
(data / "projects" / pid / "scan").mkdir(exist_ok=True)


def with_scan(p):
    p["characters"] = [{"id": "ch1", "name": "Seyoung", "gender": "female", "aliases": "", "description": "", "looks": []}]
    p["source"] = {"key": "plugin:madeup", "ref": "moon", "url": "https://example.test/moon", "title": "Moon's Tower"}
    p["chapters"][0]["source_ref"] = "moon/1"
    p["scan"] = {"id": "s1", "groups": [
        {"id": "g1", "status": "open", "size": 2, "pieces": 2, "chapters": [p["chapters"][0]["id"]], "snapshots": [{"file": "scan/g1_0.jpg", "piece": "x", "box": [0, 0, 1, 1]}],
         "suggestion": None},
        {"id": "g2", "status": "open", "size": 9, "pieces": 7, "chapters": [c["id"] for c in p["chapters"]],
         "snapshots": [{"file": f"scan/g2_{i}.jpg", "piece": "x", "box": [0, 0, 1, 1]} for i in range(6)],
         "suggestion": {"name": "Jun", "gender": "male", "description": "her coworker", "aliases": "June"}},
        {"id": "g3", "status": "added", "size": 5, "pieces": 5, "chapters": [], "snapshots": [], "suggestion": {"name": "Seyoung"}},
    ]}


store.update(pid, with_scan)
s = phone.summary(store.load(pid), full=True)
check("the review: the open groups, biggest first", [g["id"] for g in s["review"]] == ["g2", "g1"], s["review"])
g2 = s["review"][0]
check("... each with its suggestion, four snapshots at most and whether it's small",
      g2["suggestion"] == {"name": "Jun", "gender": "male", "description": "her coworker", "aliases": "June"}
      and len(g2["snapshots"]) == 4 and g2["snapshots"][0] == "scan/g2_0.jpg" and not g2["small"]
      and s["review"][1]["suggestion"] is None and s["review"][1]["small"], g2)
check("who's named already, for The same as…", s["characters"] == [{"id": "ch1", "name": "Seyoung"}], s["characters"])
check("where its chapters come from, and the ones it has", s["source"]["key"] == "plugin:madeup" and s["have_refs"] == ["moon/1"],
      (s["source"], s["have_refs"]))
check("the list's summary stays small", "review" not in phone.summary(store.load(pid)) and "have_refs" not in phone.summary(store.load(pid)))

# ---- a project made the phone's way ----
settings = {**store.new_project_settings(), "layout": "pages", "reading_direction": "rtl", "language": "ja"}
p = store.create("From the phone", settings)
check("a project from the phone: the kind and language it chose, the rest the dialog's starting choices",
      p["settings"]["layout"] == "pages" and p["settings"]["reading_direction"] == "rtl" and p["settings"]["language"] == "ja"
      and "resolution" in p["settings"], p["settings"])
done()
