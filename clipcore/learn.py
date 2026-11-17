"""The moat: re-weight detection signals from your own posted-clip performance.

Fits log(views) ~ signal features over published clips, then nudges the scorer's weights
toward the signals that actually predict views for GTA 6 content on your accounts.
"""
from __future__ import annotations

import json

import numpy as np
from sqlmodel import select

from .config import settings
from .db import session_scope
from .detect.scorer import DEFAULT_WEIGHTS, load_weights
from .models import Clip, Publication

FEATURES = ["chat_z", "hype_z", "audio_z", "viewer_clips"]
KEYMAP = {"chat_z": "chat", "hype_z": "hype", "audio_z": "audio", "viewer_clips": "viewer_clips"}
MIN_SAMPLES = 20


def learn(blend: float = 0.3) -> dict:
    with session_scope() as db:
        rows = db.exec(
            select(Publication, Clip).where(Publication.clip_id == Clip.id).where(Publication.views > 0)
        ).all()
    if len(rows) < MIN_SAMPLES:
        return {"updated": False, "samples": len(rows), "needed": MIN_SAMPLES, "weights": load_weights()}

    X, y = [], []
    for pub, clip in rows:
        f = json.loads(clip.features or "{}")
        X.append([max(0.0, float(f.get(k, 0))) for k in FEATURES])
        y.append(np.log1p(pub.views))
    X = np.array(X)
    y = np.array(y)
    Xs = (X - X.mean(0)) / (X.std(0) + 1e-6)
    A = np.column_stack([Xs, np.ones(len(Xs))])
    coef, *_ = np.linalg.lstsq(A, y - y.mean(), rcond=None)
    coef = np.clip(coef[: len(FEATURES)], 0, None)          # signals can lose influence, not invert

    current = load_weights()
    if coef.sum() > 0:
        total = sum(current[KEYMAP[k]] for k in FEATURES)
        target = coef / coef.sum() * total
        for k, t in zip(FEATURES, target):
            w = KEYMAP[k]
            current[w] = round((1 - blend) * current[w] + blend * float(t), 3)
    settings.weights_path.write_text(json.dumps(current, indent=2))
    return {"updated": True, "samples": len(rows), "weights": current,
            "importance": dict(zip(FEATURES, [round(float(c), 3) for c in coef]))}


def reset() -> dict:
    settings.weights_path.unlink(missing_ok=True)
    return dict(DEFAULT_WEIGHTS)
