"""
modules/azuracast_controller.py
--------------------------------
Deterministic AzuraCast REST API + SFTP controller.

All functions are BLOCKING — call via loop.run_in_executor() from async code.
All functions are non-fatal: errors are logged and False/None is returned.

Environment variables are read via config_store — never directly here.
"""
from __future__ import annotations
import os
import time

from modules.config_store import (
    azura_api_cfg,
    VIBE_NAMES,
    vibe_playlist_id,
    requests_playlist_id,
    chill_playlist_id,
    party_playlist_id,
    sftp_cfg,
)

_LOG = "[AZURA]"


def _headers(cfg: dict) -> dict:
    return {
        "X-API-Key":    cfg["api_key"],
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }


# ─── Now Playing ──────────────────────────────────────────────────────────────

def fetch_nowplaying() -> "dict | None":
    """GET /api/nowplaying/{station_id}  →  full NowPlaying dict or None."""
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg:
        return None
    try:
        resp = req_lib.get(
            f"{cfg['base_url']}/api/nowplaying/{cfg['station_id']}",
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
        print(f"{_LOG} nowplaying HTTP {resp.status_code}")
    except Exception as exc:
        print(f"{_LOG} nowplaying error: {exc}")
    return None


# ─── Skip with verification ───────────────────────────────────────────────────

def skip_current(max_attempts: int = 2, delay: float = 2.0) -> bool:
    """
    POST /api/station/{station_id}/backend/skip.

    Returns True immediately on HTTP 200 or 204 — AzuraCast's acceptance is
    the authoritative success signal; song-change polling caused false failures
    when AzuraCast transitions took longer than the poll window.

    On non-2xx or network error: retries once (max_attempts=2 default) after
    `delay` seconds.  All failures are logged with structured fields; nothing
    is re-raised so the bot never goes offline due to a skip error.

    Structured log fields:
        stage=azuracast_skip  station_id=  api_url=  attempt=  http_status=
        response_body=  exception=
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg:
        print(
            f"{_LOG} stage=azuracast_skip"
            f" station_id=<not_configured>"
            f" api_url=<not_configured>"
            f" exception=API_not_configured"
        )
        return False

    station_id = cfg["station_id"]
    base_url   = cfg["base_url"]
    skip_url   = f"{base_url}/api/station/{station_id}/backend/skip"
    hdrs       = _headers(cfg)

    for attempt in range(1, max_attempts + 1):
        try:
            resp = req_lib.post(skip_url, headers=hdrs, timeout=10)
            body = ""
            try:
                body = (resp.text or "")[:300]
            except Exception:
                pass
            print(
                f"{_LOG} stage=azuracast_skip"
                f" station_id={station_id!r}"
                f" api_url={base_url!r}"
                f" attempt={attempt}/{max_attempts}"
                f" http_status={resp.status_code}"
                f" response_body={body!r}"
            )
            if resp.status_code in (200, 204):
                print(f"{_LOG} stage=azuracast_skip status=accepted attempt={attempt}")
                return True
        except Exception as exc:
            print(
                f"{_LOG} stage=azuracast_skip"
                f" station_id={station_id!r}"
                f" api_url={base_url!r}"
                f" attempt={attempt}/{max_attempts}"
                f" exception={exc!r}"
            )
        if attempt < max_attempts:
            print(
                f"{_LOG} stage=azuracast_skip"
                f" status=retrying_in_{delay}s attempt={attempt}/{max_attempts}"
            )
            time.sleep(delay)

    print(
        f"{_LOG} stage=azuracast_skip"
        f" station_id={station_id!r}"
        f" api_url={base_url!r}"
        f" status=all_attempts_failed"
    )
    return False


# ─── Playlist management ──────────────────────────────────────────────────────

def set_playlist_enabled(playlist_id: str, enabled: bool) -> bool:
    """PUT /api/station/{id}/playlist/{pid}  body: {"is_enabled": bool}"""
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg or not playlist_id:
        return False
    try:
        resp = req_lib.put(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/playlist/{playlist_id}",
            json={"is_enabled": enabled},
            headers=_headers(cfg),
            timeout=10,
        )
        ok = resp.status_code in (200, 204)
        print(f"{_LOG} playlist {playlist_id} enabled={enabled} → HTTP {resp.status_code} ok={ok}")
        return ok
    except Exception as exc:
        print(f"{_LOG} playlist toggle error ({playlist_id}): {exc}")
    return False


def get_playlist_media_count(playlist_id: str) -> int:
    """
    GET /api/station/{id}/playlist/{pid}  → num_songs field.
    Returns 0 on any error (safe default: treats empty/unknown as 0).
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg or not playlist_id:
        return 0
    try:
        resp = req_lib.get(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/playlist/{playlist_id}",
            headers=_headers(cfg),
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return max(0, int(data.get("num_songs", 0)))
        print(f"{_LOG} playlist_info HTTP {resp.status_code} pl={playlist_id}")
    except Exception as exc:
        print(f"{_LOG} playlist_info error ({playlist_id}): {exc}")
    return 0


def switch_vibe(new_vibe: str) -> dict:
    """
    Enable only the selected vibe playlist; disable all other vibe playlists.
    The Requests playlist (AZURA_PLAYLIST_ID) is NEVER touched.

    Structured log fields:
        stage=vibe_switch  new_vibe=  playlist_id=  requests_playlist_id=
        enabled=  disabled=  result=  error=

    Returns:
        {"status": "ok",      "enabled": pid, "disabled": [...]}
        {"status": "partial", "enabled": pid, "disabled": [...], "errors": [...]}
        {"status": "no_config", "vibe": new_vibe}
    """
    target_id = vibe_playlist_id(new_vibe)
    req_id    = requests_playlist_id()

    if not target_id:
        print(
            f"{_LOG} stage=vibe_switch new_vibe={new_vibe!r}"
            f" result=no_config error=env_var_not_set"
        )
        return {"status": "no_config", "vibe": new_vibe}

    disabled: "list[str]" = []
    errors:   "list[str]" = []

    for v in VIBE_NAMES:
        pid = vibe_playlist_id(v)
        if not pid or pid == req_id:   # never touch Requests
            continue
        if pid == target_id:
            ok = set_playlist_enabled(pid, True)
            if not ok:
                errors.append(pid)
        else:
            ok = set_playlist_enabled(pid, False)
            if ok:
                disabled.append(pid)
            else:
                errors.append(pid)

    result = "ok" if not errors else "partial"
    print(
        f"{_LOG} stage=vibe_switch"
        f" new_vibe={new_vibe!r}"
        f" playlist_id={target_id!r}"
        f" requests_playlist_id={req_id!r}"
        f" enabled={target_id!r}"
        f" disabled={disabled!r}"
        f" result={result!r}"
        f" error={errors!r}"
    )
    return {
        "status":   result,
        "enabled":  target_id,
        "disabled": disabled,
        "errors":   errors,
    }


def apply_vibe(vibe: str) -> "tuple[bool, bool]":
    """
    Backward-compatible wrapper around switch_vibe().
    Returns (ok, ok) — callers that still use this signature keep working.
    """
    res = switch_vibe(vibe)
    ok  = res.get("status") in ("ok", "partial")
    return (ok, ok)


# ─── Media file management ────────────────────────────────────────────────────

def delete_media_file(file_id: str) -> bool:
    """
    DELETE /api/station/{id}/file/{file_id}
    404 is treated as success — the file was already deleted (idempotent).
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg or not file_id:
        return False
    try:
        resp = req_lib.delete(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/file/{file_id}",
            headers=_headers(cfg),
            timeout=10,
        )
        # 404 = file already gone — treat as success so cleaned_at is set
        ok = resp.status_code in (200, 204, 404)
        print(f"{_LOG} delete_media file_id={file_id} → HTTP {resp.status_code} ok={ok}")
        return ok
    except Exception as exc:
        print(f"{_LOG} delete_media error ({file_id}): {exc}")
    return False


def sftp_move_to_played(filename: str) -> bool:
    """
    Move a file from the SFTP Requests folder to a PlayedRequests folder
    (Option B: keep played files for audit instead of deleting them).

    Strategy
    ─────────
    1. Try sftp.rename() — atomic on same-filesystem servers (most common).
    2. Fallback: stream-copy within the same SFTP session, then delete source.
    3. If source is already gone (errno 2 / "No such file"), treat as success.

    The destination folder is:
      AZURA_PLAYED_SFTP_PATH  env var (explicit override), or
      <requests_folder>/../PlayedRequests  (derived from AZURA_SFTP_PATH).

    Returns True on success or source-already-gone; False on unrecoverable error.
    """
    import paramiko
    cfg = sftp_cfg()
    if not cfg["host"] or not cfg["user"]:
        print(f"{_LOG} sftp_move_to_played: SFTP not configured — skipping")
        return False

    src_folder    = cfg["folder"].rstrip("/")
    played_folder = (
        (os.environ.get("AZURA_PLAYED_SFTP_PATH") or "").strip()
        or os.path.join(os.path.dirname(src_folder) or ".", "PlayedRequests")
    ).rstrip("/")

    src_path  = f"{src_folder}/{filename}"
    dst_path  = f"{played_folder}/{filename}"

    ssh  = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sftp = None
    try:
        ssh.connect(
            hostname=cfg["host"], port=cfg["port"],
            username=cfg["user"], password=cfg["passwd"],
            timeout=30, look_for_keys=False, allow_agent=False,
        )
        sftp = ssh.open_sftp()

        # Ensure destination directory exists
        try:
            sftp.stat(played_folder)
        except IOError:
            try:
                sftp.mkdir(played_folder)
                print(f"{_LOG} sftp_move_to_played: created dir {played_folder!r}")
            except Exception as mk_exc:
                print(f"{_LOG} sftp_move_to_played: mkdir warning (ignored): {mk_exc}")

        # Strategy 1: atomic rename (same-filesystem move)
        try:
            sftp.rename(src_path, dst_path)
            print(f"{_LOG} sftp_move_to_played ✓ (rename) {src_path} → {dst_path}")
            return True
        except Exception as ren_exc:
            print(f"{_LOG} sftp_move_to_played rename failed ({ren_exc!r}), trying stream-copy")

        # Strategy 2: stream-copy within same SFTP session, then delete source
        with sftp.file(src_path, "rb") as src_f:
            data = src_f.read()
        with sftp.file(dst_path, "wb") as dst_f:
            dst_f.write(data)
        sftp.remove(src_path)
        print(f"{_LOG} sftp_move_to_played ✓ (copy+delete) {src_path} → {dst_path}")
        return True

    except IOError as exc:
        if getattr(exc, "errno", None) == 2 or "No such file" in str(exc):
            print(f"{_LOG} sftp_move_to_played: src already gone — {src_path}")
            return True
        print(f"{_LOG} sftp_move_to_played error ({filename}): {exc}")
        return False
    except Exception as exc:
        print(f"{_LOG} sftp_move_to_played error ({filename}): {exc}")
        return False
    finally:
        if sftp:
            try:
                sftp.close()
            except Exception:
                pass
        try:
            ssh.close()
        except Exception:
            pass


def sftp_delete_file(filename: str) -> bool:
    """
    Remove a file from the SFTP Requests folder by filename.
    FileNotFoundError (errno 2) is treated as success — already deleted.
    """
    import paramiko
    cfg = sftp_cfg()
    if not cfg["host"] or not cfg["user"]:
        return False
    remote_path = f"{cfg['folder'].rstrip('/')}/{filename}"
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sftp = None
    try:
        ssh.connect(
            hostname=cfg["host"], port=cfg["port"],
            username=cfg["user"], password=cfg["passwd"],
            timeout=30, look_for_keys=False, allow_agent=False,
        )
        sftp = ssh.open_sftp()
        sftp.remove(remote_path)
        print(f"{_LOG} sftp_delete ✓ {remote_path}")
        return True
    except IOError as exc:
        # paramiko raises IOError(errno=2) when the file does not exist —
        # treat as success (already deleted is the same as deleted).
        if getattr(exc, "errno", None) == 2 or "No such file" in str(exc):
            print(f"{_LOG} sftp_delete: already gone — {remote_path}")
            return True
        print(f"{_LOG} sftp_delete error ({filename}): {exc}")
        return False
    except Exception as exc:
        print(f"{_LOG} sftp_delete error ({filename}): {exc}")
        return False
    finally:
        if sftp:
            try:
                sftp.close()
            except Exception:
                pass
        try:
            ssh.close()
        except Exception:
            pass


# ─── Request queue submission ─────────────────────────────────────────────────

def submit_request(unique_id: str) -> bool:
    """POST /api/station/{id}/request/{unique_id}"""
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg or not unique_id:
        return False
    try:
        resp = req_lib.post(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/request/{unique_id}",
            headers=_headers(cfg),
            timeout=15,
        )
        ok = resp.status_code in (200, 204)
        print(f"{_LOG} submit_request uid={unique_id} → HTTP {resp.status_code} ok={ok}")
        return ok
    except Exception as exc:
        print(f"{_LOG} submit_request error: {exc}")
    return False


def rescan_library(folder: str = "") -> bool:
    """POST /api/station/{id}/files/batch  {"do":"rescan"}"""
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg:
        return False
    try:
        resp = req_lib.post(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/files/batch",
            json={"do": "rescan", "currentDirectory": folder},
            headers=_headers(cfg),
            timeout=30,
        )
        print(f"{_LOG} rescan folder={folder!r} → HTTP {resp.status_code}")
        return resp.status_code in (200, 204)
    except Exception as exc:
        print(f"{_LOG} rescan error: {exc}")
    return False


def search_media(filename: str) -> "dict | None":
    """
    GET /api/station/{id}/files?searchPhrase=<filename>
    Returns the file row whose path basename matches exactly, or None.
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg:
        return None
    try:
        resp = req_lib.get(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/files",
            params={"searchPhrase": filename},
            headers=_headers(cfg),
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("rows", [])
            for row in rows:
                if os.path.basename(row.get("path", "")) == filename:
                    return row
    except Exception as exc:
        print(f"{_LOG} search_media error: {exc}")
    return None


def add_file_to_playlist(file_id: "int | str", playlist_id: str) -> bool:
    """POST /api/station/{id}/files/batch  {"do":"playlist","playlist":pid,"files":[fid]}"""
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg or not playlist_id or not file_id:
        return False
    try:
        resp = req_lib.post(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/files/batch",
            json={"do": "playlist", "playlist": playlist_id, "files": [file_id]},
            headers=_headers(cfg),
            timeout=15,
        )
        ok = resp.status_code in (200, 204)
        print(f"{_LOG} playlist_add file={file_id} pl={playlist_id} → HTTP {resp.status_code}")
        return ok
    except Exception as exc:
        print(f"{_LOG} playlist_add error: {exc}")
    return False


def get_media_file(file_id: "int | str") -> "dict | None":
    """
    GET /api/station/{id}/file/{file_id}

    Returns the full AzuraCast file record (including the 'playlists' array)
    or None on error.  Used to verify playlist assignment after add_file_to_playlist.
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg or not file_id:
        return None
    try:
        resp = req_lib.get(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/file/{file_id}",
            headers=_headers(cfg),
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
        print(f"{_LOG} get_media_file({file_id}) → HTTP {resp.status_code}")
    except Exception as exc:
        print(f"{_LOG} get_media_file error ({file_id}): {exc}")
    return None


def clear_file_playlists(file_id: "int | str") -> bool:
    """
    PUT /api/station/{id}/file/{file_id}  {"playlists": []}

    Immediately removes the file from ALL playlists so AzuraCast AutoDJ stops
    queuing it for replay.  Called before delete_media_file during cleanup.
    404 treated as success (file already gone).
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg or not file_id:
        return False
    try:
        resp = req_lib.put(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/file/{file_id}",
            json={"playlists": []},
            headers=_headers(cfg),
            timeout=15,
        )
        ok = resp.status_code in (200, 204, 404)
        print(f"{_LOG} clear_playlists file_id={file_id} → HTTP {resp.status_code} ok={ok}")
        return ok
    except Exception as exc:
        print(f"{_LOG} clear_playlists error ({file_id}): {exc}")
    return False


def rescan_requests_folder() -> bool:
    """
    POST batch rescan for the Requests media subfolder.

    Derives the folder name from sftp_cfg so it matches what AzuraCast sees
    as the media library subdirectory (e.g. 'Requests').  Call this after
    deleting a file via SFTP to let AzuraCast update its internal media DB.
    """
    sftp_raw = sftp_cfg().get("folder", "Requests").strip()
    folder   = os.path.basename(sftp_raw.rstrip("/")) or sftp_raw
    return rescan_library(folder)


def verify_file_deleted(filename: str, wait_secs: float = 3.0) -> bool:
    """
    Wait wait_secs then search for filename in the AzuraCast media library.

    Returns True if:
    - filename is empty (nothing to verify), or
    - the file is no longer indexed in AzuraCast, or
    - the file record exists but has no playlist assignments.

    Returns False if the file is still found and still has playlist membership
    (meaning AzuraCast could still queue it for replay).
    """
    if not filename:
        return True
    if wait_secs > 0:
        time.sleep(wait_secs)
    row = search_media(filename)
    if row is None:
        return True
    playlists = row.get("playlists") or []
    return len(playlists) == 0


# ─── Verified-skip helpers ────────────────────────────────────────────────────

def fetch_queue() -> list:
    """
    GET /api/station/{id}/queue
    Returns the list of upcoming scheduled tracks (includes pending requests).
    Returns empty list on error or when API is not configured.
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg:
        return []
    try:
        resp = req_lib.get(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/queue",
            headers=_headers(cfg),
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else []
        print(f"{_LOG} fetch_queue HTTP {resp.status_code}")
    except Exception as exc:
        print(f"{_LOG} fetch_queue error: {exc}")
    return []


def wait_for_song_in_queue(
    unique_id: str,
    max_wait: float = 20.0,
    poll_secs: float = 2.5,
    stop_flag: "threading.Event | None" = None,
) -> bool:
    """
    Poll GET /api/station/{id}/queue (and nowplaying as a fallback) until
    `unique_id` appears or `max_wait` seconds elapse.

    The AzuraCast request queue processor is asynchronous — this gives it time
    to schedule the freshly submitted request before we call skip.

    stop_flag — threading.Event set by the playback engine when the bot is
                shutting down. The function exits immediately when it is set,
                preventing long delays during subprocess shutdown.

    Returns True if the song is confirmed queued (or already playing).
    Returns False on timeout (caller should still attempt the skip anyway).
    """
    import threading as _threading
    if not unique_id:
        return False

    deadline = time.time() + max_wait
    attempt  = 0
    while time.time() < deadline:
        # Abort immediately if the bot is shutting down
        if stop_flag and stop_flag.is_set():
            print(f"{_LOG} wait_for_queue: stop flag set — exiting early")
            return False

        attempt += 1

        # Strategy 1: check the upcoming queue
        for item in fetch_queue():
            song     = item.get("song") or {}
            item_uid = (
                song.get("unique_id")
                or song.get("id")
                or item.get("song_id")
                or ""
            )
            if item_uid and item_uid == unique_id:
                print(f"{_LOG} wait_for_queue: {unique_id!r} found in queue (attempt {attempt})")
                return True

        # Strategy 2: maybe it's already playing (very fast queue)
        np = fetch_nowplaying()
        if np:
            playing_uid = (
                ((np.get("now_playing") or {}).get("song") or {}).get("id") or ""
            ).strip()
            if playing_uid == unique_id:
                print(f"{_LOG} wait_for_queue: {unique_id!r} is already playing!")
                return True

        remaining = deadline - time.time()
        print(
            f"{_LOG} wait_for_queue attempt {attempt}: {unique_id!r} not yet in queue, "
            f"{max(remaining, 0):.0f}s left"
        )
        if remaining <= 0:
            break

        # Sleep in 0.5 s chunks so stop_flag is checked frequently
        sleep_until = time.time() + min(poll_secs, remaining)
        while time.time() < sleep_until:
            if stop_flag and stop_flag.is_set():
                return False
            time.sleep(0.5)

    print(f"{_LOG} wait_for_queue timeout: {unique_id!r} not seen in {max_wait:.0f}s")
    return False


def skip_and_verify_request(
    unique_id: str,
    max_retries: int = 3,
    post_skip_wait: float = 5.0,
    stop_flag: "threading.Event | None" = None,
) -> bool:
    """
    Ensure the requested song starts playing by:
      1. Re-submitting the song to AzuraCast's request queue (idempotent).
      2. Waiting 2 s for the queue to register.
      3. Posting a skip.
      4. Waiting `post_skip_wait` s for the new track to settle.
      5. Checking nowplaying.song.id == unique_id.
    Retries up to `max_retries` times when the wrong track is playing.

    stop_flag — threading.Event set by the playback engine on bot shutdown.
                All sleeps are broken into 0.5 s chunks so the function exits
                quickly rather than delaying subprocess shutdown.

    Returns True if the request is confirmed playing, False if all retries fail.
    """
    import threading as _threading

    def _interruptible_sleep(seconds: float) -> bool:
        """Sleep for `seconds`, returning False immediately if stop_flag is set."""
        end = time.time() + seconds
        while time.time() < end:
            if stop_flag and stop_flag.is_set():
                return False
            time.sleep(min(0.5, end - time.time()))
        return True

    if not unique_id:
        return False

    for attempt in range(1, max_retries + 1):
        # Abort if bot is shutting down
        if stop_flag and stop_flag.is_set():
            print(f"{_LOG} skip_and_verify: stop flag set — exiting early")
            return False

        print(
            f"{_LOG} skip_and_verify attempt {attempt}/{max_retries} "
            f"uid={unique_id!r}"
        )

        # Re-submit to place it at top of request queue
        submit_request(unique_id)
        if not _interruptible_sleep(2.0):
            return False

        # Skip current song
        skip_current(max_attempts=1, delay=0)

        # Wait for next track to start and stabilise (short chunked sleep)
        if not _interruptible_sleep(post_skip_wait):
            return False

        # Verify
        np = fetch_nowplaying()
        if np:
            playing_id = (
                ((np.get("now_playing") or {}).get("song") or {}).get("id") or ""
            ).strip()
            if playing_id == unique_id:
                print(f"{_LOG} skip_and_verify ✓ confirmed playing (attempt {attempt})")
                return True
            print(
                f"{_LOG} skip_and_verify attempt {attempt}: "
                f"playing={playing_id!r}, expected={unique_id!r}"
            )

        if attempt < max_retries:
            if not _interruptible_sleep(2.0):
                return False

    print(f"{_LOG} skip_and_verify: could not confirm {unique_id!r} after {max_retries} attempts")
    return False


# ─── Playlist reconciliation ──────────────────────────────────────────────────

def reconcile_requests_playlist() -> dict:
    """
    Scan yt_request_jobs for completed/failed requests that still have
    AzuraCast file IDs recorded but have not had their playlist assignments
    cleared yet (cleaned_at IS NULL).  For each such row, calls
    clear_file_playlists() so the track cannot replay via AutoDJ, then
    stamps cleaned_at in the DB.

    Call periodically (e.g. on bot startup or via admin command) to catch any
    cleanup that was missed during normal playback flow.

    Returns a summary dict:
        {"status": "ok", "checked": N, "cleaned": N, "errors": N}
    or  {"status": "skipped", "reason": "..."}
    or  {"status": "error", "error": "..."}

    stage=requests_playlist_reconcile
    """
    cfg = azura_api_cfg()
    if not cfg:
        print(f"{_LOG} stage=requests_playlist_reconcile status=skipped reason=no_api_config")
        return {"status": "skipped", "reason": "no_api_config"}

    terminal = ("played", "skipped", "failed", "cancelled", "error")

    try:
        import database as _db
        rows: list = []
        with _db.db_conn() as conn:
            ph = ",".join("?" * len(terminal))
            rows = conn.execute(
                f"SELECT id, filename, azura_file_id, title, status "
                f"FROM yt_request_jobs "
                f"WHERE status IN ({ph}) "
                f"  AND azura_file_id != '' "
                f"  AND (cleaned_at IS NULL OR cleaned_at = '') "
                f"ORDER BY id ASC LIMIT 50",
                terminal,
            ).fetchall()
    except Exception as exc:
        print(f"{_LOG} stage=requests_playlist_reconcile db_read_error={exc!r}")
        return {"status": "error", "error": str(exc)}

    cleaned = 0
    errors  = 0
    for row in rows:
        jid, fn, fid, title, status = row
        ok = clear_file_playlists(fid)
        print(
            f"{_LOG} stage=requests_playlist_reconcile"
            f" request_id={jid} file_id={fid!r}"
            f" filename={fn!r} title={title!r}"
            f" db_status={status!r}"
            f" playlist_cleared={ok}"
        )
        if ok:
            try:
                import database as _db2
                with _db2.db_conn() as conn:
                    conn.execute(
                        "UPDATE yt_request_jobs SET cleaned_at = datetime('now') WHERE id = ?",
                        (jid,),
                    )
            except Exception as exc2:
                print(f"{_LOG} stage=requests_playlist_reconcile stamp_error={exc2!r} id={jid}")
            cleaned += 1
        else:
            errors += 1

    print(
        f"{_LOG} stage=requests_playlist_reconcile"
        f" total_checked={len(rows)}"
        f" cleaned={cleaned} errors={errors}"
    )
    return {"status": "ok", "checked": len(rows), "cleaned": cleaned, "errors": errors}
