"""Follow-up automation: every funnel lead gets the funnel's text + email sequence.

A sequence stops on its own when the owner moves the lead out of "New", or the lead texts back.
Without SMTP/Twilio configured, messages are logged to the outbox as "simulated" so the whole
flow can be demoed live without sending anything.
"""
from __future__ import annotations

import json
import logging
import smtplib
import threading
from email.message import EmailMessage

from .config import settings
from .db import db, now
from .funnels import default_sequence
from .profile import complete, fill

log = logging.getLogger("standout.automation")


# ------------------------------------------------------------------ senders
def send_sms(to: str, body: str) -> str:
    if not to:
        return "skipped: no phone"
    if not (settings.twilio_sid and settings.twilio_token and settings.twilio_from):
        return "simulated"
    import httpx
    try:
        r = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_sid}/Messages.json",
            data={"To": to, "From": settings.twilio_from, "Body": body},
            auth=(settings.twilio_sid, settings.twilio_token), timeout=20)
        return "sent" if r.status_code < 300 else f"failed: {r.status_code}"
    except httpx.HTTPError as e:
        return f"failed: {e.__class__.__name__}"


def send_email(to: str, subject: str, body: str, reply_to: str = "") -> str:
    if not to:
        return "skipped: no email"
    if not (settings.smtp_host and settings.smtp_from):
        return "simulated"
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
            s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
        return "sent"
    except (smtplib.SMTPException, OSError) as e:
        return f"failed: {e.__class__.__name__}"


def log_message(conn, account_id: int, contact_id: int | None, direction: str, channel: str,
                body: str, status: str) -> None:
    conn.execute("INSERT INTO messages (account_id, contact_id, direction, channel, body, status, created) "
                 "VALUES (?,?,?,?,?,?,?)", (account_id, contact_id, direction, channel, body, status, now()))


# ------------------------------------------------------------------ sequences
def sequence_for(conn, account_id: int, funnel_key: str) -> list[dict]:
    r = conn.execute("SELECT sequence FROM funnels WHERE account_id = ? AND key = ?",
                     (account_id, funnel_key)).fetchone()
    seq = json.loads(r["sequence"]) if r else []
    return seq or default_sequence(funnel_key)


def enroll(conn, account_id: int, contact_id: int, funnel_key: str) -> None:
    seq = [s for s in sequence_for(conn, account_id, funnel_key) if s.get("on", True)]
    if not seq:
        return
    created = conn.execute("SELECT created FROM contacts WHERE id = ?", (contact_id,)).fetchone()["created"]
    conn.execute("UPDATE contacts SET seq_step = 0, seq_next = ?, seq_status = 'active' WHERE id = ?",
                 (created + seq[0]["delay"], contact_id))


def stop(conn, contact_id: int, reason: str) -> None:
    conn.execute("UPDATE contacts SET seq_status = ?, seq_next = NULL WHERE id = ? AND seq_status IN ('active','sending')",
                 (f"stopped: {reason}", contact_id))


def _first(name: str) -> str:
    return (name or "").split(" ")[0] or "there"


def run_due(limit: int = 200) -> int:
    """Send every follow-up step that is due. Returns how many messages went out.

    Each contact is claimed atomically (active -> sending) before anything is sent, so the
    background worker and the instant send after a form submit can never double-text a lead.
    Network calls happen outside any transaction so they never block the rest of the app.
    """
    sent = 0
    t = now()
    with db() as conn:
        due = [r["id"] for r in conn.execute(
            "SELECT id FROM contacts WHERE seq_status = 'active' AND seq_next <= ? ORDER BY seq_next LIMIT ?",
            (t, limit)).fetchall()]
    for cid in due:
        with db() as conn:
            claimed = conn.execute("UPDATE contacts SET seq_status = 'sending' WHERE id = ? AND seq_status = 'active'",
                                   (cid,)).rowcount
            c = conn.execute("SELECT c.*, a.profile, a.email AS owner_email FROM contacts c "
                             "JOIN accounts a ON a.id = c.account_id WHERE c.id = ?", (cid,)).fetchone()
        if not claimed or c is None:
            continue
        if c["stage"] != "new":
            with db() as conn:
                stop(conn, cid, "lead moved")
            continue
        with db() as conn:
            seq = [s for s in sequence_for(conn, c["account_id"], c["source"]) if s.get("on", True)]
        values = {**complete(json.loads(c["profile"])), "first": _first(c["name"]),
                  "name": c["name"], "service": c["details"][:60]}
        step = c["seq_step"]
        # send every step that's due (steps with the same delay go out together)
        while step < len(seq) and c["created"] + seq[step]["delay"] <= t:
            s = seq[step]
            body = fill(s["body"], values)
            if s["channel"] == "sms":
                status = send_sms(c["phone"], body)
            else:
                subject = fill(s.get("subject") or "Following up", values)
                status = send_email(c["email"], subject, body, reply_to=c["owner_email"])
                body = f"Subject: {subject}\n\n{body}"
            with db() as conn:
                log_message(conn, c["account_id"], cid, "out", s["channel"], body, status)
                conn.execute("UPDATE contacts SET seq_step = ? WHERE id = ?", (step + 1, cid))
            sent += 1
            step += 1
            with db() as conn:  # owner moved the lead or they replied mid-send: stop here
                if conn.execute("SELECT seq_status FROM contacts WHERE id = ?", (cid,)).fetchone()[0] != "sending":
                    break
        with db() as conn:
            if step >= len(seq):
                conn.execute("UPDATE contacts SET seq_next = NULL, seq_status = 'finished' "
                             "WHERE id = ? AND seq_status = 'sending'", (cid,))
            else:
                conn.execute("UPDATE contacts SET seq_next = ?, seq_status = 'active' WHERE id = ? AND seq_status = 'sending'",
                             (c["created"] + seq[step]["delay"], cid))
    return sent


def notify_owner(account: dict, subject: str, body: str) -> None:
    p = complete(account["profile"])
    send_email(account["email"], subject, body)
    if account["profile"].get("owner_mobile"):
        send_sms(account["profile"]["owner_mobile"], f"{p['business']}: {subject}")


def handle_inbound_sms(from_number: str, body: str) -> bool:
    """A lead texted back: log it, stop their sequence, flag them. True if a contact matched."""
    digits = "".join(ch for ch in from_number if ch.isdigit())[-10:]
    if not digits:
        return False
    with db() as conn:
        rows = conn.execute("SELECT id, account_id, phone FROM contacts ORDER BY updated DESC").fetchall()
        match = next((r for r in rows if "".join(ch for ch in r["phone"] if ch.isdigit())[-10:] == digits), None)
        if not match:
            return False
        log_message(conn, match["account_id"], match["id"], "in", "sms", body, "received")
        is_stop = body.strip().upper() in {"STOP", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
        stop(conn, match["id"], "opted out" if is_stop else "replied")
        conn.execute("UPDATE contacts SET updated = ? WHERE id = ?", (now(), match["id"]))
    return True


class Worker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="followups", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(settings.worker_interval):
            for job in (run_due, run_due_posts):
                try:
                    job()
                except Exception:  # keep the worker alive no matter what
                    log.exception("%s failed", job.__name__)

    def shutdown(self) -> None:
        self._stop.set()


worker = Worker()


# ------------------------------------------------------------------ scheduled posts
def publish_facebook(page_id: str, token: str, message: str) -> str:
    import httpx
    try:
        r = httpx.post(f"https://graph.facebook.com/{settings.meta_api_version}/{page_id}/feed",
                       data={"message": message, "access_token": token}, timeout=30)
        return "posted" if r.status_code < 300 else f"failed: {r.status_code}"
    except httpx.HTTPError as e:
        return f"failed: {e.__class__.__name__}"


def run_due_posts() -> int:
    """Scheduled posts whose time has come: publish to the Facebook Page if connected,
    otherwise email the owner the ready-to-post caption so they can post it in one tap."""
    import datetime as dt
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    done = 0
    with db() as conn:
        rows = conn.execute(
            "SELECT p.*, a.profile, a.email AS owner_email FROM posts p JOIN accounts a ON a.id = p.account_id "
            "WHERE p.status = 'scheduled'").fetchall()
        for r in rows:
            prof = json.loads(r["profile"])
            try:
                tz = ZoneInfo(complete(prof)["timezone"])
            except (ZoneInfoNotFoundError, ValueError):
                tz = ZoneInfo("America/New_York")
            try:
                when = dt.datetime.fromisoformat(f"{r['day']}T{r['time']}").replace(tzinfo=tz)
            except ValueError:
                continue
            if when > dt.datetime.now(tz):
                continue
            if prof.get("fb_page_id") and prof.get("fb_page_token"):
                status = publish_facebook(prof["fb_page_id"], prof["fb_page_token"], r["caption"])
                new = "posted" if status == "posted" else "failed"
            else:
                status = send_email(r["owner_email"], f"Time to post: {r['title']}",
                                    f"Your post for today is ready. Copy, paste, post.\n\n"
                                    f"--- CAPTION ---\n{r['caption']}\n\n--- SCRIPT ---\n{r['script']}")
                new = "reminded"
            conn.execute("UPDATE posts SET status = ? WHERE id = ?", (new, r["id"]))
            log_message(conn, r["account_id"], None, "out", "post", f"{r['title']} -> {new}", status)
            done += 1
    return done
