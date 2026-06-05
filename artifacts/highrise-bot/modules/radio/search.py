"""YouTube search helpers for Phase 5 radio requests."""

from __future__ import annotations

from modules.radio import settings as radio_settings


class SearchError(Exception):
    pass


def search_youtube(query: str, limit: int = 5) -> list[dict]:
    query = str(query or "").strip()
    if not query:
        raise SearchError("Search first with !play <song name>.")
    limit = max(1, min(5, int(limit or 5)))
    try:
        import yt_dlp
    except Exception as exc:
        raise SearchError(f"yt-dlp is unavailable: {exc}") from exc
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    except Exception as exc:
        raise SearchError("Could not search YouTube right now.") from exc
    entries = info.get("entries") if isinstance(info, dict) else []
    results: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        video_id = str(entry.get("id") or "").strip()
        url = entry.get("webpage_url") or entry.get("url") or ""
        if video_id and not str(url).startswith(("http://", "https://")):
            url = f"https://www.youtube.com/watch?v={video_id}"
        is_live = bool(entry.get("is_live")) or str(entry.get("live_status") or "").lower() in {"is_live", "is_upcoming"}
        if is_live and radio_settings.get_bool_setting("youtube_reject_livestreams", True):
            continue
        if not video_id or not url:
            continue
        results.append(
            {
                "title": entry.get("title") or "YouTube Result",
                "channel": entry.get("uploader") or entry.get("channel") or entry.get("creator") or "YouTube",
                "duration": int(entry.get("duration") or 0),
                "webpage_url": url,
                "video_id": video_id,
                "is_live": is_live,
                "was_playlist": False,
            }
        )
        if len(results) >= limit:
            break
    return results
