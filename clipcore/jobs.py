"""Link → ranked, captioned vertical shorts.

  1. resolve the link                      (title, length, formats)
  2. download audio only                   (~1 GB for 12h, vs ~40 GB of video)
  3. score every second                    (audio spikes + viewer clips / most-replayed)
  4. transcribe the best candidate windows (only those — never the whole VOD)
  5. re-rank on hook + speech, keep top N
  6. download video for just those N sections, render 9:16 with captions
"""
from __future__ import annotations

import json
import logging
import math
import shutil
import threading
import time
from pathlib import Path

import numpy as np
from sqlmodel import select

from . import llm, sources, transcribe
from .config import settings
from .db import session_scope
from .detect import scorer, signals
from .models import Job, Short, utcnow
from .render.captions import build_ass
from .render.ffmpeg import render_short
from .render.layout import detect as detect_layout

log = logging.getLogger("clipcore.jobs")

# Caption height per layout: on the seam for stacked, lower third otherwise.
CAPTION_Y = {"stacked": 768, "facecrop": 1380, "blurfill": 1460}

# Weights for link mode (no live chat available for VODs).
LINK_WEIGHTS = {"chat": 0.0, "hype": 0.0, "audio": 1.0, "viewer_clips": 1.6}


def auto_count(duration: float) -> int:
    return int(min(15, max(5, round(duration / 3600 * 2))))


def job_dir(job_id: int) -> Path:
    return settings.jobs_dir / f"{job_id:06d}"


class Stage:
    def __init__(self, job_id: int):
        self.job_id = job_id
        self._last = 0.0

    def __call__(self, stage: str, progress: float, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last < 1.0:
            return
        self._last = now
        with session_scope() as db:
            j = db.get(Job, self.job_id)
            j.stage, j.progress = stage, round(min(99.0, progress), 1)
            db.add(j)
            db.commit()

    def sub(self, stage: str, lo: float, hi: float):
        return lambda frac: self(stage, lo + (hi - lo) * frac)


def run_job(job_id: int) -> None:
    stage = Stage(job_id)
    work = job_dir(job_id)
    work.mkdir(parents=True, exist_ok=True)
    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status, job.error = "running", ""
            db.add(job)
            db.commit()

        stage("Opening link", 2, force=True)
        info = sources.resolve(job.url)
        with session_scope() as db:
            j = db.get(Job, job_id)
            j.title, j.channel, j.platform = info.title[:300], info.channel[:120], info.platform[:40]
            j.duration, j.thumbnail = info.duration, info.thumbnail
            db.add(j)
            db.commit()

        stage("Downloading audio", 5, force=True)
        audio = sources.download_audio(info, work, progress=stage.sub("Downloading audio", 5, 35))

        stage("Finding viral moments", 36, force=True)
        n_sec = int(info.duration)
        audio_db = signals.audio_series(audio, n_sec)
        crowd = sources.twitch_viewer_clips(info) + sources.heatmap_points(info)
        tracks = scorer.build_tracks(np.zeros(n_sec), np.zeros(n_sec), audio_db,
                                     signals.viewer_clip_series(crowd, n_sec))
        want = job.clip_count or auto_count(info.duration)
        moments = scorer.find_moments(tracks, weights=LINK_WEIGHTS, min_len=20, max_len=55,
                                      threshold=1.5, limit=want * 3)
        if not moments:
            raise sources.SourceError("No standout moments found. The video may be too quiet or too short.")

        # Transcribe the strongest candidates and re-rank on what's actually said.
        use_ai = llm.available()
        pool = moments[: min(len(moments), want * (3 if use_ai else 2), 36)]
        cands = []
        for i, m in enumerate(pool):
            stage("Listening for the best lines", 40 + 18 * i / len(pool))
            words = transcribe.transcribe_window(str(audio), m.start, m.end)
            hook = transcribe.hook_score(words)
            wps = len(words) / max(1.0, m.end - m.start) if words is not None else None
            signal = m.score + 12 * hook
            if wps is not None and wps < 0.6:
                signal -= 10                    # dead air makes weak shorts
            m.features.update(hook=hook, words_per_sec=round(wps or 0, 2), crowd=bool(crowd))
            cands.append({"m": m, "words": words, "signal": signal})

        # Signal strength relative to this stream's other candidates (0-100).
        lo_s, hi_s = min(c["signal"] for c in cands), max(c["signal"] for c in cands)
        for c in cands:
            c["rel"] = 100 * (c["signal"] - lo_s) / (hi_s - lo_s) if hi_s > lo_s else 80

        verdicts = {}
        if use_ai:
            stage("AI is picking the viral ones", 59, force=True)
            verdicts = llm.judge(info.channel, info.title, [
                {"id": i, "start": c["m"].start, "signal": c["rel"],
                 "transcript": " ".join(w["word"] for w in c["words"] or [])}
                for i, c in enumerate(cands)])
        for i, c in enumerate(cands):
            v = verdicts.get(i)
            if v:
                c["final"] = 0.75 * v["score"] * 10 + 0.25 * c["rel"]
                c["title"] = v["title"]
                c["m"].features.update(ai_score=v["score"])
            else:
                c["final"] = 55 + 0.43 * c["rel"]       # signal-only ranking: top pick ≈ 98
                c["title"] = ""
        cands.sort(key=lambda c: c["final"], reverse=True)
        chosen = [(min(99.0, c["final"]), c["m"], c["words"], c["title"]) for c in cands[:want]]

        for i, (final, m, words, ai_title) in enumerate(chosen):
            lo, hi = 62 + 37 * i / len(chosen), 62 + 37 * (i + 1) / len(chosen)
            stage(f"Making clip {i + 1} of {len(chosen)}", lo, force=True)
            cdir = work / f"clip{i + 1:02d}"
            cdir.mkdir(exist_ok=True)
            section, offset = sources.download_section(info, m.start, m.end, cdir / "section.ts")
            title = ai_title or make_title(info.channel, m.features, transcribe.best_line(words), i)
            lay = detect_layout(str(section), offset, m.end - m.start)
            m.features["layout"] = lay.name
            ass = build_ass(cdir / "captions.ass", m.end - m.start, title, words, settings.font,
                            caption_y=CAPTION_Y[lay.name])
            out, thumb = render_short(str(section), offset, offset + m.end - m.start, cdir, lay.name, lay.box, ass,
                                      center_x=lay.center_x)
            section.unlink(missing_ok=True)
            with session_scope() as db:
                db.add(Short(job_id=job_id, user_id=job.user_id, rank=i + 1, start_t=m.start, end_t=m.end,
                             score=round(final, 1), title=title,
                             transcript=" ".join(w["word"] for w in words or []),
                             features=json.dumps(m.features), path=str(out), thumb_path=str(thumb)))
                db.commit()

        audio.unlink(missing_ok=True)
        with session_scope() as db:
            j = db.get(Job, job_id)
            j.status, j.stage, j.progress, j.finished_at = "done", "Ready", 100.0, utcnow()
            db.add(j)
            db.commit()
    except Exception as e:
        log.exception("job %s failed", job_id)
        msg = str(e) if isinstance(e, sources.SourceError) else "Something went wrong processing this video. Try again, or try another link."
        with session_scope() as db:
            j = db.get(Job, job_id)
            j.status, j.error, j.finished_at = "failed", msg[:500], utcnow()
            db.add(j)
            db.commit()
        for p in list(work.glob("audio*")) + list(work.glob("clip*/section*")):
            p.unlink(missing_ok=True)


TEMPLATES = [
    "{name} was NOT ready for this 💀",
    "This is why we watch {name}",
    "{name} completely lost it",
    "Chat could not believe this 😭",
    "{name} had no idea what was coming",
    "Nobody expected this from {name}",
    "{name}'s reaction is everything",
    "Wait for it… 😂",
]


def make_title(channel: str, f: dict, quote: str = "", index: int = 0) -> str:
    """Fallback titles when the AI judge isn't enabled."""
    if quote and len(quote) > 8:
        return f'"{quote}"'
    name = (channel or "Streamer").rstrip("_")
    return TEMPLATES[index % len(TEMPLATES)].format(name=name)


# ------------------------------------------------------------------ queue
class Queue:
    def __init__(self) -> None:
        self._running: dict[int, threading.Thread] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread:
            return
        # Jobs interrupted by a restart go back in line.
        with session_scope() as db:
            for j in db.exec(select(Job).where(Job.status == "running")).all():
                j.status, j.stage, j.progress = "queued", "Waiting in line", 0
                db.add(j)
                for s in db.exec(select(Short).where(Short.job_id == j.id)).all():
                    db.delete(s)
            db.commit()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="job-queue")
        self._thread.start()

    def _loop(self) -> None:
        last_cleanup = 0.0
        while not self._stop.is_set():
            self._running = {k: t for k, t in self._running.items() if t.is_alive()}
            free = settings.max_parallel_jobs - len(self._running)
            if free > 0:
                with session_scope() as db:
                    nxt = db.exec(select(Job).where(Job.status == "queued").order_by(Job.id).limit(free)).all()
                for j in nxt:
                    if j.id in self._running:
                        continue
                    with session_scope() as db:
                        row = db.get(Job, j.id)
                        row.status, row.stage = "running", "Starting"
                        db.add(row)
                        db.commit()
                    t = threading.Thread(target=run_job, args=(j.id,), daemon=True, name=f"job-{j.id}")
                    t.start()
                    self._running[j.id] = t
            if time.time() - last_cleanup > 3600:
                try:
                    from .maintenance import cleanup
                    cleanup()
                except Exception:
                    log.exception("cleanup failed")
                last_cleanup = time.time()
            self._stop.wait(2)


queue = Queue()


def queue_position(job_id: int) -> int:
    with session_scope() as db:
        ids = [j.id for j in db.exec(select(Job).where(Job.status == "queued").order_by(Job.id)).all()]
    return ids.index(job_id) + 1 if job_id in ids else 0


def delete_job(job_id: int) -> None:
    with session_scope() as db:
        for s in db.exec(select(Short).where(Short.job_id == job_id)).all():
            db.delete(s)
        j = db.get(Job, job_id)
        if j:
            db.delete(j)
        db.commit()
    shutil.rmtree(job_dir(job_id), ignore_errors=True)
