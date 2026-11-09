"""Characters the story hasn't named yet: a scanned group added under its role ("the Director", unnamed), the
narrator told to call them that and never invent a name, the name kept by Save, and a later group's suggestion of
the real name renaming them (the role becomes another name)."""
import json

from helpers import App, check, data_folder, done, make_project

data = data_folder()
pid = make_project(data, "Night Shift", chapters=1, panels=2)
# a scan with two open groups and no pictures to crop (snapshots empty: the add keeps to the record)
d = data / "projects" / pid
p = json.loads((d / "project.json").read_text(encoding="utf-8"))
p["scan"] = {"id": "s1", "chapters": ["c0001"], "seen": 4, "groups": [
    {"id": "g1", "status": "open", "size": 3, "pieces": 3, "chapters": ["Chapter 1"], "snapshots": [], "traits": [],
     "suggestion": {"name": "", "aliases": "Director", "gender": "male", "description": "The older director of the hospital.",
                    "character": None, "same_as": None, "extra": False, "sure": False, "by": "a test"}},
    {"id": "g2", "status": "open", "size": 2, "pieces": 2, "chapters": ["Chapter 1"], "snapshots": [], "traits": []},
    {"id": "g3", "status": "open", "size": 2, "pieces": 2, "chapters": ["Chapter 1"], "snapshots": [], "traits": []},
]}
(d / "project.json").write_text(json.dumps(p), encoding="utf-8")
app = App(data)

# ---- added under the role ----
r = app.post(f"/api/projects/{pid}/scan/groups/g1/add", json={
    "name": "the Director", "gender": "male", "description": "The older director of the hospital.", "aliases": "",
    "unnamed": True})
check("added under the role", r.status_code == 200, r.text[:200])
chars = r.json()["characters"]
who = next((c for c in chars if c["name"] == "the Director"), None)
check("the character is marked as not named yet", who is not None and who.get("unnamed") is True, who)
check("the group is added to them", r.json()["scan"]["groups"][0]["status"] == "added", r.json()["scan"]["groups"][0])

# ---- the narrator's cast list says so ----
from recap import script  # noqa: E402 (after App: the app's import path)
prompt = script.system_prompt({"persona": "You narrate.", "rules": [], "styles": []}, chars)
check("the narrator is told the name isn't known and to use the role",
      "the Director" in prompt and "never make one up" in prompt, prompt[-400:])

# ---- Save keeps the mark; the real name typed takes it off on the page (checked there), Save keeps that too ----
who["description"] = "Runs Seido Hospital at night."
r = app.put(f"/api/projects/{pid}/characters", json={"characters": chars})
saved = next(c for c in r.json()["characters"] if c["id"] == who["id"])
check("Save keeps the mark", saved.get("unnamed") is True, saved)

# ---- a later group: the writer names them; adding it to the character renames them ----
r = app.post(f"/api/projects/{pid}/scan/groups/g2/add", json={
    "character": who["id"], "name": "Kurosawa", "rename": True, "description": "", "aliases": ""})
check("renamed from the role", r.status_code == 200, r.text[:200])
named = next(c for c in r.json()["characters"] if c["id"] == who["id"])
check("the name is the real one now", named["name"] == "Kurosawa", named)
check("the role is among the other names", "the director" in [a.strip().lower() for a in named["aliases"].split(",")], named)
check("the mark is off", named.get("unnamed") is False, named)
check("the description stays", named["description"] == "Runs Seido Hospital at night.", named)

# ---- Add all suggested: an item with a role name and unnamed set makes an unnamed character ----
r = app.post(f"/api/projects/{pid}/scan/add-named", json={"items": [
    {"group": "g3", "name": "the Nurse", "gender": "female", "aliases": "", "description": "The night nurse.", "unnamed": True}]})
nurse = next((c for c in r.json()["characters"] if c["name"] == "the Nurse"), None)
check("Add all suggested takes a role name with the mark", nurse is not None and nurse.get("unnamed") is True, nurse)

done()
