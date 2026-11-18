"""AI clip judge: reads candidate transcripts like a clipper would, rates virality, writes hooks.

Optional — enabled when an Anthropic API key is saved in Admin. Without it, ranking falls back
to signals + phrase heuristics. Cost is roughly a few cents per video.
"""
from __future__ import annotations

import json
import logging

from .config import settings

log = logging.getLogger("clipcore.llm")
MODEL = "claude-opus-5-5"

SYSTEM = """You are the best short-form clipper in the streaming scene. You pick which moments \
from a livestream will go viral as TikToks / YouTube Shorts, and you write the hook titles.

You'll get candidate moments from one stream: each is a ~30-50 second window with its \
transcript and how strongly the audio/crowd reacted. Judge each one like a clipper scrolling \
the VOD:

- Does it stand on its own? A viewer with zero context must get it.
- Is there a payoff: a reaction, punchline, rage moment, scare, fail, clutch play, or a wild line?
- Is it funny, shocking, relatable, or quotable enough that people comment or share?
- Does it open strong (the first 3 seconds decide the scroll)?
- Penalize: dead air, mid-sentence rambling, reading chat, ads/sponsor reads, music only, \
setup with no payoff, or transcripts that are mostly noise.

Score each 1-10 (10 = would clearly go viral, 5 = decent filler, 1 = skip). Be harsh and use \
the full range; most windows of a stream are not viral.

For each, write a hook title in the style of top streamer clip pages: max 9 words, curiosity \
or reaction driven, can quote the streamer, at most one emoji, no hashtags, no clickbait lies \
about what happens. Use the streamer's name only if it helps."""

SCHEMA = {
    "type": "object",
    "properties": {
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "score": {"type": "integer"},
                    "title": {"type": "string"},
                },
                "required": ["id", "score", "title"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["moments"],
    "additionalProperties": False,
}


def available() -> bool:
    return bool(settings.anthropic_api_key)


def judge(channel: str, video_title: str, candidates: list[dict]) -> dict[int, dict]:
    """candidates: [{id, start, transcript, signal}] → {id: {score 1-10, title}}. Empty on failure."""
    if not available() or not candidates:
        return {}
    import anthropic

    lines = [f"Streamer: {channel or 'unknown'}", f"Stream title: {video_title}", ""]
    for c in candidates:
        mins, secs = divmod(int(c["start"]), 60)
        text = c["transcript"].strip() or "(no speech detected)"
        lines.append(f"<moment id=\"{c['id']}\" at=\"{mins // 60}:{mins % 60:02d}:{secs:02d}\" "
                     f"reaction_strength=\"{c['signal']:.0f}/100\">\n{text}\n</moment>")
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=3, timeout=180)
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": "\n".join(lines)}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if resp.stop_reason != "end_turn":
            log.warning("clip judge stopped early: %s", resp.stop_reason)
            return {}
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)
    except anthropic.AuthenticationError:
        log.error("Anthropic API key was rejected")
        return {}
    except anthropic.RateLimitError:
        log.warning("Anthropic rate limit hit; ranking without AI for this video")
        return {}
    except anthropic.APIStatusError as e:
        log.warning("clip judge API error %s: %s", e.status_code, e.message)
        return {}
    except (anthropic.APIConnectionError, StopIteration, ValueError) as e:
        log.warning("clip judge failed: %s", e)
        return {}
    out = {}
    for m in data.get("moments", []):
        title = " ".join(str(m.get("title", "")).split())[:90]
        out[int(m["id"])] = {"score": max(1, min(10, int(m.get("score", 1)))), "title": title}
    return out


def test_key() -> str:
    """Raises with a readable message if the key doesn't work."""
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=1, timeout=60)
    try:
        client.messages.create(model=MODEL, max_tokens=64, output_config={"effort": "low"},
                               messages=[{"role": "user", "content": "Reply with OK."}])
    except anthropic.AuthenticationError:
        raise ValueError("Anthropic rejected this API key. Copy it again from console.anthropic.com.")
    except anthropic.PermissionDeniedError:
        raise ValueError("This API key doesn't have access. Check billing at console.anthropic.com.")
    except anthropic.APIConnectionError:
        raise ValueError("Couldn't reach Anthropic from this server.")
    return "Connected. AI ranking and titles are on."
