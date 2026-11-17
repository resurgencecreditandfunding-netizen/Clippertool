"""9:16 short rendering.

Layouts
  stacked   facecam on top (40%), gameplay below (60%) — needs Source.facecam_box
  blurfill  zoomed gameplay centred over a blurred fill — works for any stream
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

W, H = 1080, 1920
CAM_H = 768
GAME_H = H - CAM_H


def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out)["format"].get("duration") or 0.0)


def parse_box(box: str) -> tuple[float, float, float, float] | None:
    try:
        x, y, w, h = (float(v) for v in box.split(","))
    except (ValueError, AttributeError):
        return None
    if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
        return None
    return x, y, w, h


def filtergraph(layout: str, facecam: tuple[float, float, float, float] | None, ass_name: str | None) -> str:
    if layout == "stacked" and facecam:
        x, y, w, h = facecam
        game_aspect = W / GAME_H
        g = (
            f"[0:v]split=2[a][b];"
            f"[a]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},"
            f"scale={W}:{CAM_H}:force_original_aspect_ratio=increase,crop={W}:{CAM_H},setsar=1[cam];"
            f"[b]crop=min(iw\\,ih*{game_aspect:.4f}):ih,scale={W}:{GAME_H},setsar=1[game];"
            f"[cam][game]vstack=inputs=2[v0]"
        )
    else:
        g = (
            f"[0:v]split=2[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur=24:2,eq=brightness=-0.12,setsar=1[bg];"
            f"[b]crop=iw*0.78:ih*0.86,scale={W}:-2,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2-80[v0]"
        )
    tail = f";[v0]ass={ass_name},fps=30,format=yuv420p[v]" if ass_name else ";[v0]fps=30,format=yuv420p[v]"
    return g + tail


def render_short(
    video_path: str,
    start: float,
    end: float,
    out_dir: Path,
    layout: str = "blurfill",
    facecam_box: str = "",
    ass_path: Path | None = None,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "short.mp4"
    thumb = out_dir / "thumb.jpg"
    box = parse_box(facecam_box)
    if layout == "stacked" and not box:
        layout = "blurfill"
    graph = filtergraph(layout, box, ass_path.name if ass_path else None)
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(Path(video_path).resolve()),
        "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-profile:v", "high",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        "-movflags", "+faststart", out.name,
    ]
    # Run inside out_dir so the ass= filter sees a plain relative filename (no escaping issues).
    r = subprocess.run(cmd, cwd=out_dir, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg render failed: {r.stderr[-800:]}")
    mid = max(0.0, min(1.5, (end - start) / 2))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{mid:.2f}", "-i", out.name,
         "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "4", thumb.name],
        cwd=out_dir, check=True,
    )
    return out, thumb
