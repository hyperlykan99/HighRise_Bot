"""Radio V3 Azura transport.

V3 does not use the Azura native listener request queue as scheduler. It loads
one request file into a dedicated current-request playlist and performs one
controlled transition. AutoDJ/vibe remains fallback when V3 has no ready head.
"""
from __future__ import annotations

import os
import time

import modules.azuracast_controller as azura
from modules.radio_v3 import diagnostics as diag


def v3_playlist_id() -> str:
    pid = (os.getenv("RADIO_V3_PLAYLIST_ID") or "").strip()
    if pid:
        return pid
    row = (
        azura.find_playlist_by_name("Radio V3 Current")
        or azura.find_playlist_by_name("V3 Requests")
        or azura.find_playlist_by_name("V3 Current")
    )
    if row and row.get("id"):
        return str(row["id"])
    created = azura.create_playlist("Radio V3 Current")
    return str((created or {}).get("id") or "")


def lookup_uploaded_media(filename: str, title: str = "", attempts: int = 8) -> dict:
    filename = os.path.basename(filename or "")
    for attempt in range(1, attempts + 1):
        try:
            row = azura.search_media(filename)
            if not row and title:
                row = azura.search_media(title)
            if row:
                media_id = str(row.get("id") or "")
                song_id = (
                    row.get("unique_id")
                    or row.get("song_unique_id")
                    or (row.get("song") or {}).get("id")
                    or (row.get("song") or {}).get("unique_id")
                    or ""
                )
                diag.log("media_lookup", filename=filename, media_id=media_id, song_id=song_id, attempt=attempt)
                return {"media_id": media_id, "song_id": str(song_id or ""), "row": row}
        except Exception as exc:
            diag.log("media_lookup_error", filename=filename, attempt=attempt, error=repr(exc))
        if attempt < attempts:
            if attempt % 2 == 0:
                azura.rescan_requests_folder()
            time.sleep(1.5)
    diag.log("media_lookup_failed", filename=filename, title=title)
    return {}


def finalize_uploaded_request(request_id: int, filename: str, title: str) -> dict:
    azura.rescan_requests_folder()
    found = lookup_uploaded_media(filename, title)
    if not found.get("media_id"):
        return {"ok": False, "error": "media_lookup_failed"}
    media_id = found["media_id"]
    song_id = found.get("song_id", "")
    azura.clear_file_playlists(media_id)
    diag.log("media_ready", request_id=request_id, media_id=media_id, song_id=song_id)
    return {"ok": True, "media_id": media_id, "song_id": song_id}


def load_current_request(row: dict) -> bool:
    request_id = int(row.get("id") or 0)
    media_id = (row.get("azura_file_id") or "").strip()
    filename = (row.get("temp_filename") or "").strip()
    if not request_id or not media_id:
        diag.log("load_current_failed", request_id=request_id, reason="missing_media_id")
        return False
    pid = v3_playlist_id()
    if not pid:
        diag.log("load_current_failed", request_id=request_id, reason="missing_v3_playlist")
        return False
    try:
        azura.clear_file_playlists(media_id)
        added = azura.add_file_to_playlist(media_id, pid)
        enabled = azura.set_playlist_enabled(pid, True)
        diag.log(
            "load_current_playlist",
            request_id=request_id,
            media_id=media_id,
            filename=filename,
            playlist_id=pid,
            added=bool(added),
            enabled=bool(enabled),
        )
        return bool(added)
    except Exception as exc:
        diag.log("load_current_failed", request_id=request_id, error=repr(exc))
        return False


def transition_to_loaded_request(request_id: int) -> bool:
    try:
        ok = azura.skip_current(max_attempts=1, delay=0)
        diag.log("controlled_transition", request_id=request_id, result="success" if ok else "failed")
        return bool(ok)
    except Exception as exc:
        diag.log("controlled_transition_failed", request_id=request_id, error=repr(exc))
        return False

