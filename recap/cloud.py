"""Narration with other online AI services, a whole chapter per request like Claude:
OpenRouter (hundreds of models from many companies behind one key), Google Gemini, DeepSeek,
Groq and Mistral.

All of them speak OpenAI's chat-completions format, so one small client covers them. Each gets
the same request Claude does (claude._content), with the images turned into data-URL parts,
and answers in claude.SCHEMA's shape.
"""
import json
import logging
import os
import re

import requests

from . import claude
from .store import DATA_ROOT

log = logging.getLogger(__name__)  # token use (and cost, when the service reports it) per request

PROVIDERS = {
    "openrouter": {
        "name": "OpenRouter",
        "base": "https://openrouter.ai/api/v1",
        "env": ("OPENROUTER_API_KEY",),
        "keys_url": "https://openrouter.ai/settings/keys",
        "billing": "openrouter.ai (Settings → Credits)",
        "json": "schema",  # how the answer's JSON shape is asked for (see _formats)
        "default_model": "",
        "about": ("One key for hundreds of models from many companies (Anthropic, Google, OpenAI, DeepSeek "
                  "and more), paid from one balance. Pick a model that reads images: the list marks them, "
                  "with each model's price."),
    },
    "gemini": {
        "name": "Google Gemini",
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "keys_url": "https://aistudio.google.com/apikey",
        "billing": "aistudio.google.com (Billing)",
        "json": "schema",
        "default_model": "gemini-3.8-flash",
        "about": "Google's Gemini models read images. A key from Google AI Studio; there is a free tier with lower limits.",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base": "https://api.deepseek.com",
        "env": ("DEEPSEEK_API_KEY",),
        "keys_url": "https://platform.deepseek.com/api_keys",
        "billing": "platform.deepseek.com (Top up)",
        "json": "object",
        "default_model": "deepseek-flash",
        "about": ("Inexpensive. Its models may not read images; then they work from the text read from the "
                  "pages alone, which misses who is where and everything said without words."),
    },
    "groq": {
        "name": "Groq",
        "base": "https://api.groq.com/openai/v1",
        "env": ("GROQ_API_KEY",),
        "keys_url": "https://console.groq.com/keys",
        "billing": "console.groq.com (Settings → Billing)",
        "json": "object",
        "default_model": "qwen/qwen3.8-27b",
        # its image models take at most 3 images a request (Qwen 3.8; Muse Spark Open takes 5), so a
        # chapter goes in many small requests
        "max_images": 3,
        "part_tokens": 20000,
        "about": ("Very fast, with a free tier (low limits: the job waits when it reaches them). Few of its models "
                  "read images, and those take only 3 pictures a request, so a chapter is sent in many small parts."),
    },
    "mistral": {
        "name": "Mistral",
        "base": "https://api.mistral.ai/v1",
        "env": ("MISTRAL_API_KEY",),
        "keys_url": "https://console.mistral.ai/api-keys",
        "billing": "console.mistral.ai (Billing)",
        "json": "schema",
        "default_model": "mistral-medium-latest",
        "max_images": 8,
        "about": ("Mistral's own models; Medium, Small, Large and Ministral read images. "
                  "There is a free plan with lower limits."),
    },
}
MAX_TOKENS = 32000      # room for a chapter's narration plus any thinking
SMALL_MAX_TOKENS = 8192  # for models that don't allow that much
TIMEOUT = (30, 900)     # (connect, read) seconds: a long chapter can take minutes
QUICK = (15, 60)        # for checks: the key, the model list, a one-word test
_no_images: set[tuple[str, str]] = set()  # (provider, model) that turned images down this session


class Problem(Exception):
    """A failed request, sorted by what fixes it: "key", "credit", "quota" (no allowance left on this key's
    plan) or "model" (not available to this key) pause the job; "busy" waits, then pauses; "images",
    "max_tokens" and "format" are asked again differently; "declined" or "bad" go to the local model."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# ---- API keys (stored only on this PC, like Claude's) ----------------------------

def _key_file(provider):
    return DATA_ROOT / f"{provider}_key.txt"


def saved_key(provider: str) -> str | None:
    try:
        return _key_file(provider).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def save_key(provider: str, key: str) -> None:
    _key_file(provider).parent.mkdir(parents=True, exist_ok=True)
    _key_file(provider).write_text(key.strip(), encoding="utf-8")


def delete_key(provider: str) -> None:
    _key_file(provider).unlink(missing_ok=True)


def _env_key(provider):
    return next((os.environ[v] for v in PROVIDERS[provider]["env"] if os.environ.get(v)), None)


def key_status(provider: str) -> dict:
    return {"saved": saved_key(provider) is not None, "env": _env_key(provider) is not None,
            "env_name": PROVIDERS[provider]["env"][0]}


def has_key(provider: str) -> bool:
    return bool(saved_key(provider) or _env_key(provider))


# ---- requests -----------------------------------------------------------------

def _headers(provider):
    h = {"Authorization": f"Bearer {saved_key(provider) or _env_key(provider) or ''}",
         "Content-Type": "application/json"}
    if provider == "openrouter":
        h["X-Title"] = "Recap Studio"  # shown on the user's OpenRouter activity page
    return h


def _call(provider, method, path, body=None, timeout=None):
    url = PROVIDERS[provider]["base"] + path
    try:
        r = requests.request(method, url, headers=_headers(provider), json=body, timeout=timeout or TIMEOUT)
    except requests.RequestException as e:  # no connection, or it dropped or timed out
        raise Problem("busy", f"{type(e).__name__}: {e}") from e
    try:
        data = r.json()
    except ValueError:
        data = {}
    if isinstance(data, list):  # Gemini sometimes wraps an error in a list
        data = data[0] if data and isinstance(data[0], dict) else {}
    err = data.get("error") if isinstance(data, dict) else None
    if r.status_code >= 400 or err:
        # OpenRouter answers 200 with only an "error" when a model fails after accepting the request
        code = (err or {}).get("code") if isinstance(err, dict) else None
        status = code if isinstance(code, int) else r.status_code
        message = (err or {}).get("message") if isinstance(err, dict) else str(err or "")
        raise _problem(status, message or r.text[:300])
    return data


def _problem(status: int, message: str) -> Problem:
    m = message or f"HTTP {status}"
    if status == 402 or re.search(r"insufficient (balance|credit)|out of credit", m, re.I):
        return Problem("credit", m)
    if status == 403 and re.search(r"moderat|flagged|guardrail|safety", m, re.I):
        return Problem("declined", m)
    if status in (401, 403) or re.search(r"api key not valid|invalid api key|unauthorized", m, re.I):
        return Problem("key", m)
    if status == 429 and re.search(r"quota|billing|plan", m, re.I) and not re.search(r"retry|try again|per minute", m, re.I):
        return Problem("quota", m)  # e.g. a free-tier key and a model it has no allowance for: waiting won't help
    if status in (408, 409, 425, 429) or status >= 500:
        return Problem("busy", m)
    if status == 404 or re.search(r"model.{0,60}(not found|no longer available|does not exist|not supported)"
                                  r"|unknown model|invalid model", m, re.I):
        return Problem("model", m)
    if re.search(r"image|vision|multimodal|modalit", m, re.I):
        return Problem("images", m)
    if re.search(r"max[_ ]?(completion[_ ])?tokens|maximum.*tokens|output tokens", m, re.I):
        return Problem("max_tokens", m)
    if re.search(r"response_format|json_schema|structured", m, re.I):
        return Problem("format", m)
    return Problem("bad", m)


def check_key(provider: str) -> str:
    """A free request that fails if the key is wrong."""
    _call(provider, "GET", "/key" if provider == "openrouter" else "/models", timeout=QUICK)
    return "ok"


def list_models(provider: str) -> list[dict]:
    """The service's models: [{"id", "name", "vision": True/False/None (unknown), "price_in" and
    "price_out" in $ per million tokens (or None), "context"}], best first for this job."""
    path = "/models?output_modalities=text&input_modalities=all" if provider == "openrouter" else "/models"
    data = _call(provider, "GET", path, timeout=QUICK).get("data", [])
    out = []
    for m in data:
        mid = str(m.get("id", "")).removeprefix("models/")
        if provider == "gemini" and (not mid.startswith("gemini") or re.search(
                r"embed|image|tts|audio|live|veo|imagen|aqa|robotics|computer-use|transcribe|customtools|banana|omni",
                mid)):
            continue  # Gemini's list also has embedding, image, speech, video and tool-use models
        caps = m.get("capabilities") if isinstance(m.get("capabilities"), dict) else {}
        if provider == "mistral" and (not caps.get("completion_chat", True) or re.search(r"embed|moderation|ocr", mid)):
            continue  # Mistral's list also has embedding, moderation and OCR models
        if provider == "groq" and (m.get("active") is False or re.search(r"whisper|tts|guard|orpheus|compound", mid)):
            continue  # Groq's list also has speech, safety and agent models
        price = m.get("pricing") or {}
        per_m = lambda v: round(float(v) * 1e6, 3) if v not in (None, "", "-1") else None  # noqa: E731
        inputs = (m.get("architecture") or {}).get("input_modalities")
        if inputs is not None:
            vision = "image" in inputs
        elif "vision" in caps:  # Mistral says so
            vision = bool(caps["vision"])
        elif provider == "groq":  # Groq's list doesn't say; these are its image models (console.groq.com/docs/vision)
            vision = True if re.search(r"qwen3\.8|muse-spark|llama-4", mid) else None
        else:
            vision = True if provider == "gemini" else None
        out.append({
            "id": mid,
            "name": m.get("name") or mid,
            "vision": vision,
            "price_in": per_m(price.get("prompt")),
            "price_out": per_m(price.get("completion")),
            "context": m.get("context_length") or m.get("context_window") or m.get("max_context_length"),
        })
    out.sort(key=lambda x: ({True: 0, None: 1, False: 2}[x["vision"]], x["name"].lower()))  # image models first
    return out


def test_model(provider: str, model: str) -> dict:
    """One tiny request, so a model this key can't use (busy, no quota, unavailable) shows up in Settings
    rather than partway through a run. Costs a few tokens."""
    body = {"model": model, "max_tokens": 20, "messages": [{"role": "user", "content": "Reply with the single word: ready"}]}
    try:
        _call(provider, "POST", "/chat/completions", body, timeout=QUICK)
        return {"ok": True, "kind": None, "message": "This model answered: it's ready to use."}
    except Problem as e:
        why = {"busy": "is busy or overloaded right now; try again later or pick another model",
               "quota": "has no allowance left on this key's plan (free keys often can't use the bigger models)",
               "model": "isn't available to this key", "key": "rejected the key",
               "credit": "says the account is out of credit"}.get(e.kind, "didn't accept the request")
        return {"ok": False, "kind": e.kind, "message": f"{PROVIDERS[provider]['name']} {why}: {str(e)[:200]}"}


def images_ok(provider: str, model: str) -> bool:
    return (provider, model) not in _no_images


def narrate(provider: str, model: str, instructions: str, chapter: str, pieces: list[dict], context: str,
            fixed: list[dict] | None = None, scroll: bool = False, cover: bool = False) -> tuple:
    """Narrate one part of a chapter; same arguments and result as claude.narrate."""
    data = ask(provider, model, instructions, claude._content(chapter, pieces, context, fixed, scroll, cover),
               schema=claude.SCHEMA_COVER if cover else None)
    return claude.read_answer(data, len(pieces), fixed) + ((claude.read_covers(data, len(pieces)),) if cover else ())


def ask(provider: str, model: str, instructions: str, blocks: list[dict], schema: dict | None = None,
        name: str = "recap_scenes") -> dict:
    """One request (blocks in claude._content's form) whose answer is JSON in schema's shape (the scenes'
    by default)."""
    formats = _formats(PROVIDERS[provider]["json"], schema or claude.SCHEMA, name)
    max_tokens = MAX_TOKENS
    while True:
        images = images_ok(provider, model)
        body = {"model": model, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": instructions},
                             {"role": "user", "content": _parts(blocks, images, PROVIDERS[provider].get("max_images"))}]}
        if formats[0]:
            body["response_format"] = formats[0]
        try:
            data = _call(provider, "POST", "/chat/completions", body)
        except Problem as e:  # ask again in a way this model accepts, if that's what went wrong
            if e.kind == "images" and images:
                log.warning("%s %s doesn't take images; sending text only: %s", provider, model, e)
                _no_images.add((provider, model))
                continue
            if e.kind == "max_tokens" and max_tokens > SMALL_MAX_TOKENS:
                max_tokens = SMALL_MAX_TOKENS
                continue
            if e.kind == "format" and len(formats) > 1:
                formats = formats[1:]
                continue
            raise
        break
    choice = (data.get("choices") or [{}])[0]
    finish = choice.get("finish_reason")
    text = (choice.get("message") or {}).get("content") or ""
    u = data.get("usage") or {}
    log.info("%s %s: %s input + %s output tokens%s, finish %s", PROVIDERS[provider]["name"], model,
             u.get("prompt_tokens", "?"), u.get("completion_tokens", "?"),
             f", ${u['cost']:.4f}" if isinstance(u.get("cost"), (int, float)) else "", finish)
    if finish == "length":
        raise claude.CutOff(f"{PROVIDERS[provider]['name']}'s answer was cut off (too long)")
    if finish == "content_filter":
        raise claude.Declined(f"{PROVIDERS[provider]['name']} declined this part")
    return _json(text)


def _formats(kind, schema, name):
    """Ways to ask for the answer's JSON, best first; a model that refuses one gets the next."""
    schema = {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}
    if kind == "schema":
        return [schema, {"type": "json_object"}, None]
    return [{"type": "json_object"}, None]


def _parts(blocks, images: bool, max_images: int | None = None) -> list[dict]:
    """claude._content's blocks in chat-completions form; without images when the model can't take them,
    and with at most max_images pictures (a scene already grouped can't be split across requests)."""
    parts, shown, left_out = [], 0, 0
    for b in blocks:
        if b["type"] == "text":
            parts.append({"type": "text", "text": b["text"]})
        elif images and (not max_images or shown < max_images):
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:{b['source']['media_type']};base64,{b['source']['data']}"}})
            shown += 1
        elif images:
            left_out += 1
    if not images:
        parts.insert(0, {"type": "text", "text": "(The pictures can't be shown to this model, so work from the text "
                                                 "read from each piece and its place in the chapter.)"})
    elif left_out:
        parts.insert(0, {"type": "text", "text": f"(This model takes {max_images} pictures a request, so only the "
                                                 f"first {max_images} are shown; work from the text read from the "
                                                 "later pieces.)"})
    return parts


def _json(text: str) -> dict:
    """The answer's JSON object, also when the model wrapped it in a code fence or added a line around it."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()  # a reasoning model's thinking, if shown
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise
