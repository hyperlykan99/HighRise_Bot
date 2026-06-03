"""
modules/media_cleanup.py
------------------------
AzuraCast file lifecycle manager and playback engine startup.

Starts the active cleanup owner:
  1. playback_engine.startup_playback_engine(bot)
       — bot-controlled playlist switching, song detection, track announcements,
         and immediate post-play file deletion.

The yt_request cleanup startup hook is still called for backwards-compatible
startup wiring, but it is passive. Request played/cleaned lifecycle ownership
belongs to playback_engine.py after a request becomes ready.

Both tasks are idempotent on reconnect.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot


async def start(bot: "BaseBot") -> None:
    """
    Entry point for the DJ bot's on_start.
    Starts the primary playback engine and the legacy cleanup safety-net.
    """
    import os

    if (os.getenv("RADIO_SYSTEM_VERSION", "v1") or "v1").strip().lower() == "v3":
        from modules.radio_v3 import player

        player.start(bot)
        return

    from modules.playback_engine import startup_playback_engine
    from modules.yt_request      import startup_yt_cleanup_task

    await startup_playback_engine(bot)
    await startup_yt_cleanup_task(bot)
