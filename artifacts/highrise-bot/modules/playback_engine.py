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
_started:         bool  = False
_mode:            str   = "vibe"    # "vibe" | "requests"
_cur_song_id:     str   = ""        # AzuraCast song.id currently playing
_cur_req_id:      int   = 0         # yt_request_jobs.id of the active request (0 = none)
_last_ann_id:     str   = ""        # song.id last announced (dedup)
_skip_task_active: bool = False     # True while a _verified_skip_task is running

_ACT = ("pending", "downloading", "uploading", "done", "queued", "playing")
_ACT_PH = ",".join("?" * len(_ACT))   # SQL placeholders for IN clause

_COLS = (
    "id", "user_id", "username", "title", "filename",
    "azura_file_id", "azura_song_id", "coins_charged", "status",
)
_SEL = (
    "id, user_id, username, title, filename, "
    "azura_file_id, azura_song_id, coins_charged, status"
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


def _db_set_status(db_id: int, status: str) -> None:
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


def _db_match_request(azura_song_id: str, np_title: str) -> "dict | None":
    """
    Try to match the currently-playing AzuraCast song to one of our queued requests.
    Strategy 1: exact azura_song_id match (most reliable once AzuraCast has indexed the file).
    Strategy 2: title substring match (fallback for freshly uploaded files).
    """
    active = ("done", "queued", "playing")
    ph     = ",".join("?" * len(active))

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
                    return _jrow(row)
        except Exception:
            pass

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


# ─── Track event handlers ─────────────────────────────────────────────────────

async def _on_request_finished(bot: "BaseBot", db_id: int) -> None:
    global _cur_req_id
    with _lock:
        _cur_req_id = 0

    job = _db_get_job(db_id)
    _db_set_status(db_id, "played")

    if job:
        print(f"{_LOG} Request finished: {job.get('title','?')!r}")
        fid = (job.get("azura_file_id") or "").strip()
        fn  = (job.get("filename")      or "").strip()
        loop = asyncio.get_running_loop()
        if fid:
            # Fire-and-forget: run_in_executor returns a Future tracked by the
            # thread pool — do NOT wrap in create_task (Future ≠ coroutine).
            loop.run_in_executor(None, azura.delete_media_file, fid)
        elif fn:
            loop.run_in_executor(None, azura.sftp_delete_file, fn)

    remaining = _db_count_active()
    print(f"{_LOG} Remaining in queue: {remaining}")
    if remaining == 0:
        await _switch_to_vibe(bot)


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


async def _on_new_track(bot: "BaseBot", song: dict) -> None:
    global _cur_req_id, _last_ann_id

    song_id = (song.get("id")     or "").strip()
    title   = (song.get("title")  or "").strip()
    artist  = (song.get("artist") or "").strip()

    with _lock:
        if song_id and song_id == _last_ann_id:
            return
        _last_ann_id = song_id

    match = _db_match_request(song_id, title)

    if match:
        req_title = match.get("title") or title
        req_uname = match.get("username") or ""
        with _lock:
            _cur_req_id = match["id"]
        if match.get("status") != "playing":
            _db_set_status(match["id"], "playing")
        await ann.announce_request_live(bot, req_title, "", req_uname)
        print(f"{_LOG} Now playing REQUEST: {req_title!r} by @{req_uname}")
    else:
        with _lock:
            _cur_req_id = 0
        await ann.announce_now_playing(bot, title, artist, None, cs.vibe())
        print(f"{_LOG} Now playing VIBE: {title!r}")


# ─── Verified-skip background task ───────────────────────────────────────────

async def _verified_skip_task(bot: "BaseBot", job_id: int, unique_id: str) -> None:
    """
    Background asyncio task — does NOT block the poll loop.

    Flow
    ────
    1. Fetch request metadata (title, artist, username) from DB.
    2. Early-exit if the poll loop already detected it as playing.
    3. Wait 4 s flat for AzuraCast to register the newly-uploaded file in its
       internal media library / request queue (no HTTP polling needed here).
    4. Re-submit the song to AzuraCast's request endpoint (idempotent) then
       issue a single skip so AzuraCast advances to the request.
    5. Poll the Now Playing API every 2 s for up to 14 s.
       • Match by song ID first (exact), then by title (conservative substring).
       • On match  → pre-set _last_ann_id (dedup the poll loop), mark DB status
                     "playing", fire polished REQUEST LIVE room announcement.
       • On timeout → fire "queued and ready" fallback so the room knows; the
                     poll loop will catch and announce when the song plays next.
    """
    global _skip_task_active, _cur_req_id, _last_ann_id
    _skip_task_active = True
    try:
        loop = asyncio.get_running_loop()

        # ── Fetch request metadata ─────────────────────────────────────────────
        job = _db_get_job(job_id)
        if not job:
            print(f"{_LOG} Verified-skip: job {job_id} not found — aborting")
            return

        req_title  = (job.get("title")    or "Unknown").strip()
        req_artist = (job.get("artist")   or "").strip()
        req_uname  = (job.get("username") or "").strip()

        # ── Early exit: poll loop already marked it playing ────────────────────
        if job.get("status") == "playing":
            print(f"{_LOG} Job {job_id} already playing — skip task done early")
            return

        print(f"{_LOG} Verified-skip started: job={job_id} uid={unique_id!r} title={req_title!r}")

        # ── Step 1: Flat 4 s wait for AzuraCast queue refresh ─────────────────
        # (8 × 0.5 s so stop_flag is checked every half-second)
        print(f"{_LOG} Waiting 4 s for AzuraCast queue refresh…")
        for _ in range(8):
            if _stop_flag.is_set():
                return
            await asyncio.sleep(0.5)

        # Re-check after the wait
        job = _db_get_job(job_id)
        if job and job.get("status") == "playing":
            print(f"{_LOG} Job {job_id} started playing during queue wait — skip task done")
            return

        if _stop_flag.is_set():
            return

        # ── Step 2: Submit request to AzuraCast + skip ────────────────────────
        if unique_id:
            print(f"{_LOG} Submitting request {unique_id!r} to AzuraCast…")
            await loop.run_in_executor(None, azura.submit_request, unique_id)
            await asyncio.sleep(1)   # brief settle before skip

        if _stop_flag.is_set():
            return

        print(f"{_LOG} Issuing skip…")
        await loop.run_in_executor(None, azura.skip_current, 1, 0)

        # ── Step 3: Poll Now Playing for up to 14 s (7 × 2 s) ────────────────
        print(f"{_LOG} Polling NP for up to 14 s to confirm {unique_id!r}…")
        confirmed = False

        for check in range(7):
            if _stop_flag.is_set():
                return

            await asyncio.sleep(2)

            np = await loop.run_in_executor(None, azura.fetch_nowplaying)
            if not np:
                continue

            np_song   = ((np.get("now_playing") or {}).get("song") or {})
            np_id     = (np_song.get("id")     or "").strip()
            np_title  = (np_song.get("title")  or "").strip()
            np_artist = (np_song.get("artist") or "").strip()

            id_match    = bool(unique_id and np_id and np_id == unique_id)
            title_match = _title_matches(req_title, np_title)

            if id_match or title_match:
                reason = "song ID" if id_match else "title"
                print(f"{_LOG} NP confirmed by {reason} (check {check + 1}): {np_title!r}")

                # Pre-set _last_ann_id so _on_new_track won't duplicate-announce
                with _lock:
                    _last_ann_id = np_id or unique_id
                    _cur_req_id  = job_id

                _db_set_status(job_id, "playing")
                display_artist = req_artist or np_artist
                await ann.announce_request_live(bot, req_title, display_artist, req_uname)
                confirmed = True
                break

        if not confirmed:
            print(f"{_LOG} Could not confirm {unique_id!r} in 14 s — announcing queued next")
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
    global _cur_song_id, _cur_req_id, _mode

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

            np_obj  = np.get("now_playing") or {}
            song    = np_obj.get("song")    or {}
            song_id = (song.get("id")       or "").strip()

            if not song_id:
                continue

            with _lock:
                prev_song_id = _cur_song_id
                prev_req_id  = _cur_req_id

            if song_id == prev_song_id:
                continue

            # ── Song changed ──────────────────────────────────────────────────
            with _lock:
                _cur_song_id = song_id
            print(f"{_LOG} Song change: {prev_song_id!r} → {song_id!r}")

            if prev_req_id:
                await _on_request_finished(bot, prev_req_id)

            await _on_new_track(bot, song)

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
