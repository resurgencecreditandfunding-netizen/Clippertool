"""Optional speech-to-text over clip windows only (never the full VOD — that is the cost trap).

Uses faster-whisper when installed (`pip install faster-whisper`); otherwise returns None and
the pipeline still works, just without captions or hook scoring.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from .config import settings

HOOK_PHRASES = re.compile(
    r"\b(no way|oh my god|what the|are you serious|let'?s go+|bro+|wait|watch this|"
    r"i can'?t believe|how did|did you see|clip (it|that)|oh no|run|get out|holy)\b",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _model():
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        return None
    return WhisperModel(settings.whisper_model, device="auto", compute_type="int8")


def available() -> bool:
    return _model() is not None


def transcribe_window(video_path: str, start: float, end: float) -> list[dict] | None:
    """Word-level transcript with times relative to `start`."""
    model = _model()
    if model is None:
        return None
    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "a.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}",
             "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", str(wav)],
            check=True,
        )
        segments, _ = model.transcribe(str(wav), word_timestamps=True, vad_filter=True)
        words = []
        for seg in segments:
            for w in seg.words or []:
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


def best_line(words: list[dict] | None, max_words: int = 8) -> str:
    """The most quotable line, for a default title."""
    if not words:
        return ""
    text = " ".join(w["word"] for w in words)
    m = HOOK_PHRASES.search(text)
    if not m:
        return ""
    tail = text[m.start():].split()
    return " ".join(tail[:max_words]).strip(" ,.")
