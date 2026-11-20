"""Web app + API.

- Owner app at /       : onboarding, then three panels (Content, Funnels, Leads) + Setup.
- Admin app at /admin  : the program owner enrolls customers and customizes for them.
- Public funnel pages  : /f/{slug}/{funnel}, or the customer's own domain (go.theirbusiness.com/{funnel}).
"""
from __future__ import annotations

import base64
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, automation, brand, content, funnels, meta, reviews, voice
from .config import settings
from .db import db, init_db, now, row
from .profile import FIELDS, SYSTEM_FIELDS, complete, missing

WEB = Path(__file__).resolve().parent / "web"
STAGES = ["new", "contacted", "quoted", "won", "lost"]
HEX6 = re.compile(r"^#[0-9a-fA-F]{6}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    automation.worker.start()
    yield
    automation.worker.shutdown()


app = FastAPI(title="StandOut", lifespan=lifespan)


# ---------------------------------------------------------------- custom domains
def _base_host() -> str:
    return settings.base_url.split("://", 1)[-1].split("/")[0].split(":")[0].lower()


@app.middleware("http")
async def custom_domains(request: Request, call_next):
    """go.theirbusiness.com/ -> their Free Estimate page; go.theirbusiness.com/special -> that funnel."""
    host = request.headers.get("host", "").split(":")[0].lower()
    if host and host not in (_base_host(), "localhost", "127.0.0.1", "testserver"):
        with db() as conn:
            r = conn.execute("SELECT slug FROM accounts WHERE funnel_domain = ?", (host,)).fetchone()
        if r:
            path = request.scope["path"].strip("/")
            if path.startswith("media/"):
                return await call_next(request)
            key = path or "estimate"
            if key not in funnels.FUNNELS:
                return HTMLResponse("Page not found", status_code=404)
            request.scope["path"] = f"/f/{r['slug']}/{key}"
            request.scope["raw_path"] = request.scope["path"].encode()
            request.state.custom_action = f"/{key}"
    return await call_next(request)


# ---------------------------------------------------------------- session helpers
def session(request: Request) -> dict:
    data = auth.read_token(request.cookies.get(auth.COOKIE))
    if not data:
        raise HTTPException(401, "Not logged in")
    return data


PLAN_STATUSES = ("active", "past_due", "canceled")


def account_id(s: dict = Depends(session)) -> int:
    if not s.get("adm"):
        with db() as conn:
            r = conn.execute("SELECT plan_status FROM accounts WHERE id = ?", (s["a"],)).fetchone()
        if r and r["plan_status"] == "canceled":
            raise HTTPException(402, "Your subscription is canceled. Reactivate it to get back in.")
    return int(s["a"])


def admin_id(s: dict = Depends(session)) -> int:
    uid = int(s.get("adm") or s["a"])
    with db() as conn:
        r = conn.execute("SELECT is_admin FROM accounts WHERE id = ?", (uid,)).fetchone()
    if not r or not r["is_admin"]:
        raise HTTPException(403, "Admins only.")
    return uid


def load_account(conn, aid: int) -> dict:
    a = row(conn.execute("SELECT * FROM accounts WHERE id = ?", (aid,)).fetchone(), ("profile",))
    if not a:
        raise HTTPException(401, "Account not found")
    return a


def save_profile_dict(conn, aid: int, prof: dict) -> None:
    conn.execute("UPDATE accounts SET profile = ? WHERE id = ?", (json.dumps(prof), aid))


def ensure_funnels(conn, aid: int) -> None:
    for key in funnels.FUNNELS:
        conn.execute("INSERT OR IGNORE INTO funnels (account_id, key, sequence) VALUES (?,?,?)",
                     (aid, key, json.dumps(funnels.default_sequence(key))))


def funnel_link(acct: dict, key: str) -> str:
    if acct.get("funnel_domain"):
        return f"https://{acct['funnel_domain']}/{key}"
    return f"{settings.base_url}/f/{acct['slug']}/{key}"


def limit_for(acct: dict) -> int:
    return acct.get("contact_limit") or settings.contact_limit


def contact_count(conn, aid: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM contacts WHERE account_id = ?", (aid,)).fetchone()[0]


def _cookie(resp: Response, token: str) -> Response:
    resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax",
                    secure=settings.base_url.startswith("https"))
    return resp


def _session(aid: int, admin: int | None = None, payload: dict | None = None) -> JSONResponse:
    return _cookie(JSONResponse(payload or {"ok": True}), auth.make_token(aid, admin))


def _kick_followups() -> None:
    threading.Thread(target=automation.run_due, daemon=True).start()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _record(conn, aid: int, funnel: str, kind: str) -> None:
    conn.execute("INSERT INTO events (account_id, funnel, kind, day, created) VALUES (?,?,?,?,?)",
                 (aid, funnel, kind, dt.date.today().isoformat(), now()))


# ---------------------------------------------------------------- pages
@app.get("/", include_in_schema=False)
def home(request: Request):
    if not auth.account_from_token(request.cookies.get(auth.COOKIE)):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse((WEB / "index.html").read_text())


@app.get("/login", include_in_schema=False)
def login_page():
    return HTMLResponse((WEB / "login.html").read_text())


@app.get("/welcome", include_in_schema=False)
def welcome_page():
    return HTMLResponse((WEB / "welcome.html").read_text())


@app.get("/admin", include_in_schema=False)
def admin_page(request: Request):
    data = auth.read_token(request.cookies.get(auth.COOKIE))
    if not data:
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse((WEB / "admin.html").read_text())


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/media/{aid}/{name}", include_in_schema=False)
def media(aid: int, name: str):
    if not re.fullmatch(r"logo-[0-9a-f]{12}\.(png|jpg|gif|webp|svg)", name):
        raise HTTPException(404)
    path = settings.data_dir / "uploads" / str(aid) / name
    if not path.is_file():
        raise HTTPException(404)
    types = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml"}
    # Uploaded SVGs can carry scripts: never let them run, even if opened directly.
    return FileResponse(path, media_type=types[name.rsplit(".", 1)[1]], headers={
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=86400"})


# ---------------------------------------------------------------- auth
class SignupIn(BaseModel):
    email: str
    password: str
    business: str
    code: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


class SetupIn(BaseModel):
    token: str
    password: str


@app.post("/api/signup")
def signup(body: SignupIn):
    try:
        aid = auth.signup(body.email, body.password, body.business, body.code)
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    with db() as conn:
        ensure_funnels(conn, aid)
    return _session(aid)


@app.post("/api/login")
def login(body: LoginIn):
    try:
        aid = auth.login(body.email, body.password)
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    with db() as conn:
        is_admin = conn.execute("SELECT is_admin FROM accounts WHERE id = ?", (aid,)).fetchone()["is_admin"]
    return _session(aid, payload={"ok": True, "admin": bool(is_admin)})


@app.post("/api/setup-password")
def setup_password(body: SetupIn):
    try:
        aid = auth.redeem_setup(body.token, body.password)
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    return _session(aid)


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/api/auth/config")
def auth_config():
    return {"needs_code": bool(settings.invite_code)}


# ---------------------------------------------------------------- profile + home
def me_payload(conn, aid: int, s: dict | None = None) -> dict:
    a = load_account(conn, aid)
    prof = a["profile"]
    safe = {k: v for k, v in prof.items() if k not in ("fb_page_token", "capi_token", "site_text", "edge_report")}
    safe["fb_connected"] = bool(prof.get("fb_page_id") and prof.get("fb_page_token"))
    safe["capi_connected"] = meta.connected(prof)
    safe["has_site_text"] = bool(prof.get("site_text"))
    return {"email": a["email"], "slug": a["slug"], "profile": safe, "fields": FIELDS,
            "missing": missing(prof), "onboarded": bool(a["onboarded"]), "is_admin": bool(a["is_admin"]),
            "acting_as": bool(s and s.get("adm")), "funnel_domain": a["funnel_domain"] or "",
            "cname_target": settings.cname_target or _base_host(),
            "contacts_used": contact_count(conn, aid), "contact_limit": limit_for(a),
            "plan_status": a["plan_status"], "community_url": settings.community_url,
            "edge_available": reviews.available(), "edge_count": len(reviews.confirmed(prof)),
            "ai": content.ai_available(),
            "sms_live": bool(settings.twilio_sid and settings.twilio_from),
            "email_live": bool(settings.smtp_host and settings.smtp_from)}


@app.get("/api/me")
def me(s: dict = Depends(session)):
    with db() as conn:
        conn.execute("UPDATE accounts SET last_seen = ? WHERE id = ?", (now(), s["a"]))
        return me_payload(conn, int(s["a"]), s)


def apply_profile(prof: dict, body: dict) -> dict:
    allowed = (set(FIELDS) - {"owner_email"}) | {"fb_page_id", "fb_page_token", "capi_token"}
    for k, v in body.items():
        if k not in allowed or not isinstance(v, str):
            continue
        v = v.strip()[:5000]
        if k in ("fb_page_token", "capi_token") and not v:
            continue  # blank means "keep the saved secret"
        if k in ("color", "color2") and v and not HEX6.match(v):
            raise HTTPException(400, "Colors must look like #1f6feb.")
        if k == "pixel_id" and v and not meta.PIXEL_RE.match(v):
            raise HTTPException(400, "The Pixel ID is the 15-16 digit number from Events Manager.")
        if k == "lead_value" and v:
            try:
                v = str(max(0.0, float(v.replace("$", "").replace(",", ""))))
            except ValueError:
                raise HTTPException(400, "Lead value must be a number.")
        prof[k] = v
    return prof


@app.put("/api/profile")
def save_profile(body: dict, aid: int = Depends(account_id)):
    with db() as conn:
        a = load_account(conn, aid)
        prof = apply_profile(a["profile"], body)
        save_profile_dict(conn, aid, prof)
    return {"ok": True, "missing": missing(prof)}


@app.post("/api/onboarding/done")
def onboarding_done(aid: int = Depends(account_id)):
    with db() as conn:
        conn.execute("UPDATE accounts SET onboarded = 1 WHERE id = ?", (aid,))
    return {"ok": True}


# ---------------------------------------------------------------- brand: website import, logo, domain, voice
class ImportIn(BaseModel):
    url: str
    replace_logo: bool = True


def import_brand(aid: int, url: str, replace_logo: bool = True) -> dict:
    try:
        site = brand.import_site(url)
    except brand.FetchError as e:
        raise HTTPException(400, str(e))
    logo = ""
    with db() as conn:
        prof = load_account(conn, aid)["profile"]
    if site["logo_url"] and (replace_logo or not prof.get("logo")):
        try:
            logo = brand.download_logo(aid, site["logo_url"])
        except brand.FetchError:
            logo = ""
    with db() as conn:
        prof = load_account(conn, aid)["profile"]
        prof["website"] = site["url"]
        prof["site_text"] = site["text"]
        prof["site_description"] = site["description"]
        if site["colors"]:
            prof["color"] = site["colors"][0]
            prof["color2"] = site["colors"][1] if len(site["colors"]) > 1 else site["colors"][0]
        if site["font"]:
            prof["font"] = site["font"]
        if logo:
            prof["logo"] = logo
        if site["name"] and not prof.get("business"):
            prof["business"] = site["name"]
        save_profile_dict(conn, aid, prof)
    return {"colors": site["colors"], "font": site["font"], "logo": logo or prof.get("logo", ""),
            "name": site["name"], "description": site["description"], "words": len(site["text"].split())}


@app.post("/api/brand/import")
def brand_import(body: ImportIn, aid: int = Depends(account_id)):
    return import_brand(aid, body.url, body.replace_logo)


@app.post("/api/brand/logo")
async def brand_logo(file: UploadFile = File(...), aid: int = Depends(account_id)):
    data = await file.read(brand.MAX_IMAGE + 1)
    try:
        path = brand.save_logo(aid, data)
    except brand.FetchError as e:
        raise HTTPException(400, str(e))
    with db() as conn:
        prof = load_account(conn, aid)["profile"]
        prof["logo"] = path
        save_profile_dict(conn, aid, prof)
    return {"logo": path}


class DomainIn(BaseModel):
    domain: str


@app.put("/api/domain")
def set_domain(body: DomainIn, aid: int = Depends(account_id)):
    try:
        d = brand.clean_domain(body.domain)
    except brand.FetchError as e:
        raise HTTPException(400, str(e))
    if d and d == _base_host():
        raise HTTPException(400, "Use your own domain, like go.yourbusiness.com")
    with db() as conn:
        taken = conn.execute("SELECT 1 FROM accounts WHERE funnel_domain = ? AND id != ?", (d, aid)).fetchone()
        if d and taken:
            raise HTTPException(400, "That domain is already connected to another account.")
        conn.execute("UPDATE accounts SET funnel_domain = ? WHERE id = ?", (d or None, aid))
    return {"domain": d}


@app.get("/api/domain/check")
def check_domain(aid: int = Depends(account_id)):
    """Does the customer's domain point at us yet?"""
    import socket
    with db() as conn:
        d = load_account(conn, aid)["funnel_domain"]
    if not d:
        return {"ok": False, "detail": "No domain saved."}
    try:
        theirs = {i[4][0] for i in socket.getaddrinfo(d, 443)}
        ours = {i[4][0] for i in socket.getaddrinfo(settings.cname_target or _base_host(), 443)}
    except socket.gaierror:
        return {"ok": False, "detail": f"{d} doesn't exist in DNS yet. Add the CNAME record, then check again."}
    if theirs & ours:
        return {"ok": True, "detail": f"{d} points here. Your funnels are live on it."}
    return {"ok": False, "detail": f"{d} points somewhere else. DNS changes can take up to an hour."}


@app.get("/internal/tls-check", include_in_schema=False)
def tls_check(domain: str = ""):
    """For Caddy on-demand TLS: only issue certificates for domains a customer connected."""
    with db() as conn:
        ok = conn.execute("SELECT 1 FROM accounts WHERE funnel_domain = ?", (domain.lower(),)).fetchone()
    return Response(status_code=200 if ok else 404)


def generate_voice(aid: int) -> dict:
    with db() as conn:
        a = load_account(conn, aid)
        ensure_funnels(conn, aid)
        current = {r["key"]: json.loads(r["sequence"]) for r in
                   conn.execute("SELECT key, sequence FROM funnels WHERE account_id = ?", (aid,))}
    seqs, guide, used_ai = voice.rewrite(a["profile"], current)  # slow AI call outside any transaction
    with db() as conn:
        for key, seq in seqs.items():
            conn.execute("UPDATE funnels SET sequence = ? WHERE account_id = ? AND key = ?", (json.dumps(seq), aid, key))
        if guide:
            prof = load_account(conn, aid)["profile"]
            prof["voice_guide"] = guide
            save_profile_dict(conn, aid, prof)
    return {"used_ai": used_ai, "voice_guide": guide, "steps": sum(len(v) for v in seqs.values())}


@app.post("/api/voice/generate")
def voice_generate(aid: int = Depends(account_id)):
    return generate_voice(aid)


# ---------------------------------------------------------------- competitor edge (review intelligence)
class EdgeRunIn(BaseModel):
    pasted: str = ""


class EdgeSaveIn(BaseModel):
    distinguishers: list[dict]


EDGE_COOLDOWN = 3600  # paid lookups: once an hour per account (admins can rerun anytime)


def run_edge(aid: int, pasted: str = "", force: bool = False) -> dict:
    with db() as conn:
        prof = load_account(conn, aid)["profile"]
    last = float(prof.get("edge_run") or 0)
    if not pasted.strip() and not force and now() - last < EDGE_COOLDOWN:
        raise HTTPException(429, "You just ran this. Try again in an hour, or paste reviews by hand.")
    try:
        report = reviews.build_report(prof, pasted)  # slow network + AI work outside any transaction
    except reviews.ReviewError as e:
        raise HTTPException(400, str(e))
    with db() as conn:
        prof = load_account(conn, aid)["profile"]
        was_on = {d.get("label"): d for d in prof.get("distinguishers") or [] if d.get("on")}
        fresh = reviews.to_distinguishers(report["themes"])
        for d in fresh:  # keep what the owner already confirmed/edited
            if d["label"] in was_on:
                d.update({k: was_on[d["label"]][k] for k in ("promise", "proof", "hook", "on")})
        prof.update({"edge_report": report, "distinguishers": fresh, "edge_run": now()})
        save_profile_dict(conn, aid, prof)
    return {"report": report, "distinguishers": fresh}


@app.get("/api/edge")
def edge_get(aid: int = Depends(account_id)):
    with db() as conn:
        prof = load_account(conn, aid)["profile"]
    return {"available": reviews.available(), "report": prof.get("edge_report"),
            "distinguishers": prof.get("distinguishers") or [], "last_run": prof.get("edge_run")}


@app.post("/api/edge/run")
def edge_run(body: EdgeRunIn, s: dict = Depends(session), aid: int = Depends(account_id)):
    return run_edge(aid, body.pasted, force=bool(s.get("adm")))


@app.put("/api/edge")
def edge_save(body: EdgeSaveIn, aid: int = Depends(account_id)):
    clean = []
    for d in body.distinguishers[:8]:
        promise = str(d.get("promise", "")).strip()[:200]
        if not promise:
            continue
        clean.append({"key": str(d.get("key", ""))[:20], "label": str(d.get("label", ""))[:80],
                      "share": int(d.get("share") or 0), "promise": promise, "proof": str(d.get("proof", ""))[:200],
                      "hook": str(d.get("hook", ""))[:140], "on": bool(d.get("on"))})
    with db() as conn:
        prof = load_account(conn, aid)["profile"]
        prof["distinguishers"] = clean
        save_profile_dict(conn, aid, prof)
    return {"ok": True, "confirmed": sum(d["on"] for d in clean)}


# ---------------------------------------------------------------- tracking (Pixel + Conversions API)
@app.get("/api/tracking")
def tracking_status(aid: int = Depends(account_id)):
    with db() as conn:
        prof = load_account(conn, aid)["profile"]
        logs = [dict(r) for r in conn.execute(
            "SELECT event, status, detail, created FROM capi_log WHERE account_id = ? ORDER BY id DESC LIMIT 25", (aid,))]
    return {"pixel_id": prof.get("pixel_id", ""), "connected": meta.connected(prof),
            "has_token": bool(prof.get("capi_token")), "test_code": prof.get("capi_test_code", ""),
            "fb_verify": prof.get("fb_verify", ""), "lead_value": prof.get("lead_value", ""),
            "api_version": settings.meta_api_version, "log": logs,
            "events": [{"when": "Someone opens a funnel page", "sends": "PageView + ViewContent"},
                       {"when": "Someone fills in the form", "sends": "Lead (with your lead value)"},
                       {"when": "You move a lead to Contacted", "sends": "Contact"},
                       {"when": "You move a lead to Quoted", "sends": "QualifiedLead"},
                       {"when": "You move a lead to Won", "sends": "Purchase (with the job value)"}]}


@app.post("/api/tracking/test")
def tracking_test(request: Request, aid: int = Depends(account_id)):
    with db() as conn:
        prof = load_account(conn, aid)["profile"]
    if not meta.connected(prof):
        raise HTTPException(400, "Add your Pixel ID and Conversions API access token first.")
    ev = meta.event("PageView", meta.new_event_id(),
                    meta.browser_data(_client_ip(request), request.headers.get("user-agent", "")),
                    source_url=f"{settings.base_url}/test")
    ok, detail = meta.send_and_log(aid, prof, [ev])
    if not ok:
        raise HTTPException(400, f"Meta said: {detail}")
    tip = (" It shows under Test Events in Events Manager." if prof.get("capi_test_code")
           else " Add a test event code to see it under Test Events.")
    return {"ok": True, "detail": f"Connected. Meta {detail} event.{tip}"}


# ---------------------------------------------------------------- dashboard numbers
@app.get("/api/summary")
def summary(aid: int = Depends(account_id)):
    week_ago = now() - 7 * 86400
    today = dt.date.today().isoformat()
    with db() as conn:
        q = lambda sql, *a: conn.execute(sql, (aid, *a)).fetchone()[0]  # noqa: E731
        return {
            "new_leads": q("SELECT COUNT(*) FROM contacts WHERE account_id = ? AND stage = 'new'"),
            "leads_7d": q("SELECT COUNT(*) FROM contacts WHERE account_id = ? AND created >= ?", week_ago),
            "won_value": q("SELECT COALESCE(SUM(value),0) FROM contacts WHERE account_id = ? AND stage = 'won'"),
            "following_up": q("SELECT COUNT(*) FROM contacts WHERE account_id = ? AND seq_status IN ('active','sending')"),
            "messages_7d": q("SELECT COUNT(*) FROM messages WHERE account_id = ? AND direction = 'out' AND created >= ?",
                             week_ago),
            "today_post": row(conn.execute("SELECT * FROM posts WHERE account_id = ? AND day = ? ORDER BY time LIMIT 1",
                                           (aid, today)).fetchone()),
            "posts_planned": q("SELECT COUNT(*) FROM posts WHERE account_id = ? AND day >= ?", today),
        }


def funnel_stats(conn, aid: int) -> dict[str, dict]:
    d7 = (dt.date.today() - dt.timedelta(days=6)).isoformat()
    d30 = (dt.date.today() - dt.timedelta(days=29)).isoformat()
    out = {k: {"views_7": 0, "leads_7": 0, "views_30": 0, "leads_30": 0} for k in funnels.FUNNELS}
    for r in conn.execute("SELECT funnel, kind, day, COUNT(*) n FROM events WHERE account_id = ? AND day >= ? "
                          "GROUP BY funnel, kind, day", (aid, d30)):
        if r["funnel"] not in out:
            continue
        k = "views" if r["kind"] == "view" else "leads"
        out[r["funnel"]][f"{k}_30"] += r["n"]
        if r["day"] >= d7:
            out[r["funnel"]][f"{k}_7"] += r["n"]
    for v in out.values():
        v["rate_30"] = round(100 * v["leads_30"] / v["views_30"], 1) if v["views_30"] else 0
    return out


# ---------------------------------------------------------------- funnels
class FunnelIn(BaseModel):
    enabled: Optional[bool] = None
    overrides: Optional[dict] = None
    sequence: Optional[list[dict]] = None


@app.get("/api/funnels")
def list_funnels(aid: int = Depends(account_id)):
    out = []
    with db() as conn:
        a = load_account(conn, aid)
        ensure_funnels(conn, aid)
        stats = funnel_stats(conn, aid)
        for key, tpl in funnels.FUNNELS.items():
            f = row(conn.execute("SELECT * FROM funnels WHERE account_id = ? AND key = ?", (aid, key)).fetchone(),
                    ("overrides", "sequence"))
            leads = conn.execute("SELECT COUNT(*) FROM contacts WHERE account_id = ? AND source = ?",
                                 (aid, key)).fetchone()[0]
            merged = funnels.merged(key, f["overrides"])
            link = funnel_link(a, key)
            out.append({
                "key": key, "name": tpl["name"], "purpose": tpl["purpose"], "enabled": bool(f["enabled"]),
                "link": link, "preview": f"/f/{a['slug']}/{key}?preview=1", "views": f["views"], "leads": leads,
                "rate": round(100 * leads / f["views"], 1) if f["views"] else 0, "stats": stats[key],
                "copy": {k: merged[k] for k in ("headline", "sub", "button", "thanks", "bullets")},
                "sequence": f["sequence"] or funnels.default_sequence(key),
                "ad": funnels.render_ad(key, a["profile"], link),
                "gap_ads": funnels.gap_ads(a["profile"], link),
            })
    return out


def update_funnel_for(aid: int, key: str, body: FunnelIn) -> None:
    if key not in funnels.FUNNELS:
        raise HTTPException(404, "No such funnel")
    with db() as conn:
        ensure_funnels(conn, aid)
        if body.enabled is not None:
            conn.execute("UPDATE funnels SET enabled = ? WHERE account_id = ? AND key = ?", (int(body.enabled), aid, key))
        if body.overrides is not None:
            clean = {k: v for k, v in body.overrides.items() if k in ("headline", "sub", "button", "thanks", "bullets")}
            conn.execute("UPDATE funnels SET overrides = ? WHERE account_id = ? AND key = ?", (json.dumps(clean), aid, key))
        if body.sequence is not None:
            seq = []
            for s in body.sequence:
                if s.get("channel") not in ("sms", "email") or not str(s.get("body", "")).strip():
                    raise HTTPException(400, "Each step needs a channel (sms or email) and a message.")
                seq.append({"delay": max(0, int(s.get("delay", 0))), "channel": s["channel"],
                            "subject": str(s.get("subject", ""))[:200], "body": str(s["body"])[:1600],
                            "on": bool(s.get("on", True))})
            seq.sort(key=lambda s: s["delay"])
            conn.execute("UPDATE funnels SET sequence = ? WHERE account_id = ? AND key = ?", (json.dumps(seq), aid, key))


@app.put("/api/funnels/{key}")
def update_funnel(key: str, body: FunnelIn, aid: int = Depends(account_id)):
    update_funnel_for(aid, key, body)
    return {"ok": True}


@app.post("/api/funnels/{key}/reset")
def reset_funnel(key: str, aid: int = Depends(account_id)):
    if key not in funnels.FUNNELS:
        raise HTTPException(404, "No such funnel")
    with db() as conn:
        conn.execute("UPDATE funnels SET overrides = '{}', sequence = ? WHERE account_id = ? AND key = ?",
                     (json.dumps(funnels.default_sequence(key)), aid, key))
    return {"ok": True}


# ---------------------------------------------------------------- public funnel pages
def _public_funnel(conn, slug: str, key: str):
    a = row(conn.execute("SELECT * FROM accounts WHERE slug = ?", (slug,)).fetchone(), ("profile",))
    if not a or key not in funnels.FUNNELS or a["plan_status"] == "canceled":
        raise HTTPException(404, "Page not found")
    f = row(conn.execute("SELECT * FROM funnels WHERE account_id = ? AND key = ?", (a["id"], key)).fetchone(),
            ("overrides",))
    if not f or not f["enabled"]:
        raise HTTPException(404, "Page not found")
    return a, f


def _lead_value(prof: dict) -> float:
    try:
        return float(prof.get("lead_value") or 0)
    except ValueError:
        return 0.0


@app.get("/f/{slug}/{key}", response_class=HTMLResponse, include_in_schema=False)
def funnel_page(slug: str, key: str, request: Request, preview: int = 0, fbclid: str = ""):
    with db() as conn:
        a, f = _public_funnel(conn, slug, key)
        if not preview:
            conn.execute("UPDATE funnels SET views = views + 1 WHERE id = ?", (f["id"],))
            _record(conn, a["id"], key, "view")
    prof = a["profile"]
    name = funnels.FUNNELS[key]["name"]
    pv, vc = meta.new_event_id(), meta.new_event_id()
    custom = {"content_name": name, "content_category": complete(prof)["trade"]}
    head = "" if preview else meta.pixel_script(prof.get("pixel_id", ""), [("PageView", {}, pv), ("ViewContent", custom, vc)])
    if not preview and meta.connected(prof):
        url = str(request.url)
        bd = meta.browser_data(_client_ip(request), request.headers.get("user-agent", ""),
                               meta.make_fbc(fbclid, request.cookies.get("_fbc", "")), request.cookies.get("_fbp", ""))
        meta.send_later(a["id"], prof, [meta.event("PageView", pv, bd, source_url=url),
                                        meta.event("ViewContent", vc, bd, custom, source_url=url)])
    action = getattr(request.state, "custom_action", f"/f/{slug}/{key}")
    return funnels.render_page(key, f["overrides"], prof, action, head_extra=head, fbclid=fbclid)


@app.post("/f/{slug}/{key}", response_class=HTMLResponse, include_in_schema=False)
async def funnel_submit(slug: str, key: str, request: Request):
    form = await request.form()
    get = lambda k, n=300: str(form.get(k, "")).strip()[:n]  # noqa: E731
    with db() as conn:
        a, f = _public_funnel(conn, slug, key)
    prof = a["profile"]
    if get("website"):  # honeypot: bots fill hidden fields
        return funnels.render_thanks(key, f["overrides"], prof, "")
    name, phone, email, zip_code = get("name", 120), get("phone", 40), get("email", 200), get("zip", 10)
    if not name or not (phone or email):
        raise HTTPException(400, "Please enter your name and phone number.")
    details = "\n".join(x for x in [
        get("service") and f"Needs: {get('service')}",
        get("details", 2000),
        zip_code and f"ZIP: {zip_code}",
        get("friend_name") and f"Referred: {get('friend_name')} {get('friend_phone')}".strip(),
    ] if x)
    biz = complete(prof)["business"]
    alert = f"New lead from your {funnels.FUNNELS[key]['name']} page:\n\n{name}\n{phone}\n{email}\n\n{details}"
    tracking = {"fbc": meta.make_fbc(get("fbclid", 500), request.cookies.get("_fbc", "")),
                "fbp": request.cookies.get("_fbp", "")[:200], "ip": _client_ip(request),
                "ua": request.headers.get("user-agent", "")[:500], "url": get("page_url", 1000) or str(request.url),
                "event_id": meta.new_event_id()}
    stored = True
    with db() as conn:
        a = load_account(conn, a["id"])
        if contact_count(conn, a["id"]) >= limit_for(a):
            stored = False
            cid = None
        else:
            t = now()
            cid = conn.execute(
                "INSERT INTO contacts (account_id, name, phone, email, source, details, tracking, created, updated) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (a["id"], name, phone, email, key, details, json.dumps(tracking), t, t)).lastrowid
            automation.enroll(conn, a["id"], cid, key)
            if get("friend_name") and get("friend_phone") and contact_count(conn, a["id"]) < limit_for(a):
                conn.execute("INSERT INTO contacts (account_id, name, phone, source, details, created, updated) "
                             "VALUES (?,?,?,?,?,?,?)", (a["id"], get("friend_name", 120), get("friend_phone", 40),
                                                        "referral-friend", f"Referred by {name}", t, t))
        _record(conn, a["id"], key, "lead")
    if stored:
        automation.notify_owner(a, f"New lead: {name}", alert)
        _kick_followups()
    else:
        # Over the plan limit: never lose the lead. Forward it to the owner instead of storing it.
        automation.notify_owner(a, f"New lead (contact limit reached): {name}",
                                alert + f"\n\nYour {biz} account is full ({limit_for(a)} contacts). "
                                "Delete old contacts or upgrade to keep leads in your pipeline.")
    # Lead event: browser pixel on the thank-you page + server CAPI, same event_id, hashed matching.
    match = meta.matching(email, phone, name, zip_code, external_id=f"{a['id']}:{cid}" if cid else "")
    custom = {"content_name": funnels.FUNNELS[key]["name"], "content_category": complete(prof)["trade"],
              "currency": "USD", "value": _lead_value(prof)}
    head = meta.pixel_script(prof.get("pixel_id", ""), [("Lead", custom, tracking["event_id"])], advanced=match)
    meta.send_later(a["id"], prof, [meta.event(
        "Lead", tracking["event_id"],
        {**match, **meta.browser_data(tracking["ip"], tracking["ua"], tracking["fbc"], tracking["fbp"])},
        custom, source_url=tracking["url"])])
    return funnels.render_thanks(key, f["overrides"], prof, name.split(" ")[0], head_extra=head)


# ---------------------------------------------------------------- contacts (CRM)
class ContactIn(BaseModel):
    name: str
    phone: str = ""
    email: str = ""
    details: str = ""
    stage: str = "new"
    value: float = 0
    funnel: str = ""  # optional: start that funnel's follow-up


class ContactPatch(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    stage: Optional[str] = None
    notes: Optional[str] = None
    details: Optional[str] = None
    value: Optional[float] = None
    followup: Optional[str] = None  # "stop"


class MessageIn(BaseModel):
    channel: str
    body: str
    subject: str = ""


@app.get("/api/contacts")
def list_contacts(q: str = "", stage: str = "", aid: int = Depends(account_id)):
    sql = "SELECT id, account_id, name, phone, email, source, stage, details, notes, value, seq_step, seq_status, " \
          "created, updated FROM contacts WHERE account_id = ?"
    args: list = [aid]
    if stage:
        sql += " AND stage = ?"
        args.append(stage)
    if q:
        sql += " AND (name LIKE ? OR phone LIKE ? OR email LIKE ?)"
        args += [f"%{q}%"] * 3
    with db() as conn:
        rows = conn.execute(sql + " ORDER BY updated DESC LIMIT 1000", args).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/contacts")
def add_contact(body: ContactIn, aid: int = Depends(account_id)):
    if not body.name.strip():
        raise HTTPException(400, "Name is required.")
    if body.stage not in STAGES:
        raise HTTPException(400, "Unknown stage.")
    with db() as conn:
        a = load_account(conn, aid)
        if contact_count(conn, aid) >= limit_for(a):
            raise HTTPException(400, f"You've reached your {limit_for(a)}-contact limit.")
        t = now()
        cid = conn.execute(
            "INSERT INTO contacts (account_id, name, phone, email, source, stage, details, value, created, updated) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (aid, body.name.strip(), body.phone.strip(), body.email.strip(), body.funnel or "manual",
             body.stage, body.details, body.value, t, t)).lastrowid
        if body.funnel in funnels.FUNNELS and body.stage == "new":
            automation.enroll(conn, aid, cid, body.funnel)
    _kick_followups()
    return {"id": cid}


@app.get("/api/contacts/{cid}")
def get_contact(cid: int, aid: int = Depends(account_id)):
    with db() as conn:
        c = row(conn.execute("SELECT * FROM contacts WHERE id = ? AND account_id = ?", (cid, aid)).fetchone())
        if not c:
            raise HTTPException(404, "Contact not found")
        c.pop("tracking", None)
        c["messages"] = [dict(m) for m in conn.execute(
            "SELECT * FROM messages WHERE contact_id = ? ORDER BY created", (cid,)).fetchall()]
    return c


@app.patch("/api/contacts/{cid}")
def update_contact(cid: int, body: ContactPatch, aid: int = Depends(account_id)):
    with db() as conn:
        c = row(conn.execute("SELECT * FROM contacts WHERE id = ? AND account_id = ?", (cid, aid)).fetchone())
        if not c:
            raise HTTPException(404, "Contact not found")
        changes = body.model_dump(exclude_none=True)
        if "stage" in changes and changes["stage"] not in STAGES:
            raise HTTPException(400, "Unknown stage.")
        if changes.pop("followup", None) == "stop":
            automation.stop(conn, cid, "stopped by you")
        for k, v in changes.items():
            conn.execute(f"UPDATE contacts SET {k} = ? WHERE id = ?", (v, cid))  # k is from the model's fixed fields
        if changes.get("stage", "new") != "new":
            automation.stop(conn, cid, "lead moved")
        conn.execute("UPDATE contacts SET updated = ? WHERE id = ?", (now(), cid))
        prof = load_account(conn, aid)["profile"]
    new_stage = changes.get("stage")
    if new_stage and new_stage != c["stage"]:
        # Teach Meta which leads turn into real jobs (Contact / QualifiedLead / Purchase).
        meta.stage_event(aid, prof, {**c, **changes}, new_stage, changes.get("value", c["value"]))
    return {"ok": True}


@app.delete("/api/contacts/{cid}")
def delete_contact(cid: int, aid: int = Depends(account_id)):
    with db() as conn:
        conn.execute("DELETE FROM contacts WHERE id = ? AND account_id = ?", (cid, aid))
    return {"ok": True}


@app.post("/api/contacts/{cid}/message")
def message_contact(cid: int, body: MessageIn, aid: int = Depends(account_id)):
    if body.channel not in ("sms", "email") or not body.body.strip():
        raise HTTPException(400, "Pick text or email and write a message.")
    with db() as conn:
        a = load_account(conn, aid)
        c = conn.execute("SELECT * FROM contacts WHERE id = ? AND account_id = ?", (cid, aid)).fetchone()
        if not c:
            raise HTTPException(404, "Contact not found")
    if body.channel == "sms":
        status = automation.send_sms(c["phone"], body.body)
        logged = body.body
    else:
        subject = body.subject or f"From {complete(a['profile'])['business']}"
        status = automation.send_email(c["email"], subject, body.body, reply_to=a["email"])
        logged = f"Subject: {subject}\n\n{body.body}"
    with db() as conn:
        automation.log_message(conn, aid, cid, "out", body.channel, logged, status)
        conn.execute("UPDATE contacts SET updated = ? WHERE id = ?", (now(), cid))
    return {"status": status}


@app.get("/api/contacts.csv")
def export_contacts(aid: int = Depends(account_id)):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "phone", "email", "stage", "source", "value", "details", "notes", "created"])
    with db() as conn:
        for c in conn.execute("SELECT * FROM contacts WHERE account_id = ? ORDER BY created", (aid,)):
            w.writerow([c["name"], c["phone"], c["email"], c["stage"], c["source"], c["value"], c["details"],
                        c["notes"], dt.datetime.fromtimestamp(c["created"]).isoformat(timespec="minutes")])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=contacts.csv"})


@app.post("/api/contacts/import")
async def import_contacts(request: Request, aid: int = Depends(account_id)):
    text = (await request.body()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    added = skipped = 0
    with db() as conn:
        room = limit_for(load_account(conn, aid)) - contact_count(conn, aid)
        t = now()
        for r in reader:
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            name = r.get("name") or " ".join(x for x in (r.get("first name"), r.get("last name")) if x)
            if not name or added >= room:
                skipped += 1
                continue
            conn.execute("INSERT INTO contacts (account_id, name, phone, email, source, stage, created, updated) "
                         "VALUES (?,?,?,?,?,?,?,?)", (aid, name[:120], r.get("phone", "")[:40], r.get("email", "")[:200],
                                                      "import", "won" if r.get("customer") else "new", t, t))
            added += 1
    return {"added": added, "skipped": skipped}


@app.get("/api/messages")
def recent_messages(aid: int = Depends(account_id)):
    with db() as conn:
        rows = conn.execute(
            "SELECT m.*, c.name FROM messages m LEFT JOIN contacts c ON c.id = m.contact_id "
            "WHERE m.account_id = ? ORDER BY m.created DESC LIMIT 100", (aid,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- content
class GenerateIn(BaseModel):
    start: str = ""
    days: int = 30
    ai: bool = False
    replace: bool = True


class PostPatch(BaseModel):
    day: Optional[str] = None
    time: Optional[str] = None
    title: Optional[str] = None
    hook: Optional[str] = None
    script: Optional[str] = None
    caption: Optional[str] = None
    status: Optional[str] = None


@app.post("/api/content/generate")
def generate(body: GenerateIn, aid: int = Depends(account_id)):
    days = max(7, min(body.days, 35))
    try:
        start = dt.date.fromisoformat(body.start) if body.start else content.next_monday()
    except ValueError:
        raise HTTPException(400, "Start date must look like 2026-12-01.")
    with db() as conn:
        a = load_account(conn, aid)
    links = {k: funnel_link(a, k) for k in funnels.FUNNELS}
    maker = content.ai_calendar if body.ai else content.template_calendar
    posts = maker(a["profile"], start, days, links)  # slow AI call happens outside the DB transaction
    end = (start + dt.timedelta(days=days - 1)).isoformat()
    with db() as conn:
        if body.replace:
            conn.execute("DELETE FROM posts WHERE account_id = ? AND day BETWEEN ? AND ? AND status IN ('planned','scheduled')",
                         (aid, start.isoformat(), end))
        t = now()
        for p in posts:
            conn.execute(
                "INSERT INTO posts (account_id, day, time, pillar, format, title, hook, script, cta, caption, funnel, created) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (aid, p["day"], p["time"], p["pillar"], p["format"], p["title"], p["hook"], p["script"], p["cta"],
                 p["caption"], p["funnel"], t))
    return {"created": len(posts), "start": start.isoformat(), "end": end}


@app.get("/api/posts")
def list_posts(start: str = "", end: str = "", aid: int = Depends(account_id)):
    start = start or (dt.date.today() - dt.timedelta(days=7)).isoformat()
    end = end or (dt.date.today() + dt.timedelta(days=60)).isoformat()
    with db() as conn:
        rows = conn.execute("SELECT * FROM posts WHERE account_id = ? AND day BETWEEN ? AND ? ORDER BY day, time",
                            (aid, start, end)).fetchall()
    return {"posts": [dict(r) for r in rows], "rhythm": content.RHYTHM, "arc": content.ARC}


@app.patch("/api/posts/{pid}")
def update_post(pid: int, body: PostPatch, aid: int = Depends(account_id)):
    changes = body.model_dump(exclude_none=True)
    if "status" in changes and changes["status"] not in ("planned", "scheduled", "posted"):
        raise HTTPException(400, "Unknown status.")
    if "day" in changes:
        try:
            dt.date.fromisoformat(changes["day"])
        except ValueError:
            raise HTTPException(400, "Bad date.")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM posts WHERE id = ? AND account_id = ?", (pid, aid)).fetchone():
            raise HTTPException(404, "Post not found")
        for k, v in changes.items():
            conn.execute(f"UPDATE posts SET {k} = ? WHERE id = ?", (v, pid))  # k from the model's fixed fields
    return {"ok": True}


@app.delete("/api/posts/{pid}")
def delete_post(pid: int, aid: int = Depends(account_id)):
    with db() as conn:
        conn.execute("DELETE FROM posts WHERE id = ? AND account_id = ?", (pid, aid))
    return {"ok": True}


# ---------------------------------------------------------------- admin (program owner)
class EnrollIn(BaseModel):
    email: str
    business: str
    contact_limit: Optional[int] = None
    website: str = ""


class AdminAccountPatch(BaseModel):
    plan_status: Optional[str] = None
    setup_paid: Optional[bool] = None
    contact_limit: Optional[int] = None
    profile: Optional[dict] = None
    domain: Optional[str] = None
    onboarded: Optional[bool] = None


def _onboarding_steps(a: dict, has_posts: bool) -> dict:
    p = a["profile"]
    steps = {"Brand imported": bool(p.get("website") or p.get("logo")),
             "Business info": not missing(p),
             "Brand voice": bool(p.get("voice_guide") or p.get("tone")),
             "Competitor edge": bool(reviews.confirmed(p)),
             "Pixel + CAPI": meta.connected(p),
             "Content plan": has_posts}
    return {"steps": steps, "pct": round(100 * sum(steps.values()) / len(steps))}


@app.get("/api/admin/customers")
def admin_customers(adm: int = Depends(admin_id)):
    out = []
    month = now() - 30 * 86400
    with db() as conn:
        for r in conn.execute("SELECT * FROM accounts WHERE is_admin = 0 ORDER BY created DESC"):
            a = row(r, ("profile",))
            q = lambda sql, *x: conn.execute(sql, (a["id"], *x)).fetchone()[0]  # noqa: E731
            has_posts = bool(q("SELECT COUNT(*) FROM posts WHERE account_id = ?"))
            out.append({
                "id": a["id"], "email": a["email"], "business": a["profile"].get("business", ""),
                "trade": a["profile"].get("trade", ""), "city": a["profile"].get("city", ""),
                "logo": a["profile"].get("logo", ""), "created": a["created"], "last_seen": a["last_seen"],
                "onboarded": bool(a["onboarded"]), "domain": a["funnel_domain"] or "",
                "plan_status": a["plan_status"], "setup_paid": bool(a["setup_paid"]),
                "contacts": q("SELECT COUNT(*) FROM contacts WHERE account_id = ?"), "contact_limit": limit_for(a),
                "leads_30": q("SELECT COUNT(*) FROM events WHERE account_id = ? AND kind = 'lead' AND created >= ?", month),
                "views_30": q("SELECT COUNT(*) FROM events WHERE account_id = ? AND kind = 'view' AND created >= ?", month),
                "funnels_live": q("SELECT COUNT(*) FROM funnels WHERE account_id = ? AND enabled = 1"),
                "capi_errors": q("SELECT COUNT(*) FROM capi_log WHERE account_id = ? AND status = 'error' AND created >= ?",
                                 now() - 86400),
                **_onboarding_steps(a, has_posts),
            })
    return out


@app.post("/api/admin/customers")
def admin_enroll(body: EnrollIn, adm: int = Depends(admin_id)):
    """Enroll a customer: creates their account and returns a one-time setup link to send them."""
    try:
        aid = auth.create_account(body.email, body.business)
    except auth.AuthError as e:
        raise HTTPException(400, str(e))
    with db() as conn:
        ensure_funnels(conn, aid)
        if body.contact_limit:
            conn.execute("UPDATE accounts SET contact_limit = ? WHERE id = ?", (body.contact_limit, aid))
    imported = None
    if body.website.strip():
        try:
            imported = import_brand(aid, body.website)
        except HTTPException as e:
            imported = {"error": e.detail}
    return {"id": aid, "setup_link": f"{settings.base_url}/welcome?t={auth.setup_token(aid)}", "brand": imported}


@app.get("/api/admin/customers/{aid}")
def admin_customer(aid: int, adm: int = Depends(admin_id)):
    with db() as conn:
        payload = me_payload(conn, aid)
        payload["stats"] = funnel_stats(conn, aid)
        payload["capi_log"] = [dict(r) for r in conn.execute(
            "SELECT event, status, detail, created FROM capi_log WHERE account_id = ? ORDER BY id DESC LIMIT 10", (aid,))]
        payload["id"] = aid
        a = load_account(conn, aid)
        payload["setup_paid"] = bool(a["setup_paid"])
        payload["edge_report"] = a["profile"].get("edge_report")
        payload["distinguishers"] = a["profile"].get("distinguishers") or []
    return payload


@app.patch("/api/admin/customers/{aid}")
def admin_update(aid: int, body: AdminAccountPatch, adm: int = Depends(admin_id)):
    with db() as conn:
        a = load_account(conn, aid)
        if body.plan_status is not None:
            if body.plan_status not in PLAN_STATUSES:
                raise HTTPException(400, "Status must be active, past_due or canceled.")
            conn.execute("UPDATE accounts SET plan_status = ? WHERE id = ?", (body.plan_status, aid))
        if body.setup_paid is not None:
            conn.execute("UPDATE accounts SET setup_paid = ? WHERE id = ?", (int(body.setup_paid), aid))
        if body.contact_limit is not None:
            conn.execute("UPDATE accounts SET contact_limit = ? WHERE id = ?", (max(1, body.contact_limit), aid))
        if body.onboarded is not None:
            conn.execute("UPDATE accounts SET onboarded = ? WHERE id = ?", (int(body.onboarded), aid))
        if body.profile:
            save_profile_dict(conn, aid, apply_profile(a["profile"], body.profile))
    if body.domain is not None:
        set_domain(DomainIn(domain=body.domain), aid)
    return {"ok": True}


@app.post("/api/admin/customers/{aid}/import")
def admin_import(aid: int, body: ImportIn, adm: int = Depends(admin_id)):
    return import_brand(aid, body.url, body.replace_logo)


@app.post("/api/admin/customers/{aid}/voice")
def admin_voice(aid: int, adm: int = Depends(admin_id)):
    return generate_voice(aid)


@app.post("/api/admin/customers/{aid}/edge")
def admin_edge(aid: int, body: EdgeRunIn, adm: int = Depends(admin_id)):
    return run_edge(aid, body.pasted, force=True)


@app.post("/api/admin/customers/{aid}/setup-link")
def admin_setup_link(aid: int, adm: int = Depends(admin_id)):
    with db() as conn:
        load_account(conn, aid)
    return {"setup_link": f"{settings.base_url}/welcome?t={auth.setup_token(aid)}"}


@app.post("/api/admin/customers/{aid}/act-as")
def admin_act_as(aid: int, adm: int = Depends(admin_id)):
    """Open the customer's own app to customize for them. A banner shows you're acting as them."""
    with db() as conn:
        load_account(conn, aid)
    return _session(aid, admin=adm)


@app.post("/api/admin/return")
def admin_return(s: dict = Depends(session)):
    if not s.get("adm"):
        raise HTTPException(400, "Not acting as a customer.")
    adm = admin_id(s)
    return _session(adm)


# ---------------------------------------------------------------- inbound webhooks
def _twilio_signed(request: Request, params: dict) -> bool:
    url = settings.base_url + "/hooks/sms"
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    good = base64.b64encode(hmac.new(settings.twilio_token.encode(), data.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(good, request.headers.get("X-Twilio-Signature", ""))


@app.post("/hooks/sms", include_in_schema=False)
async def inbound_sms(request: Request):
    """Point your Twilio number's 'A message comes in' webhook here."""
    form = await request.form()
    if settings.twilio_token and not _twilio_signed(request, dict(form)):
        raise HTTPException(403, "Bad signature")
    automation.handle_inbound_sms(str(form.get("From", "")), str(form.get("Body", "")))
    return Response("<Response></Response>", media_type="application/xml")
