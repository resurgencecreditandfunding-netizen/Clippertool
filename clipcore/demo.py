"""Synthetic stream with known hype moments — verifies the whole pipeline without Twitch.

Generates a "gameplay" video with a facecam box, quiet commentary noise, and loud bursts at
known times, plus a chat log that spikes a few seconds after each burst and viewer clips
near the biggest ones. Detection should recover the planted moments.
"""
from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path

from sqlmodel import select

from .config import settings
from .db import init_db, session_scope
from .ingest.recorder import import_media
from .models import Campaign, Destination, Source

# (time_s, intensity 0-1)
PLANTED = [(70, 1.0), (165, 0.6), (250, 0.85), (335, 0.45)]
DURATION = 420


def make_media(out_dir: Path, seed: int = 7) -> tuple[Path, Path, list[dict]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / "demo_stream.mp4"
    chat = out_dir / "demo_chat.jsonl"

    # Audio: low noise floor, with loud tone+noise bursts at the planted moments.
    vol_expr = "0.04" + "".join(
        f"+{0.9 * k:.2f}*between(t,{t - 2},{t + 4})" for t, k in PLANTED
    )
    filt = (
        "[0:v]drawbox=x=iw*0.72:y=ih*0.04:w=iw*0.25:h=ih*0.3:color=0x33aaff@0.9:t=fill,"
        "drawtext=text='FACECAM':x=w*0.76:y=h*0.17:fontsize=48:fontcolor=white,"
        "drawtext=text='%{pts\\:hms}':x=40:y=40:fontsize=56:fontcolor=white:box=1:boxcolor=black@0.5[v];"
        f"[1:a]volume='{vol_expr}':eval=frame[a]"
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={DURATION}",
         "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.5:duration={DURATION}:sample_rate=48000",
         "-filter_complex", filt, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-c:a", "aac", "-shortest", str(video)],
        check=True,
    )

    rnd = random.Random(seed)
    lines = []
    for t in range(DURATION):
        n = rnd.choice([0, 0, 1, 1, 1, 2])             # baseline chatter
        for pt, k in PLANTED:
            lag = t - pt
            if 2 <= lag <= 14:                         # chat reacts a few seconds later
                n += int(rnd.randint(6, 14) * k)
        for _ in range(n):
            spike = any(2 <= t - pt <= 14 for pt, _ in PLANTED)
            msg = rnd.choice(["KEKW", "NO WAY", "clip it", "LMAO", "W", "OMEGALUL"]) if spike else \
                rnd.choice(["hi chat", "what car is that", "gg", "how long is stream", "nice"])
            lines.append(json.dumps({"t": t + rnd.random(), "u": f"user{rnd.randint(1, 500)}", "m": msg}))
    chat.write_text("\n".join(lines) + "\n")

    viewer_clips = [{"t": pt + 1, "views": int(400 * k)} for pt, k in PLANTED if k >= 0.8]
    return video, chat, viewer_clips


def seed_demo() -> int:
    init_db()
    work = settings.data_dir / "demo_src"
    video, chat, vclips = make_media(work)
    with session_scope() as db:
        camp = db.exec(select(Campaign).where(Campaign.name == "Demo GTA 6 Campaign")).first()
        if not camp:
            camp = Campaign(name="Demo GTA 6 Campaign", platforms="youtube,tiktok", payout_per_1k=1.5,
                            budget_total=5000, min_seconds=15, max_seconds=60,
                            required_tags="#ad", notes="Synthetic campaign for testing.")
            db.add(camp)
            db.commit()
            db.refresh(camp)
        src = db.exec(select(Source).where(Source.handle == "demo_streamer")).first()
        if not src:
            src = Source(handle="demo_streamer", display_name="DemoStreamer", campaign_id=camp.id,
                         facecam_box="0.72,0.04,0.25,0.30", auto_record=False)
            db.add(src)
        if not db.exec(select(Destination)).first():
            db.add(Destination(platform="youtube", account_name="GTA6 Moments (YT)", campaign_ids=str(camp.id)))
            db.add(Destination(platform="tiktok", account_name="@gta6moments", campaign_ids=str(camp.id)))
        db.commit()
        db.refresh(src)
        source_id = src.id
    return import_media(source_id, str(video), str(chat), vclips, title="Demo GTA 6 stream")
