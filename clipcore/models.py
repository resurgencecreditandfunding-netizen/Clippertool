"""Data model.

Campaign     a permission-based clipping deal (rules + payout) — the unit users plug in
Source       a streamer whose content a campaign authorises
Destination  a posting account (YouTube / TikTok / Instagram) owned by the user
Session      one recorded or imported broadcast of a Source
Clip         one detected moment; rendered clips go through review → schedule → publish
Publication  a clip assigned to a destination at a time slot, with its view results
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Campaign(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    platforms: str = "youtube,tiktok"          # comma-separated
    payout_per_1k: float = 1.0                  # $ per 1,000 views
    budget_total: float = 0.0                   # 0 = unknown / uncapped
    budget_spent: float = 0.0
    min_seconds: int = 15
    max_seconds: int = 60
    required_tags: str = ""                     # space-separated, e.g. "#ad @streamer"
    notes: str = ""
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class Source(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    platform: str = "twitch"
    handle: str = Field(index=True)             # twitch login
    display_name: str = ""
    broadcaster_id: str = ""
    campaign_id: Optional[int] = Field(default=None, foreign_key="campaign.id")
    # Facecam location in the source frame as fractions "x,y,w,h"; blank = blur-fill layout.
    facecam_box: str = ""
    auto_record: bool = True
    active: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class Destination(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    platform: str = "youtube"                   # youtube | tiktok | instagram
    account_name: str
    campaign_ids: str = ""                      # comma-separated; blank = any campaign
    daily_cap: int = 4
    min_gap_minutes: int = 90
    active: bool = True


class Session(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="source.id")
    title: str = ""
    started_at: datetime = Field(default_factory=utcnow)
    ended_at: Optional[datetime] = None
    video_path: str = ""
    chat_path: str = ""
    viewer_clips_path: str = ""                 # optional JSON of viewer clip offsets (seconds)
    duration: float = 0.0
    status: str = "recording"                   # recording|recorded|processing|processed|failed
    error: str = ""


class Clip(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="session.id", index=True)
    source_id: int = Field(foreign_key="source.id")
    campaign_id: Optional[int] = Field(default=None, foreign_key="campaign.id")
    peak_t: float
    start_t: float
    end_t: float
    score: float = 0.0                          # 0-100
    features: str = "{}"                        # JSON: chat_z, audio_z, viewer_clips, hype, hook
    transcript: str = ""
    title: str = ""
    caption: str = ""
    layout: str = ""                            # stacked | blurfill
    path: str = ""
    thumb_path: str = ""
    # candidate (not rendered) | pending (awaiting review) | approved | rejected | failed
    status: str = Field(default="candidate", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    reviewed_at: Optional[datetime] = None

    @property
    def duration(self) -> float:
        return round(self.end_t - self.start_t, 2)


class Publication(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    clip_id: int = Field(foreign_key="clip.id", index=True)
    destination_id: int = Field(foreign_key="destination.id")
    scheduled_for: datetime
    status: str = "scheduled"                   # scheduled | posted | skipped
    url: str = ""
    views: int = 0
    posted_at: Optional[datetime] = None
