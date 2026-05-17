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
_lock               = threading.Lock()
_started:     bool  = False
_mode:        str   = "vibe"    # "vibe" | "requests"
_cur_song_id: str   = ""        # AzuraCast song.id currently playing
_cur_req_id:  int   = 0         # yt_request_jobs.id of the active request (0 = none)
_last_ann_id: str   = ""        # song.id last announced (dedup)

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
    global _mode
    with _lock:
        _mode = "requests"
    await _apply_requests_playlists()
    if cs.auto_skip_on_request():
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, azura.skip_current)
    print(f"{_LOG} Switched → REQUESTS")


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
            asyncio.create_task(loop.run_in_executor(None, azura.delete_media_file, fid))
        elif fn:
            asyncio.create_task(loop.run_in_executor(None, azura.sftp_delete_file, fn))

    remaining = _db_count_active()
    print(f"{_LOG} Remaining in queue: {remaining}")
    if remaining == 0:
        await _switch_to_vibe(bot)


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
        await ann.announce_now_playing(bot, req_title, "", req_uname)
        print(f"{_LOG} Now playing REQUEST: {req_title!r} by @{req_uname}")
    else:
        with _lock:
            _cur_req_id = 0
        await ann.announce_now_playing(bot, title, artist, None, cs.vibe())
        print(f"{_LOG} Now playing VIBE: {title!r}")


# ─── Main poll loop ───────────────────────────────────────────────────────────

async def _poll_loop(bot: "BaseBot") -> None:
    global _cur_song_id, _cur_req_id, _mode

    print(f"{_LOG} Poll loop started (every {POLL_INTERVAL}s)")
    loop = asyncio.get_running_loop()

    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL)

            # ── Detect newly uploaded requests (status='done' + azura_file_id) ─
            for j in _db_find_new_done():
                _db_set_status(j["id"], "queued")
                print(f"{_LOG} Request promoted to queued: {j.get('title','?')!r}")

            # ── Switch to REQUESTS mode if queue has items and we're in vibe mode ─
            with _lock:
                cur_mode = _mode
            if cur_mode != "requests" and _db_count_active() > 0:
                print(f"{_LOG} Pending requests detected — switching to REQUESTS mode")
                await _switch_to_requests(bot)

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
            print(f"{_LOG} Poll loop cancelled")
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
    Entry point for the DJ bot's on_start.
    Restores playlist state from DB, recovers any in-flight requests,
    then starts the polling loop.
    """
    global _started, _mode, _cur_req_id

    if _started:
        print(f"{_LOG} Already started — skipping duplicate call")
        return
    _started = True

    print(f"{_LOG} Starting playback engine…")

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
        print(f"{_LOG} Recovery: {in_flight} request(s) in queue — enabling REQUESTS playlists")
        await _apply_requests_playlists()

    else:
        with _lock:
            _mode = "vibe"
        print(f"{_LOG} No queued requests — applying VIBE/{cs.vibe().upper()} playlists")
        await _apply_vibe_playlists()

    asyncio.create_task(_poll_loop(bot))
    print(f"{_LOG} Playback engine ready ✓")
