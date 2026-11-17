"""Route approved clips to posting accounts and time slots.

Rules (the "manifold" outputs):
  * a clip goes to at most one account per platform (no duplicate files across accounts)
  * a clip only goes to platforms its campaign allows, and accounts that carry that campaign
  * each account respects its daily cap and minimum gap between posts
  * an account avoids posting the same streamer twice in a row
  * best-scoring clips get the earliest, best slots
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlmodel import select

from .config import settings
from .db import session_scope
from .models import Campaign, Clip, Destination, Publication


def _campaign_ok(dest: Destination, campaign_id: int | None) -> bool:
    if not dest.campaign_ids.strip():
        return True
    allowed = {int(x) for x in dest.campaign_ids.split(",") if x.strip().isdigit()}
    return campaign_id in allowed


def schedule_day(day: date) -> list[Publication]:
    window_start = datetime.combine(day, time(settings.post_window_start)).astimezone()
    window_end = datetime.combine(day, time(min(settings.post_window_end, 23), 59)).astimezone()
    created: list[Publication] = []

    with session_scope() as db:
        dests = db.exec(select(Destination).where(Destination.active == True)).all()  # noqa: E712
        campaigns = {c.id: c for c in db.exec(select(Campaign)).all()}
        already = db.exec(select(Publication)).all()
        published_pairs = {(p.clip_id, _platform(dests, p.destination_id)) for p in already}

        # Existing load for the day per destination.
        slots: dict[int, list[datetime]] = defaultdict(list)
        last_source: dict[int, int] = {}
        clip_source = {c.id: c.source_id for c in db.exec(select(Clip)).all()}
        for p in sorted(already, key=lambda p: p.scheduled_for):
            if window_start <= p.scheduled_for <= window_end:
                slots[p.destination_id].append(p.scheduled_for)
                last_source[p.destination_id] = clip_source.get(p.clip_id, -1)

        clips = db.exec(
            select(Clip).where(Clip.status == "approved").order_by(Clip.score.desc())
        ).all()

        for clip in clips:
            camp = campaigns.get(clip.campaign_id) if clip.campaign_id else None
            if camp is None or not camp.active:
                continue
            if camp.budget_total and camp.budget_spent >= camp.budget_total:
                continue
            platforms = [p.strip() for p in camp.platforms.split(",") if p.strip()]
            for platform in platforms:
                if (clip.id, platform) in published_pairs:
                    continue
                options = [
                    d for d in dests
                    if d.platform == platform and _campaign_ok(d, clip.campaign_id)
                    and len(slots[d.id]) < d.daily_cap
                ]
                if not options:
                    continue
                # Prefer accounts that didn't just post this streamer, then the least loaded.
                options.sort(key=lambda d: (last_source.get(d.id) == clip.source_id, len(slots[d.id])))
                for d in options:
                    nxt = _next_slot(slots[d.id], window_start, window_end, d.min_gap_minutes)
                    if nxt is None:
                        continue
                    pub = Publication(clip_id=clip.id, destination_id=d.id, scheduled_for=nxt)
                    db.add(pub)
                    created.append(pub)
                    slots[d.id].append(nxt)
                    last_source[d.id] = clip.source_id
                    published_pairs.add((clip.id, platform))
                    break
        db.commit()
        for p in created:
            db.refresh(p)
    return created


def _platform(dests: list[Destination], dest_id: int) -> str:
    for d in dests:
        if d.id == dest_id:
            return d.platform
    return "?"


def _next_slot(taken: list[datetime], start: datetime, end: datetime, gap_min: int) -> datetime | None:
    gap = timedelta(minutes=gap_min)
    t = max([start] + [x + gap for x in taken])
    now = datetime.now().astimezone()
    if t < now:
        t = now.replace(second=0, microsecond=0) + timedelta(minutes=5)
    return t if t <= end else None
