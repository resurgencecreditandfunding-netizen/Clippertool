"""Turn a pasted link into media we can analyse and cut.

Strategy (why it's fast): a 12-hour VOD is ~40 GB at 1080p but ~1 GB as audio. We download
only the audio to find moments, then download video for just the winning sections.

HLS sources (Twitch, Kick, most streaming sites) are fetched segment-by-segment in parallel
by our own downloader. Everything else falls back to yt-dlp.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

import httpx
import yt_dlp

from .config import settings

log = logging.getLogger("clipcore.sources")
logging.getLogger("httpx").setLevel(logging.WARNING)
Progress = Callable[[float], None]
UA = "Mozilla/5.0 (compatible; ClipCore/1.0)"


class SourceError(RuntimeError):
    """A problem with the link the user should see in plain English."""


@dataclass
class MediaInfo:
    url: str
    title: str
    channel: str
    platform: str
    duration: float
    thumbnail: str
    timestamp: float | None
    video_id: str
    audio_format: dict | None
    video_format: dict | None
    heatmap: list[dict] = field(default_factory=list)


# ------------------------------------------------------------------ resolve
def resolve(url: str) -> MediaInfo:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "Sign in to confirm" in msg or "bot" in msg.lower():
            raise SourceError("YouTube blocked the download from our server. Try a Twitch or Kick VOD link.")
        if "Unsupported URL" in msg:
            raise SourceError("That link isn't a supported video. Paste a Twitch, Kick or YouTube VOD link.")
        if "subscriber" in msg.lower() or "private" in msg.lower():
            raise SourceError("That video is private or subscriber-only, so it can't be downloaded.")
        log.warning("resolve failed for %s: %s", url, msg)
        if "404" in msg or "not found" in msg.lower() or "does not exist" in msg.lower():
            raise SourceError("That video wasn't found. It may have been deleted. Check the link.")
        raise SourceError("Couldn't open that link. Make sure it's a public VOD link and try again.")
    if info.get("_type") == "playlist":
        raise SourceError("That's a channel or playlist link. Paste the link to one specific video.")
    if info.get("is_live"):
        raise SourceError("That stream is still live. Paste the VOD link once the stream has ended.")
    formats = info.get("formats") or [info]
    duration = float(info.get("duration") or 0)
    if duration <= 0 and len(formats) == 1 and formats[0].get("url"):
        duration = _probe_duration(formats[0]["url"])     # direct file links don't report length
    if duration <= 0:
        raise SourceError("Couldn't read the video's length. Make sure it's a finished VOD.")
    if duration > settings.max_vod_hours * 3600:
        raise SourceError(f"That video is longer than {settings.max_vod_hours:g} hours, the current limit.")

    return MediaInfo(
        url=url,
        title=info.get("title") or "Untitled",
        channel=info.get("uploader") or info.get("channel") or info.get("uploader_id") or "",
        platform=(info.get("extractor_key") or "").replace("Vod", "").replace("Video", "") or "Video",
        duration=duration,
        thumbnail=info.get("thumbnail") or "",
        timestamp=info.get("timestamp"),
        video_id=str(info.get("id") or ""),
        audio_format=_pick_audio(formats),
        video_format=_pick_video(formats),
        heatmap=info.get("heatmap") or [],
    )


def _probe_duration(url: str) -> float:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", url],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        return float(out or 0)
    except (subprocess.SubprocessError, ValueError):
        return 0.0


def _pick_audio(formats: list[dict]) -> dict | None:
    audio = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    if audio:
        return min(audio, key=lambda f: f.get("tbr") or f.get("abr") or 1e9)
    # No audio-only stream: smallest muxed format still carries the audio.
    muxed = [f for f in formats if f.get("acodec") not in (None, "none")]
    return min(muxed, key=lambda f: f.get("tbr") or 1e9) if muxed else None


def _pick_video(formats: list[dict]) -> dict | None:
    """Best muxed (video+audio) format at or below 1080p, preferring 60fps."""
    muxed = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none")
             and (f.get("height") or 0) <= 1080]
    if not muxed:
        return None
    return max(muxed, key=lambda f: ((f.get("height") or 0), (f.get("fps") or 0), (f.get("tbr") or 0)))


def is_hls(fmt: dict | None) -> bool:
    return bool(fmt) and "m3u8" in (fmt.get("protocol") or "")


# ------------------------------------------------------------------ HLS
@dataclass
class Segment:
    start: float
    duration: float
    url: str


def _hls_playlist(url: str, headers: dict) -> tuple[list[Segment], str | None]:
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as c:
        text = c.get(url).raise_for_status().text
        lines = text.splitlines()
        # A master playlist points at variants — take the first.
        if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):
            variant = next(l for l in lines if l and not l.startswith("#"))
            return _hls_playlist(urljoin(url, variant), headers)
    segs: list[Segment] = []
    init = None
    t = 0.0
    dur = None
    for line in lines:
        if line.startswith("#EXT-X-MAP:"):
            uri = line.split('URI="', 1)[1].split('"', 1)[0]
            init = urljoin(url, uri)
        elif line.startswith("#EXTINF:"):
            dur = float(line[8:].split(",", 1)[0])
        elif line and not line.startswith("#") and dur is not None:
            segs.append(Segment(t, dur, urljoin(url, line)))
            t += dur
            dur = None
    if not segs:
        raise SourceError("The video stream was empty.")
    return segs, init


def _fetch(client: httpx.Client, url: str) -> bytes:
    for attempt in range(4):
        try:
            r = client.get(url)
            if r.status_code == 404 and "-unmuted" in url:   # Twitch renames muted segments
                r = client.get(url.replace("-unmuted", "-muted"))
            r.raise_for_status()
            return r.content
        except httpx.HTTPError:
            if attempt == 3:
                raise
    return b""


def hls_download(fmt: dict, out: Path, start: float | None = None, end: float | None = None,
                 progress: Progress | None = None, workers: int = 16) -> float:
    """Download [start, end] of an HLS stream. Returns the offset of `start` inside the file."""
    headers = {"User-Agent": UA, **(fmt.get("http_headers") or {})}
    segs, init = _hls_playlist(fmt["url"], headers)
    lo = 0.0 if start is None else start
    hi = segs[-1].start + segs[-1].duration if end is None else end
    picked = [s for s in segs if s.start + s.duration > lo and s.start < hi]
    if not picked:
        raise SourceError("That part of the video couldn't be found.")
    done = 0
    lock = threading.Lock()
    out.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=60, headers=headers, follow_redirects=True,
                      limits=httpx.Limits(max_connections=workers)) as client, \
            ThreadPoolExecutor(workers) as pool, open(out, "wb") as fh:
        if init:
            fh.write(_fetch(client, init))

        def job(seg: Segment) -> bytes:
            nonlocal done
            data = _fetch(client, seg.url)
            with lock:
                done += 1
                if progress and (done % 10 == 0 or done == len(picked)):
                    progress(done / len(picked))
            return data

        # map() keeps order; write as results arrive in sequence.
        for data in pool.map(job, picked):
            fh.write(data)
    return lo - picked[0].start


# ------------------------------------------------------------------ public API
def download_audio(info: MediaInfo, out_dir: Path, progress: Progress | None = None) -> Path:
    """Full-length audio for analysis: 16 kHz mono WAV (decode only, instantly seekable)."""
    fmt = info.audio_format
    raw = out_dir / "audio_raw"
    if is_hls(fmt):
        hls_download(fmt, raw, progress=progress)
    else:
        _ytdlp_download(info.url, fmt["format_id"] if fmt else "worstaudio/worst", raw, progress=progress)
        raw = next(out_dir.glob("audio_raw*"))
    audio = out_dir / "audio.wav"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-vn", "-ac", "1", "-ar", "16000",
                        "-c:a", "pcm_s16le", str(audio)], capture_output=True, text=True)
    raw.unlink(missing_ok=True)
    if r.returncode != 0:
        raise SourceError("Couldn't read the video's audio.")
    return audio


def download_section(info: MediaInfo, start: float, end: float, out: Path) -> tuple[Path, float]:
    """Video covering [start, end]. Returns (file, offset of `start` inside it).

    The file is not trimmed here: the renderer re-encodes anyway, so it cuts frame-exact,
    which keeps captions in sync (a stream-copy cut would snap to a keyframe).
    """
    fmt = info.video_format
    if is_hls(fmt):
        offset = hls_download(fmt, out, start, end, workers=8)
        return out, offset
    _ytdlp_download(info.url, fmt["format_id"] if fmt else "best[height<=1080]/best", out,
                    section=(start, end))
    got = next(out.parent.glob(out.name + ".*"))
    return got, 0.0


def _ytdlp_download(url: str, fmt: str, out: Path, section: tuple[float, float] | None = None,
                    progress: Progress | None = None) -> None:
    def hook(d):
        if progress and d.get("status") == "downloading" and d.get("total_bytes"):
            progress(d.get("downloaded_bytes", 0) / d["total_bytes"])

    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "format": fmt,
            "outtmpl": str(out) + ".%(ext)s", "progress_hooks": [hook], "concurrent_fragment_downloads": 8}
    if section:
        opts["download_ranges"] = yt_dlp.utils.download_range_func(None, [section])
        opts["force_keyframes_at_cuts"] = True
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([url])
    except yt_dlp.utils.DownloadError as e:
        log.warning("download failed for %s: %s", url, e)
        raise SourceError("The video download failed partway. Try again in a minute.")


# ------------------------------------------------------------------ crowd signals
def heatmap_points(info: MediaInfo) -> list[dict]:
    """YouTube 'most replayed' → peaks as signal points."""
    pts = []
    for h in info.heatmap:
        v = float(h.get("value") or 0)
        if v >= 0.5:
            pts.append({"t": (h["start_time"] + h["end_time"]) / 2, "views": 0, "weight": v * 2})
    return pts


def twitch_viewer_clips(info: MediaInfo) -> list[dict]:
    """Viewer-made clips of this VOD via the official Twitch API (needs admin's Twitch keys)."""
    if "twitch" not in info.platform.lower() or not settings.has_twitch:
        return []
    try:
        from .discovery.twitch import get_client
        tw = get_client()
        vid = info.video_id.lstrip("v")
        videos = tw._get("/videos", {"id": vid})
        if not videos:
            return []
        v = videos[0]
        created = datetime.strptime(v["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        end = datetime.fromtimestamp(created.timestamp() + info.duration + 86400 * 7, tz=timezone.utc)
        clips = tw.clips(v["user_id"], created, end)
    except Exception as e:  # a bonus signal — never fail the job over it
        log.warning("viewer clips unavailable: %s", e)
        return []
    out = []
    for c in clips:
        if str(c.get("video_id")) == vid and c.get("vod_offset") is not None:
            # Clips capture the ~30s before the click; the moment sits late in the clip.
            out.append({"t": c["vod_offset"] + 0.6 * float(c.get("duration") or 30),
                        "views": int(c.get("view_count") or 0)})
    return out
