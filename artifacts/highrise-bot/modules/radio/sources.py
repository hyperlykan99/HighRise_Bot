"""Direct YouTube URL source preparation for Phase 4."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from modules.radio import settings as radio_settings


YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}


class SourceError(Exception):
    pass


def validate_youtube_url(url: str) -> str:
    url = str(url or "").strip()
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host not in YOUTUBE_HOSTS:
        raise SourceError("Only YouTube links are supported right now.")
    query = parse_qs(parsed.query)
    if radio_settings.get_bool_setting("youtube_reject_playlists", True) and query.get("list"):
        raise SourceError("Playlists are disabled. Add one song at a time.")
    if radio_settings.get_bool_setting("youtube_reject_mixes", True):
        list_id = (query.get("list") or [""])[0]
        if list_id.upper().startswith(("RD", "UL")) or query.get("start_radio"):
            raise SourceError("YouTube mixes/radio links are disabled.")
    if "/shorts/" in parsed.path and radio_settings.get_bool_setting("youtube_reject_shorts", False):
        raise SourceError("YouTube Shorts are disabled.")
    if host.endswith("youtube.com") and parsed.path == "/watch" and not query.get("v"):
        raise SourceError("That YouTube link is missing a video id.")
    if host.endswith("youtu.be") and not parsed.path.strip("/"):
        raise SourceError("That YouTube link is missing a video id.")
    return url


def youtube_video_id(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    host = parsed.netloc.lower()
    if host.endswith("youtu.be"):
        return parsed.path.strip("/").split("/", 1)[0]
    if host in YOUTUBE_HOSTS:
        if parsed.path == "/watch":
            return (parse_qs(parsed.query).get("v") or [""])[0].strip()
        if "/shorts/" in parsed.path:
            parts = [part for part in parsed.path.split("/") if part]
            try:
                return parts[parts.index("shorts") + 1]
            except (ValueError, IndexError):
                return ""
    return ""


def fetch_metadata(url: str) -> dict:
    try:
        import yt_dlp
    except Exception as exc:
        raise SourceError(f"yt-dlp is unavailable: {exc}") from exc
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise SourceError("Could not read that YouTube video.") from exc
    if not isinstance(info, dict):
        raise SourceError("Could not read that YouTube video.")
    is_live = info.get("is_live") or str(info.get("live_status") or "").lower() in {"is_live", "is_upcoming"}
    if is_live and radio_settings.get_bool_setting("youtube_reject_livestreams", True):
        raise SourceError("Livestreams are disabled.")
    return {
        "title": info.get("title") or "YouTube Request",
        "artist": info.get("uploader") or info.get("channel") or "YouTube",
        "duration": int(info.get("duration") or 0),
        "webpage_url": info.get("webpage_url") or url,
        "id": info.get("id") or "",
    }


def staging_dir() -> Path:
    root = Path(__file__).resolve().parents[2]
    path = root / "data" / "radio_staging"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_youtube_filename(request_id: int) -> str:
    return f"radio_yt_{int(request_id)}_{uuid.uuid4().hex[:12]}.mp3"


def download_youtube_mp3(url: str, remote_filename: str) -> Path:
    if not re.match(r"^radio_yt_\d+_[a-f0-9]{12}\.mp3$", remote_filename):
        raise SourceError("Unsafe request filename.")
    try:
        import yt_dlp
    except Exception as exc:
        raise SourceError(f"yt-dlp is unavailable: {exc}") from exc
    output_base = staging_dir() / remote_filename[:-4]
    final_path = staging_dir() / remote_filename
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(output_base) + ".%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        raise SourceError("Could not prepare that YouTube audio.") from exc
    if not final_path.exists():
        matches = list(staging_dir().glob(f"{output_base.name}*.mp3"))
        if matches:
            os.replace(matches[0], final_path)
    if not final_path.exists():
        raise SourceError("Prepared audio file was not created.")
    return final_path
