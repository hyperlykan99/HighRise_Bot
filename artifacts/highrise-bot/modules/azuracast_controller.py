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
    get_dynamic_vibe_playlist,
)

_LOG = "[AZURA]"


def _safe_request_basename(filename: str) -> bool:
    """True only for a plain filename inside the configured Requests folder."""
    if not filename:
        return False
    name = str(filename).strip()
    return (
        name == os.path.basename(name)
        and "/" not in name
        and "\\" not in name
        and name not in (".", "..")
        and ".." not in name.split(os.sep)
    )


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

    Checks env-var playlist ID first, then falls back to the DB-backed dynamic
    playlist ID (set by folder-discovery flow in radio_commands.handle_vibe).

    Structured log fields:
        stage=vibe_switch  new_vibe=  playlist_id=  requests_playlist_id=
        enabled=  disabled=  result=  error=

    Returns:
        {"status": "ok",      "enabled": pid, "disabled": [...]}
        {"status": "partial", "enabled": pid, "disabled": [...], "errors": [...]}
        {"status": "no_config", "vibe": new_vibe}
    """
    target_id = vibe_playlist_id(new_vibe) or get_dynamic_vibe_playlist(new_vibe)
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
    if not _safe_request_basename(filename):
        print(f"{_LOG} sftp_move_to_played refused unsafe filename={filename!r}")
        return False
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
    if not _safe_request_basename(filename):
        print(f"{_LOG} sftp_delete refused unsafe filename={filename!r}")
        return False
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
    ok, _status, _body = submit_request_verbose(unique_id)
    return ok


def submit_request_verbose(unique_id: str) -> "tuple[bool, int, str]":
    """
    POST /api/station/{id}/request/{unique_id}
    Returns (success, http_status_code, response_body_excerpt).
    Logs endpoint URL, station_id, unique_id, status, and response body.
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg:
        print(f"{_LOG} submit_request_verbose: no API config")
        return False, 0, "no_api_config"
    if not unique_id:
        print(f"{_LOG} submit_request_verbose: empty unique_id")
        return False, 0, "empty_unique_id"
    url = f"{cfg['base_url']}/api/station/{cfg['station_id']}/request/{unique_id}"
    print(
        f"{_LOG} submit_request"
        f" station={cfg['station_id']!r}"
        f" uid={unique_id!r}"
        f" url={url!r}"
    )
    try:
        resp = req_lib.post(url, headers=_headers(cfg), timeout=15)
        ok = resp.status_code in (200, 204)
        try:
            body = resp.json()
            body_s = str(body)[:300]
        except Exception:
            body_s = resp.text[:300] if resp.text else ""
        print(
            f"{_LOG} submit_request"
            f" status={resp.status_code} ok={ok}"
            f" body={body_s!r}"
        )
        return ok, resp.status_code, body_s
    except Exception as exc:
        print(f"{_LOG} submit_request error: {exc!r}")
        return False, 0, repr(exc)


def lookup_requestable_id(search_phrase: str) -> "str | None":
    """
    GET /api/station/{id}/requests?searchPhrase=<phrase>
    Returns the request_id (== unique_id) for the first matching requestable
    song, or None.  Used as fallback when submit_request fails because AzuraCast
    needs the requestable unique_id rather than the file unique_id.
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg or not search_phrase:
        return None
    url = f"{cfg['base_url']}/api/station/{cfg['station_id']}/requests"
    print(f"{_LOG} lookup_requestable_id phrase={search_phrase!r} url={url!r}")
    try:
        resp = req_lib.get(
            url,
            params={"searchPhrase": search_phrase},
            headers=_headers(cfg),
            timeout=15,
        )
        print(f"{_LOG} lookup_requestable_id status={resp.status_code}")
        if resp.status_code != 200:
            return None
        data = resp.json()
        rows = data if isinstance(data, list) else data.get("rows", [])
        for row in rows:
            rid = (
                row.get("request_id")
                or row.get("unique_id")
                or (row.get("song") or {}).get("id")
                or (row.get("song") or {}).get("unique_id")
            )
            if rid:
                print(f"{_LOG} lookup_requestable_id found rid={rid!r}")
                return str(rid)
    except Exception as exc:
        print(f"{_LOG} lookup_requestable_id error: {exc!r}")
    return None


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


def verify_file_in_playlist(file_id: "int | str", playlist_id: "int | str") -> bool:
    """
    GET /api/station/{id}/file/{file_id}
    Returns True if playlist_id appears in the file's 'playlists' array.
    Matches by string comparison of the 'id' field inside each playlist entry.
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg or not file_id or not playlist_id:
        return False
    url = f"{cfg['base_url']}/api/station/{cfg['station_id']}/file/{file_id}"
    try:
        r = req_lib.get(url, headers=_headers(cfg), timeout=15)
        print(
            f"{_LOG} verify_file_in_playlist"
            f" file_id={file_id!r} playlist_id={playlist_id!r}"
            f" → HTTP {r.status_code}"
        )
        if r.status_code == 200:
            data = r.json()
            playlists = data.get("playlists") or []
            print(f"{_LOG} verify_file_in_playlist playlists={playlists!r}")
            return any(
                str(p.get("id") if isinstance(p, dict) else p) == str(playlist_id)
                for p in playlists
            )
    except Exception as exc:
        print(f"{_LOG} verify_file_in_playlist error: {exc!r}")
    return False


def add_file_to_playlist(file_id: "int | str", playlist_id: "int | str") -> bool:
    """
    Assign a media file to a playlist.  Tries two methods (matching the
    yt_request.py flow) and verifies via GET after each:

      Method A: POST /api/station/{id}/files/batch
                {"do":"playlist","playlist":<pid>,"files":[<int fid>]}

      Method B: PUT  /api/station/{id}/file/{fid}
                {"playlists":[<int pid>]}

    Returns True only when GET /file/{fid} confirms the playlist is present.
    Full debug logging of URL, method, payload, HTTP status, and response body.
    """
    import requests as req_lib
    import time as _time
    cfg = azura_api_cfg()
    if not cfg or not playlist_id or not file_id:
        print(
            f"{_LOG} add_file_to_playlist: missing arg"
            f" file_id={file_id!r} playlist_id={playlist_id!r}"
        )
        return False

    base  = cfg["base_url"]
    sid   = cfg["station_id"]
    hdrs  = _headers(cfg)

    # Normalise IDs — AzuraCast batch endpoint needs int for files
    try:
        fid_int = int(file_id)
    except (ValueError, TypeError):
        fid_int = file_id
    try:
        pid_int: "int | str" = int(playlist_id)
    except (ValueError, TypeError):
        pid_int = playlist_id

    # ── Method A: batch POST ──────────────────────────────────────────────────
    batch_url  = f"{base}/api/station/{sid}/files/batch"
    batch_body = {"do": "playlist", "playlist": str(playlist_id), "files": [fid_int]}
    print(
        f"{_LOG} add_file_to_playlist method=A(batch)"
        f" url={batch_url!r}"
        f" payload={batch_body!r}"
    )
    try:
        resp = req_lib.post(batch_url, json=batch_body, headers=hdrs, timeout=15)
        try:
            body_s = resp.json()
            body_s = str(body_s)[:300]
        except Exception:
            body_s = resp.text[:300] if resp.text else ""
        print(
            f"{_LOG} add_file_to_playlist method=A"
            f" status={resp.status_code}"
            f" body={body_s!r}"
        )
    except Exception as exc:
        print(f"{_LOG} add_file_to_playlist method=A error: {exc!r}")

    _time.sleep(2)  # Let AzuraCast process the batch write

    if verify_file_in_playlist(fid_int, playlist_id):
        print(f"{_LOG} add_file_to_playlist method=A verified ✓")
        return True

    # ── Method B: direct PUT /file/{id} ──────────────────────────────────────
    put_url  = f"{base}/api/station/{sid}/file/{fid_int}"
    put_body = {"playlists": [pid_int]}
    print(
        f"{_LOG} add_file_to_playlist method=B(put)"
        f" url={put_url!r}"
        f" payload={put_body!r}"
    )
    try:
        resp = req_lib.put(put_url, json=put_body, headers=hdrs, timeout=15)
        try:
            body_s = resp.json()
            body_s = str(body_s)[:300]
        except Exception:
            body_s = resp.text[:300] if resp.text else ""
        print(
            f"{_LOG} add_file_to_playlist method=B"
            f" status={resp.status_code}"
            f" body={body_s!r}"
        )
    except Exception as exc:
        print(f"{_LOG} add_file_to_playlist method=B error: {exc!r}")

    _time.sleep(2)

    if verify_file_in_playlist(fid_int, playlist_id):
        print(f"{_LOG} add_file_to_playlist method=B verified ✓")
        return True

    print(f"{_LOG} add_file_to_playlist FAILED — not in playlist after both methods")
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


def _normalize_queue_item(item: dict, source: str = "queue") -> dict:
    """Return a compact comparable view of an Azura queue/nowplaying item."""
    if not isinstance(item, dict):
        item = {}
    song = item.get("song") or {}
    media = item.get("media") or item.get("song_media") or {}
    path = (
        media.get("path")
        or item.get("path")
        or item.get("media_path")
        or song.get("path")
        or ""
    )
    filename = (path or item.get("filename") or "").rsplit("/", 1)[-1]
    media_id = (
        media.get("id")
        or item.get("media_id")
        or item.get("media")
        or item.get("file_id")
        or ""
    )
    song_id = (
        song.get("unique_id")
        or song.get("id")
        or item.get("song_id")
        or item.get("unique_id")
        or ""
    )
    queue_id = item.get("id") or item.get("queue_id") or item.get("request_id") or ""
    title = song.get("title") or item.get("title") or ""
    return {
        "source": source,
        "queue_id": str(queue_id or ""),
        "media_id": str(media_id or ""),
        "song_id": str(song_id or ""),
        "unique_id": str(song_id or ""),
        "path": str(path or ""),
        "filename": str(filename or ""),
        "title": str(title or ""),
        "raw": item,
    }


def inspect_upcoming_items() -> list[dict]:
    """
    Defensive read of Azura upcoming/playing-next state.

    Includes /queue plus any playing_next/upcoming fields surfaced by
    nowplaying. Callers should treat queue ids as optional.
    """
    items: list[dict] = []
    for item in fetch_queue():
        items.append(_normalize_queue_item(item, "queue"))
    try:
        np = fetch_nowplaying() or {}
        for key in ("playing_next", "next_playing", "upcoming", "queue"):
            value = np.get(key)
            if isinstance(value, dict):
                items.append(_normalize_queue_item(value, key))
            elif isinstance(value, list):
                for entry in value:
                    items.append(_normalize_queue_item(entry, key))
    except Exception as exc:
        print(f"{_LOG} inspect_upcoming_items error: {exc!r}")
    return items


def _queue_item_matches_request(item: dict, *, media_id: str = "", song_id: str = "", filename: str = "", path: str = "") -> bool:
    media_id = str(media_id or "").strip()
    song_id = str(song_id or "").strip()
    filename = str(filename or "").strip().rsplit("/", 1)[-1]
    path = str(path or "").strip().lstrip("/")
    item_media = str(item.get("media_id") or "").strip()
    item_song = str(item.get("song_id") or item.get("unique_id") or "").strip()
    item_path = str(item.get("path") or "").strip().lstrip("/")
    item_file = str(item.get("filename") or "").strip()
    if media_id and item_media and media_id == item_media:
        return True
    if song_id and item_song and song_id == item_song:
        return True
    if path and item_path and path.lower() == item_path.lower():
        return True
    if filename and item_file and filename.lower() == item_file.lower():
        return True
    if filename and item_path and item_path.lower() == f"requests/{filename}".lower():
        return True
    return False


def remove_queue_items_matching(*, media_id: str = "", song_id: str = "", filename: str = "", path: str = "") -> int:
    """Best-effort removal of Azura upcoming queue rows matching request media."""
    cfg = azura_api_cfg()
    if not cfg:
        return 0
    import requests as req_lib

    removed = 0
    for item in inspect_upcoming_items():
        if not _queue_item_matches_request(
            item,
            media_id=media_id,
            song_id=song_id,
            filename=filename,
            path=path,
        ):
            continue
        queue_id = item.get("queue_id") or ""
        if not queue_id:
            print(
                f"{_LOG} remove_queue_item_match skipped"
                f" reason=no_queue_id item={str(item.get('raw') or item)[:180]!r}"
            )
            continue
        try:
            resp = req_lib.delete(
                f"{cfg['base_url']}/api/station/{cfg['station_id']}/queue/{queue_id}",
                headers=_headers(cfg),
                timeout=10,
            )
            ok = resp.status_code in (200, 204, 404)
            print(
                f"{_LOG} remove_queue_item_match"
                f" queue_id={queue_id!r} media_id={media_id!r}"
                f" song_id={song_id!r} filename={filename!r}"
                f" status={resp.status_code} ok={ok}"
            )
            if ok:
                removed += 1
        except Exception as exc:
            print(f"{_LOG} remove_queue_item_match error queue_id={queue_id!r}: {exc!r}")
    return removed


def remove_queue_items_for_song(unique_id: str) -> int:
    """
    Best-effort removal of queued AzuraCast request entries for a song.

    AzuraCast consumes a request when it starts playing, but if a stale queued
    entry remains after bot-side terminal cleanup, it can replay the same media.
    This scans GET /queue and DELETEs matching queue rows when the API exposes
    a queue item id.
    """
    cfg = azura_api_cfg()
    if not cfg or not unique_id:
        return 0
    import requests as req_lib

    removed = 0
    for item in fetch_queue():
        song = item.get("song") or {}
        item_uid = (
            song.get("unique_id")
            or song.get("id")
            or item.get("song_id")
            or ""
        )
        if str(item_uid) != str(unique_id):
            continue
        queue_id = item.get("id") or item.get("queue_id")
        if not queue_id:
            print(
                f"{_LOG} remove_queue_item skipped uid={unique_id!r}"
                f" reason=no_queue_id item={str(item)[:180]!r}"
            )
            continue
        try:
            resp = req_lib.delete(
                f"{cfg['base_url']}/api/station/{cfg['station_id']}/queue/{queue_id}",
                headers=_headers(cfg),
                timeout=10,
            )
            ok = resp.status_code in (200, 204, 404)
            print(
                f"{_LOG} remove_queue_item"
                f" queue_id={queue_id!r} uid={unique_id!r}"
                f" status={resp.status_code} ok={ok}"
            )
            if ok:
                removed += 1
        except Exception as exc:
            print(f"{_LOG} remove_queue_item error uid={unique_id!r}: {exc!r}")
    return removed


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
                import modules.request_queue as rq
                rq.mark_cleaned(jid)
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


# ─── Dynamic folder / playlist discovery ──────────────────────────────────────

def _norm_pl_name(name: str) -> str:
    """Normalize a playlist/folder name for case-insensitive comparison.
    Strips spaces, hyphens, underscores and lowercases.
    DJSet = djset = DJ Set = dj-set = dj_set
    """
    import re
    return re.sub(r"[\s\-_]+", "", name).lower()


def list_playlists() -> list:
    """
    GET /api/station/{id}/playlists
    Returns list of playlist dicts (id, name, num_songs, is_enabled, …).
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg:
        return []
    try:
        resp = req_lib.get(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/playlists",
            headers=_headers(cfg),
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else data.get("result", [])
        print(f"{_LOG} list_playlists HTTP {resp.status_code}")
    except Exception as exc:
        print(f"{_LOG} list_playlists error: {exc}")
    return []


def find_playlist_by_name(name: str) -> "dict | None":
    """Case-insensitive, space/hyphen-insensitive search for a playlist by name."""
    target = _norm_pl_name(name)
    for pl in list_playlists():
        if _norm_pl_name(pl.get("name", "")) == target:
            return pl
    return None


def create_playlist(name: str) -> "dict | None":
    """
    POST /api/station/{id}/playlist — create a shuffled auto-DJ playlist.
    Returns the created playlist dict, or None on failure.
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg:
        return None
    try:
        resp = req_lib.post(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/playlist",
            json={
                "name":             name,
                "type":             "default",
                "source":           "songs",
                "order":            "shuffle",
                "is_enabled":       True,
                "is_jingle":        False,
                "weight":           3,
                "avoid_duplicates": True,
            },
            headers=_headers(cfg),
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return resp.json()
        print(f"{_LOG} create_playlist '{name}' → HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as exc:
        print(f"{_LOG} create_playlist error: {exc}")
    return None


def list_folder_files(folder_name: str) -> list:
    """
    GET /api/station/{id}/files?currentDirectory=<folder>
    Returns list of media file dicts inside that folder (excludes sub-directories).
    Handles both list and paginated-dict API response shapes.
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg:
        return []
    try:
        resp = req_lib.get(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/files",
            params={"currentDirectory": folder_name, "rowCount": 5000},
            headers=_headers(cfg),
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            rows = data if isinstance(data, list) else data.get("rows", [])
            # Filter: keep actual media files (have unique_id or media subdict)
            # Directories have neither and usually lack a file extension
            result = []
            for r in rows:
                if r.get("unique_id"):
                    result.append(r)
                elif r.get("media"):
                    result.append(r)
                elif r.get("path") and "." in os.path.basename(r.get("path", "")):
                    result.append(r)
            print(f"{_LOG} list_folder_files({folder_name!r}) → {len(result)} files")
            return result
        print(f"{_LOG} list_folder_files({folder_name!r}) HTTP {resp.status_code}")
    except Exception as exc:
        print(f"{_LOG} list_folder_files error: {exc}")
    return []


def count_folder_files(folder_name: str) -> int:
    """Count media files inside a folder in the AzuraCast media library."""
    return len(list_folder_files(folder_name))


def assign_folder_to_playlist(folder_name: str, playlist_id: str) -> bool:
    """
    Batch-assign all media files in a folder to a playlist.
    POST /api/station/{id}/files/batch
      body: {"do":"playlist","playlist":<pid>,"currentDirectory":<folder>}
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg or not playlist_id:
        return False
    try:
        resp = req_lib.post(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/files/batch",
            json={
                "do":               "playlist",
                "playlist":         playlist_id,
                "currentDirectory": folder_name,
            },
            headers=_headers(cfg),
            timeout=30,
        )
        ok = resp.status_code in (200, 204)
        print(
            f"{_LOG} assign_folder folder={folder_name!r} pl={playlist_id}"
            f" → HTTP {resp.status_code} ok={ok}"
        )
        return ok
    except Exception as exc:
        print(f"{_LOG} assign_folder error: {exc}")
    return False


def list_media_folders(parent: str = "") -> list:
    """
    Return folder names under `parent` in the AzuraCast media library.
    Calls GET /api/station/{id}/files?currentDirectory=<parent> and returns
    items that look like directories (no unique_id, no file extension in basename).
    """
    import requests as req_lib
    cfg = azura_api_cfg()
    if not cfg:
        return []
    try:
        resp = req_lib.get(
            f"{cfg['base_url']}/api/station/{cfg['station_id']}/files",
            params={"currentDirectory": parent, "rowCount": 1000},
            headers=_headers(cfg),
            timeout=15,
        )
        if resp.status_code == 200:
            data    = resp.json()
            rows    = data if isinstance(data, list) else data.get("rows", [])
            folders = []
            for r in rows:
                if r.get("unique_id") or r.get("media"):
                    continue
                name = r.get("name") or os.path.basename(r.get("path", ""))
                if name and "." not in name:
                    folders.append(name)
            print(f"{_LOG} list_media_folders({parent!r}) → {len(folders)} folders")
            return folders
        print(f"{_LOG} list_media_folders({parent!r}) HTTP {resp.status_code}")
    except Exception as exc:
        print(f"{_LOG} list_media_folders error: {exc}")
    return []


def list_all_media() -> list:
    """
    Return every media file in the AzuraCast library by:
      1. Listing all folders from the root.
      2. Fetching files from root + each folder (+ one level of subfolders).

    Uses the existing list_folder_files / list_media_folders helpers so the
    same API shape handling is reused.  Returns a flat list of file dicts.
    """
    all_files: list = []
    seen_paths: set = set()

    def _add(rows: list) -> None:
        for r in rows:
            p = r.get("path", "")
            if p and p not in seen_paths:
                seen_paths.add(p)
                all_files.append(r)

    # Root-level files (some libraries keep files directly under media/)
    _add(list_folder_files(""))

    # Top-level folders (EDM, BGCNights, Requests, …)
    top_folders = list_media_folders("")
    for folder in top_folders:
        fname = folder if isinstance(folder, str) else ""
        if not fname:
            continue
        _add(list_folder_files(fname))
        # One level deeper
        sub_folders = list_media_folders(fname)
        for sub in sub_folders:
            sname = sub if isinstance(sub, str) else ""
            if sname:
                _add(list_folder_files(sname))

    print(f"{_LOG} list_all_media → {len(all_files)} total files")
    return all_files


def scan_vibe_sources() -> dict:
    """
    Discover all vibe-worthy playlists and media folders from AzuraCast.

    Phase 1 — scans all station playlists; skips Requests and empty playlists.
    Phase 2 — scans root media folders; only adds folders whose normalized name
               is not already covered by a playlist.

    Returns:
        {
            "vibes": {
                "<norm_key>": {
                    "display":     "<Original Name>",
                    "playlist_id": "<id>" | None,
                    "song_count":  <int>,
                    "source":      "playlist" | "folder",
                },
                ...
            },
            "scanned_at": <float>,
        }
    """
    import time as _time
    req_pid: str   = requests_playlist_id()
    seen_keys: set = set()
    vibes: dict    = {}

    # ── Phase 1: existing AzuraCast playlists ────────────────────────────────
    for pl in list_playlists():
        pl_id   = str(pl.get("id", ""))
        pl_name = (pl.get("name") or "").strip()
        count   = int(pl.get("num_songs") or 0)
        if not pl_id or not pl_name:
            continue
        if pl_id == req_pid:
            continue
        if count == 0:
            continue
        key = _norm_pl_name(pl_name)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        vibes[key] = {
            "display":     pl_name,
            "playlist_id": pl_id,
            "song_count":  count,
            "source":      "playlist",
        }
        print(f"{_LOG} scan_vibe: playlist '{pl_name}' key={key!r} songs={count}")

    # ── Phase 2: media library folders not yet covered ───────────────────────
    for folder_name in list_media_folders():
        key = _norm_pl_name(folder_name)
        if not key or key in seen_keys:
            continue
        count = count_folder_files(folder_name)
        if count == 0:
            continue
        seen_keys.add(key)
        vibes[key] = {
            "display":     folder_name,
            "playlist_id": None,
            "song_count":  count,
            "source":      "folder",
        }
        print(f"{_LOG} scan_vibe: folder '{folder_name}' key={key!r} songs={count}")

    result = {"vibes": vibes, "scanned_at": _time.time()}
    print(f"{_LOG} scan_vibe_sources complete → {len(vibes)} vibes discovered")
    return result


def switch_vibe_to(target_playlist_id: str, all_vibe_pids: "list[str]") -> dict:
    """
    Enable target_playlist_id; disable every other playlist ID in all_vibe_pids.
    The Requests playlist (AZURA_PLAYLIST_ID) is NEVER touched.
    all_vibe_pids should include every known vibe playlist ID (cache + env vars).
    """
    req_pid  = requests_playlist_id()
    disabled = []
    errors   = []

    for pid in all_vibe_pids:
        pid = str(pid or "").strip()
        if not pid or pid == req_pid or pid == target_playlist_id:
            continue
        ok = set_playlist_enabled(pid, False)
        if ok:
            disabled.append(pid)
        else:
            errors.append(pid)

    ok = set_playlist_enabled(target_playlist_id, True)
    if not ok:
        errors.append(target_playlist_id)

    result = "ok" if not errors else "partial"
    print(
        f"{_LOG} stage=switch_vibe_to"
        f" target={target_playlist_id!r}"
        f" disabled={len(disabled)}"
        f" result={result!r}"
        f" errors={errors!r}"
    )
    return {
        "status":   result,
        "enabled":  target_playlist_id,
        "disabled": disabled,
        "errors":   errors,
    }


def switch_vibe_dynamic(target_playlist_id: str) -> dict:
    """
    Enable target_playlist_id; disable all env-var-configured vibe playlists.
    The Requests playlist is NEVER touched.
    Used for dynamically discovered folder-based vibes (e.g. DJSet).
    """
    req_id   = requests_playlist_id()
    disabled = []
    errors   = []

    for v in VIBE_NAMES:
        pid = vibe_playlist_id(v)
        if not pid or pid == req_id or pid == target_playlist_id:
            continue
        ok = set_playlist_enabled(pid, False)
        if ok:
            disabled.append(pid)
        else:
            errors.append(pid)

    ok = set_playlist_enabled(target_playlist_id, True)
    if not ok:
        errors.append(target_playlist_id)

    result = "ok" if not errors else "partial"
    print(
        f"{_LOG} stage=vibe_switch_dynamic"
        f" target={target_playlist_id!r}"
        f" disabled={disabled!r}"
        f" result={result!r}"
        f" errors={errors!r}"
    )
    return {
        "status":   result,
        "enabled":  target_playlist_id,
        "disabled": disabled,
        "errors":   errors,
    }
