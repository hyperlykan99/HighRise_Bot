"""DB-backed settings for the rebuilt radio economy."""

from __future__ import annotations

import database as db


DEFAULT_SETTINGS: dict[str, tuple[str, str]] = {
    "music_shop_enabled": ("true", "bool"),
    "music_disc_display_name": ("Song Request 💽", "str"),
    "music_disc_price_coins": ("500", "int"),
    "music_disc_price_luxe": ("50", "int"),
    "music_disc_purchase_coins_enabled": ("true", "bool"),
    "music_disc_purchase_luxe_enabled": ("true", "bool"),
    "music_disc_max_purchase_per_command": ("10", "int"),
    "music_disc_daily_purchase_limit": ("50", "int"),
    "request_disc_cost_normal": ("1", "int"),
    "request_disc_cost_vip": ("1", "int"),
    "request_disc_cost_staff": ("0", "int"),
    "request_disc_cost_owner": ("0", "int"),
    "radio_enabled": ("true", "bool"),
    "radio_poll_interval_secs": ("3", "int"),
    "radio_submit_ready_immediately": ("true", "bool"),
    "radio_request_prequeue_enabled": ("true", "bool"),
    "radio_request_prequeue_count": ("1", "int"),
    "liquidsoap_queue_next_path": ("liquidsoap/queue/next", "str"),
    "liquidsoap_playing_path": ("liquidsoap/queue/playing", "str"),
    "liquidsoap_played_path": ("liquidsoap/queue/played", "str"),
    "liquidsoap_no_replay_move_delay_secs": ("20", "int"),
    "liquidsoap_cleanup_buffer_secs": ("60", "int"),
    "liquidsoap_cleanup_fallback_duration_secs": ("300", "int"),
    "spotdl_bin_path": ("/opt/highrise-bots/spotdl-venv/bin/spotdl", "str"),
    "now_announce_song_changes": ("true", "bool"),
    "now_announce_autodj": ("true", "bool"),
    "now_announce_requests": ("true", "bool"),
    "now_command_response_mode": ("whisper", "str"),
    "now_show_progress_bar": ("true", "bool"),
    "now_show_likes_dislikes": ("true", "bool"),
    "now_show_request_play_count": ("true", "bool"),
    "now_footer_text": ("🎶 !play to request a song", "str"),
    "max_song_duration_normal_secs": ("300", "int"),
    "max_song_duration_vip_secs": ("480", "int"),
    "max_song_duration_staff_secs": ("600", "int"),
    "max_song_duration_owner_secs": ("0", "int"),
    "youtube_direct_url_enabled": ("true", "bool"),
    "youtube_search_enabled": ("true", "bool"),
    "youtube_search_result_count": ("5", "int"),
    "youtube_search_session_timeout_secs": ("120", "int"),
    "radio_favorites_enabled": ("true", "bool"),
    "radio_favorites_max_per_user": ("25", "int"),
    "youtube_reject_playlists": ("true", "bool"),
    "youtube_reject_mixes": ("true", "bool"),
    "youtube_reject_livestreams": ("true", "bool"),
    "youtube_reject_shorts": ("false", "bool"),
    "block_requests_when_azura_unhealthy": ("true", "bool"),
}


def ensure_radio_settings() -> None:
    """Create and seed the radio_settings table idempotently."""
    with db.db_conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS radio_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                value_type TEXT,
                updated_by TEXT,
                updated_at TEXT
            )"""
        )
        for key, (value, value_type) in DEFAULT_SETTINGS.items():
            conn.execute(
                """INSERT OR IGNORE INTO radio_settings
                   (key, value, value_type, updated_by, updated_at)
                   VALUES (?, ?, ?, 'system', datetime('now'))""",
                (key, value, value_type),
            )


def get_setting(key: str, default=None):
    ensure_radio_settings()
    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT value FROM radio_settings WHERE key=?",
            (key,),
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value, value_type: str = "str", updated_by: str = "") -> None:
    ensure_radio_settings()
    with db.db_conn() as conn:
        conn.execute(
            """INSERT INTO radio_settings (key, value, value_type, updated_by, updated_at)
               VALUES (?, ?, ?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value,
                   value_type=excluded.value_type,
                   updated_by=excluded.updated_by,
                   updated_at=datetime('now')""",
            (key, str(value), value_type, updated_by or "system"),
        )


def get_int_setting(key: str, default: int) -> int:
    try:
        return int(get_setting(key, default))
    except (TypeError, ValueError):
        return int(default)


def radio_poll_interval_secs() -> int:
    value = get_int_setting("radio_poll_interval_secs", 3)
    return max(3, min(30, int(value)))


def radio_request_prequeue_count() -> int:
    value = get_int_setting("radio_request_prequeue_count", 1)
    return max(0, min(3, int(value)))


def liquidsoap_cleanup_buffer_secs() -> int:
    value = get_int_setting("liquidsoap_cleanup_buffer_secs", 60)
    return max(0, min(600, int(value)))


def liquidsoap_no_replay_move_delay_secs() -> int:
    value = get_int_setting("liquidsoap_no_replay_move_delay_secs", 20)
    return max(5, min(120, int(value)))


def liquidsoap_cleanup_fallback_duration_secs() -> int:
    value = get_int_setting("liquidsoap_cleanup_fallback_duration_secs", 300)
    return max(30, min(3600, int(value)))


def youtube_search_result_count() -> int:
    value = get_int_setting("youtube_search_result_count", 5)
    return max(1, min(5, int(value)))


def youtube_search_session_timeout_secs() -> int:
    value = get_int_setting("youtube_search_session_timeout_secs", 120)
    return max(30, min(300, int(value)))


def radio_favorites_max_per_user() -> int:
    value = get_int_setting("radio_favorites_max_per_user", 25)
    return max(1, min(100, int(value)))


def get_bool_setting(key: str, default: bool) -> bool:
    value = get_setting(key, "true" if default else "false")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}
