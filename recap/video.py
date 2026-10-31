"""Video export: pan/zoom over each panel, crossfades, narration audio, subtitles.

Frames are drawn with OpenCV, in several processes at once when there are cores to
spare, and piped in order into one ffmpeg. ffmpeg encodes on the GPU when it can (NVIDIA's
NVENC, AMD's AMF or Intel's Quick Sync), and on the CPU otherwise.
"""
import bisect
import functools
import hashlib
import json
import logging
import multiprocessing as mp
import os
import queue
import shutil
import subprocess
import threading
import time
import traceback
from multiprocessing import shared_memory
from pathlib import Path

import cv2
import numpy as np
import soundfile as sf

from .panels import imread
from .scenes import stack
from .tts import SAMPLE_RATE

log = logging.getLogger(__name__)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CPU_ENCODER = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
# NVIDIA's hardware encoder. On a 1080p webtoon export it ran ~2.5x faster than CPU_ENCODER,
# with a slightly closer match to the source (SSIM) and a ~13% smaller file.
GPU_ENCODER = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0"]
# AMD's (AMF) and Intel's (Quick Sync) hardware encoders, at about the same quality: constant quality,
# no bitrate target. Not measured here (this PC has only an NVIDIA card); each is tried on a few
# frames first and the CPU is used when it doesn't work (see hardware_encoder).
AMD_ENCODER = ["-c:v", "h264_amf", "-usage", "transcoding", "-quality", "balanced", "-rc", "cqp",
               "-qp_i", "21", "-qp_p", "23"]
INTEL_ENCODER = ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "23", "-look_ahead", "0"]
# tried in this order when the encoder is Automatic: (who makes it, its arguments)
HARDWARE = {"nvidia": ("NVIDIA", GPU_ENCODER), "amd": ("AMD", AMD_ENCODER), "intel": ("Intel", INTEL_ENCODER)}
# drawing in several processes (see _encode_parallel)
BLOCK = 30   # frames a drawing process draws in a row before the next one takes over
SLOTS = 4    # frames each drawing process can have ready ahead of ffmpeg
PARALLEL_PIX_FMT = "yuv420p"  # drawing processes convert to the video's colour format themselves
# automatic: (at most, one per this many cores). Measured at 1080p on 12 cores: 2 processes gave
# ~12% (GPU encoder) and ~8% (CPU encoder) over 1; more gave no more, as the encoder is then the limit
AUTO_PROCESSES = {"gpu": (2, 4), "cpu": (2, 6)}
# joining the chapters takes about half as long as drawing them (5 h 37 min of 720p video: drawn in 15 minutes
# on the GPU, joined in 7½, mostly encoding the narration on one CPU thread); the app passes its own measure
JOIN_COST = 0.5


def find_ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


@functools.lru_cache(maxsize=None)
def _encodes(ffmpeg: str, args: tuple) -> bool:
    """True when this ffmpeg can encode a few frames with these encoder arguments (a hardware encoder
    needs a build that has it, the maker's card and its driver)."""
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                            "-i", "color=c=black:s=256x256:r=30", "-frames:v", "3", *args,
                            "-pix_fmt", output_pix_fmt(list(args)), "-f", "null", "-"],
                           capture_output=True, timeout=30, creationflags=NO_WINDOW)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def hardware_encoder(ffmpeg: str | None) -> str | None:
    """Which GPU's encoder works here ("nvidia", "amd" or "intel"), or None (then the CPU encodes)."""
    return next((k for k, (_, args) in HARDWARE.items() if ffmpeg and _encodes(ffmpeg, tuple(args))), None)


def is_hardware(encoder_args) -> bool:
    return encoder_args is not CPU_ENCODER and list(encoder_args) != CPU_ENCODER


def output_pix_fmt(encoder_args) -> str:
    """The video's colour format: Intel's encoder only takes NV12 (the same 4:2:0 layout, interleaved)."""
    return "nv12" if list(encoder_args)[:2] == ["-c:v", "h264_qsv"] else "yuv420p"


def _ease(p: float) -> float:
    p = min(1.0, max(0.0, p))
    return p * p * (3 - 2 * p)


def _srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


FOCUS_ZOOM = 1.4  # zooming toward a scene's subject goes at most this far past fitting the frame


class Clip:
    def __init__(self, img: np.ndarray, duration: float, W: int, H: int, motion: bool, focus=None):
        """focus: the scene's subject [center x, center y, width, height] as fractions of the picture (see
        focus.py); a scene that zooms then zooms in toward it instead of the middle."""
        self.duration = duration
        self.W, self.H = W, H
        ph, pw = img.shape[:2]
        margin = 0.9
        fit = min(W * margin / pw, H * margin / ph)
        # pan scale: fill the frame's width (or height), but keep at least a
        # roughly square window of the panel in view so it isn't over-zoomed
        scale_w = max(fit, min(W * margin / pw, H * margin / (0.75 * pw)))
        scale_h = max(fit, min(H * margin / ph, W * margin / (0.75 * ph)))
        if motion and ph * scale_w > H * 1.25:
            self.mode, s_max = "pan_v", min(scale_w, 3.0)
        elif motion and pw * scale_h > W * 1.25:
            self.mode, s_max = "pan_h", min(scale_h, 3.0)
        elif motion:
            self.mode, s_max = "zoom", fit * 1.06
        else:
            self.mode, s_max = "static", fit
        self.fit = fit
        self.end = None  # zooming toward the subject: (scale, picture center on screen) at the end
        if self.mode == "zoom" and focus:
            fx, fy, fw, fh = focus

            def center(size, screen, f, s):  # along one side: the subject toward the middle, the picture
                span = size * s              # staying inside the frame where it fits, covering it where it doesn't
                left = min(max(0.0, screen - span), max(min(0.0, screen - span), screen / 2 - f * span))
                return left + span / 2

            def off(s, cx, cy):  # how far the subject is from the middle of the frame
                return np.hypot(cx + (fx - 0.5) * pw * s - W / 2, cy + (fy - 0.5) * ph * s - H / 2)
            # in far enough that the subject fills about 3/4 of the frame, within limits; less when that
            # would carry a subject near the picture's edge further from the middle than it starts
            s1 = min(fit * FOCUS_ZOOM, max(fit * 1.06, fit * 0.75 / max(fw, fh, 1e-3)))
            start = off(fit, W / 2, H / 2)
            while s1 > fit * 1.06 and off(s1, center(pw, W, fx, s1), center(ph, H, fy, s1)) > start:
                s1 = max(fit * 1.06, s1 * 0.97)
            self.end = (s1, center(pw, W, fx, s1), center(ph, H, fy, s1))
            s_max = s1
        # pre-shrink big sources once so per-frame warps stay cheap
        pre = min(1.0, s_max * 1.05)
        if pre < 1.0:
            img = cv2.resize(img, (max(1, int(pw * pre)), max(1, int(ph * pre))), interpolation=cv2.INTER_AREA)
        self.img, self.pre = img, pre
        self.pw, self.ph = pw, ph
        self.s_pan = s_max
        # blurred, darkened backdrop that fills the frame
        cover = max(W / pw, H / ph)
        small = cv2.resize(img, (max(1, int(pw * cover / 8)), max(1, int(ph * cover / 8))), interpolation=cv2.INTER_AREA)
        small = cv2.GaussianBlur(small, (0, 0), 6)
        bg = cv2.resize(small, (int(pw * cover) + 2, int(ph * cover) + 2), interpolation=cv2.INTER_LINEAR)
        y0 = (bg.shape[0] - H) // 2
        x0 = (bg.shape[1] - W) // 2
        self.bg = (bg[y0:y0 + H, x0:x0 + W].astype(np.float32) * 0.4).astype(np.uint8)
        self._still = None  # a static clip's picture, drawn once

    def frame(self, t: float, out: np.ndarray | None = None) -> np.ndarray:
        """The picture at time t, drawn into out (H x W x 3) when given."""
        if self._still is not None:
            if out is None:
                return self._still.copy()
            np.copyto(out, self._still)
            return out
        W, H, pw, ph = self.W, self.H, self.pw, self.ph
        s, tx, ty = self.placement(_ease(t / self.duration) if self.duration > 0 else 1.0)
        k = s / self.pre  # scale relative to the pre-shrunk image
        if out is None:
            out = self.bg.copy()
        else:
            np.copyto(out, self.bg)
        x0, y0 = max(0, int(np.ceil(tx))), max(0, int(np.ceil(ty)))
        x1, y1 = min(W, int(tx + pw * s)), min(H, int(ty + ph * s))
        if x1 > x0 and y1 > y0:  # warp only the part of the frame the panel covers
            M = np.float32([[k, 0, tx - x0], [0, k, ty - y0]])
            out[y0:y1, x0:x1] = cv2.warpAffine(self.img, M, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR,
                                               borderMode=cv2.BORDER_CONSTANT)
        if self.mode == "static":
            self._still = out.copy()
        return out

    def placement(self, p: float) -> tuple[float, float, float]:
        """Where the panel is at eased progress p (0 to 1): its scale (screen pixels per panel
        pixel) and the screen position of its top-left corner."""
        W, H, pw, ph = self.W, self.H, self.pw, self.ph
        if self.mode == "pan_v":
            s = self.s_pan
            top, bottom = H * 0.05, H * 0.95 - ph * s
            return s, (W - pw * s) / 2, top + (bottom - top) * p
        if self.mode == "pan_h":
            s = self.s_pan
            left, right = W * 0.05, W * 0.95 - pw * s
            return s, left + (right - left) * p, (H - ph * s) / 2
        if self.end:  # scale and the picture's center both move evenly, as an editor's keyframes would
            s1, cx, cy = self.end
            s = self.fit + (s1 - self.fit) * p
            x, y = W / 2 + (cx - W / 2) * p, H / 2 + (cy - H / 2) * p
            return s, x - pw * s / 2, y - ph * s / 2
        s = self.fit * (1 + 0.06 * p) if self.mode == "zoom" else self.fit
        return s, (W - pw * s) / 2, (H - ph * s) / 2


def _load_audio(path) -> np.ndarray | None:
    if not path or not Path(path).exists():
        return None
    a, _ = sf.read(path, dtype="float32")
    return a.mean(axis=1) if a.ndim > 1 else a


class SceneSegment:
    """One scene shown as a still with pan/zoom: its pieces stacked back together."""

    def __init__(self, item, settings, W, H):
        lead = float(settings["transition_seconds"]) * 0.5
        audio = _load_audio(item.get("audio"))
        alen = len(audio) / SAMPLE_RATE if audio is not None else 0.0
        self.duration = max(float(settings["min_panel_seconds"]), alen + lead + float(settings["pad_seconds"]))
        # (time in segment, samples, subtitle text) for each narration line
        self.events = [(lead, audio, item.get("narration", ""))] if audio is not None else []
        self.images, self.W, self.H, self.motion = item["images"], W, H, scene_motion(settings)
        self.focus = item.get("focus")
        self._clip = None

    def frame(self, t, out=None):
        if self._clip is None:
            self._clip = Clip(stack([imread(p) for p in self.images]), self.duration, self.W, self.H, self.motion,
                              self.focus)
        return self._clip.frame(t, out)

    def release(self):
        self._clip = None

    def __getstate__(self):  # sent to drawing processes without loaded images
        return {**self.__dict__, "_clip": None}


class StripSegment:
    """A webtoon chapter as one strip, scrolled top to bottom.

    Each scene's narration plays while the middle of the screen passes through that
    scene, so the scroll slows down for long lines and speeds up through quiet parts.
    """
    MAX_SPEED = 600     # screen pixels per second while a line is read (at 1080p; scaled for others)
    QUIET_SPEED = 350   # scenes without narration
    SKIP_SPEED = 1800   # scenes left out of the video
    LEAD, START_HOLD, END_HOLD = 0.12, 0.8, 1.0

    def __init__(self, item, settings, W, H):
        """item: {"pages": [(path, w, h)], "scenes": [{"y0", "y1", "audio", "narration", "enabled"}]},
        scene rows given in the strip with every page scaled to the first page's width."""
        self.W, self.H, self.fps = W, H, int(settings["fps"])
        self.sw = W if H > W else int(W * 0.42) // 2 * 2  # strip width on screen
        base = item["pages"][0][1]
        self.scale = self.sw / base
        self.paths = [path for path, _, _ in item["pages"]]
        self.heights = [max(1, round(h * base / w * self.scale)) for _, w, h in item["pages"]]
        self.offsets = np.cumsum([0] + self.heights)
        self.strip_h = int(self.offsets[-1])
        self.cache: dict[int, np.ndarray] = {}

        scenes = item["scenes"]
        spans = [(sc["y0"] * self.scale, sc["y1"] * self.scale) for sc in scenes]
        # make scenes contiguous: the gutter between two scenes is split between them
        edges = [0.0] + [(spans[i][1] + spans[i + 1][0]) / 2 for i in range(len(spans) - 1)] + [float(self.strip_h)]
        pad = float(settings["pad_seconds"])
        px = min(W, H) / 1080  # speeds are for 1080p: the same pace through the story at any size
        # scenes left out (the scan group's credits and ads, or unticked) aren't shown at all: the strip the
        # video scrolls is the scenes used, each stretch of them joined straight onto the next. Rows below
        # are in that strip; self.runs places its stretches in the whole one
        on = [i for i, sc in enumerate(scenes) if sc.get("enabled", True)]
        shown = on or list(range(len(scenes)))  # (none used: all of it, left-out scenes passing quickly)
        self.runs, at = [], {}  # (first row, last row + 1, where it starts in the strip shown); scene -> run
        for i in shown:
            lo, hi = int(np.ceil(edges[i] - 1e-6)), int(np.floor(edges[i + 1] + 1e-6))
            if self.runs and at.get(i - 1) == len(self.runs) - 1:
                a, _, v = self.runs[-1]
                self.runs[-1] = (a, hi, v)
            else:
                self.runs.append((lo, hi, self.runs[-1][2] + self.runs[-1][1] - self.runs[-1][0] if self.runs else 0))
            at[i] = len(self.runs) - 1

        def row(i, y):  # where row y of the whole strip, in scene i, is in the strip shown
            a, b, v = self.runs[at[i]]
            return v + min(max(y, a), b) - a
        self.begin, self.strip_h = 0, self.runs[-1][2] + self.runs[-1][1] - self.runs[-1][0]
        t = self.START_HOLD
        top0 = row(shown[0], edges[shown[0]])
        keys_t, keys_y, self.events = [0.0, t], [top0, top0], []
        self.voiced = []  # the scene each of events belongs to
        ends = []
        for i, sc in enumerate(scenes):
            if i in at:
                height = max(1.0, edges[i + 1] - edges[i])
                audio = _load_audio(sc.get("audio")) if sc.get("enabled", True) else None
                if not sc.get("enabled", True):
                    d = height / (self.SKIP_SPEED * px)
                elif audio is not None:
                    d = max(self.LEAD + len(audio) / SAMPLE_RATE + pad, height / (self.MAX_SPEED * px))
                    self.events.append((t + self.LEAD, audio, sc.get("narration", "")))
                    self.voiced.append(i)
                else:
                    d = max(0.5, height / (self.QUIET_SPEED * px))
                if keys_y[-1] != row(i, edges[i]):  # past a stretch left out: carry on from where this starts
                    keys_t.append(t)
                    keys_y.append(row(i, edges[i]))
                t += d
                keys_t.append(t)
                keys_y.append(row(i, edges[i + 1]))
            ends.append(t)  # (a scene not shown takes no time)
        self.duration = t + self.END_HOLD
        # scene i passes the middle of the screen from bounds[i] to bounds[i + 1]
        self.bounds = [self.START_HOLD] + ends + [self.duration]
        keys_t.append(self.duration)
        keys_y.append(keys_y[-1])
        # reading line (middle of the screen) per frame, eased so speed changes are gentle
        n = int(self.duration * self.fps) + 1
        y = np.interp(np.arange(n) / self.fps, keys_t, keys_y)
        sigma = 0.3 * self.fps
        k = np.exp(-0.5 * (np.arange(-int(3 * sigma), int(3 * sigma) + 1) / sigma) ** 2)
        y = np.convolve(np.pad(y, len(k) // 2, mode="edge"), k / k.sum(), mode="valid")
        self.top = np.clip(y - H / 2, self.begin, max(self.begin, self.strip_h - H))

    def _page(self, k):
        if k not in self.cache:
            img = imread(self.paths[k])
            if img is None:
                img = np.zeros((self.heights[k], self.sw, 3), np.uint8)
            self.cache[k] = cv2.resize(img, (self.sw, self.heights[k]), interpolation=cv2.INTER_AREA)
        return self.cache[k]

    def _shown(self, a, b):
        """Rows a to b of the strip shown, as stretches (first row, last row + 1) of the whole one."""
        return [(lo + s - v, lo + e - v) for lo, hi, v in self.runs
                for s, e in [(max(a, v), min(b, v + hi - lo))] if s < e]

    def _rows(self, a, b):
        parts, spans = [], self._shown(a, b)
        for ra, rb in spans:
            for k in range(len(self.paths)):
                lo, hi = max(ra, self.offsets[k]), min(rb, self.offsets[k + 1])
                if lo < hi:
                    parts.append(self._page(k)[lo - self.offsets[k]:hi - self.offsets[k]])
        for k in [k for k in self.cache
                  if all(self.offsets[k + 1] < ra - self.H or self.offsets[k] > rb + self.H for ra, rb in spans)]:
            del self.cache[k]  # keep only pages near the view
        return np.vstack(parts)

    def frame(self, t, out=None):
        """The picture at time t, drawn into out (H x W x 3) when given."""
        W, H, sw = self.W, self.H, self.sw
        # (the tiny nudge stops rounding, e.g. 3/30 * 30 = 2.9999…, from repeating one frame and skipping the next)
        idx = min(int(t * self.fps + 1e-6), len(self.top) - 1)
        top = int(round(self.top[idx]))
        bottom = min(self.strip_h, top + H)
        x0, y0 = (W - sw) // 2, max(0, (H - (bottom - top)) // 2)
        # blurred, darkened backdrop from what's on screen; it changes slowly, so it is
        # built small and refreshed every 3 frames (from the view at that frame, so the
        # picture depends only on the frame, whichever process draws it)
        key = idx - idx % 3
        if getattr(self, "_bg_idx", None) != key:
            ktop = int(round(self.top[key]))
            small = cv2.resize(self._rows(ktop, min(self.strip_h, ktop + H)), (max(1, W // 12), max(1, H // 12)),
                               interpolation=cv2.INTER_AREA)
            small = cv2.convertScaleAbs(cv2.GaussianBlur(small, (0, 0), 3), alpha=0.35)
            self._bg, self._bg_idx = cv2.resize(small, (W, H), interpolation=cv2.INTER_LINEAR), key
        bg = self._bg
        if out is None:
            out = np.empty((H, W, 3), np.uint8)
        # backdrop around the strip, then the strip's rows copied straight from the pages
        out[:, :x0] = bg[:, :x0]
        out[:, x0 + sw:] = bg[:, x0 + sw:]
        out[:y0, x0:x0 + sw] = bg[:y0, x0:x0 + sw]
        out[y0 + bottom - top:, x0:x0 + sw] = bg[y0 + bottom - top:, x0:x0 + sw]
        y = y0
        for ra, rb in self._shown(top, bottom):
            for k in range(len(self.paths)):
                lo, hi = max(ra, self.offsets[k]), min(rb, self.offsets[k + 1])
                if lo < hi:
                    out[y + lo - ra:y + hi - ra, x0:x0 + sw] = self._page(k)[lo - self.offsets[k]:hi - self.offsets[k]]
            y += rb - ra
        return out

    def release(self):
        self.cache.clear()

    def __getstate__(self):  # sent to drawing processes without loaded pages
        return {**self.__dict__, "cache": {}, "_bg": None, "_bg_idx": None}


class _Timeline:
    """Which segment is on screen at each frame, with crossfades between segments.
    A frame's picture depends only on its number, so any process can draw any frame."""

    def __init__(self, segments, fps: int, tr: float, fade: int = 0):
        """fade: frames to fade in from black at the start and out to black at the end (a chapter piece)."""
        self.segments, self.fps, self.tr, self.fade = segments, fps, tr, fade
        self.durations = [s.duration for s in segments]
        self.starts = list(np.concatenate([[0.0], np.cumsum(self.durations)[:-1]]))
        self.frames = int(float(sum(self.durations)) * fps)
        self._released = 0  # segments before this one have had their images freed

    def draw(self, f: int, out: np.ndarray, spare: np.ndarray) -> np.ndarray:
        """Frame f drawn into out; spare holds the previous segment's picture during a crossfade."""
        t = f / self.fps
        i = max(0, bisect.bisect_right(self.starts, t) - 1)
        while self._released < i - 1:  # free images of segments already shown
            self.segments[self._released].release()
            self._released += 1
        local = t - self.starts[i]
        frame = self.segments[i].frame(local, out)
        if i > 0 and self.tr > 0 and local < self.tr:
            prev = self.segments[i - 1].frame(self.durations[i - 1] + local, spare)
            a = _ease(local / self.tr)
            cv2.addWeighted(prev, 1 - a, frame, a, 0, dst=frame)
        if self.fade and (f < self.fade or f >= self.frames - self.fade):
            k = min(1.0, (f + 0.5) / self.fade, (self.frames - f - 0.5) / self.fade)
            cv2.convertScaleAbs(frame, dst=frame, alpha=_ease(max(0.0, k)))
        return frame

    def release(self):
        for seg in self.segments:
            seg.release()
        self._released = 0


class _Reel:
    """Segments one after another with nothing joining them, each drawn for its given number of
    frames (a video editor's crossfade uses a segment's frames past where the next one starts).
    Draws like _Timeline, so the same encoders take it."""

    def __init__(self, segments, lengths, fps: int):
        self.segments, self.fps = segments, fps
        self.starts = [int(x) for x in np.cumsum([0] + list(lengths[:-1]))]
        self.frames = int(sum(lengths))
        self._released = 0

    def draw(self, f: int, out: np.ndarray, spare: np.ndarray) -> np.ndarray:
        i = bisect.bisect_right(self.starts, f) - 1
        while self._released < i:  # free images of segments already drawn
            self.segments[self._released].release()
            self._released += 1
        return self.segments[i].frame((f - self.starts[i]) / self.fps, out)

    def release(self):
        for seg in self.segments:
            seg.release()
        self._released = 0


def segment(item, settings, W, H):
    """What draws one of render()'s items."""
    return StripSegment(item, settings, W, H) if item.get("type") == "strip" else SceneSegment(item, settings, W, H)


RESOLUTIONS = {1080: (1920, 1080), 720: (1280, 720), 480: (854, 480), 360: (640, 360)}


def frame_size(settings) -> tuple[int, int]:
    w, h = RESOLUTIONS.get(int(settings.get("resolution") or 1080), RESOLUTIONS[1080])
    return (w, h) if settings["orientation"] == "landscape" else (h, w)


def scene_motion(settings) -> bool:
    """Whether scenes shown one at a time pan and zoom (see the video_style setting)."""
    style = settings.get("video_style", "auto")
    return style == "motion" or (style == "auto" and bool(settings.get("motion", True)))


def drawing_processes(settings: dict, gpu: bool) -> int:
    """How many processes draw video frames. 0 in settings means automatic: enough to keep the
    GPU encoder busy, or fewer on the CPU encoder, which itself needs most of the cores."""
    n = int(settings.get("video_workers", 0) or 0)
    if n > 0:
        return n
    cores = os.cpu_count() or 4
    most, cores_each = AUTO_PROCESSES["gpu" if gpu else "cpu"]
    return max(1, min(most, cores // cores_each))


def render(items, out_dir: Path, settings: dict, progress=None, cancelled=None, stem: str = "recap",
           status=None):
    """items, in order: {"type": "scene", "images": [piece paths], "audio", "narration"} for a
    scene shown as a still, or {"type": "strip", "pages": [...], "scenes": [...]} for a webtoon
    chapter that scrolls (see StripSegment). Consecutive items crossfade.
    status: called with a short note on how the video is being made, e.g. "GPU encoder, 3 drawing processes".

    Writes <stem>.mp4, <stem>.srt and <stem>.wav into out_dir; returns (video_path, srt_path).
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Install it or run setup.ps1.")
    W, H = frame_size(settings)
    fps = int(settings["fps"])
    timeline = _Timeline([segment(it, settings, W, H) for it in items], fps, float(settings["transition_seconds"]))
    total = float(sum(timeline.durations))

    # audio track + subtitles
    track = np.zeros(int(total * SAMPLE_RATE) + SAMPLE_RATE, dtype=np.float32)
    srt = []
    for seg, st in zip(timeline.segments, timeline.starts):
        for at, a, text in seg.events:
            o = int((st + at) * SAMPLE_RATE)
            track[o:o + len(a)] += a[: len(track) - o]
            if text.strip():
                srt.append(f"{len(srt) + 1}\n{_srt_time(st + at)} --> {_srt_time(st + at + len(a) / SAMPLE_RATE)}\n"
                           f"{text.strip()}\n")
        seg.events = []  # samples are in the track now
    np.clip(track, -1, 1, out=track)
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / f"{stem}.wav"
    sf.write(wav, track, SAMPLE_RATE)
    del track  # can be gigabytes for a long video; ffmpeg reads it from the file
    srt_path = out_dir / f"{stem}.srt"
    srt_path.write_text("\n".join(srt), encoding="utf-8")

    video = out_dir / f"{stem}.mp4"
    first = chapter_encoder(settings)
    encoders = [first, CPU_ENCODER] if is_hardware(first) else [CPU_ENCODER]
    for n, encoder_args in enumerate(encoders):
        gpu = is_hardware(encoder_args)
        workers = drawing_processes(settings, gpu)

        def cmd(pix_fmt):
            return [ffmpeg, "-y", "-loglevel", "error",
                    "-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
                    "-i", str(wav),
                    *encoder_args, "-pix_fmt", output_pix_fmt(encoder_args),
                    "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
                    str(video)]

        def tell(processes):
            if status:
                status(f"{'GPU' if gpu else 'CPU'} encoder, {processes} drawing process{'es' if processes > 1 else ''}")
        tell(workers)
        try:
            if workers > 1:
                try:
                    _encode_parallel(cmd(PARALLEL_PIX_FMT), PARALLEL_PIX_FMT, timeline, W, H, workers,
                                     progress, cancelled)
                except _WorkersFailed as e:  # e.g. processes can't be started here: draw in this one
                    log.warning("Couldn't draw video frames in several processes, using one: %s", e)
                    timeline.release()
                    tell(1)
                    _encode(cmd("bgr24"), timeline, W, H, progress, cancelled)
            else:
                _encode(cmd("bgr24"), timeline, W, H, progress, cancelled)
            break
        except _EncoderFailed as e:
            if n + 1 == len(encoders) or e.frames > fps:  # only an encoder that fails to start is swapped
                raise RuntimeError(str(e)) from None
            log.warning("GPU video encoding didn't start, using the CPU instead: %s", e)
            timeline.release()
    return video, srt_path


class _EncoderFailed(RuntimeError):
    def __init__(self, message, frames):
        super().__init__(message)
        self.frames = frames  # frames handed to ffmpeg before it failed


class _WorkersFailed(RuntimeError):
    """The drawing processes couldn't start (nothing was drawn)."""


def _encode(cmd, timeline, W, H, progress, cancelled):
    """Draw every frame in this process and pipe it into ffmpeg. Frames are drawn into a few
    reused buffers while a writer thread feeds ffmpeg, so drawing and encoding overlap."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    free, ready = queue.Queue(), queue.Queue()
    for _ in range(4):
        free.put(np.empty((H, W, 3), np.uint8))
    spare = np.empty((H, W, 3), np.uint8)
    broken = []

    def write():
        while (frame := ready.get()) is not None:
            if not broken:
                try:
                    proc.stdin.write(memoryview(frame))
                except OSError as e:  # ffmpeg stopped reading (it failed or was killed)
                    broken.append(e)
            free.put(frame)

    writer = threading.Thread(target=write, daemon=True, name="video-writer")
    writer.start()
    total_frames = timeline.frames
    f = 0
    try:
        for f in range(total_frames):
            if cancelled and cancelled():
                raise InterruptedError("cancelled")
            if broken:
                break
            ready.put(timeline.draw(f, free.get(), spare))
            if progress and f % timeline.fps == 0:
                progress(f / max(1, total_frames))
        ready.put(None)
        writer.join()
        try:
            proc.stdin.close()
        except OSError as e:
            broken.append(e)
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0 or broken:
            raise _EncoderFailed(f"ffmpeg failed: {err[-800:] or broken[0]}", f)
    except BaseException:
        ready.put(None)
        proc.kill()
        writer.join(timeout=10)  # a write blocked on ffmpeg fails once it is killed
        for pipe in (proc.stdin, proc.stderr):
            try:
                pipe.close()
            except OSError:
                pass
        raise


def _encode_parallel(cmd, pix_fmt, timeline, W, H, workers, progress, cancelled):
    """Draw frames in several processes and pipe them into ffmpeg in order. Process k draws
    blocks k, k + workers, k + 2 * workers, ... of BLOCK frames into its own SLOTS in shared
    memory; this process only hands each finished frame to ffmpeg, so drawing isn't limited
    to one core. The pictures are the same as drawing them all here."""
    yuv = pix_fmt == "yuv420p"
    fb = H * W * 3 // 2 if yuv else H * W * 3
    ctx = mp.get_context("spawn")
    shm = shared_memory.SharedMemory(create=True, size=workers * SLOTS * fb)
    free = [ctx.Semaphore(SLOTS) for _ in range(workers)]
    filled = [ctx.Semaphore(0) for _ in range(workers)]
    stop, errors = ctx.Event(), ctx.Queue()
    procs, proc, f = [], None, 0
    try:
        try:
            for k in range(workers):
                pr = ctx.Process(target=_draw_frames, name=f"video-draw-{k}", daemon=True,
                                 args=(k, workers, shm.name, (H, W), yuv, timeline, free[k], filled[k], stop, errors))
                pr.start()
                procs.append(pr)
        except Exception as e:
            raise _WorkersFailed(f"{type(e).__name__}: {e}") from e
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        used = [0] * workers
        broken = None
        n = timeline.frames
        for f in range(n):
            k = (f // BLOCK) % workers
            while not filled[k].acquire(timeout=0.25):
                if cancelled and cancelled():
                    raise InterruptedError("cancelled")
                if not errors.empty() or not procs[k].is_alive():
                    try:
                        why = errors.get(timeout=1)
                    except queue.Empty:
                        why = f"drawing process {k} stopped (exit code {procs[k].exitcode})"
                    if f == 0:
                        raise _WorkersFailed(why)
                    raise RuntimeError(f"Drawing video frames failed: {why}")
            if cancelled and f % timeline.fps == 0 and cancelled():
                raise InterruptedError("cancelled")
            off = (k * SLOTS + used[k] % SLOTS) * fb
            try:
                proc.stdin.write(shm.buf[off:off + fb])
            except OSError as e:  # ffmpeg stopped reading
                broken = e
                break
            used[k] += 1
            free[k].release()
            if progress and f % timeline.fps == 0:
                progress(f / max(1, n))
        try:
            proc.stdin.close()
        except OSError as e:
            broken = broken or e
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0 or broken:
            raise _EncoderFailed(f"ffmpeg failed: {err[-800:] or broken}", f)
    finally:
        stop.set()
        if proc and proc.poll() is None:  # stopped early: cancelled or failed
            proc.kill()
            proc.wait()
        for pr in procs:
            pr.join(timeout=5)
            if pr.is_alive():
                pr.terminate()
        if proc:
            for pipe in (proc.stdin, proc.stderr):
                try:
                    pipe.close()
                except OSError:
                    pass
        try:
            shm.close()
        except BufferError:
            pass
        shm.unlink()


def _draw_frames(k, workers, shm_name, shape, yuv, timeline, free, filled, stop, errors):
    """A drawing process (see _encode_parallel)."""
    shm = shared_memory.SharedMemory(name=shm_name)
    H, W = shape
    fb = H * W * 3 // 2 if yuv else H * W * 3
    slots = [np.ndarray((H * 3 // 2, W) if yuv else (H, W, 3), np.uint8, shm.buf, (k * SLOTS + s) * fb)
             for s in range(SLOTS)]
    bgr, spare = np.empty((H, W, 3), np.uint8), np.empty((H, W, 3), np.uint8)
    slot = None
    try:
        c = 0
        for start in range(k * BLOCK, timeline.frames, workers * BLOCK):
            for f in range(start, min(timeline.frames, start + BLOCK)):
                while not free.acquire(timeout=0.5):
                    if stop.is_set():
                        return
                slot = slots[c % SLOTS]
                if yuv:
                    cv2.cvtColor(timeline.draw(f, bgr, spare), cv2.COLOR_BGR2YUV_I420, dst=slot)
                else:
                    timeline.draw(f, slot, spare)
                c += 1
                filled.release()
    except BaseException:
        errors.put(traceback.format_exc()[-1500:])
    finally:
        slots = slot = None  # views on the shared memory must go before it closes
        try:
            shm.close()
        except BufferError:
            pass


# ---- rendering by chapter -----------------------------------------------------
# A project's video is made one chapter at a time. Each chapter becomes a "piece" (its video, its
# narration and its subtitles), kept in the project's render folder under a fingerprint of all it
# depends on: a stopped render picks up where it left off, and after an edit only the chapters that
# changed are made again. The pieces are then joined, without encoding the video again, into one
# video or into parts (for a site's length limit), each with subtitles and a YouTube chapter list.
# Each chapter fades in from black and out to black, so pieces join cleanly whenever they were made.

PIECE_VERSION = 1  # bump when the drawing changes, so pieces made before are made again


def _stat(path):
    try:
        st = Path(path).stat()
        return [str(path), st.st_size, st.st_mtime_ns]
    except (OSError, TypeError, ValueError):
        return [str(path), None, None]


def piece_signature(items, settings, encoder_args) -> str:
    """Fingerprint of a chapter's piece: its pages or panel pictures, voice files and narration, and
    the video settings that change the picture. While it stays the same, the piece is reused."""
    content = []
    for it in items:
        if it.get("type") == "strip":
            ends_out = it["scenes"] and not (it["scenes"][0].get("enabled", True) and it["scenes"][-1].get("enabled", True))
            on = [i for i, sc in enumerate(it["scenes"]) if sc.get("enabled", True)]
            cut = bool(on) and len(on) < on[-1] - on[0] + 1  # left out between scenes used: cut out, not passed
            content.append(["strip", [_stat(p) + [w, h] for p, w, h in it["pages"]],
                            [[sc["y0"], sc["y1"], bool(sc.get("enabled", True)),
                              _stat(sc["audio"]) if sc.get("audio") else None, sc.get("narration", "")]
                             for sc in it["scenes"]], *(["trimmed"] if ends_out else []), *(["cut"] if cut else [])])
        else:
            content.append(["scene", [_stat(p) for p in it["images"]],
                            _stat(it["audio"]) if it.get("audio") else None, it.get("narration", ""),
                            *([it["focus"]] if it.get("focus") else [])])  # pieces made before keep their names
    look = [frame_size(settings), int(settings["fps"]), float(settings["transition_seconds"]),
            float(settings["pad_seconds"]), float(settings["min_panel_seconds"]), scene_motion(settings)]
    blob = json.dumps([PIECE_VERSION, look, list(encoder_args), content], default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _piece_file(base: Path, kind: str) -> Path:
    return Path(f"{base}.{kind}")


def piece_done(base: Path) -> bool:
    """A finished piece (its .json is put in place last, after the video and the audio)."""
    return all(_piece_file(base, k).exists() for k in ("mp4", "wav", "json"))


def chapter_encoder(settings) -> list:
    """The encoder a render starts with: the first GPU encoder that works here (NVIDIA, AMD, Intel),
    unless the setting says CPU. It may still fall back to the CPU if the GPU one won't start."""
    kind = None if settings.get("video_encoder", "auto") == "cpu" else hardware_encoder(find_ffmpeg())
    return HARDWARE[kind][1] if kind else CPU_ENCODER


def render_piece(items, base: Path, settings, encoder_args, workers, progress=None, cancelled=None) -> dict:
    """Make one chapter's piece: <base>.mp4 (video only), <base>.wav (its narration, exactly as long)
    and <base>.json (its length and subtitles). Files only take their final names once complete."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Install it or run setup.ps1.")
    W, H = frame_size(settings)
    fps = int(settings["fps"])
    tr = float(settings["transition_seconds"])
    timeline = _Timeline([segment(it, settings, W, H) for it in items], fps, tr, fade=round(tr * 0.5 * fps))
    n = round(timeline.frames * SAMPLE_RATE / fps)  # the narration runs exactly as long as the frames
    track = np.zeros(n, dtype=np.float32)
    subs = []
    for seg, st in zip(timeline.segments, timeline.starts):
        for at, a, text in seg.events:
            o = int((st + at) * SAMPLE_RATE)
            if o < n:
                track[o:o + len(a)] += a[: n - o]
            if text.strip():
                subs.append([round(st + at, 3), round(st + at + len(a) / SAMPLE_RATE, 3), text.strip()])
        seg.events = []
    np.clip(track, -1, 1, out=track)
    tmp = {k: _piece_file(base, f"part.{k}") for k in ("mp4", "wav", "json")}
    base.parent.mkdir(parents=True, exist_ok=True)
    try:
        sf.write(tmp["wav"], track, SAMPLE_RATE)
        del track

        def cmd(pix_fmt):
            return [ffmpeg, "-y", "-loglevel", "error",
                    "-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
                    *encoder_args, "-pix_fmt", output_pix_fmt(encoder_args), "-an", str(tmp["mp4"])]
        _draw(cmd, timeline, W, H, workers, progress, cancelled)
        info = {"frames": timeline.frames, "fps": fps, "seconds": timeline.frames / fps, "subs": subs}
        tmp["json"].write_text(json.dumps(info), encoding="utf-8")
        for k in ("mp4", "wav", "json"):  # the .json last: it marks the piece as finished
            tmp[k].replace(_piece_file(base, k))
        return info
    finally:
        for f in tmp.values():
            f.unlink(missing_ok=True)


def _draw(cmd, timeline, W, H, workers, progress, cancelled):
    """Draw the timeline into ffmpeg (cmd(pix_fmt) -> its command line), in several processes when asked."""
    if workers > 1:
        try:
            return _encode_parallel(cmd(PARALLEL_PIX_FMT), PARALLEL_PIX_FMT, timeline, W, H, workers, progress,
                                    cancelled)
        except _WorkersFailed as e:  # e.g. processes can't be started here: draw in this one
            log.warning("Couldn't draw video frames in several processes, using one: %s", e)
            timeline.release()
    _encode(cmd("bgr24"), timeline, W, H, progress, cancelled)


def render_reel(segments, lengths, path: Path, settings, encoder_args, workers, progress=None, cancelled=None):
    """Draw segments one after another, each for its number of frames (see _Reel), into path: a video
    with no sound and a keyframe every 2 seconds, so an editor can cut it anywhere and still play
    smoothly. The file only takes its name once complete."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Install it or run setup.ps1.")
    W, H = frame_size(settings)
    fps = int(settings["fps"])
    tmp = path.with_name(path.stem + ".part.mp4")
    path.parent.mkdir(parents=True, exist_ok=True)

    def cmd(pix_fmt):
        return [ffmpeg, "-y", "-loglevel", "error",
                "-f", "rawvideo", "-pix_fmt", pix_fmt, "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
                *encoder_args, "-g", str(2 * fps), "-pix_fmt", output_pix_fmt(encoder_args), "-an", "-movflags", "+faststart", str(tmp)]
    try:
        _draw(cmd, _Reel(segments, lengths, fps), W, H, workers, progress, cancelled)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def render_chapters(chapters, split, out_dir: Path, cache_dir: Path, settings, progress=None, cancelled=None,
                    status=None, between=None, join_cost=JOIN_COST) -> dict:
    """chapters: [{"id", "name", "items", "weight"}] in order (weight: about how long its video runs).
    Makes the pieces that are missing (reusing the rest); then split([seconds of each chapter]) ->
    [(file stem, [chapter indexes])] decides the parts from the chapters' exact lengths, and each part
    is joined into out_dir: <stem>.mp4, <stem>.srt, <stem>.wav and "<stem> - YouTube chapters.txt".
    between() runs before each chapter (Pause takes effect there); status(note) says what's happening.
    progress(fraction) shares the bar between drawing and joining by how long each should take: join_cost
    is how long joining a second of video takes, as a share of drawing one.
    Returns {"parts": [...], "made": n, "reused": n, "joined": seconds of video joined, "join_time": seconds}."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Install it or run setup.ps1.")
    order = list(range(len(chapters)))
    first = chapter_encoder(settings)
    encoders = [first, CPU_ENCODER] if is_hardware(first) else [CPU_ENCODER]
    for attempt, encoder_args in enumerate(encoders):
        gpu = is_hardware(encoder_args)
        workers = drawing_processes(settings, gpu)
        how = f"{'GPU' if gpu else 'CPU'} encoder, {workers} drawing process{'es' if workers > 1 else ''}"
        bases = {i: cache_dir / f"{chapters[i]['id']}-{piece_signature(chapters[i]['items'], settings, encoder_args)}"
                 for i in order}
        ready = sum(1 for i in order if piece_done(bases[i]))
        # the bar: chapters still to draw, then the join (all of it when every chapter is already made)
        to_draw = sum(chapters[i]["weight"] for i in order if not piece_done(bases[i]))
        to_join = join_cost * sum(ch["weight"] for ch in chapters)
        drawn_at = to_draw / (to_draw + to_join) if to_draw else 0.0
        made = reused = 0
        done = 0.0
        try:
            for k, i in enumerate(order):
                if between:
                    between()
                if cancelled and cancelled():
                    raise InterruptedError("cancelled")
                ch = chapters[i]
                if piece_done(bases[i]):
                    reused += 1
                    continue
                if status:
                    status(f"{how}; {ch['name']}, {k + 1} of {len(order)}"
                           + (f"; {ready} already made" if ready else ""))
                w0, w = done, ch["weight"]
                render_piece(ch["items"], bases[i], settings, encoder_args, workers,
                             (lambda f, w0=w0, w=w: progress(drawn_at * (w0 + f * w) / to_draw)) if progress else None,
                             cancelled)
                made += 1
                done += w
                if progress:
                    progress(drawn_at * done / to_draw)
            break
        except _EncoderFailed as e:
            if attempt + 1 == len(encoders) or e.frames > int(settings["fps"]):
                raise RuntimeError(str(e)) from None
            log.warning("GPU video encoding didn't start, using the CPU instead: %s", e)
    seconds = [json.loads(_piece_file(bases[i], "json").read_text(encoding="utf-8"))["seconds"] for i in order]
    parts = split(seconds)
    whole = sum(seconds) or 1.0
    outs, joined, started = [], 0.0, time.monotonic()
    for n, (stem, idx) in enumerate(parts, 1):
        if status:
            status(f"{how}; joining {'the chapters' if len(parts) == 1 else f'part {n} of {len(parts)}'}")
        j0, span = joined, sum(seconds[i] for i in idx)
        outs.append(_join_part(ffmpeg, stem, [(chapters[i]["name"], bases[i]) for i in idx], out_dir, cancelled,
                               (lambda f, j0=j0, span=span: progress(drawn_at + (1 - drawn_at) * (j0 + f * span) / whole))
                               if progress else None))
        joined += span
    _clean_cache(cache_dir, {chapters[i]["id"]: bases[i].name for i in order})
    if progress:
        progress(1.0)
    return {"parts": outs, "made": made, "reused": reused, "joined": sum(seconds),
            "join_time": time.monotonic() - started}


def _yt_time(seconds: float, hours: bool) -> str:
    s = int(seconds)  # rounded down, so a chapter's mark is never after its start
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02}:{s:02}" if hours else f"{m}:{s:02}"


def _concat_line(path: Path) -> str:
    """A line of ffmpeg's concat list (single quotes inside a name are written '\\'')."""
    return "file '" + path.resolve().as_posix().replace("'", "'\\''") + "'\n"


def _join_part(ffmpeg, stem, pieces, out_dir: Path, cancelled=None, progress=None) -> dict:
    """Join chapter pieces [(name, base)] into one video, copying the video as it is and encoding the
    narration once, plus its subtitles and a YouTube chapter list (paste it into the description).
    progress(fraction) follows ffmpeg's position in the video."""
    out_dir.mkdir(parents=True, exist_ok=True)
    infos = [json.loads(_piece_file(b, "json").read_text(encoding="utf-8")) for _, b in pieces]
    starts = [0.0]
    for x in infos[:-1]:
        starts.append(starts[-1] + x["seconds"])
    total = starts[-1] + infos[-1]["seconds"]
    wav = out_dir / f"{stem}.wav"
    # plain WAV can't go past 4 GB (about 24 hours of this narration); RF64 can
    kind = "WAV" if total * SAMPLE_RATE * 2 < 3.9e9 else "RF64"
    with sf.SoundFile(wav, "w", SAMPLE_RATE, 1, subtype="PCM_16", format=kind) as w:
        for _, b in pieces:
            with sf.SoundFile(_piece_file(b, "wav")) as r:
                while len(block := r.read(1 << 20, dtype="float32")):
                    w.write(block)
    listing = out_dir / f"{stem}.pieces.txt"
    listing.write_text("".join(_concat_line(_piece_file(b, "mp4")) for _, b in pieces), encoding="utf-8")
    video = out_dir / f"{stem}.mp4"
    tmp = out_dir / f"{stem}.joining.mp4"
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-nostats", "-progress", "pipe:1", "-f", "concat", "-safe", "0",
           "-i", str(listing), "-i", str(wav), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac",
           "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(tmp)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
    at = [0.0]  # seconds of video written so far, from ffmpeg's progress lines ("out_time_us=...")

    def follow():
        with proc.stdout:
            for line in proc.stdout:
                key, _, value = line.decode(errors="replace").strip().partition("=")
                if key == "out_time_us" and value.isdigit():
                    at[0] = int(value) / 1e6
    threading.Thread(target=follow, daemon=True).start()
    try:
        while True:
            try:
                proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancelled and cancelled():
                    proc.kill()
                    proc.wait()
                    raise InterruptedError("cancelled")
                if progress:  # the last bit is kept for the end, when the file is rewritten to play from the start
                    progress(0.98 * min(1.0, at[0] / max(total, 0.001)))
        err = proc.stderr.read().decode(errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"Joining the chapters failed: {err[-800:]}")
        tmp.replace(video)
    finally:
        proc.stderr.close()  # (stdout is closed by its reader when ffmpeg ends)
        listing.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    srt, lines = out_dir / f"{stem}.srt", []
    for info, st in zip(infos, starts):
        for a, b, text in info["subs"]:
            lines.append(f"{len(lines) + 1}\n{_srt_time(st + a)} --> {_srt_time(st + b)}\n{text}\n")
    srt.write_text("\n".join(lines), encoding="utf-8")
    chapters = out_dir / f"{stem} - YouTube chapters.txt"
    chapters.write_text("".join(f"{_yt_time(st, total >= 3600)} {name}\n" for (name, _), st in zip(pieces, starts)),
                        encoding="utf-8")
    return {"video": video, "srt": srt, "wav": wav, "chapters": chapters, "seconds": total,
            "first": pieces[0][0], "last": pieces[-1][0]}


def _clean_cache(cache_dir: Path, current: dict):
    """Delete pieces of these chapters made for an earlier version of them (other chapters' are kept)."""
    if not cache_dir.exists():
        return
    for f in cache_dir.iterdir():
        cid = f.name.split("-", 1)[0]
        if cid in current and not f.name.startswith(current[cid] + "."):
            f.unlink(missing_ok=True)
