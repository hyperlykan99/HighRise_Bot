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

def skip_current(max_attempts: int = 3, delay: float = 2.0) -> bool:
    """
    POST backend/skip.  Verifies the song actually changed before returning True.
    Returns True when a song change is confirmed, or the skip was accepted but
    verification is inconclusive.  Returns False on repeated HTTP errors.
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg:
        print(f"{_LOG} skip_current: API not configured")
        return False

    hdrs     = _headers(cfg)
    skip_url = f"{cfg['base_url']}/api/station/{cfg['station_id']}/backend/skip"

    id_before = ""
    np = fetch_nowplaying()
    if np:
        id_before = (
            ((np.get("now_playing") or {}).get("song") or {}).get("id", "")
        ) or ""

    for attempt in range(1, max_attempts + 1):
        try:
            resp = req_lib.post(skip_url, headers=hdrs, timeout=10)
            print(f"{_LOG} skip attempt {attempt}/{max_attempts} → HTTP {resp.status_code}")
            if resp.status_code in (200, 204):
                if not id_before:
                    return True
                time.sleep(delay)
                np2 = fetch_nowplaying()
                if np2:
                    id_after = (
                        ((np2.get("now_playing") or {}).get("song") or {}).get("id", "")
                    ) or ""
                    if id_after and id_after != id_before:
                        print(f"{_LOG} skip verified ✓ (attempt {attempt})")
                        return True
                print(f"{_LOG} song unchanged after attempt {attempt}")
        except Exception as exc:
            print(f"{_LOG} skip error attempt {attempt}: {exc}")
        if attempt < max_attempts:
            time.sleep(delay)

    print(f"{_LOG} skip unconfirmed after {max_attempts} attempts")
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


def apply_vibe(vibe: str) -> "tuple[bool, bool]":
    """
    Deterministically switch playlists for the given vibe.
    chill → enable chill playlist, disable party playlist
    party → disable chill playlist, enable party playlist
    The Requests playlist is always left untouched.
    Returns (chill_ok, party_ok).
    """
    chill_id = chill_playlist_id()
    party_id = party_playlist_id()

    if vibe == "party":
        chill_ok = set_playlist_enabled(chill_id, False) if chill_id else True
        party_ok = set_playlist_enabled(party_id, True)  if party_id else True
    else:  # chill
        chill_ok = set_playlist_enabled(chill_id, True)  if chill_id else True
        party_ok = set_playlist_enabled(party_id, False) if party_id else True

    return chill_ok, party_ok


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
