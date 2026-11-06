"""Server mode's login, the way Sonarr does it: one username and password on a login page, and an API key for
other programs (Claude Code's MCP server, scripts). Only when Recap Studio runs as a server (--server, or the server
AppImage); the desktop app answers its own computer only and has no login.

Kept in server.json in the data folder (open to its owner only, system.keep_private):
  user, salt, password   the login; the password as scrypt's hash, never itself
  secret                 signs the session cookies; a new one (a new password) signs every browser out
  api_key                for other programs, in the X-Api-Key header
  host, port             where the server listens (Settings → Server; __main__'s --host and --port win)
Before there's a user, the first visit makes one, and has to give the setup code the server writes in its log, so
whoever reaches it first on the network can't take it. --reset-login forgets the user and prints a new code.
Five wrong passwords from one address in ten minutes make it wait. Standard library only."""
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from collections import defaultdict, deque

from . import paths

FILE = paths.DATA / "server.json"
COOKIE = "recap_session"
SESSION_DAYS = 30
DEFAULT_HOST, DEFAULT_PORT = "0.0.0.0", 8780
TRIES, TRY_WINDOW = 5, 600  # wrong passwords from one address before it waits, within this many seconds
_lock = threading.Lock()
_setup_code = None
_failures: dict[str, deque] = defaultdict(deque)


def code_place() -> str:
    """Where the setup code is: the terminal or systemd's journal, or where a package says (the QNAP package: QTS's
    event log and its log file, in RECAP_CODE_PLACE)."""
    return os.environ.get("RECAP_CODE_PLACE") or "journalctl -u recap-studio, or the terminal it was started from"


def load() -> dict:
    try:
        cfg = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    return cfg if isinstance(cfg, dict) else {}


def _save(cfg: dict) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    part = FILE.with_name(FILE.name + ".part")
    part.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    part.replace(FILE)


def address() -> tuple[str, int]:
    cfg = load()
    return str(cfg.get("host") or DEFAULT_HOST), int(cfg.get("port") or DEFAULT_PORT)


def set_address(host: str, port: int) -> None:
    with _lock:
        cfg = load()
        cfg.update(host=host, port=int(port))
        _save(cfg)


def configured() -> bool:
    cfg = load()
    return bool(cfg.get("user") and cfg.get("password"))


def user() -> str:
    return load().get("user") or ""


def setup_code() -> str:
    """The one-time code the first visit needs (made once per run, while there's no user). It's written in the
    server's log when it's made: at the start, or at the first visit after --reset-login."""
    global _setup_code
    if _setup_code is None:
        letters = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # (none that read alike: 0 and O, 1 and I)
        _setup_code = "-".join("".join(secrets.choice(letters) for _ in range(4)) for _ in range(3))
        logging.getLogger("recap").warning("the server's setup code (asked on the first visit): %s", _setup_code)
    return _setup_code


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def _check_new(name: str, password: str) -> tuple[str, str]:
    name = (name or "").strip()
    if not name or len(name) > 64 or any(c.isspace() for c in name):
        raise ValueError("The username needs to be one word, at most 64 characters.")
    if len(password or "") < 8:
        raise ValueError("The password needs at least 8 characters.")
    return name, password


def _set_login(cfg: dict, name: str, password: str) -> None:
    salt = secrets.token_bytes(16)
    cfg.update(user=name, salt=salt.hex(), password=_hash(password, salt), secret=secrets.token_hex(32))
    cfg.setdefault("api_key", secrets.token_hex(16))


def setup(code: str, name: str, password: str) -> None:
    """The first visit: the setup code from the log, then the username and password."""
    global _setup_code
    name, password = _check_new(name, password)
    with _lock:
        cfg = load()
        if cfg.get("user") and cfg.get("password"):
            raise PermissionError("This server has its login already.")
        given = (code or "").replace("-", "").replace(" ", "").upper().encode("utf-8")
        if not hmac.compare_digest(given, setup_code().replace("-", "").encode()):
            raise PermissionError(f"That isn't the setup code. It's in the server's log ({code_place()}).")
        _set_login(cfg, name, password)
        _save(cfg)
        _setup_code = None


def reset() -> None:
    """--reset-login: the user forgotten (every browser signed out); the next visit sets it up again."""
    with _lock:
        cfg = load()
        for k in ("user", "salt", "password", "secret"):
            cfg.pop(k, None)
        _save(cfg)


def password_ok(name: str, password: str) -> bool:
    cfg = load()
    if not (cfg.get("user") and cfg.get("password") and cfg.get("salt")):
        return False
    try:
        got = _hash(password or "", bytes.fromhex(cfg["salt"]))
    except ValueError:
        return False
    same_password = hmac.compare_digest(got, cfg["password"])
    same_user = hmac.compare_digest((name or "").strip().encode("utf-8"), cfg["user"].encode("utf-8"))
    return same_password and same_user


def change(current: str, name: str, password: str) -> None:
    """A new username and password (the current password first). Every browser is signed out, this one too."""
    name, password = _check_new(name, password)
    if not password_ok(user(), current):
        raise PermissionError("That isn't the current password.")
    with _lock:
        cfg = load()
        _set_login(cfg, name, password)
        _save(cfg)


def wait(host: str) -> int:
    """Seconds before this address may try a password again (0: now)."""
    now = time.time()
    tries = _failures[host]
    while tries and now - tries[0] > TRY_WINDOW:
        tries.popleft()
    return int(tries[0] + TRY_WINDOW - now) + 1 if len(tries) >= TRIES else 0


def failed(host: str) -> None:
    _failures[host].append(time.time())


def succeeded(host: str) -> None:
    _failures.pop(host, None)


def _sign(secret: str, body: str) -> str:
    return hmac.new(bytes.fromhex(secret), body.encode("utf-8"), hashlib.sha256).hexdigest()


def session(name: str) -> str:
    """A session cookie's value: who, until when, signed with the secret."""
    cfg = load()
    who = base64.urlsafe_b64encode(name.encode("utf-8")).decode().rstrip("=")
    body = f"{who}.{int(time.time()) + SESSION_DAYS * 86400}.{secrets.token_hex(8)}"
    return f"{body}.{_sign(cfg['secret'], body)}"


def session_user(value: str | None) -> str | None:
    """Who a session cookie is for, if it's signed by this server, hasn't run out and is the current user's."""
    cfg = load()
    if not value or not cfg.get("secret") or not cfg.get("user") or value.count(".") != 3:
        return None
    body, _, signature = value.rpartition(".")
    if not hmac.compare_digest(_sign(cfg["secret"], body).encode(), signature.encode("utf-8")):
        return None
    who, until, _ = body.split(".")
    try:
        name = base64.urlsafe_b64decode(who + "=" * (-len(who) % 4)).decode("utf-8")
        if int(until) < time.time():
            return None
    except ValueError:
        return None
    return name if name == cfg["user"] else None


def api_key() -> str:
    """The API key (made the first time it's asked for)."""
    with _lock:
        cfg = load()
        if not cfg.get("api_key"):
            cfg["api_key"] = secrets.token_hex(16)
            _save(cfg)
        return cfg["api_key"]


def new_api_key() -> str:
    with _lock:
        cfg = load()
        cfg["api_key"] = secrets.token_hex(16)
        _save(cfg)
        return cfg["api_key"]


def api_key_ok(value: str | None) -> bool:
    key = load().get("api_key")
    return bool(value and key) and hmac.compare_digest(value.encode("utf-8"), key.encode())
