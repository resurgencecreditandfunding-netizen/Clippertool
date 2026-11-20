"""Launch Kit: the exact Meta Ads Manager settings for a funnel, filled in for this business.

This is the "which buttons do I press" part, done for them. Every value the owner has to type
or paste is pre-filled from their profile, funnel, Pixel and confirmed promises, with a copy button.
Menu names in Ads Manager shift a little over time; the settings themselves are what matter.
"""
from __future__ import annotations

import datetime as dt
import re

from . import funnels, meta
from .profile import complete
from .reviews import confirmed

CTA = {"estimate": "Get quote", "special": "Get offer", "emergency": "Call now", "referral": "Learn more"}
WHEN = {
    "estimate": "Start here. This is your everyday lead getter.",
    "special": "Run it 7 to 14 days when the schedule has gaps, then turn it off.",
    "emergency": "For urgent trades (plumbing, HVAC, restoration). Keep the radius tight.",
    "referral": "Show it to past customers and people who engaged with your page, not cold audiences.",
}


def _utm(link: str, key: str) -> str:
    sep = "&" if "?" in link else "?"
    return f"{link}{sep}utm_source=facebook&utm_medium=paid&utm_campaign={key}"


def checks(acct: dict, enabled: bool) -> list[dict]:
    p = acct["profile"]
    return [
        {"ok": enabled, "label": "Funnel page is turned on", "fix": "Funnels → turn the switch on"},
        {"ok": meta.connected(p), "label": "Pixel + Conversions API connected",
         "fix": "Setup → Facebook tracking. Without it, Facebook can't learn who becomes a lead."},
        {"ok": bool(p.get("fb_verify")) or not acct.get("funnel_domain"), "label": "Your domain is verified with Facebook",
         "fix": "Setup → Facebook tracking → domain verification code"},
        {"ok": bool(confirmed(p)), "label": "Competitor-edge promises confirmed",
         "fix": "Setup → Your edge. Gives you ads built from what customers hate about competitors."},
        {"ok": bool(p.get("phone")), "label": "Business phone set", "fix": "Setup → Your business"},
    ]


def kit(acct: dict, key: str, link: str, enabled: bool) -> dict:
    p = complete(acct["profile"])
    raw = acct["profile"]
    name = funnels.FUNNELS[key]["name"]
    ad = funnels.render_ad(key, raw, link)
    gaps = funnels.gap_ads(raw, link)
    month = dt.date.today().strftime("%b %Y")
    radius = "10 miles" if key == "emergency" else "15 to 25 miles"
    pixel = raw.get("pixel_id") or "(connect your Pixel in Setup first)"
    budget = "$20 to $30 a day" if key != "special" else "$20 to $40 a day for 7 to 14 days"
    area = re.sub(r"\s+and surrounding areas$", "", p["service_area"])
    v = lambda label, value, note="", copy=True: {"label": label, "value": value, "note": note, "copy": copy}  # noqa: E731
    steps = [
        {"title": "1. Create the campaign", "items": [
            v("Click", "Ads Manager → + Create", copy=False),
            v("Campaign objective", "Leads", "Pick Leads. Not Traffic, not Engagement.", copy=False),
            v("Campaign name", f"{p['business']} | {name} | {month}"),
            v("Special ad category", "None", "Home services usually aren't a special category. If Meta asks, answer honestly.", copy=False),
            v("Budget", budget, "Turn on campaign budget (Advantage+). Start small. Don't raise it in the first 3 to 5 days.", copy=False),
        ]},
        {"title": "2. Set up the ad set", "items": [
            v("Conversion location", "Website", copy=False),
            v("Pixel / dataset", pixel, "Must match the Pixel connected in your Setup."),
            v("Conversion event", "Lead", "Your funnel sends this event from both the browser and our server.", copy=False),
            v("Performance goal", "Maximize number of conversions", copy=False),
            v("Location", f"{p['city']} + {radius}", f"Cover {area}. Remove areas you don't serve.", copy=False),
            v("Audience", "Advantage+ audience (let Facebook find buyers)", "Don't stack interests. Your Pixel data does the targeting.", copy=False),
            v("Placements", "Advantage+ placements", copy=False),
        ]},
        {"title": "3. Build the ad", "items": [
            v("Media", "A real photo or 15 to 30 second video from one of your jobs", "Real beats stock every time. Before/after works best.", copy=False),
            v("Primary text", ad["primary"]),
            v("Headline", ad["headline"]),
            v("Call to action button", CTA[key], copy=False),
            v("Website URL", _utm(link, key), "Includes tracking tags so you can see which campaign the lead came from."),
        ]},
    ]
    if gaps:
        steps.append({"title": "4. Add your competitor-gap ads (same ad set)", "items": [
            item for g in gaps for item in (
                v(f"Ad: {g['label']}: primary text", g["primary"], g["angle"]),
                v(f"Ad: {g['label']}: headline", g["headline"]))
        ] + [v("Why", "2 to 4 ads in one ad set lets Facebook find the message that works best for you.", copy=False)]})
    steps.append({"title": f"{len(steps) + 1}. After you hit Publish", "items": [
        v("Days 1 to 5", "Hands off. Facebook is learning. Changing things restarts the learning.", copy=False),
        v("Every day", "Open Leads. Your follow-up texts and emails are already going out. Call the hot ones.", copy=False),
        v("Move leads", "Drag leads to Contacted, Quoted, Won. Each move teaches Facebook what a real customer looks like.", copy=False),
        v("Level up", "Once you're getting about 50 Quoted or Won leads a week, make a copy of the campaign that optimizes "
                      "for QualifiedLead or Purchase instead of Lead. Now you're paying for jobs, not form fills.", copy=False),
    ]})
    return {"key": key, "name": name, "when": WHEN[key], "checks": checks(acct, enabled), "steps": steps}
