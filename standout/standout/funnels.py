"""Four ready-made funnels for local service businesses.

Owners pick nothing from a library: these four cover how local jobs actually come in. Every page,
ad and follow-up message fills itself from the business profile, so there is no per-client setup.
"""
from __future__ import annotations

import copy
import html
import json

from .profile import complete, fill, split

HOUR = 3600
DAY = 86400

# Each step: delay (seconds after the lead came in), channel ("sms" | "email"), subject, body.
FUNNELS: dict[str, dict] = {
    "estimate": {
        "name": "Free Estimate",
        "purpose": "Your main lead getter. Send most of your ads here.",
        "headline": "Get a Free {Trade} Estimate in {city}",
        "sub": "Upfront pricing. No pressure. {business} answers fast.",
        "bullets": ["Honest, upfront quote before any work starts",
                    "{guarantee}",
                    "Local to {service_area}"],
        "button": "Get My Free Estimate",
        "fields": ["name", "phone", "email", "service", "details"],
        "thanks": "Got it, {first}! We'll reach out shortly to set up your estimate.",
        "ad": {
            "primary": "{city} homeowners: tired of contractors who don't call back?\n\n"
                       "{business} gives you a clear, upfront {trade} quote, with no surprises and no pressure.\n\n"
                       "✅ {difference}\n✅ {guarantee}\n\nTap below to get your free estimate. It takes 30 seconds.",
            "headline": "Free {Trade} Estimate in {city}",
            "angle": "Problem / relief: speak to the frustration, then offer the easy next step.",
        },
        "sequence": [
            {"delay": 0, "channel": "sms", "subject": "",
             "body": "Hi {first}, this is {owner} with {business}. Got your estimate request! "
                     "What's a good time today or tomorrow for a quick call? Reply here or call {phone}."},
            {"delay": 0, "channel": "email", "subject": "We got your request, {first}",
             "body": "Hi {first},\n\nThanks for reaching out to {business}. We received your request and "
                     "will contact you shortly to set up your free estimate.\n\nNeed us sooner? Call {phone}.\n\n{owner}\n{business}"},
            {"delay": 2 * HOUR, "channel": "sms", "subject": "",
             "body": "Hi {first}, just making sure you saw my text. When works best for your free estimate? - {owner}, {business}"},
            {"delay": 1 * DAY, "channel": "email", "subject": "What to expect from your estimate",
             "body": "Hi {first},\n\nHere's how we work: we show up on time, look at the job, and give you a clear price "
                     "before anything starts. {guarantee}.\n\nReply to this email or call {phone} to pick a time.\n\n{owner}"},
            {"delay": 3 * DAY, "channel": "sms", "subject": "",
             "body": "{first}, still need help with your {trade} project? We have openings this week. Reply YES and I'll get you on the schedule."},
            {"delay": 7 * DAY, "channel": "email", "subject": "Should I close your file?",
             "body": "Hi {first},\n\nI haven't heard back so I'll assume the timing isn't right. If you still want "
                     "that free estimate, just reply and we'll take care of you.\n\n{owner}\n{business} · {phone}"},
        ],
    },
    "special": {
        "name": "Seasonal Special",
        "purpose": "Fill the schedule in slow weeks with a time-limited offer.",
        "headline": "{offer}",
        "sub": "For {service_area} customers. Limited spots this month.",
        "bullets": ["Claim it now and book when it works for you",
                    "{difference}",
                    "{guarantee}"],
        "button": "Claim My Offer",
        "fields": ["name", "phone", "email"],
        "thanks": "You're in, {first}! Your offer is saved. We'll text you to book a time.",
        "ad": {
            "primary": "🔥 This month only for {city}: {offer}\n\n{business} is opening a limited number of spots. "
                       "{difference}.\n\nClaim yours below before they're gone.",
            "headline": "{offer}",
            "angle": "Scarcity + specific offer. Run it 7 to 14 days, then rotate.",
        },
        "sequence": [
            {"delay": 0, "channel": "sms", "subject": "",
             "body": "Hi {first}! Your offer from {business} is locked in: {offer}. Want me to grab you a time this week? Reply YES."},
            {"delay": 0, "channel": "email", "subject": "Your offer is saved: {offer}",
             "body": "Hi {first},\n\nYour offer is saved: {offer}.\n\nReply or call {phone} to book. Spots are limited this month.\n\n{owner}\n{business}"},
            {"delay": 1 * DAY, "channel": "sms", "subject": "",
             "body": "{first}, spots for the {business} special are filling up. Want one? Reply YES and I'll book you."},
            {"delay": 4 * DAY, "channel": "email", "subject": "Last call on your offer",
             "body": "Hi {first},\n\nQuick heads up: the special ({offer}) ends soon. Reply to this email or call {phone} to use it.\n\n{owner}"},
        ],
    },
    "emergency": {
        "name": "Same-Day Help",
        "purpose": "For urgent calls. Pushes people to call now, with a callback form as backup.",
        "headline": "Need {Trade} Help Today in {city}?",
        "sub": "Call now: {phone}. Or leave your number and we'll call you right back.",
        "bullets": ["Fast response across {service_area}",
                    "Clear price before we start",
                    "{guarantee}"],
        "button": "Call Me Back Now",
        "fields": ["name", "phone", "details"],
        "thanks": "Thanks {first}, we're calling you back now. Urgent? Call {phone}.",
        "ad": {
            "primary": "Problem that can't wait? {business} handles same-day {trade} calls in {city}.\n\n"
                       "📞 Call {phone} or tap below and we'll call you right back.",
            "headline": "Same-Day {Trade} Help",
            "angle": "Urgency. Best on mobile, with a call button and tight local targeting.",
        },
        "sequence": [
            {"delay": 0, "channel": "sms", "subject": "",
             "body": "Hi {first}, {owner} from {business} here. We got your request and are calling you shortly. "
                     "If it's urgent, call {phone} now."},
            {"delay": 30 * 60, "channel": "sms", "subject": "",
             "body": "{first}, tried to reach you about your {trade} issue. Still need help today? Reply or call {phone}."},
            {"delay": 1 * DAY, "channel": "sms", "subject": "",
             "body": "Hi {first}, did you get your {trade} issue handled? If not, we can be there today. - {business}"},
        ],
    },
    "referral": {
        "name": "Review & Refer",
        "purpose": "Send to happy customers. Gets reviews and referrals on autopilot.",
        "headline": "Thank You for Choosing {business}!",
        "sub": "Know someone who needs {trade} help? Send them our way and you both get taken care of.",
        "bullets": ["Your friend gets VIP scheduling",
                    "You get a thank-you from us when they book",
                    "Takes 20 seconds"],
        "button": "Send My Referral",
        "fields": ["name", "phone", "email", "friend_name", "friend_phone"],
        "thanks": "Thank you, {first}! We'll take great care of your friend.",
        "ad": {
            "primary": "To everyone in {city} who has trusted {business}: thank you. 🙏\n\n"
                       "Know a neighbor who needs {trade} help? Send them our way. Tap below.",
            "headline": "Refer a Neighbor",
            "angle": "Retarget past customers and page engagers. Cheap, high-trust leads.",
        },
        "sequence": [
            {"delay": 0, "channel": "sms", "subject": "",
             "body": "Thanks so much {first}! One quick favor: would you leave us a Google review? It helps a local business a ton: {review_link}"},
            {"delay": 3 * DAY, "channel": "email", "subject": "A quick favor, {first}?",
             "body": "Hi {first},\n\nThank you for sending a referral to {business}. If you haven't yet, a short Google review "
                     "would mean the world to us: {review_link}\n\n{owner}"},
        ],
    },
}

FIELD_LABELS = {
    "name": ("Your name", "text", True),
    "phone": ("Mobile phone", "tel", True),
    "email": ("Email", "email", False),
    "service": ("What do you need?", "text", False),
    "details": ("Tell us a bit more", "textarea", False),
    "friend_name": ("Friend's name", "text", False),
    "friend_phone": ("Friend's phone", "tel", False),
}


def default_sequence(key: str) -> list[dict]:
    return copy.deepcopy(FUNNELS[key]["sequence"])


def merged(key: str, overrides: dict) -> dict:
    """Template plus any copy the owner changed."""
    f = copy.deepcopy(FUNNELS[key])
    for k in ("headline", "sub", "button", "thanks"):
        if overrides.get(k):
            f[k] = overrides[k]
    if overrides.get("bullets"):
        f["bullets"] = [b for b in overrides["bullets"] if b.strip()]
    return f


def render_ad(key: str, profile: dict, link: str) -> dict:
    p = complete(profile)
    ad = FUNNELS[key]["ad"]
    return {"primary": fill(ad["primary"], p), "headline": fill(ad["headline"], p),
            "angle": ad["angle"], "link": link}


def _e(s: str) -> str:
    return html.escape(s, quote=True)


def render_page(key: str, overrides: dict, profile: dict, action: str) -> str:
    p = complete(profile)
    f = merged(key, overrides)
    color = p["color"] if p["color"].startswith("#") and len(p["color"]) in (4, 7) and p["color"] != "#000000" else "#1f6feb"
    bullets = "".join(f"<li>{_e(fill(b, p))}</li>" for b in f["bullets"])
    inputs = []
    for name in f["fields"]:
        label, kind, req = FIELD_LABELS[name]
        r = " required" if req else ""
        if kind == "textarea":
            inputs.append(f'<label>{label}<textarea name="{name}" rows="3"{r}></textarea></label>')
        else:
            inputs.append(f'<label>{label}<input name="{name}" type="{kind}"{r}></label>')
    call = ""
    if p["phone"]:
        tel = "".join(c for c in p["phone"] if c.isdigit() or c == "+")
        call = f'<a class="call" href="tel:{_e(tel)}">📞 Call {_e(p["phone"])}</a>'
    years = f'<span>{_e(p["years"])}+ years serving {_e(p["city"])}</span>' if p["years"] else ""
    services = "".join(f"<span>{_e(s)}</span>" for s in split(p["services"])[:6])
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(fill(f["headline"], p))} | {_e(p["business"])}</title>
<style>
:root{{--brand:{color}}}*{{box-sizing:border-box}}body{{margin:0;font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#14181f;background:#f5f7fa}}
header{{background:var(--brand);color:#fff;padding:14px 16px;font-weight:700}}
main{{max-width:980px;margin:0 auto;padding:28px 16px;display:grid;gap:28px;grid-template-columns:1.2fr 1fr}}
@media(max-width:760px){{main{{grid-template-columns:1fr}}}}
h1{{font-size:clamp(28px,5vw,42px);line-height:1.1;margin:0 0 12px}}.sub{{font-size:18px;color:#3c4656}}
ul{{padding-left:0;list-style:none}}li{{padding:6px 0 6px 28px;position:relative}}li:before{{content:"✓";position:absolute;left:0;color:var(--brand);font-weight:800}}
.tags span{{display:inline-block;background:#fff;border:1px solid #dde3ea;border-radius:99px;padding:4px 12px;margin:0 6px 6px 0;font-size:14px}}
form{{background:#fff;border-radius:14px;padding:22px;box-shadow:0 6px 24px rgba(20,24,31,.08);display:grid;gap:12px;align-self:start}}
label{{display:grid;gap:4px;font-size:14px;font-weight:600}}input,textarea{{font:inherit;padding:11px;border:1px solid #c9d1db;border-radius:8px}}
button,.call{{font:inherit;font-weight:700;background:var(--brand);color:#fff;border:0;border-radius:10px;padding:14px;cursor:pointer;text-align:center;text-decoration:none;display:block}}
.call{{background:#14181f;margin-top:10px}}.trust{{color:#3c4656;font-size:14px}}.hp{{position:absolute;left:-5000px}}
footer{{text-align:center;color:#6b7686;font-size:13px;padding:24px}}
</style></head><body><header>{_e(p["business"])}</header><main>
<section><h1>{_e(fill(f["headline"], p))}</h1><p class="sub">{_e(fill(f["sub"], p))}</p>
<ul>{bullets}</ul><div class="tags">{services}</div><p class="trust">{years}</p>{call}</section>
<form method="post" action="{_e(action)}"><strong>{_e(fill(f["button"], p))}</strong>
{''.join(inputs)}<input class="hp" name="website" tabindex="-1" autocomplete="off">
<button type="submit">{_e(fill(f["button"], p))}</button>
<small class="trust">By submitting you agree to receive texts and emails from {_e(p["business"])} about your request. Reply STOP to opt out.</small></form>
</main><footer>{_e(p["business"])} · {_e(p["service_area"])}</footer></body></html>"""


def render_thanks(key: str, overrides: dict, profile: dict, first: str) -> str:
    p = complete(profile)
    f = merged(key, overrides)
    msg = fill(f["thanks"], {**p, "first": first or "there"})
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Thank you | {_e(p["business"])}</title>
<style>body{{margin:0;font:18px/1.5 system-ui,sans-serif;background:#f5f7fa;color:#14181f;display:grid;place-items:center;min-height:100vh;padding:16px}}
div{{background:#fff;border-radius:14px;padding:32px;max-width:520px;text-align:center;box-shadow:0 6px 24px rgba(20,24,31,.08)}}</style>
</head><body><div><h1>✅ Thank you!</h1><p>{_e(msg)}</p></div></body></html>"""


def dumps(v) -> str:
    return json.dumps(v)
