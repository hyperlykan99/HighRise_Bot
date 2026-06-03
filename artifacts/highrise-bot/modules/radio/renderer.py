"""Message rendering for the rebuilt radio skeleton."""

from __future__ import annotations


def format_duration(seconds) -> str:
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        total = 0
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def progress_bar(elapsed, duration, blocks: int = 10) -> str:
    blocks = max(1, int(blocks))
    try:
        elapsed_i = max(0, int(float(elapsed or 0)))
        duration_i = max(0, int(float(duration or 0)))
    except (TypeError, ValueError):
        elapsed_i, duration_i = 0, 0
    if duration_i <= 0:
        filled = 1 if elapsed_i > 0 else 0
    else:
        filled = round((min(elapsed_i, duration_i) / duration_i) * blocks)
    filled = max(0, min(blocks, filled))
    return "▰" * filled + "▱" * (blocks - filled)


def _clean(value: str | None, fallback: str) -> str:
    text = str(value or "").strip()
    return text if text else fallback


def render_now_playing_card(track, source, requester=None, stats=None, settings=None) -> str:
    track = track or {}
    stats = stats or {}
    settings = settings or {}
    title = _clean(track.get("title"), "Unknown Track")
    artist = _clean(track.get("artist"), "Unknown Artist")
    elapsed = track.get("elapsed")
    duration = track.get("duration")
    footer = _clean(settings.get("now_footer_text"), "🎶 !play to request a song")
    lines = [
        "🎧 NOW PLAYING",
        f"🎵 Title: {title}",
        f"🎤 Artist: {artist}",
    ]
    if str(source).lower() == "request":
        lines.append("📡 Source: Requests")
        if requester:
            lines.append(f"🫵🏼 @{requester}")
    else:
        lines.append("🤖 Source: Auto DJ")
    if settings.get("now_show_progress_bar", True):
        progress = f"⏱️ {format_duration(elapsed)} {progress_bar(elapsed, duration)}"
        if duration:
            progress = f"{progress} {format_duration(duration)}"
        lines.append(progress)
    if settings.get("now_show_likes_dislikes", True) or settings.get("now_show_request_play_count", True):
        likes = int(stats.get("likes") or 0)
        dislikes = int(stats.get("dislikes") or 0)
        plays = int(stats.get("request_play_count") or 0)
        lines.append(f"👍 {likes} | 👎 {dislikes} | 🎧 {plays} plays requested")
    lines.append(footer)
    return "\n".join(lines)

