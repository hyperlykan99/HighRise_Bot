"""
modules/local_replay.py
-----------------------
SAFE TEMP REQUEST-COPY PLAYBACK

Feature-flagged — BOTH env vars must be exactly "true" (case-insensitive):
    LOCAL_REPLAY_ENABLED=false
    LOCAL_REPLAY_ALLOW_COPY=false

Commands (owner/admin only during testing):
    !playfavlocal <#>       — copy a local favorite to UUID temp, queue it
    !localreplaytest <#>    — alias for !playfavlocal (test-mode label)
    !localreplaystatus      — list recent local replay jobs
    !localreplaycleanup     — manually purge stale temp files

Safe flow
---------
1.  Validate favorite exists and is a local (non-YouTube) file.
2.  GET AzuraCast file record → confirm source exists, obtain VPS path.
3.  SFTP: stat source path to verify file is present on VPS.
4.  Generate temp UUID filename:  tmp_replay_{hex12}.mp3
5.  SFTP: stream-copy source → {requests_folder}/tmp_replay_{hex12}.mp3
        Source root: AZURA_MEDIA_SFTP_PATH env var (if set),
                     else parent dir of AZURA_SFTP_PATH (derived default).
6.  AzuraCast: rescan requests folder, sleep 2 s for indexing.
7.  AzuraCast: search_media(temp_filename) → extract unique_id.
8.  AzuraCast: submit_request(unique_id) — queue the temp copy only.
9.  Track lifecycle in local_replay_jobs (lazy-created) DB table.
10. Cleanup: stale temps (>2 h) deleted at start of next !playfavlocal,
             or on demand via !localreplaycleanup.

ABSOLUTE RULES
--------------
- Failures NEVER crash the bot or affect the main queue / AutoDJ.
- No startup imports; all imports are deferred inside call sites.
- No background polling tasks created by this module.
- NEVER delete or queue original library files — only tmp_replay_* files.
- Both feature flags must be "true" or every handler exits silently.
"""
from __future__ import annotations

import os
import uuid
import asyncio
import shutil
from datetime import datetime, timezone

_LOG = "[LOCAL_REPLAY]"
_SAFE_TEMP_PREFIXES = ("tmp_replay_", "local_request_")


def _safe_temp_filename(filename: str) -> bool:
    base = os.path.basename(filename or "")
    return bool(base and base == filename and base.startswith(_SAFE_TEMP_PREFIXES))


def _radio_event(event: str, **fields: object) -> None:
    try:
        import modules.radio_diagnostics as _diag
        _diag.log_radio_event(event, **fields)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

def _flags_enabled() -> tuple[bool, str]:
    """Returns (True, '') only when both flags are explicitly 'true'."""
    if os.environ.get("LOCAL_REPLAY_ENABLED", "false").strip().lower() != "true":
        return False, "LOCAL_REPLAY_ENABLED is not 'true'"
    if os.environ.get("LOCAL_REPLAY_ALLOW_COPY", "false").strip().lower() != "true":
        return False, "LOCAL_REPLAY_ALLOW_COPY is not 'true'"
    return True, ""


# ---------------------------------------------------------------------------
# DB schema — lazy, never raises
# ---------------------------------------------------------------------------

def _ensure_schema() -> None:
    try:
        import database as _db
        with _db.db_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS local_replay_jobs (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id          TEXT,
                    username         TEXT,
                    fav_title        TEXT,
                    source_path      TEXT,
                    temp_filename    TEXT UNIQUE,
                    azura_unique_id  TEXT,
                    azura_file_id    TEXT DEFAULT '',
                    status           TEXT DEFAULT 'queued',
                    created_at       TEXT,
                    queued_at        TEXT,
                    cleanup_complete TEXT
                )
            """)
            # Idempotent migration for existing DBs that predate azura_file_id
            for _col_sql in (
                "ALTER TABLE local_replay_jobs ADD COLUMN azura_file_id TEXT DEFAULT ''",
                "ALTER TABLE local_replay_jobs ADD COLUMN yt_request_job_id INTEGER",
                "ALTER TABLE local_replay_jobs ADD COLUMN playback_started TEXT",
            ):
                try:
                    conn.execute(_col_sql)
                except Exception:
                    pass  # column already exists
    except Exception as _e:
        print(f"{_LOG} schema init (non-fatal): {_e!r}")


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_job(
    user_id: str, username: str, fav_title: str,
    source_path: str, temp_filename: str,
    azura_unique_id: str = "", azura_file_id: str = "",
) -> None:
    try:
        _ensure_schema()
        import database as _db
        with _db.db_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO local_replay_jobs "
                "(user_id, username, fav_title, source_path, temp_filename, "
                " azura_unique_id, azura_file_id, status, created_at, queued_at) "
                "VALUES (?,?,?,?,?,?,?,'queued',?,?)",
                (user_id, username, fav_title, source_path,
                 temp_filename, azura_unique_id, azura_file_id, _ts(), _ts()),
            )
    except Exception as _e:
        print(f"{_LOG} insert_job error: {_e!r}")


def _update_status(temp_filename: str, status: str, cleanup_ts: str = "") -> None:
    try:
        import database as _db
        with _db.db_conn() as conn:
            if cleanup_ts:
                conn.execute(
                    "UPDATE local_replay_jobs "
                    "SET status=?, cleanup_complete=? WHERE temp_filename=?",
                    (status, cleanup_ts, temp_filename),
                )
            else:
                conn.execute(
                    "UPDATE local_replay_jobs SET status=? WHERE temp_filename=?",
                    (status, temp_filename),
                )
    except Exception as _e:
        print(f"{_LOG} update_status error: {_e!r}")


def _list_jobs(limit: int = 10) -> list[dict]:
    try:
        _ensure_schema()
        import database as _db
        with _db.db_conn() as conn:
            rows = conn.execute(
                "SELECT id, username, fav_title, temp_filename, status, created_at "
                "FROM local_replay_jobs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"id": r[0], "username": r[1], "title": r[2],
             "temp": r[3], "status": r[4], "created": r[5]}
            for r in rows
        ]
    except Exception:
        return []


def _local_map_row_by_file_id(azura_file_id: str) -> dict:
    try:
        import database as _db
        with _db.db_conn() as conn:
            row = conn.execute(
                "SELECT azura_file_id, unique_id, title, artist, path, filename "
                "FROM local_media_map WHERE azura_file_id=? LIMIT 1",
                (str(azura_file_id or ""),),
            ).fetchone()
        if row:
            return {
                "azura_file_id": row[0],
                "unique_id": row[1],
                "title": row[2],
                "artist": row[3],
                "path": row[4],
                "filename": row[5],
            }
    except Exception as exc:
        print(f"{_LOG} local_media_map fid lookup error: {exc!r}")
    return {}


def _register_as_yt_request_job(
    user_id: str, username: str, title: str,
    temp_filename: str, azura_file_id: str, azura_song_id: str,
    coins_charged: int = 0, payment_type: str = "free",
) -> int:
    """
    Insert a yt_request_jobs row (status='ready', source_type='local_replay')
    so playback_engine can detect this file in now-playing and run the normal
    _delete_request_file cleanup lifecycle after play.

    Returns the new row id (0 on error).
    """
    try:
        import modules.request_queue as rq
        return rq.create_request(
            user_id=user_id,
            username=username,
            url="",
            title=title,
            status="ready",
            filename=temp_filename,
            azura_file_id=azura_file_id,
            azura_song_id=azura_song_id,
            coins_charged=int(coins_charged or 0),
            payment_type=payment_type or "free",
            source_type="local_replay",
        )
    except Exception as exc:
        print(f"{_LOG} _register_as_yt_request_job error: {exc!r}")
        return 0


def _link_yt_job(temp_filename: str, yt_job_id: int) -> None:
    """Cross-reference: write yt_request_jobs.id back to local_replay_jobs row."""
    if not yt_job_id:
        return
    try:
        import database as _db
        with _db.db_conn() as conn:
            conn.execute(
                "UPDATE local_replay_jobs SET yt_request_job_id=? WHERE temp_filename=?",
                (yt_job_id, temp_filename),
            )
    except Exception as exc:
        print(f"{_LOG} _link_yt_job error: {exc!r}")


def _fetch_job_by_temp(temp_basename: str) -> "dict | None":
    """
    Return the local_replay_jobs row whose temp_filename matches temp_basename
    (exact match or suffix match).  Returns None on no-match or error.
    """
    try:
        _ensure_schema()
        import database as _db
        with _db.db_conn() as conn:
            row = conn.execute(
                "SELECT id, user_id, username, fav_title, azura_file_id,"
                "       azura_unique_id, yt_request_job_id, status"
                " FROM local_replay_jobs"
                " WHERE temp_filename=? OR temp_filename LIKE ?"
                " ORDER BY id DESC LIMIT 1",
                (temp_basename, f"%{temp_basename}"),
            ).fetchone()
        if row:
            return {
                "id": row[0], "user_id": row[1], "username": row[2],
                "fav_title": row[3], "azura_file_id": row[4],
                "azura_unique_id": row[5], "yt_request_job_id": row[6],
                "status": row[7],
            }
    except Exception as exc:
        print(f"{_LOG} _fetch_job_by_temp error: {exc!r}")
    return None


def _mark_job_playing(temp_basename: str) -> None:
    """
    Set status='playing' and playback_started=NOW.
    Idempotent — only updates when status is not already 'playing'.
    Matches on temp_filename exact or suffix.
    """
    try:
        import database as _db
        with _db.db_conn() as conn:
            conn.execute(
                "UPDATE local_replay_jobs"
                " SET status='playing', playback_started=datetime('now')"
                " WHERE (temp_filename=? OR temp_filename LIKE ?)"
                "   AND status != 'playing'",
                (temp_basename, f"%{temp_basename}"),
            )
    except Exception as exc:
        print(f"{_LOG} _mark_job_playing error: {exc!r}")


def _mark_job_cleanup_complete(temp_basename: str) -> None:
    """Set status='cleaned' and cleanup_complete=NOW on the matching row."""
    try:
        import database as _db
        with _db.db_conn() as conn:
            conn.execute(
                "UPDATE local_replay_jobs"
                " SET status='cleaned', cleanup_complete=datetime('now')"
                " WHERE temp_filename=? OR temp_filename LIKE ?",
                (temp_basename, f"%{temp_basename}"),
            )
    except Exception as exc:
        print(f"{_LOG} _mark_job_cleanup_complete error: {exc!r}")


# ---------------------------------------------------------------------------
# SFTP helpers — all deferred imports, never raise
# ---------------------------------------------------------------------------

def _get_sftp_cfg() -> dict:
    try:
        from modules.config_store import sftp_cfg
        return sftp_cfg()
    except Exception:
        return {}


# Ordered fallback roots tried when AZURA_MEDIA_SFTP_PATH doesn't work.
# The SFTP user may be chrooted or see a different filesystem layout.
_SFTP_FALLBACK_ROOTS: list[str] = [
    "/var/lib/docker/volumes/azuracast_station_data/_data/chilltopia/media",
    "/var/lib/docker/volumes/azuracast_station_data/data/chilltopia/media",
    "/var/azuracast/stations/chilltopia/media",
    "chilltopia/media",
    "media",
    "",  # bare relative path — last resort
]


def _dedupe_paths(paths: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for path in paths:
        clean = str(path or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def _media_root_candidates() -> list[str]:
    roots: list[str] = []
    env_root = os.environ.get("AZURA_MEDIA_SFTP_PATH", "").strip()
    if env_root:
        roots.append(env_root)
        if "/_data/" in env_root:
            roots.append(env_root.replace("/_data/", "/data/"))
        if "/data/" in env_root:
            roots.append(env_root.replace("/data/", "/_data/"))
    roots.extend(_SFTP_FALLBACK_ROOTS)
    return _dedupe_paths([r for r in roots if r])


def _relative_path_candidates(azura_file_path: str, filename: str = "") -> list[str]:
    rel = str(azura_file_path or "").strip().lstrip("/")
    fname = os.path.basename(str(filename or "").strip()) or os.path.basename(rel)
    paths = [rel]
    if fname and fname != rel:
        parent = os.path.dirname(rel)
        if parent:
            paths.append(f"{parent}/{fname}")
        paths.append(fname)
    return _dedupe_paths(paths)


def _log_source_candidate(kind: str, path: str, exists: bool) -> None:
    print(f"{_LOG} source_path_candidate kind={kind} path={path!r} exists={str(bool(exists)).lower()}")


def _find_by_basename(root: str, basenames: list[str]) -> str | None:
    wanted = {os.path.basename(b) for b in basenames if os.path.basename(b)}
    if not wanted or not root or not os.path.isdir(root):
        return None
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            # Avoid wandering into request temp/history folders while looking
            # for immutable library media.
            dirnames[:] = [d for d in dirnames if d.lower() not in {"requests", ".trash", "trash"}]
            for fname in filenames:
                if fname in wanted:
                    return os.path.join(dirpath, fname)
    except Exception as exc:
        print(f"{_LOG} basename_search error root={root!r} error={exc!r}")
    return None


def _resolve_local_source_path(azura_file_path: str, filename: str = "") -> str | None:
    rels = _relative_path_candidates(azura_file_path, filename)
    candidates: list[str] = []
    for rel in rels:
        if os.path.isabs(rel):
            candidates.append(rel)
    for root in _media_root_candidates():
        for rel in rels:
            candidates.append(rel if os.path.isabs(rel) else os.path.join(root, rel))

    for path in _dedupe_paths(candidates):
        exists = os.path.isfile(path)
        _log_source_candidate("local", path, exists)
        if exists:
            print(f"{_LOG} source_path_resolved method=local path={path!r}")
            return path

    found = _find_by_basename(_media_root_candidates()[0] if _media_root_candidates() else "", rels)
    if not found:
        for root in _media_root_candidates()[1:]:
            found = _find_by_basename(root, rels)
            if found:
                break
    if found:
        _log_source_candidate("local_basename_search", found, True)
        print(f"{_LOG} source_path_resolved method=local path={found!r}")
        return found

    print(f"{_LOG} source_path_missing original={azura_file_path!r} base={os.path.basename(filename or azura_file_path)!r}")
    return None


def _local_requests_dir_for_source(source_path: str) -> str | None:
    src = os.path.abspath(source_path)
    roots = sorted(_media_root_candidates(), key=len, reverse=True)
    for root in roots:
        abs_root = os.path.abspath(root)
        try:
            if os.path.commonpath([src, abs_root]) == abs_root:
                return os.path.join(abs_root, os.environ.get("AZURA_REQUESTS_FOLDER", "Requests").strip("/") or "Requests")
        except Exception:
            continue
    env_root = os.environ.get("AZURA_MEDIA_SFTP_PATH", "").strip()
    if env_root:
        return os.path.join(env_root, os.environ.get("AZURA_REQUESTS_FOLDER", "Requests").strip("/") or "Requests")
    return None


def _local_copy_to_temp(azura_file_path: str, temp_filename: str, filename: str = "") -> bool:
    if not _safe_temp_filename(temp_filename):
        print(f"{_LOG} local copy refused — unsafe temp filename: {temp_filename!r}")
        return False
    source = _resolve_local_source_path(azura_file_path, filename)
    if not source:
        return False
    dest_dir = _local_requests_dir_for_source(source)
    if not dest_dir:
        print(f"{_LOG} local copy unavailable — no requests directory root resolved")
        return False
    dest = os.path.join(dest_dir, temp_filename)
    if os.path.exists(dest) and not _safe_temp_filename(os.path.basename(dest)):
        print(f"{_LOG} local copy refused — destination not temp-safe: {dest!r}")
        return False
    try:
        os.makedirs(dest_dir, exist_ok=True)
        print(f"{_LOG} local_temp_copy_started source={source!r} dest={dest!r}")
        shutil.copyfile(source, dest)
        size = os.path.getsize(dest)
        print(f"{_LOG} local_temp_copy_done bytes={size}")
        return True
    except Exception as exc:
        print(f"{_LOG} local copy failed; falling back to SFTP: {exc!r}")
        return False


def _copy_to_temp(azura_file_path: str, temp_filename: str, filename: str = "") -> tuple[bool, str]:
    """
    Copy a library file to a temp request file.

    Returns (ok, reason):
      ok                         — local or SFTP copy succeeded
      local_source_missing       — no local/SFTP source path resolved
      local_temp_copy_failed     — source resolved but copy failed
    """
    if _local_copy_to_temp(azura_file_path, temp_filename, filename):
        return True, "ok"

    resolved_sftp, _trial_log = _sftp_stat_exists_with_log(azura_file_path, filename)
    if not resolved_sftp:
        return False, "local_source_missing"

    if _sftp_copy_to_temp(azura_file_path, temp_filename, filename):
        return True, "ok"
    return False, "local_temp_copy_failed"


def _build_path_candidates(azura_file_path: str, filename: str = "") -> list[str]:
    """
    Return an ordered list of absolute/relative SFTP paths to probe.

    Order:
      1. AZURA_MEDIA_SFTP_PATH env var  (if set)
      2–6. _SFTP_FALLBACK_ROOTS
    Duplicates are silently skipped.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        if path not in seen:
            seen.add(path)
            candidates.append(path)

    rels = _relative_path_candidates(azura_file_path, filename)
    for rel in rels:
        _add(rel)
        _add(f"media/{rel}")
        _add(f"chilltopia/media/{rel}")
    for root in _media_root_candidates():
        for rel in rels:
            _add(rel if os.path.isabs(rel) else f"{root.rstrip('/')}/{rel}")

    return candidates


def _sftp_resolve_path(
    sftp,
    candidates: list[str],
) -> "tuple[str | None, list[tuple[int, str, bool]]]":
    """
    Try each candidate with sftp.stat() on an already-open SFTP session.

    Returns:
      resolved  — first path where stat succeeded, or None
      trial_log — [(1-based-index, path, ok), …] for every path tried
    Stops at the first success.
    """
    trial_log: list[tuple[int, str, bool]] = []
    resolved: "str | None" = None
    for i, path in enumerate(candidates, 1):
        try:
            sftp.stat(path)
            trial_log.append((i, path, True))
            _log_source_candidate("sftp", path, True)
            resolved = path
            print(f"{_LOG} source_path_resolved method=sftp path={path!r}")
            break
        except (IOError, FileNotFoundError):
            trial_log.append((i, path, False))
            _log_source_candidate("sftp", path, False)
        except Exception as exc:
            trial_log.append((i, path, False))
            _log_source_candidate("sftp", path, False)
            print(f"{_LOG} resolve candidate {i} error: {exc!r}")
    return resolved, trial_log


def _sftp_copy_to_temp(azura_file_path: str, temp_filename: str, filename: str = "") -> bool:
    """
    Open an SFTP session, resolve the source path via candidate probing,
    and stream-copy to requests_folder/temp_filename.

    Returns True on success; False on any error (logged, never re-raised).
    NEVER modifies the original file.
    """
    try:
        import paramiko
    except ImportError:
        print(f"{_LOG} paramiko not installed — copy aborted")
        return False

    cfg = _get_sftp_cfg()
    if not cfg.get("host") or not cfg.get("user"):
        print(f"{_LOG} SFTP not configured — copy aborted")
        return False

    candidates   = _build_path_candidates(azura_file_path, filename)
    dest_path    = f"{cfg['folder'].rstrip('/')}/{temp_filename}"

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

        resolved, trial_log = _sftp_resolve_path(sftp, candidates)
        if not resolved:
            tried = ", ".join(p for _, p, _ in trial_log)
            print(f"{_LOG} source not found — tried: {tried}")
            print(f"{_LOG} source_path_missing original={azura_file_path!r} base={os.path.basename(filename or azura_file_path)!r}")
            return False

        print(f"{_LOG} copying {resolved!r} → {dest_path!r}")
        with sftp.file(resolved, "rb") as src_f:
            data = src_f.read()
        with sftp.file(dest_path, "wb") as dst_f:
            dst_f.write(data)
        print(f"{_LOG} temp copy created: {temp_filename} ({len(data):,} bytes)")
        return True

    except Exception as exc:
        print(f"{_LOG} sftp_copy_to_temp error: {exc!r}")
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


def _sftp_stat_exists_with_log(
    azura_file_path: str,
    filename: str = "",
) -> "tuple[str | None, list[tuple[int, str, bool]]]":
    """
    Probe all candidate paths via SFTP stat without copying anything.

    Returns (resolved_path or None, trial_log).
    Used by !localreplaytest debug whisper only.
    """
    try:
        import paramiko
    except ImportError:
        return None, []

    cfg = _get_sftp_cfg()
    if not cfg.get("host") or not cfg.get("user"):
        return None, []

    candidates = _build_path_candidates(azura_file_path, filename)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sftp = None
    try:
        ssh.connect(
            hostname=cfg["host"], port=cfg["port"],
            username=cfg["user"], password=cfg["passwd"],
            timeout=15, look_for_keys=False, allow_agent=False,
        )
        sftp = ssh.open_sftp()
        return _sftp_resolve_path(sftp, candidates)
    except Exception as exc:
        print(f"{_LOG} stat_exists_with_log connect error: {exc!r}")
        return None, []
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


def _sftp_delete_temp(temp_filename: str) -> bool:
    """
    Delete a temp replay file from the requests folder.
    Hard safety check: only deletes files using the local request temp prefixes.
    """
    if not _safe_temp_filename(temp_filename):
        print(f"{_LOG} delete refused — not a temp file: {temp_filename!r}")
        _radio_event(
            "cleanup_safety_skip",
            temp_path=temp_filename,
            source_path="",
        )
        return False
    try:
        from modules.azuracast_controller import sftp_delete_file
        ok = sftp_delete_file(temp_filename)
        print(f"{_LOG} sftp_delete_temp {'✓' if ok else '✗'}: {temp_filename}")
        return ok
    except Exception as exc:
        print(f"{_LOG} sftp_delete_temp error: {exc!r}")
        return False


# ---------------------------------------------------------------------------
# Stale cleanup — on-demand only, no background task
# ---------------------------------------------------------------------------

def _cleanup_stale_temps() -> int:
    """
    Find old local_replay_jobs whose playback was confirmed and cleanup was
    requested, delete their SFTP temp file (safety-checked), mark them done.

    Returns count of entries cleaned.  Never raises.
    """
    try:
        _ensure_schema()
        import database as _db
        with _db.db_conn() as conn:
            rows = conn.execute(
                "SELECT temp_filename, COALESCE(azura_file_id,'') "
                "FROM local_replay_jobs "
                "WHERE status IN ('cleanup_pending') "
                "  AND (cleanup_complete IS NULL OR cleanup_complete='') "
                "  AND playback_started IS NOT NULL "
                "  AND created_at < datetime('now','-2 hours')",
            ).fetchall()
    except Exception as exc:
        print(f"{_LOG} cleanup query error: {exc!r}")
        return 0

    cleaned = 0
    for (tfn, fid) in rows:
        if not tfn or not _safe_temp_filename(tfn):
            _radio_event("cleanup_safety_skip", temp_path=tfn or "", source_path="")
            continue
        # Remove from all playlists before SFTP delete so AutoDJ
        # cannot replay the temp file while the delete is in flight.
        if fid:
            try:
                from modules.azuracast_controller import clear_file_playlists
                clear_file_playlists(fid)
                print(f"{_LOG} cleanup: cleared playlists for file_id={fid!r}")
            except Exception as _cex:
                print(f"{_LOG} cleanup clear_playlists error: {_cex!r}")
        ok = _sftp_delete_temp(tfn)
        if ok:
            _update_status(tfn, "done", _ts())
            cleaned += 1
    return cleaned


# ---------------------------------------------------------------------------
# Backwards-compat stubs (existing callers in other modules, if any)
# ---------------------------------------------------------------------------

async def queue_local_copy(
    user_id: str = "", username: str = "", title: str = "",
    artist: str = "", azura_file_id: str = "", azura_song_id: str = "",
) -> tuple[bool, str]:
    """Stub kept for backwards compat — use handle_playfavlocal instead."""
    print(f"{_LOG} queue_local_copy called — local replay is feature-flagged")
    return False, "use !playfavlocal command"


def queue_local_copy_sync(
    user_id: str = "", username: str = "", title: str = "",
    artist: str = "", azura_file_id: str = "", azura_song_id: str = "",
) -> tuple[bool, str]:
    """Stub kept for backwards compat."""
    return False, "use !playfavlocal command"


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# queue_local_fav — called from handle_playfav for local AzuraCast favorites
# ---------------------------------------------------------------------------

async def queue_local_fav(
    bot, user, fav: dict, pos: int, credit_consumed: bool = False,
    coins_charged: int = 0, payment_type: str = "free", queue_position: int = 0,
    staff_free: bool = False, plays_left: "int | None" = None,
) -> None:
    """
    Queue a local (non-YouTube) AzuraCast favorite as a local replay.

    Called from handle_playfav after credit/cooldown/capacity checks are done.
    Runs the full SFTP-copy → rescan → playlist-assign → yt_request_jobs pipeline
    silently (no debug whispers).

    On any failure: refunds music request credit and coins already charged, then
    whispers a clean error to the user.
    On success: whispers the same "Added to queue" shape as YouTube requests.
    """
    uid   = user.id
    uname = user.username

    async def _w(msg: str) -> None:
        try:
            await bot.highrise.send_whisper(uid, msg[:249])
        except Exception:
            pass

    async def _fail(msg: str, request_id: int = 0) -> None:
        if request_id:
            try:
                import modules.request_queue as _rq
                _rq.mark_failed(request_id, "local_replay_failed")
            except Exception:
                pass
        if credit_consumed:
            try:
                import modules.music_credits as _mc2
                _mc2.refund_credit(uid, uname)
            except Exception:
                pass
        if coins_charged > 0:
            try:
                import modules.payment_service as _ps
                _ps.refund(uid, coins_charged, "local_replay_failed")
            except Exception as exc:
                print(f"{_LOG} queue_local_fav refund error: {exc!r}")
        await _w(msg)

    ok, _reason = _flags_enabled()
    if not ok:
        await _fail("🔒 Local replay is currently unavailable.")
        return

    fav_title  = (fav.get("title")         or "?")[:40]
    fav_artist = (fav.get("artist")        or "").strip()
    azura_fid  = (fav.get("azura_file_id") or "").strip()

    if not azura_fid:
        await _fail(f"❌ No library file linked to '{fav_title}'. Resave with !fav.")
        return

    try:
        import modules.request_queue as rq
        request_id = rq.create_request(
            user_id=uid,
            username=uname,
            url="",
            title=fav_title,
            status="processing",
            filename="",
            azura_file_id="",
            azura_song_id="",
            coins_charged=int(coins_charged or 0),
            payment_type=payment_type or "free",
            source_type="local_replay",
        )
    except Exception as exc:
        print(f"{_LOG} queue_local_fav create_request error: {exc!r}")
        request_id = 0

    if not request_id:
        await _fail(f"❌ Could not add '{fav_title}' to the queue. Try again.")
        return

    if plays_left is None and not staff_free:
        try:
            import modules.music_credits as _mc
            plays_left = _mc.get_credits(uid, uname)["total"]
        except Exception:
            plays_left = None
    await _w(rq.render_added_to_queue_message(
        title=fav_title,
        artist=fav_artist,
        position=queue_position,
        staff_free=staff_free,
        plays_left=plays_left,
    ))
    asyncio.create_task(
        _prepare_local_fav_request(
            bot=bot,
            user=user,
            fav=fav,
            request_id=request_id,
            fav_title=fav_title,
            fav_artist=fav_artist,
            azura_fid=azura_fid,
            credit_consumed=credit_consumed,
            coins_charged=coins_charged,
            payment_type=payment_type,
        ),
        name=f"local_replay_prepare_{request_id}",
    )


async def _prepare_local_fav_request(
    bot, user, fav: dict, request_id: int, fav_title: str, fav_artist: str,
    azura_fid: str, credit_consumed: bool = False, coins_charged: int = 0,
    payment_type: str = "free",
) -> None:
    uid = user.id
    uname = user.username

    async def _w(msg: str) -> None:
        try:
            await bot.highrise.send_whisper(uid, msg[:249])
        except Exception:
            pass

    async def _fail(msg: str, reason: str = "local_replay_failed",
                    temp_filename: str = "", source_path: str = "") -> None:
        try:
            import modules.request_queue as _rq
            _rq.mark_failed(request_id, reason)
        except Exception:
            pass
        if temp_filename:
            _update_status(temp_filename, "failed")
        if credit_consumed:
            try:
                import modules.music_credits as _mc2
                _mc2.refund_credit(uid, uname)
            except Exception:
                pass
        if coins_charged > 0:
            try:
                import modules.payment_service as _ps
                _ps.refund(uid, coins_charged, reason)
            except Exception as exc:
                print(f"{_LOG} queue_local_fav refund error: {exc!r}")
        _radio_event(
            "cleanup_safety_skip" if reason == "local_cleanup_safety_skip" else "request_failed_refunded",
            request_id=request_id,
            user_id=uid,
            username=uname,
            title=fav_title,
            source_type="local_replay",
            temp_path=temp_filename,
            source_path=source_path,
        )
        await _w(msg)

    def _request_is_terminal() -> bool:
        try:
            import modules.request_queue as _rq
            return _rq.is_terminal_job(request_id)
        except Exception:
            return False

    loop = asyncio.get_running_loop()
    azura_file_rec: dict = {}

    # ── Get AzuraCast file record ─────────────────────────────────────────────
    try:
        from modules.azuracast_controller import get_media_file
        azura_file_rec = await loop.run_in_executor(None, get_media_file, azura_fid) or {}
    except Exception as exc:
        print(f"{_LOG} queue_local_fav get_media_file error: {exc!r}")
    if azura_fid:
        map_by_fid = await loop.run_in_executor(None, _local_map_row_by_file_id, azura_fid)
        if map_by_fid:
            azura_file_rec = {**map_by_fid, **azura_file_rec}
            if not azura_file_rec.get("filename") and map_by_fid.get("filename"):
                azura_file_rec["filename"] = map_by_fid["filename"]

    # Fallback: local_media_map search
    if not azura_file_rec:
        try:
            from modules.local_media_map import match_from_map, _fav_backfill
            map_row, map_status = match_from_map(fav_title, fav_artist)
            if map_status == "ok" and map_row:
                _fav_backfill(fav["id"], map_row["azura_file_id"], map_row["unique_id"])
                azura_file_rec = {
                    "path":      map_row["path"],
                    "filename":  map_row.get("filename", ""),
                    "unique_id": map_row["unique_id"],
                    "id":        map_row["azura_file_id"],
                }
        except Exception as exc:
            print(f"{_LOG} queue_local_fav map_search error: {exc!r}")

    if not azura_file_rec:
        await _fail(f"❌ '{fav_title}' not found in library. It may have been removed.",
                    "local_source_missing")
        return

    azura_file_path = (azura_file_rec.get("path") or "").strip()
    azura_filename = (azura_file_rec.get("filename") or azura_file_rec.get("name") or "").strip()
    if not azura_file_path:
        await _fail(f"❌ AzuraCast record has no path for '{fav_title}'.",
                    "local_source_path_missing")
        return

    # ── Stale cleanup + SFTP copy ─────────────────────────────────────────────
    try:
        await loop.run_in_executor(None, _cleanup_stale_temps)
    except Exception:
        pass

    temp_filename = f"tmp_replay_{request_id}_{uuid.uuid4().hex[:10]}.mp3"
    try:
        import modules.request_queue as rq
        rq.update_job_fields(request_id, filename=temp_filename, status="processing")
    except Exception:
        pass
    _insert_job(
        user_id=uid, username=uname,
        fav_title=fav_title, source_path=azura_file_path,
        temp_filename=temp_filename,
    )
    _link_yt_job(temp_filename, request_id)
    _update_status(temp_filename, "processing")
    _radio_event(
        "local_temp_copy_started",
        request_id=request_id,
        user_id=uid,
        username=uname,
        title=fav_title,
        source_type="local_replay",
        azura_file_id="",
        azura_song_id="",
        temp_path=temp_filename,
        source_path=azura_file_path,
    )
    copy_ok = False
    copy_reason = "local_temp_copy_failed"
    try:
        copy_ok, copy_reason = await loop.run_in_executor(None, _copy_to_temp, azura_file_path, temp_filename, azura_filename)
    except Exception as exc:
        print(f"{_LOG} queue_local_fav copy error: {exc!r}")

    if not copy_ok:
        if copy_reason == "local_source_missing":
            await _fail(
                f"❌ Local file not found on VPS for '{fav_title}'.",
                "local_source_missing",
                temp_filename,
                azura_file_path,
            )
            return
        await _fail(
            f"❌ Could not prepare '{fav_title}'. Check local replay copy path.",
            "local_temp_copy_failed",
            temp_filename,
            azura_file_path,
        )
        return
    _update_status(temp_filename, "uploaded")
    _radio_event(
        "local_temp_copy_done",
        request_id=request_id,
        user_id=uid,
        username=uname,
        title=fav_title,
        source_type="local_replay",
        azura_file_id="",
        azura_song_id="",
        temp_path=temp_filename,
        source_path=azura_file_path,
    )

    if _request_is_terminal():
        _radio_event(
            "cleanup_started",
            request_id=request_id,
            user_id=uid,
            username=uname,
            title=fav_title,
            source_type="local_replay",
            temp_path=temp_filename,
            source_path=azura_file_path,
        )
        if await loop.run_in_executor(None, _sftp_delete_temp, temp_filename):
            _radio_event(
                "temp_deleted",
                request_id=request_id,
                user_id=uid,
                username=uname,
                title=fav_title,
                source_type="local_replay",
                temp_path=temp_filename,
                source_path=azura_file_path,
            )
        return

    try:
        from modules.yt_request import process_existing_request_file
        ok = await process_existing_request_file(
            bot,
            request_id,
            temp_filename,
            source_type="local_replay",
            source_path=azura_file_path,
        )
    except Exception as exc:
        print(f"{_LOG} queue_local_fav post-file pipeline error: {exc!r}")
        ok = False

    if not ok and not _request_is_terminal():
        await _fail(
            f"❌ Couldn't prepare '{fav_title}'. Try another version.",
            "local_post_file_pipeline_failed",
            temp_filename,
            azura_file_path,
        )
    return

# !playfavlocal / !localreplaytest
# ---------------------------------------------------------------------------

async def handle_playfavlocal(bot, user, args: list[str] | None = None) -> None:
    """
    !playfavlocal <#>  /  !localreplaytest <#>
    Owner/admin only.  Creates a UUID temp copy of a local favorite and queues it.
    """
    import asyncio

    async def _w(msg: str) -> None:
        try:
            await bot.highrise.send_whisper(user.id, msg[:249])
        except Exception:
            pass

    command_name = str(args[0]).lower() if args else "playfavlocal"
    is_test = command_name == "localreplaytest"

    # ── Permission ───────────────────────────────────────────────────────────
    # !playfavlocal mirrors public !playfav for the caller's own favorites.
    # Diagnostic/test mode remains owner/admin-only.
    if is_test:
        try:
            from modules.economy import can_manage_economy
            if not can_manage_economy(user.username):
                await _w("Owner/admin only.")
                return
        except Exception as _pe:
            print(f"{_LOG} permission check error: {_pe!r}")
            await _w("Permission check error (non-fatal).")
            return

    # ── Feature flags ────────────────────────────────────────────────────────
    ok, reason = _flags_enabled()
    if not ok:
        await _w("🔒 Local replay is currently unavailable.")
        return

    # ── Arg validation ───────────────────────────────────────────────────────
    # Support both arg styles:
    #   args=["playfavlocal","1"]  (standard — cmd is args[0], number is args[1])
    #   args=["1"]                 (stripped-cmd style — number is args[0])
    num_arg: str | None = None
    if args:
        if str(args[0]).isdigit():
            num_arg = str(args[0])
        elif len(args) >= 2 and str(args[1]).isdigit():
            num_arg = str(args[1])
    if not num_arg:
        await _w("Usage: !playfavlocal <#>  (see !favs for list)")
        return
    pos = int(num_arg)

    # ── Load favorites (full meta — includes azura_file_id, source_type) ────
    try:
        from modules.local_media_map import _fav_get_full
        rows = _fav_get_full(user.id, limit=20)
    except Exception as exc:
        print(f"{_LOG} _fav_get_full error: {exc!r}")
        await _w("Could not load favorites. Try again.")
        return

    if not rows:
        await _w("No favorites saved yet. Use !fav while a song plays.")
        return
    if pos < 1 or pos > len(rows):
        await _w(f"Favorite #{pos} not found. You have {len(rows)}. Use !favs.")
        return

    fav         = rows[pos - 1]
    fav_title   = (fav.get("title") or "?")[:40]
    fav_artist  = (fav.get("artist") or "").strip()
    azura_fid   = (fav.get("azura_file_id") or "").strip()

    # Only reject if this is definitely a YouTube track.
    # A blank azura_file_id or any source_type label is NOT grounds for rejection —
    # we fall back to an AzuraCast library search below.
    yt_url   = (fav.get("youtube_url") or fav.get("url") or "").strip()
    video_id = (fav.get("video_id") or fav.get("yt_id") or "").strip()
    if yt_url or video_id:
        await _w(
            f"⚠️ '{fav_title}' is a YouTube track.\n"
            f"Use !playfav for YouTube tracks."
        )
        return

    print(f"{_LOG} playfavlocal: {user.username} #{pos} '{fav_title}' fid={azura_fid!r}")
    await _w(f"🎵 Local replay #{pos}: {fav_title}\nValidating source...")

    # ── AzuraCast: get file record (by fid, then fallback title/artist search) ─
    loop = asyncio.get_running_loop()
    azura_file_rec: dict = {}

    if azura_fid:
        try:
            from modules.azuracast_controller import get_media_file
            azura_file_rec = await loop.run_in_executor(
                None, get_media_file, azura_fid
            ) or {}
        except Exception as exc:
            print(f"{_LOG} get_media_file error: {exc!r}")
        map_by_fid = await loop.run_in_executor(None, _local_map_row_by_file_id, azura_fid)
        if map_by_fid:
            azura_file_rec = {**map_by_fid, **azura_file_rec}
            if not azura_file_rec.get("filename") and map_by_fid.get("filename"):
                azura_file_rec["filename"] = map_by_fid["filename"]

    # Fallback: search local_media_map by normalized title/artist
    if not azura_file_rec:
        print(f"{_LOG} no fid record — searching local_media_map for {fav_title!r}")
        try:
            from modules.local_media_map import match_from_map, _fav_backfill
            map_row, map_status = match_from_map(fav_title, fav_artist)
            if map_status == "ok" and map_row:
                # Backfill the favorites row so future calls use the fid directly
                _fav_backfill(
                    fav["id"],
                    map_row["azura_file_id"],
                    map_row["unique_id"],
                )
                # Build a minimal azura_file_rec from the map row
                azura_file_rec = {
                    "path":      map_row["path"],
                    "filename":  map_row.get("filename", ""),
                    "unique_id": map_row["unique_id"],
                    "id":        map_row["azura_file_id"],
                }
                await _w(
                    f"✅ Local match found:\n"
                    f"{map_row['title']}"
                    + (f" — {map_row['artist']}" if map_row.get("artist") else "")
                    + f"\nPath: {map_row['path']}"
                )
            elif map_status == "multiple":
                await _w(
                    f"⚠️ Multiple matches found for '{fav_title}'.\n"
                    f"Use !localmediafind {fav_title[:30]} to identify."
                )
                return
            else:
                await _w(
                    f"❌ No local media match for '{fav_title}'.\n"
                    f"Run !localmediascan, then retry."
                )
                return
        except Exception as exc:
            print(f"{_LOG} map search error: {exc!r}")
            await _w(
                f"❌ AzuraCast has no record for '{fav_title}'.\n"
                f"File may have been removed from the library."
            )
            return

    if not azura_file_rec:
        await _w(
            f"❌ AzuraCast has no record for '{fav_title}'.\n"
            f"File may have been removed from the library."
        )
        return

    azura_file_path = (azura_file_rec.get("path") or "").strip()
    azura_filename = (azura_file_rec.get("filename") or azura_file_rec.get("name") or "").strip()
    if not azura_file_path:
        await _w("❌ AzuraCast record has no path field. Cannot copy.")
        return

    print(f"{_LOG} source validated: path={azura_file_path!r}")

    # ── !localreplaytest debug whisper ───────────────────────────────────────
    if is_test:
        flags_ok, flags_reason = _flags_enabled()
        local_path = await loop.run_in_executor(
            None, _resolve_local_source_path, azura_file_path, azura_filename
        )
        resolved_path, trial_log = await loop.run_in_executor(
            None, _sftp_stat_exists_with_log, azura_file_path, azura_filename
        )
        cfg = _get_sftp_cfg()
        dest_path = f"{str(cfg.get('folder') or os.environ.get('AZURA_REQUESTS_FOLDER', 'Requests')).rstrip('/')}/{temp_filename if 'temp_filename' in locals() else 'tmp_replay_*.mp3'}"
        found_label = f"found ✅" if (local_path or resolved_path) else "missing ❌"
        await _w(
            f"🔍 [TEST] #{pos} {fav_title}\n"
            f"Flags: {'on' if flags_ok else 'off'} {flags_reason[:40]}\n"
            f"Source: {found_label}"
        )
        await _w(
            f"DB path: {azura_file_path[:100]}\n"
            f"Local: {(local_path or 'missing')[:100]}\n"
            f"SFTP: {(resolved_path or 'missing')[:100]}"
        )
        await _w(
            f"Requests dest: {dest_path[:160]}"
        )
        # Show every probed path so the owner can see which root works
        if trial_log:
            page = "Tried:"
            for idx, path, ok in trial_log:
                mark = "✅" if ok else "❌"
                line = f"\n{idx} {mark} {path}"
                if len(page + line) > 249:
                    await _w(page)
                    page = line.lstrip("\n")
                else:
                    page += line
            if page and page != "Tried:":
                await _w(page)

    # ── Lazy stale cleanup ───────────────────────────────────────────────────
    try:
        cleaned = await loop.run_in_executor(None, _cleanup_stale_temps)
        if cleaned:
            print(f"{_LOG} pre-run cleanup: {cleaned} stale temp(s) removed")
    except Exception:
        pass

    # ── Generate temp filename ───────────────────────────────────────────────
    temp_filename = f"tmp_replay_{uuid.uuid4().hex[:12]}.mp3"
    print(f"{_LOG} temp filename: {temp_filename}")

    # ── SFTP: copy source → temp ─────────────────────────────────────────────
    copy_ok = False
    copy_reason = "local_temp_copy_failed"
    try:
        copy_ok, copy_reason = await loop.run_in_executor(
            None, _copy_to_temp, azura_file_path, temp_filename, azura_filename
        )
    except Exception as exc:
        print(f"{_LOG} copy executor error: {exc!r}")

    if not copy_ok:
        if copy_reason == "local_source_missing":
            await _w(f"❌ Local file not found on VPS for '{fav_title}'.")
            return
        await _w(
            f"❌ Could not prepare '{fav_title}'. Check local replay copy path."
        )
        return

    # ── AzuraCast: rescan → poll until indexed (max 5 attempts × 2 s) ────────
    _MAX_INDEX_ATTEMPTS = 5
    _INDEX_RETRY_DELAY  = 2.0   # seconds between each search attempt

    azura_unique_id = ""
    azura_file_id_str = ""
    try:
        from modules.azuracast_controller import (
            rescan_requests_folder, search_media,
        )
        # Trigger rescan once, then poll
        await loop.run_in_executor(None, rescan_requests_folder)
        await asyncio.sleep(_INDEX_RETRY_DELAY)

        for attempt in range(1, _MAX_INDEX_ATTEMPTS + 1):
            media_row = await loop.run_in_executor(
                None, search_media, temp_filename
            )
            if media_row:
                # Match yt_request.py extraction exactly — never fall back to
                # the numeric 'id' field, which is NOT a valid requestable id.
                azura_unique_id = str(
                    media_row.get("unique_id")
                    or media_row.get("song_unique_id")
                    or (media_row.get("song") or {}).get("id")
                    or ""
                )
                azura_file_id_str = str(media_row.get("id") or "")
                print(
                    f"{_LOG} indexed attempt={attempt}"
                    f" file_id={azura_file_id_str!r}"
                    f" unique_id={azura_unique_id!r}"
                    f" song_id={(media_row.get('song') or {}).get('id')!r}"
                )
                await _w(
                    f"Attempt {attempt}: indexed"
                    f" file_id={azura_file_id_str}"
                    f" uid={azura_unique_id[:20] if azura_unique_id else 'EMPTY'}"
                )
                break

            print(f"{_LOG} attempt {attempt}: not indexed yet ({temp_filename})")
            await _w(f"Attempt {attempt}: not indexed yet…")

            if attempt < _MAX_INDEX_ATTEMPTS:
                # Re-trigger rescan on each miss, then wait
                try:
                    await loop.run_in_executor(None, rescan_requests_folder)
                except Exception:
                    pass
                await asyncio.sleep(_INDEX_RETRY_DELAY)

    except Exception as exc:
        print(f"{_LOG} rescan/search error: {exc!r}")

    # ── Track in DB ──────────────────────────────────────────────────────────
    _insert_job(
        user_id=user.id,
        username=user.username,
        fav_title=fav_title,
        source_path=azura_file_path,
        temp_filename=temp_filename,
        azura_unique_id=azura_unique_id,
        azura_file_id=azura_file_id_str,
    )

    # ── Debug: upload path vs AzuraCast media path ───────────────────────────
    _sftp_cfg_now  = _get_sftp_cfg()
    _upload_folder = _sftp_cfg_now.get("folder", "?")
    _upload_dest   = f"{_upload_folder.rstrip('/')}/{temp_filename}"
    _azura_mpath   = (media_row.get("path") if media_row else None) or "NOT_INDEXED"
    print(
        f"{_LOG} path_debug"
        f" upload_dest={_upload_dest!r}"
        f" azura_media_path={_azura_mpath!r}"
        f" file_id={azura_file_id_str!r}"
        f" unique_id={azura_unique_id!r}"
    )
    await _w(
        f"📂 upload→{_upload_folder}"
        f"\nazura_path={_azura_mpath}"
    )

    # ── Assign temp file to Requests playlist — mirrors YouTube pipeline ─────
    # YouTube's _azura_post_upload resolves playlist_id in two steps:
    #   1. AZURA_PLAYLIST_ID env var
    #   2. GET /playlists → find by name "Requests" / "Request"
    # We apply the same two-step resolution here.
    _pl_assigned  = False
    _target_pl_id = ""
    _pl_source    = ""
    if azura_file_id_str:
        try:
            import modules.config_store as _cs
            from modules.azuracast_controller import (
                add_file_to_playlist as _add_pl,
                list_playlists       as _list_pl,
                find_playlist_by_name as _find_pl,
            )

            # Step 1: env var (same as YouTube)
            _target_pl_id = _cs.requests_playlist_id()
            if _target_pl_id:
                _pl_source = "env_var"
            else:
                # Step 2: name lookup (same fallback as YouTube _azura_post_upload)
                _all_pls = await loop.run_in_executor(None, _list_pl)
                _pl_names = [
                    f"{p.get('id')}:{p.get('name')}"
                    for p in _all_pls
                ]
                print(
                    f"{_LOG} requests_assign_debug"
                    f" AZURA_PLAYLIST_ID=UNSET"
                    f" playlists_found={len(_all_pls)}"
                    f" names={_pl_names!r}"
                )
                await _w(
                    f"AZURA_PLAYLIST_ID unset → name lookup"
                    f"\nFound {len(_all_pls)} playlists:"
                    f" {', '.join(_pl_names)[:180]}"
                )
                for _pname in ("Requests", "Request"):
                    _pl_row = await loop.run_in_executor(None, _find_pl, _pname)
                    if _pl_row:
                        _target_pl_id = str(_pl_row.get("id") or "")
                        _pl_source    = f"name_lookup:{_pname}"
                        print(
                            f"{_LOG} requests_assign_debug"
                            f" name_lookup={_pname!r}"
                            f" found_id={_target_pl_id!r}"
                        )
                        break

            print(
                f"{_LOG} requests_assign"
                f" playlist_id={_target_pl_id!r}"
                f" source={_pl_source!r}"
                f" file_id={azura_file_id_str!r}"
            )
            if _target_pl_id:
                _pl_assigned = await loop.run_in_executor(
                    None, _add_pl, azura_file_id_str, _target_pl_id
                )
                print(
                    f"{_LOG} requests_assign result={_pl_assigned}"
                    f" playlist_id={_target_pl_id!r}"
                    f" source={_pl_source!r}"
                )
            else:
                print(
                    f"{_LOG} requests_assign: no playlist resolved"
                    f" (env_var=unset, name_lookup=no_match)"
                )
        except Exception as _vex:
            print(f"{_LOG} requests_assign error: {_vex!r}")
        await _w(
            f"playlist_id={_target_pl_id or 'NONE'}"
            f" src={_pl_source or 'none'}"
            f" assigned={'true' if _pl_assigned else 'false'}"
        )
        if not _pl_assigned:
            await _w(
                "❌ Requests playlist assign failed."
                " Staging blocked — track not requestable."
            )

    # ── Gate: must have AzuraCast unique_id ─────────────────────────────────
    if not azura_unique_id:
        await _w(
            f"⚠️ AzuraCast indexing timeout for '{fav_title}'.\n"
            f"Use !localreplaycleanup to clear the temp file."
        )
        return

    # Gate: file must be in Requests playlist so AzuraCast can service it.
    if azura_file_id_str and not _pl_assigned:
        _update_status(temp_filename, "cleanup_pending")
        await _w(
            f"❌ Cannot stage: '{fav_title[:40]}' not in"
            f" Requests playlist {_target_pl_id or 'NONE'}"
            f" (src={_pl_source or 'none'})."
            f" !localreplaycleanup to clear."
        )
        return

    # ── Stage: insert yt_request_jobs so playback_engine submits + cleans up ─
    # Do NOT call submit_request here — playback_engine owns submission.
    # Flow: ready → playback_engine submits → playing → finished → cleanup.
    _yt_job_id = _register_as_yt_request_job(
        user_id=user.id,
        username=user.username,
        title=fav_title,
        temp_filename=temp_filename,
        azura_file_id=azura_file_id_str,
        azura_song_id=azura_unique_id,
    )
    if _yt_job_id:
        _link_yt_job(temp_filename, _yt_job_id)
        print(
            f"{_LOG} staged yt_request_job_id={_yt_job_id}"
            f" uid={azura_unique_id!r}"
            f" — playback_engine will submit"
        )
        await _w(
            f"✅ Local replay staged!\n"
            f"'{fav_title}'\n"
            f"Temp: {temp_filename[:28]}\n"
            f"Cleanup: auto after play."
        )
    else:
        _update_status(temp_filename, "cleanup_pending")
        print(
            f"{_LOG} yt_request_jobs insert returned 0"
            f" — staging failed; use !localreplaycleanup"
        )
        await _w(
            f"❌ Staging failed: could not create request job"
            f" (check bot console).\n"
            f"!localreplaycleanup to clear temp."
        )


# ---------------------------------------------------------------------------
# !localreplaystatus
# ---------------------------------------------------------------------------

async def handle_localreplaystatus(bot, user, _args=None) -> None:
    """!localreplaystatus — show recent local replay jobs (owner/admin)."""

    async def _w(msg: str) -> None:
        try:
            await bot.highrise.send_whisper(user.id, msg[:249])
        except Exception:
            pass

    try:
        from modules.economy import can_manage_economy
        if not can_manage_economy(user.username):
            await _w("Owner/admin only.")
            return
    except Exception:
        await _w("Permission check error.")
        return

    en, _ = _flags_enabled()
    flag_line = "🟢 ENABLED" if en else "🔴 DISABLED"
    jobs = _list_jobs(limit=5)
    lines = [f"🎵 Local Replay [{flag_line}]"]
    if jobs:
        for j in jobs:
            t = (j.get("title") or "?")[:22]
            s = j.get("status", "?")
            lines.append(f"#{j['id']} {t} [{s}]")
    else:
        lines.append("No jobs found.")
    await _w("\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !localreplaycleanup
# ---------------------------------------------------------------------------

async def handle_localreplaycleanup(bot, user, _args=None) -> None:
    """!localreplaycleanup — manually purge stale temp files (owner/admin)."""
    import asyncio

    async def _w(msg: str) -> None:
        try:
            await bot.highrise.send_whisper(user.id, msg[:249])
        except Exception:
            pass

    try:
        from modules.economy import can_manage_economy
        if not can_manage_economy(user.username):
            await _w("Owner/admin only.")
            return
    except Exception:
        await _w("Permission check error.")
        return

    await _w("🧹 Running local replay cleanup...")
    try:
        loop = asyncio.get_running_loop()
        cleaned = await loop.run_in_executor(None, _cleanup_stale_temps)
        await _w(f"✅ Cleanup done: {cleaned} temp file(s) removed.")
        print(f"{_LOG} manual cleanup by {user.username}: {cleaned} removed")
    except Exception as exc:
        print(f"{_LOG} cleanup command error: {exc!r}")
        await _w("⚠️ Cleanup error — see logs. Queue unaffected.")
