"""The content engine: what to say, when to say it, and the script to say it with.

The method (the part owners can't figure out on their own):
- A fixed WEEKLY RHYTHM so every day has a job: teach, show, prove, warn, ask, connect, share.
- A MONTHLY ARC that builds attention, then trust, then proof, then fills the schedule.
- Every post points to exactly one funnel, so content feeds the same pipeline the ads do.

Built-in templates always work (no API key needed). With an Anthropic key, Claude writes
fresher, trade-specific scripts using the same method.
"""
from __future__ import annotations

import datetime as dt
import json
import logging

from .config import settings
from .profile import complete, fill, split

log = logging.getLogger("standout.content")

# weekday() -> pillar. Monday = 0.
RHYTHM = [
    {"pillar": "Teach", "format": "Short video", "time": "07:30", "funnel": "estimate",
     "why": "Start the week by being useful. Tips build authority and get saved and shared."},
    {"pillar": "Behind the Scenes", "format": "Short video", "time": "12:00", "funnel": "estimate",
     "why": "Show the work. People hire who they've seen do the job."},
    {"pillar": "Proof", "format": "Photo carousel", "time": "18:30", "funnel": "estimate",
     "why": "Midweek proof: before/after and reviews do the selling for you."},
    {"pillar": "Mistake / Myth", "format": "Short video", "time": "07:30", "funnel": "emergency",
     "why": "Warn them about a costly mistake. Urgent problems turn into same-day calls."},
    {"pillar": "Offer", "format": "Talking head video", "time": "11:00", "funnel": "special",
     "why": "Friday is the ask. Clear offer, clear deadline, one link."},
    {"pillar": "Local", "format": "Photo", "time": "10:00", "funnel": "referral",
     "why": "Weekend community post. Be the local name people recognize."},
    {"pillar": "Story", "format": "Talking head video", "time": "19:00", "funnel": "estimate",
     "why": "Sunday story: why you do this. Trust is built on people, not logos."},
]

ARC = [
    {"week": 1, "theme": "Get Noticed", "goal": "Reach new people in your area"},
    {"week": 2, "theme": "Build Trust", "goal": "Show how you work and who you are"},
    {"week": 3, "theme": "Prove It", "goal": "Stack reviews and real results"},
    {"week": 4, "theme": "Fill the Schedule", "goal": "Push the offer and book jobs"},
    {"week": 5, "theme": "Fill the Schedule", "goal": "Close out the month strong"},
]

FUNNEL_CTA = {
    "estimate": "Tap the link to get your free estimate",
    "special": "Tap the link to claim {offer}",
    "emergency": "Need help today? Call {phone} or tap the link",
    "referral": "Know someone who needs this? Send them our way at the link",
}

# Each template: title, hook (first 3 seconds), beats (the body), film (shoot notes).
TEMPLATES: dict[str, list[dict]] = {
    "Teach": [
        {"title": "The 30-second {service} tip",
         "hook": "Do this one thing and you'll save money on {service}.",
         "beats": ["Name the tip in one sentence.", "Show it: do it on camera, step by step.",
                   "Say what it prevents: {problem}.", "Tell them when to call a pro instead."],
         "film": "Phone vertical, show your hands doing the tip. Natural light. Under 45 seconds."},
        {"title": "How often should you really get {service}?",
         "hook": "Most people in {city} wait way too long for {service}.",
         "beats": ["Give the honest answer: how often, and why.", "Signs it's overdue.",
                   "What waiting costs: {problem}.", "Easy rule of thumb they can remember."],
         "film": "Talk to camera on a job site. Cut to a close-up of the warning sign."},
        {"title": "What a {trade} pro checks first",
         "hook": "When I walk into a home, this is the first thing I check.",
         "beats": ["Show the first thing you check.", "Explain what a good one looks like vs a bad one.",
                   "How a homeowner can check it themselves.", "When it's time to call."],
         "film": "Point-of-view walk-in shot, then close-ups."},
        {"title": "3 questions to ask any {trade} company",
         "hook": "Before you hire anyone for {service}, ask these 3 questions.",
         "beats": ["Question 1: Is the price upfront, in writing?", "Question 2: What happens if something goes wrong?",
                   "Question 3: Can I see recent reviews from {city}?", "How you answer all three: {difference}."],
         "film": "Talking head, hold up 1-2-3 fingers. Text on screen for each question."},
    ],
    "Behind the Scenes": [
        {"title": "A day on the job in {city}",
         "hook": "Come with me on a {service} job in {city}.",
         "beats": ["Arrive: show the truck, tools, and that you're on time.", "The problem you found.",
                   "Quick clips of the fix.", "Clean-up and the finished result."],
         "film": "5-8 quick clips, 2-3 seconds each. Add voiceover after."},
        {"title": "What's in the truck",
         "hook": "Here's why we can fix most problems on the first visit.",
         "beats": ["Open the truck and show how it's organized.", "The 3 tools or parts you use most.",
                   "Why showing up prepared saves the customer money."],
         "film": "Walk-around of the truck or van. Bright daylight."},
        {"title": "How we leave a home",
         "hook": "This is the part of the job most contractors skip.",
         "beats": ["Show your clean-up routine.", "Shoe covers, drop cloths, wipe-down.",
                   "Why it matters: {difference}."],
         "film": "Before/after of the work area. Slow pan."},
        {"title": "Meet the team",
         "hook": "These are the people who'll show up at your door.",
         "beats": ["Introduce each person in one line.", "One fun fact each.",
                   "What every one of them promises: {guarantee}."],
         "film": "Each team member says their name and role to camera."},
    ],
    "Proof": [
        {"title": "Before & after: {service}",
         "hook": "Swipe to see the difference.",
         "beats": ["Slide 1: the before (the problem).", "Slide 2-3: the work.",
                   "Slide 4: the after.", "Slide 5: what the customer said."],
         "film": "Same angle for before and after photos. Clean, well lit."},
        {"title": "Customer review spotlight",
         "hook": "This is why we do what we do.",
         "beats": ["Screenshot or read a real review.", "The problem they had.",
                   "How you solved it.", "Thank the customer by first name (with permission)."],
         "film": "Review screenshot + photo of the finished job."},
        {"title": "The problem we found",
         "hook": "The homeowner had no idea this was happening.",
         "beats": ["Show the hidden problem.", "What it would have turned into: {problem}.",
                   "The fix.", "Lesson for everyone watching."],
         "film": "Close-up of the damage, then the repaired result."},
        {"title": "Job of the week",
         "hook": "Our favorite job this week in {city}.",
         "beats": ["What they called about.", "What you did.", "The result.", "The customer's reaction."],
         "film": "Carousel: 3-5 photos from start to finish."},
    ],
    "Mistake / Myth": [
        {"title": "The {problem} mistake",
         "hook": "Stop doing this. It's how {problem} starts.",
         "beats": ["Name the common mistake.", "Why people do it.",
                   "What it actually causes, and what it costs to fix.", "What to do instead."],
         "film": "Talking head, then show the mistake. Big text on screen: STOP."},
        {"title": "Myth: 'I can wait on {service}'",
         "hook": "'It can wait.' That's the most expensive sentence in {trade}.",
         "beats": ["The myth.", "What really happens when you wait.",
                   "A real example from a job (no names).", "The cheap fix vs the expensive fix."],
         "film": "Talking head on a job site."},
        {"title": "DIY vs calling a pro",
         "hook": "When DIY is fine, and when it costs you double.",
         "beats": ["What's safe to DIY.", "What's not, and why.",
                   "The warning sign that means call now."],
         "film": "Split screen or two quick scenes: OK vs NOT OK."},
        {"title": "Warning signs you can't ignore",
         "hook": "If you notice any of these, call a {trade} pro today.",
         "beats": ["Sign 1.", "Sign 2.", "Sign 3: {problem}.", "What to do while you wait for help."],
         "film": "Text list on screen, quick clip for each sign."},
    ],
    "Offer": [
        {"title": "This month's special",
         "hook": "{city}, this month only: {offer}.",
         "beats": ["What the offer is, in one sentence.", "Who it's for: {ideal_customer}.",
                   "Why you're doing it.", "The deadline or the limited number of spots."],
         "film": "Talking head, smiling, in front of the truck. Offer on screen."},
        {"title": "We have openings this week",
         "hook": "We just had a few openings this week in {city}.",
         "beats": ["Say what you can help with: {service}.", "Mention the offer: {offer}.",
                   "How fast you can be there.", "Tell them exactly what to do next."],
         "film": "Talking head from the driver's seat. Casual and direct."},
        {"title": "Our promise to you",
         "hook": "Here's what you get when you hire {business}.",
         "beats": ["Upfront price.", "{guarantee}.", "{difference}.", "The offer right now: {offer}."],
         "film": "Talking head, text on screen for each promise."},
        {"title": "Free estimate, no pressure",
         "hook": "Not sure what it'll cost? Let's find out for free.",
         "beats": ["What happens at the estimate.", "How long it takes.",
                   "That there's zero pressure.", "How to book it."],
         "film": "Talking head at a customer's door (with permission) or in the shop."},
    ],
    "Local": [
        {"title": "Shout-out to a local business",
         "hook": "Our favorite spot in {city} right now.",
         "beats": ["Name a local business you love.", "Why you love it.", "Tag them."],
         "film": "Photo of you or the team at the business."},
        {"title": "Proud to serve {city}",
         "hook": "Where we worked this week.",
         "beats": ["List neighborhoods you served.", "Thank the community.", "Invite referrals."],
         "film": "Map screenshot or a photo of the truck in a recognizable local spot."},
        {"title": "Weekend {trade} checklist",
         "hook": "Quick weekend checklist for {city} homeowners.",
         "beats": ["3 quick things to check this weekend.", "Why now (season or weather).", "Save this post."],
         "film": "Simple graphic or a photo with the checklist in the caption."},
        {"title": "Community moment",
         "hook": "Something we're proud of this week.",
         "beats": ["A sponsorship, charity, local team, or event.", "Why it matters to you."],
         "film": "Real photo from the event."},
    ],
    "Story": [
        {"title": "Why I started {business}",
         "hook": "I started {business} because I was tired of seeing this happen.",
         "beats": ["What you saw that bothered you.", "The moment you decided to start.",
                   "What you promised yourself you'd do differently: {difference}."],
         "film": "Talking head, sitting down, warm light. Speak from the heart."},
        {"title": "A customer I'll never forget",
         "hook": "This customer reminded me why I do this.",
         "beats": ["Who they were (no names).", "What they were going through.",
                   "What you did.", "What it meant to you."],
         "film": "Talking head. Keep it real; no script reading."},
        {"title": "The hardest lesson in business",
         "hook": "The hardest lesson I learned running a {trade} business.",
         "beats": ["The mistake you made.", "What it cost you.", "What you do now because of it."],
         "film": "Talking head, honest and short."},
        {"title": "What we stand for",
         "hook": "Here's the standard every job at {business} has to meet.",
         "beats": ["Your values in 3 words.", "An example of each.", "{guarantee}."],
         "film": "Talking head, or the team standing together."},
    ],
}


def next_monday(today: dt.date | None = None) -> dt.date:
    today = today or dt.date.today()
    return today + dt.timedelta(days=(7 - today.weekday()) % 7 or 7)


def _values(p: dict, i: int) -> dict:
    services = split(p["services"]) or [p["trade"]]
    problems = split(p["problems"]) or ["a bigger repair bill"]
    return {**p, "service": services[i % len(services)], "problem": problems[i % len(problems)]}


def build_post(p: dict, date: dt.date, index: int, funnel_links: dict[str, str]) -> dict:
    rhythm = RHYTHM[date.weekday()]
    week = min(index // 7, len(ARC) - 1)
    pillar = rhythm["pillar"]
    funnel = rhythm["funnel"]
    if ARC[week]["theme"] == "Fill the Schedule" and pillar in ("Teach", "Story"):
        funnel = "special"  # late month: more posts point to the offer
    t = TEMPLATES[pillar][(index // 7) % len(TEMPLATES[pillar])]
    v = _values(p, index)
    cta = fill(FUNNEL_CTA[funnel], v)
    beats = "\n".join(f"{n}. {fill(b, v)}" for n, b in enumerate(t["beats"], 1))
    hook = fill(t["hook"], v)
    script = (f"HOOK (first 3 seconds):\n\"{hook}\"\n\n"
              f"SAY / SHOW:\n{beats}\n\n"
              f"CLOSE:\n\"{cta}.\"\n\n"
              f"FILM IT:\n{t['film']}\n\n"
              f"WHY TODAY: {rhythm['why']}")
    link = funnel_links.get(funnel, "")
    tags = " ".join("#" + "".join(w.capitalize() for w in s.split()) for s in
                    [p["city"], p["trade"], f"{p['city']} {p['trade']}"])
    caption = f"{hook}\n\n{fill(t['beats'][0], v)}\n\n👉 {cta}: {link}\n\n{tags}".strip()
    return {"day": date.isoformat(), "time": rhythm["time"], "pillar": pillar, "format": rhythm["format"],
            "title": fill(t["title"], v), "hook": hook, "script": script, "cta": cta,
            "caption": caption, "funnel": funnel}


def template_calendar(profile: dict, start: dt.date, days: int, funnel_links: dict[str, str]) -> list[dict]:
    from .reviews import confirmed
    p = complete(profile)
    edge = confirmed(profile)
    posts = []
    for i in range(days):
        post = build_post(p, start + dt.timedelta(days=i), i, funnel_links)
        if edge and post["pillar"] in ("Mistake / Myth", "Offer"):
            d = edge[(i // 7) % len(edge)]
            post["script"] = post["script"].replace(
                "\n\nCLOSE:", f"\n+ Contrast it: \"{d['hook']}\" Then your promise: \"{d['promise']}\"\n\nCLOSE:", 1)
        posts.append(post)
    return posts


# ------------------------------------------------------------------ AI version
MODEL = "claude-opus-5-5"

SYSTEM = """You write social media content for local service businesses (plumbers, HVAC, roofers, \
cleaners, landscapers, electricians and the like). The owners are busy, not marketers, and \
film on their phone. Every script must be something they can shoot in under 5 minutes on a job site.

Follow this method exactly; it is the product:
- Weekly rhythm (each weekday has one job): Mon=Teach, Tue=Behind the Scenes, Wed=Proof, \
Thu=Mistake / Myth, Fri=Offer, Sat=Local, Sun=Story.
- Monthly arc: week 1 Get Noticed, week 2 Build Trust, week 3 Prove It, week 4+ Fill the Schedule.
- Every post points to exactly one funnel: estimate (free estimate), special (the current offer), \
emergency (same-day help), or referral (reviews and referrals). Use the funnel given for each day.

Rules for each post:
- hook: the first 3 seconds, spoken, max 14 words, specific to the trade and city. No clickbait.
- script: plain text with sections "HOOK", "SAY / SHOW" (3-5 numbered beats), "CLOSE", "FILM IT" \
(one or two lines of shooting directions). Talk like a real tradesperson, not an ad agency.
- caption: 2-4 short lines + the CTA + the funnel link exactly as given + 3 local hashtags.
- Never invent reviews, prices, licenses, awards or statistics. Use only facts from the profile; \
otherwise leave placeholders like [customer first name].
- Vary services and problems across the month; don't repeat a hook."""

SCHEMA = {
    "type": "object",
    "properties": {"posts": {"type": "array", "items": {
        "type": "object",
        "properties": {"day": {"type": "string"}, "title": {"type": "string"}, "hook": {"type": "string"},
                       "script": {"type": "string"}, "cta": {"type": "string"}, "caption": {"type": "string"}},
        "required": ["day", "title", "hook", "script", "cta", "caption"],
        "additionalProperties": False}}},
    "required": ["posts"],
    "additionalProperties": False,
}


def ai_available() -> bool:
    return bool(settings.anthropic_api_key)


def ai_calendar(profile: dict, start: dt.date, days: int, funnel_links: dict[str, str]) -> list[dict]:
    """Claude-written calendar on the same rhythm. Falls back to templates on any failure."""
    plan = template_calendar(profile, start, days, funnel_links)
    if not ai_available():
        return plan
    import anthropic

    p = complete(profile)
    skip = {"color", "color2", "owner_mobile", "logo", "font", "site_text", "voice_sample", "pixel_id",
            "capi_token", "capi_test_code", "fb_verify", "timezone", "Trade"}
    profile_txt = "\n".join(f"- {k}: {v}" for k, v in p.items() if k not in skip and not k.startswith("fb_")
                            and isinstance(v, str) and len(v) < 600)
    from .reviews import confirmed
    edge = confirmed(profile)
    if edge:
        profile_txt += ("\n\nWhat local customers hate about competitors, and this owner's answer (use on Mistake/Myth "
                        "and Offer days; never name or hint at a competitor):\n"
                        + "\n".join(f"- {d['hook']} -> {d['promise']}" for d in edge))
    if p.get("voice_guide"):
        profile_txt += f"\n\nBrand voice (write captions and scripts in this voice):\n{p['voice_guide']}"
    slots = "\n".join(f"- {x['day']} ({dt.date.fromisoformat(x['day']).strftime('%A')}): pillar={x['pillar']}, "
                      f"format={x['format']}, funnel={x['funnel']}, link={funnel_links.get(x['funnel'], '')}"
                      for x in plan)
    prompt = f"Business profile:\n{profile_txt}\n\nWrite one post for each of these {days} days:\n{slots}"
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2, timeout=600)
        with client.beta.messages.stream(
            model=MODEL, max_tokens=64000, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        ) as stream:
            resp = stream.get_final_message()
        if resp.stop_reason != "end_turn":
            log.warning("AI calendar stopped early: %s", resp.stop_reason)
            return plan
        data = json.loads(next(b.text for b in resp.content if b.type == "text"))
    except anthropic.AuthenticationError:
        log.error("Anthropic API key was rejected; using templates")
        return plan
    except (anthropic.APIError, StopIteration, ValueError) as e:
        log.warning("AI calendar failed (%s); using templates", e)
        return plan
    by_day = {x.get("day"): x for x in data.get("posts", [])}
    out = []
    for slot in plan:
        ai = by_day.get(slot["day"])
        if ai and all(str(ai.get(k, "")).strip() for k in ("hook", "script", "caption")):
            slot = {**slot, **{k: str(ai[k]).strip() for k in ("title", "hook", "script", "cta", "caption")}}
        out.append(slot)
    return out
