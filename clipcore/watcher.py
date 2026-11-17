"""The always-on loop: when a source goes live, record it; when it ends, process it."""
from __future__ import annotations

import logging
import threading
import time

from sqlmodel import select

from .db import session_scope
from .discovery.twitch import Twitch
from .ingest.recorder import record_live
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
    tw = Twitch()
    active: dict[int, threading.Thread] = {}
    log.info("watcher started (poll every %ss)", interval)
    while not stop.is_set():
        with session_scope() as db:
            sources = db.exec(
                select(Source).where(Source.active == True, Source.auto_record == True)  # noqa: E712
            ).all()
        try:
            live = tw.streams_for([s.handle for s in sources]) if sources else {}
        except Exception as e:
            log.warning("live check failed: %s", e)
            live = {}
        for s in sources:
            t = active.get(s.id)
            if t and t.is_alive():
                continue
            stream = live.get(s.handle.lower())
            if stream:
                log.info("%s is live (%s viewers) — recording", s.handle, stream.get("viewer_count"))
                th = threading.Thread(
                    target=_record_then_process, args=(s.id, stream.get("title", ""), stop), daemon=True
                )
                th.start()
                active[s.id] = th
        stop.wait(interval)
