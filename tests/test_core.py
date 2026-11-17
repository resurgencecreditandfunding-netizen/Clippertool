import json
from datetime import date, datetime, timedelta

import numpy as np
import pytest
from sqlmodel import select

from clipcore import rules
from clipcore.db import init_db, session_scope
from clipcore.detect import scorer, signals
from clipcore.ingest.chat import _parse_privmsg
from clipcore.models import Campaign, Clip, Destination, Publication, Session, Source


def synthetic_tracks(n=1800, events=((300, 1.0), (900, 0.6), (1500, 0.85)), seed=1):
    rng = np.random.default_rng(seed)
    rate = rng.poisson(1.0, n).astype(float)
    hype = rate.copy()
    audio = -32 + rng.normal(0, 1.5, n)
    for t, k in events:
        rate[t + 3 : t + 14] += 12 * k        # chat reacts after the moment
        hype[t + 3 : t + 14] += 24 * k
        audio[t - 1 : t + 4] += 18 * k
    return scorer.build_tracks(rate, hype, audio, np.zeros(n))


def test_detects_planted_moments_in_intensity_order():
    moments = scorer.find_moments(synthetic_tracks(), weights=dict(scorer.DEFAULT_WEIGHTS))
    top = [round(m.peak / 10) * 10 for m in moments[:3]]
    assert top == [300, 1500, 900]
    for m in moments[:3]:
        assert 15 <= m.end - m.start <= 60
        assert m.start < m.peak < m.end


def test_no_moments_in_flat_stream():
    n = 1200
    rng = np.random.default_rng(2)
    t = scorer.build_tracks(rng.poisson(1.0, n).astype(float), np.zeros(n), -32 + rng.normal(0, 1, n), np.zeros(n))
    strong = [m for m in scorer.find_moments(t) if m.score >= 60]
    assert strong == []


def test_rolling_z_is_relative_to_own_baseline():
    busy = np.full(600, 50.0); busy[300] = 80
    quiet = np.full(600, 2.0); quiet[300] = 5
    assert signals.rolling_z(busy)[300] > 3
    assert signals.rolling_z(quiet)[300] > 3


def test_chat_parse():
    line = ":bob!bob@bob.tmi.twitch.tv PRIVMSG #streamer :NO WAY KEKW"
    assert _parse_privmsg(line) == ("bob", "NO WAY KEKW")
    assert _parse_privmsg(":tmi.twitch.tv 001 justinfan1 :Welcome") is None


def test_viewer_clip_bumps():
    s = signals.viewer_clip_series([{"t": 100, "views": 0}], 300)
    assert int(np.argmax(s)) == 100 and s[100] == pytest.approx(1.0)


def test_compliance_rules():
    camp = Campaign(name="c", min_seconds=20, max_seconds=45, required_tags="#ad @deal")
    src = Source(handle="streamer")
    clip = Clip(session_id=1, source_id=1, peak_t=10, start_t=0, end_t=10)
    clip.caption = rules.build_caption("t", src, camp)
    issues = rules.check(clip, camp)
    assert any("Too short" in i for i in issues)
    assert not any("missing required tag" in i for i in issues)   # auto-appended
    assert rules.check(clip, None)[0].startswith("No campaign")
    clip.caption = "no tags"
    assert sum("missing required tag" in i for i in rules.check(clip, camp)) == 2


@pytest.fixture()
def seeded():
    init_db()
    with session_scope() as db:
        for t in (Publication, Clip, Session, Destination, Source, Campaign):
            for row in db.exec(select(t)).all():
                db.delete(row)
        db.commit()
        a = Campaign(name="A", platforms="youtube,tiktok", payout_per_1k=2)
        b = Campaign(name="B", platforms="youtube")
        db.add_all([a, b]); db.commit(); db.refresh(a); db.refresh(b)
        s1 = Source(handle="one", campaign_id=a.id); s2 = Source(handle="two", campaign_id=b.id)
        db.add_all([s1, s2]); db.commit(); db.refresh(s1); db.refresh(s2)
        sess = Session(source_id=s1.id); db.add(sess); db.commit(); db.refresh(sess)
        yt1 = Destination(platform="youtube", account_name="yt1", campaign_ids=str(a.id), daily_cap=2, min_gap_minutes=60)
        yt2 = Destination(platform="youtube", account_name="yt2", campaign_ids=str(b.id), daily_cap=5)
        tt = Destination(platform="tiktok", account_name="tt", daily_cap=5)
        db.add_all([yt1, yt2, tt]); db.commit()
        for i in range(4):
            db.add(Clip(session_id=sess.id, source_id=s1.id, campaign_id=a.id, peak_t=i, start_t=0, end_t=30,
                        score=90 - i, status="approved"))
        db.add(Clip(session_id=sess.id, source_id=s2.id, campaign_id=b.id, peak_t=0, start_t=0, end_t=30,
                    score=50, status="approved"))
        db.add(Clip(session_id=sess.id, source_id=s1.id, campaign_id=a.id, peak_t=0, start_t=0, end_t=30,
                    score=99, status="pending"))
        db.commit()
        return {"a": a.id, "b": b.id}


def test_router_rules(seeded):
    from clipcore.router import schedule_day

    day = date.today() + timedelta(days=1)
    pubs = schedule_day(day)
    with session_scope() as db:
        dests = {d.id: d for d in db.exec(select(Destination)).all()}
        clips = {c.id: c for c in db.exec(select(Clip)).all()}
    per_dest = {}
    seen = set()
    for p in pubs:
        d = dests[p.destination_id]
        c = clips[p.clip_id]
        per_dest.setdefault(d.account_name, []).append(p.scheduled_for)
        assert c.status == "approved"                                   # never pending clips
        assert (c.id, d.platform) not in seen                          # one account per platform
        seen.add((c.id, d.platform))
        if d.campaign_ids:
            assert str(c.campaign_id) in d.campaign_ids.split(",")      # campaign scoping
        if c.campaign_id == seeded["b"]:
            assert d.platform == "youtube"                              # campaign platform rules
    assert len(per_dest["yt1"]) == 2                                    # daily cap
    gaps = sorted(per_dest["yt1"])
    assert gaps[1] - gaps[0] >= timedelta(minutes=60)                  # posting gap
    assert len(per_dest["tt"]) == 4                                     # all campaign-A clips
    assert len(per_dest["yt2"]) == 1
    assert schedule_day(day) == []                                      # idempotent


def test_learning_shifts_weights_toward_predictive_signal(seeded):
    from clipcore import learn
    from clipcore.config import settings

    settings.weights_path.unlink(missing_ok=True)
    rng = np.random.default_rng(3)
    with session_scope() as db:
        src = db.exec(select(Source)).first()
        sess = db.exec(select(Session)).first()
        dest = db.exec(select(Destination)).first()
        for i in range(40):
            audio = float(rng.uniform(0, 8))
            chat = float(rng.uniform(0, 8))
            c = Clip(session_id=sess.id, source_id=src.id, peak_t=i, start_t=0, end_t=30, status="approved",
                     features=json.dumps({"chat_z": chat, "hype_z": 1, "audio_z": audio, "viewer_clips": 0}))
            db.add(c); db.flush()
            # Views driven by audio only.
            db.add(Publication(clip_id=c.id, destination_id=dest.id, scheduled_for=datetime.now().astimezone(),
                               views=int(200 * np.exp(audio * 0.6)), status="posted"))
        db.commit()
    before = scorer.load_weights()
    r = learn.learn()
    assert r["updated"]
    assert r["weights"]["audio"] > before["audio"]
    assert r["weights"]["chat"] < before["chat"]
    learn.reset()
