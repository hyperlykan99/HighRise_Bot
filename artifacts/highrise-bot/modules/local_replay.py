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
                    status           TEXT DEFAULT 'queued',
                    created_at       TEXT,
                    queued_at        TEXT,
                    cleanup_complete TEXT
                )
            """)
    except Exception as _e:
        print(f"{_LOG} schema init (non-fatal): {_e!r}")


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_job(
    user_id: str, username: str, fav_title: str,
    source_path: str, temp_filename: str, azura_unique_id: str = "",
) -> None:
    try:
        _ensure_schema()
        import database as _db
        with _db.db_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO local_replay_jobs "
                "(user_id, username, fav_title, source_path, temp_filename, "
                " azura_unique_id, status, created_at, queued_at) "
                "VALUES (?,?,?,?,?,?,'queued',?,?)",
                (user_id, username, fav_title, source_path,
                 temp_filename, azura_unique_id, _ts(), _ts()),
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


# ---------------------------------------------------------------------------
# SFTP helpers — all deferred imports, never raise
# ---------------------------------------------------------------------------

def _get_sftp_cfg() -> dict:
    try:
        from modules.config_store import sftp_cfg
        return sftp_cfg()
    except Exception:
        return {}


def _sftp_copy_to_temp(azura_file_path: str, temp_filename: str) -> bool:
    """
    Open an SFTP session and stream-copy the library file to the requests
    folder as temp_filename.

    Source full SFTP path is built as:
      {media_root}/{azura_file_path}
    where media_root = AZURA_MEDIA_SFTP_PATH  (env var, explicit override)
                    or parent dir of AZURA_SFTP_PATH  (derived default).

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

    requests_folder = cfg["folder"].rstrip("/")
    media_root = (
        os.environ.get("AZURA_MEDIA_SFTP_PATH", "").strip()
        or os.path.dirname(requests_folder)
    )

    src_path  = f"{media_root.rstrip('/')}/{azura_file_path.lstrip('/')}"
    dest_path = f"{requests_folder}/{temp_filename}"

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

        # 1. Stat source to confirm it exists before reading
        try:
            sftp.stat(src_path)
        except IOError:
            print(f"{_LOG} source not found on VPS: {src_path!r}")
            return False

        # 2. Stream-copy in the same SFTP session — never touches original
        print(f"{_LOG} copying {src_path!r} → {dest_path!r}")
        with sftp.file(src_path, "rb") as src_f:
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


def _sftp_stat_exists(azura_file_path: str) -> bool:
    """
    Return True if the file exists on the VPS at:
      AZURA_MEDIA_SFTP_PATH / azura_file_path
    Used only by !localreplaytest debug whisper — never modifies files.
    """
    try:
        import paramiko
    except ImportError:
        return False

    cfg = _get_sftp_cfg()
    if not cfg.get("host") or not cfg.get("user"):
        return False

    media_root = (
        os.environ.get("AZURA_MEDIA_SFTP_PATH", "").strip()
        or os.path.dirname(cfg["folder"].rstrip("/"))
    )
    src_path = f"{media_root.rstrip('/')}/{azura_file_path.lstrip('/')}"

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
        sftp.stat(src_path)
        return True
    except IOError:
        return False
    except Exception:
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
                "SELECT temp_filename FROM local_replay_jobs "
                "WHERE status IN ('queued','playing','cleanup_pending') "
                "  AND (cleanup_complete IS NULL OR cleanup_complete='') "
                "  AND created_at < datetime('now','-2 hours')",
            ).fetchall()
    except Exception as exc:
        print(f"{_LOG} cleanup query error: {exc!r}")
        return 0

    cleaned = 0
    for (tfn,) in rows:
        if not tfn or not tfn.startswith("tmp_replay_"):
            continue
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
        media_root = (
            os.environ.get("AZURA_MEDIA_SFTP_PATH", "").strip()
            or os.path.dirname(
                (_get_sftp_cfg().get("folder") or "").rstrip("/")
            )
        )
        src_full = (
            f"{media_root.rstrip('/')}/{azura_file_path.lstrip('/')}"
            if media_root else azura_file_path
        )
        file_exists = await loop.run_in_executor(
            None, _sftp_stat_exists, azura_file_path
        )
        found_label = "found ✅" if file_exists else "missing ❌"
        await _w(
            f"🔍 [TEST] #{pos} {fav_title}\n"
            f"Path: {azura_file_path}\n"
            f"Source: {found_label}"
        )

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

    # ── AzuraCast: rescan → find temp → get unique_id ────────────────────────
    azura_unique_id = ""
    try:
        from modules.azuracast_controller import rescan_requests_folder, search_media
        await loop.run_in_executor(None, rescan_requests_folder)
        await asyncio.sleep(2)    # let AzuraCast index the new file
        media_row = await loop.run_in_executor(None, search_media, temp_filename)
        if media_row:
            azura_unique_id = str(
                media_row.get("unique_id")
                or media_row.get("song_id")
                or media_row.get("id")
                or ""
            )
            print(f"{_LOG} AzuraCast indexed temp: uid={azura_unique_id!r}")
        else:
            print(f"{_LOG} temp file not indexed after rescan: {temp_filename}")
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
    )

    # ── Submit request ───────────────────────────────────────────────────────
    if not azura_unique_id:
        await _w(
            f"⚠️ Temp copied but AzuraCast hasn't indexed it yet.\n"
            f"Wait 30s then retry or use !localreplaycleanup."
        )
        return

    request_ok = False
    try:
        from modules.azuracast_controller import submit_request
        request_ok = await loop.run_in_executor(
            None, submit_request, azura_unique_id
        )
    except Exception as exc:
        print(f"{_LOG} submit_request error: {exc!r}")

    if request_ok:
        print(f"{_LOG} ✓ queued: {temp_filename}")
        await _w(
            f"✅ Replay queued!\n"
            f"'{fav_title}'\n"
            f"Temp: {temp_filename[:28]}\n"
            f"Cleanup: auto on next replay."
        )
    else:
        _update_status(temp_filename, "cleanup_pending")
        await _w(
            f"⚠️ Temp copy created but request submit failed.\n"
            f"Use !localreplaycleanup to clear. Queue unaffected."
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
