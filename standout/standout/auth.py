"""Email + password accounts with signed session cookies (stdlib only)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from .config import settings
from .db import db, now

COOKIE = "standout_session"
SESSION_DAYS = 30
_ITER = 200_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(ValueError):
    pass


def _key() -> bytes:
    path = settings.data_dir / "secret.key"
    try:
        return bytes.fromhex(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        key = secrets.token_hex(32)
        path.write_text(key)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return bytes.fromhex(key)


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITER).hex()


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40] or "biz"


def unique_slug(conn, base: str) -> str:
    base = _slugify(base)
    slug, n = base, 1
    while conn.execute("SELECT 1 FROM accounts WHERE slug = ?", (slug,)).fetchone():
        n += 1
        slug = f"{base}-{n}"
    return slug


def signup(email: str, password: str, business: str, code: str = "") -> int:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    if len(password) < 8:
        raise AuthError("Password must be at least 8 characters.")
    if not business.strip():
        raise AuthError("Enter your business name.")
    if settings.invite_code and not hmac.compare_digest(code.strip(), settings.invite_code):
        raise AuthError("That access code isn't valid.")
    salt = secrets.token_hex(16)
    with db() as conn:
        if conn.execute("SELECT 1 FROM accounts WHERE email = ?", (email,)).fetchone():
            raise AuthError("An account with that email already exists. Log in instead.")
        profile = json.dumps({"business": business.strip(), "owner_email": email})
        cur = conn.execute(
            "INSERT INTO accounts (email, pw_salt, pw_hash, slug, profile, created) VALUES (?,?,?,?,?,?)",
            (email, salt, _hash(password, salt), unique_slug(conn, business), profile, now()),
        )
        return cur.lastrowid


def login(email: str, password: str) -> int:
    with db() as conn:
        r = conn.execute("SELECT id, pw_salt, pw_hash FROM accounts WHERE email = ?",
                         (email.strip().lower(),)).fetchone()
    if not r or not hmac.compare_digest(_hash(password, r["pw_salt"]), r["pw_hash"]):
        raise AuthError("Wrong email or password.")
    return r["id"]


def make_token(account_id: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(
        {"a": account_id, "exp": int(time.time()) + SESSION_DAYS * 86400}).encode()).decode()
    sig = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def account_from_token(token: str | None) -> int | None:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    good = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except ValueError:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return int(data["a"])
