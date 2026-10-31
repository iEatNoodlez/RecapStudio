"""Online voices, as an alternative to Kokoro on this computer:

- Microsoft Edge's voices (free, no key): through the edge-tts package, which uses the service behind
  Edge's Read Aloud. It isn't an official API, so Microsoft can change or limit it.
- ElevenLabs (paid, your own API key), through its text-to-speech API.

Each turns one narration line into mono samples at tts.SAMPLE_RATE, like tts.synthesize.
"""
import asyncio
import io
import logging
import os
import time

import numpy as np
import requests
import soundfile as sf

from .store import DATA_ROOT
from .tts import SAMPLE_RATE

log = logging.getLogger(__name__)

PROVIDERS = {"kokoro": "Kokoro (on this computer)", "edge": "Microsoft Edge voices", "elevenlabs": "ElevenLabs"}
WORKERS = {"edge": 4, "elevenlabs": 2}  # lines voiced at once (ElevenLabs' cheaper plans take 2-3 at a time)
EDGE_DEFAULT = "en-US-AndrewNeural"
ELEVEN_BASE = "https://api.elevenlabs.io"
ELEVEN_MODEL = "eleven_multilingual_v2"
ELEVEN_SPEED = (0.7, 1.2)  # the speeds ElevenLabs accepts
RETRY_WAITS = (2, 5, 15)   # seconds before trying a line again when the service is busy or unreachable
TIMEOUT = (15, 120)


class Problem(Exception):
    """A line that couldn't be voiced, sorted by what fixes it: "key" (missing or rejected), "quota" (no
    characters left on the plan), "voice" (the chosen voice isn't available), "busy" (still failing
    after a few tries) or "bad" (the service didn't accept this text)."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _resample(a: np.ndarray, sr: int) -> np.ndarray:
    if sr == SAMPLE_RATE or not len(a):
        return a.astype(np.float32)
    n = int(len(a) * SAMPLE_RATE / sr)
    return np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)


def _retrying(do, who: str):
    """Run do(), trying again after a wait when the service is busy or the connection fails."""
    for wait in (*RETRY_WAITS, None):
        try:
            return do()
        except Problem as e:
            if e.kind != "busy" or wait is None:
                raise
            log.warning("%s: %s; trying again in %s s", who, e, wait)
            time.sleep(wait)


# ---- Microsoft Edge ------------------------------------------------------------

_edge_list = None


def edge_voices() -> list[dict]:
    """Edge's voices (fetched once per run): [{"id", "name", "locale", "gender"}], English first."""
    global _edge_list
    if _edge_list is None:
        import edge_tts
        try:
            found = asyncio.run(edge_tts.list_voices())
        except Exception as e:  # no connection, or the service changed
            raise Problem("busy", f"Couldn't get Edge's voice list: {type(e).__name__}: {e}") from e
        _edge_list = sorted(({"id": v["ShortName"], "locale": v["Locale"], "gender": v.get("Gender", ""),
                              "name": v["ShortName"].split("-", 2)[-1].removesuffix("Neural")} for v in found),
                            key=lambda v: (not v["locale"].startswith("en-"), v["locale"], v["name"]))
    return _edge_list


def edge_speak(text: str, voice: str, speed: float) -> np.ndarray:
    import aiohttp
    import edge_tts
    rate = f"{round((float(speed) - 1) * 100):+d}%"

    async def fetch():
        data = bytearray()
        async for chunk in edge_tts.Communicate(text, voice or EDGE_DEFAULT, rate=rate).stream():
            if chunk["type"] == "audio":
                data += chunk["data"]
        return bytes(data)

    def once():
        try:
            mp3 = asyncio.run(fetch())
        except edge_tts.exceptions.NoAudioReceived as e:  # now and then for no reason, or always for a voice it lacks
            try:
                known = any(v["id"] == (voice or EDGE_DEFAULT) for v in edge_voices())
            except Problem:
                known = True
            if not known:
                raise Problem("voice", f"Edge doesn't have the voice {voice}") from e
            raise Problem("busy", f"Edge returned no audio for this line ({voice}): {text[:60]}") from e
        except ValueError as e:  # an unknown voice name
            raise Problem("voice", f"Edge doesn't have the voice {voice}: {e}") from e
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, edge_tts.exceptions.EdgeTTSException) as e:
            raise Problem("busy", f"{type(e).__name__}: {e}") from e
        a, sr = sf.read(io.BytesIO(mp3), dtype="float32")
        return _resample(a.mean(axis=1) if a.ndim > 1 else a, sr)
    return _retrying(once, "Edge voice")


# ---- ElevenLabs ------------------------------------------------------------------
# The key is stored only on this PC (data\elevenlabs_key.txt), like the script writers' keys.

def _key_file():
    return DATA_ROOT / "elevenlabs_key.txt"


def saved_key() -> str | None:
    try:
        return _key_file().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def save_key(key: str) -> None:
    _key_file().parent.mkdir(parents=True, exist_ok=True)
    _key_file().write_text(key.strip(), encoding="utf-8")


def delete_key() -> None:
    _key_file().unlink(missing_ok=True)


def key_status() -> dict:
    return {"saved": saved_key() is not None, "env": bool(os.environ.get("ELEVENLABS_API_KEY")),
            "env_name": "ELEVENLABS_API_KEY"}


def has_key() -> bool:
    return bool(saved_key() or os.environ.get("ELEVENLABS_API_KEY"))


def _eleven(method: str, path: str, **kw) -> requests.Response:
    key = saved_key() or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise Problem("key", "No ElevenLabs API key: add one in Settings → Voice.")
    try:
        r = requests.request(method, ELEVEN_BASE + path, headers={"xi-api-key": key}, timeout=TIMEOUT, **kw)
    except requests.RequestException as e:
        raise Problem("busy", f"{type(e).__name__}: {e}") from e
    if r.status_code < 400:
        return r
    try:
        detail = r.json().get("detail")
    except ValueError:
        detail = None
    status = detail.get("status", "") if isinstance(detail, dict) else ""
    message = (detail.get("message") if isinstance(detail, dict) else str(detail or "")) or r.text[:200]
    if r.status_code == 401 and status not in ("quota_exceeded", "payment_required"):
        raise Problem("key", f"ElevenLabs rejected the key: {message}")
    if r.status_code == 402 or status in ("quota_exceeded", "payment_required", "insufficient_credits"):
        raise Problem("quota", f"ElevenLabs: {message}")
    if r.status_code == 429 or r.status_code >= 500:
        raise Problem("busy", f"ElevenLabs is busy (HTTP {r.status_code}): {message}")
    if r.status_code == 404 or status in ("voice_not_found", "model_not_found"):
        raise Problem("voice", f"ElevenLabs: {message}")
    raise Problem("bad", f"ElevenLabs (HTTP {r.status_code}): {message}")


def eleven_check() -> dict:
    """The key works: what's left on its plan this month."""
    sub = _eleven("GET", "/v1/user/subscription").json()
    used, limit = sub.get("character_count"), sub.get("character_limit")
    return {"tier": sub.get("tier"), "used": used, "limit": limit}


def eleven_voices() -> list[dict]:
    """The voices this key can use (its own and the ready-made ones): [{"id", "name", "category", "labels"}]."""
    out, token = [], None
    for _ in range(10):
        params = {"page_size": 100, "include_total_count": "false", **({"next_page_token": token} if token else {})}
        data = _eleven("GET", "/v2/voices", params=params).json()
        out += [{"id": v["voice_id"], "name": v.get("name") or v["voice_id"], "category": v.get("category") or "",
                 "labels": v.get("labels") or {}} for v in data.get("voices", [])]
        token = data.get("next_page_token")
        if not data.get("has_more") or not token:
            break
    return sorted(out, key=lambda v: v["name"].lower())


def eleven_models() -> list[dict]:
    """Its text-to-speech models: [{"id", "name", "cost"}] (cost: credits per character)."""
    out = []
    for m in _eleven("GET", "/v1/models").json():
        if m.get("can_do_text_to_speech", True):
            out.append({"id": m["model_id"], "name": m.get("name") or m["model_id"],
                        "cost": (m.get("model_rates") or {}).get("character_cost_multiplier")})
    return out


def eleven_speak(text: str, voice: str, model: str, speed: float) -> np.ndarray:
    if not voice:
        raise Problem("voice", "Choose an ElevenLabs voice in Settings → Voice.")
    body = {"text": text, "model_id": model or ELEVEN_MODEL}
    speed = min(ELEVEN_SPEED[1], max(ELEVEN_SPEED[0], float(speed)))
    if abs(speed - 1.0) > 1e-3:
        body["voice_settings"] = {"speed": speed}

    def once():
        r = _eleven("POST", f"/v1/text-to-speech/{voice}", params={"output_format": f"pcm_{SAMPLE_RATE}"}, json=body)
        return np.frombuffer(r.content, dtype="<i2").astype(np.float32) / 32768.0  # 16-bit mono PCM
    return _retrying(once, "ElevenLabs")
