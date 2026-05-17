"""
modules/media_cleanup.py
------------------------
AzuraCast file lifecycle manager.

Wraps the yt_request cleanup loop to provide a named, discoverable startup
entry point.  The cleanup loop itself runs inside yt_request so it can access
the in-memory job state directly — future work can migrate the loop here once
the state layer is fully extracted.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot


async def start(bot: "BaseBot") -> None:
    """
    Start the AzuraCast media cleanup + now-playing announcement loop.
    Call once from on_start for the DJ bot — idempotent (yt_request guards
    against double-start internally).
    """
    from modules.yt_request import startup_yt_cleanup_task
    await startup_yt_cleanup_task(bot)
