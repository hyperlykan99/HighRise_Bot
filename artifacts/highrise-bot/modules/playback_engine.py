"""
modules/playback_engine.py
--------------------------
Bot-as-source-of-truth AzuraCast playback engine.

The bot controls what AzuraCast streams by managing which playlists are enabled.
AzuraCast is used only as the audio streaming backend — it never decides what
plays next on its own.

Playlist State Machine
──────────────────────
  VIBE      — only the active vibe playlist (Chill or Party) is enabled
  REQUESTS  — only the Requests playlist is enabled; Chill + Party disabled

Transitions
───────────
  New request ready (status='done' + azura_file_id set in DB)
      → switch to REQUESTS mode, optionally skip current vibe track
  Last queued request finishes playing
      → switch back to VIBE mode (Chill or Party per saved vibe)

Song Detection  (polls every POLL_INTERVAL seconds)
───────────────
  On song change:
    1. Previous track was a request → mark played in DB, delete file from AzuraCast
    2. New track matches a queued request → mark playing, announce to room
    3. New track is a vibe song → announce with vibe prefix
    4. Queue just emptied → switch playlists back to vibe folder
"""
from __future__ import annotations
import asyncio
import threading
from typing import TYPE_CHECKING

import database as db
import modules.azuracast_controller as azura
import modules.config_store         as cs
import modules.dj_announcer         as ann

if TYPE_CHECKING:
    from highrise import BaseBot

_LOG          = "[PLAYBACK]"
POLL_INTERVAL = 5       # seconds between AzuraCast nowplaying polls
_STATE_NS     = "playback_"

# ─── Module-level state ───────────────────────────────────────────────────────
_lock                   = threading.Lock()
_stop_flag              = threading.Event()    # Set on shutdown; executor threads check this
_stage_promotion_lock   = threading.Lock()     # Prevents concurrent staged-job promotions
_started:         bool  = False
_mode:            str   = "vibe"    # "vibe" | "requests"
_cur_song_id:     str   = ""        # AzuraCast song.id currently playing
_cur_req_id:      int   = 0         # yt_request_jobs.id of the active request (0 = none)
_last_ann_id:     str   = ""        # song.id last announced (dedup)
_last_ann_title:  str   = ""        # normalized title last announced (title-fallback dedup)
_skip_task_active: bool = False     # True while a _verified_skip_task is running
_cur_elapsed:     int   = 0         # AzuraCast elapsed seconds for current song
_cur_duration:    int   = 0         # AzuraCast total duration of current song

_ACT = ("pending", "downloading", "uploading", "staged", "done", "queued", "playing")
_ACT_PH = ",".join("?" * len(_ACT))   # SQL placeholders for IN clause

_COLS = (
    "id", "user_id", "username", "title", "filename",
    "azura_file_id", "azura_song_id", "coins_charged", "status", "video_id",
)
_SEL = (
    "id, user_id, username, title, filename, "
    "azura_file_id, azura_song_id, coins_charged, status, video_id"
)


def _jrow(row) -> dict:
    return dict(zip(_COLS, row))


# ─── Persistent state ─────────────────────────────────────────────────────────

def _save(key: str, value: str) -> None:
    try:
        db.set_room_setting(_STATE_NS + key, value)
    except Exception as exc:
        print(f"{_LOG} _save({key!r}) error: {exc}")


# ─── DB job queries ───────────────────────────────────────────────────────────

def _db_find_oldest_queued() -> "dict | None":
    """The oldest request with status='queued' that hasn't started playing yet."""
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs "
                "WHERE status='queued' AND played_at IS NULL "
                "ORDER BY id ASC LIMIT 1",
            ).fetchone()
            return _jrow(row) if row else None
    except Exception as exc:
        print(f"{_LOG} _db_find_oldest_queued: {exc}")
        return None


def _db_find_new_done() -> list:
    """
    Jobs that have been uploaded + registered with AzuraCast (status='done',
    azura_file_id set) but not yet promoted to 'queued'.
    """
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                f"SELECT {_SEL} FROM yt_request_jobs "
                "WHERE status='done' AND azura_file_id!='' "
                "  AND played_at IS NULL AND cleaned_at IS NULL "
                "ORDER BY id ASC",
            ).fetchall()
            return [_jrow(r) for r in rows]
    except Exception as exc:
        print(f"{_LOG} _db_find_new_done: {exc}")
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
    (status IN ('queued','playing') AND azura_file_id set).
    Used to decide whether the /Requests slot is free for a staged promotion.
    """
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM yt_request_jobs "
                "WHERE status IN ('queued','playing') "
                "  AND azura_file_id != '' AND played_at IS NULL",
            ).fetchone()
            return row[0] if row else 0
    except Exception as exc:
        print(f"{_LOG} _db_count_active_in_requests: {exc}")
        return 0


def _db_set_status(db_id: int, status: str, media_id: str = "") -> None:
    if not db_id:
        return
    try:
        with db.db_conn() as conn:
            if status == "played":
                conn.execute(
                    "UPDATE yt_request_jobs "
                    "SET status='played', played_at=datetime('now') WHERE id=?",
                    (db_id,),
                )
            elif status == "playing":
                if media_id:
                    conn.execute(
                        "UPDATE yt_request_jobs "
                        "SET status='playing', started_at=datetime('now'), "
                        "azura_file_id=CASE WHEN (azura_file_id IS NULL OR azura_file_id='') "
                        "THEN ? ELSE azura_file_id END "
                        "WHERE id=?",
                        (media_id, db_id),
                    )
                else:
                    conn.execute(
                        "UPDATE yt_request_jobs "
                        "SET status='playing', started_at=datetime('now') WHERE id=?",
                        (db_id,),
                    )
            else:
                conn.execute(
                    "UPDATE yt_request_jobs SET status=? WHERE id=?",
                    (status, db_id),
                )
    except Exception as exc:
        print(f"{_LOG} _db_set_status({db_id},{status!r}): {exc}")


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
    active = ("done", "queued", "playing")
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
                    # Fallback: oldest active job (path proves it's a request)
                    if rows:
                        j = _jrow(rows[0])
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

async def _apply_requests_playlists() -> None:
    """Enable Requests playlist only. Disable Chill and Party."""
    loop     = asyncio.get_running_loop()
    req_id   = cs.requests_playlist_id()
    chill_id = cs.chill_playlist_id()
    party_id = cs.party_playlist_id()

    if chill_id:
        await loop.run_in_executor(None, azura.set_playlist_enabled, chill_id, False)
    if party_id:
        await loop.run_in_executor(None, azura.set_playlist_enabled, party_id, False)
    if req_id:
        await loop.run_in_executor(None, azura.set_playlist_enabled, req_id, True)

    _save("playlist_mode", "requests")
    print(f"{_LOG} Playlists → REQUESTS (req={req_id or 'unset'})")


async def _apply_vibe_playlists() -> None:
    """Enable the current vibe playlist only. Disable Requests + the other vibe."""
    loop     = asyncio.get_running_loop()
    v        = cs.vibe()
    req_id   = cs.requests_playlist_id()
    chill_id = cs.chill_playlist_id()
    party_id = cs.party_playlist_id()

    if req_id:
        await loop.run_in_executor(None, azura.set_playlist_enabled, req_id, False)

    if v == "party":
        if chill_id:
            await loop.run_in_executor(None, azura.set_playlist_enabled, chill_id, False)
        if party_id:
            await loop.run_in_executor(None, azura.set_playlist_enabled, party_id, True)
    else:
        if chill_id:
            await loop.run_in_executor(None, azura.set_playlist_enabled, chill_id, True)
        if party_id:
            await loop.run_in_executor(None, azura.set_playlist_enabled, party_id, False)

    _save("playlist_mode", "vibe")
    print(f"{_LOG} Playlists → VIBE/{v.upper()}")


async def _switch_to_requests(bot: "BaseBot") -> None:
    """Enable Requests playlist only. Does NOT skip — skip is handled by _verified_skip_task."""
    global _mode
    with _lock:
        _mode = "requests"
    await _apply_requests_playlists()
    print(f"{_LOG} Switched → REQUESTS (skip handled by verified-skip task)")


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
    if not db_id:
        return
    try:
        with db.db_conn() as conn:
            conn.execute(
                "UPDATE yt_request_jobs SET cleaned_at=datetime('now') WHERE id=?",
                (db_id,),
            )
    except Exception as exc:
        print(f"{_LOG} _db_set_cleaned({db_id}): {exc}")


def _delete_request_file(db_id: int, fid: str, fn: str, title: str) -> None:
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

    Log fields: stage=request_cleanup  request_id=  media_id=  filename=
                action=  result=success|fail  [error=]
    """
    title_s = title[:50] if title else "?"
    cur_fid = fid   # working copy — may be updated by the lookup step

    def _log(action: str, result: str, **extra: str) -> None:
        extras = "".join(f" {k}={v}" for k, v in extra.items())
        print(
            f"{_LOG} stage=request_cleanup"
            f" request_id={db_id}"
            f" media_id={cur_fid!r}"
            f" filename={fn!r}"
            f" action={action}"
            f" result={result}{extras}"
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
                    try:
                        with db.db_conn() as conn:
                            conn.execute(
                                "UPDATE yt_request_jobs SET azura_file_id=?"
                                " WHERE id=?"
                                " AND (azura_file_id IS NULL OR azura_file_id='')",
                                (cur_fid, db_id),
                            )
                    except Exception:
                        pass
            else:
                _log("lookup_media_id", "not_found")
        except Exception as exc:
            _log("lookup_media_id", "error", error=repr(str(exc)))

    # ── 2. Remove from Requests playlist ─────────────────────────────────────
    # Stops AutoDJ from re-queuing the file even before deletion completes.
    if cur_fid:
        ok_pl = azura.clear_file_playlists(cur_fid)
        _log("remove_from_playlist", "success" if ok_pl else "fail")

    # ── 3. Delete via AzuraCast API ──────────────────────────────────────────
    ok_api = False
    if cur_fid:
        ok_api = azura.delete_media_file(cur_fid)
        _log("delete_file", "success" if ok_api else "fail", method="api")

    # ── 4. SFTP move to PlayedRequests (Option B) ────────────────────────────
    # Move the physical file out of /Requests so AzuraCast cannot replay it.
    # Falls back to SFTP delete if the move fails (e.g. cross-filesystem server).
    ok_sftp = False
    if fn:
        ok_sftp = azura.sftp_move_to_played(fn)
        _log("move_to_played", "success" if ok_sftp else "fail", method="sftp_rename_or_copy")
        if not ok_sftp:
            ok_sftp = azura.sftp_delete_file(fn)
            _log("delete_file", "success" if ok_sftp else "fail", method="sftp_fallback")

    ok = ok_api or ok_sftp

    # ── 5. Rescan — let AzuraCast update its media DB ────────────────────────
    ok_rescan = azura.rescan_requests_folder()
    _log("rescan", "success" if ok_rescan else "fail")

    # ── 6. Verify — confirm file is gone / not in any playlist ───────────────
    verify_ok = azura.verify_file_deleted(fn, wait_secs=3.0)
    _log("verify", "success" if verify_ok else "fail")

    # ── 7. Mark cleaned_at ───────────────────────────────────────────────────
    if ok:
        _db_set_cleaned(db_id)
        _log("cleanup_complete", "success", title=repr(title_s))
    else:
        _log("cleanup_complete", "fail", title=repr(title_s),
             note="will_retry_on_next_cycle")


# ─── Track event handlers ─────────────────────────────────────────────────────

async def _on_request_finished(bot: "BaseBot", db_id: int) -> None:
    global _cur_req_id
    with _lock:
        _cur_req_id = 0

    job = _db_get_job(db_id)
    _db_set_status(db_id, "played")

    fn_s  = (job.get("filename")      if job else None) or "?"
    fid_s = (job.get("azura_file_id") if job else None) or "?"
    ttl_s = (job.get("title")         if job else None) or "?"
    usr_s = (job.get("username")      if job else None) or "?"
    print(
        f"{_LOG} stage=request_finished"
        f" request_id={db_id}"
        f" media_id={fid_s!r}"
        f" filename={fn_s!r}"
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
            loop.run_in_executor(
                None, _delete_request_file,
                db_id, fid, fn, job.get("title", "?"),
            )

    remaining = _db_count_active()
    print(
        f"{_LOG} stage=request_cleanup request_id={db_id}"
        f" remaining_in_queue={remaining}"
    )
    if remaining == 0:
        with _lock:
            cur_mode = _mode
        if cur_mode != "vibe":
            await _switch_to_vibe(bot)
        else:
            print(f"{_LOG} Already in VIBE mode — skipping redundant playlist switch")


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
    global _cur_req_id, _last_ann_id, _last_ann_title

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
            await ann.announce_request_live(bot, req_title, "", req_uname)
            print(
                f"{_LOG} stage=request_announcement"
                f" request_id={db_id}"
                f" media_id={live_fid!r}"
                f" title={req_title!r}"
                f" username={req_uname!r}"
            )

        print(f"{_LOG} Now playing REQUEST: {req_title!r} by @{req_uname}")

        # ── Proactive file deletion ────────────────────────────────────────────
        if live_fid or live_fn:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(
                None, _delete_request_file, db_id, live_fid, live_fn, req_title
            )
        print(
            f"{_LOG} stage=request_cleanup request_id={db_id}"
            f" filename={live_fn!r} status=playing (proactive deletion queued)"
        )

        # ── Pre-switch playlists to VIBE if this is the last active request ───
        remaining = _db_count_active()
        if remaining <= 1:
            print(f"{_LOG} Last active request — pre-switching playlists to VIBE")
            await _switch_to_vibe(bot)

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

    Flow
    ────
    1. Fetch request metadata from DB; early-exit if already playing.
    2. Brief 2 s settle wait (lets AzuraCast rescan complete after upload).
    3. Submit AzuraCast request + issue first skip.
       → logs stage=request_force_skip attempt=1
    4. Poll Now Playing every 1 s for up to 15 s.
       → logs stage=request_nowplaying_poll each second
    5. At 5 s, if not yet confirmed: issue a second skip.
       → logs stage=request_force_skip attempt=2
    6. Match uses 6-strategy logic: media_id, song_unique_id, Requests/ path,
       filename, video_id, title fuzzy.
    7. On match  → logs stage=request_takeover_success; marks DB playing;
                   fires REQUEST LIVE announcement; queues file cleanup.
       On timeout → logs stage=request_takeover_timeout; fires queued-next whisper.
    """
    global _skip_task_active, _cur_req_id, _last_ann_id, _last_ann_title
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

        if _stop_flag.is_set():
            return

        # ── First skip ─────────────────────────────────────────────────────────
        if unique_id:
            await loop.run_in_executor(None, azura.submit_request, unique_id)
            await asyncio.sleep(0.3)

        skip_ok = await loop.run_in_executor(None, azura.skip_current, 1, 0)
        print(
            f"{_LOG} stage=request_force_skip"
            f" request_id={job_id} username={req_uname!r}"
            f" title={req_title!r} unique_id={unique_id!r}"
            f" attempt=1 http_status={'200' if skip_ok else 'failed'}"
        )

        # ── Poll Now Playing every 1 s for 15 s ────────────────────────────────
        confirmed        = False
        second_skip_done = False

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
            elif np_lpath.startswith("requests/"):
                if req_fn and np_fn and req_fn.lower() == np_fn:
                    match_method = "requests_path_filename"
                else:
                    match_method = "requests_path"
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

            # At 5 s: issue second skip if still not confirmed
            if check == 4 and not match_method and not second_skip_done:
                second_skip_done = True
                skip_ok2 = await loop.run_in_executor(None, azura.skip_current, 1, 0)
                print(
                    f"{_LOG} stage=request_force_skip"
                    f" request_id={job_id} username={req_uname!r}"
                    f" title={req_title!r} unique_id={unique_id!r}"
                    f" attempt=2 http_status={'200' if skip_ok2 else 'failed'}"
                )
                continue

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

                # Pre-set dedup vars so _on_new_track won't duplicate-announce
                with _lock:
                    _last_ann_id    = np_id or unique_id
                    _last_ann_title = np_title.lower()[:80]
                    _cur_req_id     = job_id

                _db_set_status(job_id, "playing", media_id=np_fid)
                display_artist = req_artist or np_artist
                await ann.announce_request_live(bot, req_title, display_artist, req_uname)

                # Proactive file cleanup
                job_fresh = _db_get_job(job_id)
                if job_fresh:
                    _fid = (job_fresh.get("azura_file_id") or np_fid).strip()
                    _fn  = (job_fresh.get("filename") or req_fn).strip()
                    if _fid or _fn:
                        loop.run_in_executor(
                            None, _delete_request_file,
                            job_id, _fid, _fn, req_title,
                        )
                    print(
                        f"{_LOG} stage=request_cleanup request_id={job_id}"
                        f" filename={_fn!r} status=playing (deletion queued by skip task)"
                    )
                if _db_count_active() <= 1:
                    print(f"{_LOG} Last active request — pre-switching to vibe from skip task")
                    await _switch_to_vibe(bot)

                confirmed = True
                break

        if not confirmed:
            print(
                f"{_LOG} stage=request_takeover_timeout"
                f" request_id={job_id} username={req_uname!r}"
                f" title={req_title!r} unique_id={unique_id!r}"
                f" elapsed_s=15"
            )
            await ann.announce_request_queued_next(bot)

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
    global _cur_song_id, _cur_req_id, _cur_elapsed, _cur_duration, _mode

    print(f"{_LOG} Poll loop started (every {POLL_INTERVAL}s)")
    loop = asyncio.get_running_loop()

    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL)

            # ── Detect newly uploaded requests (status='done' + azura_file_id) ─
            new_done = _db_find_new_done()
            for j in new_done:
                _db_set_status(j["id"], "queued")
                print(f"{_LOG} Request promoted to queued: {j.get('title','?')!r}")

            # ── Switch to REQUESTS mode if queue has items and we're in vibe mode ─
            with _lock:
                cur_mode        = _mode
                skip_task_busy  = _skip_task_active
            if cur_mode != "requests" and _db_count_active() > 0:
                print(f"{_LOG} Pending requests detected — switching to REQUESTS mode")
                await _switch_to_requests(bot)  # playlists only, no skip

            # ── Promote next staged job if the /Requests slot is free ────────
            # Handles both the steady-state case (slot just freed by cleanup)
            # and the startup-recovery case (staged jobs found on restart).
            # _do_promote_staged acquires a lock so concurrent calls are safe.
            if not new_done and _db_count_staged() > 0 and _db_count_active_in_requests() == 0:
                loop.run_in_executor(None, _do_promote_staged, bot, loop)

            # ── Fire verified-skip task for newly-queued requests ─────────────
            # Triggered only when new 'done' jobs were just promoted AND
            # auto-skip is on AND no skip task is already running.
            if new_done and cs.auto_skip_on_request() and not skip_task_busy:
                next_job = _db_find_oldest_queued()
                if next_job:
                    uid = (next_job.get("azura_song_id") or "").strip()
                    if uid:
                        print(
                            f"{_LOG} Firing verified-skip task for "
                            f"{next_job.get('title','?')!r} uid={uid!r}"
                        )
                        asyncio.create_task(
                            _verified_skip_task(bot, next_job["id"], uid)
                        )
                    else:
                        # No unique_id — fall back to simple unverified skip.
                        # run_in_executor returns a Future; do NOT wrap in create_task.
                        loop.run_in_executor(None, azura.skip_current)

            # ── Fetch nowplaying from AzuraCast ──────────────────────────────
            np = await loop.run_in_executor(None, azura.fetch_nowplaying)
            if not np:
                continue

            np_obj   = np.get("now_playing") or {}
            song     = np_obj.get("song")    or {}
            media    = np_obj.get("media")   or {}
            song_id  = (song.get("id")       or "").strip()
            elapsed  = int(np_obj.get("elapsed")  or 0)
            duration = int(np_obj.get("duration") or 0)

            if not song_id:
                continue

            with _lock:
                prev_song_id  = _cur_song_id
                prev_req_id   = _cur_req_id
                prev_elapsed  = _cur_elapsed
                prev_duration = _cur_duration
                _cur_elapsed  = elapsed
                _cur_duration = duration

            # ── Replay detection: same song_id but elapsed has reset ──────────
            # AzuraCast looped the Requests playlist — the same file started
            # playing again.  Treat this as the song having finished so cleanup
            # fires and AzuraCast is moved back to vibe mode.
            if (
                song_id     == prev_song_id
                and prev_req_id
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
                _cur_song_id = song_id
            print(f"{_LOG} Song change: {prev_song_id!r} → {song_id!r}")

            if prev_req_id:
                await _on_request_finished(bot, prev_req_id)

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


def get_current_request() -> "dict | None":
    """Return the DB record of the request currently playing, or None."""
    return _db_find_playing()


async def on_request_skipped(bot: "BaseBot", job_id: int) -> None:
    """
    Public — called by the !skip command handler after AzuraCast confirms
    the skip succeeded.

    Mirrors _on_request_finished but logs stage=request_skipped so audit
    logs can distinguish staff-initiated skips from natural end-of-song
    transitions.  Queues file cleanup (move to PlayedRequests/, rescan)
    and switches playlists back to VIBE mode if the queue is now empty.
    """
    global _cur_req_id
    with _lock:
        if _cur_req_id == job_id:
            _cur_req_id = 0

    job = _db_get_job(job_id)
    _db_set_status(job_id, "played")

    fn_s  = (job.get("filename")      if job else None) or "?"
    fid_s = (job.get("azura_file_id") if job else None) or "?"
    ttl_s = (job.get("title")         if job else None) or "?"
    usr_s = (job.get("username")      if job else None) or "?"
    print(
        f"{_LOG} stage=request_skipped"
        f" request_id={job_id}"
        f" media_id={fid_s!r}"
        f" filename={fn_s!r}"
        f" title={ttl_s!r}"
        f" username={usr_s!r}"
    )

    if job:
        fid = (job.get("azura_file_id") or "").strip()
        fn  = (job.get("filename")      or "").strip()
        if fid or fn:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(
                None, _delete_request_file,
                job_id, fid, fn, job.get("title", "?"),
            )

    remaining = _db_count_active()
    print(
        f"{_LOG} stage=request_cleanup request_id={job_id}"
        f" remaining_in_queue={remaining} source=skip"
    )
    if remaining == 0:
        with _lock:
            cur_mode = _mode
        if cur_mode != "vibe":
            await _switch_to_vibe(bot)
        else:
            print(f"{_LOG} Already in VIBE mode — skipping redundant playlist switch")


async def apply_vibe_change(bot: "BaseBot") -> None:
    """
    Called by the !vibe command after config_store.set_vibe() has been saved.
    Immediately re-applies playlists if in VIBE mode.
    If in REQUESTS mode, the new vibe takes effect once the queue empties.
    """
    with _lock:
        cur_mode = _mode
    if cur_mode == "vibe":
        await _apply_vibe_playlists()
    else:
        print(f"{_LOG} In REQUESTS mode — new vibe takes effect when queue empties")


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
    print(f"{_LOG} Playback engine scheduled ✓ (init deferred)")
    asyncio.create_task(_startup_init_task(bot))


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
                _mode       = "requests"
            print(f"{_LOG} Recovery: request was playing db_id={playing_now['id']!r}")
            await _apply_requests_playlists()

        elif in_flight > 0:
            with _lock:
                _mode = "requests"
            print(f"{_LOG} Recovery: {in_flight} request(s) in queue — REQUESTS mode")
            await _apply_requests_playlists()

        else:
            with _lock:
                _mode = "vibe"
            print(f"{_LOG} No queued requests — applying VIBE/{cs.vibe().upper()} playlists")
            await _apply_vibe_playlists()

        asyncio.create_task(_poll_loop(bot))
        print(f"{_LOG} Playback engine ready ✓")

    except asyncio.CancelledError:
        print(f"{_LOG} Startup init task cancelled (bot disconnecting before init completed)")
        raise

    except Exception as exc:
        import traceback
        print(f"{_LOG} Startup init error (non-fatal): {exc}")
        traceback.print_exc()
        # Start the poll loop anyway — it may self-correct once AzuraCast is reachable
        try:
            asyncio.create_task(_poll_loop(bot))
            print(f"{_LOG} Poll loop started despite init error")
        except Exception as _pe:
            print(f"{_LOG} Could not start poll loop: {_pe}")
