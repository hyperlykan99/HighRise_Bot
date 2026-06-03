"""Read-only AzuraCast helpers for the rebuilt radio skeleton."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from modules import config_store as cs


def _api_request(path: str, timeout: int = 8) -> dict | list | None:
    cfg = cs.azura_api_cfg()
    if not cfg:
        return None
    url = f"{cfg['base_url']}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw else {}


def fetch_nowplaying() -> dict | None:
    cfg = cs.azura_api_cfg()
    if not cfg:
        return None
    try:
        data = _api_request(f"/api/nowplaying/{cfg['station_id']}")
        return data if isinstance(data, dict) else None
    except Exception as exc:
        print(f"[RADIO_SKELETON] event=azura_nowplaying_failed error={exc!r}")
        return None


def test_api() -> dict:
    try:
        data = fetch_nowplaying()
        if data:
            return {"ok": True, "error": ""}
        return {"ok": False, "error": "no_nowplaying_response"}
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


def test_sftp() -> dict:
    missing = cs.sftp_missing_vars()
    if missing:
        return {"ok": False, "error": f"missing:{','.join(missing)}"}
    cfg = cs.sftp_cfg()
    try:
        with socket.create_connection((cfg["host"], int(cfg["port"])), timeout=5):
            return {"ok": True, "error": ""}
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


def _song_dict(np_data: dict) -> dict:
    song = np_data.get("now_playing") or {}
    if isinstance(song, dict):
        inner = song.get("song")
        if isinstance(inner, dict):
            return inner
    return {}


def extract_nowplaying_track(np_data: dict | None) -> dict | None:
    if not isinstance(np_data, dict):
        return None
    now_playing = np_data.get("now_playing") or {}
    song = _song_dict(np_data)
    station = np_data.get("station") if isinstance(np_data.get("station"), dict) else {}
    elapsed = now_playing.get("elapsed") if isinstance(now_playing, dict) else None
    duration = now_playing.get("duration") if isinstance(now_playing, dict) else None
    raw_title = song.get("title") or song.get("text") or ""
    artist = song.get("artist") or ""
    title = raw_title
    if not artist and " - " in raw_title:
        maybe_artist, maybe_title = raw_title.split(" - ", 1)
        artist, title = maybe_artist.strip(), maybe_title.strip()
    track = {
        "title": title or "Unknown Track",
        "artist": artist or "Unknown Artist",
        "elapsed": _int_or_none(elapsed),
        "duration": _int_or_none(duration or song.get("duration")),
        "song_id": song.get("id") or song.get("song_id") or "",
        "unique_id": song.get("unique_id") or "",
        "media_id": song.get("media_id") or song.get("id") or "",
        "path": song.get("path") or "",
        "art": song.get("art") or station.get("art") or "",
        "raw": song,
    }
    track["track_key"] = track_key(track)
    return track


def track_key(track: dict[str, Any]) -> str:
    for key in ("unique_id", "song_id", "media_id", "path"):
        value = str(track.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return f"title:{track.get('title','').strip().lower()}|artist:{track.get('artist','').strip().lower()}"


def _int_or_none(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None

