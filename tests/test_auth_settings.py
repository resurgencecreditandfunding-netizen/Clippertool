import time

import pytest

from clipcore import auth
from clipcore.config import settings


def test_password_flow():
    (settings.data_dir / "auth.json").unlink(missing_ok=True)
    assert not auth.is_setup()
    with pytest.raises(ValueError):
        auth.set_password("short")
    auth.set_password("a-long-password")
    assert auth.is_setup()
    assert auth.check_password("a-long-password")
    assert not auth.check_password("wrong-password")
    assert "a-long-password" not in (settings.data_dir / "auth.json").read_text()


def test_session_tokens():
    auth.set_password("a-long-password")
    tok = auth.make_token()
    assert auth.valid_token(tok)
    exp, sig = tok.split(".")
    assert not auth.valid_token(f"{int(exp) + 1}.{sig}")          # tampered expiry
    assert not auth.valid_token(f"{int(time.time()) - 5}.{sig}")  # expired
    assert not auth.valid_token("garbage") and not auth.valid_token(None)
    auth.set_password("another-password")                         # new password logs everyone out
    assert not auth.valid_token(tok)


def test_settings_save_and_validation():
    settings.save({"twitch_client_id": " abc ", "twitch_client_secret": "s3cret", "timezone": "America/Chicago"})
    assert settings.twitch_client_id == "abc" and settings.has_twitch
    settings.save({"twitch_client_secret": ""})                   # blank keeps the saved secret
    assert settings.twitch_client_secret == "s3cret"
    pub = settings.public()
    assert "twitch_client_secret" not in pub and pub["twitch_client_secret_set"] is True
    for bad in ({"timezone": "Mars/Base"}, {"post_window_start": 22, "post_window_end": 9},
                {"retention_days": 0}, {"record_quality": "8k"}):
        with pytest.raises(ValueError):
            settings.save(bad)
    assert settings.timezone == "America/Chicago"
    settings.settings_path.unlink()
    settings.reload()
