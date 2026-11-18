"""Optional speech-to-text over clip windows only (never the full VOD — that is the cost trap).

Uses faster-whisper when installed (`pip install faster-whisper`); otherwise returns None and
the pipeline still works, just without captions or hook scoring.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading

import numpy as np
from functools import lru_cache

from .config import settings

HOOK_PHRASES = re.compile(
    r"\b(no way|oh my (god|gosh|goodness)|what the|are you (serious|kidding)|let'?s go+|bro+|dude|"
    r"wait|watch this|i can(no|')?t believe|unbelievable|how did|did you see|clip (it|that)|oh no|"
    r"run|get out|holy|insane|crazy|scared|help|stop|why|what is (that|this)|no no+|ain'?t no)\b",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _model():
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return None
    return WhisperModel(settings.whisper_model, device="auto", compute_type="int8",
                        cpu_threads=max(1, (os.cpu_count() or 2) - 1))


_lock = threading.Lock()   # one transcription at a time; the model already uses every core


def available() -> bool:
    return _model() is not None


def transcribe_window(media_path: str, start: float, end: float) -> list[dict] | None:
    """Word-level transcript with times relative to `start`.

    Audio is decoded by ffmpeg and handed over as samples, so the model never opens files
    itself (avoids version clashes between faster-whisper and PyAV).
    """
    model = _model()
    if model is None:
        return None
    pcm = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", media_path,
         "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, check=True,
    ).stdout
    audio = np.frombuffer(pcm, dtype=np.float32)
    if audio.size < 16000:
        return []
    words = []
    with _lock:
        segments, _ = model.transcribe(audio, word_timestamps=True, vad_filter=True)
        for seg in segments:
            for w in seg.words or []:
                if w.word.strip():
                    words.append({"start": float(w.start), "end": float(w.end), "word": w.word.strip()})
    return words


def hook_score(words: list[dict] | None, first_seconds: float = 4.0) -> float:
    """0-1: does the clip open with a hook, and does it have hype language at all?"""
    if not words:
        return 0.0
    text = " ".join(w["word"] for w in words)
    opening = " ".join(w["word"] for w in words if w["start"] < first_seconds)
    s = 0.0
    if HOOK_PHRASES.search(opening):
        s += 0.6
    s += min(0.4, 0.1 * len(HOOK_PHRASES.findall(text)))
    return round(min(1.0, s), 2)


def best_line(words: list[dict] | None, max_words: int = 11) -> str:
    """The most quotable short sentence, for a default title."""
    if not words:
        return ""
    text = " ".join(w["word"] for w in words)
    sentences = [x.strip(" ,") for x in re.split(r"(?<=[.!?])\s+", text) if 3 <= len(x.split()) <= max_words]
    if not sentences:
        return ""
    def rank(x: str) -> float:
        return len(HOOK_PHRASES.findall(x)) * 2 + x.count("!") + (1 if x.endswith("?") else 0) - abs(len(x.split()) - 6) * 0.1
    best = max(sentences, key=rank)
    return best if rank(best) > 0.5 else ""
