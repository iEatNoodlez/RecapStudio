"""Server mode (python -m recap --server; auth.py): the app on the network behind a login, the way Sonarr does it.
- Before its login is set up, nothing answers but the login page, and the setup needs the code the server prints;
- every request needs the session cookie or the API key, from this computer too (a reverse proxy here makes every
  visitor look local); Claude Code's /mcp takes the API key;
- wrong passwords from one address make it wait, others still get in; a new login signs every browser out;
- a restart keeps the sessions and starts it as a server again; --reset-login forgets the login, and the next visit
  logs a new setup code;
- under a service that starts it again itself (systemd, or the QNAP package's script: RECAP_SUPERVISED), a restart
  only closes it; a package can say where its setup code is (RECAP_CODE_PLACE);
- on a server: no folders to show, no phone page, no plugin lists added from the browser (without --allow-plugins),
  and a project file can be sent from the browser."""
import json
import os
import re
import subprocess
import sys
import threading
import time

import requests

from helpers import APP, check, data_folder, done, free_port, make_project, until

data = data_folder()
port = free_port()
base = f"http://127.0.0.1:{port}"
env = {**os.environ, "RECAP_DATA": str(data)}
said = []  # what the server prints


def start(at=None, with_env=None, lines=None):
    proc = subprocess.Popen([sys.executable, "-m", "recap", "--server", "--host", "127.0.0.1", "--port", str(at or port)],
                            cwd=str(APP), env=with_env or env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    out = said if lines is None else lines
    threading.Thread(target=lambda: [out.append(line.rstrip()) for line in proc.stdout], daemon=True).start()
    return proc


def answering():
    try:
        return requests.get(base + "/api/auth", timeout=2).ok
    except requests.RequestException:
        return False


def code_said(after: int = 0):
    for line in said[after:]:
        m = re.search(r"setup code[^A-Z0-9]*([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})", line)
        if m:
            return m.group(1)
    return None


def code_said_in(lines):
    for line in lines:
        m = re.search(r"setup code[^A-Z0-9]*([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})", line)
        if m:
            return m.group(1)
    return None


def from_(ip):  # (uvicorn takes the client's address from a proxy on this computer)
    return {"X-Forwarded-For": ip}


server = start()
try:
    check("the server starts", until(answering, 60))
    check("it says where, and the setup code", until(lambda: code_said(), 10) and any(
        f"running as a server at http://127.0.0.1:{port}/" in x for x in said), said)
    code = code_said()

    # ---- before the login is set up ----
    check("the API needs the login", requests.get(base + "/api/status").status_code == 401)
    r = requests.get(base + "/", allow_redirects=False)
    check("the page sends you to the login page", r.status_code == 303 and r.headers["location"] == "/login", r.status_code)
    check("which answers", "Set up Recap Studio" in requests.get(base + "/login").text)
    check("... and says there's no login yet", requests.get(base + "/api/auth").json() ==
          {"server": True, "configured": False, "user": None, "code_place": None})
    r = requests.post(base + "/api/auth/setup", json={"code": "AAAA-BBBB-CCCC", "user": "owner", "password": "a long one"},
                      headers=from_("10.0.0.7"))
    check("setting it up without the code: refused", r.status_code == 403 and "setup code" in r.json()["detail"], r.text)
    r = requests.post(base + "/api/auth/setup", json={"code": code, "user": "owner", "password": "short"})
    check("... with a short password: refused", r.status_code == 400 and "8 characters" in r.json()["detail"], r.text)
    me = requests.Session()
    r = me.post(base + "/api/auth/setup", json={"code": code.lower(), "user": "owner", "password": "a long one"})
    check("set up with the code: signed in", r.ok and r.json()["user"] == "owner" and "recap_session" in me.cookies, r.text)
    cookie = me.cookies.get("recap_session")
    check("... the cookie only for the server's own pages", "httponly" in r.headers["set-cookie"].lower()
          and "samesite=strict" in r.headers["set-cookie"].lower(), r.headers["set-cookie"])
    r = requests.post(base + "/api/auth/setup", json={"code": code, "user": "intruder", "password": "another one"})
    check("set up twice: refused", r.status_code == 403)

    # ---- signed in, and not ----
    st = me.get(base + "/api/status")
    check("signed in: the app answers, as a server", st.ok and st.json()["server"] is True, st.status_code)
    check("this computer isn't trusted without the login", requests.get(base + "/api/projects").status_code == 401)
    key = me.get(base + "/api/server").json()["api_key"]
    check("the API key gets in", requests.get(base + "/api/projects", headers={"X-Api-Key": key}).ok)
    check("... a wrong one doesn't", requests.get(base + "/api/projects", headers={"X-Api-Key": "nope"}).status_code == 401)
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    check("Claude Code's /mcp needs it too", requests.post(base + "/mcp", json=ping).status_code == 401
          and "result" in requests.post(base + "/mcp", json=ping, headers={"X-Api-Key": key}).json())
    check("files need the login", requests.get(base + "/files/x/output/a.mp4").status_code == 401)

    # ---- a project, cheaply (what the page polls while a job runs) ----
    pid = make_project(data, "Moon's Tower")
    panel = sorted((data / "projects" / pid / "panels").iterdir())[0].name
    h = me.head(f"{base}/files/{pid}/panels/{panel}")
    check("a file's headers alone (HEAD), as a browser or a NAS asks before getting it", h.status_code == 200
          and int(h.headers.get("Content-Length", 0)) > 0 and h.content == b"", (h.status_code, dict(h.headers)))
    check("... and the file itself", me.get(f"{base}/files/{pid}/panels/{panel}").status_code == 200)
    r = me.get(f"{base}/api/projects/{pid}")
    tag = r.headers.get("ETag")
    check("a project comes with an ETag, its job included", r.ok and bool(tag) and r.json()["job"] is None
          and r.json()["name"] == "Moon's Tower", (r.status_code, tag))
    check("... sent back, it answers 304 while nothing has changed",
          me.get(f"{base}/api/projects/{pid}", headers={"If-None-Match": tag}).status_code == 304)
    me.patch(f"{base}/api/projects/{pid}", json={"name": "Moon's Tower (renamed)"})
    r = me.get(f"{base}/api/projects/{pid}", headers={"If-None-Match": tag})
    check("... and in full, with a new one, after a change", r.status_code == 200 and r.headers.get("ETag") != tag
          and r.json()["name"] == "Moon's Tower (renamed)", r.status_code)
    me.patch(f"{base}/api/projects/{pid}", json={"name": "Moon's Tower"})
    check("the job alone: none yet", me.get(f"{base}/api/projects/{pid}/job").json() is None)

    # ---- a server's own ways ----
    check("no folder to show", me.post(f"{base}/api/projects/{pid}/show-folder").status_code == 400)
    check("no phone page", me.put(base + "/api/phone", json={"enabled": True}).status_code == 400)
    r = me.post(base + "/api/sources/repos", json={"url": "https://example.com/index.json"})
    check("no plugin lists added from the browser", r.status_code == 403 and "--allow-plugins" in r.json()["detail"], r.text)
    sys.path.insert(0, str(APP))
    os.environ["RECAP_DATA"] = str(data)
    from recap import package  # noqa: E402  (after RECAP_DATA)
    saved = data / "Moon's Tower.recap"
    package.save(pid, saved, lambda *a, **k: None, lambda: False)
    with open(saved, "rb") as f:
        r = me.post(base + "/api/projects/open-file/upload", files={"file": ("Moon's Tower.recap", f)})
    check("a project file sent from the browser opens as a project", r.ok and r.json()["name"].startswith("Moon's Tower")
          and not list((data / "uploads").glob("*")), r.text)

    # ---- passwords ----
    for _ in range(5):
        requests.post(base + "/api/auth/login", json={"user": "owner", "password": "guess"}, headers=from_("10.0.0.9"))
    r = requests.post(base + "/api/auth/login", json={"user": "owner", "password": "a long one"}, headers=from_("10.0.0.9"))
    check("five wrong passwords: that address waits, even with the right one", r.status_code == 429, r.status_code)
    other = requests.Session()
    r = other.post(base + "/api/auth/login", json={"user": "owner", "password": "a long one"}, headers=from_("10.0.0.10"))
    check("... another address still gets in", r.ok and other.get(base + "/api/projects").ok, r.text)
    r = me.post(base + "/api/auth/password", json={"current": "a long one", "user": "owner", "password": "a new password"})
    check("a new login: this browser stays in", r.ok and me.get(base + "/api/projects").ok, r.text)
    check("... every other one is signed out", other.get(base + "/api/projects").status_code == 401)
    check("... and the old password is gone", requests.post(base + "/api/auth/login", json={
        "user": "owner", "password": "a long one"}, headers=from_("10.0.0.11")).status_code == 401)
    r = me.post(base + "/api/auth/logout")
    check("signing out", r.ok and me.get(base + "/api/projects").status_code == 401)
    me.post(base + "/api/auth/login", json={"user": "owner", "password": "a new password"})

    # ---- a restart, and forgetting the login ----
    old = server
    r = me.post(base + "/api/restart", json={"force": True})
    check("a restart", r.ok, r.text)
    check("... the old copy closes", until(lambda: old.poll() is not None, 30))
    check("... the new one is a server, and the session still works", until(answering, 60)
          and me.get(base + "/api/status").json().get("server") is True)
    r = subprocess.run([sys.executable, "-m", "recap", "--reset-login"], cwd=str(APP), env=env, capture_output=True,
                       text=True, timeout=60)
    check("--reset-login", r.returncode == 0 and "forgotten" in r.stdout, r.stdout + r.stderr)
    check("... signs everyone out", me.get(base + "/api/projects").status_code == 401)
    seen = len(said)
    check("... and the next visit sets it up again", requests.get(base + "/api/auth").json()["configured"] is False)
    check("... with a new setup code in the log", until(lambda: code_said(seen) not in (None, code), 10), said[seen:])

    # ---- under a service that starts it again itself ----
    port2, data2, lines2 = free_port(), data_folder(), []
    place = "QTS's event log (QuLog Center), or /share/x/.qpkg/RecapStudio/logs/recap-studio.log"
    base2 = f"http://127.0.0.1:{port2}"
    supervised = start(port2, {**os.environ, "RECAP_DATA": str(data2), "RECAP_SUPERVISED": "1",
                               "RECAP_CODE_PLACE": place}, lines2)
    try:
        def answering2():
            try:
                return requests.get(base2 + "/api/auth", timeout=2).ok
            except requests.RequestException:
                return False
        check("supervised: it starts", until(answering2, 60))
        st = requests.get(base2 + "/api/auth").json()
        check("... the login page says where the package keeps the code", st["code_place"] == place, st)
        r = requests.post(base2 + "/api/auth/setup", json={"code": "AAAA-BBBB-CCCC", "user": "o", "password": "a long one"})
        check("... and so does a wrong code's answer", r.status_code == 403 and place in r.json()["detail"], r.text)
        code2 = until(lambda: code_said_in(lines2), 10)
        s2 = requests.Session()
        s2.post(base2 + "/api/auth/setup", json={"code": code_said_in(lines2), "user": "owner", "password": "a long one"})
        r = s2.post(base2 + "/api/restart", json={"force": True})
        check("... a restart only closes it (the service starts it again)", r.ok and code2
              and until(lambda: supervised.poll() is not None, 30) and not until(answering2, 8), r.text)
    finally:
        if supervised.poll() is None:
            supervised.terminate()
finally:
    server.terminate()
    try:  # (the restarted copy is no child of this test: stopped by its pid)
        pid_now = json.loads((data / "app.json").read_text(encoding="utf-8"))["pid"]
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid_now)], capture_output=True)
        else:
            os.kill(pid_now, 15)
    except (OSError, ValueError, KeyError):
        pass
    time.sleep(1)
done()
