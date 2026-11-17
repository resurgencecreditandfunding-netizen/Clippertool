"""Campaign compliance: every clip that leaves the system meets the deal's rules."""
from __future__ import annotations

from .models import Campaign, Clip, Source

BASE_TAGS = "#GTA6 #GTAVI #shorts"


def default_title(source: Source, features: dict, quote: str = "") -> str:
    name = source.display_name or source.handle
    if quote:
        return f'{name}: "{quote}"'
    if features.get("viewer_clips", 0) >= 1.5:
        return f"Chat could NOT believe what {name} just did"
    if features.get("audio_z", 0) >= 4:
        return f"{name} completely lost it 💀"
    if features.get("hype_z", 0) >= 4:
        return f"{name} pulled off the impossible in GTA 6"
    return f"{name} in GTA 6 is unreal"


def build_caption(title: str, source: Source, campaign: Campaign | None) -> str:
    credit = f"🎥 twitch.tv/{source.handle}"
    tags = BASE_TAGS
    if campaign and campaign.required_tags.strip():
        tags = f"{campaign.required_tags.strip()} {tags}"
    return f"{title}\n\n{credit}\n{tags}"


def check(clip: Clip, campaign: Campaign | None) -> list[str]:
    issues: list[str] = []
    if campaign is None:
        issues.append("No campaign attached — you have no posting permission on record for this streamer.")
        return issues
    if not campaign.active:
        issues.append(f"Campaign '{campaign.name}' is inactive.")
    if clip.duration < campaign.min_seconds:
        issues.append(f"Too short: {clip.duration:.0f}s < {campaign.min_seconds}s minimum.")
    if clip.duration > campaign.max_seconds:
        issues.append(f"Too long: {clip.duration:.0f}s > {campaign.max_seconds}s maximum.")
    for tag in campaign.required_tags.split():
        if tag.lower() not in clip.caption.lower():
            issues.append(f"Caption missing required tag {tag}.")
    if campaign.budget_total and campaign.budget_spent >= campaign.budget_total:
        issues.append("Campaign budget is exhausted — views will not pay.")
    return issues
