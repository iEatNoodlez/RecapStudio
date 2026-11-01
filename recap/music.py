"""Background music under the narration: the project's own tracks (its music folder, listed in project["music"]),
played in their order and again from the first for as long as the video runs, at the project's music volume.
They're mixed in when the chapters are joined (video._join_part), so changing them draws nothing again.

Each track is converted once into the render folder: 48 kHz stereo, its loudness evened out (a loudly mastered
song would drown the narration) and its ends faded, so one track gives way to the next without a jump. A concat
list then repeats them until they cover the video."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import store, video

FORMATS = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma")
RATE = 48000
LOUDNESS = -20.0  # LUFS every track is evened out to, before the volume setting
FADE = 1.5        # seconds each track fades in and out
VERSION = 1       # bump when the conversion changes, so the tracks are converted again


def duration(path) -> float:
    """A track's length in seconds, as ffmpeg reads it (0.0 when it can't)."""
    r = subprocess.run([video.find_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=video.NO_WINDOW)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0


def tracks(project) -> list[dict]:
    """The project's tracks that are there and long enough to play, in order."""
    d = store.project_dir(project["id"])
    return [t for t in project.get("music") or [] if t.get("seconds", 0) > 4 * FADE and (d / t["file"]).is_file()]


def _prepared(ffmpeg, src: Path, seconds: float, cache: Path) -> Path:
    """The track converted for mixing (once: kept in the cache while the file stays the same)."""
    st = src.stat()
    key = hashlib.sha1(json.dumps([VERSION, src.name, st.st_size, st.st_mtime_ns]).encode()).hexdigest()[:16]
    out = cache / f"{key}.wav"
    if out.exists():
        return out
    tmp = cache / f"{key}.part.wav"
    af = (f"loudnorm=I={LOUDNESS}:TP=-2:LRA=11,aresample={RATE},"
          f"afade=t=in:d={FADE},afade=t=out:st={max(0.0, seconds - FADE):.3f}:d={FADE}")
    r = subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(src), "-vn", "-af", af, "-ar", str(RATE),
                        "-ac", "2", "-c:a", "pcm_s16le", str(tmp)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", creationflags=video.NO_WINDOW)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"The music track {src.name} couldn't be read: {r.stderr.strip()[-300:]}")
    tmp.replace(out)
    return out


def playlist(project, seconds: float, cache: Path) -> Path | None:
    """A concat list of the project's tracks, converted, repeated until they last the given seconds; None
    without tracks."""
    have = tracks(project)
    if not have:
        return None
    ffmpeg = video.find_ffmpeg()
    d = store.project_dir(project["id"])
    cache.mkdir(parents=True, exist_ok=True)
    files = [(_prepared(ffmpeg, d / t["file"], t["seconds"], cache), t["seconds"]) for t in have]
    lines, total = [], 0.0
    while total < seconds + 1:
        f, s = files[len(lines) % len(files)]
        lines.append(video._concat_line(f))
        total += s
    out = cache / "playlist.txt"
    out.write_text("".join(lines), encoding="utf-8")
    return out
