"""Competitor review intelligence: what customers hate about the competition, turned into your edge.

1. Find the top local competitors for the owner's trade + city (SerpApi, Google Maps).
2. Pull their lowest-rated reviews (1-2 stars).
3. Group the complaints into themes and rank them by how often they come up.
4. Turn each theme into a "distinguisher": a promise the owner can keep, the proof they can show,
   and an ad hook. The owner confirms only the ones they truly deliver.

Rules baked in: competitor names and review text never leave the owner's private dashboard.
Only the *message* (the confirmed promises and hooks) flows into funnels, ads, scripts and follow-up.
Owners without the API (or who prefer it) can paste reviews in by hand.
"""
from __future__ import annotations

import json
import logging
import re

from .config import settings
from .profile import complete

log = logging.getLogger("standout.reviews")
SERPAPI = "https://serpapi.com/search.json"
MODEL = "claude-opus-5-5"


class ReviewError(ValueError):
    pass


# Theme -> (label, keywords, promise, proof, ad hook). Used for the no-AI path and as the shared vocabulary.
THEMES: dict[str, dict] = {
    "no_show": {
        "label": "Didn't show up, or showed up late",
        "words": ["no show", "no-show", "never showed", "didn't show", "did not show", "never came", "stood up",
                  "late", "waited all", "waited for", "wasted my day", "rescheduled", "cancelled on", "canceled on"],
        "promise": "We show up when we say we will, and text you when we're on the way.",
        "proof": "Arrival window confirmed by text, plus an on-the-way message",
        "hook": "Tired of taking off work for a contractor who never shows?",
    },
    "communication": {
        "label": "Never called back / impossible to reach",
        "words": ["call back", "called back", "return my call", "returned my", "never heard", "no response",
                  "didn't respond", "did not respond", "voicemail", "ghosted", "no reply", "never answered",
                  "communication", "couldn't reach", "could not reach", "no follow"],
        "promise": "Every request gets a reply within minutes, from a real person at our company.",
        "proof": "Instant text on every request, then a call from the owner",
        "hook": "Sick of leaving voicemails nobody returns?",
    },
    "price": {
        "label": "Surprise pricing and hidden fees",
        "words": ["overcharg", "hidden fee", "charged me", "price gouge", "ripped off", "rip off", "rip-off",
                  "scam", "more than the quote", "more than quoted", "doubled", "outrageous", "too expensive",
                  "bait and switch", "surprise", "extra charge", "upcharge"],
        "promise": "You get the price in writing before any work starts. No surprise fees.",
        "proof": "Written, upfront quote you approve first",
        "hook": "Ever had the bill come in way higher than the quote?",
    },
    "quality": {
        "label": "Job not done right the first time",
        "words": ["not fixed", "didn't fix", "did not fix", "still broken", "still leak", "broke again", "had to call",
                  "came back", "redo", "re-do", "shoddy", "sloppy", "poor work", "poor quality", "botched",
                  "worse than", "didn't work", "stopped working", "another company"],
        "promise": "Done right the first time, or we come back and make it right at no charge.",
        "proof": "Written workmanship guarantee",
        "hook": "Paid someone to fix it and it's still not fixed?",
    },
    "mess": {
        "label": "Left a mess or damaged the property",
        "words": ["mess", "messy", "dirty", "damage", "damaged", "scratched", "clean up", "cleanup", "debris",
                  "trash", "left nails", "mud", "footprints", "broke my"],
        "promise": "We protect your home and leave it cleaner than we found it.",
        "proof": "Shoe covers, drop cloths, and a final walkthrough with you",
        "hook": "Did the last contractor leave you a mess to clean up?",
    },
    "pushy": {
        "label": "Pushy upsells or rude staff",
        "words": ["upsell", "up-sell", "pushy", "pressure", "rude", "attitude", "disrespect", "unprofessional",
                  "condescending", "salesman", "sales pitch", "talked down", "yelled"],
        "promise": "No pressure and no upsells. We recommend only what you actually need.",
        "proof": "Options explained plainly, with your decision respected",
        "hook": "Hate being upsold on stuff you don't need?",
    },
    "warranty": {
        "label": "Wouldn't stand behind the work",
        "words": ["warranty", "guarantee", "refund", "wouldn't honor", "would not honor", "refused to", "won't fix",
                  "blamed", "not responsible", "stand behind"],
        "promise": "Our guarantee is in writing, and we stand behind it.",
        "proof": "Written guarantee handed over at every job",
        "hook": "Couldn't get them to stand behind their own work?",
    },
}


# ------------------------------------------------------------------ fetching (SerpApi)
def available() -> bool:
    return bool(settings.serpapi_key)


def _serp(params: dict) -> dict:
    import httpx
    try:
        r = httpx.get(SERPAPI, params={**params, "api_key": settings.serpapi_key}, timeout=60)
    except httpx.HTTPError as e:
        raise ReviewError(f"Couldn't reach the review service ({e.__class__.__name__}).")
    try:
        data = r.json()
    except ValueError:
        raise ReviewError(f"Review service error (HTTP {r.status_code}).")
    if r.status_code >= 400 or data.get("error"):
        raise ReviewError(f"Review service: {data.get('error') or r.status_code}")
    return data


def _same_business(a: str, b: str) -> bool:
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())  # noqa: E731
    a, b = norm(a), norm(b)
    return bool(a and b and (a in b or b in a))


def find_competitors(trade: str, city: str, own_name: str = "", limit: int = 5) -> list[dict]:
    data = _serp({"engine": "google_maps", "type": "search", "q": f"{trade} near {city}", "hl": "en"})
    out = []
    for r in data.get("local_results", []):
        if not r.get("data_id") or _same_business(r.get("title", ""), own_name):
            continue
        out.append({"name": r.get("title", "")[:100], "data_id": r["data_id"], "rating": r.get("rating"),
                    "reviews": r.get("reviews") or 0})
    out.sort(key=lambda c: c["reviews"], reverse=True)  # the established players set the expectations
    return out[:limit]


def low_reviews(data_id: str, pages: int = 2) -> list[dict]:
    reviews, token = [], None
    for _ in range(pages):
        params = {"engine": "google_maps_reviews", "data_id": data_id, "sort_by": "ratingLow", "hl": "en"}
        if token:
            params.update({"next_page_token": token, "num": 20})
        data = _serp(params)
        batch = data.get("reviews", [])
        for r in batch:
            text = (r.get("extracted_snippet") or {}).get("original") or r.get("snippet") or ""
            if (r.get("rating") or 5) <= 2 and len(text.strip()) > 15:
                reviews.append({"rating": r.get("rating"), "text": " ".join(text.split())[:1500]})
        token = (data.get("serpapi_pagination") or {}).get("next_page_token")
        if not token or any((r.get("rating") or 5) > 2 for r in batch):
            break  # sorted lowest-first: once we see 3+ stars, the bad ones are done
    return reviews


def collect(profile: dict) -> tuple[list[dict], list[dict]]:
    """(competitors, low reviews) for this owner's trade and city."""
    if not available():
        raise ReviewError("Competitor lookup isn't turned on yet. Paste reviews instead.")
    p = complete(profile)
    comps = find_competitors(p["trade"], p["city"], p["business"])
    if not comps:
        raise ReviewError(f"Couldn't find {p['trade']} competitors near {p['city']}.")
    all_reviews = []
    for c in comps:
        try:
            rs = low_reviews(c["data_id"])
        except ReviewError as e:
            log.warning("reviews for %s failed: %s", c["name"], e)
            rs = []
        c["low_reviews"] = len(rs)
        all_reviews += [{**r, "competitor": c["name"]} for r in rs]
    return comps, all_reviews


def parse_pasted(text: str) -> list[dict]:
    """Reviews pasted by hand: separated by blank lines (or one per line)."""
    chunks = [c for c in re.split(r"\n\s*\n", text or "") if c.strip()]
    if len(chunks) <= 1:
        chunks = (text or "").splitlines()
    return [{"rating": None, "text": " ".join(c.split())[:1500], "competitor": ""} for c in chunks
            if len(c.strip()) > 15][:300]


# ------------------------------------------------------------------ analysis
def keyword_themes(reviews: list[dict]) -> list[dict]:
    counts = {k: [] for k in THEMES}
    for r in reviews:
        low = r["text"].lower()
        for k, t in THEMES.items():
            if any(w in low for w in t["words"]):
                counts[k].append(r["text"])
    total = max(1, len(reviews))
    out = []
    for k, hits in counts.items():
        if not hits:
            continue
        t = THEMES[k]
        out.append({"key": k, "label": t["label"], "count": len(hits), "share": round(100 * len(hits) / total),
                    "summary": f"{len(hits)} of {len(reviews)} bad reviews mention this.",
                    "promise": t["promise"], "proof": t["proof"], "hook": t["hook"],
                    "examples": [_excerpt(h) for h in hits[:3]]})
    return sorted(out, key=lambda x: x["count"], reverse=True)


def _excerpt(text: str, n: int = 170) -> str:
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


SYSTEM = """You analyze 1 and 2 star reviews of a local service business's competitors so the owner can \
position against them. Group the complaints into 3-7 themes (e.g. no-shows, never calls back, surprise \
pricing, not fixed right, mess or damage, pushy or rude, won't honor warranty; or trade-specific ones you \
find). For each theme give:
- label: short, in the customer's words (max 8 words)
- count: how many of the given reviews mention it (a review can count toward several themes)
- summary: one sentence on what actually went wrong for these customers
- promise: a specific, keepable promise that answers it, written in first person plural ("We ..."). \
Concrete beats vague ("text you 30 minutes before we arrive", not "great communication").
- proof: how the owner can show they keep it (a policy, a habit, a guarantee); no invented credentials
- hook: a one-line ad or video opener that names the frustration, max 14 words, never naming any company
- examples: up to 2 short paraphrases of the complaints, without names
Never name or hint at a competitor's identity in label, promise, proof or hook. Rank by count."""

SCHEMA = {
    "type": "object",
    "properties": {"themes": {"type": "array", "items": {
        "type": "object",
        "properties": {k: {"type": "string"} for k in ("label", "summary", "promise", "proof", "hook")}
        | {"count": {"type": "integer"}, "examples": {"type": "array", "items": {"type": "string"}}},
        "required": ["label", "count", "summary", "promise", "proof", "hook", "examples"],
        "additionalProperties": False}}},
    "required": ["themes"],
    "additionalProperties": False,
}


def analyze(reviews: list[dict], profile: dict, competitor_names: list[str] = ()) -> tuple[list[dict], bool]:
    """Themes ranked by frequency. Returns (themes, used_ai)."""
    fallback = keyword_themes(reviews)
    if not settings.anthropic_api_key or not reviews:
        return fallback, False
    import anthropic

    p = complete(profile)
    body = "\n".join(f"<review stars=\"{r['rating'] or '?'}\">{r['text']}</review>" for r in reviews[:250])
    prompt = (f"Owner's business: {p['business']}, a {p['trade']} company in {p['city']}.\n"
              f"Their current guarantee: {p['guarantee']}\nWhat they say makes them different: {p['difference']}\n\n"
              f"{len(reviews)} low-star competitor reviews:\n{body}")
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2, timeout=600)
        with client.beta.messages.stream(
            model=MODEL, max_tokens=16000, system=SYSTEM, messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        ) as stream:
            resp = stream.get_final_message()
        if resp.stop_reason != "end_turn":
            return fallback, False
        data = json.loads(next(b.text for b in resp.content if b.type == "text"))
    except anthropic.AuthenticationError:
        log.error("Anthropic API key was rejected")
        return fallback, False
    except (anthropic.APIError, StopIteration, ValueError) as e:
        log.warning("review analysis failed: %s", e)
        return fallback, False
    names = [n.lower() for n in competitor_names if len(n) > 3]
    total = max(1, len(reviews))
    themes = []
    for i, t in enumerate(data.get("themes", [])[:7]):
        public = " ".join(str(t.get(k, "")) for k in ("label", "promise", "proof", "hook")).lower()
        if any(n in public for n in names):
            continue  # never let a competitor's name reach public-facing copy
        count = max(0, min(int(t.get("count", 0)), total))
        themes.append({"key": f"ai{i}", "label": t["label"][:80], "count": count, "share": round(100 * count / total),
                       "summary": t["summary"][:300], "promise": t["promise"][:200], "proof": t["proof"][:200],
                       "hook": t["hook"][:140], "examples": [_excerpt(e) for e in t.get("examples", [])[:2]]})
    themes.sort(key=lambda x: x["count"], reverse=True)
    return (themes or fallback), bool(themes)


def build_report(profile: dict, pasted: str = "") -> dict:
    """Run the whole thing. Returns the report saved on the account."""
    if pasted.strip():
        comps, reviews, source = [], parse_pasted(pasted), "pasted"
        if not reviews:
            raise ReviewError("Paste at least one review (a sentence or more).")
    else:
        comps, reviews = collect(profile)
        source = "google"
        if not reviews:
            raise ReviewError("Your competitors don't have enough 1-2 star reviews to learn from. Lucky them.")
    themes, used_ai = analyze(reviews, profile, [c["name"] for c in comps])
    return {"source": source, "reviews_analyzed": len(reviews), "used_ai": used_ai,
            "competitors": [{"name": c["name"], "rating": c["rating"], "reviews": c["reviews"],
                             "low_reviews": c.get("low_reviews", 0)} for c in comps],
            "themes": themes}


def to_distinguishers(themes: list[dict], keep: int = 4) -> list[dict]:
    """Starting list for the owner to confirm. Nothing is 'on' until they say they really do it."""
    return [{"key": t["key"], "label": t["label"], "share": t["share"], "promise": t["promise"],
             "proof": t["proof"], "hook": t["hook"], "on": False} for t in themes[:keep]]


def confirmed(profile: dict) -> list[dict]:
    return [d for d in profile.get("distinguishers") or [] if d.get("on") and d.get("promise", "").strip()]
