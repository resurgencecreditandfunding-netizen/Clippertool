import os
import sys
import tempfile
from pathlib import Path

os.environ["STANDOUT_DATA_DIR"] = tempfile.mkdtemp(prefix="standout-test-")
os.environ["STANDOUT_CONTACT_LIMIT"] = "5"
os.environ["STANDOUT_WORKER_SECONDS"] = "3600"
for k in ("ANTHROPIC_API_KEY", "SMTP_HOST", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "STANDOUT_ACCESS_CODE"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture(autouse=True)
def sync_followups(monkeypatch):
    """Run the instant follow-up send inline so tests are deterministic."""
    from standout import automation, main
    monkeypatch.setattr(main, "_kick_followups", automation.run_due)
