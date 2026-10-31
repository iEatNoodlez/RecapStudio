"""Your own recorded narration instead of a generated voice: one recording per chapter, read from its
script in order, is split into the scenes' lines and checked against the script.

Speech recognition is Vosk with its small English model (Apache 2.0, 41 MB from alphacephei.com,
downloaded on request; it runs on this computer). It writes down what was said with each word's time.
Those words are lined up with the script's (longest matching runs first, in order), so each line's
part of the recording is where its words were heard; a line whose words weren't heard gets the
stretch between its neighbours, if there is one. Each line's share of words heard shows how well the
recording follows the script: low ones are listed for checking, and so is anything said that isn't
in the script. Names the recognizer doesn't know count as not heard, so a short line with a name in
it can show a low share even when it was read right.
"""
import difflib
import json
import re
import subprocess

import numpy as np
import soundfile as sf

from . import addon, store
from .tts import SAMPLE_RATE

MODEL = "vosk-model-small-en-us-0.15"
DOWNLOAD = addon.Download(store.ADDONS_DIR / "vosk", {
    f"{MODEL}.zip": ("", "", f"{MODEL}.zip", 41205931,
                     "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"),
}, url="https://alphacephei.com/vosk/models/{path}", unzip=True)
FORMATS = (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".webm")
RATE = 16000        # what the recognizer listens at
GOOD = 0.6          # share of a line's words heard, to count as found
WORD_SECONDS = 0.35  # about how long a word takes: room for words at a line's ends that weren't heard
LEAD, TAIL = 0.1, 0.15  # kept before a line's first word and after its last
EXTRA_WORDS = 4     # this many words in a row not in the script are listed as said but not in it


def installed() -> bool:
    return DOWNLOAD.installed() and (DOWNLOAD.folder / MODEL).is_dir()


def decode(path, rate: int, ffmpeg: str) -> np.ndarray:
    """The recording as mono samples at rate (any format ffmpeg reads)."""
    r = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"],
                       capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError(f"Couldn't read the recording: {r.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(r.stdout, np.float32).copy()


def transcribe(samples: np.ndarray, progress=None, cancelled=None) -> list[dict]:
    """What was said in samples (mono, RATE): [{"word", "start", "end", "conf"}] in order."""
    import vosk
    vosk.SetLogLevel(-1)
    rec = vosk.KaldiRecognizer(vosk.Model(str(DOWNLOAD.folder / MODEL)), RATE)
    rec.SetWords(True)
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    words, step = [], RATE * 2 * 4  # 4 seconds at a time
    for at in range(0, len(pcm), step):
        if cancelled and cancelled():
            raise InterruptedError("cancelled")
        if rec.AcceptWaveform(pcm[at:at + step]):
            words += json.loads(rec.Result()).get("result", [])
        if progress:
            progress(min(1.0, (at + step) / max(1, len(pcm))))
    words += json.loads(rec.FinalResult()).get("result", [])
    return words


def tokens(text: str) -> list[str]:
    """A line's words as the recognizer writes them: lower case, hyphenated words apart, no punctuation."""
    text = text.lower().replace("’", "'").replace("-", " ").replace("—", " ")
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text)


def align(lines: list[str], words: list[dict], total: float) -> tuple[list[dict], list[dict]]:
    """Where each line is in the recording: [{"start", "end" (seconds, or None when it wasn't found),
    "heard" (share of its words heard), "words"}], and what was said that isn't in the script:
    [{"start", "text"}]. total: the recording's length in seconds."""
    script = [(t, i) for i, line in enumerate(lines) for t in tokens(line)]
    heard = [w["word"] for w in words]
    match = {}
    for a, b, size in difflib.SequenceMatcher(None, [t for t, _ in script], heard, autojunk=False).get_matching_blocks():
        for k in range(size):
            match[a + k] = b + k
    out = []
    for i, line in enumerate(lines):
        idx = [k for k, (_, li) in enumerate(script) if li == i]
        got = [k for k in idx if k in match]
        if not got:
            out.append({"a": None, "b": None, "heard": 0.0, "words": len(idx), "lead": 0, "trail": 0})
            continue
        out.append({"a": words[match[got[0]]]["start"], "b": words[match[got[-1]]]["end"],
                    "heard": len(got) / len(idx), "words": len(idx),
                    "lead": idx.index(got[0]), "trail": len(idx) - 1 - idx.index(got[-1])})
    # each line's stretch: its heard words, widened for words at its ends that weren't heard, up to
    # halfway to the next line's heard words; a line not heard at all gets the gap between its neighbours
    found = [k for k, x in enumerate(out) if x["a"] is not None]
    for k, x in enumerate(out):
        before = max((out[j]["b"] for j in found if j < k), default=0.0)
        after = min((out[j]["a"] for j in found if j > k), default=total)
        if x["a"] is None:
            lo, hi = before + TAIL + 0.05, after - LEAD - 0.05  # clear of the neighbours' own stretches
            x["start"], x["end"] = (lo, hi) if hi - lo >= 0.4 else (None, None)
            continue
        x["start"] = max((before + x["a"]) / 2 if k and before else 0.0, x["a"] - LEAD - WORD_SECONDS * x["lead"])
        x["end"] = min((x["b"] + after) / 2 if after < total else total, x["b"] + TAIL + WORD_SECONDS * x["trail"])
    extras, run = [], []
    used = set(match.values())
    for j, w in enumerate(words + [None]):
        if w is not None and j not in used:
            run.append(w)
            continue
        if len(run) >= EXTRA_WORDS:
            extras.append({"start": round(run[0]["start"], 1), "text": " ".join(x["word"] for x in run)})
        run = []
    return ([{"start": x["start"], "end": x["end"], "heard": round(x["heard"], 2), "words": x["words"]} for x in out],
            extras)


def _frames(a: np.ndarray) -> np.ndarray:
    """Loudness (RMS) of each 30 ms of a."""
    n = int(0.03 * SAMPLE_RATE)
    k = len(a) // n
    return np.sqrt((a[:k * n].reshape(k, n) ** 2).mean(axis=1)) if k else np.zeros(0)


def _speech_level(a: np.ndarray) -> float:
    """How loud speech is in the recording: its louder stretches."""
    f = _frames(a)
    return float(np.percentile(f, 90)) if len(f) else 0.0


def _talking(a: np.ndarray, loud: float) -> float:
    """Seconds of a that sound like speech (within 20 dB of the recording's speech)."""
    return float((_frames(a) > 0.1 * loud).sum() * 0.03) if loud > 0 else 0.0


def split(recording, lines: list[tuple[str, str]], out_dir, ffmpeg: str, progress=None, cancelled=None) -> dict:
    """Cut recording into lines [(name, narration)]: each found line is written to out_dir/<name>.wav.
    Returns {"lines": [{"name", "file" (or None), "seconds", "heard"}], "extras", "seconds", "heard"}."""
    listen = decode(recording, RATE, ffmpeg)
    total = len(listen) / RATE
    words = transcribe(listen, (lambda f: progress(0.85 * f)) if progress else None, cancelled)
    del listen
    placed, extras = align([text for _, text in lines], words, total)
    audio = decode(recording, SAMPLE_RATE, ffmpeg)
    fade = int(0.01 * SAMPLE_RATE)
    loud = _speech_level(audio)
    out = []
    for (name, _), x in zip(lines, placed):
        if x["start"] is not None and not x["heard"]:  # a gap between lines: only if there's speech in it
            gap = audio[int(x["start"] * SAMPLE_RATE):int(x["end"] * SAMPLE_RATE)]
            if _talking(gap, loud) < max(0.4, 0.5 * WORD_SECONDS * x["words"]):
                x["start"] = None
        if x["start"] is None or x["end"] - x["start"] < 0.2:
            out.append({"name": name, "file": None, "seconds": 0.0, "heard": x["heard"]})
            continue
        part = audio[int(x["start"] * SAMPLE_RATE):int(x["end"] * SAMPLE_RATE)].copy()
        ramp = np.linspace(0, 1, min(fade, len(part) // 2), dtype=np.float32)
        part[:len(ramp)] *= ramp  # no click where it's cut
        part[len(part) - len(ramp):] *= ramp[::-1]
        file = f"{name}.wav"
        sf.write(out_dir / file, part, SAMPLE_RATE)
        out.append({"name": name, "file": file, "seconds": len(part) / SAMPLE_RATE, "heard": x["heard"]})
    if progress:
        progress(1.0)
    said = sum(x["words"] for x in placed)
    return {"lines": out, "extras": extras, "seconds": total,
            "heard": round(sum(x["heard"] * x["words"] for x in placed) / said, 2) if said else 0.0}
