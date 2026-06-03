"""Radio V2 temp request cleanup."""
from __future__ import annotations

import os

import modules.azuracast_controller as azura
from modules.radio_v2 import diagnostics as diag
from modules.radio_v2 import queue

_SAFE_PREFIXES = ("tmp_replay_", "local_request_", "request_")


def safe_temp_filename(filename: str) -> bool:
    name = os.path.basename(filename or "")
    if not name or name != (filename or "").strip() or not name.endswith(".mp3"):
        return False
    if name.startswith(_SAFE_PREFIXES):
        return True
    stem = name[:-4]
    return 6 <= len(stem) <= 32 and all(c.isalnum() or c in "_-" for c in stem)


def cleanup_request_media(row: dict, reason: str = "cleanup") -> bool:
    request_id = int(row.get("id") or 0)
    filename = os.path.basename((row.get("temp_filename") or "").strip())
    media_id = (row.get("azura_file_id") or "").strip()
    song_id = (row.get("azura_song_id") or "").strip()
    diag.log("cleanup_start", request_id=request_id, filename=filename, media_id=media_id, reason=reason)
    removed = False
    try:
        if song_id or media_id or filename:
            removed_count = azura.remove_queue_items_matching(
                media_id=media_id,
                song_id=song_id,
                filename=filename,
                path=f"Requests/{filename}" if filename else "",
            )
            if removed_count:
                removed = True
        if media_id and azura.clear_file_playlists(media_id):
            removed = True
        if media_id and azura.delete_media_file(media_id):
            removed = True
        if filename and safe_temp_filename(filename):
            if azura.sftp_move_to_played(filename) or azura.sftp_delete_file(filename):
                removed = True
        elif filename:
            diag.log("cleanup_sftp_skipped", request_id=request_id, filename=filename, reason="unsafe_filename")
        azura.rescan_requests_folder()
        verified = azura.verify_file_deleted(filename, wait_secs=2.0) if filename else True
        if request_id and (removed or (filename and verified)):
            queue.mark_status(request_id, "cleaned")
        diag.log("cleanup_done", request_id=request_id, filename=filename, removed=bool(removed), verified=bool(verified))
        return bool(removed or verified)
    except Exception as exc:
        diag.log("cleanup_failed", request_id=request_id, filename=filename, error=repr(exc))
        return False
