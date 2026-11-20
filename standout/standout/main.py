"""Web app + API. Three panels for the owner (Content, Funnels, Leads) and public funnel pages."""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, automation, content, funnels
from .config import settings
from .db import db, init_db, now, row
from .profile import FIELDS, complete, missing

WEB = Path(__file__).resolve().parent / "web"
STAGES = ["new", "contacted", "quoted", "won", "lost"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    automation.worker.start()
    yield
    automation.worker.shutdown()


app = FastAPI(title="StandOut", lifespan=lifespan)


# ---------------------------------------------------------------- helpers
def account_id(request: Request) -> int:
    aid = auth.account_from_token(request.cookies.get(auth.COOKIE))
    if not aid:
        raise HTTPException(401, "Not logged in")
    return aid


def load_account(conn, aid: int) -> dict:
    a = row(conn.execute("SELECT id, email, slug, profile, created FROM accounts WHERE id = ?", (aid,)).fetchone(),
            ("profile",))
    if not a:
        raise HTTPException(401, "Account not found")
    return a


def ensure_funnels(conn, aid: int) -> None:
    for key in funnels.FUNNELS:
        conn.execute("INSERT OR IGNORE INTO funnels (account_id, key, sequence) VALUES (?,?,?)",
                     (aid, key, json.dumps(funnels.default_sequence(key))))


def funnel_link(slug: str, key: str) -> str:
    return f"{settings.base_url}/f/{slug}/{key}"


def links_for(slug: str) -> dict[str, str]:
    return {k: funnel_link(slug, k) for k in funnels.FUNNELS}


def contact_count(conn, aid: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM contacts WHERE account_id = ?", (aid,)).fetchone()[0]


def _session(aid: int) -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, auth.make_token(aid), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax", secure=settings.base_url.startswith("https"))
    return resp


def _kick_followups() -> None:
    threading.Thread(target=automation.run_due, daemon=True).start()


# ---------------------------------------------------------------- pages
@app.get("/", include_in_schema=False)
def home(request: Request):
    if not auth.account_from_token(request.cookies.get(auth.COOKIE)):
        return RedirectResponse("/login", status_code=303)
    return HTMLResponse((WEB / "index.html").read_text())


@app.get("/login", include_in_schema=False)
def login_page():
    return HTMLResponse((WEB / "login.html").read_text())


app.mount("/static", StaticFiles(directory=WEB), name="static")


# ---------------------------------------------------------------- auth
class SignupIn(BaseModel):
    email: str
    password: str
    business: str
    code: str = ""


class LoginIn(BaseModel):
    email: str
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
        return _session(auth.login(body.email, body.password))
    except auth.AuthError as e:
        raise HTTPException(400, str(e))


@app.post("/api/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/api/auth/config")
def auth_config():
    return {"needs_code": bool(settings.invite_code)}


# ---------------------------------------------------------------- profile + home
@app.get("/api/me")
def me(aid: int = Depends(account_id)):
    with db() as conn:
        a = load_account(conn, aid)
        used = contact_count(conn, aid)
    prof = a["profile"]
    safe = {k: v for k, v in prof.items() if k != "fb_page_token"}
    safe["fb_connected"] = bool(prof.get("fb_page_id") and prof.get("fb_page_token"))
    return {"email": a["email"], "slug": a["slug"], "profile": safe, "fields": FIELDS,
            "missing": missing(prof), "contacts_used": used, "contact_limit": settings.contact_limit,
            "ai": content.ai_available(),
            "sms_live": bool(settings.twilio_sid and settings.twilio_from),
            "email_live": bool(settings.smtp_host and settings.smtp_from)}


@app.put("/api/profile")
def save_profile(body: dict, aid: int = Depends(account_id)):
    allowed = set(FIELDS) | {"fb_page_id", "fb_page_token"}
    with db() as conn:
        a = load_account(conn, aid)
        prof = a["profile"]
        for k, v in body.items():
            if k in allowed and isinstance(v, str):
                if k == "fb_page_token" and not v.strip():
                    continue  # blank means "keep the saved token"
                prof[k] = v.strip()[:2000]
        conn.execute("UPDATE accounts SET profile = ? WHERE id = ?", (json.dumps(prof), aid))
    return {"ok": True, "missing": missing(prof)}


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
            "following_up": q("SELECT COUNT(*) FROM contacts WHERE account_id = ? AND seq_status = 'active'"),
            "messages_7d": q("SELECT COUNT(*) FROM messages WHERE account_id = ? AND direction = 'out' AND created >= ?",
                             week_ago),
            "today_post": row(conn.execute("SELECT * FROM posts WHERE account_id = ? AND day = ? ORDER BY time LIMIT 1",
                                           (aid, today)).fetchone()),
            "posts_planned": q("SELECT COUNT(*) FROM posts WHERE account_id = ? AND day >= ?", today),
        }


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
        for key, tpl in funnels.FUNNELS.items():
            f = row(conn.execute("SELECT * FROM funnels WHERE account_id = ? AND key = ?", (aid, key)).fetchone(),
                    ("overrides", "sequence"))
            leads = conn.execute("SELECT COUNT(*) FROM contacts WHERE account_id = ? AND source = ?",
                                 (aid, key)).fetchone()[0]
            merged = funnels.merged(key, f["overrides"])
            link = funnel_link(a["slug"], key)
            out.append({
                "key": key, "name": tpl["name"], "purpose": tpl["purpose"], "enabled": bool(f["enabled"]),
                "link": link, "views": f["views"], "leads": leads,
                "rate": round(100 * leads / f["views"], 1) if f["views"] else 0,
                "copy": {k: merged[k] for k in ("headline", "sub", "button", "thanks", "bullets")},
                "sequence": f["sequence"] or funnels.default_sequence(key),
                "ad": funnels.render_ad(key, a["profile"], link),
            })
    return out


@app.put("/api/funnels/{key}")
def update_funnel(key: str, body: FunnelIn, aid: int = Depends(account_id)):
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
    a = row(conn.execute("SELECT id, email, slug, profile FROM accounts WHERE slug = ?", (slug,)).fetchone(), ("profile",))
    if not a or key not in funnels.FUNNELS:
        raise HTTPException(404, "Page not found")
    f = row(conn.execute("SELECT * FROM funnels WHERE account_id = ? AND key = ?", (a["id"], key)).fetchone(),
            ("overrides",))
    if not f or not f["enabled"]:
        raise HTTPException(404, "Page not found")
    return a, f


@app.get("/f/{slug}/{key}", response_class=HTMLResponse, include_in_schema=False)
def funnel_page(slug: str, key: str, preview: int = 0):
    with db() as conn:
        a, f = _public_funnel(conn, slug, key)
        if not preview:
            conn.execute("UPDATE funnels SET views = views + 1 WHERE id = ?", (f["id"],))
    return funnels.render_page(key, f["overrides"], a["profile"], f"/f/{slug}/{key}")


@app.post("/f/{slug}/{key}", response_class=HTMLResponse, include_in_schema=False)
async def funnel_submit(slug: str, key: str, request: Request):
    form = await request.form()
    get = lambda k, n=300: str(form.get(k, "")).strip()[:n]  # noqa: E731
    with db() as conn:
        a, f = _public_funnel(conn, slug, key)
        if get("website"):  # honeypot: bots fill hidden fields
            return funnels.render_thanks(key, f["overrides"], a["profile"], "")
        name, phone, email = get("name", 120), get("phone", 40), get("email", 200)
        if not name or not (phone or email):
            raise HTTPException(400, "Please enter your name and phone number.")
        details = "\n".join(x for x in [
            get("service") and f"Needs: {get('service')}",
            get("details", 2000),
            get("friend_name") and f"Referred: {get('friend_name')} {get('friend_phone')}".strip(),
        ] if x)
        biz = complete(a["profile"])["business"]
        alert = f"New lead from your {funnels.FUNNELS[key]['name']} page:\n\n{name}\n{phone}\n{email}\n\n{details}"
        if contact_count(conn, a["id"]) >= settings.contact_limit:
            # Over the plan limit: never lose the lead. Forward it to the owner instead of storing it.
            automation.notify_owner(a, f"New lead (contact limit reached): {name}",
                                    alert + f"\n\nYour {biz} account is full ({settings.contact_limit} contacts). "
                                    "Delete old contacts or upgrade to keep leads in your pipeline.")
            return funnels.render_thanks(key, f["overrides"], a["profile"], name.split(" ")[0])
        t = now()
        cid = conn.execute(
            "INSERT INTO contacts (account_id, name, phone, email, source, details, created, updated) "
            "VALUES (?,?,?,?,?,?,?,?)", (a["id"], name, phone, email, key, details, t, t)).lastrowid
        automation.enroll(conn, a["id"], cid, key)
        if get("friend_name") and get("friend_phone") and contact_count(conn, a["id"]) < settings.contact_limit:
            conn.execute("INSERT INTO contacts (account_id, name, phone, source, details, created, updated) "
                         "VALUES (?,?,?,?,?,?,?)", (a["id"], get("friend_name", 120), get("friend_phone", 40),
                                                    "referral-friend", f"Referred by {name}", t, t))
    automation.notify_owner(a, f"New lead: {name}", alert)
    _kick_followups()
    return funnels.render_thanks(key, f["overrides"], a["profile"], name.split(" ")[0])


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
    sql = "SELECT * FROM contacts WHERE account_id = ?"
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
        if contact_count(conn, aid) >= settings.contact_limit:
            raise HTTPException(400, f"You've reached your {settings.contact_limit}-contact limit.")
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
        c["messages"] = [dict(m) for m in conn.execute(
            "SELECT * FROM messages WHERE contact_id = ? ORDER BY created", (cid,)).fetchall()]
    return c


@app.patch("/api/contacts/{cid}")
def update_contact(cid: int, body: ContactPatch, aid: int = Depends(account_id)):
    with db() as conn:
        c = conn.execute("SELECT * FROM contacts WHERE id = ? AND account_id = ?", (cid, aid)).fetchone()
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
        room = settings.contact_limit - contact_count(conn, aid)
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
    links = links_for(a["slug"])
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


# ---------------------------------------------------------------- inbound webhooks
def _twilio_signed(request: Request, params: dict) -> bool:
    import base64
    import hashlib
    import hmac

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
