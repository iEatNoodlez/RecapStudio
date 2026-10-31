"""Text-to-speech with Kokoro (open-weights model, runs locally on ONNX Runtime), or one of the online
voices in online_voices.py (see speak)."""
import threading

import numpy as np

from . import gpu
from .addon import Download
from .store import MODELS_DIR

MODEL_FILE = MODELS_DIR / "kokoro-v1.0.onnx"
VOICES_FILE = MODELS_DIR / "voices-v1.0.bin"
SAMPLE_RATE = 24000
# The model is fetched by setup.ps1, or from the app (first-run setup) when it's missing:
# Kokoro v1.0 from the kokoro-onnx project's GitHub release, checked against its size and SHA-256.
DOWNLOAD = Download(MODELS_DIR, {
    "kokoro-v1.0.onnx": ("", "model-files-v1.0", "kokoro-v1.0.onnx", 325532387,
                         "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"),
    "voices-v1.0.bin": ("", "model-files-v1.0", "voices-v1.0.bin", 28214398,
                        "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
}, url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/{rev}/{path}")

_engine = None       # the most recently built engine (for status)
device = "CPU"       # what it actually runs on
_guard = threading.Lock()
_local = threading.local()     # each worker thread has its own engine
_phonemize = threading.Lock()  # espeak, which turns text into phonemes, isn't thread-safe


def available() -> bool:
    return MODEL_FILE.exists() and VOICES_FILE.exists()


def _get(use_gpu: bool | None = None):
    """This thread's voice engine; rebuilt when the GPU setting changes (None: keep what's loaded)."""
    global _engine, device
    engine, built_gpu = getattr(_local, "engine", None), getattr(_local, "gpu", None)
    if engine is None or (use_gpu is not None and bool(use_gpu) != built_gpu):
        if not available():
            raise RuntimeError(
                "Kokoro voice model not found. Download it from Settings → Run setup again, or run setup.ps1."
            )
        import onnxruntime as ort
        from kokoro_onnx import Kokoro
        with _guard:  # load models one engine at a time
            want = bool(use_gpu)
            try:
                session = ort.InferenceSession(str(MODEL_FILE), providers=gpu.providers(want))
            except Exception:  # GPU session failed to start: fall back to the CPU
                session = ort.InferenceSession(str(MODEL_FILE), providers=["CPUExecutionProvider"])
            engine = Kokoro.from_session(session, str(VOICES_FILE))
            _local.engine, _local.gpu = engine, want
            _engine, device = engine, gpu.device_of(session)
    return engine


def voices() -> list[str]:
    """Voice names, read from the voices file (without loading the model)."""
    if not available():
        return []
    with np.load(VOICES_FILE) as data:
        return sorted(data.files)


def lang_for(voice: str) -> str:
    # Kokoro voice prefixes: a=American, b=British, e=Spanish, f=French, h=Hindi,
    # i=Italian, j=Japanese, p=Portuguese, z=Mandarin
    return {
        "a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
        "i": "it", "j": "ja", "p": "pt-br", "z": "cmn",
    }.get(voice[:1], "en-us")


def synthesize(text: str, voice: str, speed: float, use_gpu: bool = True) -> np.ndarray:
    engine = _get(use_gpu)
    with _phonemize:  # quick; the slow part (the model) runs in parallel below
        phonemes = engine.tokenizer.phonemize(text, lang_for(voice))
    samples, sr = engine.create(phonemes, voice=voice, speed=float(speed), lang=lang_for(voice), is_phonemes=True)
    if sr != SAMPLE_RATE:
        # resample with linear interpolation (Kokoro is 24 kHz, so this rarely runs)
        n = int(len(samples) * SAMPLE_RATE / sr)
        samples = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples)
    return samples.astype(np.float32)


def speak(text: str, settings: dict) -> np.ndarray:
    """One narration line with the voice chosen in Settings → Voice: Kokoro here, or an online voice."""
    provider = settings.get("voice_provider", "kokoro")
    if provider == "edge":
        from . import online_voices
        return online_voices.edge_speak(text, settings.get("edge_voice"), settings.get("speed", 1.0))
    if provider == "elevenlabs":
        from . import online_voices
        return online_voices.eleven_speak(text, settings.get("elevenlabs_voice"), settings.get("elevenlabs_model"),
                                          settings.get("speed", 1.0))
    return synthesize(text, settings["voice"], settings["speed"], use_gpu=settings.get("use_gpu", True))
