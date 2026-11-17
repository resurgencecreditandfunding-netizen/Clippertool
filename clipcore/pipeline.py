"""Session → moments → rendered, compliant shorts."""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from sqlmodel import select

from . import rules, transcribe
from .config import settings
from .db import session_scope
from .detect import scorer, signals
from .models import Campaign, Clip, Session, Source
from .render.captions import build_ass
from .render.ffmpeg import probe_duration, render_short

log = logging.getLogger("clipcore.pipeline")


def _viewer_clips(sess: Session, source: Source) -> list[dict]:
    if sess.viewer_clips_path and Path(sess.viewer_clips_path).exists():
        return json.loads(Path(sess.viewer_clips_path).read_text())
    if settings.has_twitch and source.broadcaster_id and sess.ended_at:
        try:
            from .discovery.twitch import clip_offsets, get_client

            raw = get_client().clips(source.broadcaster_id, sess.started_at, sess.ended_at)
            return clip_offsets(raw, sess.started_at)
        except Exception as e:  # clips are a bonus signal; never fail the run on them
            log.warning("viewer clips unavailable: %s", e)
    return []


def process_session(session_id: int, render_top: int | None = None) -> list[int]:
    """Detect moments for a session, store all as candidates, render the best ones."""
    with session_scope() as db:
        sess = db.get(Session, session_id)
        source = db.get(Source, sess.source_id)
        campaign = db.get(Campaign, source.campaign_id) if source.campaign_id else None
        sess.status = "processing"
        sess.error = ""
        db.add(sess)
        db.commit()

    try:
        duration = int(sess.duration or probe_duration(sess.video_path))
        rate, hype = signals.chat_series(sess.chat_path, duration)
        audio = signals.audio_series(sess.video_path, duration)
        vclips = signals.viewer_clip_series(_viewer_clips(sess, source), duration)
        tracks = scorer.build_tracks(rate, hype, audio, vclips)
        moments = scorer.find_moments(
            tracks,
            min_len=campaign.min_seconds if campaign else 15,
            max_len=min(campaign.max_seconds, 60) if campaign else 60,
        )

        if render_top is None:
            render_top = max(3, math.ceil(duration / 3600 * settings.clips_per_hour))

        with session_scope() as db:
            # Re-processing replaces unreviewed output, keeps anything already reviewed.
            for old in db.exec(select(Clip).where(Clip.session_id == session_id)).all():
                if old.status in ("candidate", "pending", "failed"):
                    db.delete(old)
            db.commit()
            ids = []
            for m in moments:
                c = Clip(
                    session_id=session_id, source_id=source.id,
                    campaign_id=campaign.id if campaign else None,
                    peak_t=m.peak, start_t=m.start, end_t=m.end,
                    score=m.score, features=json.dumps(m.features),
                )
                db.add(c)
                db.flush()
                ids.append(c.id)
            db.commit()

        rendered = []
        for cid in ids[:render_top]:
            try:
                render_clip(cid)
                rendered.append(cid)
            except Exception as e:
                log.exception("render failed for clip %s", cid)
                with session_scope() as db:
                    c = db.get(Clip, cid)
                    c.status = "failed"
                    c.transcript = f"render error: {e}"[:500]
                    db.add(c)
                    db.commit()

        with session_scope() as db:
            sess = db.get(Session, session_id)
            sess.status = "processed"
            db.add(sess)
            db.commit()
        return rendered
    except Exception as e:
        with session_scope() as db:
            sess = db.get(Session, session_id)
            sess.status, sess.error = "failed", str(e)[:500]
            db.add(sess)
            db.commit()
        raise


def render_clip(clip_id: int, layout: str | None = None) -> Clip:
    with session_scope() as db:
        clip = db.get(Clip, clip_id)
        sess = db.get(Session, clip.session_id)
        source = db.get(Source, clip.source_id)
        campaign = db.get(Campaign, clip.campaign_id) if clip.campaign_id else None

    if not sess.video_path or not Path(sess.video_path).exists():
        raise ValueError("The original recording for this clip was deleted (storage cleanup), so it can't be re-cut.")
    feats = json.loads(clip.features or "{}")
    words = transcribe.transcribe_window(sess.video_path, clip.start_t, clip.end_t)
    if words is not None:
        feats["hook"] = transcribe.hook_score(words)
        clip.transcript = " ".join(w["word"] for w in words)
        # Small, bounded boost for clips that open on a hook.
        clip.score = round(min(100.0, clip.score + 8 * feats["hook"]), 1)

    if not clip.title:
        clip.title = rules.default_title(source, feats, transcribe.best_line(words))
    clip.caption = rules.build_caption(clip.title, source, campaign)
    clip.layout = layout or clip.layout or ("stacked" if source.facecam_box else "blurfill")

    out_dir = settings.clips_dir / f"{clip.id:06d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    ass = build_ass(out_dir / "captions.ass", clip.end_t - clip.start_t, clip.title, words, settings.font)
    path, thumb = render_short(
        sess.video_path, clip.start_t, clip.end_t, out_dir, clip.layout, source.facecam_box, ass
    )
    clip.path, clip.thumb_path = str(path), str(thumb)
    clip.features = json.dumps(feats)
    if clip.status in ("candidate", "failed"):
        clip.status = "pending"
    with session_scope() as db:
        db.add(clip)
        db.commit()
        db.refresh(clip)
    return clip


def recut(clip_id: int, start_delta: float = 0, end_delta: float = 0, layout: str | None = None,
          title: str | None = None) -> Clip:
    with session_scope() as db:
        clip = db.get(Clip, clip_id)
        sess = db.get(Session, clip.session_id)
        clip.start_t = max(0.0, clip.start_t + start_delta)
        clip.end_t = min(sess.duration or clip.end_t + end_delta, clip.end_t + end_delta)
        if clip.end_t - clip.start_t < 5:
            raise ValueError("clip would be shorter than 5 seconds")
        if title is not None:
            clip.title = title
        db.add(clip)
        db.commit()
    return render_clip(clip_id, layout)
