"""The business profile: filled in once, it drives every funnel, script and message."""
from __future__ import annotations

FIELDS = {
    "business": "Business name",
    "owner": "Owner first name",
    "trade": "Trade (e.g. plumbing, HVAC, roofing, cleaning)",
    "city": "Main city",
    "service_area": "Service area",
    "phone": "Business phone",
    "services": "Top services (comma separated)",
    "ideal_customer": "Ideal customer",
    "problems": "Top problems customers call about (comma separated)",
    "offer": "Current special offer",
    "guarantee": "Guarantee",
    "years": "Years in business",
    "difference": "What makes you different",
    "review_link": "Google review link",
    "color": "Brand color",
    "timezone": "Time zone",
    "owner_mobile": "Your mobile (for new-lead alerts)",
}

DEFAULTS = {
    "business": "Your Business",
    "owner": "the owner",
    "trade": "home service",
    "city": "your area",
    "service_area": "",
    "phone": "",
    "services": "repairs, installs, maintenance",
    "ideal_customer": "homeowners who want it done right the first time",
    "problems": "surprise breakdowns, contractors who never show up, unclear pricing",
    "offer": "$50 off your first service",
    "guarantee": "We show up on time and the job is done right, or we make it right",
    "years": "",
    "difference": "upfront pricing, on-time arrival and clean work",
    "review_link": "",
    "color": "#1f6feb",
    "timezone": "America/New_York",
    "owner_mobile": "",
}


def complete(profile: dict) -> dict:
    """Profile with defaults filled in for anything blank."""
    out = dict(DEFAULTS)
    for k, v in (profile or {}).items():
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    out["Trade"] = " ".join(w[:1].upper() + w[1:] for w in out["trade"].split())
    if not out["service_area"]:
        out["service_area"] = f"{out['city']} and surrounding areas"
    return out


def split(text: str) -> list[str]:
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def missing(profile: dict) -> list[str]:
    need = ("business", "trade", "city", "phone", "services", "offer")
    return [k for k in need if not str((profile or {}).get(k, "")).strip()]


class SafeDict(dict):
    def __missing__(self, key):
        return ""


def fill(template: str, values: dict) -> str:
    try:
        return template.format_map(SafeDict(values))
    except (ValueError, IndexError, AttributeError):
        return template  # owner typed a stray brace; show their text as-is
