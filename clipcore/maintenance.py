"""Disk housekeeping: finished projects are deleted after the retention window."""
from __future__ import annotations

import shutil
from datetime import timedelta

from sqlmodel import select

from .config import settings
from .db import session_scope
from .models import Job, utcnow


def disk_usage() -> dict:
    total, used, free = shutil.disk_usage(settings.data_dir)
    gb = 1024 ** 3
    size = sum(f.stat().st_size for f in settings.jobs_dir.rglob("*") if f.is_file()) if settings.jobs_dir.exists() else 0
    return {"projects_gb": round(size / gb, 2), "free_gb": round(free / gb, 1), "total_gb": round(total / gb, 1)}


def cleanup(days: int | None = None) -> dict:
    from .jobs import delete_job

    cutoff = utcnow() - timedelta(days=days or settings.retention_days)
    with session_scope() as db:
        old = [j.id for j in db.exec(select(Job).where(Job.status.in_(["done", "failed"]))).all()
               if (j.finished_at or j.created_at) < cutoff]
    for jid in old:
        delete_job(jid)
    return {"deleted_projects": len(old), **disk_usage()}
