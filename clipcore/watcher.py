"""The always-on loop: when a source goes live, record it; when it ends, process it.

Also runs hourly disk cleanup. Waits quietly if Twitch keys aren't set yet, and picks them
up as soon as they're saved on the Settings page.
"""
from __future__ import annotations

import logging
import threading
import time

from sqlmodel import select

from .config import settings
from .db import session_scope
from .discovery.twitch import get_client
from .ingest.recorder import record_live
from .maintenance import cleanup_recordings, disk_usage
from .models import Source
from .pipeline import process_session

log = logging.getLogger("clipcore.watcher")


def _record_then_process(source_id: int, title: str, stop: threading.Event) -> None:
    try:
        sid = record_live(source_id, title, stop)
        log.info("session %s recorded, processing", sid)
        process_session(sid)
    except Exception:
        log.exception("record/process failed for source %s", source_id)


def watch(interval: int = 60, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    active: dict[int, threading.Thread] = {}
    last_cleanup = 0.0
    warned = False
    log.info("watcher started (poll every %ss)", interval)
    while not stop.is_set():
        if time.time() - last_cleanup > 3600:
            try:
                r = cleanup_recordings()
                if r["recordings_deleted"] or r["rejected_clips_deleted"]:
                    log.info("cleanup freed %s GB", r["freed_gb"])
            except Exception:
                log.exception("cleanup failed")
            last_cleanup = time.time()

        if not settings.has_twitch:
            if not warned:
                log.warning("Twitch keys not set — add them on the Settings page; watcher is waiting")
                warned = True
            stop.wait(interval)
            continue
        warned = False

        with session_scope() as db:
            sources = db.exec(
                select(Source).where(Source.active == True, Source.auto_record == True)  # noqa: E712
            ).all()
        try:
            live = get_client().streams_for([s.handle for s in sources]) if sources else {}
        except Exception as e:
            log.warning("live check failed: %s", e)
            live = {}
        low_disk = disk_usage()["free_gb"] < settings.min_free_gb
        if low_disk:
            log.error("only %.1f GB free — not starting new recordings until space is freed",
                      disk_usage()["free_gb"])
        for s in sources:
            t = active.get(s.id)
            if t and t.is_alive():
                continue
            stream = live.get(s.handle.lower())
            if stream and not low_disk:
                log.info("%s is live (%s viewers) — recording", s.handle, stream.get("viewer_count"))
                th = threading.Thread(
                    target=_record_then_process, args=(s.id, stream.get("title", ""), stop), daemon=True
                )
                th.start()
                active[s.id] = th
        stop.wait(interval)
