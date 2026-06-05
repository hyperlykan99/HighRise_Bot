"""Liquidsoap/Icecast now-playing helpers."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


STATUS_URL = "http://127.0.0.1:8000/status-json.xsl"
MOUNT_NAME = "/chilltopia"


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def fetch_status_json(timeout: float = 2.0) -> dict | None:
    print(f"[RADIO_LIQUIDSOAP_NOWPLAYING_FETCH] url={STATUS_URL!r}")
    try:
        request = urllib.request.Request(STATUS_URL, headers={"User-Agent": "DJ_DUDU-radio/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(256 * 1024)
        return json.loads(raw.decode("utf-8", "replace"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f"[RADIO_LIQUIDSOAP_NOWPLAYING_FAILED] error={exc!r}")
        return None


def extract_mount_track(status_json: dict | None, mount: str = MOUNT_NAME) -> dict | None:
    if not isinstance(status_json, dict):
        return None
    icestats = status_json.get("icestats")
    if not isinstance(icestats, dict):
        return None
    sources = [source for source in _as_list(icestats.get("source")) if isinstance(source, dict)]
    selected = None
    for source in sources:
        listen_url = str(source.get("listenurl") or "")
        server_url = str(source.get("server_url") or "")
        if listen_url.endswith(mount) or server_url.endswith(mount):
            selected = source
            break
    if selected is None and len(sources) == 1:
        selected = sources[0]
    if selected:
        title = str(selected.get("title") or selected.get("server_name") or "").strip()
        artist = str(selected.get("artist") or "").strip()
        if " - " in title and not artist:
            artist, title = [part.strip() for part in title.split(" - ", 1)]
        return {
            "title": title or "Unknown",
            "artist": artist or "Unknown Artist",
            "duration": 0,
            "elapsed": 0,
            "live": True,
            "track_key": f"icecast:{title or 'unknown'}:{artist or ''}",
        }
    return None
