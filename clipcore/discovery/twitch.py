"""Twitch Helix client: find live streams of a game, resolve users, pull viewer-made clips."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx

from ..config import settings

HELIX = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"


class TwitchError(RuntimeError):
    pass


class Twitch:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = client_id or settings.twitch_client_id
        self.client_secret = client_secret or settings.twitch_client_secret
        if not (self.client_id and self.client_secret):
            raise TwitchError(
                "Twitch credentials missing: set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET "
                "(create an app at dev.twitch.tv/console)."
            )
        self._token: str | None = None
        self._token_exp = 0.0
        self._http = httpx.Client(timeout=20)
        self._game_ids: dict[str, str] = {}

    # -- auth -------------------------------------------------------------
    def _auth(self) -> dict[str, str]:
        if not self._token or time.time() > self._token_exp - 60:
            r = self._http.post(
                TOKEN_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                },
            )
            if r.status_code != 200:
                raise TwitchError(f"Twitch auth failed ({r.status_code}): {r.text[:200]}")
            body = r.json()
            self._token = body["access_token"]
            self._token_exp = time.time() + body.get("expires_in", 3600)
        return {"Client-Id": self.client_id, "Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, params: Any, max_pages: int = 1) -> list[dict]:
        out: list[dict] = []
        cursor = None
        for _ in range(max_pages):
            p = list(params.items()) if isinstance(params, dict) else list(params)
            if cursor:
                p.append(("after", cursor))
            r = self._http.get(f"{HELIX}{path}", params=p, headers=self._auth())
            if r.status_code == 429:
                time.sleep(2)
                continue
            if r.status_code != 200:
                raise TwitchError(f"GET {path} failed ({r.status_code}): {r.text[:200]}")
            body = r.json()
            out.extend(body.get("data", []))
            cursor = body.get("pagination", {}).get("cursor")
            if not cursor:
                break
        return out

    # -- queries ----------------------------------------------------------
    def game_id(self, name: str | None = None) -> str:
        name = name or settings.game_name
        if name not in self._game_ids:
            data = self._get("/games", {"name": name})
            if not data:
                raise TwitchError(f"Game not found on Twitch: {name!r}")
            self._game_ids[name] = data[0]["id"]
        return self._game_ids[name]

    def live_streams(self, limit: int = 20, language: str | None = None, game: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"game_id": self.game_id(game), "first": 100, "type": "live"}
        if language:
            params["language"] = language
        streams = self._get("/streams", params, max_pages=3)
        streams.sort(key=lambda s: s.get("viewer_count", 0), reverse=True)
        return streams[:limit]

    def streams_for(self, logins: Iterable[str]) -> dict[str, dict]:
        logins = [l.lower() for l in logins]
        live: dict[str, dict] = {}
        for i in range(0, len(logins), 100):
            batch = [("user_login", l) for l in logins[i : i + 100]]
            for s in self._get("/streams", batch):
                live[s["user_login"].lower()] = s
        return live

    def users(self, logins: Iterable[str]) -> dict[str, dict]:
        logins = [l.lower() for l in logins]
        out: dict[str, dict] = {}
        for i in range(0, len(logins), 100):
            for u in self._get("/users", [("login", l) for l in logins[i : i + 100]]):
                out[u["login"].lower()] = u
        return out

    def clips(self, broadcaster_id: str, started_at: datetime, ended_at: datetime) -> list[dict]:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        return self._get(
            "/clips",
            {
                "broadcaster_id": broadcaster_id,
                "started_at": started_at.astimezone(timezone.utc).strftime(fmt),
                "ended_at": ended_at.astimezone(timezone.utc).strftime(fmt),
                "first": 100,
            },
            max_pages=5,
        )


def clip_offsets(clips: list[dict], session_start: datetime) -> list[dict]:
    """Map viewer-made clips onto recording time.

    A Twitch clip is created *after* the moment it captures, so the moment sits roughly
    in the middle of [created_at - duration, created_at].
    """
    out = []
    for c in clips:
        created = datetime.strptime(c["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        t = (created - session_start).total_seconds() - c.get("duration", 30) / 2
        out.append({"t": t, "views": c.get("view_count", 0), "title": c.get("title", "")})
    return out


_client: Twitch | None = None
_client_key: tuple[str, str] | None = None


def get_client() -> Twitch:
    """Shared client that picks up credentials changed on the Settings page."""
    global _client, _client_key
    key = (settings.twitch_client_id, settings.twitch_client_secret)
    if _client is None or key != _client_key:
        _client, _client_key = Twitch(*key), key
    return _client


def reset_client() -> None:
    global _client, _client_key
    _client, _client_key = None, None
