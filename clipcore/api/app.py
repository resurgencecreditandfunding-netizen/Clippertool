"""REST API + dashboard."""
from __future__ import annotations

import io
import json
import threading
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import func, select

from .. import auth
from .. import learn as learning
from .. import rules
from ..config import settings
from ..db import init_db, session_scope
from ..detect.scorer import load_weights
from ..models import Campaign, Clip, Destination, Publication, Session, Source, utcnow
from ..pipeline import process_session, recut
from ..router import schedule_day

init_db()
app = FastAPI(title="ClipCore OS", version="0.1.0")
WEB = Path(__file__).resolve().parent.parent / "web"
_jobs: dict[str, str] = {}


def _bg(key: str, fn, *args) -> None:
    def run():
        _jobs[key] = "running"
        try:
            fn(*args)
            _jobs[key] = "done"
        except Exception as e:  # surfaced via session.error / clip status
            _jobs[key] = f"error: {e}"
    threading.Thread(target=run, daemon=True).start()


def _today() -> date:
    return datetime.now(settings.tz).date()


# ---------------------------------------------------------------- login
OPEN_PATHS = {"/login.html", "/styles.css", "/api/auth/status", "/api/auth/setup", "/api/auth/login"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in OPEN_PATHS or auth.valid_token(request.cookies.get(auth.COOKIE)):
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    return RedirectResponse("/login.html", status_code=303)


class PasswordBody(BaseModel):
    password: str


def _session_response(request: Request) -> JSONResponse:
    resp = JSONResponse({"ok": True})
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(auth.COOKIE, auth.make_token(), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="strict", secure=https)
    return resp


@app.get("/api/auth/status")
def auth_status(request: Request):
    return {"setup": auth.is_setup(), "logged_in": auth.valid_token(request.cookies.get(auth.COOKIE))}


@app.post("/api/auth/setup")
def auth_setup(body: PasswordBody, request: Request):
    if auth.is_setup():
        raise HTTPException(403, "A password is already set. Log in instead.")
    try:
        auth.set_password(body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _session_response(request)


_failed: list[float] = []


@app.post("/api/auth/login")
def auth_login(body: PasswordBody, request: Request):
    import time
    now = time.time()
    _failed[:] = [t for t in _failed if now - t < 600]
    if len(_failed) >= 10:
        raise HTTPException(429, "Too many wrong attempts. Wait 10 minutes.")
    if not auth.check_password(body.password):
        _failed.append(now)
        time.sleep(1)
        raise HTTPException(401, "Wrong password.")
    return _session_response(request)


@app.post("/api/auth/logout")
def auth_logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


class ChangePassword(BaseModel):
    current: str
    new: str


@app.post("/api/auth/change")
def auth_change(body: ChangePassword, request: Request):
    if not auth.check_password(body.current):
        raise HTTPException(401, "Current password is wrong.")
    try:
        auth.set_password(body.new)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _session_response(request)


# ---------------------------------------------------------------- settings
@app.get("/api/settings")
def get_settings():
    from ..maintenance import disk_usage
    from ..config import QUALITY_OPTIONS
    return {**settings.public(), "disk": disk_usage(), "quality_options": QUALITY_OPTIONS}


@app.post("/api/settings")
def save_settings(body: dict[str, Any]):
    try:
        settings.save(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    from ..discovery.twitch import reset_client
    reset_client()
    return settings.public()


@app.post("/api/settings/test-twitch")
def test_twitch():
    if not settings.has_twitch:
        raise HTTPException(400, "Enter your Client ID and Client Secret first, then Save.")
    from ..discovery.twitch import TwitchError, get_client
    try:
        tw = get_client()
        tw._auth()
    except TwitchError as e:
        msg = str(e)
        if "auth failed" in msg:
            msg = "Twitch rejected these keys. Re-copy the Client ID, and generate a New Secret if unsure."
        raise HTTPException(400, msg)
    except Exception as e:
        raise HTTPException(502, f"Could not reach Twitch: {e}")
    try:
        gid = tw.game_id()
        live = tw.live_streams(limit=100)
        return {"ok": True, "message": f"Connected. '{settings.game_name}' has {len(live)} live streams right now.", "game_id": gid}
    except TwitchError:
        return {"ok": True, "message": f"Keys work, but Twitch has no category named '{settings.game_name}' yet. "
                                        "Before launch, test with 'Grand Theft Auto V'."}


@app.post("/api/settings/cleanup")
def cleanup_now():
    from ..maintenance import cleanup_recordings
    return cleanup_recordings()


# ---------------------------------------------------------------- discovery
@app.get("/api/discover/live")
def discover(limit: int = 20, language: Optional[str] = None):
    if not settings.has_twitch:
        raise HTTPException(400, "Add TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET to .env to enable discovery.")
    from ..discovery.twitch import get_client
    with session_scope() as db:
        tracked = {s.handle for s in db.exec(select(Source).where(Source.active == True)).all()}  # noqa: E712
    streams = get_client().live_streams(limit, language)
    return [
        {
            "handle": s["user_login"], "display_name": s["user_name"], "viewers": s["viewer_count"],
            "title": s["title"], "language": s["language"], "started_at": s["started_at"],
            "thumbnail": s["thumbnail_url"].replace("{width}", "320").replace("{height}", "180"),
            "tracked": s["user_login"] in tracked,
        }
        for s in streams
    ]


# ---------------------------------------------------------------- sessions
@app.get("/api/sessions/all")
def sessions():
    with session_scope() as db:
        rows = db.exec(select(Session).order_by(Session.id.desc()).limit(100)).all()
        sources = {s.id: s for s in db.exec(select(Source)).all()}
        counts = dict(db.exec(select(Clip.session_id, func.count(Clip.id)).group_by(Clip.session_id)).all())
        return [
            {**r.model_dump(), "handle": sources[r.source_id].handle if r.source_id in sources else "?",
             "clips": counts.get(r.id, 0)}
            for r in rows
        ]


class ImportBody(BaseModel):
    source_id: int
    path_or_url: str
    chat_path: Optional[str] = None


@app.post("/api/sessions/import")
def import_session(body: ImportBody):
    from ..ingest.recorder import import_media

    def go():
        sid = import_media(body.source_id, body.path_or_url, body.chat_path)
        process_session(sid)
    _bg(f"import:{body.path_or_url}", go)
    return {"queued": True}


@app.post("/api/sessions/{session_id}/process")
def process(session_id: int):
    _bg(f"session:{session_id}", process_session, session_id)
    return {"queued": True}


# ---------------------------------------------------------------- clips
def _clip_out(c: Clip, sources: dict, campaigns: dict) -> dict:
    src = sources.get(c.source_id)
    camp = campaigns.get(c.campaign_id)
    d = c.model_dump()
    d.update(
        duration=c.duration,
        features=json.loads(c.features or "{}"),
        handle=src.handle if src else "?",
        streamer=(src.display_name or src.handle) if src else "?",
        campaign=camp.name if camp else None,
        payout_per_1k=camp.payout_per_1k if camp else 0,
        issues=rules.check(c, camp) if c.path else [],
        video_url=f"/api/clips/{c.id}/video?v={int(Path(c.path).stat().st_mtime) if c.path and Path(c.path).exists() else 0}",
        thumb_url=f"/api/clips/{c.id}/thumb",
    )
    return d


@app.get("/api/clips/list")
def clips(status: str = "pending", limit: int = 200):
    with session_scope() as db:
        q = select(Clip).order_by(Clip.score.desc()).limit(limit)
        if status != "all":
            q = q.where(Clip.status == status)
        rows = db.exec(q).all()
        sources = {s.id: s for s in db.exec(select(Source)).all()}
        campaigns = {c.id: c for c in db.exec(select(Campaign)).all()}
        return [_clip_out(c, sources, campaigns) for c in rows]


class ClipPatch(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None


@app.patch("/api/clips/{clip_id}")
def patch_clip(clip_id: int, body: ClipPatch):
    with session_scope() as db:
        c = db.get(Clip, clip_id)
        if not c:
            raise HTTPException(404)
        if body.title is not None and body.title != c.title:
            c.title = body.title.strip()
            src = db.get(Source, c.source_id)
            camp = db.get(Campaign, c.campaign_id) if c.campaign_id else None
            c.caption = rules.build_caption(c.title, src, camp)
        if body.status in ("approved", "rejected", "pending"):
            c.status = body.status
            c.reviewed_at = utcnow()
        db.add(c)
        db.commit()
        db.refresh(c)
        return {"ok": True, "status": c.status}


class RecutBody(BaseModel):
    start_delta: float = 0
    end_delta: float = 0
    layout: Optional[str] = None
    title: Optional[str] = None


@app.post("/api/clips/{clip_id}/recut")
def recut_clip(clip_id: int, body: RecutBody):
    try:
        c = recut(clip_id, body.start_delta, body.end_delta, body.layout, body.title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with session_scope() as db:
        sources = {s.id: s for s in db.exec(select(Source)).all()}
        campaigns = {x.id: x for x in db.exec(select(Campaign)).all()}
    return _clip_out(c, sources, campaigns)


@app.post("/api/clips/render-more")
def render_more(count: int = 5):
    """Render the next-best unrendered candidates."""
    from ..pipeline import render_clip
    with session_scope() as db:
        ids = [c.id for c in db.exec(
            select(Clip).where(Clip.status == "candidate").order_by(Clip.score.desc()).limit(count)
        ).all()]

    def go():
        for i in ids:
            try:
                render_clip(i)
            except Exception:
                pass
    _bg("render-more", go)
    return {"queued": len(ids)}


def _file(clip_id: int, attr: str, media: str):
    with session_scope() as db:
        c = db.get(Clip, clip_id)
    p = getattr(c, attr, "") if c else ""
    if not p or not Path(p).exists():
        raise HTTPException(404)
    return FileResponse(p, media_type=media)


@app.get("/api/clips/{clip_id}/video")
def clip_video(clip_id: int):
    return _file(clip_id, "path", "video/mp4")


@app.get("/api/clips/{clip_id}/thumb")
def clip_thumb(clip_id: int):
    return _file(clip_id, "thumb_path", "image/jpeg")


# ---------------------------------------------------------------- schedule / publish
@app.post("/api/schedule")
def schedule(day: Optional[str] = None):
    d = date.fromisoformat(day) if day else _today()
    return {"scheduled": len(schedule_day(d))}


@app.get("/api/publications")
def publications(day: Optional[str] = None):
    with session_scope() as db:
        q = select(Publication).order_by(Publication.scheduled_for)
        rows = db.exec(q).all()
        if day:
            d = date.fromisoformat(day)
            rows = [p for p in rows if p.scheduled_for.astimezone(settings.tz).date() == d]
        clips_ = {c.id: c for c in db.exec(select(Clip)).all()}
        dests = {d.id: d for d in db.exec(select(Destination)).all()}
        sources = {s.id: s for s in db.exec(select(Source)).all()}
        out = []
        for p in rows:
            c, dst = clips_.get(p.clip_id), dests.get(p.destination_id)
            src = sources.get(c.source_id) if c else None
            out.append({
                **p.model_dump(),
                "title": c.title if c else "", "caption": c.caption if c else "", "score": c.score if c else 0,
                "streamer": (src.display_name or src.handle) if src else "?",
                "platform": dst.platform if dst else "?", "account": dst.account_name if dst else "?",
                "thumb_url": f"/api/clips/{p.clip_id}/thumb", "video_url": f"/api/clips/{p.clip_id}/video",
            })
        return out


class PubPatch(BaseModel):
    status: Optional[str] = None
    url: Optional[str] = None
    views: Optional[int] = None


@app.patch("/api/publications/{pub_id}")
def patch_pub(pub_id: int, body: PubPatch):
    with session_scope() as db:
        p = db.get(Publication, pub_id)
        if not p:
            raise HTTPException(404)
        if body.url is not None:
            p.url = body.url
        if body.status in ("scheduled", "posted", "skipped"):
            p.status = body.status
            if body.status == "posted" and not p.posted_at:
                p.posted_at = utcnow()
        if body.views is not None:
            clip = db.get(Clip, p.clip_id)
            camp = db.get(Campaign, clip.campaign_id) if clip and clip.campaign_id else None
            if camp:
                camp.budget_spent = max(0.0, camp.budget_spent + (body.views - p.views) / 1000 * camp.payout_per_1k)
                db.add(camp)
            p.views = max(0, body.views)
            if p.status == "scheduled":
                p.status, p.posted_at = "posted", p.posted_at or utcnow()
        db.add(p)
        db.commit()
        db.refresh(p)
        return p


@app.delete("/api/publications/{pub_id}")
def delete_pub(pub_id: int):
    with session_scope() as db:
        p = db.get(Publication, pub_id)
        if p:
            db.delete(p)
            db.commit()
    return {"ok": True}


@app.get("/api/export")
def export(day: Optional[str] = None):
    """Zip of the day's posts: one folder per account, each post = mp4 + caption.txt."""
    pubs = publications(day or _today().isoformat())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        with session_scope() as db:
            for i, p in enumerate(pubs, 1):
                c = db.get(Clip, p["clip_id"])
                if not c or not c.path or not Path(c.path).exists():
                    continue
                when = p["scheduled_for"].astimezone(settings.tz).strftime("%H%M")
                folder = f"{p['platform']}_{p['account']}".replace("/", "_").replace("@", "")
                stem = f"{folder}/{when}_clip{c.id}"
                z.write(c.path, f"{stem}.mp4")
                z.writestr(f"{stem}.txt", c.caption)
    buf.seek(0)
    name = f"clipcore_{day or _today().isoformat()}.zip"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------- stats / analytics
@app.get("/api/overview/stats")
def stats():
    today = datetime.now(settings.tz).replace(hour=0, minute=0, second=0, microsecond=0)
    with session_scope() as db:
        clips_ = db.exec(select(Clip)).all()
        pubs = db.exec(select(Publication)).all()
        campaigns = {c.id: c for c in db.exec(select(Campaign)).all()}
        clip_camp = {c.id: c.campaign_id for c in clips_}
        sources = db.exec(select(Source).where(Source.active == True)).all()  # noqa: E712
        sess = db.exec(select(Session).where(Session.status.in_(["recording", "processing"]))).all()
    earnings = sum(
        p.views / 1000 * campaigns[clip_camp[p.clip_id]].payout_per_1k
        for p in pubs if clip_camp.get(p.clip_id) in campaigns
    )
    return {
        "pending": sum(c.status == "pending" for c in clips_),
        "candidates": sum(c.status == "candidate" for c in clips_),
        "approved_unscheduled": sum(
            c.status == "approved" and c.id not in {p.clip_id for p in pubs} for c in clips_
        ),
        "scheduled_today": sum(
            p.status != "skipped" and today <= p.scheduled_for < today + timedelta(days=1) for p in pubs
        ),
        "views": sum(p.views for p in pubs),
        "earnings": round(earnings, 2),
        "sources": len(sources),
        "live_jobs": [{"id": s.id, "status": s.status} for s in sess],
        "transcription": _whisper_available(),
        "low_disk": _low_disk(),
        "twitch": settings.has_twitch,
    }


def _low_disk() -> bool:
    from ..maintenance import disk_usage
    return disk_usage()["free_gb"] < settings.min_free_gb


def _whisper_available() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("faster_whisper") is not None
    except Exception:
        return False


@app.get("/api/overview/analytics")
def analytics():
    with session_scope() as db:
        clips_ = {c.id: c for c in db.exec(select(Clip)).all()}
        pubs = db.exec(select(Publication)).all()
        campaigns = {c.id: c for c in db.exec(select(Campaign)).all()}
        sources = {s.id: s for s in db.exec(select(Source)).all()}
        dests = {d.id: d for d in db.exec(select(Destination)).all()}

    def bucket():
        return {"clips": 0, "posted": 0, "views": 0, "earnings": 0.0}

    by_campaign: dict[str, dict] = {}
    by_source: dict[str, dict] = {}
    by_account: dict[str, dict] = {}
    for p in pubs:
        c = clips_.get(p.clip_id)
        if not c:
            continue
        camp = campaigns.get(c.campaign_id)
        rate = camp.payout_per_1k if camp else 0
        keys = [
            (by_campaign, camp.name if camp else "—"),
            (by_source, (sources[c.source_id].display_name or sources[c.source_id].handle) if c.source_id in sources else "?"),
            (by_account, f"{dests[p.destination_id].platform} · {dests[p.destination_id].account_name}" if p.destination_id in dests else "?"),
        ]
        for table, key in keys:
            b = table.setdefault(key, bucket())
            b["clips"] += 1
            b["posted"] += p.status == "posted"
            b["views"] += p.views
            b["earnings"] = round(b["earnings"] + p.views / 1000 * rate, 2)

    def rows(t):
        out = [{"name": k, **v, "avg_views": round(v["views"] / v["posted"]) if v["posted"] else 0} for k, v in t.items()]
        return sorted(out, key=lambda r: r["earnings"], reverse=True)

    budgets = [
        {"name": c.name, "spent": round(c.budget_spent, 2), "total": c.budget_total, "rate": c.payout_per_1k,
         "remaining": round(c.budget_total - c.budget_spent, 2) if c.budget_total else None}
        for c in campaigns.values() if c.active
    ]
    reviewed = [c for c in clips_.values() if c.status in ("approved", "rejected")]
    return {
        "by_campaign": rows(by_campaign), "by_source": rows(by_source), "by_account": rows(by_account),
        "budgets": budgets,
        "approval_rate": round(sum(c.status == "approved" for c in reviewed) / len(reviewed) * 100) if reviewed else None,
        "weights": load_weights(),
        "learn_samples": sum(1 for p in pubs if p.views > 0),
        "learn_needed": learning.MIN_SAMPLES,
    }


@app.post("/api/overview/learn")
def run_learn():
    return learning.learn()


@app.get("/api/overview/jobs")
def jobs():
    return _jobs


# ---------------------------------------------------------------- generic CRUD
CRUD = {"campaigns": Campaign, "sources": Source, "destinations": Destination}


@app.get("/api/{kind}")
def list_items(kind: str):
    if kind not in CRUD:
        raise HTTPException(404)
    with session_scope() as db:
        return db.exec(select(CRUD[kind]).order_by(CRUD[kind].id)).all()


@app.post("/api/{kind}")
def create_item(kind: str, body: dict[str, Any]):
    if kind not in CRUD:
        raise HTTPException(404)
    model = CRUD[kind]
    body = {k: v for k, v in body.items() if k in model.model_fields and k not in ("id", "created_at")}
    if kind == "sources":
        body["handle"] = body.get("handle", "").strip().lower().lstrip("@")
        if not body["handle"]:
            raise HTTPException(400, "handle required")
        if not body.get("campaign_id"):
            raise HTTPException(400, "A source must belong to a campaign — that is your permission to clip it.")
        if settings.has_twitch:
            try:
                from ..discovery.twitch import get_client
                u = get_client().users([body["handle"]]).get(body["handle"])
                if u:
                    body["broadcaster_id"] = u["id"]
                    body["display_name"] = body.get("display_name") or u["display_name"]
            except Exception:
                pass
    item = model.model_validate(body)
    with session_scope() as db:
        db.add(item)
        db.commit()
        db.refresh(item)
        return item


@app.patch("/api/{kind}/{item_id}")
def update_item(kind: str, item_id: int, body: dict[str, Any]):
    if kind not in CRUD:
        raise HTTPException(404)
    model = CRUD[kind]
    with session_scope() as db:
        item = db.get(model, item_id)
        if not item:
            raise HTTPException(404)
        for k, v in body.items():
            if k in model.model_fields and k not in ("id", "created_at"):
                setattr(item, k, v)
        db.add(item)
        db.commit()
        db.refresh(item)
        return item


@app.delete("/api/{kind}/{item_id}")
def delete_item(kind: str, item_id: int):
    if kind not in CRUD:
        raise HTTPException(404)
    with session_scope() as db:
        item = db.get(CRUD[kind], item_id)
        if item:
            if kind == "sources":
                item.active = False          # keep history; sources are referenced by clips
                db.add(item)
            else:
                db.delete(item)
            db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- static UI
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
