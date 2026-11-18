"""REST API + web app."""
from __future__ import annotations

import io
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlmodel import func, select

from .. import auth
from ..config import settings
from ..db import init_db, session_scope
from ..jobs import auto_count, delete_job, queue, queue_position
from ..models import Job, Short, User

init_db()
app = FastAPI(title="ClipCore", version="1.0.0")
WEB = Path(__file__).resolve().parent.parent / "web"


@app.on_event("startup")
def _start_queue() -> None:
    queue.start()


# ---------------------------------------------------------------- auth plumbing
OPEN_PATHS = {"/login.html", "/styles.css", "/favicon.svg", "/api/auth/status", "/api/auth/signup", "/api/auth/login"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    user = auth.user_from_token(request.cookies.get(auth.COOKIE))
    request.state.user = user
    if path in OPEN_PATHS or user:
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Not logged in"}, status_code=401)
    return RedirectResponse("/login.html", status_code=303)


def current_user(request: Request) -> User:
    return request.state.user


def admin_user(request: Request) -> User:
    u = request.state.user
    if not u or not u.is_admin:
        raise HTTPException(403, "Admins only.")
    return u


def _session(request: Request, user: User) -> JSONResponse:
    resp = JSONResponse({"ok": True, "email": user.email})
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(auth.COOKIE, auth.make_token(user), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=https)
    return resp


_attempts: dict[str, list[float]] = {}


def _throttle(request: Request) -> None:
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    now = time.time()
    hits = [t for t in _attempts.get(ip, []) if now - t < 600]
    if len(hits) >= 15:
        raise HTTPException(429, "Too many attempts. Wait a few minutes and try again.")
    hits.append(now)
    _attempts[ip] = hits


class Creds(BaseModel):
    email: str
    password: str
    invite: str = ""


@app.get("/api/auth/status")
def auth_status(request: Request):
    u = request.state.user
    return {"logged_in": bool(u), "first_user": auth.user_count() == 0,
            "invite_required": bool(settings.invite_code) and auth.user_count() > 0}


@app.post("/api/auth/signup")
def signup(body: Creds, request: Request):
    _throttle(request)
    try:
        user = auth.signup(body.email, body.password, body.invite)
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    return _session(request, user)


@app.post("/api/auth/login")
def login(body: Creds, request: Request):
    _throttle(request)
    try:
        user = auth.login(body.email, body.password)
    except auth.AuthError as e:
        time.sleep(0.5)
        raise HTTPException(401, str(e))
    return _session(request, user)


@app.post("/api/auth/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


class PwChange(BaseModel):
    current: str
    new: str


@app.post("/api/auth/password")
def change_password(body: PwChange, request: Request, user: User = Depends(current_user)):
    try:
        auth.login(user.email, body.current)
        auth.set_password(user.id, body.new)
    except auth.AuthError as e:
        raise HTTPException(400, "Current password is wrong." if "Wrong" in str(e) else str(e))
    with session_scope() as db:
        fresh = db.get(User, user.id)
    return _session(request, fresh)


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return {"email": user.email, "is_admin": user.is_admin}


# ---------------------------------------------------------------- jobs
URL_RE = re.compile(r"^https?://\S+$")


class NewJob(BaseModel):
    url: str
    clip_count: int = 0


def _job_out(j: Job, shorts: int = 0) -> dict:
    d = j.model_dump()
    d["shorts"] = shorts
    d["position"] = queue_position(j.id) if j.status == "queued" else 0
    return d


@app.post("/api/jobs")
def create_job(body: NewJob, user: User = Depends(current_user)):
    url = body.url.strip()
    if not URL_RE.match(url):
        raise HTTPException(400, "Paste a full link that starts with https://")
    if body.clip_count not in (0, 5, 10, 15, 20):
        raise HTTPException(400, "Choose 5, 10, 15 or 20 clips (or Auto).")
    with session_scope() as db:
        active = db.exec(select(func.count(Job.id)).where(
            Job.user_id == user.id, Job.status.in_(["queued", "running"]))).one()
        if active >= settings.jobs_per_user:
            raise HTTPException(429, f"You already have {active} videos processing. Wait for one to finish.")
        j = Job(user_id=user.id, url=url, clip_count=body.clip_count)
        db.add(j)
        db.commit()
        db.refresh(j)
        return _job_out(j)


@app.get("/api/jobs")
def list_jobs(user: User = Depends(current_user)):
    with session_scope() as db:
        jobs = db.exec(select(Job).where(Job.user_id == user.id).order_by(Job.id.desc()).limit(100)).all()
        counts = dict(db.exec(select(Short.job_id, func.count(Short.id))
                              .where(Short.user_id == user.id).group_by(Short.job_id)).all())
    return [_job_out(j, counts.get(j.id, 0)) for j in jobs]


def _own_job(job_id: int, user: User) -> Job:
    with session_scope() as db:
        j = db.get(Job, job_id)
    if not j or j.user_id != user.id:
        raise HTTPException(404, "Not found.")
    return j


def _short_out(s: Short) -> dict:
    import json
    v = int(Path(s.path).stat().st_mtime) if s.path and Path(s.path).exists() else 0
    return {
        "id": s.id, "rank": s.rank, "title": s.title, "score": s.score, "duration": s.duration,
        "start": s.start_t, "end": s.end_t, "transcript": s.transcript, "features": json.loads(s.features or "{}"),
        "video_url": f"/api/shorts/{s.id}/video?v={v}", "thumb_url": f"/api/shorts/{s.id}/thumb?v={v}",
        "download_url": f"/api/shorts/{s.id}/download",
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int, user: User = Depends(current_user)):
    j = _own_job(job_id, user)
    with session_scope() as db:
        shorts = db.exec(select(Short).where(Short.job_id == job_id).order_by(Short.rank)).all()
    return {**_job_out(j, len(shorts)), "clips": [_short_out(s) for s in shorts]}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: int, user: User = Depends(current_user)):
    j = _own_job(job_id, user)
    if j.status != "failed":
        raise HTTPException(400, "Only failed videos can be retried.")
    with session_scope() as db:
        row = db.get(Job, job_id)
        row.status, row.stage, row.progress, row.error = "queued", "Waiting in line", 0, ""
        db.add(row)
        db.commit()
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def remove_job(job_id: int, user: User = Depends(current_user)):
    j = _own_job(job_id, user)
    if j.status == "running":
        raise HTTPException(400, "This video is processing. Delete it once it finishes.")
    delete_job(job_id)
    return {"ok": True}


def _slug(text: str, n: int = 50) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:n].strip("-") or "clip"


@app.get("/api/jobs/{job_id}/zip")
def download_zip(job_id: int, user: User = Depends(current_user)):
    j = _own_job(job_id, user)
    with session_scope() as db:
        shorts = db.exec(select(Short).where(Short.job_id == job_id).order_by(Short.rank)).all()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for s in shorts:
            if s.path and Path(s.path).exists():
                z.write(s.path, f"{s.rank:02d}-{_slug(s.title)}.mp4")
    buf.seek(0)
    name = f"{_slug(j.channel or 'clips', 30)}-{_slug(j.title, 40)}.zip"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------- shorts
def _own_short(short_id: int, user: User) -> Short:
    with session_scope() as db:
        s = db.get(Short, short_id)
    if not s or s.user_id != user.id or not s.path or not Path(s.path).exists():
        raise HTTPException(404, "Not found.")
    return s


@app.get("/api/shorts/{short_id}/video")
def short_video(short_id: int, user: User = Depends(current_user)):
    return FileResponse(_own_short(short_id, user).path, media_type="video/mp4")


@app.get("/api/shorts/{short_id}/thumb")
def short_thumb(short_id: int, user: User = Depends(current_user)):
    return FileResponse(_own_short(short_id, user).thumb_path, media_type="image/jpeg")


@app.get("/api/shorts/{short_id}/download")
def short_download(short_id: int, user: User = Depends(current_user)):
    s = _own_short(short_id, user)
    return FileResponse(s.path, media_type="video/mp4", filename=f"{s.rank:02d}-{_slug(s.title)}.mp4")


@app.delete("/api/shorts/{short_id}")
def short_delete(short_id: int, user: User = Depends(current_user)):
    s = _own_short(short_id, user)
    Path(s.path).parent.exists() and __import__("shutil").rmtree(Path(s.path).parent, ignore_errors=True)
    with session_scope() as db:
        db.delete(db.get(Short, short_id))
        db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- admin
@app.get("/api/admin/overview")
def admin_overview(_: User = Depends(admin_user)):
    from ..maintenance import disk_usage
    with session_scope() as db:
        users = db.exec(select(User).order_by(User.id)).all()
        jobs = dict(db.exec(select(Job.user_id, func.count(Job.id)).group_by(Job.user_id)).all())
        clips = dict(db.exec(select(Short.user_id, func.count(Short.id)).group_by(Short.user_id)).all())
        status = dict(db.exec(select(Job.status, func.count(Job.id)).group_by(Job.status)).all())
    return {
        "settings": settings.public(),
        "disk": disk_usage(),
        "queue": status,
        "users": [{"id": u.id, "email": u.email, "is_admin": u.is_admin, "disabled": u.disabled,
                   "created_at": u.created_at, "videos": jobs.get(u.id, 0), "clips": clips.get(u.id, 0)} for u in users],
    }


@app.post("/api/admin/settings")
def admin_settings(body: dict[str, Any], _: User = Depends(admin_user)):
    try:
        settings.save(body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    from ..discovery.twitch import reset_client
    reset_client()
    return settings.public()


@app.post("/api/admin/test-twitch")
def admin_test_twitch(_: User = Depends(admin_user)):
    if not settings.has_twitch:
        raise HTTPException(400, "Enter the Client ID and Client Secret first, then Save.")
    from ..discovery.twitch import TwitchError, get_client
    try:
        get_client()._auth()
    except TwitchError:
        raise HTTPException(400, "Twitch rejected these keys. Re-copy the Client ID, or generate a New Secret.")
    except Exception as e:
        raise HTTPException(502, f"Could not reach Twitch: {e}")
    return {"ok": True, "message": "Connected. Viewer-clip ranking is on for Twitch links."}


@app.post("/api/admin/test-ai")
def admin_test_ai(_: User = Depends(admin_user)):
    from .. import llm
    if not llm.available():
        raise HTTPException(400, "Paste your Anthropic API key first, then Save.")
    try:
        return {"ok": True, "message": llm.test_key()}
    except ValueError as e:
        raise HTTPException(400, str(e))


class UserPatch(BaseModel):
    disabled: Optional[bool] = None
    password: Optional[str] = None


@app.patch("/api/admin/users/{user_id}")
def admin_user_patch(user_id: int, body: UserPatch, admin: User = Depends(admin_user)):
    if user_id == admin.id and body.disabled:
        raise HTTPException(400, "You can't disable your own account.")
    with session_scope() as db:
        u = db.get(User, user_id)
        if not u:
            raise HTTPException(404)
        if body.disabled is not None:
            u.disabled = body.disabled
            db.add(u)
            db.commit()
    if body.password:
        try:
            auth.set_password(user_id, body.password)
        except auth.AuthError as e:
            raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/admin/cleanup")
def admin_cleanup(_: User = Depends(admin_user)):
    from ..maintenance import cleanup
    return cleanup()


# ---------------------------------------------------------------- static web app
app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
