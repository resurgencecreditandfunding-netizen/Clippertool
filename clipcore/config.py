"""Runtime settings, read from the environment (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
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


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.environ.get("CLIPCORE_DATA_DIR", ROOT / "data"))
    twitch_client_id: str = os.environ.get("TWITCH_CLIENT_ID", "")
    twitch_client_secret: str = os.environ.get("TWITCH_CLIENT_SECRET", "")
    game_name: str = os.environ.get("CLIPCORE_GAME", "Grand Theft Auto VI")
    whisper_model: str = os.environ.get("CLIPCORE_WHISPER_MODEL", "small")
    # How many clips to render per hour of stream (the rest stay as unrendered candidates).
    clips_per_hour: float = _f("CLIPCORE_CLIPS_PER_HOUR", 1.5)
    # Posting window, local server time (24h).
    post_window_start: int = int(os.environ.get("CLIPCORE_POST_START", 11))
    post_window_end: int = int(os.environ.get("CLIPCORE_POST_END", 23))
    font: str = os.environ.get("CLIPCORE_FONT", "Inter")

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
