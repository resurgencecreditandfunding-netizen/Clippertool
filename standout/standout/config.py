"""Settings from environment variables. Everything optional has a safe default so the demo runs bare."""
from __future__ import annotations

import os
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.data_dir = Path(os.environ.get("STANDOUT_DATA_DIR", "data")).resolve()
        self.base_url = os.environ.get("STANDOUT_BASE_URL", "http://localhost:8000").rstrip("/")
        self.invite_code = os.environ.get("STANDOUT_ACCESS_CODE", "")
        self.contact_limit = int(os.environ.get("STANDOUT_CONTACT_LIMIT", "500"))
        # AI scripts (optional; built-in templates are used without it)
        self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        # Email (optional; without it messages are logged to the outbox as "simulated")
        self.smtp_host = os.environ.get("SMTP_HOST", "")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self.smtp_user = os.environ.get("SMTP_USER", "")
        self.smtp_password = os.environ.get("SMTP_PASSWORD", "")
        self.smtp_from = os.environ.get("SMTP_FROM", "")
        # SMS (optional) - one platform Twilio account; each business gets its own number later
        self.twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        self.twilio_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        self.twilio_from = os.environ.get("TWILIO_FROM_NUMBER", "")
        # Meta (Facebook) Graph API version for Conversions API + Page posting. Bump yearly.
        self.meta_api_version = os.environ.get("META_API_VERSION", "v26.0")
        # Hostname customers point their funnel subdomain (CNAME) at, e.g. funnels.yourbrand.com
        self.cname_target = os.environ.get("STANDOUT_CNAME_TARGET", "")
        # Competitor review lookups (SerpApi). Without it, owners can paste reviews by hand.
        self.serpapi_key = os.environ.get("SERPAPI_KEY", "")
        # Your community / premium support (e.g. your Skool group). Shown to active customers.
        self.community_url = os.environ.get("STANDOUT_COMMUNITY_URL", "")
        self.worker_interval = float(os.environ.get("STANDOUT_WORKER_SECONDS", "30"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "standout.db"


settings = Settings()
