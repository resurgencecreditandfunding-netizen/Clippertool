# ClipCore OS

**Plug in clipping campaigns. Get a ranked daily slate of compliant Shorts, routed to your accounts.**

ClipCore OS is a permission-based clipping engine for GTA 6 streams. It records the streamers
whose campaigns you're in, finds the moments chat and audio say were big, renders 9:16 Shorts
with hook titles and captions, checks every clip against its campaign's rules, and routes the
approved ones across your posting accounts. Logged view counts feed back into the detection
model, so it learns what actually gets views on *your* accounts.

```
INPUTS (campaigns → streamers)     ENGINE                              OUTPUTS (your accounts)
Campaign A ─ streamer 1 ─┐                                            ┌─ YouTube Shorts · acct 1
            streamer 2 ─┤   record → detect → rank → render →        ├─ TikTok · acct 1
Campaign B ─ streamer 3 ─┼─► compliance gate → swipe review → route ─►├─ YouTube Shorts · acct 2
            streamer 4 ─┘                     ▲                       └─ day pack (.zip)
                                   views ─── learn ◄──────────────────────── results
```

## Put it on a server (recommended)

**See [DEPLOY.md](DEPLOY.md):** a plain-English, copy-paste guide. One command installs
everything on an Ubuntu server. You then set your password and Twitch keys in the dashboard's
**Settings** page. No files to edit.

## Run it locally (developers)

Requires Python 3.11+ and ffmpeg.

```bash
pip install -e .
clipcore demo                 # synthetic stream → full pipeline, no Twitch needed
clipcore serve --watch        # dashboard at http://127.0.0.1:8787 + auto-recording
```

On first visit you create a dashboard password, then add your Twitch keys under **Settings**.
Environment variables in `.env` (see `.env.example`) still work as defaults; values saved in
Settings take priority.

Optional captions (word-level, burned in): `pip install -e ".[captions]"`, or install on the
server with `WITH_CAPTIONS=1`.

## Daily workflow

1. **Campaigns:** add each clipping campaign you've joined: payout per 1k views, budget,
   allowed platforms, length limits and required tags.
2. **Sources:** add the streamers those campaigns cover. *Discover* lists the top live GTA 6
   streams for scouting. A source can't be saved without a campaign.
3. **Accounts:** add your posting accounts, with a daily cap and posting gap for each.
4. **Leave `serve --watch` running.** Sources are recorded when they go live, with chat, and
   processed when the stream ends.
5. **Review:** swipe through clips ranked by score. `A` approves, `X` rejects, `←`/`→` browse,
   `E` edits the hook title. Trim and layout buttons re-render the clip.
6. **Schedule:** *Route approved clips*, then *Download day pack*: a folder per account with
   each mp4 and its caption. Paste post links and log views as they come in.
7. **Analytics:** earnings by campaign, streamer and account, plus budget burn. Once 20 posts
   have views, click *Learn* to re-weight detection toward what actually performs.

## How detection works

Each signal is a per-second track, scored against the streamer's **own rolling baseline**
(robust z-score). A big streamer's always-busy chat and a small streamer's quiet chat are
judged on the same terms.

| Signal | Why it matters |
|---|---|
| Chat velocity (shifted ~5s back for reaction lag) | The strongest "something just happened" signal |
| Hype-weighted chat (KEKW, NO WAY, "clip it"…) | Separates hype from busy small talk |
| Audio loudness | Screams, laughs, explosions |
| Viewer-made Twitch clips | Viewers already voted with a clip button |
| Hook score (with captions on) | Does the clip open on something quotable? |

Signals under 1σ count for nothing, and the rest are log-compressed and weighted. Peaks are
picked with 45s separation, and windows get ~18s of build-up and a payoff that extends while
the moment is still elevated. Only the top ~1.5 moments per stream hour are rendered. The
rest wait as candidates (*Render next 5*), so compute goes where the views are.

## Layouts

- **stacked:** facecam on top (40%), gameplay below. Set the source's facecam box as
  fractions `x,y,w,h`. For example, a cam in the top-right quarter is `0.72,0.04,0.25,0.30`.
- **blur-fill:** zoomed gameplay over a blurred fill. Works on any stream with no setup.

## CLI

```
clipcore demo                          synthetic end-to-end run
clipcore serve [--watch]               dashboard + API (+ auto-record)
clipcore discover [--limit 20]         top live streams for CLIPCORE_GAME
clipcore add-source HANDLE --campaign ID [--facecam x,y,w,h]
clipcore watch                         auto-record only (headless)
clipcore record SOURCE_ID              record one live stream now
clipcore import SOURCE_ID URL|FILE     VOD or file → session (+ processing via UI)
clipcore process SESSION_ID [--top N]  detect + render
clipcore schedule [--date YYYY-MM-DD]  route approved clips
clipcore learn                         re-fit detection weights from views
```

## Compute and storage

Recording is cheap. Rendering a 30s Short takes a few seconds of CPU. Transcription only runs
on the clip windows, never the full VOD. A 1080p60 Twitch stream is ~3–6 GB per hour on disk.
**Delete recordings once a session is processed and reviewed**, because 8 streamers × 6 hours
is roughly 150–300 GB a day.

## Permission model

Every source must belong to a campaign, and every clip is checked against its campaign (length,
required tags, platform, budget) before it can be routed. Captions always credit the streamer.
Discovery is for scouting only: add a streamer once you're in their campaign or have their
written OK.

## Roadmap

- Direct posting via the YouTube Data API and TikTok Content Posting API (both require app
  audits; the day pack covers posting until then)
- Automatic view-count sync from the platform APIs
- LLM-written hook titles and description variants per platform
- Rolling (in-stream) processing so clips land while the streamer is still live
- Kick and YouTube Live sources
- Multi-user accounts and billing (Starter / Pro / Agency source slots)
