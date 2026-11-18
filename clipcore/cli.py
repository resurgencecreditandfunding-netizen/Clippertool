"""clipcore — command line entry point."""
from __future__ import annotations

import argparse
import logging


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clipcore", description="ClipCore — paste a stream link, get viral shorts")
    sub = p.add_subparsers(dest="cmd", required=True)

    sv = sub.add_parser("serve", help="run the web app")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8787)

    c = sub.add_parser("clip", help="process one link from the command line (for testing)")
    c.add_argument("url")
    c.add_argument("--count", type=int, default=0)

    r = sub.add_parser("reset-password", help="set a new password for an account")
    r.add_argument("email")
    r.add_argument("password", nargs="?", help="omit to be asked (keeps it out of shell history)")

    ca = sub.add_parser("create-admin", help="create the admin account (or reset it) — server only")
    ca.add_argument("email")
    ca.add_argument("--stdin", action="store_true", help="read the password from standard input")
    sub.add_parser("has-admin", help="exit 0 if an admin account exists, 1 if not")

    sub.add_parser("cleanup", help="delete projects older than the retention window")
    sub.add_parser("preload", help="download the caption model ahead of the first job")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    from .db import init_db, session_scope
    init_db()

    if args.cmd == "serve":
        import uvicorn
        uvicorn.run("clipcore.api.app:app", host=args.host, port=args.port, log_level="info",
                    proxy_headers=True, forwarded_allow_ips="127.0.0.1")
    elif args.cmd == "clip":
        from sqlmodel import select
        from .jobs import run_job
        from .models import Job, Short, User
        with session_scope() as db:
            u = db.exec(select(User)).first()
            if not u:
                print("Create an account in the web app first.")
                return 1
            j = Job(user_id=u.id, url=args.url, clip_count=args.count)
            db.add(j)
            db.commit()
            db.refresh(j)
        run_job(j.id)
        with session_scope() as db:
            j = db.get(Job, j.id)
            print(f"\n{j.status}: {j.title} ({j.duration / 3600:.1f}h) {j.error}")
            for s in db.exec(select(Short).where(Short.job_id == j.id).order_by(Short.rank)).all():
                print(f"  #{s.rank} score {s.score:>5.1f}  {s.start_t:>7.0f}s-{s.end_t:.0f}s  {s.title}  {s.path}")
    elif args.cmd == "reset-password":
        from sqlmodel import select
        from .auth import set_password
        from .models import User
        with session_scope() as db:
            u = db.exec(select(User).where(User.email == args.email.lower())).first()
        if not u:
            print("No account with that email.")
            return 1
        import getpass
        set_password(u.id, args.password or getpass.getpass("New password (8+ characters): "))
        print("Password updated.")
    elif args.cmd == "create-admin":
        import getpass
        import sys
        from .auth import AuthError, create_admin
        if args.stdin:
            pw = sys.stdin.readline().rstrip("\n")
        else:
            pw = getpass.getpass("Admin password (8+ characters): ")
            if pw != getpass.getpass("Type it again: "):
                print("Passwords don't match.")
                return 1
        try:
            u = create_admin(args.email, pw)
        except AuthError as e:
            print(e)
            return 1
        print(f"Admin account ready: {u.email}")
    elif args.cmd == "has-admin":
        from .auth import admin_exists
        return 0 if admin_exists() else 1
    elif args.cmd == "cleanup":
        from .maintenance import cleanup
        print(cleanup())
    elif args.cmd == "preload":
        from . import transcribe
        print("caption model ready" if transcribe.available() else "faster-whisper not installed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
