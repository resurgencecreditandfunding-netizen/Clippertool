"""Single-owner password login for the dashboard.

The first visit asks you to create a password. After that every page and API call needs a
signed session cookie. Stored in data/auth.json as a salted PBKDF2 hash — never plaintext.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

from .config import settings

SESSION_DAYS = 30
COOKIE = "clipcore_session"
_ITER = 300_000


def _path():
    return settings.data_dir / "auth.json"


def _load() -> dict:
    try:
        return json.loads(_path().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _write(data: dict) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(data))
    try:
        _path().chmod(0o600)
    except OSError:
        pass


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITER).hex()


def is_setup() -> bool:
    return "hash" in _load()


def set_password(password: str) -> None:
    if len(password) < 10:
        raise ValueError("Use at least 10 characters.")
    salt = secrets.token_hex(16)
    data = _load()
    # A new password also rotates the signing key, which logs out every old session.
    data.update(salt=salt, hash=_hash(password, salt), key=secrets.token_hex(32))
    _write(data)


def check_password(password: str) -> bool:
    data = _load()
    if "hash" not in data:
        return False
    return hmac.compare_digest(_hash(password, data["salt"]), data["hash"])


def make_token() -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    sig = hmac.new(bytes.fromhex(_load()["key"]), str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def valid_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    data = _load()
    if "key" not in data:
        return False
    exp, _, sig = token.partition(".")
    if not exp.isdigit() or int(exp) < time.time():
        return False
    good = hmac.new(bytes.fromhex(data["key"]), exp.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, sig)
