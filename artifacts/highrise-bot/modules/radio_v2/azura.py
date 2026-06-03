"""Radio V2 Azura transport helpers."""
from __future__ import annotations

import os
import time

import modules.azuracast_controller as azura
import modules.config_store as cs
from modules.radio_v2 import diagnostics as diag


def _headers(cfg: dict) -> dict:
    return {
        "X-API-Key": cfg["api_key"],
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _playlist_id() -> str:
    env_pid = (os.getenv("AZURA_PLAYLIST_ID") or "").strip()
    if env_pid:
        return env_pid
    try:
        pid = cs.requests_playlist_id()
        if pid:
            return str(pid)
    except Exception:
        pass
    row = azura.find_playlist_by_name("Requests") or azura.find_playlist_by_name("Request")
    return str((row or {}).get("id") or "")


def assign_to_requests_playlist(media_id: str) -> bool:
    import requests

    cfg = cs.azura_api_cfg()
    pid = _playlist_id()
    if not cfg or not media_id or not pid:
        return False
    try:
        pid_val: int | str
        try:
            pid_val = int(pid)
        except Exception:
            pid_val = pid
        resp = requests.put(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/file/{media_id}",
            json={"playlists": [pid_val]},
            headers=_headers(cfg),
            timeout=15,
        )
        ok = resp.status_code in (200, 204)
        diag.log("playlist_assign", media_id=media_id, playlist_id=pid, status=resp.status_code, ok=ok)
        return ok
    except Exception as exc:
        diag.log("playlist_assign_failed", media_id=media_id, error=repr(exc))
        return False


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


def submit_native_request(request_id: int, *, media_id: str, song_id: str, filename: str, title: str) -> tuple[bool, str]:
    diag.log("native_request_mode_enabled")
    diag.log("requests_playlist_should_not_be_general_rotation")
    candidates = [c for c in (song_id, media_id) if c]
    for candidate in candidates:
        ok, status, _body = azura.submit_request_verbose(candidate)
        diag.log(
            "native_request_submit",
            request_id=request_id,
            requestable_id=candidate,
            result="success" if ok else "fail",
            status=status,
        )
        if ok:
            return True, candidate

    for term in (filename, f"Requests/{filename}" if filename else "", title):
        term = (term or "").strip()
        if not term:
            continue
        rid = azura.lookup_requestable_id(term)
        diag.log("requestable_lookup", request_id=request_id, search=term, result=rid or "")
        if not rid:
            time.sleep(1.5)
            rid = azura.lookup_requestable_id(term)
            diag.log("requestable_lookup", request_id=request_id, search=term, retry=True, result=rid or "")
        if not rid:
            continue
        ok, status, _body = azura.submit_request_verbose(rid)
        diag.log(
            "native_request_submit",
            request_id=request_id,
            requestable_id=rid,
            result="success" if ok else "fail",
            status=status,
        )
        if ok:
            return True, str(rid)
    diag.log("native_request_submit_failed", request_id=request_id, reason="lookup_or_submit_failed")
    return False, ""


def finalize_uploaded_request(
    request_id: int,
    filename: str,
    title: str,
    *,
    submit_native: bool = False,
) -> dict:
    azura.rescan_requests_folder()
    found = lookup_uploaded_media(filename, title)
    if not found.get("media_id"):
        return {"ok": False, "error": "media_lookup_failed"}
    media_id = found["media_id"]
    song_id = found.get("song_id", "")
    assign_to_requests_playlist(media_id)
    if not submit_native:
        diag.log("media_uploaded_ready", request_id=request_id, media_id=media_id, song_id=song_id)
        return {
            "ok": True,
            "media_id": media_id,
            "song_id": song_id,
            "requestable_id": "",
        }
    ok, requestable_id = submit_native_request(
        request_id,
        media_id=media_id,
        song_id=song_id,
        filename=filename,
        title=title,
    )
    if not ok:
        return {
            "ok": False,
            "error": "native_request_submit_failed",
            "media_id": media_id,
            "song_id": song_id,
        }
    diag.log("native_request_ready", request_id=request_id, requestable_id=requestable_id)
    return {
        "ok": True,
        "media_id": media_id,
        "song_id": song_id,
        "requestable_id": requestable_id,
    }
