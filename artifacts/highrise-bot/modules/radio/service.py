"""Read-only radio service layer for Phase 3."""

from __future__ import annotations

from modules.radio import azura
from modules.radio import db as radio_db
from modules.radio import renderer
from modules.radio import settings as radio_settings


def ensure_ready() -> None:
    radio_db.ensure_schema()
    radio_settings.ensure_radio_settings()


def _renderer_settings() -> dict:
    return {
        "now_show_progress_bar": radio_settings.get_bool_setting("now_show_progress_bar", True),
        "now_show_likes_dislikes": radio_settings.get_bool_setting("now_show_likes_dislikes", True),
        "now_show_request_play_count": radio_settings.get_bool_setting("now_show_request_play_count", True),
        "now_footer_text": radio_settings.get_setting("now_footer_text", "🎶 !play to request a song"),
    }


def now_playing_card() -> tuple[str | None, dict | None, str]:
    ensure_ready()
    if not radio_settings.get_bool_setting("radio_enabled", True):
        return "📻 Music system is currently disabled.", None, ""
    np_data = azura.fetch_nowplaying()
    if not np_data:
        radio_db.mark_last_poll(False, "nowplaying_unavailable")
        return "📻 Radio is live, but I can't read the current track right now.", None, "nowplaying_unavailable"
    track = azura.extract_nowplaying_track(np_data)
    if not track:
        radio_db.mark_last_poll(False, "track_parse_failed")
        return "📻 Radio is live, but I can't read the current track right now.", None, "track_parse_failed"
    stats = radio_db.read_track_stats(track.get("track_key", ""), track.get("title", ""), track.get("artist", ""))
    radio_db.mark_last_poll(True)
    radio_db.set_runtime_state("current_track", track)
    return renderer.render_now_playing_card(track, "autodj", stats=stats, settings=_renderer_settings()), track, ""


def queue_display(limit: int = 10) -> str:
    rows = radio_db.queue_rows(limit)
    if not rows:
        return "🎶 QUEUE\nEmpty\nSong requests coming soon."
    lines = ["🎶 QUEUE"]
    icons = {
        "pending": "⏳",
        "preparing": "⏳",
        "ready": "✅",
        "submitted": "✅",
    }
    for idx, row in enumerate(rows, 1):
        icon = icons.get(str(row.get("status") or ""), "⏳")
        user = row.get("username") or "Unknown Player"
        title = row.get("title") or "Untitled request"
        lines.append(f"{idx}. {icon} @{user} — {title}")
    return "\n".join(lines)


def health_snapshot() -> dict:
    ensure_ready()
    api = azura.test_api()
    sftp = azura.test_sftp()
    card, track, err = now_playing_card()
    return {
        "radio_enabled": radio_settings.get_bool_setting("radio_enabled", True),
        "api": api,
        "sftp": sftp,
        "nowplaying_ok": bool(track),
        "track": track,
        "queue_size": radio_db.active_queue_count(),
        "last_error": err or radio_db.get_runtime_state("last_error", "none"),
        "card": card,
    }


def track_dedupe_key(track: dict | None) -> str:
    if not track:
        return ""
    return str(track.get("track_key") or "")
