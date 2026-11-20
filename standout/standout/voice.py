"""Brand voice: rewrite every follow-up email and text so it sounds like this business.

Inputs come from onboarding: the words on their website, their tone pick, words they use and
avoid, a real message they've sent a customer, and their sign-off. Claude writes a short voice
guide plus a rewrite of all four sequences, keeping each step's timing, channel and purpose.
Without an API key we apply the tone's greeting and the sign-off, which is honest but not a true rewrite.
"""
from __future__ import annotations

import json
import logging
import re

from .config import settings
from .funnels import FUNNELS, default_sequence
from .profile import complete

log = logging.getLogger("standout.voice")
MODEL = "claude-opus-5-5"
PLACEHOLDERS = {"first", "name", "business", "owner", "phone", "offer", "trade", "Trade", "city",
                "service_area", "guarantee", "difference", "review_link", "service"}
TONES = {
    "friendly": "Friendly neighbor: warm, casual, first names, a little personality.",
    "straight": "Straight shooter: short, direct, no fluff, gets to the point.",
    "professional": "Polished professional: courteous, clear, reassuring, no slang.",
}
GREETING = {"friendly": "Hey", "straight": "Hi", "professional": "Hello"}

SYSTEM = """You write follow-up texts and emails for a local service business, in the owner's own voice. \
These go to people who just asked for help on the business's website, so they must sound like a real \
person from that business, not a marketing department.

You'll get the business profile, evidence of how the brand sounds (website copy, the owner's tone \
choice, words they use and avoid, a real message they wrote) and the current sequences. Rewrite every \
step of every sequence:
- Keep the same number of steps, same channel, same order and the same job for each step (the \
timing is fixed; you only change words).
- Texts: under 300 characters, no subject. The FIRST text of each sequence must name the business.
- Emails: a subject line under 60 characters and a short body (under 120 words) with the sign-off.
- Use these fill-in fields exactly as written, with braces, wherever the real value belongs: \
{first} {owner} {business} {phone} {offer} {trade} {city} {guarantee} {review_link} {service}. \
Never invent prices, discounts, guarantees, licenses or facts that are not in the profile.
- Match the voice evidence: vocabulary, sentence length, warmth, formality. Avoid words they avoid.
Also write voice_guide: 3-5 short bullet lines describing this brand's voice, for future writers."""

STEP = {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
        "required": ["subject", "body"], "additionalProperties": False}
SCHEMA = {
    "type": "object",
    "properties": {
        "voice_guide": {"type": "string"},
        "sequences": {"type": "object",
                      "properties": {k: {"type": "array", "items": STEP} for k in FUNNELS},
                      "required": list(FUNNELS), "additionalProperties": False},
    },
    "required": ["voice_guide", "sequences"],
    "additionalProperties": False,
}


def _clean_placeholders(text: str) -> str:
    """Keep known {fields}; strip braces from anything else so sends never show raw braces."""
    return re.sub(r"\{([^{}]*)\}", lambda m: m.group(0) if m.group(1) in PLACEHOLDERS else m.group(1), text)


def evidence(profile: dict) -> str:
    p = profile
    parts = [f"Tone they picked: {TONES.get(p.get('tone', ''), 'not chosen')}"]
    from .reviews import confirmed
    promises = confirmed(p)
    if promises:
        parts.append("Promises that set them apart from local competitors (weave these in where they fit, "
                     "never mention competitors):\n" + "\n".join(f"- {d['promise']}" for d in promises))
    for key, label in (("voice_words", "Words/phrases they use"), ("voice_avoid", "Words they never use"),
                       ("signoff", "How they sign off"), ("voice_sample", "A real message they sent a customer"),
                       ("site_description", "Website description"), ("site_text", "Website copy (excerpt)")):
        if str(p.get(key, "")).strip():
            parts.append(f"{label}:\n{str(p[key]).strip()[:4000]}")
    return "\n\n".join(parts)


def basic_rewrite(seq: list[dict], profile: dict) -> list[dict]:
    """No-AI fallback: tone greeting + the owner's sign-off on emails."""
    hello = GREETING.get(profile.get("tone", ""), "Hi")
    signoff = profile.get("signoff", "").strip()
    out = []
    for s in seq:
        s = dict(s)
        s["body"] = re.sub(r"^(Hi|Hey|Hello)\b", hello, s["body"])
        if s["channel"] == "email" and signoff:
            paras = s["body"].rstrip().split("\n\n")
            if paras and paras[-1].lstrip().startswith("{owner}"):
                paras[-1] = f"{signoff}\n{paras[-1].strip()}"
            s["body"] = "\n\n".join(paras)
        out.append(s)
    return out


def rewrite(profile: dict, current: dict[str, list[dict]]) -> tuple[dict[str, list[dict]], str, bool]:
    """Returns (sequences by funnel key, voice guide, used_ai)."""
    current = {k: (current.get(k) or default_sequence(k)) for k in FUNNELS}
    fallback = {k: basic_rewrite(v, profile) for k, v in current.items()}
    if not settings.anthropic_api_key:
        return fallback, "", False
    import anthropic

    p = complete(profile)
    facts = "\n".join(f"- {k}: {p[k]}" for k in ("business", "owner", "trade", "city", "service_area", "services",
                                                  "ideal_customer", "offer", "guarantee", "difference", "years"))
    skeleton = {k: [{"channel": s["channel"], "when": s["delay"], "subject": s.get("subject", ""), "body": s["body"]}
                    for s in v] for k, v in current.items()}
    prompt = (f"Business profile:\n{facts}\n\nVoice evidence:\n{evidence(profile)}\n\n"
              f"Current sequences (\"when\" = seconds after the lead came in):\n{json.dumps(skeleton, indent=1)}")
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=2, timeout=600)
        with client.beta.messages.stream(
            model=MODEL, max_tokens=32000, system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        ) as stream:
            resp = stream.get_final_message()
        if resp.stop_reason != "end_turn":
            log.warning("voice rewrite stopped early: %s", resp.stop_reason)
            return fallback, "", False
        data = json.loads(next(b.text for b in resp.content if b.type == "text"))
    except anthropic.AuthenticationError:
        log.error("Anthropic API key was rejected")
        return fallback, "", False
    except (anthropic.APIError, StopIteration, ValueError) as e:
        log.warning("voice rewrite failed: %s", e)
        return fallback, "", False
    out = {}
    for key, seq in current.items():
        new = data["sequences"].get(key) or []
        if len(new) != len(seq):  # model changed the shape: keep this funnel's safe version
            out[key] = fallback[key]
            continue
        steps = []
        for old, n in zip(seq, new):
            body = _clean_placeholders(n.get("body", "").strip()) or old["body"]
            subject = _clean_placeholders(n.get("subject", "").strip()) if old["channel"] == "email" else ""
            steps.append({**old, "body": body[:1600], "subject": (subject or old.get("subject", ""))[:200]})
        out[key] = steps
    return out, data.get("voice_guide", "").strip()[:1500], True
