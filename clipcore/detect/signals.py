"""Per-second signal tracks over a recording.

Every track is a numpy array indexed by second. They are normalised with a *rolling*
baseline so a big streamer's always-busy chat and a small streamer's quiet chat are
judged against themselves, not against each other.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

# Chat tokens that mean "something just happened".
HYPE = re.compile(
    r"\b(lul|lmao+|lol+|kekw|omegalul|pog+|pogchamp|poggers|w+|l+|no+ way|wtf|holy|bro+|"
    r"clip+ (it|that)|clipped|omg|sheesh|wait|what+|ayo+|insane|crazy|💀|😂|🤣)\b",
    re.IGNORECASE,
)
CLIP_CALL = re.compile(r"\bclip+\b|\bclipped\b", re.IGNORECASE)


def chat_series(chat_path: str | Path | None, duration: int) -> tuple[np.ndarray, np.ndarray]:
    """Returns (messages_per_second, hype_weighted_per_second)."""
    rate = np.zeros(duration, dtype=float)
    hype = np.zeros(duration, dtype=float)
    if not chat_path or not Path(chat_path).exists():
        return rate, hype
    with open(chat_path, encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = int(row.get("t", -1))
            if 0 <= t < duration:
                msg = row.get("m", "")
                rate[t] += 1
                w = 1.0
                if HYPE.search(msg):
                    w += 1.0
                if CLIP_CALL.search(msg):
                    w += 2.0                       # chat literally asking for a clip
                if len(msg) > 3 and msg.isupper():
                    w += 0.5
                hype[t] += w
    return rate, hype


def audio_series(video_path: str | Path, duration: int, sample_rate: int = 8000) -> np.ndarray:
    """Loudness in dBFS per second, streamed through ffmpeg so memory stays flat on 8h VODs."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video_path), "-vn", "-ac", "1",
        "-ar", str(sample_rate), "-f", "s16le", "-",
    ]
    out = np.full(duration, -90.0)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    chunk = sample_rate * 2  # one second of int16
    i = 0
    assert proc.stdout is not None
    while i < duration:
        buf = proc.stdout.read(chunk)
        if not buf:
            break
        x = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
        out[i] = 20 * np.log10(max(rms, 1e-5))
        i += 1
    proc.stdout.close()
    proc.wait()
    return out


def viewer_clip_series(offsets: list[dict], duration: int, spread: float = 8.0) -> np.ndarray:
    """Gaussian bumps where viewers made clips; weighted gently by clip views."""
    out = np.zeros(duration, dtype=float)
    if not offsets:
        return out
    t = np.arange(duration)
    for c in offsets:
        center = float(c["t"])
        if -30 < center < duration + 30:
            w = 1.0 + 0.25 * np.log10(1 + c.get("views", 0))
            out += w * np.exp(-0.5 * ((t - center) / spread) ** 2)
    return out


def smooth(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or x.size == 0:
        return x
    k = np.ones(window) / window
    return np.convolve(x, k, mode="same")


def rolling_z(x: np.ndarray, window: int = 300, min_scale: float = 0.0) -> np.ndarray:
    """Robust z-score against a centred rolling median / MAD baseline.

    `min_scale` is an absolute floor in the signal's own units (dB, msgs/s), so a very
    steady stretch can't turn meaningless wiggles into big z-scores.
    """
    n = x.size
    if n == 0:
        return x
    window = max(15, min(window, n if n % 2 else n - 1))
    if window % 2 == 0:
        window -= 1
    half = window // 2
    padded = np.pad(x, half, mode="reflect") if n > half else np.pad(x, half, mode="edge")
    views = sliding_window_view(padded, window)[:n]
    med = np.median(views, axis=1)
    mad = np.median(np.abs(views - med[:, None]), axis=1)
    # Floor the scale so flat stretches don't turn tiny wiggles into huge z-scores.
    floor = max(np.median(mad), 1e-3)
    scale = np.maximum(1.4826 * np.maximum(mad, floor), min_scale)
    return (x - med) / scale
