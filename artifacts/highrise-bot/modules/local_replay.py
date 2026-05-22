"""
modules/local_replay.py  —  LOCAL REPLAY DISABLED
---------------------------------------------------
This module is a safe stub. Local replay (SFTP-copy of AzuraCast library files
into the Requests folder) is not active.

No SFTP connections, no AzuraCast API calls, no background tasks.
All entry points return (False, "disabled") immediately.
"""
from __future__ import annotations


async def queue_local_copy(
    user_id: str = "",
    username: str = "",
    title: str = "",
    artist: str = "",
    azura_file_id: str = "",
    azura_song_id: str = "",
) -> tuple[bool, str]:
    """Stub — local replay is disabled. Returns (False, 'disabled') immediately."""
    print("[LOCAL_REPLAY] queue_local_copy called but local replay is disabled — skipping")
    return False, "disabled"


def queue_local_copy_sync(
    user_id: str = "",
    username: str = "",
    title: str = "",
    artist: str = "",
    azura_file_id: str = "",
    azura_song_id: str = "",
) -> tuple[bool, str]:
    """Stub — local replay is disabled. Returns (False, 'disabled') immediately."""
    return False, "disabled"
