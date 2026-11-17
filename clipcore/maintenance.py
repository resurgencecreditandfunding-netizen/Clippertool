"""Disk housekeeping — a server runs out of space fast without it.

Full stream recordings are 3-6 GB per hour. Once a session is processed and older than the
retention window, its recording is deleted. Rendered clips stay, except rejected ones.
"""
from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

from sqlmodel import select

from .config import settings
from .db import session_scope
from .models import Clip, Session, utcnow


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def disk_usage() -> dict:
    total, used, free = shutil.disk_usage(settings.data_dir)
    gb = 1024 ** 3
    return {
        "recordings_gb": round(_size(settings.recordings_dir) / gb, 2),
        "clips_gb": round(_size(settings.clips_dir) / gb, 2),
        "free_gb": round(free / gb, 1),
        "total_gb": round(total / gb, 1),
    }


def cleanup_recordings(days: int | None = None) -> dict:
    days = days or settings.retention_days
    cutoff = utcnow() - timedelta(days=days)
    freed = 0
    removed_sessions = 0
    removed_clips = 0
    with session_scope() as db:
        for s in db.exec(select(Session).where(Session.status.in_(["processed", "failed"]))).all():
            ended = s.ended_at or s.started_at
            if ended and ended < cutoff and s.video_path and Path(s.video_path).exists():
                p = Path(s.video_path)
                freed += _size(p)
                p.unlink(missing_ok=True)
                s.video_path = ""
                s.error = (s.error + " " if s.error else "") + "recording deleted after retention window"
                db.add(s)
                removed_sessions += 1
        for c in db.exec(select(Clip).where(Clip.status == "rejected")).all():
            if c.reviewed_at and c.reviewed_at < cutoff and c.path:
                d = Path(c.path).parent
                freed += _size(d)
                shutil.rmtree(d, ignore_errors=True)
                c.path = c.thumb_path = ""
                db.add(c)
                removed_clips += 1
        db.commit()
    return {"freed_gb": round(freed / 1024 ** 3, 2), "recordings_deleted": removed_sessions,
            "rejected_clips_deleted": removed_clips, **disk_usage()}
