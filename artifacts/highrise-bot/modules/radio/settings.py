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


def get_bool_setting(key: str, default: bool) -> bool:
    value = get_setting(key, "true" if default else "false")
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}
