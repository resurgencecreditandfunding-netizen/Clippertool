"""clipcore — command line entry point."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date

from sqlmodel import select


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clipcore", description="ClipCore OS — permission-based GTA 6 clipping engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the database")
    sv = sub.add_parser("serve", help="run the dashboard + API")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8787)
    sv.add_argument("--watch", action="store_true", help="also run the auto-record watcher")

    d = sub.add_parser("discover", help="list live streams of the game on Twitch")
    d.add_argument("--limit", type=int, default=20)
    d.add_argument("--language")

    a = sub.add_parser("add-source", help="add a streamer you have permission to clip")
    a.add_argument("handle")
    a.add_argument("--campaign", type=int, required=True, help="campaign id that authorises this streamer")
    a.add_argument("--facecam", default="", help='facecam box as fractions "x,y,w,h"')

    r = sub.add_parser("record", help="record one live source now (blocks until stream ends)")
    r.add_argument("source_id", type=int)

    sub.add_parser("watch", help="auto-record all active sources when they go live")

    im = sub.add_parser("import", help="import a VOD URL or local file as a session")
    im.add_argument("source_id", type=int)
    im.add_argument("path_or_url")
    im.add_argument("--chat", help="chat .jsonl aligned to the video (optional)")

    pr = sub.add_parser("process", help="detect + render clips for a session")
    pr.add_argument("session_id", type=int)
    pr.add_argument("--top", type=int)

    sc = sub.add_parser("schedule", help="route approved clips to accounts for a day")
    sc.add_argument("--date", default=date.today().isoformat())

    sub.add_parser("learn", help="re-weight detection from posted clip views")
    sub.add_parser("demo", help="generate a synthetic stream and run the full pipeline")

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    from .db import init_db, session_scope
    init_db()

    if args.cmd == "init":
        print("database ready")
    elif args.cmd == "serve":
        import threading

        import uvicorn

        if args.watch:
            from .watcher import watch
            threading.Thread(target=watch, daemon=True).start()
        uvicorn.run("clipcore.api.app:app", host=args.host, port=args.port, log_level="info")
    elif args.cmd == "discover":
        from .discovery.twitch import Twitch
        for s in Twitch().live_streams(args.limit, args.language):
            print(f"{s['viewer_count']:>7}  {s['user_login']:<24} {s['title'][:70]}")
    elif args.cmd == "add-source":
        from .discovery.twitch import Twitch
        from .models import Campaign, Source
        from .config import settings
        with session_scope() as db:
            if not db.get(Campaign, args.campaign):
                print(f"campaign {args.campaign} not found", file=sys.stderr)
                return 1
            src = Source(handle=args.handle.lower(), campaign_id=args.campaign, facecam_box=args.facecam)
            if settings.has_twitch:
                u = Twitch().users([src.handle]).get(src.handle)
                if u:
                    src.broadcaster_id, src.display_name = u["id"], u["display_name"]
            db.add(src)
            db.commit()
            db.refresh(src)
            print(f"source {src.id}: {src.handle}")
    elif args.cmd == "record":
        from .ingest.recorder import record_live
        from .pipeline import process_session
        sid = record_live(args.source_id)
        print("session", sid, "rendered", process_session(sid))
    elif args.cmd == "watch":
        from .watcher import watch
        watch()
    elif args.cmd == "import":
        from .ingest.recorder import import_media
        print("session", import_media(args.source_id, args.path_or_url, args.chat))
    elif args.cmd == "process":
        from .pipeline import process_session
        print("rendered clips:", process_session(args.session_id, args.top))
    elif args.cmd == "schedule":
        from .router import schedule_day
        pubs = schedule_day(date.fromisoformat(args.date))
        for pub in pubs:
            print(f"clip {pub.clip_id} → destination {pub.destination_id} at {pub.scheduled_for:%H:%M}")
        print(f"{len(pubs)} posts scheduled")
    elif args.cmd == "learn":
        from .learn import learn
        print(json.dumps(learn(), indent=2))
    elif args.cmd == "demo":
        from .demo import PLANTED, seed_demo
        from .models import Clip
        from .pipeline import process_session
        sid = seed_demo()
        process_session(sid)
        with session_scope() as db:
            clips = db.exec(select(Clip).where(Clip.session_id == sid).order_by(Clip.score.desc())).all()
        print(f"\nplanted moments: {[t for t, _ in PLANTED]}")
        for c in clips:
            print(f"  clip {c.id}: peak {c.peak_t:>5.0f}s  {c.start_t:.0f}-{c.end_t:.0f}s  score {c.score:>5.1f}  {c.status}")
        print("\nrun `clipcore serve` and open http://127.0.0.1:8787 to review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
