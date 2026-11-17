"""Fuse signal tracks into ranked moment candidates."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import numpy as np

from ..config import settings
from . import signals

DEFAULT_WEIGHTS = {"chat": 1.0, "hype": 0.6, "audio": 0.7, "viewer_clips": 1.5}
CHAT_LAG = 5          # chat reacts ~3-8s after the moment it is reacting to
PRE_ROLL = 18         # seconds of build-up before the peak
POST_ROLL = 10        # seconds of payoff after the peak


def load_weights() -> dict[str, float]:
    try:
        w = json.loads(settings.weights_path.read_text())
        return {**DEFAULT_WEIGHTS, **{k: float(v) for k, v in w.items() if k in DEFAULT_WEIGHTS}}
    except (FileNotFoundError, ValueError):
        return dict(DEFAULT_WEIGHTS)


@dataclass
class Moment:
    peak: float
    start: float
    end: float
    raw: float
    score: float
    features: dict = field(default_factory=dict)


@dataclass
class Tracks:
    chat_z: np.ndarray
    hype_z: np.ndarray
    audio_z: np.ndarray
    viewer_clips: np.ndarray
    audio_db: np.ndarray

    @property
    def duration(self) -> int:
        return self.audio_db.size


def build_tracks(chat_rate, chat_hype, audio_db, viewer_clips) -> Tracks:
    n = audio_db.size

    def shift_back(x: np.ndarray, lag: int) -> np.ndarray:
        if lag <= 0 or x.size <= lag:
            return x
        return np.concatenate([x[lag:], np.zeros(lag)])

    has_chat = chat_rate.sum() > 0
    chat_z = signals.rolling_z(signals.smooth(shift_back(chat_rate, CHAT_LAG), 6), min_scale=0.5) if has_chat else np.zeros(n)
    hype_z = signals.rolling_z(signals.smooth(shift_back(chat_hype, CHAT_LAG), 6), min_scale=0.8) if has_chat else np.zeros(n)
    audio_z = signals.rolling_z(signals.smooth(audio_db, 3), min_scale=1.5)
    return Tracks(chat_z, hype_z, audio_z, viewer_clips, audio_db)


def combined(tracks: Tracks, weights: dict[str, float]) -> np.ndarray:
    # Only upside counts. Log-compress instead of hard-capping so a huge moment still
    # outranks a merely strong one, without one outlier swamping everything else.
    # Signals under 1σ are noise and count for nothing.
    pos = lambda x: 2.5 * np.log1p(np.clip(x - 1.0, 0, None))  # noqa: E731
    return (
        weights["chat"] * pos(tracks.chat_z)
        + weights["hype"] * pos(tracks.hype_z)
        + weights["audio"] * pos(tracks.audio_z)
        + weights["viewer_clips"] * np.clip(tracks.viewer_clips, 0, 5)
    )


def find_moments(
    tracks: Tracks,
    weights: dict[str, float] | None = None,
    min_len: int = 15,
    max_len: int = 60,
    min_gap: int = 45,
    threshold: float = 2.5,
    limit: int = 200,
) -> list[Moment]:
    weights = weights or load_weights()
    n = tracks.duration
    score = signals.smooth(combined(tracks, weights), 3)
    work = score.copy()
    moments: list[Moment] = []

    while len(moments) < limit:
        peak = int(np.argmax(work))
        raw = float(work[peak])
        if raw < threshold:
            break
        work[max(0, peak - min_gap) : min(n, peak + min_gap)] = -np.inf

        start = max(0, peak - PRE_ROLL)
        end = min(n, peak + POST_ROLL)
        # Let the payoff breathe: extend while the moment is still elevated.
        while end < n and end - start < max_len and score[min(end, n - 1)] > raw * 0.45:
            end += 1
        if end - start < min_len:
            pad = min_len - (end - start)
            start = max(0, start - pad // 2)
            end = min(n, start + min_len)
        end = min(end, start + max_len)
        if end - start < min(min_len, n):
            continue

        lo, hi = max(0, peak - 5), min(n, peak + 6)
        feats = {
            "chat_z": round(float(np.max(tracks.chat_z[lo:hi])), 2),
            "hype_z": round(float(np.max(tracks.hype_z[lo:hi])), 2),
            "audio_z": round(float(np.max(tracks.audio_z[lo:hi])), 2),
            "audio_peak_db": round(float(np.max(tracks.audio_db[lo:hi])), 1),
            "viewer_clips": round(float(np.max(tracks.viewer_clips[lo:hi])), 2),
            "raw": round(raw, 2),
        }
        moments.append(Moment(peak, float(start), float(end), raw, to_score(raw), feats))
    return moments


def to_score(raw: float, scale: float = 8.0) -> float:
    """Map unbounded combined signal to 0-100."""
    return round(100 * (1 - np.exp(-raw / scale)), 1)
