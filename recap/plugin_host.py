"""Runs one source plugin (sources.py) in a process of its own, so a plugin that hangs, crashes or prints can't take
the app with it. Started with the app's own Python in isolated mode (-I: nothing from the current folder, the user's
site-packages or PYTHONPATH), so a plugin has the standard library, the app's packages (requests) and the plugins
folder (for helpers shared by several plugins).

One JSON message a line. In: {"call": name, "args": [...], "passes": [...]}. Out: {"ok": true, "result": ...} or
{"ok": false, "error": "...", "trace": "..."}, with "check": {"host", "url"} when a site answered with Cloudflare's
check for a person instead of its page. What the plugin prints goes to stderr (the plugin's log), never into the
answers.

Passes (sources.set_pass): what the member got by passing a site's check in a window, [{"domain", "cookies",
"user_agent"}]. The plugin's requests (through requests, as plugins make them) to that site carry its cookies and
that window's browser name, which the site's pass is tied to; plugins don't have to know.

    python -I -X utf8 plugin_host.py <plugin file>
"""
import importlib.util
import json
import sys
import traceback
from pathlib import Path
from urllib.parse import urlparse

CALLS = {"search", "browse", "series", "pages", "characters"}
_passes: list = []  # this call's passes
_checks: list = []  # the checks met during this call


def on_site(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_check(r) -> bool:
    """Whether a response is Cloudflare's check for a person (a page that runs a script in a browser), rather than
    the site's page or an ordinary error."""
    if r.headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    if r.status_code not in (403, 429, 503) or "cloudflare" not in r.headers.get("Server", "").lower():
        return False
    try:
        head = r.content[:20000].decode("utf-8", "replace")
    except Exception:
        return False
    return "/cdn-cgi/challenge-platform/" in head or "<title>Just a moment...</title>" in head


def _watch_requests() -> None:
    """Every request a plugin makes through requests: a site's pass added, and its checks noted."""
    try:
        import requests
        from requests.cookies import RequestsCookieJar, create_cookie, merge_cookies
    except ImportError:
        return
    original = requests.Session.request

    def request(self, method, url, *args, **kw):
        host = (urlparse(url).hostname or "").lower()
        mine = next((p for p in _passes if on_site(host, p["domain"])), None)
        if mine:
            headers = {k: v for k, v in (kw.get("headers") or {}).items() if k.lower() != "user-agent"}
            kw["headers"] = {**headers, "User-Agent": mine["user_agent"]}
            jar = merge_cookies(RequestsCookieJar(), kw.get("cookies") or {})
            for name, value in (mine.get("cookies") or {}).items():  # (for that site only, redirects too)
                jar.set_cookie(create_cookie(name, value, domain="." + mine["domain"]))
            kw["cookies"] = jar
        r = original(self, method, url, *args, **kw)
        if is_check(r):
            _checks.append({"host": (urlparse(r.url).hostname or host).lower(), "url": r.url})
        return r

    requests.Session.request = request


def main():
    path = Path(sys.argv[1])
    answers = sys.stdout
    sys.stdout = sys.stderr
    sys.path.insert(0, str(path.parent))
    _watch_requests()
    module, failed = None, None
    try:
        spec = importlib.util.spec_from_file_location(f"recap_plugin_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except BaseException as e:  # reported with every call, so the app can show it
        failed = (f"The plugin didn't load: {type(e).__name__}: {e}", traceback.format_exc()[-4000:])
    for line in sys.stdin:
        _checks.clear()
        try:
            msg = json.loads(line)
            _passes[:] = [p for p in msg.get("passes") or [] if isinstance(p, dict) and p.get("domain")]
            if failed:
                answers.write(json.dumps({"ok": False, "error": failed[0], "trace": failed[1]}) + "\n")
                answers.flush()
                continue
            name = msg["call"]
            fn = getattr(module, name, None) if name in CALLS else None
            if not callable(fn):
                raise AttributeError(f"The plugin has no {name}()")
            reply = {"ok": True, "result": fn(*msg.get("args", []))}
        except BaseException as e:
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-4000:]}
        if _checks:
            reply["check"] = _checks[0]
        try:
            text = json.dumps(reply, ensure_ascii=False, default=str)
        except (TypeError, ValueError) as e:
            text = json.dumps({"ok": False, "error": f"The plugin's answer isn't JSON: {e}"})
        answers.write(text + "\n")
        answers.flush()


if __name__ == "__main__":
    main()
