"""Safe cleanup helpers for generated radio request files."""

from __future__ import annotations

import os

from modules.radio import azura
from modules.radio import db as radio_db


def safe_generated_filename(filename: str) -> bool:
    return azura.safe_request_filename(os.path.basename(str(filename or ""))) and os.path.basename(str(filename or "")) == filename


def cleanup_request_media(request_id: int, reason: str = "cleanup") -> bool:
    job = radio_db.get_request(request_id)
    if not job:
        return False
    filename = job.get("temp_filename") or ""
    file_id = job.get("azura_file_id") or ""
    if file_id:
        azura.clear_file_playlists(str(file_id))
    removed = azura.delete_request_file(filename if safe_generated_filename(filename) else "", str(file_id or ""))
    azura.rescan_requests_folder()
    verified = azura.verify_request_file_gone(filename) if filename and safe_generated_filename(filename) else True
    ok = bool(removed or verified)
    if ok:
        radio_db.mark_status(request_id, "cleaned", finish_reason=reason)
    print(f"[RADIO_PHASE4] event=cleanup_request_media request_id={request_id} reason={reason!r} ok={ok}")
    return ok

