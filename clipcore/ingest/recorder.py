"""Live recording (streamlink + chat) and VOD/file import (yt-dlp)."""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from ..config import settings
from ..db import session_scope
from ..models import Session, Source, utcnow
from ..render.ffmpeg import probe_duration
from .chat import log_chat


def _session_dir(source: Source, started: datetime) -> Path:
    d = settings.recordings_dir / source.handle / started.strftime("%Y%m%d-%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


def record_live(source_id: int, title: str = "", stop: threading.Event | None = None) -> int:
    """Record a live Twitch stream plus its chat until the stream ends. Returns session id."""
    if not shutil.which("streamlink"):
        raise RuntimeError("streamlink is not installed (pip install streamlink)")
    stop = stop or threading.Event()
    with session_scope() as db:
        source = db.get(Source, source_id)
        started = utcnow()
        sdir = _session_dir(source, started)
        sess = Session(
            source_id=source.id,
            title=title,
            started_at=started,
            video_path=str(sdir / "stream.ts"),
            chat_path=str(sdir / "chat.jsonl"),
            status="recording",
        )
        db.add(sess)
        db.commit()
        db.refresh(sess)
        handle, sid = source.handle, sess.id

    cmd = [
        "streamlink", "--retry-open", "3", "--hls-live-restart",
        f"https://twitch.tv/{handle}", settings.record_quality, "-o", sess.video_path, "--force",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    t0 = time.time()
    chat_stop = threading.Event()
    chat_thread = threading.Thread(
        target=log_chat, args=(handle, Path(sess.chat_path), t0, chat_stop), daemon=True
    )
    chat_thread.start()
    try:
        while proc.poll() is None:
            if stop.wait(5):
                proc.terminate()
                proc.wait(timeout=30)
                break
    finally:
        chat_stop.set()
        chat_thread.join(timeout=10)

    err = (proc.stderr.read() or b"").decode(errors="ignore")[-500:] if proc.stderr else ""
    with session_scope() as db:
        sess = db.get(Session, sid)
        sess.ended_at = utcnow()
        if Path(sess.video_path).exists() and Path(sess.video_path).stat().st_size > 0:
            sess.duration = probe_duration(sess.video_path)
            sess.status = "recorded"
        else:
            sess.status, sess.error = "failed", err or "no video recorded"
        db.add(sess)
        db.commit()
    return sid


def import_media(
    source_id: int,
    path_or_url: str,
    chat_path: str | None = None,
    viewer_clips: list[dict] | None = None,
    title: str = "",
) -> int:
    """Register an existing file, or download a VOD URL with yt-dlp, as a session."""
    with session_scope() as db:
        source = db.get(Source, source_id)
        started = utcnow()
        sdir = _session_dir(source, started)

    src = Path(path_or_url)
    if src.exists():
        video = sdir / f"stream{src.suffix or '.mp4'}"
        shutil.copy(src, video)
    else:
        if not shutil.which("yt-dlp"):
            raise RuntimeError("yt-dlp is not installed (pip install yt-dlp)")
        video = sdir / "stream.mp4"
        subprocess.run(
            ["yt-dlp", "-f", "best[height<=1080]/best", "--merge-output-format", "mp4",
             "-o", str(video), path_or_url],
            check=True,
        )

    chat_dst = ""
    if chat_path and Path(chat_path).exists():
        chat_dst = str(sdir / "chat.jsonl")
        shutil.copy(chat_path, chat_dst)
    clips_dst = ""
    if viewer_clips:
        clips_dst = str(sdir / "viewer_clips.json")
        Path(clips_dst).write_text(json.dumps(viewer_clips))

    with session_scope() as db:
        sess = Session(
            source_id=source_id,
            title=title or src.name,
            started_at=started,
            ended_at=utcnow(),
            video_path=str(video),
            chat_path=chat_dst,
            viewer_clips_path=clips_dst,
            duration=probe_duration(str(video)),
            status="recorded",
        )
        db.add(sess)
        db.commit()
        db.refresh(sess)
        return sess.id
