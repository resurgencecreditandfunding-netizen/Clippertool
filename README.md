# ClipCore

**Paste a stream. Get the viral clips.**

ClipCore turns a Twitch, Kick or YouTube VOD link into ranked, captioned, vertical Shorts
ready to download. Clippers sign up, paste a link, and get back the moments that went off,
cut to 9:16 with burned-in captions and a hook title.

## How it works

1. **Opens the link** and reads the video's details.
2. **Downloads only the audio** (~1 GB for a 12-hour stream, versus ~40 GB of video).
3. **Scores every second** of the stream:
   - audio spikes (screams, laughter, explosions), measured against the streamer's own baseline
   - **viewer-made Twitch clips** at their exact VOD position (official Twitch API, needs the
     admin's Twitch keys), the strongest viral signal there is
   - YouTube **"most replayed"** peaks
4. **Transcribes only the best candidates** and re-ranks them on hook strength and speech.
   Dead air gets demoted.
5. **Downloads video for just the winning sections**, then renders 1080×1920 Shorts with
   word-by-word captions and a hook title.

A 4-hour Twitch VOD typically finishes in a few minutes on a 4-CPU server.

## Put it on a server

See **[DEPLOY.md](DEPLOY.md)**: a plain-English, copy-paste guide. One command installs
everything on Ubuntu, including HTTPS when you have a domain.

## Admin

The first account created is the admin. The **Admin** page has:

- **Access code:** leave it blank for open sign-up, or set one to make the app invite-only.
  Sell access through your existing checkout and send buyers the code.
- **Twitch keys** (optional): turns on viewer-clip ranking for Twitch links.
- **Limits:** videos processing at once, videos per user, longest VOD, and how long finished
  clips are kept.
- **Users:** see usage, reset passwords, disable accounts.

## Run locally (developers)

Requires Python 3.11+ and ffmpeg.

```bash
pip install -e ".[dev]"
clipcore serve                         # http://127.0.0.1:8787
clipcore clip <VOD URL> --count 5      # process one link from the command line
pytest                                 # offline end-to-end tests
```

## Code map

| Path | What it does |
|---|---|
| `clipcore/sources.py` | Link resolving, parallel HLS segment downloader, section downloads, crowd signals |
| `clipcore/detect/` | Per-second signals and moment ranking |
| `clipcore/transcribe.py` | Word-level transcription of candidate windows only |
| `clipcore/render/` | 9:16 layout and caption rendering (ffmpeg + ASS) |
| `clipcore/jobs.py` | The link-to-clips pipeline and the job queue |
| `clipcore/api/app.py` | Web API: accounts, jobs, downloads, admin |
| `clipcore/web/` | Landing / sign-up page and the app |

The earlier campaign-based version (live recording, scheduling, analytics) is preserved on
the `archive/campaign-os` branch.
