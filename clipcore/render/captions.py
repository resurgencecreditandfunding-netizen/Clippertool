"""ASS subtitle generation: hook title band + punchy 2-3 word captions."""
from __future__ import annotations

from pathlib import Path


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(text: str) -> str:
    # Burned-in text can't render colour emoji (they'd show as empty boxes), so drop them.
    text = "".join(ch for ch in text if ord(ch) < 0x2190 or 0x2200 <= ord(ch) < 0x2300)
    text = " ".join(text.split())
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def build_ass(
    path: Path,
    duration: float,
    title: str = "",
    words: list[dict] | None = None,
    font: str = "Inter",
    caption_y: int = 1180,
) -> Path:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Hook,{font},62,&H00111111,&H00111111,&H00FFFFFF,&H00FFFFFF,1,0,0,0,100,100,0,0,3,16,0,8,80,80,110,1
Style: Cap,{font},92,&H00FFFFFF,&H0000E5FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    if title:
        # Hook title on screen for the first 3.5s — the scroll-stop window.
        lines.append(f"Dialogue: 1,{_ts(0)},{_ts(min(3.5, duration))},Hook,,0,0,0,,{_esc(title.upper())}")
    if words:
        chunk: list[dict] = []
        for w in words:
            chunk.append(w)
            if len(chunk) == 3 or w["word"].endswith((".", "!", "?", ",")):
                lines.append(_caption_line(chunk, caption_y))
                chunk = []
        if chunk:
            lines.append(_caption_line(chunk, caption_y))
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return path


def _caption_line(chunk: list[dict], y: int) -> str:
    start, end = chunk[0]["start"], max(chunk[-1]["end"], chunk[0]["start"] + 0.35)
    # Yellow highlight sweep across the words using karaoke tags.
    parts = []
    for w in chunk:
        cs = max(1, int(round((w["end"] - w["start"]) * 100)))
        parts.append(f"{{\\kf{cs}}}{_esc(w['word'].upper())}")
    return f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{{\\an5\\pos(540,{y})}}" + " ".join(parts)
