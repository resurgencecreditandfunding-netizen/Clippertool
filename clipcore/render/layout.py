"""Automatic layout: find the streamer's face and choose how to frame the short.

  stacked    small webcam overlay found → webcam on top, gameplay below (classic streamer clip)
  facecrop   large face (full-screen camera / just chatting) → 9:16 crop that follows the face
  blurfill   no reliable face → zoomed gameplay over a blurred fill
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

import numpy as np

log = logging.getLogger("clipcore.layout")
W, H = 640, 360          # analysis resolution
CAM_ASPECT = 1080 / 768  # the top panel of the stacked layout


@dataclass
class Layout:
    name: str                 # stacked | facecrop | blurfill
    box: str = ""             # stacked: "x,y,w,h" fractions of the frame
    center_x: float = 0.5     # facecrop: face centre as a fraction of frame width


def _frames(path: str, offset: float, duration: float, n: int = 10) -> list[np.ndarray]:
    frames = []
    for i in range(n):
        t = offset + duration * (i + 0.5) / n
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
             "-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True).stdout
        if len(raw) == W * H:
            frames.append(np.frombuffer(raw, np.uint8).reshape(H, W))
    return frames


def detect(path: str, offset: float, duration: float) -> Layout:
    try:
        import cv2
    except ImportError:
        return Layout("blurfill")
    frames = _frames(path, offset, duration)
    if len(frames) < 4:
        return Layout("blurfill")
    cascades = [cv2.CascadeClassifier(cv2.data.haarcascades + name)
                for name in ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml")]
    faces = []
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))   # lifts dark streamer rooms
    for f in frames:
        eq = clahe.apply(f)
        found = []
        for cascade in cascades:
            for (x, y, w, h) in cascade.detectMultiScale(eq, scaleFactor=1.08, minNeighbors=5, minSize=(16, 16)):
                c = (x + w / 2, y + h / 2, w, h)
                if not any(abs(c[0] - o[0]) < w * 0.5 and abs(c[1] - o[1]) < h * 0.5 for o in found):
                    found.append(c)
        faces.extend(found)
    if not faces:
        return Layout("blurfill")

    # The streamer's face sits in roughly the same place across the clip; game faces move.
    best, best_hits = None, 0
    for cx, cy, w, h in faces:
        tol_x, tol_y = max(W * 0.04, w * 0.7), max(H * 0.06, h * 0.7)
        cluster = [f for f in faces if abs(f[0] - cx) < tol_x and abs(f[1] - cy) < tol_y]
        if len(cluster) > best_hits:
            best_hits, best = len(cluster), cluster
    if best_hits < max(3, len(frames) * 0.3):
        return Layout("blurfill")
    cx = float(np.median([f[0] for f in best]))
    cy = float(np.median([f[1] for f in best]))
    fw = float(np.median([f[2] for f in best]))
    fh = float(np.median([f[3] for f in best]))

    if fw / W > 0.11:
        # Big face: the camera is the stream. Crop 9:16 around it.
        return Layout("facecrop", center_x=round(cx / W, 4))

    # Small face: a webcam overlay. Prefer the overlay's real border, else frame around the face.
    edges = _overlay_edges(frames, cx, cy, fw, fh)
    if edges:
        x0, y0, x1, y1 = edges
        box = _fit_aspect(x0, y0, x1 - x0, y1 - y0, cx, cy)
    else:
        bh = min(H, fh * 2.6)
        box = _fit_aspect(cx - bh * CAM_ASPECT / 2, cy - bh * 0.45, bh * CAM_ASPECT, bh, cx, cy)
    log.info("webcam found at %s (%d/%d frames)", box, best_hits, len(frames))
    return Layout("stacked", box=box)


def _overlay_edges(frames: list[np.ndarray], cx: float, cy: float, fw: float, fh: float):
    """Find the webcam overlay's border: straight edges that stay put while the game moves."""
    import cv2
    gx = np.median([np.abs(cv2.Sobel(f, cv2.CV_32F, 1, 0, ksize=3)) for f in frames], axis=0)
    gy = np.median([np.abs(cv2.Sobel(f, cv2.CV_32F, 0, 1, ksize=3)) for f in frames], axis=0)

    def strongest(profile: np.ndarray, lo: int, hi: int, default: float) -> float:
        lo, hi = max(0, lo), min(len(profile), hi)
        if hi - lo < 3:
            return default
        seg = profile[lo:hi]
        i = int(np.argmax(seg))
        return float(lo + i) if seg[i] > 3.0 * (np.median(profile) + 1e-3) else default

    rows = slice(max(0, int(cy - fh)), min(H, int(cy + fh)))
    cols = slice(max(0, int(cx - fw)), min(W, int(cx + fw)))
    col_profile = gx[rows, :].mean(axis=0)
    row_profile = gy[:, cols].mean(axis=1)
    left = strongest(col_profile, int(cx - fw * 4), int(cx - fw * 0.8), 0.0)
    right = strongest(col_profile, int(cx + fw * 0.8), int(cx + fw * 4), float(W))
    top = strongest(row_profile, int(cy - fh * 3), int(cy - fh * 0.8), 0.0)
    bottom = strongest(row_profile, int(cy + fh * 0.8), int(cy + fh * 3.5), float(H))
    w, h = right - left, bottom - top
    # Sanity: a webcam overlay is a modest box around the face, not most of the screen.
    if not (fw * 1.8 <= w <= W * 0.6 and fh * 1.8 <= h <= H * 0.75):
        return None
    return left + 2, top + 2, right - 2, bottom - 2


def _fit_aspect(x: float, y: float, w: float, h: float, cx: float, cy: float) -> str:
    """Trim the box to the top panel's aspect (crop, never pad), keeping the face in frame."""
    if w / h > CAM_ASPECT:
        nw = h * CAM_ASPECT
        x = min(max(x, cx - nw / 2), x + w - nw)
        w = nw
    else:
        nh = w / CAM_ASPECT
        y = min(max(y, cy - nh * 0.45), y + h - nh)
        h = nh
    x = min(max(0.0, x), W - w)
    y = min(max(0.0, y), H - h)
    return f"{x / W:.4f},{y / H:.4f},{w / W:.4f},{h / H:.4f}"
