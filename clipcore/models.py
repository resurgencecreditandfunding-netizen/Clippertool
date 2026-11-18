"""Data model.

User   a clipper with a login
Job    one pasted link being turned into clips
Short  one finished vertical clip from a job
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    pw_salt: str
    pw_hash: str
    is_admin: bool = False
    disabled: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    url: str
    clip_count: int = 0                         # 0 = automatic
    title: str = ""
    channel: str = ""
    platform: str = ""
    thumbnail: str = ""
    duration: float = 0.0
    # queued | running | done | failed
    status: str = Field(default="queued", index=True)
    stage: str = "Waiting in line"
    progress: float = 0.0                       # 0-100
    error: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: Optional[datetime] = None


class Short(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    rank: int = 0
    start_t: float
    end_t: float
    score: float = 0.0                          # 0-100 "viral score"
    title: str = ""
    transcript: str = ""
    features: str = "{}"
    path: str = ""
    thumb_path: str = ""
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def duration(self) -> float:
        return round(self.end_t - self.start_t, 1)
