"""End-to-end tests that run without internet: a synthetic 'stream' is served over local HTTP
both as a plain mp4 and as an HLS playlist, and pushed through the real pipeline."""
import functools
import http.server
import json
import subprocess
import threading
from pathlib import Path

import numpy as np
import pytest
from sqlmodel import select

from clipcore import auth, sources
from clipcore.config import settings
from clipcore.db import init_db, session_scope
from clipcore.detect import scorer, signals
from clipcore.models import Job, Short, User

PLANTED = [(60, 1.0), (150, 0.7), (240, 0.85)]
DURATION = 300


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    """Static server with HTTP Range support, like real video hosts (needed for seeking)."""

    def log_message(self, *a):
        pass

    def send_head(self):
        rng = self.headers.get("Range")
        path = Path(self.translate_path(self.path))
        if not rng or not path.is_file():
            return super().send_head()
        size = path.stat().st_size
        start_s, _, end_s = rng.replace("bytes=", "").partition("-")
        start = int(start_s or 0)
        end = min(int(end_s) if end_s else size - 1, size - 1)
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(str(path)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            try:
                outputfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                break
            remaining -= len(chunk)


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    vol = "0.04" + "".join(f"+{0.9 * k:.2f}*between(t,{t - 2},{t + 4})" for t, k in PLANTED)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={DURATION}",
         "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.5:duration={DURATION}:sample_rate=48000",
         "-filter_complex", f"[1:a]volume='{vol}':eval=frame[a]", "-map", "0:v", "-map", "[a]",
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-c:a", "aac", "-shortest", str(d / "stream.mp4")],
        check=True)
    hls = d / "hls"
    hls.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(d / "stream.mp4"), "-c", "copy",
                    "-f", "hls", "-hls_time", "10", "-hls_list_size", "0", str(hls / "index.m3u8")], check=True)
    handler = functools.partial(RangeHandler, directory=str(d))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture()
def fresh_db():
    init_db()
    with session_scope() as db:
        for t in (Short, Job, User):
            for row in db.exec(select(t)).all():
                db.delete(row)
        db.commit()
    settings.save({"invite_code": ""})


# ------------------------------------------------------------------ accounts
def test_first_user_is_admin_and_invite_code(fresh_db):
    admin = auth.signup("Boss@Example.com", "password-1")
    assert admin.is_admin and admin.email == "boss@example.com"
    settings.save({"invite_code": "GTA6VIP"})
    with pytest.raises(auth.AuthError, match="access code"):
        auth.signup("a@b.co", "password-1", "wrong")
    u = auth.signup("a@b.co", "password-1", "GTA6VIP")
    assert not u.is_admin
    with pytest.raises(auth.AuthError, match="already exists"):
        auth.signup("A@B.co", "password-1", "GTA6VIP")
    with pytest.raises(auth.AuthError):
        auth.login("a@b.co", "nope-nope")
    assert auth.login("a@b.co", "password-1").id == u.id


def test_tokens(fresh_db):
    u = auth.signup("t@t.co", "password-1")
    tok = auth.make_token(u)
    assert auth.user_from_token(tok).id == u.id
    uid, exp, sig = tok.split(".")
    assert auth.user_from_token(f"{uid}.{int(exp) + 5}.{sig}") is None
    assert auth.user_from_token(f"{int(uid) + 1}.{exp}.{sig}") is None
    auth.set_password(u.id, "new-password-2")             # password change logs out old sessions
    assert auth.user_from_token(tok) is None


# ------------------------------------------------------------------ detection
def test_link_mode_detection_ranks_by_intensity():
    rng = np.random.default_rng(1)
    n = 1800
    audio = -32 + rng.normal(0, 1.5, n)
    for t, k in ((300, 1.0), (900, 0.6), (1500, 0.85)):
        audio[t - 1: t + 4] += 18 * k
    tracks = scorer.build_tracks(np.zeros(n), np.zeros(n), audio, np.zeros(n))
    from clipcore.jobs import LINK_WEIGHTS
    peaks = [round(m.peak / 10) * 10 for m in scorer.find_moments(tracks, weights=LINK_WEIGHTS, threshold=1.5)[:3]]
    assert peaks == [300, 1500, 900]


def test_viewer_clips_outrank_noise():
    n = 1200
    audio = -32 + np.random.default_rng(2).normal(0, 1.5, n)
    crowd = signals.viewer_clip_series([{"t": 700, "views": 5000}, {"t": 705, "views": 900}], n)
    from clipcore.jobs import LINK_WEIGHTS
    top = scorer.find_moments(scorer.build_tracks(np.zeros(n), np.zeros(n), audio, crowd), weights=LINK_WEIGHTS)[0]
    assert abs(top.peak - 702) < 10


def test_heatmap_points():
    info = sources.MediaInfo("u", "t", "c", "Youtube", 600, "", None, "id", None, None,
                             heatmap=[{"start_time": 100, "end_time": 110, "value": 1.0},
                                      {"start_time": 200, "end_time": 210, "value": 0.2}])
    pts = sources.heatmap_points(info)
    assert len(pts) == 1 and pts[0]["t"] == 105


# ------------------------------------------------------------------ media
def test_hls_section_download(media, tmp_path):
    fmt = {"url": f"{media}/hls/index.m3u8", "protocol": "m3u8_native"}
    out = tmp_path / "sec.ts"
    offset = sources.hls_download(fmt, out, start=125, end=155)
    assert 0 <= offset < 10.5                      # start lands inside the first segment
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                               capture_output=True, text=True).stdout)
    assert dur >= 30


@pytest.mark.parametrize("path", ["stream.mp4", "hls/index.m3u8"])
def test_full_pipeline_from_link(media, fresh_db, path):
    from clipcore.jobs import run_job

    u = auth.signup("clipper@example.com", "password-1")
    with session_scope() as db:
        j = Job(user_id=u.id, url=f"{media}/{path}", clip_count=3)
        db.add(j)
        db.commit()
        db.refresh(j)
    run_job(j.id)
    with session_scope() as db:
        job = db.get(Job, j.id)
        shorts = db.exec(select(Short).where(Short.job_id == j.id).order_by(Short.rank)).all()
    assert job.status == "done", job.error
    assert job.duration == pytest.approx(DURATION, abs=1)
    assert len(shorts) == 3
    found = sorted(round((s.start_t + 18) / 30) * 30 for s in shorts)
    assert found == [60, 150, 240]                  # every planted moment, nothing else
    for s in shorts:
        info = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height:format=duration", "-of", "json", s.path],
            capture_output=True, text=True).stdout)
        assert info["streams"][0]["width"] == 1080 and info["streams"][0]["height"] == 1920
        assert abs(float(info["format"]["duration"]) - s.duration) < 0.6
        assert Path(s.thumb_path).exists()
    assert not list((settings.jobs_dir / f"{j.id:06d}").glob("audio*"))   # temp audio cleaned up


def test_bad_links_fail_with_plain_english(fresh_db):
    with pytest.raises(sources.SourceError):
        sources.resolve("https://example.invalid/not-a-video")


def test_ai_judge_reorders_and_titles(media, fresh_db, monkeypatch):
    """With the AI judge on, its scores decide the order and its titles are used."""
    from clipcore import jobs, llm

    seen = {}

    def fake_judge(channel, title, cands):
        seen["n"] = len(cands)
        weakest = min(cands, key=lambda c: c["signal"])["id"]
        seen["weakest_start"] = next(c["start"] for c in cands if c["id"] == weakest)
        # The AI loves the weakest-sounding candidate and dislikes the rest.
        return {c["id"]: {"score": 10 if c["id"] == weakest else 2, "title": f"AI title {c['id']}"} for c in cands}

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "judge", fake_judge)
    u = auth.signup("ai@example.com", "password-1")
    with session_scope() as db:
        j = Job(user_id=u.id, url=f"{media}/hls/index.m3u8", clip_count=2)
        db.add(j)
        db.commit()
        db.refresh(j)
    jobs.run_job(j.id)
    with session_scope() as db:
        job = db.get(Job, j.id)
        shorts = db.exec(select(Short).where(Short.job_id == j.id).order_by(Short.rank)).all()
    assert job.status == "done", job.error
    assert seen["n"] >= 3                                   # AI saw more candidates than it kept
    assert shorts[0].start_t == seen["weakest_start"]       # AI's favourite ranks #1
    assert all(s.title.startswith("AI title") for s in shorts)
    assert shorts[0].score > shorts[1].score


def test_ai_judge_failure_falls_back(monkeypatch):
    from clipcore import llm
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "sk-ant-invalid")
    # No network to Anthropic here → must return {} rather than raise.
    assert llm.judge("x", "y", [{"id": 0, "start": 0, "signal": 50, "transcript": "hi"}]) == {}
