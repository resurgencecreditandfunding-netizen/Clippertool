"""Runtime settings.

Priority (highest first):
  1. values saved from the admin Settings page  (data/settings.json)
  2. environment variables / .env file
  3. built-in defaults
"""
from __future__ import annotations

import json
import os
from pathlib import Path

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
    "anthropic_api_key": ("ANTHROPIC_API_KEY", str, ""),         # optional AI clip judge
    "invite_code": ("CLIPCORE_INVITE_CODE", str, ""),          # blank = open sign-up
    "max_parallel_jobs": ("CLIPCORE_MAX_PARALLEL_JOBS", int, 2),
    "jobs_per_user": ("CLIPCORE_JOBS_PER_USER", int, 2),        # running/queued at once
    "retention_days": ("CLIPCORE_RETENTION_DAYS", int, 7),
    "max_vod_hours": ("CLIPCORE_MAX_VOD_HOURS", float, 14),
    "whisper_model": ("CLIPCORE_WHISPER_MODEL", str, "small"),
    "font": ("CLIPCORE_FONT", str, "Inter"),
}
SECRET_FIELDS = {"twitch_client_secret", "anthropic_api_key"}
LIMITS = {
    "max_parallel_jobs": (1, 16),
    "jobs_per_user": (1, 20),
    "retention_days": (1, 90),
    "max_vod_hours": (0.5, 48),
}


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.environ.get("CLIPCORE_DATA_DIR", ROOT / "data"))
        self.reload()

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
                raise ValueError(f"Invalid value for {name.replace('_', ' ')}")
            if name in LIMITS:
                lo, hi = LIMITS[name]
                if not lo <= value <= hi:
                    raise ValueError(f"{name.replace('_', ' ').capitalize()} must be between {lo} and {hi}")
            clean[name] = value
        merged = {**self._saved(), **clean}
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(merged, indent=2))
        try:
            self.settings_path.chmod(0o600)
        except OSError:
            pass
        self.reload()

    def public(self) -> dict:
        out = {name: getattr(self, name) for name in FIELDS if name not in SECRET_FIELDS}
        for name in SECRET_FIELDS:
            out[f"{name}_set"] = bool(getattr(self, name))
        return out

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'clipcore.db'}"

    @property
    def has_twitch(self) -> bool:
        return bool(self.twitch_client_id and self.twitch_client_secret)

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.jobs_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
