"""Accounts: email + password, signed session cookies.

Web sign-ups are always regular users. The admin account is created only on the server itself
(`clipcore create-admin`, which the installer runs for you), so nobody can claim admin from the
website. If an access code is set in Admin, sign-up requires it — sell access through your checkout and hand out the code.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time

from sqlmodel import func, select

from .config import settings
from .db import session_scope
from .models import User

SESSION_DAYS = 30
COOKIE = "clipcore_session"
_ITER = 300_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(ValueError):
    pass


# ------------------------------------------------------------------ signing key
def _key() -> bytes:
    path = settings.data_dir / "auth.json"
    try:
        return bytes.fromhex(json.loads(path.read_text())["key"])
    except (FileNotFoundError, ValueError, KeyError):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        key = secrets.token_hex(32)
        path.write_text(json.dumps({"key": key}))
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return bytes.fromhex(key)


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITER).hex()


def _check_password_rules(password: str) -> None:
    if len(password) < 8:
        raise AuthError("Password must be at least 8 characters.")


# ------------------------------------------------------------------ accounts
def user_count() -> int:
    with session_scope() as db:
        return db.exec(select(func.count(User.id))).one()


def signup(email: str, password: str, invite: str = "") -> User:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    _check_password_rules(password)
    if settings.invite_code and not hmac.compare_digest(invite.strip(), settings.invite_code):
        raise AuthError("That access code isn't valid.")
    with session_scope() as db:
        if db.exec(select(User).where(User.email == email)).first():
            raise AuthError("An account with that email already exists. Log in instead.")
        salt = secrets.token_hex(16)
        user = User(email=email, pw_salt=salt, pw_hash=_hash(password, salt), is_admin=False)
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def create_admin(email: str, password: str) -> User:
    """Server-side only. Creates the admin, or promotes an existing account and resets its password."""
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise AuthError("Enter a valid email address.")
    _check_password_rules(password)
    with session_scope() as db:
        user = db.exec(select(User).where(User.email == email)).first()
        salt = secrets.token_hex(16)
        if user is None:
            user = User(email=email, pw_salt=salt, pw_hash=_hash(password, salt), is_admin=True)
        else:
            user.pw_salt, user.pw_hash, user.is_admin, user.disabled = salt, _hash(password, salt), True, False
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def admin_exists() -> bool:
    with session_scope() as db:
        return db.exec(select(User).where(User.is_admin == True)).first() is not None  # noqa: E712


def login(email: str, password: str) -> User:
    email = email.strip().lower()
    with session_scope() as db:
        user = db.exec(select(User).where(User.email == email)).first()
    if not user or not hmac.compare_digest(_hash(password, user.pw_salt), user.pw_hash):
        raise AuthError("Wrong email or password.")
    if user.disabled:
        raise AuthError("This account has been disabled.")
    return user


def set_password(user_id: int, password: str) -> None:
    _check_password_rules(password)
    with session_scope() as db:
        user = db.get(User, user_id)
        user.pw_salt = secrets.token_hex(16)
        user.pw_hash = _hash(password, user.pw_salt)
        db.add(user)
        db.commit()


# ------------------------------------------------------------------ sessions
def make_token(user: User) -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    # Tie the token to the password hash, so changing a password logs out old sessions.
    payload = f"{user.id}.{exp}"
    sig = hmac.new(_key(), f"{payload}.{user.pw_hash[:16]}".encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def user_from_token(token: str | None) -> User | None:
    if not token or token.count(".") != 2:
        return None
    uid, exp, sig = token.split(".")
    if not (uid.isdigit() and exp.isdigit()) or int(exp) < time.time():
        return None
    with session_scope() as db:
        user = db.get(User, int(uid))
    if not user or user.disabled:
        return None
    good = hmac.new(_key(), f"{uid}.{exp}.{user.pw_hash[:16]}".encode(), hashlib.sha256).hexdigest()
    return user if hmac.compare_digest(good, sig) else None
