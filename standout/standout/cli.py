"""Server-side commands. The admin account can only be created here, never from the website.

    python -m standout.cli create-admin you@yourbrand.com
"""
from __future__ import annotations

import getpass
import sys

from . import auth
from .db import db, init_db


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    init_db()
    if len(argv) == 2 and argv[0] == "create-admin":
        email = argv[1]
        pw = getpass.getpass("Admin password (8+ characters): ")
        with db() as conn:
            existing = conn.execute("SELECT id FROM accounts WHERE email = ?", (email.strip().lower(),)).fetchone()
            if existing:
                conn.execute("UPDATE accounts SET is_admin = 1 WHERE id = ?", (existing["id"],))
        if existing:
            auth.set_password(existing["id"], pw)
            print(f"{email} is now an admin.")
        else:
            auth.create_account(email, "Admin", pw, admin=True)
            print(f"Admin {email} created. Log in, then open /admin")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
