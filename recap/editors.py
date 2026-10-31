"""Export for video editors: the recap as an OpenTimelineIO bundle (.otioz), which DaVinci Resolve,
Premiere Pro (25.6 and later) and other editors that read OpenTimelineIO open as a timeline: one
clip per scene, the narration as one clip per line, a crossfade transition wherever the video
crossfades and a marker at each chapter. Its subtitles (.srt) are saved next to it.

Two kinds:
- "resolve": each scene is its picture, moving as in the video by keyframes of DaVinci Resolve's
  own Transform effect, over its blurred backdrop on the track below; the motion can be changed in
  Resolve. Other editors don't read those keyframes and show the pictures still, fitted to the frame.
- "baked": each chapter is rendered once as a video with the motion drawn in, every scene running
  on for the length of a crossfade, and the timeline cuts that video into a clip per scene. Every
  editor shows it as the rendered video looks. These videos are kept in the project's editors
  folder, so exporting again only renders the chapters that changed.

The timing is the rendered video's: scenes follow each other, each crossfading into the next (a
transition starting where the next scene starts, using the outgoing scene's frames after it ends),
and a scrolling chapter is one scroll, cut into a clip per scene where the cuts don't show.

A bundle is a zip of content.otio (the timeline, JSON), version.txt and the media in media/, stored
uncompressed (OpenTimelineIO's "File Bundles"). The Resolve parts follow what Resolve writes when it
exports OTIO itself (OpenTimelineIO's tests/sample_data/effects.otio): Transform's zoom is relative
to the picture fitted into the frame, pan and tilt are fractions of that fitted picture's width and
height (tilt upward), and keyframes count frames from the clip's first frame.
"""
import hashlib
import json
import logging
import zipfile
from pathlib import Path

import cv2
import numpy as np

from . import video
from .panels import imread
from .scenes import stack
from .tts import SAMPLE_RATE

log = logging.getLogger(__name__)

KINDS = {"resolve": "DaVinci Resolve", "baked": "any editor"}
BUNDLE_VERSION = "1.0.0"
MAX_SCREENS = 7     # a moving picture is cut into clips at most this many screens long, as Resolve's
                    # pan and tilt reach 4 fitted pictures either way
TOLERANCE = 0.25    # screen pixels a scroll's keyframes may be off from the video's scroll
MARGIN = 2          # screen pixels of picture kept past what a clip shows, so a keyframe's rounding never shows an edge
LONGEST = 8192      # longest side of a picture in the bundle (more is never on screen)
PICTURE_QUALITY, BACKDROP_QUALITY = 95, 85
REEL_VERSION = 1    # bump when a chapter's video for editors changes, so the kept ones are made again
# Resolve's Transform parameters: (default, lowest, highest), as Resolve writes them
TRANSFORM = {"transformationZoomX": (1.0, 0.0, 100.0), "transformationZoomY": (1.0, 0.0, 100.0),
             "transformationPan": (0.0, -4.0, 4.0), "transformationTilt": (0.0, -4.0, 4.0)}


# ---- OpenTimelineIO, as JSON --------------------------------------------------

def _time(frames, fps):
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": float(fps), "value": float(frames)}


def _range(start, duration, fps):
    return {"OTIO_SCHEMA": "TimeRange.1", "duration": _time(duration, fps), "start_time": _time(start, fps)}


def _clip(name, url, start, length, available, fps, effects=()):
    """A clip of frames start .. start + length of the media at url, which has `available` frames."""
    return {"OTIO_SCHEMA": "Clip.2", "metadata": {}, "name": name, "source_range": _range(start, length, fps),
            "effects": list(effects), "markers": [], "enabled": True,
            "media_references": {"DEFAULT_MEDIA": {
                "OTIO_SCHEMA": "ExternalReference.1", "metadata": {}, "name": url.rsplit("/", 1)[-1],
                "available_range": _range(0, available, fps), "available_image_bounds": None, "target_url": url}},
            "active_media_reference_key": "DEFAULT_MEDIA"}


def _gap(length, fps):
    return {"OTIO_SCHEMA": "Gap.1", "metadata": {}, "name": "", "source_range": _range(0, length, fps),
            "effects": [], "markers": [], "enabled": True}


# DaVinci Resolve's own video transitions, for the Resolve export (Settings: editor_transition): (its name,
# the family name it files it under, Resolve's number for it). They're written the way Resolve exports a
# transition itself (OpenTimelineIO's effects.otio, whose audio cross fade has this "Effects" block), with
# no parameters, so Resolve's defaults apply. The names and numbers are the ones RecapSynth's Resolve export
# uses; not checked in Resolve itself. "dissolve" stays a plain cross dissolve, which every editor reads.
RESOLVE_TRANSITIONS = {
    "slide": ("Slide", "Slide", 49),
    "push": ("Push", "Push", 50),
    "blur": ("Blur Dissolve", "Blur Dissolve", 14),
    "smooth": ("Smooth Cut", "Morph Dissolve", 39),
}
TRANSITIONS = ("dissolve", *RESOLVE_TRANSITIONS, "mixed")
MIXED = ("dissolve", "slide", "push", "blur")  # "mixed" takes turns with these, the same at a cut on every track


def _dissolve(frames, fps, style="dissolve"):
    """A transition that starts on the cut, using the outgoing clip's frames after it ends: a cross dissolve,
    or one of Resolve's own (see RESOLVE_TRANSITIONS)."""
    t = {"OTIO_SCHEMA": "Transition.1", "metadata": {}, "name": "Cross Dissolve",
         "in_offset": _time(0, fps), "out_offset": _time(frames, fps), "transition_type": "SMPTE_Dissolve"}
    if style in RESOLVE_TRANSITIONS:
        name, family, number = RESOLVE_TRANSITIONS[style]
        t.update(name=name, transition_type="Custom_Transition", metadata={"Resolve_OTIO": {
            "Effects": {"Effect Name": name, "Enabled": True, "Name": family, "Parameters": [], "Type": number},
            "Transition Type": name}})
    return t


def _track(name, kind, children, resolve):
    return {"OTIO_SCHEMA": "Track.1", "metadata": {"Resolve_OTIO": resolve}, "name": name, "source_range": None,
            "effects": [], "markers": [], "enabled": True, "children": children, "kind": kind}


def _marker(name, frame, fps):
    # Marker.2, as every OpenTimelineIO release from 0.14 to 0.18 writes it (reading Marker.1 expects an
    # older layout and loses the range)
    return {"OTIO_SCHEMA": "Marker.2", "metadata": {}, "name": name, "color": "BLUE",
            "marked_range": _range(frame, 1, fps), "comment": ""}


def _transform(keys: dict, eased: bool = False) -> dict:
    """Resolve's Transform effect. keys: {parameter: [(frame, value)]}, a single pair being a fixed
    value. eased: two keyframes ease in and out (flat handles a third of the way along: the video's
    own easing)."""
    params = []
    for pid, pairs in keys.items():
        default, low, high = TRANSFORM[pid]
        frames = {}
        if len(pairs) > 1:
            third = (pairs[-1][0] - pairs[0][0]) / 3
            for n, (f, v) in enumerate(pairs):
                k = {"Value": float(v), "Variant Type": "Double"}
                if eased and len(pairs) == 2:
                    side = "OutBez" if n == 0 else "InBez"
                    k[side] = {side: [third if n == 0 else -third, 0.0], "Variant Type": "POINTF"}
                frames[str(int(f))] = k
        params.append({"Default Parameter Value": default, "Key Frames": frames, "Parameter ID": pid,
                       "Parameter Value": float(pairs[0][1]), "Variant Type": "Double",
                       "maxValue": high, "minValue": low})
    return {"OTIO_SCHEMA": "Effect.1", "metadata": {"Resolve_OTIO": {
        "Effect Name": "Transform", "Enabled": True, "Name": "Transform", "Parameters": params, "Type": 2}},
        "name": "", "effect_name": "Resolve Effect"}


def _place(W, H, pw, ph, sx, sy, cx, cy) -> dict:
    """Resolve's Transform values for a pw x ph picture shown at sx, sy screen pixels per picture pixel
    with its center at (cx, cy) on a W x H screen (Resolve first fits the picture into the frame)."""
    fit = min(W / pw, H / ph)
    return {"transformationZoomX": sx / fit, "transformationZoomY": sy / fit,
            "transformationPan": (cx - W / 2) / (pw * fit), "transformationTilt": (H / 2 - cy) / (ph * fit)}


def _keyframes(values: np.ndarray, tolerance: float) -> list[int]:
    """Frames to keep so straight lines between them stay within tolerance of values (Ramer-Douglas-Peucker)."""
    keep, todo = {0, len(values) - 1}, [(0, len(values) - 1)]
    while todo:
        i, j = todo.pop()
        if j - i < 2:
            continue
        err = np.abs(values[i:j + 1] - np.linspace(values[i], values[j], j - i + 1))
        k = int(np.argmax(err))
        if err[k] > tolerance:
            keep.add(i + k)
            todo += [(i, i + k), (i + k, j)]
    return sorted(keep)


def _runs(lo, hi, limit):
    """Cut frames into runs whose parts of the picture on screen (lo[f] to hi[f]) together span at
    most limit: [(first frame, end frame, span start, span end)]."""
    runs, f0, a, b = [], 0, lo[0], hi[0]
    for f in range(1, len(lo)):
        if max(b, hi[f]) - min(a, lo[f]) > limit:
            runs.append((f0, f, a, b))
            f0, a, b = f, lo[f], hi[f]
        else:
            a, b = min(a, lo[f]), max(b, hi[f])
    runs.append((f0, len(lo), a, b))
    return runs


class _Bundle:
    """An .otioz being written: media first, the timeline last. It only takes its name once complete."""

    def __init__(self, path: Path):
        self.path, self.part = path, path.with_name(path.name + ".part")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.zip = zipfile.ZipFile(self.part, "w", zipfile.ZIP_STORED, allowZip64=True)

    def image(self, name, img, quality) -> str:
        h, w = img.shape[:2]
        if max(h, w) > LONGEST:  # the keyframes don't change: Resolve fits any size the same way
            k = LONGEST / max(h, w)
            img = cv2.resize(img, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
        ok, data = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError(f"Couldn't save the picture {name}")
        self.zip.writestr(f"media/{name}", data.tobytes())
        return f"media/{name}"

    def file(self, name, src) -> str:
        self.zip.write(src, f"media/{name}")
        return f"media/{name}"

    def finish(self, timeline: dict):
        self.zip.writestr("content.otio", json.dumps(timeline, indent=4), zipfile.ZIP_DEFLATED)
        self.zip.writestr("version.txt", BUNDLE_VERSION, zipfile.ZIP_DEFLATED)
        self.zip.close()
        self.part.replace(self.path)

    def abandon(self):
        self.zip.close()
        self.part.unlink(missing_ok=True)


def reel_signature(items, settings, encoder_args) -> str:
    """Fingerprint of a chapter's video for editors (see video.piece_signature)."""
    blob = f"{REEL_VERSION}:{video.piece_signature(items, settings, encoder_args)}"
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _shots(seg, length, fps):
    """A segment's clips, one per scene: [(first frame, end frame, scene index)]."""
    if not isinstance(seg, video.StripSegment):
        return [(0, length, 0)]
    cuts = [0] + [min(length, max(0, round(t * fps))) for t in seg.bounds[1:-1]] + [length]
    return [(cuts[i], cuts[i + 1], i) for i in range(len(cuts) - 1) if cuts[i + 1] > cuts[i]]


def _lines(seg, item):
    """A segment's narration: [(seconds into it, voice file, samples, text, scene index)]."""
    if isinstance(seg, video.StripSegment):
        return [(at, item["scenes"][i]["audio"], len(a), text, i) for (at, a, text), i in zip(seg.events, seg.voiced)]
    return [(at, item["audio"], len(a), text, 0) for at, a, text in seg.events]


class _Export:
    def __init__(self, kind, settings, bundle):
        self.kind, self.settings, self.bundle = kind, settings, bundle
        self.W, self.H = video.frame_size(settings)
        self.fps = int(settings["fps"])
        self.tail = round(float(settings["transition_seconds"]) * self.fps)  # frames of a crossfade
        self.pictures, self.backdrops, self.voice, self.markers, self.subs = [], [], [], [], []
        self.pos = 0  # timeline frame where the next segment starts

    def chapter(self, n, ch, segs, lengths, lines, reel=None):
        """Add a chapter: its segments, each lengths[i] frames with narration lines[i] (see _lines),
        cut from reel (url, frames) when baked."""
        self.markers.append(_marker(ch["name"], self.pos, self.fps))
        reel_at = 0
        for i, (seg, item, length) in enumerate(zip(segs, ch["items"], lengths)):
            shots = _shots(seg, length, self.fps)
            fade = min(self.tail, shots[0][1]) if self.pos > 0 else 0  # never longer than the clip it fades into
            for at, wav, samples, text, scene in lines[i]:
                k = i + scene + 1
                start = self.pos + round(at * self.fps)
                frames = max(1, int(samples / SAMPLE_RATE * self.fps))
                url = self.bundle.file(f"ch{n:03d}_sc{k:03d}_voice.wav", wav)
                self.voice.append((start, _clip(f"{ch['name']} - scene {k} voice", url, 0, frames, frames, self.fps),
                                   frames))
                if text.strip():
                    self.subs.append((start / self.fps, start / self.fps + samples / SAMPLE_RATE, text.strip()))
            if reel:
                url, total = reel
                for s, (f0, f1, scene) in enumerate(shots):
                    name = f"{ch['name']} - scene {i + scene + 1}"
                    self.pictures.append((_clip(name, url, reel_at + f0, f1 - f0, total, self.fps),
                                          min(fade, f1 - f0) if s == 0 else 0))
                reel_at += length + self.tail
            elif isinstance(seg, video.StripSegment):
                self._strip(n, ch, seg, length, shots, fade)
            else:
                self._scene(n, ch, i + 1, seg, item, length, fade)
            self.pos += length

    def _scene(self, n, ch, k, seg, item, length, fade):
        """One scene's picture over its backdrop, moving as video.Clip moves it."""
        W, H, fps = self.W, self.H, self.fps
        pics = [im for im in (imread(p) for p in item["images"]) if im is not None]
        img = stack(pics) if pics else np.zeros((H // 2, W // 2, 3), np.uint8)
        clip = video.Clip(img, seg.duration, W, H, seg.motion, seg.focus)
        name, media, avail = f"{ch['name']} - scene {k}", f"ch{n:03d}_sc{k:03d}", length + self.tail
        url = self.bundle.image(f"{media}_backdrop.jpg", clip.bg, BACKDROP_QUALITY)  # fills the frame as it is
        self.backdrops.append((_clip(f"{name} backdrop", url, 0, length, avail, fps), fade))
        ph, pw = img.shape[:2]
        K = length  # frames the motion takes (it then holds, through the crossfade out)
        vertical = clip.mode == "pan_v"
        across = (ph, H) if vertical else (pw, W)
        if clip.mode in ("pan_v", "pan_h") and across[0] * clip.s_pan > MAX_SCREENS * across[1]:
            # too long a pan for one clip: cut it into clips, each with the part of the picture it shows
            s = clip.s_pan
            o = np.array([clip.placement(video._ease(f / K))[2 if vertical else 1] for f in range(avail)])
            size, screen = across
            lo, hi = np.clip(-o / s, 0, size), np.clip((screen - o) / s, 0, size)
            runs = _runs(lo[:length], hi[:length], MAX_SCREENS * screen / s)
            for r, (f0, f1, a, b) in enumerate(runs):
                end = avail if r == len(runs) - 1 else f1
                pad = int(np.ceil(MARGIN / s))
                a, b = max(0, int(np.floor(a)) - pad), min(size, int(np.ceil(b)) + pad)
                part = img[a:b] if vertical else img[:, a:b]
                c = o[f0:end] + (a + b) / 2 * s  # the part's center on screen, along the pan
                keys = {pid: [] for pid in TRANSFORM}
                for f in _keyframes(c, TOLERANCE):
                    cx, cy = (W / 2, c[f]) if vertical else (c[f], H / 2)
                    for pid, v in _place(W, H, part.shape[1], part.shape[0], s, s, cx, cy).items():
                        keys[pid].append((f, v))
                url = self.bundle.image(f"{media}_{r + 1}_picture.jpg", part, PICTURE_QUALITY)
                self.pictures.append((_clip(f"{name} ({r + 1} of {len(runs)})", url, 0, f1 - f0, end - f0, fps,
                                            [_transform(_fixed(keys))]), min(fade, f1 - f0) if r == 0 else 0))
            return
        start, finish = (_place(W, H, pw, ph, s, s, tx + pw * s / 2, ty + ph * s / 2)
                         for s, tx, ty in (clip.placement(0.0), clip.placement(1.0)))
        keys = {pid: [(0, start[pid])] if abs(finish[pid] - start[pid]) < 1e-9 else [(0, start[pid]), (K, finish[pid])]
                for pid in TRANSFORM}
        url = self.bundle.image(f"{media}_picture.jpg", img, PICTURE_QUALITY)
        self.pictures.append((_clip(name, url, 0, length, avail, fps, [_transform(keys, eased=True)]), fade))

    def _strip(self, n, ch, seg, length, shots, fade):
        """A scrolling chapter: per scene (cut further when long), the part of the strip it scrolls
        through, with the scroll as keyframes, over the same part blurred and stretched to the frame."""
        W, H, fps = self.W, self.H, self.fps
        frames = length + self.tail
        top = seg.top[np.minimum(np.arange(frames), len(seg.top) - 1)]
        bottom = np.minimum(seg.strip_h, top + H)
        o = np.maximum(0, (H - (bottom - top)) // 2) - top  # where the strip's first row is on screen
        cx = (W - seg.sw) // 2 + seg.sw / 2
        for s, (f0, f1, scene) in enumerate(shots):
            k = scene + 1
            last = f1 == length
            runs = _runs(top[f0:f1], bottom[f0:f1], MAX_SCREENS * H)
            for r, (g0, g1, a, b) in enumerate(runs):
                g0, g1 = f0 + g0, f0 + g1
                end = frames if last and r == len(runs) - 1 else g1
                a = max(seg.begin, int(np.floor(min(a, top[g0:end].min()))) - MARGIN)
                b = min(seg.strip_h, int(np.ceil(max(b, bottom[g0:end].max()))) + MARGIN)
                part = seg._rows(a, b)
                c = o[g0:end] + (a + b) / 2  # the part's center on screen
                small = cv2.resize(part, (max(1, W // 12), max(1, (b - a) // 12)), interpolation=cv2.INTER_AREA)
                back = cv2.convertScaleAbs(cv2.GaussianBlur(small, (0, 0), 3), alpha=0.35)
                bh, bw = back.shape[:2]
                pic_keys, back_keys = {pid: [] for pid in TRANSFORM}, {pid: [] for pid in TRANSFORM}
                for f in _keyframes(c, TOLERANCE):
                    for pid, v in _place(W, H, seg.sw, b - a, 1, 1, cx, c[f]).items():
                        pic_keys[pid].append((f, v))
                    for pid, v in _place(W, H, bw, bh, W / bw, (b - a) / bh, W / 2, c[f]).items():
                        back_keys[pid].append((f, v))
                tag = f"{k}" if len(runs) == 1 else f"{k} ({r + 1} of {len(runs)})"
                media = f"ch{n:03d}_sc{k:03d}" + ("" if len(runs) == 1 else f"_{r + 1}")
                first = min(fade, g1 - g0) if s == 0 and r == 0 else 0
                url = self.bundle.image(f"{media}_picture.jpg", part, PICTURE_QUALITY)
                self.pictures.append((_clip(f"{ch['name']} - scene {tag}", url, 0, g1 - g0, end - g0, fps,
                                            [_transform(_fixed(pic_keys))]), first))
                url = self.bundle.image(f"{media}_backdrop.jpg", back, BACKDROP_QUALITY)
                self.backdrops.append((_clip(f"{ch['name']} - scene {tag} backdrop", url, 0, g1 - g0, end - g0, fps,
                                             [_transform(_fixed(back_keys))]), first))

    def timeline(self, name) -> dict:
        fps = self.fps
        style = self.settings.get("editor_transition", "dissolve") if self.kind == "resolve" else "dissolve"
        cuts = sorted({at for entries in (self.pictures, self.backdrops) for at in _cuts(entries)})

        def lay(entries):
            out, at = [], 0
            for clip, fade in entries:
                if fade:
                    kind = MIXED[cuts.index(at) % len(MIXED)] if style == "mixed" else style
                    out.append(_dissolve(fade, fps, kind))
                out.append(clip)
                at += clip["source_range"]["duration"]["value"]
            return out
        tracks = ([("Video 1", self.backdrops), ("Video 2", self.pictures)] if self.kind == "resolve"
                  else [("Video 1", self.pictures)])
        children = [_track(t, "Video", lay(entries), {"Locked": False}) for t, entries in tracks]
        audio, at = [], 0
        for start, clip, frames in self.voice:
            start = max(start, at)  # a line rounded onto the end of the one before waits a frame
            if start > at:
                audio.append(_gap(start - at, fps))
            audio.append(clip)
            at = start + frames
        children.append(_track("Audio 1", "Audio", audio, {"Audio Type": "Mono", "Locked": False, "SoloOn": False}))
        return {"OTIO_SCHEMA": "Timeline.1", "metadata": {"Resolve_OTIO": {"Resolve OTIO Meta Version": "1.0"}},
                "name": name, "global_start_time": _time(3600 * fps, fps),  # 01:00:00:00, as editors start
                "tracks": {"OTIO_SCHEMA": "Stack.1", "metadata": {}, "name": "", "source_range": None, "effects": [],
                           "markers": self.markers, "enabled": True, "children": children}}


def _cuts(entries) -> list:
    """Where a track's transitions are: the frames at which a clip that fades in starts."""
    out, at = [], 0
    for clip, fade in entries:
        if fade:
            out.append(at)
        at += clip["source_range"]["duration"]["value"]
    return out


def _fixed(keys: dict) -> dict:
    """Parameters that don't change as a single value."""
    return {pid: pairs if any(abs(v - pairs[0][1]) > 1e-9 for _, v in pairs) else pairs[:1]
            for pid, pairs in keys.items()}


def export(chapters, kind, stem, name, out_dir: Path, cache_dir: Path, settings, progress=None, cancelled=None,
           status=None, between=None) -> dict:
    """chapters: [{"id", "name", "items", "weight"}] in order (as for video.render_chapters).
    Writes out_dir/"<stem> (for DaVinci Resolve).otioz" (or "... (for editors)" when baked) and its
    subtitles; name names the timeline. between() runs before each chapter (Pause takes effect there);
    status(note) says what's happening. Returns {"file", "srt", "seconds", "clips", "lines", "size",
    "made", "reused"}."""
    if kind not in KINDS:
        raise ValueError(f"Unknown kind of export: {kind}")
    W, H = video.frame_size(settings)
    fps = int(settings["fps"])
    path = out_dir / f"{stem} (for {'DaVinci Resolve' if kind == 'resolve' else 'editors'}).otioz"
    bundle = _Bundle(path)
    ex = _Export(kind, settings, bundle)
    total = sum(ch["weight"] for ch in chapters) or 1.0
    done, made, reused, kept = 0.0, 0, 0, {}
    encoder = video.chapter_encoder(settings) if kind == "baked" else None
    try:
        for n, ch in enumerate(chapters, 1):
            if between:
                between()
            if cancelled and cancelled():
                raise InterruptedError("cancelled")
            segs = [video.segment(it, settings, W, H) for it in ch["items"]]
            lengths = [max(1, round(seg.duration * fps)) for seg in segs]
            lines = [_lines(seg, item) for seg, item in zip(segs, ch["items"])]
            for seg in segs:
                seg.events = []  # the voice is copied as it is; drawing doesn't need it
            w0, w = done, ch["weight"]
            if kind == "baked":
                reel = None
                while reel is None:
                    base = cache_dir / f"{ch['id']}-{reel_signature(ch['items'], settings, encoder)}.mp4"
                    if base.exists():
                        reused += 1
                        reel = base
                        continue
                    gpu = video.is_hardware(encoder)
                    workers = video.drawing_processes(settings, gpu)
                    if status:
                        status(f"{'GPU' if gpu else 'CPU'} encoder, {workers} drawing process"
                               f"{'es' if workers > 1 else ''}; {ch['name']}, {n} of {len(chapters)}")
                    try:
                        video.render_reel(segs, [x + ex.tail for x in lengths], base, settings, encoder, workers,
                                          (lambda f: progress(0.98 * (w0 + f * w) / total)) if progress else None,
                                          cancelled)
                    except video._EncoderFailed as e:  # only an encoder that fails to start is swapped
                        if not video.is_hardware(encoder) or e.frames > fps:
                            raise RuntimeError(str(e)) from None
                        log.warning("GPU video encoding didn't start, using the CPU instead: %s", e)
                        encoder = video.CPU_ENCODER
                        for seg in segs:
                            seg.release()
                        continue
                    made += 1
                    reel = base
                kept[ch["id"]] = reel.name
                url = bundle.file(f"ch{n:03d}_video.mp4", reel)
                ex.chapter(n, ch, segs, lengths, lines, reel=(url, sum(lengths) + ex.tail * len(lengths)))
            else:
                if status:
                    status(f"{ch['name']}, {n} of {len(chapters)}")
                ex.chapter(n, ch, segs, lengths, lines)
            for seg in segs:
                seg.release()
            done += w
            if progress:
                progress(0.98 * done / total)
        bundle.finish(ex.timeline(name))
    except BaseException:
        bundle.abandon()
        raise
    srt = path.with_suffix(".srt")
    srt.write_text("\n".join(f"{i}\n{video._srt_time(a)} --> {video._srt_time(b)}\n{text}\n"
                             for i, (a, b, text) in enumerate(ex.subs, 1)), encoding="utf-8")
    _clean_cache(cache_dir, kept)
    if progress:
        progress(1.0)
    return {"file": path, "srt": srt, "seconds": ex.pos / fps, "clips": len(ex.pictures), "lines": len(ex.voice),
            "size": path.stat().st_size, "made": made, "reused": reused}


def _clean_cache(cache_dir: Path, current: dict):
    """Delete these chapters' videos made for an earlier version of them (other chapters' are kept)."""
    if not cache_dir.exists():
        return
    for f in cache_dir.iterdir():
        cid = f.name.split("-", 1)[0]
        if cid in current and f.name != current[cid]:
            f.unlink(missing_ok=True)
