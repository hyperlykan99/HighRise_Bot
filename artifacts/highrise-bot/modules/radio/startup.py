"""Startup hooks for the rebuilt radio skeleton."""

from __future__ import annotations

import asyncio

from modules.radio import db as radio_db
from modules.radio import service
from modules.radio import settings as radio_settings


async def startup_radio_skeleton(bot) -> None:
    """Initialize schemas and start a lightweight AutoDJ now-playing poll."""
    service.ensure_ready()
    print("[RADIO_SKELETON] event=startup_ready")
    asyncio.create_task(_nowplaying_poll_loop(bot), name="radio_skeleton_nowplaying_poll")


async def _nowplaying_poll_loop(bot) -> None:
    last_key = radio_db.get_runtime_state("last_announced_track_key", "")
    while True:
        await asyncio.sleep(45)
        try:
            if not radio_settings.get_bool_setting("radio_enabled", True):
                continue
            if not radio_settings.get_bool_setting("now_announce_song_changes", True):
                continue
            card, track, _error = service.now_playing_card()
            key = service.track_dedupe_key(track)
            if not key or key == last_key:
                continue
            is_request = bool(radio_db.get_runtime_state("current_request_id", ""))
            if is_request and not radio_settings.get_bool_setting("now_announce_requests", True):
                continue
            if not is_request and not radio_settings.get_bool_setting("now_announce_autodj", True):
                continue
            last_key = key
            radio_db.set_runtime_state("last_announced_track_key", key)
            try:
                await bot.highrise.chat(str(card or "")[:900])
            except Exception as exc:
                print(f"[RADIO_SKELETON] event=announce_failed error={exc!r}")
        except Exception as exc:
            radio_db.mark_last_poll(False, repr(exc))
            print(f"[RADIO_SKELETON] event=poll_failed error={exc!r}")
