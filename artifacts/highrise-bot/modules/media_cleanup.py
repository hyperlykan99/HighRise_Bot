"""
modules/media_cleanup.py
------------------------
AzuraCast file lifecycle manager and playback engine startup.

Calls two background tasks:
  1. playback_engine.startup_playback_engine(bot)
       — bot-controlled playlist switching, song detection, track announcements,
         and immediate post-play file deletion.
  2. startup_yt_cleanup_task(bot) from yt_request
       — legacy safety-net that catches any stale request files the primary
         engine may have missed (e.g. if the bot was offline during playback).

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
    from modules.playback_engine import startup_playback_engine
    from modules.yt_request      import startup_yt_cleanup_task

    await startup_playback_engine(bot)
    await startup_yt_cleanup_task(bot)
