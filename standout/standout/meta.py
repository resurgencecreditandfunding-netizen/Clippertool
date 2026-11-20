"""Facebook Pixel + Conversions API (CAPI).

Why this matters for cheaper leads: Meta's ad delivery learns from the conversions it can see.
Browser-only pixels lose a large share of events to ad blockers and iOS privacy. So every funnel event
is sent twice, from the browser (Pixel) and from our server (CAPI), with the SAME event_id
so Meta counts it once (deduplication), plus hashed customer details so Meta can match the event to
the person who clicked the ad. Then the CRM keeps teaching it: when the owner moves a lead to
Contacted, Quoted or Won, that downstream event goes to Meta too, so ads can optimize for real jobs,
not just form fills.

Event map
  funnel page view    -> PageView + ViewContent   (pixel + server, deduplicated)
  form submitted      -> Lead (value = lead value) (pixel + server, deduplicated, advanced matching)
  lead -> Contacted   -> Contact                   (server, system_generated)
  lead -> Quoted      -> QualifiedLead (custom)    (server, system_generated)
  lead -> Won         -> Purchase (value = job $)  (server, system_generated)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid

from .config import settings
from .db import db, now

log = logging.getLogger("standout.meta")
PIXEL_RE = re.compile(r"^\d{10,20}$")
STAGE_EVENTS = {"contacted": "Contact", "quoted": "QualifiedLead", "won": "Purchase"}


def new_event_id() -> str:
    return uuid.uuid4().hex


def connected(profile: dict) -> bool:
    return bool(PIXEL_RE.match(profile.get("pixel_id", "")) and profile.get("capi_token"))


# ------------------------------------------------------------------ hashing / matching
def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def norm_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:  # US number without country code
        digits = "1" + digits
    return digits if len(digits) >= 11 else ""


def matching(email: str = "", phone: str = "", name: str = "", zip_code: str = "",
             external_id: str = "") -> dict:
    """Hashed customer-information parameters, in Meta's normalized format."""
    out: dict[str, list[str]] = {}
    email = (email or "").strip().lower()
    if "@" in email:
        out["em"] = [_sha(email)]
    ph = norm_phone(phone)
    if ph:
        out["ph"] = [_sha(ph)]
    parts = re.sub(r"[^a-z\s'-]", "", (name or "").lower()).split()
    if parts:
        out["fn"] = [_sha(parts[0])]
        if len(parts) > 1:
            out["ln"] = [_sha(parts[-1])]
    z = re.sub(r"\D", "", zip_code or "")[:5]
    if len(z) == 5:
        out["zp"] = [_sha(z)]
    if out.get("ph") or out.get("zp"):
        out["country"] = [_sha("us")]
    if external_id:
        out["external_id"] = [_sha(external_id)]
    return out


def make_fbc(fbclid: str, cookie_fbc: str = "") -> str:
    if cookie_fbc:
        return cookie_fbc
    if fbclid and re.fullmatch(r"[A-Za-z0-9_\-]{10,500}", fbclid):
        return f"fb.1.{int(time.time() * 1000)}.{fbclid}"
    return ""


def browser_data(ip: str, ua: str, fbc: str = "", fbp: str = "") -> dict:
    d = {}
    if ip:
        d["client_ip_address"] = ip
    if ua:
        d["client_user_agent"] = ua[:500]
    if fbc:
        d["fbc"] = fbc
    if fbp:
        d["fbp"] = fbp
    return d


def event(name: str, event_id: str, user_data: dict, custom: dict | None = None,
          source_url: str = "", action_source: str = "website") -> dict:
    e = {"event_name": name, "event_time": int(time.time()), "event_id": event_id,
         "action_source": action_source, "user_data": user_data}
    if source_url:
        e["event_source_url"] = source_url
    if custom:
        e["custom_data"] = custom
    return e


# ------------------------------------------------------------------ sending
def send(profile: dict, events: list[dict]) -> tuple[bool, str]:
    """POST events to the Conversions API. Returns (ok, human-readable detail)."""
    if not connected(profile):
        return False, "not connected"
    import httpx

    data = {"data": json.dumps(events), "access_token": profile["capi_token"]}
    if profile.get("capi_test_code"):
        data["test_event_code"] = profile["capi_test_code"]
    url = f"https://graph.facebook.com/{settings.meta_api_version}/{profile['pixel_id']}/events"
    try:
        r = httpx.post(url, data=data, timeout=20)
    except httpx.HTTPError as e:
        return False, f"network error: {e.__class__.__name__}"
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code < 300:
        return True, f"received {body.get('events_received', '?')}"
    err = body.get("error", {})
    return False, (err.get("error_user_msg") or err.get("message") or f"HTTP {r.status_code}")[:300]


def _log(account_id: int, names: str, ok: bool, detail: str) -> None:
    with db() as conn:
        conn.execute("INSERT INTO capi_log (account_id, event, status, detail, created) VALUES (?,?,?,?,?)",
                     (account_id, names, "ok" if ok else "error", detail, now()))
        # keep the log small: last 200 rows per account
        conn.execute("DELETE FROM capi_log WHERE account_id = ? AND id NOT IN "
                     "(SELECT id FROM capi_log WHERE account_id = ? ORDER BY id DESC LIMIT 200)",
                     (account_id, account_id))


def send_and_log(account_id: int, profile: dict, events: list[dict]) -> tuple[bool, str]:
    ok, detail = send(profile, events)
    if detail != "not connected":
        _log(account_id, ", ".join(e["event_name"] for e in events), ok, detail)
    return ok, detail


def send_later(account_id: int, profile: dict, events: list[dict]) -> None:
    """Fire-and-forget so the visitor's page never waits on Meta."""
    if connected(profile):
        threading.Thread(target=send_and_log, args=(account_id, profile, events), daemon=True).start()


def stage_event(account_id: int, profile: dict, contact: dict, stage: str, value: float) -> None:
    name = STAGE_EVENTS.get(stage)
    if not name or not connected(profile):
        return
    t = json.loads(contact.get("tracking") or "{}")
    ud = {**matching(contact.get("email", ""), contact.get("phone", ""), contact.get("name", ""),
                     external_id=f"{account_id}:{contact['id']}"),
          **browser_data("", "", t.get("fbc", ""), t.get("fbp", ""))}
    custom = {"lead_event_source": "StandOut CRM", "event_source": "crm", "content_name": contact.get("source", "")}
    if name == "Purchase":
        custom.update({"currency": "USD", "value": round(float(value or 0), 2)})
    send_later(account_id, profile, [event(name, f"{name.lower()}-{account_id}-{contact['id']}", ud, custom,
                                           action_source="system_generated")])


# ------------------------------------------------------------------ browser pixel
def _js(v) -> str:
    return json.dumps(v).replace("</", "<\\/")


def pixel_script(pixel_id: str, track: list[tuple[str, dict, str]], advanced: dict | None = None) -> str:
    """Standard Meta Pixel base code + track calls carrying our event IDs for deduplication."""
    if not PIXEL_RE.match(pixel_id or ""):
        return ""
    am = {k: v[0] for k, v in (advanced or {}).items() if k in ("em", "ph", "fn", "ln", "zp", "external_id")}
    calls = "".join(f"fbq('{'track' if n in STANDARD else 'trackCustom'}',{_js(n)},{_js(c)},{{eventID:{_js(eid)}}});"
                    for n, c, eid in track)
    return ("<script>!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?"
            "n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;"
            "n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;s=b.getElementsByTagName(e)[0];"
            "s.parentNode.insertBefore(t,s)}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');"
            f"fbq('init',{_js(pixel_id)},{_js(am)});{calls}</script>"
            f'<noscript><img height="1" width="1" style="display:none" alt="" '
            f'src="https://www.facebook.com/tr?id={pixel_id}&ev=PageView&noscript=1"></noscript>')


STANDARD = {"PageView", "ViewContent", "Lead", "Contact", "Purchase", "Schedule", "CompleteRegistration"}
