"""Safe cleanup helpers for generated radio request files."""

from __future__ import annotations

import os

from modules.radio import azura
from modules.radio import db as radio_db
from modules.radio import liquidsoap_queue


def safe_generated_filename(filename: str) -> bool:
    name = os.path.basename(str(filename or ""))
    return (
        (azura.safe_request_filename(name) or liquidsoap_queue.safe_request_filename(name))
        and name == filename
    )


def cleanup_request_media(request_id: int, reason: str = "cleanup") -> bool:
    job = radio_db.get_request(request_id)
    if not job:
        return False
    filename = job.get("temp_filename") or ""
    file_id = job.get("azura_file_id") or ""
    remote_path = ""
    if azura.safe_request_filename(os.path.basename(str(filename or ""))):
        remote_path = azura.build_requests_remote_path(filename)
    elif liquidsoap_queue.safe_request_filename(os.path.basename(str(filename or ""))):
        remote_path = str(job.get("azura_path") or filename or "")
    if file_id:
        azura.clear_file_playlists(str(file_id))
    removed = azura.delete_request_file(filename if safe_generated_filename(filename) else "", str(file_id or ""))
    liquidsoap_path = str(job.get("azura_path") or filename or "")
    liquidsoap_removed = liquidsoap_queue.remove_request_file(liquidsoap_path)
    removed = bool(removed or liquidsoap_removed)
    azura.rescan_requests_folder()
    verified = azura.verify_request_file_gone(filename) if filename and safe_generated_filename(filename) else True
    ok = bool(removed or verified)
    if ok:
        radio_db.mark_status(request_id, "cleaned", finish_reason=reason)
    print(
        f"[RADIO_PHASE4] event=cleanup_request_media request_id={request_id} "
        f"reason={reason!r} remote_path={remote_path!r} ok={ok}"
    )
    return ok
