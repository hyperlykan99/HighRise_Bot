"""
modules/playback_engine.py
--------------------------
AzuraCast-first playback engine.

AzuraCast AutoDJ controls playback order entirely.
The bot submits requests, watches now-playing, and cleans up files after play.

Playlist Rules (always enforced)
─────────────────────────────────
  • Requests playlist  — ALWAYS enabled
  • Current vibe playlist — ALWAYS enabled
  • All other vibe/music playlists — disabled

The bot never disables the vibe playlist when requests are queued, never
pre-switches modes, and never skips the current song automatically.

Song Detection  (polls every POLL_INTERVAL seconds)
───────────────
  On song change:
    1. Previous track was a request → mark played in DB, delete file from AzuraCast
    2. New track matches a queued request → mark playing, announce to room
    3. New track is a vibe song → announce with vibe prefix
"""
from __future__ import annotations
import asyncio
import threading
import time
from typing import TYPE_CHECKING

import database as db
import modules.azuracast_controller as azura
import modules.config_store         as cs
import modules.dj_announcer         as ann
import modules.radio_diagnostics   as diag
import modules.request_queue        as rq
from modules.radio_status import ACTIVE_QUEUE_STATUSES

if TYPE_CHECKING:
    from highrise import BaseBot

_LOG          = "[PLAYBACK]"
POLL_INTERVAL = 5       # seconds between AzuraCast nowplaying polls
_STATE_NS     = "playback_"

# ─── Module-level state ───────────────────────────────────────────────────────
_lock                   = threading.Lock()
_stop_flag              = threading.Event()    # Set on shutdown; executor threads check this
_stage_promotion_lock   = threading.Lock()     # Prevents concurrent staged-job promotions
_submitted_jids:  set   = set()                # job IDs submitted to AzuraCast request queue
_started:         bool  = False
_mode:            str   = "vibe"    # "vibe" | "requests"
_cur_song_id:     str   = ""        # AzuraCast song.id currently playing
_cur_req_id:      int   = 0         # yt_request_jobs.id of the active request (0 = none)
_last_ann_id:     str   = ""        # song.id last announced (dedup)
_last_ann_title:  str   = ""        # normalized title last announced (title-fallback dedup)
_skip_task_active: bool = False     # True while a _verified_skip_task is running
_cur_elapsed:     int   = 0         # AzuraCast elapsed seconds for current song
_cur_duration:    int   = 0         # AzuraCast total duration of current song
_live_req: "dict | None" = None     # In-memory cache of the currently-playing request; set at
                                    # announcement time, cleared on finish/skip/AutoDJ
_cur_replay_temp: str   = ""        # basename of tmp_replay_* currently playing ("" = none)
_replay_marked:   set   = set()     # basenames already marked status='playing' in local_replay_jobs
_finished_jids:    set   = set()     # request IDs already consumed this process
_LOCAL_TEMP_PREFIXES = ("tmp_replay_", "local_request_")

_ACT = ACTIVE_QUEUE_STATUSES
_ACT_PH = ",".join("?" * len(_ACT))   # SQL placeholders for IN clause

_COLS = (
    "id", "user_id", "username", "url", "title", "filename",
    "azura_file_id", "azura_song_id", "coins_charged", "status", "video_id",
    "source_type",
)
_SEL = (
    "id, user_id, username, url, title, filename, "
    "azura_file_id, azura_song_id, coins_charged, status, video_id, "
    "COALESCE(source_type, '') AS source_type"
)


def _jrow(row) -> dict:
    return dict(zip(_COLS, row))


def _is_local_temp_basename(name: str) -> bool:
    return bool(name and name == name.rsplit("/", 1)[-1] and name.startswith(_LOCAL_TEMP_PREFIXES))


# ─── Persistent state ─────────────────────────────────────────────────────────

def _save(key: str, value: str) -> None:
    try:
        db.set_room_setting(_STATE_NS + key, value)
    except Exception as exc:
        print(f"{_LOG} _save({key!r}) error: {exc}")


# ─── DB job queries ───────────────────────────────────────────────────────────

def _db_find_oldest_ready() -> "dict | None":
    """The oldest request with status='ready' that hasn't started playing yet."""
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs "
                "WHERE status='ready' AND played_at IS NULL "
                "  AND cleaned_at IS NULL "
                "ORDER BY id ASC LIMIT 1",
            ).fetchone()
            return _jrow(row) if row else None
    except Exception as exc:
        print(f"{_LOG} _db_find_oldest_ready: {exc}")
        return None


def _db_find_new_ready() -> list:
    """
    Jobs uploaded + registered with AzuraCast (status='ready', azura_file_id
    set) that are candidates for submission to the AzuraCast request queue.
    """
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs "
                "WHERE status='ready' AND azura_file_id!='' "
                "  AND played_at IS NULL AND cleaned_at IS NULL "
                "ORDER BY id ASC",
            ).fetchall()
            return [_jrow(r) for r in rows]
    except Exception as exc:
        print(f"{_LOG} _db_find_new_ready: {exc}")
        return []


def _db_find_playing() -> "dict | None":
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs "
                "WHERE status='playing' AND played_at IS NULL "
                "ORDER BY id DESC LIMIT 1",
            ).fetchone()
            return _jrow(row) if row else None
    except Exception as exc:
        print(f"{_LOG} _db_find_playing: {exc}")
        return None


def _db_count_active() -> int:
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM yt_request_jobs "
                f"WHERE status IN ({_ACT_PH}) AND played_at IS NULL",
                _ACT,
            ).fetchone()
            return row[0] if row else 0
    except Exception as exc:
        print(f"{_LOG} _db_count_active: {exc}")
        return 0


def _db_count_staged() -> int:
    """Count jobs with status='staged' waiting for the /Requests SFTP slot."""
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM yt_request_jobs "
                "WHERE status='staged' AND played_at IS NULL",
            ).fetchone()
            return row[0] if row else 0
    except Exception as exc:
        print(f"{_LOG} _db_count_staged: {exc}")
        return 0


def _db_count_active_in_requests() -> int:
    """
    Count jobs with an active registered file in /Requests
    (status IN ('ready','playing') AND azura_file_id set).
    """
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM yt_request_jobs "
                "WHERE status IN ('ready','playing') "
                "  AND azura_file_id != '' AND played_at IS NULL",
            ).fetchone()
            return row[0] if row else 0
    except Exception as exc:
        print(f"{_LOG} _db_count_active_in_requests: {exc}")
        return 0


def _db_set_status(db_id: int, status: str, media_id: str = "") -> None:
    if status == "ready":
        rq.mark_ready(db_id)
    elif status == "playing":
        rq.mark_playing(db_id, media_id=media_id, reason="playback_engine")
    elif status == "played":
        rq.mark_played(db_id, reason="playback_engine")
    elif status == "error":
        rq.mark_failed(db_id, "playback_engine_error")
    else:
        rq.update_job_fields(db_id, status=status)


def _db_fail_and_refund(db_id: int, reason: str) -> None:
    """Mark an unplayed request failed and refund its coin charge once."""
    if not db_id:
        return
    try:
        import modules.payment_service as ps
        row = rq.mark_failed(db_id, reason, refund_details=True)
        if not row:
            return
        uid = row.get("user_id", "")
        username = row.get("username", "")
        coins = int(row.get("coins_charged") or 0)
        if uid and int(coins or 0) > 0:
            ps.refund(uid, int(coins or 0), reason)
            diag.log_radio_event(
                "refund",
                request_id=db_id,
                user_id=uid,
                coins=int(coins or 0),
                reason=reason,
            )
        print(
            f"{_LOG} stage=request_failed_refund request_id={db_id}"
            f" username={username!r} coins={int(coins or 0)} reason={reason!r}"
        )
    except Exception as exc:
        print(f"{_LOG} _db_fail_and_refund({db_id},{reason!r}): {exc}")


def _db_backfill_playfav_source_after_play(job: dict) -> None:
    """Backfill a source-less favorite only after its fallback request played."""
    uid = (job.get("user_id") or "").strip()
    url = (job.get("url") or "").strip()
    if not uid or not url:
        return
    try:
        with db.db_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS playfav_fallback_sources ("
                "fav_id INTEGER NOT NULL, "
                "user_id TEXT NOT NULL, "
                "url TEXT NOT NULL, "
                "artist TEXT NOT NULL DEFAULT '', "
                "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
                "PRIMARY KEY (fav_id, url)"
                ")"
            )
            row = conn.execute(
                "SELECT fav_id, artist FROM playfav_fallback_sources "
                "WHERE user_id=? AND url=? "
                "ORDER BY created_at DESC LIMIT 1",
                (uid, url),
            ).fetchone()
            if not row:
                return
            fav_id, artist = row
            conn.execute(
                "UPDATE dj_favorites "
                "SET youtube_url=?, source_type='youtube', "
                "    artist=CASE WHEN ?!='' THEN ? ELSE artist END "
                "WHERE id=? AND user_id=?",
                (url, artist or "", artist or "", fav_id, uid),
            )
            conn.execute(
                "DELETE FROM playfav_fallback_sources WHERE fav_id=? AND url=?",
                (fav_id, url),
            )
        print(f"{_LOG} stage=playfav_fallback_backfilled fav_id={fav_id} url={url!r}")
    except Exception as exc:
        print(f"{_LOG} playfav fallback backfill error: {exc!r}")


def _db_get_job(db_id: int) -> "dict | None":
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs WHERE id=?",
                (db_id,),
            ).fetchone()
            return _jrow(row) if row else None
    except Exception as exc:
        print(f"{_LOG} _db_get_job({db_id}): {exc}")
        return None


def _db_match_request(
    azura_song_id: str,
    np_title: str,
    media_id: str = "",
    media_path: str = "",
) -> "dict | None":
    """
    Try to match the currently-playing AzuraCast song to one of our queued requests.
    Injects '_match_method' key into the returned dict for structured logging.

    Strategy 0: azura_file_id match by numeric media_id (most reliable when available).
    Strategy 1: azura_song_id match by song.unique_id (reliable once AzuraCast indexes).
    Strategy 2: Requests/ path — file is definitively from the Requests playlist.
                Sub-match by filename first; fallback to oldest active queued job.
                Returns None early if path is Requests/ but queue is empty
                (never mislabel a Requests/ track as AutoDJ).
    Strategy 3: filename substring in NP media path (non-Requests paths only).
    Strategy 4: video_id substring in media path or song hash.
    Strategy 5: title substring match (last fallback — only without reliable path info).
    """
    active = (
        "pending", "downloading", "downloaded", "uploading",
        "staged", "ready", "queued", "submitted", "done", "playing",
    )
    ph     = ",".join("?" * len(active))

    # Strategy 0: match by numeric azura_file_id (= media.id from NP API)
    if media_id:
        try:
            with db.db_conn() as conn:
                row = conn.execute(
                    f"SELECT {_SEL} FROM yt_request_jobs "
                    f"WHERE azura_file_id=? AND status IN ({ph}) "
                    "  AND played_at IS NULL LIMIT 1",
                    (media_id, *active),
                ).fetchone()
                if row:
                    j = _jrow(row)
                    j["_match_method"] = "media_id"
                    return j
        except Exception:
            pass

    # Strategy 1: azura_song_id exact match (song.unique_id stored at upload time)
    if azura_song_id:
        try:
            with db.db_conn() as conn:
                row = conn.execute(
                    f"SELECT {_SEL} FROM yt_request_jobs "
                    f"WHERE azura_song_id=? AND status IN ({ph}) "
                    "  AND played_at IS NULL LIMIT 1",
                    (azura_song_id, *active),
                ).fetchone()
                if row:
                    j = _jrow(row)
                    j["_match_method"] = "song_unique_id"
                    return j
        except Exception:
            pass

    # Strategy 2: Requests/ path — definitively from the Requests playlist
    if media_path:
        lpath = media_path.lstrip("/")
        if lpath.lower().startswith("requests/"):
            path_base = media_path.rsplit("/", 1)[-1].lower()
            try:
                with db.db_conn() as conn:
                    rows = conn.execute(
                        f"SELECT {_SEL} FROM yt_request_jobs "
                        f"WHERE status IN ({ph}) AND played_at IS NULL "
                        "  ORDER BY id ASC",
                        active,
                    ).fetchall()
                    # Sub-match: filename within the Requests/ path
                    if path_base:
                        for row in rows:
                            j   = _jrow(row)
                            jfn = (j.get("filename") or "").lower()
                            if jfn and (
                                jfn == path_base
                                or jfn in path_base
                                or path_base in jfn
                            ):
                                j["_match_method"] = "requests_path_filename"
                                return j
                    # Fallback: oldest active job (path proves it's a request).
                    # ONLY allowed when the job has no filename stored — if a
                    # filename is set but didn't match, this NP song is not our
                    # job (e.g. a different request or a stale Requests/ file).
                    # Using this fallback on a named job would set status='playing'
                    # on the wrong row and trigger premature cleanup.
                    if rows:
                        j = _jrow(rows[0])
                        if not (j.get("filename") or "").strip():
                            j["_match_method"] = "requests_path_oldest"
                            return j
            except Exception:
                pass
            # Path is Requests/ but queue is empty — never label this as AutoDJ
            return None

    # Strategy 3: filename substring in NP media path (non-Requests paths)
    if media_path:
        path_base = media_path.rsplit("/", 1)[-1].lower()
        if path_base:
            try:
                with db.db_conn() as conn:
                    rows = conn.execute(
                        f"SELECT {_SEL} FROM yt_request_jobs "
                        f"WHERE status IN ({ph}) AND played_at IS NULL "
                        "  AND filename!='' ORDER BY id ASC",
                        active,
                    ).fetchall()
                    for row in rows:
                        j   = _jrow(row)
                        jfn = (j.get("filename") or "").lower()
                        if jfn and (
                            jfn == path_base
                            or jfn in path_base
                            or path_base in jfn
                        ):
                            j["_match_method"] = "filename"
                            return j
            except Exception:
                pass

    # Strategy 4: video_id substring in media path or song hash
    if media_path or azura_song_id:
        try:
            with db.db_conn() as conn:
                rows = conn.execute(
                    f"SELECT {_SEL} FROM yt_request_jobs "
                    f"WHERE status IN ({ph}) AND played_at IS NULL "
                    "  AND video_id!='' ORDER BY id ASC",
                    active,
                ).fetchall()
                for row in rows:
                    j   = _jrow(row)
                    vid = (j.get("video_id") or "").strip()
                    if vid and (
                        (media_path and vid in media_path)
                        or (azura_song_id and vid in azura_song_id)
                    ):
                        j["_match_method"] = "video_id"
                        return j
        except Exception:
            pass

    # Strategy 5: title substring match (last fallback — only without reliable path)
    if np_title:
        norm = np_title.lower().strip()
        try:
            with db.db_conn() as conn:
                rows = conn.execute(
                    f"SELECT {_SEL} FROM yt_request_jobs "
                    f"WHERE status IN ({ph}) AND played_at IS NULL "
                    "  AND title!='' ORDER BY id ASC",
                    active,
                ).fetchall()
                for row in rows:
                    j  = _jrow(row)
                    jt = j["title"].lower().strip()
                    if jt and (jt in norm or norm in jt or jt[:40] == norm[:40]):
                        j["_match_method"] = "title_fuzzy"
                        return j
        except Exception:
            pass

    return None


# ─── Playlist control ─────────────────────────────────────────────────────────

async def _apply_vibe_playlists() -> None:
    """
    Enable the current vibe playlist; disable all other vibe playlists.
    Requests playlist is left ALWAYS ON — never disabled here.
    """
    loop   = asyncio.get_running_loop()
    v      = cs.vibe()
    req_id = cs.requests_playlist_id()

    # Requests always stays on
    if req_id:
        await loop.run_in_executor(None, azura.set_playlist_enabled, req_id, True)

    # Enable selected vibe playlist, disable all others
    await loop.run_in_executor(None, azura.switch_vibe, v)

    _save("playlist_mode", "vibe")
    print(f"{_LOG} Playlists → VIBE/{v.upper()} (Requests always ON)")


async def _switch_to_vibe(bot: "BaseBot") -> None:
    global _mode
    with _lock:
        _mode = "vibe"
    await _apply_vibe_playlists()
    print(f"{_LOG} Switched → VIBE/{cs.vibe().upper()}")


# ─── Staged-job promotion helper ─────────────────────────────────────────────

def _do_promote_staged(bot: "BaseBot", loop: "asyncio.AbstractEventLoop") -> None:
    """
    Blocking wrapper called via loop.run_in_executor from the poll loop.

    Acquires _stage_promotion_lock (non-blocking) so only one promotion runs
    at a time across concurrent poll cycles.  Imports radio_promote_staged_job
    lazily to avoid a circular-import at module load time.

    Log field: stage=queue_promote_next
    """
    if not _stage_promotion_lock.acquire(blocking=False):
        print(f"{_LOG} stage=queue_promote_next result=skipped reason=already_running")
        return
    try:
        from modules.yt_request import radio_promote_staged_job as _promote
        result = _promote(bot, loop)
        print(
            f"{_LOG} stage=queue_promote_next"
            f" result={'promoted' if result else 'none_staged'}"
        )
    except Exception as exc:
        print(f"{_LOG} stage=queue_promote_next result=error error={exc!r}")
    finally:
        _stage_promotion_lock.release()


# ─── Request file cleanup helpers ────────────────────────────────────────────

def _db_set_cleaned(db_id: int) -> None:
    """Mark the request file as deleted by setting cleaned_at in the DB."""
    rq.mark_cleaned(db_id)


def _delete_request_file(
    db_id: int,
    fid: str,
    fn: str,
    title: str,
    song_id: str = "",
) -> bool:
    """
    Blocking — MUST be called via run_in_executor.

    Full cleanup sequence for a completed request file.  All steps are
    idempotent: 404 / file-not-found are treated as success.  Errors are
    logged and never raised; nothing is shown to players.

    Steps
    ─────
    1. Lookup media_id    — if azura_file_id is missing, search AzuraCast by
                            filename so the API path is always preferred.
    2. Remove from playlist (action=remove_from_playlist) — PUT playlists=[]
                            so AutoDJ stops queuing the file immediately, even
                            if the subsequent deletion takes a moment.
    3. Delete via API     (action=delete_file method=api) — removes the media
                            record and all playlist memberships at once.
    4. SFTP move          (action=move_to_played) — move file from /Requests to
                            /PlayedRequests for replay prevention and audit
                            (Option B).  Falls back to SFTP delete if move fails.
    5. Rescan             (action=rescan) — tells AzuraCast to re-index the
                            Requests folder so orphaned records are cleared.
    6. Verify             (action=verify) — search confirms file is gone /
                            no longer in any playlist.
    7. Mark cleaned_at in DB on success.

    Log fields: stage=request_cleanup request_id= media_id= song_id= path=
                submitted_to_azura= removed_from_azura= action= result=
    """
    title_s = title[:50] if title else "?"
    cur_fid = fid   # working copy — may be updated by the lookup step
    azura_path = f"Requests/{fn}" if fn else ""
    removed_from_azura = False
    is_local_temp = _is_local_temp_basename(fn)
    log_user_id = ""
    log_username = ""
    log_source_type = ""
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT user_id, username, source_type FROM yt_request_jobs WHERE id=?",
                (db_id,),
            ).fetchone()
            if row:
                log_user_id = row[0] or ""
                log_username = row[1] or ""
                log_source_type = row[2] or ""
    except Exception:
        pass

    def _log(action: str, result: str, **extra: str) -> None:
        extras = "".join(f" {k}={v}" for k, v in extra.items())
        print(
            f"{_LOG} stage=request_cleanup"
            f" request_id={db_id}"
            f" media_id={cur_fid!r}"
            f" song_id={song_id!r}"
            f" filename={fn!r}"
            f" azura_path={azura_path!r}"
            f" submitted_to_azura=true"
            f" removed_from_azura={str(removed_from_azura).lower()}"
            f" action={action}"
            f" result={result}{extras}"
        )

    # ── 1. Lookup media_id if azura_file_id was never stored ─────────────────
    if fn and ("/" in fn or "\\" in fn):
        _log("cleanup_safety_skip", "fail", reason="path_not_basename")
        diag.log_radio_event(
            "cleanup_safety_skip",
            request_id=db_id,
            user_id=log_user_id,
            username=log_username,
            title=title_s,
            source_type=log_source_type,
            azura_file_id=cur_fid,
            azura_song_id=song_id,
            temp_path=fn,
            source_path="",
            reason="path_not_basename",
        )
        return False

    if log_source_type in ("local", "local_copy", "local_replay") and not is_local_temp:
        _log("cleanup_safety_skip", "fail", reason="local_source_not_protected_temp")
        diag.log_radio_event(
            "cleanup_safety_skip",
            request_id=db_id,
            user_id=log_user_id,
            username=log_username,
            title=title_s,
            source_type=log_source_type,
            azura_file_id=cur_fid,
            azura_song_id=song_id,
            temp_path=fn,
            source_path="",
            reason="local_source_not_protected_temp",
        )
        return False

    if is_local_temp:
        diag.log_radio_event(
            "cleanup_started",
            request_id=db_id,
            user_id=log_user_id,
            username=log_username,
            title=title_s,
            source_type=log_source_type,
            azura_file_id=cur_fid,
            azura_song_id=song_id,
            temp_path=fn,
            source_path="",
        )

    # ── 1. Lookup media_id if azura_file_id was never stored ─────────────────
    if not cur_fid and fn:
        try:
            row = azura.search_media(fn)
            if row:
                cur_fid = str(row.get("id") or "")
                _log("lookup_media_id", "found" if cur_fid else "not_found",
                     found_id=repr(cur_fid))
                if cur_fid:
                    rq.set_azura_file_id_if_empty(db_id, cur_fid)
            else:
                _log("lookup_media_id", "not_found")
        except Exception as exc:
            _log("lookup_media_id", "error", error=repr(str(exc)))

    # ── 2. Remove from Requests playlist ─────────────────────────────────────
    if song_id:
        removed_queue = azura.remove_queue_items_for_song(song_id)
        if removed_queue:
            removed_from_azura = True
            diag.log_radio_event(
                "removed_from_azura_queue",
                request_id=db_id,
                user_id=log_user_id,
                username=log_username,
                title=title_s,
                source_type=log_source_type,
                azura_file_id=cur_fid,
                azura_song_id=song_id,
                temp_path=fn,
            )
        _log("remove_from_queue", "success" if removed_queue else "none",
             removed=str(removed_queue))

    # ── 3. Remove from Requests playlist ─────────────────────────────────────
    # Stops AutoDJ from re-queuing the file even before deletion completes.
    if cur_fid:
        ok_pl = azura.clear_file_playlists(cur_fid)
        removed_from_azura = bool(removed_from_azura or ok_pl)
        if ok_pl:
            diag.log_radio_event(
                "removed_from_azura_playlist",
                request_id=db_id,
                user_id=log_user_id,
                username=log_username,
                title=title_s,
                source_type=log_source_type,
                azura_file_id=cur_fid,
                azura_song_id=song_id,
                temp_path=fn,
            )
        _log("remove_from_playlist", "success" if ok_pl else "fail")

    # ── 4. Delete via AzuraCast API ──────────────────────────────────────────
    ok_api = False
    if cur_fid:
        ok_api = azura.delete_media_file(cur_fid)
        if ok_api:
            removed_from_azura = True
            diag.log_radio_event(
                "removed_from_azura_media",
                request_id=db_id,
                user_id=log_user_id,
                username=log_username,
                title=title_s,
                source_type=log_source_type,
                azura_file_id=cur_fid,
                azura_song_id=song_id,
                temp_path=fn,
            )
        _log("delete_file", "success" if ok_api else "fail", method="api")

    # ── 5. SFTP move to PlayedRequests (Option B) ────────────────────────────
    # Move the physical file out of /Requests so AzuraCast cannot replay it.
    # Falls back to SFTP delete if the move fails (e.g. cross-filesystem server).
    ok_sftp = False
    if fn:
        ok_sftp = azura.sftp_move_to_played(fn)
        if ok_sftp:
            removed_from_azura = True
        _log("move_to_played", "success" if ok_sftp else "fail", method="sftp_rename_or_copy")
        if not ok_sftp:
            ok_sftp = azura.sftp_delete_file(fn)
            if ok_sftp:
                removed_from_azura = True
            _log("delete_file", "success" if ok_sftp else "fail", method="sftp_fallback")

    ok = ok_api or ok_sftp

    # ── 6. Rescan — let AzuraCast update its media DB ────────────────────────
    ok_rescan = azura.rescan_requests_folder()
    _log("rescan", "success" if ok_rescan else "fail")

    # ── 7. Verify — confirm file is gone / not in any playlist ───────────────
    verify_ok = azura.verify_file_deleted(fn, wait_secs=3.0)
    _log("verify", "success" if verify_ok else "fail")
    if not verify_ok:
        diag.log_radio_event(
            "cleanup_failed",
            request_id=db_id,
            user_id=log_user_id,
            username=log_username,
            title=title_s,
            source_type=log_source_type,
            azura_file_id=cur_fid,
            azura_song_id=song_id,
            temp_path=fn,
            reason="verify_failed",
        )

    # ── 8. Mark cleaned_at ───────────────────────────────────────────────────
    if ok or removed_from_azura:
        _db_set_cleaned(db_id)
        _log("cleanup_complete", "success", title=repr(title_s))
        if is_local_temp:
            diag.log_radio_event(
                "removed_from_azura",
                request_id=db_id,
                user_id=log_user_id,
                username=log_username,
                title=title_s,
                source_type=log_source_type,
                azura_file_id=cur_fid,
                azura_song_id=song_id,
                temp_path=fn,
                source_path="",
            )
            diag.log_radio_event(
                "temp_deleted",
                request_id=db_id,
                user_id=log_user_id,
                username=log_username,
                title=title_s,
                source_type=log_source_type,
                azura_file_id=cur_fid,
                azura_song_id=song_id,
                temp_path=fn,
                source_path="",
            )
        return True
    else:
        _log("cleanup_complete", "fail", title=repr(title_s),
             note="will_retry_on_next_cycle")
        diag.log_radio_event(
            "cleanup_failed",
            request_id=db_id,
            user_id=log_user_id,
            username=log_username,
            title=title_s,
            source_type=log_source_type,
            azura_file_id=cur_fid,
            azura_song_id=song_id,
            temp_path=fn,
        )
        return False


def _nowplaying_matches_request(fid: str, song_id: str, fn: str) -> bool:
    """Return true only when Azura still reports the finished request on-air."""
    try:
        np = azura.fetch_nowplaying() or {}
        np_obj = np.get("now_playing") or {}
        np_song = np_obj.get("song") or {}
        np_media = np_obj.get("media") or {}
        np_fid = str(np_media.get("id") or "").strip()
        np_song_id = (np_song.get("unique_id") or np_song.get("id") or "").strip()
        np_path = (np_media.get("path") or "").strip()
        np_fn = np_path.rsplit("/", 1)[-1].strip()
        if fid and np_fid and fid == np_fid:
            return True
        if song_id and np_song_id and song_id == np_song_id:
            return True
        if fn and np_fn and fn.lower() == np_fn.lower():
            return True
    except Exception as exc:
        print(f"{_LOG} nowplaying request match error: {exc!r}")
    return False


def _log_replay_guard_blocked(
    job_id: int,
    status: str,
    title: str = "",
    temp_path: str = "",
    azura_file_id: str = "",
    azura_song_id: str = "",
    reason: str = "",
) -> None:
    diag.log_radio_event(
        "replay_guard_blocked",
        request_id=job_id,
        status=status,
        title=title,
        temp_path=temp_path,
        azura_file_id=azura_file_id,
        azura_song_id=azura_song_id,
        reason=reason,
    )


# ─── Track event handlers ─────────────────────────────────────────────────────

async def _on_request_finished(bot: "BaseBot", db_id: int) -> None:
    global _cur_req_id, _live_req, _submitted_jids, _finished_jids
    if db_id in _finished_jids:
        print(f"{_LOG} stage=request_finished_guard request_id={db_id} already_handled=true")
        return
    with _lock:
        _cur_req_id = 0
        _live_req   = None
        _submitted_jids.discard(db_id)
    print(f"{_LOG} stage=request_live_clear request_id={db_id}")

    job = _db_get_job(db_id)
    job_status = (job.get("status") if job else None) or ""

    # Safety net: never mark a request as played unless it was actually confirmed
    # playing. If status is still 'ready', it was queued but AzuraCast never
    # played it this cycle — leave it in the queue for the next poll.
    if job_status != "playing":
        print(
            f"[QUEUE_GUARD] _on_request_finished: job={db_id} status={job_status!r}"
            f" — not playing, refusing to mark as played. Queue preserved."
        )
        return

    _finished_jids.add(db_id)
    rq.mark_played(db_id, reason="playback_engine_finished")
    if job:
        _db_backfill_playfav_source_after_play(job)

    fn_s  = (job.get("filename")      if job else None) or "?"
    fid_s = (job.get("azura_file_id") if job else None) or "?"
    ttl_s = (job.get("title")         if job else None) or "?"
    usr_s = (job.get("username")      if job else None) or "?"
    sid_s = (job.get("azura_song_id") if job else None) or "?"
    path_s = f"Requests/{fn_s}" if fn_s and fn_s != "?" else ""
    print(
        f"{_LOG} stage=request_finished"
        f" request_id={db_id}"
        f" media_id={fid_s!r}"
        f" song_id={sid_s!r}"
        f" filename={fn_s!r}"
        f" azura_path={path_s!r}"
        f" title={ttl_s!r}"
        f" username={usr_s!r}"
    )

    if job:
        fid  = (job.get("azura_file_id") or "").strip()
        fn   = (job.get("filename")      or "").strip()
        # Re-attempt deletion — idempotent: if _on_new_track already deleted
        # the file, the API returns 404 and sftp returns file-not-found.
        # Both are treated as success so cleaned_at is set.
        if fid or fn:
            loop = asyncio.get_running_loop()
            cleanup_ok = await loop.run_in_executor(
                None, _delete_request_file,
                db_id, fid, fn, job.get("title", "?"),
                (job.get("azura_song_id") or "").strip(),
            )
            print(
                f"{_LOG} stage=request_azura_consume"
                f" request_id={db_id}"
                f" azura_file_id={fid!r}"
                f" azura_song_id={(job.get('azura_song_id') or '').strip()!r}"
                f" azura_path={(f'Requests/{fn}' if fn else '')!r}"
                f" submitted_to_azura=true"
                f" removed_from_azura={str(bool(cleanup_ok)).lower()}"
            )
            if cleanup_ok and _db_count_active() <= 0:
                sid = (job.get("azura_song_id") or "").strip()
                still_current = await loop.run_in_executor(
                    None, _nowplaying_matches_request, fid, sid, fn
                )
                if still_current:
                    skipped = await loop.run_in_executor(None, azura.skip_current)
                    _log_replay_guard_blocked(
                        db_id,
                        rq.get_job_status(db_id),
                        title=job.get("title", "?"),
                        temp_path=fn,
                        azura_file_id=fid,
                        azura_song_id=sid,
                        reason="finished_request_still_current",
                    )
                    print(
                        f"{_LOG} stage=replay_guard_blocked"
                        f" request_id={db_id}"
                        f" skip_current={str(bool(skipped)).lower()}"
                        f" reason=finished_request_still_current"
                    )

    remaining = _db_count_active()
    print(
        f"{_LOG} stage=request_cleanup request_id={db_id}"
        f" remaining_in_queue={remaining}"
    )
    if remaining > 0:
        print(
            f"[QUEUE_GUARD] skip switch_to_vibe because"
            f" active_requests={remaining}"
        )
        print(f"[QUEUE_GUARD] pending request preserved")
    else:
        with _lock:
            cur_mode = _mode
        if cur_mode != "vibe":
            await _switch_to_vibe(bot)
        else:
            print(f"{_LOG} Already in VIBE mode — skipping redundant playlist switch")


async def _on_local_replay_finished(bot: "BaseBot", temp_basename: str) -> None:
    """
    Called when a tmp_replay_* file stops playing (song change detected in _poll_loop).

    Runs the normal _delete_request_file cleanup path independently of
    _db_match_request, then marks local_replay_jobs cleanup_complete.

    Safety: only acts on filenames starting with tmp_replay_.
    """
    _LR  = "[LOCAL_REPLAY_CLEANUP]"
    loop = asyncio.get_running_loop()

    if not _is_local_temp_basename(temp_basename):
        print(f"{_LR} safety guard: ignored non-replay basename={temp_basename!r}")
        diag.log_radio_event("cleanup_safety_skip", temp_path=temp_basename)
        return

    # ── Fetch local_replay_jobs row ───────────────────────────────────────────
    yt_job_id = 0
    azura_fid = ""
    lr_title  = temp_basename
    try:
        from modules.local_replay import _fetch_job_by_temp
        job = _fetch_job_by_temp(temp_basename)
        if job:
            yt_job_id = int(job.get("yt_request_job_id") or 0)
            azura_fid = (job.get("azura_file_id") or "").strip()
            lr_title  = (job.get("fav_title") or temp_basename)
            print(
                f"{_LR} found job"
                f" yt_job_id={yt_job_id}"
                f" azura_fid={azura_fid!r}"
                f" title={lr_title!r}"
            )
        else:
            print(f"{_LR} no local_replay_jobs row for temp={temp_basename!r}")
    except Exception as exc:
        print(f"{_LR} fetch error: {exc!r}")

    # ── Run normal cleanup (remove playlist, delete API, SFTP, rescan, cleaned_at) ─
    ok = False
    try:
        await loop.run_in_executor(
            None, _delete_request_file,
            yt_job_id, azura_fid, temp_basename, lr_title,
        )
        ok = True
    except Exception as exc:
        print(f"{_LR} delete error: {exc!r}")

    # ── Mark local_replay_jobs complete ──────────────────────────────────────
    try:
        from modules.local_replay import _mark_job_cleanup_complete
        await loop.run_in_executor(None, _mark_job_cleanup_complete, temp_basename)
    except Exception as exc:
        print(f"{_LR} mark_complete error: {exc!r}")

    # ── Clean up tracking set ─────────────────────────────────────────────────
    _replay_marked.discard(temp_basename)

    if ok:
        print(f"{_LR} success temp={temp_basename!r}")
    else:
        print(f"{_LR} failed, retry pending temp={temp_basename!r}")


def _title_matches(req_title: str, np_title: str) -> bool:
    """
    Decide if the AzuraCast now-playing title corresponds to our request.

    Rules (conservative to avoid remix / live / sped-up mismatches):
    - Exact lowercase match → True
    - Shorter title is ≥ 20 chars AND is a substring of the longer → True
    - First 30 chars match AND both are ≥ 20 chars → True
    - Everything else → False
    """
    if not req_title or not np_title:
        return False
    r = req_title.lower().strip()
    n = np_title.lower().strip()
    if r == n:
        return True
    shorter = r if len(r) <= len(n) else n
    longer  = n if len(r) <= len(n) else r
    if len(shorter) >= 20 and shorter in longer:
        return True
    if len(r) >= 20 and len(n) >= 20 and r[:30] == n[:30]:
        return True
    return False


async def _on_new_track(
    bot: "BaseBot", song: dict, media: "dict | None" = None
) -> None:
    global _cur_req_id, _last_ann_id, _last_ann_title, _live_req

    song_id    = (song.get("id")        or "").strip()
    song_uid   = (song.get("unique_id") or "").strip()   # stored as azura_song_id in DB
    title      = (song.get("title")     or "").strip()
    artist     = (song.get("artist")    or "").strip()
    media_id   = str((media or {}).get("id") or "").strip()
    media_path = ((media or {}).get("path") or "").strip()

    # ── Duplicate-announcement guard ──────────────────────────────────────────
    # Primary:  AzuraCast song.id (stable per unique audio content).
    # Fallback: normalized title (guards when song.id is empty/missing).
    norm_title = title.lower()[:80]
    with _lock:
        if song_id and song_id == _last_ann_id:
            return
        if not song_id and norm_title and norm_title == _last_ann_title:
            return
        _last_ann_id    = song_id
        _last_ann_title = norm_title

    # ── Path-based source detection ───────────────────────────────────────────
    # If NP media path starts with Requests/ it is definitively a request —
    # never label it AutoDJ even if the DB match fails.
    from_requests = bool(
        media_path and media_path.lstrip("/").lower().startswith("requests/")
    )

    # Use song.unique_id for DB matching (this is what yt_request stores as azura_song_id)
    match_uid = song_uid or song_id
    match = _db_match_request(match_uid, title, media_id, media_path)
    match_method = (match.get("_match_method") or "unknown") if match else "none"

    print(
        f"{_LOG} stage=request_detection"
        f" song_id={song_id!r} unique_id={song_uid!r}"
        f" media_id={media_id!r} path={media_path!r}"
        f" from_requests={from_requests}"
        f" match_method={match_method!r}"
        f" title={title!r}"
    )

    if match:
        req_title = match.get("title") or title
        req_uname = match.get("username") or ""
        db_id     = match["id"]
        fid       = (match.get("azura_file_id") or "").strip()
        fn        = (match.get("filename")      or "").strip()
        live_fid  = media_id or fid    # prefer live media_id from NP over stored
        live_fn   = media_path or fn   # prefer NP path over stored filename

        with _lock:
            _cur_req_id = db_id

        if match.get("status") == "playing":
            # _verified_skip_task already set status + announced — suppress duplicate.
            print(f"{_LOG} Request already announced by skip task — no duplicate announce")
        else:
            _db_set_status(db_id, "playing", media_id=live_fid)
            print(
                f"{_LOG} stage=request_started"
                f" request_id={db_id}"
                f" media_id={live_fid!r}"
                f" filename={live_fn!r}"
                f" title={req_title!r}"
                f" username={req_uname!r}"
                f" match_method={match_method!r}"
            )
            await ann.announce_request_live(bot, req_title, artist, req_uname)
            with _lock:
                _live_req = {
                    "title":      req_title,
                    "artist":     artist,
                    "username":   req_uname,
                    "started_at": time.time(),
                    "job_id":     db_id,
                    "media_id":   live_fid,
                    "unique_id":  (match.get("azura_song_id") or song_uid or ""),
                    "file_path":  live_fn,
                    "filename":   (match.get("filename") or ""),
                    "youtube_id": (match.get("video_id") or ""),
                }
            print(
                f"{_LOG} stage=request_live_start"
                f" request_id={db_id} username={req_uname!r} title={req_title!r}"
            )
            print(
                f"{_LOG} stage=request_announcement"
                f" request_id={db_id}"
                f" media_id={live_fid!r}"
                f" title={req_title!r}"
                f" username={req_uname!r}"
            )

        print(f"{_LOG} Now playing REQUEST: {req_title!r} by @{req_uname}")
        # Playlist switch deferred to _on_request_finished when queue empties.

    elif from_requests:
        # NP is from Requests/ folder but no DB record matched.
        # Keep _cur_req_id=0 and suppress AutoDJ announcement.
        with _lock:
            _cur_req_id = 0
        print(
            f"{_LOG} NP from Requests/ but no DB match — suppressing AutoDJ announce"
            f" path={media_path!r}"
        )

    else:
        with _lock:
            _cur_req_id = 0
            _live_req   = None
        print(f"{_LOG} stage=autodj_resume title={title!r}")
        await ann.announce_now_playing(bot, title, artist, None, cs.vibe())
        print(
            f"{_LOG} stage=autodj_announcement"
            f" title={title!r}"
            f" artist={artist!r}"
            f" vibe={cs.vibe()!r}"
        )
        print(f"{_LOG} Now playing VIBE: {title!r}")


# ─── Verified-skip background task ───────────────────────────────────────────

async def _verified_skip_task(bot: "BaseBot", job_id: int, unique_id: str) -> None:
    """
    Background asyncio task — does NOT block the poll loop.

    Submits the request to AzuraCast so it is queued for playback after the
    current song ends.  Does NOT skip the current song — only staff !skip may
    do that.

    Flow
    ────
    1. Fetch request metadata from DB; early-exit if already playing.
    2. Brief 2 s settle wait (lets AzuraCast rescan complete after upload).
    3. Submit AzuraCast request (queues it — no skip).
    4. Poll Now Playing every 1 s for up to 15 s to detect natural takeover.
       → logs stage=request_nowplaying_poll each second
    5. Match uses 6-strategy logic: media_id, song_unique_id, Requests/ path,
       filename, video_id, title fuzzy.
    6. On match  → logs stage=request_takeover_success; marks DB playing;
                   fires REQUEST LIVE announcement; queues file cleanup.
       On timeout → logs stage=request_takeover_timeout (song still waiting).
    """
    global _skip_task_active, _cur_req_id, _cur_song_id, _last_ann_id, _last_ann_title, _live_req
    _skip_task_active = True
    try:
        loop = asyncio.get_running_loop()

        # ── Fetch request metadata ─────────────────────────────────────────────
        job = _db_get_job(job_id)
        if not job:
            print(f"{_LOG} Verified-skip: job {job_id} not found — aborting")
            return

        req_title  = (job.get("title")        or "Unknown").strip()
        req_artist = (job.get("artist")        or "").strip()
        req_uname  = (job.get("username")      or "").strip()
        req_fid    = (job.get("azura_file_id") or "").strip()
        req_fn     = (job.get("filename")      or "").strip()
        req_vid    = (job.get("video_id")      or "").strip()

        if job.get("status") == "playing":
            print(f"{_LOG} Job {job_id} already playing — skip task done early")
            return
        if rq.is_terminal_status(job.get("status", "")):
            _submitted_jids.discard(job_id)
            _log_replay_guard_blocked(
                job_id,
                job.get("status", ""),
                title=req_title,
                temp_path=req_fn,
                azura_file_id=req_fid,
                azura_song_id=unique_id,
                reason="terminal_before_submit",
            )
            print(
                f"{_LOG} stage=request_submit_guard request_id={job_id}"
                f" status={job.get('status')!r} terminal=true"
            )
            return

        print(
            f"{_LOG} Verified-skip started:"
            f" job={job_id} uid={unique_id!r}"
            f" title={req_title!r} filename={req_fn!r} youtube_id={req_vid!r}"
        )

        # ── Brief settle wait: 4 × 0.5 s = 2 s ──────────────────────────────
        for _ in range(4):
            if _stop_flag.is_set():
                return
            await asyncio.sleep(0.5)

        job = _db_get_job(job_id)
        if job and job.get("status") == "playing":
            print(f"{_LOG} Job {job_id} started playing during settle wait — done")
            return
        if (not job) or rq.is_terminal_status(job.get("status", "")):
            _submitted_jids.discard(job_id)
            _log_replay_guard_blocked(
                job_id,
                (job or {}).get("status", ""),
                title=req_title,
                temp_path=req_fn,
                azura_file_id=req_fid,
                azura_song_id=unique_id,
                reason="terminal_after_settle",
            )
            print(
                f"{_LOG} stage=request_submit_guard request_id={job_id}"
                f" status={(job or {}).get('status')!r} terminal=true"
            )
            return

        if _stop_flag.is_set():
            return

        # ── Submit request to AzuraCast queue ─────────────────────────────────
        if unique_id:
            if rq.is_terminal_job(job_id):
                _submitted_jids.discard(job_id)
                _log_replay_guard_blocked(
                    job_id,
                    rq.get_job_status(job_id),
                    title=req_title,
                    temp_path=req_fn,
                    azura_file_id=req_fid,
                    azura_song_id=unique_id,
                    reason="terminal_pre_azura_submit",
                )
                print(
                    f"{_LOG} stage=request_submit_guard request_id={job_id}"
                    f" status={rq.get_job_status(job_id)!r} terminal=true"
                )
                return
            submit_ok = await loop.run_in_executor(None, azura.submit_request, unique_id)
            if not submit_ok:
                _submitted_jids.discard(job_id)
                await loop.run_in_executor(
                    None, _db_fail_and_refund, job_id, "azuracast_submit_failed"
                )
                return
            rq.mark_submitted(job_id)
            await asyncio.sleep(0.3)
            print(
                f"{_LOG} stage=request_submitted_no_skip"
                f" request_id={job_id} username={req_uname!r}"
                f" title={req_title!r}"
                f" azura_file_id={req_fid!r}"
                f" azura_song_id={unique_id!r}"
                f" azura_path={(f'Requests/{req_fn}' if req_fn else '')!r}"
                f" submitted_to_azura=true"
                f" removed_from_azura=false"
                f" — queued in AzuraCast, current song will not be interrupted"
            )

        # ── Poll Now Playing every 1 s for 15 s ────────────────────────────────
        # Passive monitor only — detects if the request starts playing naturally
        # (e.g. it was already next when uploaded).  No additional skips.
        confirmed = False

        for check in range(15):
            if _stop_flag.is_set():
                return

            await asyncio.sleep(1)

            np = await loop.run_in_executor(None, azura.fetch_nowplaying)
            if not np:
                continue

            np_obj   = np.get("now_playing") or {}
            np_song  = np_obj.get("song")   or {}
            np_media = np_obj.get("media")  or {}
            np_id    = (np_song.get("id")        or "").strip()
            np_uid   = (np_song.get("unique_id") or "").strip()
            np_title = (np_song.get("title")     or "").strip()
            np_artist= (np_song.get("artist")    or "").strip()
            np_fid   = str(np_media.get("id") or "").strip()
            np_path  = (np_media.get("path") or "").strip()
            np_fn    = np_path.rsplit("/", 1)[-1].lower() if np_path else ""
            np_lpath = np_path.lstrip("/").lower()

            # Multi-strategy match (mirrors _db_match_request priority order)
            match_method: "str | None" = None
            if req_fid and np_fid and req_fid == np_fid:
                match_method = "media_id"
            elif unique_id and np_id and np_id == unique_id:
                match_method = "song_id"
            elif unique_id and np_uid and np_uid == unique_id:
                match_method = "unique_id"
            elif np_lpath.startswith("requests/") and req_fn and np_fn and req_fn.lower() == np_fn:
                # Only match when the exact filename in Requests/ matches ours.
                # The old loose fallback ("requests_path") fired for ANY Requests/
                # song and incorrectly confirmed the local replay as playing.
                match_method = "requests_path_filename"
            elif req_fn and np_fn and req_fn.lower() == np_fn:
                match_method = "filename"
            elif req_vid and np_path and req_vid in np_path:
                match_method = "video_id"
            elif _title_matches(req_title, np_title):
                match_method = "title_fuzzy"

            print(
                f"{_LOG} stage=request_nowplaying_poll"
                f" request_id={job_id} elapsed_s={check + 1}"
                f" nowplaying_title={np_title!r}"
                f" nowplaying_media_id={np_fid!r}"
                f" path={np_path!r}"
                f" match_method={match_method!r}"
            )

            if match_method:
                print(
                    f"{_LOG} stage=request_takeover_success"
                    f" request_id={job_id} elapsed_s={check + 1}"
                    f" match_method={match_method!r}"
                    f" username={req_uname!r}"
                    f" title={req_title!r}"
                    f" nowplaying_title={np_title!r}"
                    f" media_id={np_fid!r}"
                )

                # Pre-set dedup vars so _on_new_track won't duplicate-announce.
                # Also update _cur_song_id so the poller's next cycle sees
                # song_id == prev_song_id and does NOT call _on_request_finished
                # prematurely (which would zero _live_req while audio still plays).
                with _lock:
                    _last_ann_id    = np_id or unique_id
                    _last_ann_title = np_title.lower()[:80]
                    _cur_req_id     = job_id
                    if np_id:
                        _cur_song_id = np_id

                _db_set_status(job_id, "playing", media_id=np_fid)
                diag.log_radio_event(
                    "playback_confirmed",
                    request_id=job_id,
                    user_id=job.get("user_id", ""),
                    username=job.get("username", ""),
                    title=req_title,
                    source_type=job.get("source_type", ""),
                    temp_path=req_fn,
                    azura_file_id=np_fid,
                    azura_song_id=np_uid or unique_id,
                    status="playing",
                    queue_event="request_takeover_success",
                    match_method=match_method,
                )
                display_artist = req_artist or np_artist
                await ann.announce_request_live(bot, req_title, display_artist, req_uname)
                with _lock:
                    _live_req = {
                        "title":      req_title,
                        "artist":     display_artist,
                        "username":   req_uname,
                        "started_at": time.time(),
                        "job_id":     job_id,
                        "media_id":   np_fid,
                        "unique_id":  (np_uid or unique_id),
                        "file_path":  np_path,
                        "filename":   req_fn,
                        "youtube_id": req_vid,
                    }
                print(
                    f"{_LOG} stage=request_live_start source=skip_task"
                    f" request_id={job_id} username={req_uname!r} title={req_title!r}"
                )

                # Cleanup deferred to _on_request_finished / _on_request_skipped
                confirmed = True
                break

        if not confirmed:
            print(
                f"{_LOG} stage=request_takeover_timeout"
                f" request_id={job_id} username={req_uname!r}"
                f" title={req_title!r} unique_id={unique_id!r}"
                f" elapsed_s=15 — queued, no room announcement"
            )

    except asyncio.CancelledError:
        _stop_flag.set()
        print(f"{_LOG} Verified-skip task cancelled — stop flag set")
        raise
    except Exception as exc:
        import traceback
        print(f"{_LOG} Verified-skip task error (non-fatal): {exc}")
        traceback.print_exc()
    finally:
        _skip_task_active = False


# ─── Main poll loop ───────────────────────────────────────────────────────────

async def _poll_loop(bot: "BaseBot") -> None:
    global _cur_song_id, _cur_req_id, _cur_elapsed, _cur_duration, _mode, _cur_replay_temp

    print(f"{_LOG} Poll loop started (every {POLL_INTERVAL}s)")
    loop = asyncio.get_running_loop()

    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL)
            db.set_room_setting("radio_worker_heartbeat_queue", str(time.time()))

            # ── Find ready requests (uploaded + registered in AzuraCast) ─────
            all_ready = _db_find_new_ready()
            # Sync _submitted_jids — remove IDs no longer status='ready'
            if _submitted_jids:
                ready_ids = {j["id"] for j in all_ready}
                _submitted_jids.intersection_update(ready_ids)

            # AzuraCast AutoDJ controls order — vibe+requests playlists always both ON
            with _lock:
                skip_task_busy = _skip_task_active

            # ── Submit oldest unsubmitted ready request to AzuraCast ─────────
            # Queues the song so AzuraCast plays it after the current track.
            # NEVER skips — only staff !skip may interrupt the current song.
            if all_ready and not skip_task_busy:
                next_job = _db_find_oldest_ready()
                if next_job and next_job["id"] not in _submitted_jids:
                    if rq.is_terminal_status(next_job.get("status", "")):
                        print(
                            f"{_LOG} stage=request_submit_guard"
                            f" request_id={next_job['id']} status={next_job.get('status')!r}"
                            f" terminal=true"
                        )
                        continue
                    uid = (next_job.get("azura_song_id") or "").strip()
                    if uid:
                        _submitted_jids.add(next_job["id"])
                        print(
                            f"{_LOG} Queuing request for playback (no skip): "
                            f"{next_job.get('title','?')!r} uid={uid!r}"
                        )
                        asyncio.create_task(
                            _verified_skip_task(bot, next_job["id"], uid),
                            name=f"radio_verified_submit_{next_job['id']}",
                        )
                    else:
                        print(
                            f"{_LOG} stage=request_no_uid"
                            f" job={next_job.get('id','?')!r}"
                            f" — no AzuraCast unique_id yet, skipping submit"
                        )

            # ── Fetch nowplaying from AzuraCast ──────────────────────────────
            np = await loop.run_in_executor(None, azura.fetch_nowplaying)
            if not np:
                continue
            db.set_room_setting("radio_worker_heartbeat_azura", str(time.time()))

            np_obj   = np.get("now_playing") or {}
            song     = np_obj.get("song")    or {}
            media    = np_obj.get("media")   or {}
            song_id  = (song.get("id")       or "").strip()
            elapsed  = int(np_obj.get("elapsed")  or 0)
            duration = int(np_obj.get("duration") or 0)

            # Local-replay path detection — media.path basename starts with tmp_replay_
            np_mpath = (media.get("path") or "").strip()
            np_mbase = np_mpath.rsplit("/", 1)[-1] if np_mpath else ""
            np_is_lr = _is_local_temp_basename(np_mbase)

            if not song_id:
                continue

            with _lock:
                prev_song_id     = _cur_song_id
                prev_req_id      = _cur_req_id
                prev_elapsed     = _cur_elapsed
                prev_duration    = _cur_duration
                prev_replay_temp = _cur_replay_temp
                _cur_elapsed     = elapsed
                _cur_duration    = duration
                if np_is_lr:
                    _cur_replay_temp = np_mbase
                elif song_id != prev_song_id:
                    _cur_replay_temp = ""   # clear when song changes to non-replay

            # ── Local-replay playing detection (path-based, runs every poll) ──
            # Independent of _db_match_request — fires as soon as media.path
            # shows a tmp_replay_ file so we can mark it playing promptly.
            if np_is_lr and np_mbase not in _replay_marked:
                _replay_marked.add(np_mbase)
                print(f"[LOCAL_REPLAY_CLEANUP] detected playing temp={np_mbase!r}")
                try:
                    from modules.local_replay import _mark_job_playing
                    loop.run_in_executor(None, _mark_job_playing, np_mbase)
                except Exception as _lr_exc:
                    print(f"[LOCAL_REPLAY_CLEANUP] mark_playing error: {_lr_exc!r}")

            # ── Replay detection: same song_id but elapsed has reset ──────────
            # AzuraCast looped the Requests playlist — the same file started
            # playing again.  Treat this as the song having finished so cleanup
            # fires and AzuraCast is moved back to vibe mode.
            if (
                prev_req_id
                and prev_req_id not in _finished_jids
                and song_id == prev_song_id
                and duration > 0
                and elapsed >= duration
            ):
                print(
                    f"{_LOG} End-of-track detected: request {prev_req_id}"
                    f" elapsed={elapsed} dur={duration}"
                    f" — consuming Azura request before replay"
                )
                await _on_request_finished(bot, prev_req_id)
                continue

            if (
                song_id     == prev_song_id
                and prev_req_id
                and prev_req_id not in _finished_jids
                and prev_duration > 30
                and prev_elapsed  > prev_duration * 0.75
                and elapsed       < min(POLL_INTERVAL * 2 + 2, 14)
            ):
                print(
                    f"{_LOG} Replay detected: request {prev_req_id}"
                    f" elapsed {prev_elapsed}→{elapsed} dur={prev_duration}"
                    f" — treating as finished"
                )
                await _on_request_finished(bot, prev_req_id)

            if song_id == prev_song_id:
                continue

            # ── Song changed ──────────────────────────────────────────────────
            with _lock:
                _cur_song_id     = song_id
                _cur_replay_temp = np_mbase if np_is_lr else ""
            print(f"{_LOG} Song change: {prev_song_id!r} → {song_id!r}")

            # Local-replay cleanup: previous song was a tmp_replay_ file.
            # Runs independently of _db_match_request / prev_req_id so that
            # cleanup fires even when azura_song_id matching fails.
            if prev_replay_temp and _is_local_temp_basename(prev_replay_temp):
                print(
                    f"[LOCAL_REPLAY_CLEANUP] song changed,"
                    f" deleting temp={prev_replay_temp!r}"
                )
                asyncio.create_task(
                    _on_local_replay_finished(bot, prev_replay_temp),
                    name="radio_local_replay_cleanup",
                )

            if prev_req_id:
                # Only fire cleanup if the job was actually confirmed playing.
                # If status is still 'ready' (never confirmed), the request
                # was never played — preserve it and reset _cur_req_id only.
                _prev_job    = _db_get_job(prev_req_id)
                _prev_status = (_prev_job.get("status") if _prev_job else None) or ""
                if _prev_status == "playing":
                    await _on_request_finished(bot, prev_req_id)
                else:
                    print(
                        f"[QUEUE_GUARD] song changed but prev_req={prev_req_id}"
                        f" status={_prev_status!r} (not playing) — preserving,"
                        f" clearing _cur_req_id only"
                    )
                    with _lock:
                        _cur_req_id = 0

            await _on_new_track(bot, song, media)

        except asyncio.CancelledError:
            # Bot is disconnecting/restarting — signal all executor threads to stop early
            _stop_flag.set()
            print(f"{_LOG} Poll loop cancelled — stop flag set, bot disconnecting")
            return
        except Exception as exc:
            import traceback
            print(f"{_LOG} Poll error (non-fatal): {exc}")
            traceback.print_exc()
            await asyncio.sleep(POLL_INTERVAL)


# ─── Public API ───────────────────────────────────────────────────────────────

def get_playlist_mode() -> str:
    with _lock:
        return _mode


def get_cur_duration() -> int:
    """Return the AzuraCast-reported total duration of the current song (seconds).
    Updated each poll cycle. Used by dj_announcer for room-announce progress bars."""
    with _lock:
        return _cur_duration


def get_cur_elapsed() -> int:
    """Return the AzuraCast-reported elapsed seconds for the current song.
    Updated each poll cycle BEFORE _on_new_track fires, so the value is
    accurate when startup/reconnect announcements are built."""
    with _lock:
        return _cur_elapsed


def get_current_request() -> "dict | None":
    """Return the DB record of the request currently playing, or None."""
    return _db_find_playing()


def get_live_request() -> "dict | None":
    """Source-of-truth for whether a request is currently live.

    Priority order:
      1. _live_req (in-memory) — set at announcement, cleared on finish/skip/AutoDJ.
      2. _db_find_playing()    — DB fallback for restart safety.

    Logs stage=now_playing_mode source=memory|db|none.
    """
    with _lock:
        cached = _live_req
    if cached is not None:
        print(
            f"{_LOG} stage=now_playing_mode source=memory"
            f" job_id={cached.get('job_id')} username={cached.get('username')!r}"
        )
        return cached
    db_row = _db_find_playing()
    if db_row:
        print(
            f"{_LOG} stage=now_playing_mode source=db"
            f" job_id={db_row.get('id')}"
        )
        return db_row
    return None


def match_and_recover(
    song_id: str,
    song_uid: str,
    np_title: str,
    media_id: str,
    media_path: str,
    np_artist: str = "",
) -> "dict | None":
    """Self-correction hook for handle_nowplaying.

    When the background poller hasn't set _live_req (e.g. after a restart or
    transient lag), !now calls this to:
      1. Try all _db_match_request strategies against the current AzuraCast song.
      2. Promote status to 'playing' if the job is still 'ready'.
      3. Populate _live_req so subsequent !now calls hit the memory fast-path.

    Logs stage=now_playing_mode source=recovered on success, source=none on miss.
    Returns the populated _live_req dict, or None if no match.
    """
    global _live_req, _cur_req_id

    with _lock:
        if _live_req is not None:
            return _live_req

    match_uid = song_uid or song_id
    match = _db_match_request(match_uid, np_title, media_id, media_path)
    if not match:
        print(f"{_LOG} stage=now_playing_mode source=none np_title={np_title!r}")
        return None

    job_id    = match["id"]
    req_title = (match.get("title")         or np_title).strip()
    req_uname = (match.get("username")      or "").strip()
    req_fn    = (match.get("filename")      or "").strip()
    req_vid   = (match.get("video_id")      or "").strip()
    req_uid   = (match.get("azura_song_id") or match_uid).strip()

    if match.get("status") == "ready":
        _db_set_status(job_id, "playing", media_id=media_id)

    with _lock:
        _cur_req_id = job_id
        if _live_req is None:
            _live_req = {
                "title":      req_title,
                "artist":     np_artist,
                "username":   req_uname,
                "started_at": time.time(),
                "job_id":     job_id,
                "media_id":   media_id,
                "unique_id":  req_uid,
                "file_path":  media_path or "",
                "filename":   req_fn,
                "youtube_id": req_vid,
            }
        recovered = _live_req

    print(
        f"{_LOG} stage=now_playing_mode source=recovered"
        f" job_id={job_id} username={req_uname!r} title={req_title!r}"
        f" match_method={match.get('_match_method')!r}"
    )
    return recovered


def match_nowplaying_to_job(np: dict) -> "dict | None":
    """
    Public wrapper around _db_match_request for the !skip handler.

    Extracts song/media fields from a raw AzuraCast NP response and returns
    the matching active job (status in ready/playing) or None.

    Used by handle_skip so a pending/ready request is consumed even when the
    poll loop hasn't yet promoted its status from 'ready' to 'playing'.
    """
    np_obj   = (np.get("now_playing") or {})
    song     = (np_obj.get("song")    or {})
    media    = (np_obj.get("media")   or {})
    song_uid = (song.get("unique_id") or song.get("id") or "").strip()
    title    = (song.get("title")     or "").strip()
    media_id = str(media.get("id") or "").strip()
    path     = (media.get("path")     or "").strip()
    return _db_match_request(song_uid, title, media_id, path)


async def on_request_skipped(bot: "BaseBot", job_id: int) -> None:
    """
    Public — called by the !skip command handler after AzuraCast confirms
    the skip succeeded.

    Mirrors _on_request_finished but logs stage=request_skipped so audit
    logs can distinguish staff-initiated skips from natural end-of-song
    transitions.  Queues file cleanup (move to PlayedRequests/, rescan)
    and switches playlists back to VIBE mode if the queue is now empty.
    """
    global _cur_req_id, _live_req, _submitted_jids, _finished_jids
    _finished_jids.add(job_id)
    with _lock:
        if _cur_req_id == job_id:
            _cur_req_id = 0
        _live_req = None
        _submitted_jids.discard(job_id)
    print(f"{_LOG} stage=autodj_resume source=skip request_id={job_id}")

    job = _db_get_job(job_id)
    rq.mark_played(job_id, reason="playback_engine_skipped")

    fn_s  = (job.get("filename")      if job else None) or "?"
    fid_s = (job.get("azura_file_id") if job else None) or "?"
    ttl_s = (job.get("title")         if job else None) or "?"
    usr_s = (job.get("username")      if job else None) or "?"
    sid_s = (job.get("azura_song_id") if job else None) or "?"
    path_s = f"Requests/{fn_s}" if fn_s and fn_s != "?" else ""
    print(
        f"{_LOG} stage=request_skipped"
        f" request_id={job_id}"
        f" media_id={fid_s!r}"
        f" song_id={sid_s!r}"
        f" filename={fn_s!r}"
        f" azura_path={path_s!r}"
        f" title={ttl_s!r}"
        f" username={usr_s!r}"
    )

    if job:
        fid = (job.get("azura_file_id") or "").strip()
        fn  = (job.get("filename")      or "").strip()
        if fid or fn:
            loop = asyncio.get_running_loop()
            cleanup_ok = await loop.run_in_executor(
                None, _delete_request_file,
                job_id, fid, fn, job.get("title", "?"),
                (job.get("azura_song_id") or "").strip(),
            )
            print(
                f"{_LOG} stage=request_azura_consume"
                f" request_id={job_id}"
                f" azura_file_id={fid!r}"
                f" azura_song_id={(job.get('azura_song_id') or '').strip()!r}"
                f" azura_path={(f'Requests/{fn}' if fn else '')!r}"
                f" submitted_to_azura=true"
                f" removed_from_azura={str(bool(cleanup_ok)).lower()}"
            )

    remaining = _db_count_active()
    print(
        f"{_LOG} stage=request_cleanup request_id={job_id}"
        f" remaining_in_queue={remaining} source=skip"
    )
    if remaining > 0:
        print(
            f"[QUEUE_GUARD] skip switch_to_vibe because"
            f" active_requests={remaining}"
        )
        print(f"[QUEUE_GUARD] pending request preserved")
    else:
        with _lock:
            cur_mode = _mode
        if cur_mode != "vibe":
            await _switch_to_vibe(bot)
        else:
            print(f"{_LOG} Already in VIBE mode — skipping redundant playlist switch")


async def apply_vibe_change(bot: "BaseBot") -> None:
    """
    Called by the !vibe command after config_store.set_vibe() has been saved.
    Always re-applies playlists immediately (vibe + requests always both ON).
    """
    await _apply_vibe_playlists()


async def startup_playback_engine(bot: "BaseBot") -> None:
    """
    Entry point called from on_start (via startup_radio → media_cleanup.start).

    Returns IMMEDIATELY — no HTTP calls happen here.  All I/O is deferred to
    _startup_init_task which runs as a background asyncio task.  This ensures
    the Highrise WebSocket event loop is never delayed by AzuraCast HTTP calls
    at startup time, and the bot can always respond to Highrise traffic.
    """
    global _started
    if _started:
        print(f"{_LOG} Already started — skipping duplicate call")
        return
    _started = True
    _stop_flag.clear()   # Reset flag in case this is a reconnect cycle
    print(f"[DJ_RADIO] playback engine starting…")
    print(f"{_LOG} Playback engine scheduled ✓ (init deferred)")
    asyncio.create_task(_startup_init_task(bot), name="radio_playback_startup")


async def _startup_init_task(bot: "BaseBot") -> None:
    """
    Deferred startup — runs as a background asyncio task after on_start returns.

    Waits 2 s for the Highrise WebSocket to fully settle, applies the correct
    AzuraCast playlist configuration, then launches the persistent poll loop.
    Failures here are non-fatal: the poll loop still starts so the engine is
    at least partially operational even if AzuraCast is temporarily unreachable.
    """
    global _mode, _cur_req_id
    try:
        await asyncio.sleep(2)   # Let the WebSocket connection settle before HTTP calls

        playing_now = _db_find_playing()
        in_flight   = _db_count_active()

        if playing_now:
            with _lock:
                _cur_req_id = playing_now["id"]
                _mode       = "vibe"
            print(f"{_LOG} Recovery: request was playing db_id={playing_now['id']!r}")

        elif in_flight > 0:
            with _lock:
                _mode = "vibe"
            print(f"{_LOG} Recovery: {in_flight} request(s) in queue — vibe+requests both ON")

        else:
            with _lock:
                _mode = "vibe"
            print(f"{_LOG} Startup — applying VIBE/{cs.vibe().upper()} + Requests playlists")

        await _apply_vibe_playlists()

        asyncio.create_task(_poll_loop(bot), name="radio_playback_poll")
        from modules.yt_request import radio_request_prepare_worker as _rpw
        asyncio.create_task(_rpw(bot, _stop_flag), name="radio_prepare_worker")
        print(f"{_LOG} Playback engine ready ✓")
        print(f"[DJ_RADIO] playback engine started")

    except asyncio.CancelledError:
        print(f"{_LOG} Startup init task cancelled (bot disconnecting before init completed)")
        raise

    except Exception as exc:
        import traceback
        print(f"{_LOG} Startup init error (non-fatal): {exc}")
        traceback.print_exc()
        # Start the poll loop anyway — it may self-correct once AzuraCast is reachable
        try:
            asyncio.create_task(_poll_loop(bot), name="radio_playback_poll")
            print(f"{_LOG} Poll loop started despite init error")
        except Exception as _pe:
            print(f"{_LOG} Could not start poll loop: {_pe}")
