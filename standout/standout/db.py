"""SQLite storage. One file, no server to run. Every row is scoped to an account."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  pw_salt TEXT NOT NULL,
  pw_hash TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  profile TEXT NOT NULL DEFAULT '{}',
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS funnels (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  overrides TEXT NOT NULL DEFAULT '{}',
  sequence TEXT NOT NULL DEFAULT '[]',
  views INTEGER NOT NULL DEFAULT 0,
  UNIQUE(account_id, key)
);
CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  name TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'manual',
  stage TEXT NOT NULL DEFAULT 'new',
  details TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  value REAL NOT NULL DEFAULT 0,
  seq_step INTEGER NOT NULL DEFAULT 0,
  seq_next REAL,
  seq_status TEXT NOT NULL DEFAULT 'none',
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS contacts_acct ON contacts(account_id, stage);
CREATE INDEX IF NOT EXISTS contacts_due ON contacts(seq_status, seq_next);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
  direction TEXT NOT NULL,
  channel TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  time TEXT NOT NULL DEFAULT '09:00',
  pillar TEXT NOT NULL,
  format TEXT NOT NULL,
  title TEXT NOT NULL,
  hook TEXT NOT NULL,
  script TEXT NOT NULL,
  cta TEXT NOT NULL,
  caption TEXT NOT NULL,
  funnel TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'planned',
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS posts_acct ON posts(account_id, day);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  funnel TEXT NOT NULL,
  kind TEXT NOT NULL,
  day TEXT NOT NULL,
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_acct ON events(account_id, day);
CREATE TABLE IF NOT EXISTS capi_log (
  id INTEGER PRIMARY KEY,
  account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  event TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS capi_acct ON capi_log(account_id, created);
"""

# Columns added after the first release. Applied once, safely, on startup.
MIGRATIONS = [
    "ALTER TABLE accounts ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE accounts ADD COLUMN contact_limit INTEGER",
    "ALTER TABLE accounts ADD COLUMN funnel_domain TEXT",
    "ALTER TABLE accounts ADD COLUMN onboarded INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE accounts ADD COLUMN last_seen REAL",
    "ALTER TABLE contacts ADD COLUMN tracking TEXT NOT NULL DEFAULT '{}'",
    "CREATE UNIQUE INDEX IF NOT EXISTS accounts_domain ON accounts(funnel_domain)",
    "ALTER TABLE accounts ADD COLUMN plan_status TEXT NOT NULL DEFAULT 'active'",
    "ALTER TABLE accounts ADD COLUMN setup_paid INTEGER NOT NULL DEFAULT 0",
]

_lock = threading.Lock()
_ready = False


def connect() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    global _ready
    with _lock:
        if _ready:
            return
        conn = connect()
        conn.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e):
                    raise
        conn.commit()
        conn.close()
        _ready = True


@contextmanager
def db():
    init_db()
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row(r: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for f in json_fields:
        if f in d and isinstance(d[f], str):
            d[f] = json.loads(d[f] or "null")
    return d


def now() -> float:
    return time.time()
