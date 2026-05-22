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
from datetime import datetime, timezone

_LOG = "[LOCAL_REPLAY]"


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


def _register_as_yt_request_job(
    user_id: str, username: str, title: str,
    temp_filename: str, azura_file_id: str, azura_song_id: str,
) -> int:
    """
    Insert a yt_request_jobs row (status='ready', source_type='local_replay')
    so playback_engine can detect this file in now-playing and run the normal
    _delete_request_file cleanup lifecycle after play.

    Returns the new row id (0 on error).
    """
    try:
        import database as _db
        with _db.db_conn() as conn:
            cur = conn.execute(
                """INSERT INTO yt_request_jobs
                       (user_id, username, url, title, status, started_at,
                        filename, azura_file_id, azura_song_id, source_type)
                   VALUES (?, ?, '', ?, 'ready', datetime('now'), ?, ?, 'local_replay')""",
                (user_id, username, title, temp_filename, azura_file_id, azura_song_id),
            )
            return cur.lastrowid or 0
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
    "/var/azuracast/stations/chilltopia/media",
    "chilltopia/media",
    "media",
    "",  # bare relative path — last resort
]


def _build_path_candidates(azura_file_path: str) -> list[str]:
    """
    Return an ordered list of absolute/relative SFTP paths to probe.

    Order:
      1. AZURA_MEDIA_SFTP_PATH env var  (if set)
      2–6. _SFTP_FALLBACK_ROOTS
    Duplicates are silently skipped.
    """
    rel = azura_file_path.lstrip("/")
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(root: str) -> None:
        path = f"{root.rstrip('/')}/{rel}" if root else rel
        if path not in seen:
            seen.add(path)
            candidates.append(path)

    env_root = os.environ.get("AZURA_MEDIA_SFTP_PATH", "").strip()
    if env_root:
        _add(env_root)
    for root in _SFTP_FALLBACK_ROOTS:
        _add(root)

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
            resolved = path
            break
        except (IOError, FileNotFoundError):
            trial_log.append((i, path, False))
        except Exception as exc:
            trial_log.append((i, path, False))
            print(f"{_LOG} resolve candidate {i} error: {exc!r}")
    return resolved, trial_log


def _sftp_copy_to_temp(azura_file_path: str, temp_filename: str) -> bool:
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

    candidates   = _build_path_candidates(azura_file_path)
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

    candidates = _build_path_candidates(azura_file_path)

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
    Hard safety check: only deletes files starting with 'tmp_replay_'.
    """
    if not temp_filename.startswith("tmp_replay_"):
        print(f"{_LOG} delete refused — not a temp file: {temp_filename!r}")
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
    Find local_replay_jobs older than 2 h that are not yet in a terminal
    state, delete their SFTP temp file (safety-checked), mark them done.

    Returns count of entries cleaned.  Never raises.
    """
    try:
        _ensure_schema()
        import database as _db
        with _db.db_conn() as conn:
            rows = conn.execute(
                "SELECT temp_filename, COALESCE(azura_file_id,'') "
                "FROM local_replay_jobs "
                "WHERE status IN ('queued','playing','cleanup_pending') "
                "  AND (cleanup_complete IS NULL OR cleanup_complete='') "
                "  AND created_at < datetime('now','-2 hours')",
            ).fetchall()
    except Exception as exc:
        print(f"{_LOG} cleanup query error: {exc!r}")
        return 0

    cleaned = 0
    for (tfn, fid) in rows:
        if not tfn or not tfn.startswith("tmp_replay_"):
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

    # ── Permission ───────────────────────────────────────────────────────────
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
        await _w(f"🔒 Local replay disabled.\n{reason}")
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

    # Detect test mode: args[0] is the command name when not stripped.
    is_test = bool(args) and str(args[0]).lower() == "localreplaytest"

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
    if not azura_file_path:
        await _w("❌ AzuraCast record has no path field. Cannot copy.")
        return

    print(f"{_LOG} source validated: path={azura_file_path!r}")

    # ── !localreplaytest debug whisper ───────────────────────────────────────
    if is_test:
        resolved_path, trial_log = await loop.run_in_executor(
            None, _sftp_stat_exists_with_log, azura_file_path
        )
        found_label = f"found ✅" if resolved_path else "missing ❌"
        await _w(
            f"🔍 [TEST] #{pos} {fav_title}\n"
            f"Path: {azura_file_path}\n"
            f"Source: {found_label}"
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
    try:
        copy_ok = await loop.run_in_executor(
            None, _sftp_copy_to_temp, azura_file_path, temp_filename
        )
    except Exception as exc:
        print(f"{_LOG} copy executor error: {exc!r}")

    if not copy_ok:
        await _w(
            f"❌ Copy failed for '{fav_title}'.\n"
            f"Check SFTP config and AZURA_MEDIA_SFTP_PATH."
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
            submit_request_verbose, lookup_requestable_id,
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

    # ── Assign temp file to active vibe playlist ─────────────────────────────
    # AzuraCast only marks a file requestable when it belongs to an *enabled*
    # playlist.  The vibe system keeps exactly one vibe playlist active at a
    # time, so the temp copy must be in that playlist before submit_request.
    _pl_assigned  = False
    _active_vibe  = ""
    _target_pl_id = ""
    if azura_file_id_str:
        try:
            import modules.config_store as _cs
            from modules.azuracast_controller import add_file_to_playlist as _add_pl
            _active_vibe = _cs.vibe()
            _cache_vibes = (_cs.get_vibe_scan_cache().get("vibes") or {})
            _target_pl_id = (
                (_cache_vibes.get(_active_vibe) or {}).get("playlist_id")
                or _cs.get_dynamic_vibe_playlist(_active_vibe)
                or _cs.vibe_playlist_id(_active_vibe)
                or _cs.requests_playlist_id()
            )
            print(
                f"{_LOG} vibe_assign"
                f" vibe={_active_vibe!r}"
                f" playlist={_target_pl_id!r}"
                f" file_id={azura_file_id_str!r}"
            )
            if _target_pl_id:
                _pl_assigned = await loop.run_in_executor(
                    None, _add_pl, azura_file_id_str, _target_pl_id
                )
                print(f"{_LOG} vibe_assign result={_pl_assigned}")
            else:
                print(f"{_LOG} vibe_assign: no playlist resolved"
                      f" for vibe={_active_vibe!r}")
        except Exception as _vex:
            print(f"{_LOG} vibe_assign error: {_vex!r}")
        await _w(
            f"vibe={_active_vibe or '?'}"
            f" playlist={_target_pl_id[:28] if _target_pl_id else 'NONE'}"
            f" requestable={'true' if _pl_assigned else 'false'}"
        )
        if not _pl_assigned:
            await _w(
                "❌ Vibe playlist assign failed (both methods)."
                " Blocking submit — track not requestable."
            )

    # ── Submit request ───────────────────────────────────────────────────────
    if not azura_unique_id:
        await _w(
            f"⚠️ AzuraCast indexing timeout for '{fav_title}'.\n"
            f"Use !localreplaycleanup to clear the temp file."
        )
        return

    # Gate on playlist assignment — AzuraCast rejects requests for files
    # that are not in an enabled playlist.
    if azura_file_id_str and not _pl_assigned:
        _update_status(temp_filename, "cleanup_pending")
        await _w(
            f"❌ Cannot submit: '{fav_title}' is not in"
            f" playlist {_target_pl_id or '?'}.\n"
            f"vibe={_active_vibe or '?'}\n"
            f"!localreplaycleanup to clear temp."
        )
        return

    # Use same verbose path as normal !play — logs URL, station_id, uid,
    # HTTP status, and response body so failures are fully visible.
    req_ok = False
    req_status = 0
    req_body = ""
    used_uid = azura_unique_id
    try:
        req_ok, req_status, req_body = await loop.run_in_executor(
            None, submit_request_verbose, azura_unique_id
        )
    except Exception as exc:
        print(f"{_LOG} submit_request_verbose error: {exc!r}")
        req_body = repr(exc)

    # ── Fallback: if direct submit failed, look up station request_id ────────
    if not req_ok:
        print(
            f"{_LOG} submit failed uid={azura_unique_id!r}"
            f" status={req_status} body={req_body!r}"
            f" — trying lookup_requestable_id fallback"
        )
        await _w(
            f"⚠️ Submit failed (HTTP {req_status}).\n"
            f"uid={azura_unique_id[:20] if azura_unique_id else 'EMPTY'}\n"
            f"Trying requestable-id lookup…"
        )
        try:
            rid = await loop.run_in_executor(
                None, lookup_requestable_id, temp_filename
            )
            if rid and rid != azura_unique_id:
                used_uid = rid
                print(f"{_LOG} retrying with requestable rid={rid!r}")
                req_ok, req_status, req_body = await loop.run_in_executor(
                    None, submit_request_verbose, rid
                )
            elif rid == azura_unique_id:
                print(f"{_LOG} requestable id same as unique_id — no retry")
            else:
                print(f"{_LOG} lookup_requestable_id returned nothing")
        except Exception as exc2:
            print(f"{_LOG} fallback lookup error: {exc2!r}")

    if req_ok:
        print(f"{_LOG} ✓ queued: {temp_filename} uid={used_uid!r}")
        # Register with yt_request_jobs — playback_engine will auto-delete
        # the temp file via the normal ready→playing→played→cleaned lifecycle.
        _yt_job_id = _register_as_yt_request_job(
            user_id=user.id,
            username=user.username,
            title=fav_title,
            temp_filename=temp_filename,
            azura_file_id=azura_file_id_str,
            azura_song_id=used_uid,
        )
        if _yt_job_id:
            _link_yt_job(temp_filename, _yt_job_id)
            print(
                f"{_LOG} yt_request_job_id={_yt_job_id}"
                f" linked → temp cleanup is automatic"
            )
        else:
            print(
                f"{_LOG} warn: yt_request_jobs insert returned 0"
                f" — auto-cleanup unavailable; use !localreplaycleanup"
            )
        await _w(
            f"✅ Replay queued!\n"
            f"'{fav_title}'\n"
            f"Temp: {temp_filename[:28]}\n"
            f"Cleanup: auto after play."
        )
    else:
        _update_status(temp_filename, "cleanup_pending")
        err_hint = req_body[:120] if req_body else "no response body"
        await _w(
            f"❌ Request submit failed (HTTP {req_status}).\n"
            f"uid={used_uid[:24] if used_uid else 'EMPTY'}\n"
            f"AzuraCast: {err_hint}\n"
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
