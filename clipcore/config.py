"""Runtime settings.

Priority (highest first):
  1. values saved from the dashboard's Settings page  (data/settings.json)
  2. environment variables / .env file
  3. built-in defaults
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

# name -> (env var, type, default)
FIELDS: dict[str, tuple[str, type, object]] = {
    "twitch_client_id": ("TWITCH_CLIENT_ID", str, ""),
    "twitch_client_secret": ("TWITCH_CLIENT_SECRET", str, ""),
    "game_name": ("CLIPCORE_GAME", str, "Grand Theft Auto VI"),
    "timezone": ("CLIPCORE_TIMEZONE", str, "America/New_York"),
    "post_window_start": ("CLIPCORE_POST_START", int, 11),
    "post_window_end": ("CLIPCORE_POST_END", int, 23),
    "clips_per_hour": ("CLIPCORE_CLIPS_PER_HOUR", float, 1.5),
    "retention_days": ("CLIPCORE_RETENTION_DAYS", int, 2),
    "record_quality": ("CLIPCORE_RECORD_QUALITY", str, "1080p60,1080p,best"),
    "min_free_gb": ("CLIPCORE_MIN_FREE_GB", int, 15),
    "whisper_model": ("CLIPCORE_WHISPER_MODEL", str, "small"),
    "font": ("CLIPCORE_FONT", str, "Inter"),
}
SECRET_FIELDS = {"twitch_client_secret"}
# streamlink stream preference lists (first available wins)
QUALITY_OPTIONS = {
    "1080p60,1080p,best": "1080p: sharpest Shorts, about 3 GB per stream hour",
    "720p60,720p,best": "720p: a bit softer, about 1.7 GB per stream hour",
}


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.environ.get("CLIPCORE_DATA_DIR", ROOT / "data"))
        self.reload()

    # ------------------------------------------------------------------ load/save
    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    def _saved(self) -> dict:
        try:
            return json.loads(self.settings_path.read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def reload(self) -> None:
        saved = self._saved()
        for name, (env, typ, default) in FIELDS.items():
            raw = saved.get(name, os.environ.get(env, default))
            try:
                value = typ(raw)
            except (TypeError, ValueError):
                value = default
            setattr(self, name, value)

    def save(self, updates: dict) -> None:
        """Validate and persist values from the Settings page."""
        clean: dict = {}
        for name, value in updates.items():
            if name not in FIELDS:
                continue
            typ = FIELDS[name][1]
            if isinstance(value, str):
                value = value.strip()
            if name in SECRET_FIELDS and not value:
                continue                      # blank = keep the existing secret
            try:
                value = typ(value)
            except (TypeError, ValueError):
                raise ValueError(f"Invalid value for {name}")
            if name == "timezone":
                try:
                    ZoneInfo(value)
                except (ZoneInfoNotFoundError, ValueError):
                    raise ValueError(f"Unknown timezone: {value}")
            if name in ("post_window_start", "post_window_end") and not 0 <= value <= 23:
                raise ValueError("Posting hours must be between 0 and 23")
            if name == "retention_days" and value < 1:
                raise ValueError("Keep recordings for at least 1 day")
            if name == "record_quality" and value not in QUALITY_OPTIONS:
                raise ValueError("Unknown recording quality")
            if name == "clips_per_hour" and not 0.1 <= value <= 20:
                raise ValueError("Clips per hour must be between 0.1 and 20")
            clean[name] = value
        merged = {**self._saved(), **clean}
        if merged.get("post_window_start", self.post_window_start) >= merged.get("post_window_end", self.post_window_end):
            raise ValueError("Posting window must start before it ends")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(merged, indent=2))
        try:
            self.settings_path.chmod(0o600)
        except OSError:
            pass
        self.reload()

    def public(self) -> dict:
        """Settings for the UI — secrets are never sent back, only whether they're set."""
        out = {name: getattr(self, name) for name in FIELDS if name not in SECRET_FIELDS}
        for name in SECRET_FIELDS:
            out[f"{name}_set"] = bool(getattr(self, name))
        return out

    # ------------------------------------------------------------------ derived
    @property
    def tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            return ZoneInfo("UTC")

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'clipcore.db'}"

    @property
    def weights_path(self) -> Path:
        return self.data_dir / "weights.json"

    @property
    def has_twitch(self) -> bool:
        return bool(self.twitch_client_id and self.twitch_client_secret)

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.recordings_dir, self.clips_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
