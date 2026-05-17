"""
modules/dj_music.py
-------------------
Production-ready song-request queue for DJ_DUDU.

Bot mode : "dj"  (BOT_MODE=dj)   Display name: DJ_DUDU
All commands are owned exclusively by the dj bot.

Public commands:
  !request <song>      Search YouTube, whisper top 5 results
    aliases: !req !sr !song !requesy
  !pick <1-5>          Confirm a search result → add to queue (dj bot only)
  !queue / !djqueue    Show next 5 pending songs
  !np / !nowplaying    Show currently featured song + link
  !skipvote            Vote to skip current song
  !radio               Show configured radio stream URL
  !djhelp              Show all DJ commands

Manager commands:
  !skip / !djskip      Force-advance the queue
  !stopmusic           Clear all pending / playing entries
  !djconfig            Show current DJ settings

Admin commands:
  !djlock on|off       Block/allow new song requests from players
  !djclear             Wipe the entire queue (pending + playing)
  !djremove <#>        Remove a specific pending queue entry by position
  !djset <key> <val>   Change a DJ setting:
                         queuemax <1-50>       max queue size          (default 20)
                         cooldown <5-3600>     request cooldown in sec (default 30)
                         usermax <1-10>        max pending per user    (default 2)
                         votethreshold <2-10>  votes needed to auto-skip (default 3)
  !djdebug on|off      Toggle search debug whispers

Architecture:
  • PlaybackBackend protocol  — plug in IcecastBackend later with zero handler changes
  • NullBackend               — active now (no-op, queue-only mode)
  • Explicit status='playing' — front-of-queue song is promoted; backend.play() called there
  • status state machine:     pending → playing → played | skipped
  • Search TTL (3 min)        — stale !pick calls are rejected cleanly
  • Duplicate guard           — exact URL match + normalised-title match block re-requests
  • Per-user limit            — max 2 pending/playing songs per player (configurable)
  • Request cooldown          — 30 sec between requests per player (configurable)
  • Queue lock                — !djlock on prevents new requests; admins bypass
  • Configurable via db.get_room_setting / db.set_room_setting
  • All DB ops use get_connection() — no module-level connection held open

Search fix: yt-dlp ytsearch prefix MUST be embedded in the query string itself
  (e.g. "ytsearch5:despacito").  The `default_search` option does NOT trigger
  YouTube search — it returns a bare URL result instead.

DB table : dj_requests  (migration 3.2M + 3.2N in database.py)
Settings : room_settings keys  dj_queue_max | dj_cooldown_secs | dj_user_max
                                dj_skipvote_threshold | dj_debug_mode | dj_lock
"""
from __future__ import annotations

import asyncio
import random as _random
import re
import string
import time
import traceback
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import database as db
from modules.permissions import can_manage_games, is_admin

if TYPE_CHECKING:
    from highrise import BaseBot, User

# Guard: all handlers are safe to import from any bot process, but !pick
# silently exits unless this process is running as the dj bot.
try:
    from config import BOT_MODE as _DJ_BOT_MODE
except Exception:
    _DJ_BOT_MODE = ""

_IS_DJ_BOT: bool = (_DJ_BOT_MODE == "dj")


# ---------------------------------------------------------------------------
# Playback abstraction layer
# ---------------------------------------------------------------------------

@runtime_checkable
class PlaybackBackend(Protocol):
    """
    Minimal interface every streaming backend must satisfy.
    Swap NullBackend for IcecastBackend (or any other) at module load time
    without touching a single command handler.
    """
    @property
    def is_active(self) -> bool: ...
    @property
    def is_paused(self) -> bool: ...

    async def play(self, youtube_url: str, title: str) -> bool:
        """Start playback. Returns True on success."""
        ...

    async def stop(self) -> None:
        """Stop current playback immediately."""
        ...

    async def pause(self) -> None:
        """Pause playback (stream stays connected)."""
        ...

    async def resume(self) -> None:
        """Resume a paused stream."""
        ...

    async def set_volume(self, pct: int) -> None:
        """0-100 volume level."""
        ...


class NullBackend:
    """
    No-op backend — queue-only mode until a real stream server is configured.
    All methods succeed silently so handler logic is unchanged when a real
    backend is swapped in.
    """
    _active: bool = False
    _paused: bool = False

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_paused(self) -> bool:
        return self._paused

    async def play(self, youtube_url: str, title: str) -> bool:
        self._active = True
        self._paused = False
        return True

    async def stop(self) -> None:
        self._active = False
        self._paused = False

    async def pause(self) -> None:
        if self._active:
            self._paused = True

    async def resume(self) -> None:
        if self._active:
            self._paused = False

    async def set_volume(self, pct: int) -> None:
        pass


# Active backend instance — replace with IcecastBackend() when ready
_backend: Any = NullBackend()


def set_playback_backend(backend: PlaybackBackend) -> None:
    """
    Called at startup (or dynamically) to swap in a real streaming backend.
    Example (future):
        from modules.dj_icecast import IcecastBackend
        dj_music.set_playback_backend(IcecastBackend(host, port, mount, pwd))
    """
    global _backend
    _backend = backend


# ---------------------------------------------------------------------------
# Config helpers  (all settings stored in room_settings table)
# ---------------------------------------------------------------------------

_CFG_QUEUE_MAX   = "dj_queue_max"
_CFG_COOLDOWN    = "dj_cooldown_secs"   # unit: seconds (was dj_cooldown_mins)
_CFG_USER_MAX    = "dj_user_max"
_CFG_VOTE_THRESH = "dj_skipvote_threshold"
_CFG_DEBUG       = "dj_debug_mode"
_CFG_LOCK        = "dj_lock"
_CFG_REPEAT      = "dj_repeat"
_CFG_AUTOPLAY    = "dj_autoplay"
_CFG_PERSONALITY = "dj_personality"

_CFG_DEFAULTS: dict[str, str] = {
    _CFG_QUEUE_MAX:   "20",
    _CFG_COOLDOWN:    "30",   # 30 seconds between requests per user
    _CFG_USER_MAX:    "2",    # max 2 pending songs per user
    _CFG_VOTE_THRESH: "3",
    _CFG_DEBUG:       "off",
    _CFG_LOCK:        "off",
    _CFG_REPEAT:      "off",
    _CFG_AUTOPLAY:    "off",
    _CFG_PERSONALITY: "off",
}

# Friendly names and valid ranges for !djset
_CFG_META: dict[str, tuple[str, int, int]] = {
    # key → (setting_key, min_val, max_val)
    "queuemax":      (_CFG_QUEUE_MAX,   1,   50),
    "cooldown":      (_CFG_COOLDOWN,    5, 3600),   # 5 sec – 60 min
    "usermax":       (_CFG_USER_MAX,    1,   10),
    "votethreshold": (_CFG_VOTE_THRESH, 2,   10),
}


def _cfg(key: str) -> int:
    return int(db.get_room_setting(key, _CFG_DEFAULTS[key]))


def _queue_max()    -> int:  return _cfg(_CFG_QUEUE_MAX)
def _cooldown()     -> int:  return _cfg(_CFG_COOLDOWN)
def _user_max()     -> int:  return _cfg(_CFG_USER_MAX)
def _vote_thresh()  -> int:  return _cfg(_CFG_VOTE_THRESH)
def _debug_mode()   -> bool:
    return db.get_room_setting(_CFG_DEBUG, "off").lower() == "on"
def _dj_locked()      -> bool:
    return db.get_room_setting(_CFG_LOCK,        "off").lower() == "on"
def _dj_repeat()      -> bool:
    return db.get_room_setting(_CFG_REPEAT,      "off").lower() == "on"
def _dj_autoplay()    -> bool:
    return db.get_room_setting(_CFG_AUTOPLAY,    "off").lower() == "on"
def _dj_personality() -> bool:
    return db.get_room_setting(_CFG_PERSONALITY, "off").lower() == "on"


# ---------------------------------------------------------------------------
# Volatile in-memory state  (intentionally resets on restart — see notes)
# ---------------------------------------------------------------------------

# { user_id: (unix_timestamp, [result_dict, ...]) }
# TTL: 3 minutes — after that, !pick is rejected and user must !request again
_pending_searches: dict[str, tuple[float, list[dict]]] = {}

_SEARCH_TTL: int = 180   # seconds

# user_ids who have voted to skip the current playing row
# keyed to the current playing row id so votes reset automatically on advance
_skip_votes: dict[int, set[str]] = {}   # { row_id: {user_id, ...} }

# Playback timer state — intentionally volatile (resets on restart)
_bot_start_time:  float = time.time()   # epoch when this bot process started
_playing_since:   float = 0.0           # epoch when current song began playing
_playback_task:   "asyncio.Task[None] | None" = None  # auto-advance timer
_repeat_song:     dict | None = None    # copy of currently playing row for !repeat

# DJ personality hype lines (used when dj_personality=on, ~40% chance per song)
_HYPE_LINES: list[str] = [
    "🔥 That's what I'm talking about! Let's gooo!",
    "🎶 DJ DUDU keeping the vibes alive all night!",
    "💃 Hands up! DJ DUDU is in the house!",
    "🎧 Certified banger — DJ DUDU approved!",
    "✨ The queue never stops. Neither does DJ DUDU!",
    "🔊 Turn it up! DJ DUDU bringing the heat!",
    "🎤 Feelin' this one? Use !request <song> to add yours!",
    "🎵 Good taste! DJ DUDU salutes you!",
    "🕺 Keep those requests coming — DJ DUDU is ready!",
]


# ---------------------------------------------------------------------------
# Normalisation helpers (duplicate detection)
# ---------------------------------------------------------------------------

_PUNCT = re.compile(r"[^\w\s]")


def _normalise(text: str) -> str:
    """Lower-case, strip punctuation/extra spaces — used for title dedup."""
    return _PUNCT.sub("", text.lower()).split()  # type: ignore[return-value]
    # returns list of tokens; compared as set intersection below


def _titles_are_dupes(a: str, b: str) -> bool:
    """True if two titles share ≥ 80 % of their word tokens (order-independent)."""
    ta = set(_PUNCT.sub("", a.lower()).split())
    tb = set(_PUNCT.sub("", b.lower()).split())
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(len(ta), len(tb))
    return overlap >= 0.80


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _pending_count() -> int:
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT COUNT(*) AS n FROM dj_requests WHERE status='pending'"
        ).fetchone()
        conn.close()
        return row["n"] if row else 0
    except Exception:
        return 0


def _user_pending_count(user_id: str) -> int:
    """Number of songs this user currently has pending or playing in the queue."""
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT COUNT(*) AS n FROM dj_requests "
            "WHERE user_id=? AND status IN ('pending','playing')",
            (user_id,),
        ).fetchone()
        conn.close()
        return int(row["n"]) if row else 0
    except Exception:
        return 0


def _user_cooldown_secs(user_id: str) -> int:
    """
    Minutes since user's most recent request that is still pending/playing.
    Returns 9999 if the user has never requested (cooldown is cleared).
    """
    try:
        conn = db.get_connection()
        row  = conn.execute(
            """SELECT (CAST(strftime('%s','now') AS INTEGER)
                       - CAST(strftime('%s', requested_at) AS INTEGER)) AS secs_ago
               FROM dj_requests
               WHERE user_id = ?
               ORDER BY requested_at DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        conn.close()
        if row and row["secs_ago"] is not None:
            return int(row["secs_ago"])
    except Exception:
        pass
    return 9999


def _remove_request_by_pos(pos: int) -> dict | None:
    """
    Remove the nth pending request (1-based, ordered oldest-first).
    Returns the removed row dict {id, title, username} or None if out of range.
    """
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT id, title, username FROM dj_requests "
            "WHERE status='pending' ORDER BY requested_at ASC",
        ).fetchall()
        if pos < 1 or pos > len(rows):
            conn.close()
            return None
        target = rows[pos - 1]
        conn.execute("DELETE FROM dj_requests WHERE id=?", (target["id"],))
        conn.commit()
        conn.close()
        return dict(target)
    except Exception:
        return None


def _is_duplicate(youtube_url: str, title: str) -> dict | None:
    """
    Returns an existing active queue row if this song is already queued.
    Checks exact URL match first, then normalised-title similarity.
    """
    try:
        conn = db.get_connection()
        rows = conn.execute(
            """SELECT id, title, youtube_url, status,
                      (SELECT COUNT(*) FROM dj_requests WHERE status IN ('pending','playing')
                       AND requested_at <= r.requested_at) AS pos
               FROM dj_requests r
               WHERE status IN ('pending','playing')
               ORDER BY requested_at ASC""",
        ).fetchall()
        conn.close()
        for row in rows:
            if youtube_url and row["youtube_url"] == youtube_url:
                return dict(row)
            if _titles_are_dupes(title, row["title"]):
                return dict(row)
    except Exception:
        pass
    return None


def _add_request(
    user_id: str, username: str, title: str,
    youtube_url: str = "", duration: str = "",
) -> int:
    """
    Insert a pending request.
    If the queue was empty, auto-promotes the new row to 'playing'
    and calls backend.play() via asyncio.  Returns queue position (1-based).
    """
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO dj_requests
                   (user_id, username, title, youtube_url, duration, status)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (user_id, username.lower(), title, youtube_url, duration),
        )
        conn.commit()
        pos = conn.execute(
            "SELECT COUNT(*) AS n FROM dj_requests WHERE status IN ('pending','playing')"
        ).fetchone()["n"]
        conn.close()
        return pos
    except Exception:
        return -1


def _get_nowplaying() -> dict | None:
    """
    Return the front-of-queue row.
    Priority: status='playing' first, then oldest 'pending'.
    """
    try:
        conn = db.get_connection()
        row  = conn.execute(
            """SELECT id, username, title, youtube_url, duration, status
               FROM dj_requests
               WHERE status IN ('playing','pending')
               ORDER BY CASE status WHEN 'playing' THEN 0 ELSE 1 END,
                        requested_at ASC
               LIMIT 1"""
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _get_queue(limit: int = 5) -> list[dict]:
    """Return upcoming 'pending' rows (does not include the 'playing' row)."""
    try:
        conn = db.get_connection()
        rows = conn.execute(
            """SELECT id, username, title, youtube_url, duration
               FROM dj_requests
               WHERE status = 'pending'
               ORDER BY requested_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _promote_front() -> dict | None:
    """
    If no row is currently 'playing', promote the oldest 'pending' row.
    Returns the newly promoted row, or None if queue is empty.
    Does NOT call backend.play() — callers do that asynchronously.
    """
    try:
        conn = db.get_connection()
        playing = conn.execute(
            "SELECT id FROM dj_requests WHERE status='playing' LIMIT 1"
        ).fetchone()
        if playing:
            conn.close()
            return None   # already have a playing row

        nxt = conn.execute(
            """SELECT id, username, title, youtube_url, duration
               FROM dj_requests WHERE status='pending'
               ORDER BY requested_at ASC LIMIT 1"""
        ).fetchone()
        if not nxt:
            conn.close()
            return None

        conn.execute(
            "UPDATE dj_requests SET status='playing' WHERE id=?",
            (nxt["id"],),
        )
        conn.commit()
        conn.close()
        return dict(nxt)
    except Exception:
        return None


def _advance_queue() -> dict | None:
    """
    Mark the current playing/leading-pending row as 'played'.
    Promote the next pending row to 'playing'.
    Clears skip votes for the outgoing row.
    Returns the NEW playing row, or None if queue is now empty.
    """
    global _skip_votes
    try:
        conn = db.get_connection()
        current = conn.execute(
            """SELECT id FROM dj_requests
               WHERE status IN ('playing','pending')
               ORDER BY CASE status WHEN 'playing' THEN 0 ELSE 1 END,
                        requested_at ASC LIMIT 1"""
        ).fetchone()
        if not current:
            conn.close()
            return None

        conn.execute(
            "UPDATE dj_requests SET status='played', played_at=datetime('now') WHERE id=?",
            (current["id"],),
        )
        conn.commit()
        # Clear skip votes for the row we just finished
        _skip_votes.pop(current["id"], None)

        nxt = conn.execute(
            """SELECT id, username, title, youtube_url, duration
               FROM dj_requests WHERE status='pending'
               ORDER BY requested_at ASC LIMIT 1"""
        ).fetchone()
        if nxt:
            conn.execute(
                "UPDATE dj_requests SET status='playing' WHERE id=?",
                (nxt["id"],),
            )
            conn.commit()
        conn.close()
        return dict(nxt) if nxt else None
    except Exception:
        return None


def _clear_queue() -> int:
    """Mark all pending + playing rows as skipped. Returns count cleared."""
    global _skip_votes
    try:
        conn = db.get_connection()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM dj_requests "
            "WHERE status IN ('pending','playing')"
        ).fetchone()["n"]
        conn.execute(
            "UPDATE dj_requests SET status='skipped', played_at=datetime('now') "
            "WHERE status IN ('pending','playing')"
        )
        conn.commit()
        conn.close()
        _skip_votes.clear()
        return n
    except Exception:
        return 0


def _total_active() -> int:
    """Count of pending + playing rows."""
    try:
        conn = db.get_connection()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM dj_requests WHERE status IN ('pending','playing')"
        ).fetchone()["n"]
        conn.close()
        return n
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

class _SearchError(Exception):
    """Raised by _yt_search_sync on failure; carries captured yt-dlp log."""
    def __init__(self, message: str, log_lines: list[str]) -> None:
        super().__init__(message)
        self.log_lines = log_lines


class _YDLLogger:
    """
    Custom yt-dlp logger that captures all output to a list instead of
    printing to stderr.  Attach via opts["logger"] = _YDLLogger().
    """
    def __init__(self) -> None:
        self.lines: list[str] = []

    def debug(self, msg: str) -> None:
        # yt-dlp sends both debug and info through debug()
        if msg.startswith("[debug] "):
            return          # skip verbose internal debug lines
        self.lines.append(msg)

    def info(self, msg: str) -> None:
        self.lines.append(msg)

    def warning(self, msg: str) -> None:
        self.lines.append(f"[WARN] {msg}")

    def error(self, msg: str) -> None:
        self.lines.append(f"[ERROR] {msg}")


def _yt_search_sync(query: str, max_results: int = 5) -> list[dict]:
    """
    Blocking YouTube search via yt-dlp.
    MUST be called via run_in_executor — never awaited directly.

    Key fix: the ytsearch prefix is embedded in the query string itself
    ("ytsearch5:query").  The `default_search` YDL option is NOT used because
    it does not trigger the YouTube search extractor — it returns a bare
    URL-like result with zero entries instead.

    Raises _SearchError on any failure so callers can whisper debug details.
    """
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        raise _SearchError("yt-dlp is not installed.", [])

    logger = _YDLLogger()
    opts: dict[str, Any] = {
        "quiet":        True,
        "no_warnings":  True,
        "extract_flat": True,
        "skip_download": True,
        "logger":       logger,
        # Do NOT set default_search — embed ytsearch prefix in query instead
    }

    prefixed_query = f"ytsearch{max_results}:{query}"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(prefixed_query, download=False)
    except Exception as exc:
        raise _SearchError(
            f"yt-dlp extract_info raised {type(exc).__name__}: {exc}",
            logger.lines,
        ) from exc

    if not info:
        raise _SearchError(
            "yt-dlp returned None for query.",
            logger.lines,
        )

    # Validate we got a search playlist back, not a bare URL result
    info_type = info.get("_type", "")
    entries   = info.get("entries") or []
    if not entries:
        detail = (
            f"_type={info_type!r} "
            f"keys={list(info.keys())[:8]}"
        )
        raise _SearchError(
            f"yt-dlp returned 0 entries. {detail}",
            logger.lines,
        )

    results: list[dict] = []
    for e in entries[:max_results]:
        if not e:
            continue
        vid_id = e.get("id", "")
        if not vid_id:
            continue
        secs = e.get("duration") or 0
        m, s = divmod(int(secs), 60)
        results.append({
            "title":    (e.get("title") or "Unknown")[:80],
            "url":      f"https://youtu.be/{vid_id}",
            "duration": f"{m}:{s:02d}" if secs else "?:??",
            "channel":  (e.get("uploader") or e.get("channel") or "")[:30],
        })

    if not results:
        raise _SearchError(
            "All entries were empty or missing video IDs.",
            logger.lines,
        )

    return results


async def _yt_search(query: str, max_results: int = 5) -> tuple[list[dict], str]:
    """
    Async wrapper around _yt_search_sync.
    Returns (results, error_detail).
    results is [] and error_detail is non-empty on failure.
    """
    loop = asyncio.get_event_loop()
    try:
        results = await loop.run_in_executor(
            None, _yt_search_sync, query, max_results
        )
        return results, ""
    except _SearchError as exc:
        log_snippet = "\n".join(exc.log_lines[-8:]) if exc.log_lines else "(no log)"
        detail = f"{exc}\nyt-dlp log:\n{log_snippet}"
        return [], detail
    except Exception as exc:
        detail = f"Unexpected error: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-400:]}"
        return [], detail


def _store_search(user_id: str, results: list[dict]) -> None:
    _pending_searches[user_id] = (time.monotonic(), results)


def _pop_search(user_id: str) -> list[dict] | None:
    """Return and remove pending search if still within TTL, else None."""
    entry = _pending_searches.get(user_id)
    if not entry:
        return None
    ts, results = entry
    if time.monotonic() - ts > _SEARCH_TTL:
        _pending_searches.pop(user_id, None)
        return None
    del _pending_searches[user_id]
    return results


def _peek_search(user_id: str) -> list[dict] | None:
    """Return pending search without removing (used for !pick re-display)."""
    entry = _pending_searches.get(user_id)
    if not entry:
        return None
    ts, results = entry
    if time.monotonic() - ts > _SEARCH_TTL:
        _pending_searches.pop(user_id, None)
        return None
    return results


# ---------------------------------------------------------------------------
# Whisper / chat helpers
# ---------------------------------------------------------------------------

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


async def _chat(bot: "BaseBot", msg: str) -> None:
    try:
        await bot.highrise.chat(msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Duration / formatting helpers
# ---------------------------------------------------------------------------

def _parse_duration(s: str) -> int:
    """Parse "M:SS" or "H:MM:SS" → total seconds. Returns 0 on failure."""
    if not s or not isinstance(s, str):
        return 0
    try:
        parts = [int(p) for p in s.strip().split(":")]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
    except (ValueError, AttributeError):
        pass
    return 0


def _fmt_secs(secs: int) -> str:
    """Format seconds as M:SS."""
    secs = max(0, int(secs))
    return f"{secs // 60}:{secs % 60:02d}"


# ---------------------------------------------------------------------------
# Playback DB helpers (repeat / favorites / stats)
# ---------------------------------------------------------------------------

def _requeue_front(song: dict) -> None:
    """Re-insert `song` at the front of the pending queue for !repeat mode."""
    try:
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO dj_requests "
            "(user_id, username, title, youtube_url, duration, status, requested_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', datetime('now', '-7200 seconds'))",
            (song.get("user_id", ""), song.get("username", ""),
             song.get("title", ""), song.get("youtube_url", ""),
             song.get("duration", "")),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _get_user_favorites(user_id: str, limit: int = 5) -> list[dict]:
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT title, youtube_url FROM dj_favorites "
            "WHERE user_id=? ORDER BY favorited_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _add_favorite(
    user_id: str, username: str, title: str, youtube_url: str,
) -> bool:
    """Insert into dj_favorites if not already there. Returns True if added."""
    try:
        conn = db.get_connection()
        existing = conn.execute(
            "SELECT id FROM dj_favorites WHERE user_id=? AND lower(title)=lower(?)",
            (user_id, title),
        ).fetchone()
        if existing:
            conn.close()
            return False
        conn.execute(
            "INSERT INTO dj_favorites (user_id, username, title, youtube_url) "
            "VALUES (?, ?, ?, ?)",
            (user_id, username.lower(), title, youtube_url),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def _get_djstats() -> dict:
    """Return aggregate stats: total requests, most active requester, uptime."""
    try:
        conn = db.get_connection()
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM dj_requests"
        ).fetchone()["n"]
        top_row = conn.execute(
            "SELECT username, COUNT(*) AS cnt FROM dj_requests "
            "WHERE user_id != 'autoplay' "
            "GROUP BY user_id ORDER BY cnt DESC LIMIT 1"
        ).fetchone()
        conn.close()
        top_name = top_row["username"] if top_row else "—"
        top_cnt  = top_row["cnt"]      if top_row else 0
        uptime_secs = int(time.time() - _bot_start_time)
        h, rem = divmod(uptime_secs, 3600)
        uptime_str = f"{h}h {rem // 60}m" if h else f"{rem // 60}m"
        return {
            "total":    total,
            "top_name": top_name,
            "top_cnt":  top_cnt,
            "uptime":   uptime_str,
            "queue":    _total_active(),
        }
    except Exception:
        return {"total": 0, "top_name": "—", "top_cnt": 0,
                "uptime": "?", "queue": 0}


# ---------------------------------------------------------------------------
# Central playback advance  (timer · repeat · autoplay · announce · personality)
# ---------------------------------------------------------------------------

async def _do_advance(bot: "BaseBot") -> None:
    """
    Central queue advance: cancel timer → handle repeat → advance DB →
    announce + start new timer.  Called by !skip, !skipvote, playback timer.
    """
    global _playback_task, _playing_since, _repeat_song

    if _playback_task and not _playback_task.done():
        _playback_task.cancel()
    _playback_task = None

    # Repeat: re-insert the current song at the front before advancing
    if _dj_repeat() and _repeat_song:
        _requeue_front(_repeat_song)

    nxt = _advance_queue()

    # Autoplay: when queue is empty, search for a related track
    if nxt is None and _dj_autoplay():
        nxt = await _autoplay_search()   # adds to DB; returns dict or None
        if nxt:
            promoted = _promote_front()
            if promoted:
                nxt = promoted
            await _chat(bot, f"🤖 Autoplay: {nxt['title'][:60]}")

    if nxt:
        await _play_song(bot, nxt)
    else:
        _playing_since = 0.0
        _repeat_song   = None
        await _backend.stop()
        await _chat(bot, "🎵 Queue finished. Use !request <song> to add more!")


async def _playback_timer(bot: "BaseBot", duration_secs: int) -> None:
    """Sleep for the song's duration, then auto-advance to the next song."""
    try:
        await asyncio.sleep(duration_secs)
        await _do_advance(bot)
    except asyncio.CancelledError:
        pass   # cancelled by skip / stop / clear


async def _play_song(bot: "BaseBot", song: dict) -> None:
    """
    Set playing state, start auto-advance timer, post room announcement,
    optionally fire a personality hype line, then call backend.play().
    Single point where a song 'starts'.
    """
    global _playing_since, _repeat_song, _playback_task

    _playing_since = time.time()
    _repeat_song   = dict(song)

    dur_secs = _parse_duration(song.get("duration", ""))
    if dur_secs > 0:
        _playback_task = asyncio.get_event_loop().create_task(
            _playback_timer(bot, dur_secs)
        )

    dur = f" [{song['duration']}]" if song.get("duration") else ""
    url = f"\n{song['youtube_url']}" if song.get("youtube_url") else ""
    await _chat(
        bot,
        f"🎵 Now Playing: {song['title'][:60]}{dur}"
        f"\nRequested by @{song.get('username', '?')[:15]}{url}"
    )

    if _dj_personality() and _random.random() < 0.40:
        await asyncio.sleep(1.5)
        await _chat(bot, _random.choice(_HYPE_LINES))

    await _backend.play(song.get("youtube_url", ""), song["title"])


async def _autoplay_search() -> dict | None:
    """Search for a related song based on play history. Returns song dict or None."""
    history = _get_history(3)
    seed    = history[0]["title"] if history else "top pop hits"
    results, _ = await _yt_search(seed)
    if not results:
        return None
    recent_low = {r["title"].lower() for r in history}
    pick = next(
        (r for r in results if r["title"].lower() not in recent_low),
        results[0],
    )
    pos = _add_request(
        "autoplay", "autoplay", pick["title"], pick["url"], pick["duration"]
    )
    return pick if pos > 0 else None


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_dj_request(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!request <song>  —  search YouTube, whisper top 5 results."""
    if len(args) < 2:
        await _w(bot, user.id,
                 "🎵 Usage: !request <song title>\n"
                 "Then use !pick <1-5> to confirm.")
        return

    query = " ".join(args[1:]).strip()[:120]
    if not query:
        await _w(bot, user.id, "🎵 Please include a song title.")
        return

    # Queue lock — admins bypass
    if _dj_locked() and not is_admin(user.username):
        await _w(bot, user.id,
                 "🔒 Song requests are paused. Check back soon!")
        return

    # Per-user pending limit — admins bypass
    umax = _user_max()
    if not is_admin(user.username) and _user_pending_count(user.id) >= umax:
        await _w(bot, user.id,
                 f"🎵 You already have {umax} song(s) in the queue.\n"
                 f"Wait for them to play before requesting more.")
        return

    # Per-user cooldown — admins bypass
    secs_ago = _user_cooldown_secs(user.id)
    cooldown = _cooldown()
    if not is_admin(user.username) and secs_ago < cooldown:
        wait = cooldown - secs_ago
        await _w(bot, user.id, f"⏳ You can request again in {wait}s.")
        return

    # Global queue cap check
    if _total_active() >= _queue_max():
        await _w(bot, user.id,
                 f"🚫 Queue is full ({_queue_max()} songs). Try again soon!")
        return

    await _w(bot, user.id, f"🔍 Searching: {query[:60]}…")

    results, error_detail = await _yt_search(query)
    if not results:
        await _w(bot, user.id,
                 "⚠️ No results found. Try a different search term.")
        # Whisper full debug info to admins when debug mode is on
        if error_detail and (is_admin(user.username) or _debug_mode()):
            for chunk_start in range(0, min(len(error_detail), 700), 245):
                await _w(bot, user.id,
                         f"[DJ DEBUG] {error_detail[chunk_start:chunk_start+245]}")
        return

    _store_search(user.id, results)

    lines = ["🎵 Pick with !pick <number>:"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title'][:45]} [{r['duration']}]")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_dj_pick(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!pick <1-5>  —  confirm a search result and add it to the queue.
    Only handled when BOT_MODE=dj (DJ_DUDU). Other bots silently ignore it.
    """
    if not _IS_DJ_BOT:
        return

    results = _peek_search(user.id)
    if results is None:
        await _w(bot, user.id,
                 "🎵 No active search (results expire after 3 min).\n"
                 "Use !request <song> to search again.")
        return

    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "🎵 Usage: !pick <1-5>")
        return

    choice = int(args[1])
    if choice < 1 or choice > len(results):
        await _w(bot, user.id,
                 f"🎵 Pick a number between 1 and {len(results)}.")
        return

    pick = results[choice - 1]

    # Duplicate guard
    dupe = _is_duplicate(pick["url"], pick["title"])
    if dupe:
        pos_q = (
            f"#{dupe['pos']}" if "pos" in dupe else "the queue"
        )
        await _w(bot, user.id,
                 f"⚠️ Already in queue: {dupe['title'][:50]}\n"
                 f"Position: {pos_q}")
        return

    # Queue cap (re-check — another user might have filled it)
    if _total_active() >= _queue_max():
        await _w(bot, user.id,
                 f"🚫 Queue filled up ({_queue_max()} songs). Try again soon!")
        return

    # Consume the pending search
    _pop_search(user.id)

    pos = _add_request(
        user.id, user.username,
        pick["title"], pick["url"], pick["duration"],
    )
    if pos < 0:
        await _w(bot, user.id, "⚠️ Could not add your request. Try again.")
        return

    # If this is the first song, promote it to 'playing' and start the timer
    promoted = _promote_front()

    dur = f" [{pick['duration']}]" if pick["duration"] else ""
    url = f"\n{pick['url']}" if pick["url"] else ""
    await _w(bot, user.id,
             f"✅ Added #{pos}: {pick['title'][:50]}{dur}{url}"[:249])

    if promoted:
        await _play_song(bot, promoted)   # announces + starts timer + fires backend
    else:
        name = user.username[:15]
        await _chat(
            bot,
            f"🎵 @{name} added: {pick['title'][:55]}{dur} (#{pos} in queue)"
        )


async def handle_dj_queue(bot: "BaseBot", user: "User") -> None:
    """!queue  —  show next 5 pending songs (not counting now-playing)."""
    now = _get_nowplaying()
    upcoming = _get_queue(limit=5)

    if not now and not upcoming:
        await _w(bot, user.id,
                 "🎵 Queue is empty! Use !request <song> to add one.")
        return

    total_pending = _pending_count()
    lines: list[str] = []

    if now:
        dur  = f" [{now['duration']}]" if now.get("duration") else ""
        st   = "▶️" if now["status"] == "playing" else "🎵"
        lines.append(f"{st} NOW: {now['title'][:40]}{dur}")

    if upcoming:
        lines.append(f"— Next {min(len(upcoming), 5)} of {total_pending} pending —")
        for i, r in enumerate(upcoming, 1):
            dur = f" [{r['duration']}]" if r.get("duration") else ""
            lines.append(f"#{i} {r['title'][:38]}{dur}")

    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_dj_nowplaying(bot: "BaseBot", user: "User") -> None:
    """!nowplaying / !np  —  show current song + progress + link + skip votes."""
    now = _get_nowplaying()
    if not now:
        await _w(bot, user.id,
                 "🎵 Nothing playing. Use !request <song> to add one!")
        return

    # Progress bar — only when we have _playing_since + a parseable duration
    progress_str = ""
    if _playing_since > 0 and now.get("status") == "playing" and now.get("duration"):
        elapsed   = int(time.time() - _playing_since)
        total_sec = _parse_duration(now["duration"])
        if total_sec > 0:
            elapsed      = min(elapsed, total_sec)
            progress_str = f"\n[{_fmt_secs(elapsed)} / {now['duration']}]"

    url    = f"\n🔗 {now['youtube_url']}" if now.get("youtube_url") else ""
    votes  = len(_skip_votes.get(now["id"], set()))
    thresh = _vote_thresh()
    vote_str = (
        f"\n👎 Skip votes: {votes}/{thresh} (use !skipvote)"
        if votes > 0 else ""
    )
    upcoming = _get_queue(limit=1)
    nxt_str = ""
    if upcoming:
        n = upcoming[0]
        nd = f" [{n['duration']}]" if n.get("duration") else ""
        nxt_str = f"\nUp next: {n['title'][:40]}{nd} (@{n['username'][:12]})"

    msg = (
        f"▶️ Now Playing:\n"
        f"{now['title'][:60]}\n"
        f"Requested by @{now['username'][:15]}"
        f"{progress_str}{url}{vote_str}{nxt_str}"
    )
    await _w(bot, user.id, msg[:249])


async def handle_dj_skip(bot: "BaseBot", user: "User") -> None:
    """!skip / !djskip  —  force-advance queue (manager+)."""
    if not can_manage_games(user.username):
        await _w(bot, user.id,
                 "🔒 Manager only. Players can use !skipvote.")
        return

    now = _get_nowplaying()
    if not now:
        await _w(bot, user.id, "🎵 Queue is already empty.")
        return

    await _do_advance(bot)


async def handle_dj_skipvote(bot: "BaseBot", user: "User") -> None:
    """!skipvote  —  public vote to skip current song."""
    now = _get_nowplaying()
    if now is None:
        await _w(bot, user.id, "🎵 Nothing is playing right now.")
        return

    row_id = now["id"]
    if row_id not in _skip_votes:
        _skip_votes[row_id] = set()

    if user.id in _skip_votes[row_id]:
        cur  = len(_skip_votes[row_id])
        need = _vote_thresh() - cur
        await _w(bot, user.id,
                 f"👎 Already voted. {need} more vote(s) needed to skip.")
        return

    _skip_votes[row_id].add(user.id)
    votes  = len(_skip_votes[row_id])
    thresh = _vote_thresh()

    if votes >= thresh:
        title = now["title"][:50]
        await _chat(
            bot,
            f"👎 Vote skip passed ({votes}/{thresh})! Skipping: {title}"
        )
        await _do_advance(bot)
    else:
        remaining = thresh - votes
        name = user.username[:15]
        await _chat(
            bot,
            f"👎 @{name} voted to skip. {remaining} more vote(s) needed."
        )


async def handle_dj_stopmusic(bot: "BaseBot", user: "User") -> None:
    """!stopmusic  —  clear all pending + playing entries (manager+)."""
    global _playback_task, _playing_since, _repeat_song
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return

    cleared = _clear_queue()
    if _playback_task and not _playback_task.done():
        _playback_task.cancel()
    _playback_task = None
    _playing_since = 0.0
    _repeat_song   = None

    if cleared == 0:
        await _w(bot, user.id, "🎵 Queue was already empty.")
        return

    await _backend.stop()
    await _w(bot, user.id, f"🛑 Queue cleared. {cleared} song(s) removed.")
    await _chat(bot, f"🛑 DJ queue cleared. ({cleared} removed)")


async def handle_dj_config(bot: "BaseBot", user: "User") -> None:
    """!djconfig  —  show current DJ settings (manager+)."""
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return

    qmax  = _queue_max()
    umax  = _user_max()
    cd    = _cooldown()
    vt    = _vote_thresh()
    total = _total_active()
    lock  = "ON" if _dj_locked() else "off"
    bk    = type(_backend).__name__

    await _w(
        bot, user.id,
        f"🎛️ DJ Config:\n"
        f"Queue: {total}/{qmax} | PerUser: {umax} | Cooldown: {cd}s\n"
        f"SkipVotes: {vt} | Lock: {lock} | Backend: {bk}\n"
        f"!djset <queuemax|cooldown|usermax|votethreshold> <val>"
    )


async def handle_dj_set(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!djset <key> <value>  —  change a DJ setting (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    if len(args) < 3:
        keys = " | ".join(_CFG_META.keys())
        await _w(bot, user.id,
                 f"🎛️ Usage: !djset <key> <value>\n"
                 f"Keys: {keys}")
        return

    key_raw = args[1].lower()
    val_raw = args[2]

    if key_raw not in _CFG_META:
        keys = " | ".join(_CFG_META.keys())
        await _w(bot, user.id,
                 f"⚠️ Unknown key '{key_raw}'.\nValid keys: {keys}")
        return

    setting_key, vmin, vmax = _CFG_META[key_raw]

    if not val_raw.isdigit():
        await _w(bot, user.id, f"⚠️ Value must be a number ({vmin}–{vmax}).")
        return

    val = int(val_raw)
    if not (vmin <= val <= vmax):
        await _w(bot, user.id,
                 f"⚠️ {key_raw} must be between {vmin} and {vmax}.")
        return

    db.set_room_setting(setting_key, str(val))
    await _w(bot, user.id, f"✅ {key_raw} set to {val}.")


async def handle_dj_debug(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!djdebug on|off  —  toggle search debug whispers (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        current = "ON" if _debug_mode() else "OFF"
        await _w(bot, user.id,
                 f"🔧 DJ debug mode is currently {current}.\n"
                 f"Usage: !djdebug on|off")
        return

    state = args[1].lower()
    db.set_room_setting(_CFG_DEBUG, state)
    label = "ON" if state == "on" else "OFF"
    await _w(bot, user.id,
             f"🔧 DJ debug mode: {label}.\n"
             f"{'Search errors will now be whispered to admins.' if state == 'on' else 'Search errors are now silent to regular users.'}")


async def handle_dj_lock(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!djlock on|off  —  block/allow new song requests (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        current = "ON" if _dj_locked() else "OFF"
        await _w(bot, user.id,
                 f"🔒 Request lock is currently {current}.\n"
                 f"Usage: !djlock on|off")
        return

    state = args[1].lower()
    db.set_room_setting(_CFG_LOCK, state)
    if state == "on":
        await _w(bot, user.id, "🔒 Queue locked. New requests are paused.")
        await _chat(bot, "🔒 Song requests are paused for now.")
    else:
        await _w(bot, user.id, "🔓 Queue unlocked. Requests are open again.")
        await _chat(bot, "🎵 Song requests are open! Use !request <song>.")


async def handle_dj_clear(bot: "BaseBot", user: "User") -> None:
    """!djclear  —  wipe entire queue, pending + playing (admin+)."""
    global _playback_task, _playing_since, _repeat_song
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    cleared = _clear_queue()
    if _playback_task and not _playback_task.done():
        _playback_task.cancel()
    _playback_task = None
    _playing_since = 0.0
    _repeat_song   = None

    if cleared == 0:
        await _w(bot, user.id, "🎵 Queue was already empty.")
        return

    await _backend.stop()
    await _w(bot, user.id, f"🗑️ Queue wiped. {cleared} song(s) removed.")
    await _chat(bot, f"🗑️ DJ queue wiped by staff. ({cleared} removed)")


async def handle_dj_remove(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!djremove <#>  —  remove a specific pending queue entry (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id,
                 "🎵 Usage: !djremove <position>\n"
                 "Use !queue to see positions.")
        return

    pos = int(args[1])
    removed = _remove_request_by_pos(pos)
    if removed is None:
        await _w(bot, user.id,
                 f"⚠️ No pending song at position {pos}.\n"
                 f"Use !queue to see the current list.")
        return

    title = removed["title"][:50]
    by    = removed.get("username", "?")
    await _w(bot, user.id, f"✅ Removed #{pos}: {title} (by {by})")


async def handle_dj_radio(bot: "BaseBot", user: "User") -> None:
    """!radio  —  show the configured radio stream URL (public)."""
    import os
    url = os.environ.get("RADIO_STREAM_URL", "").strip()
    if url:
        await _w(bot, user.id, f"📻 Radio stream:\n{url[:220]}")
    else:
        await _w(bot, user.id,
                 "📻 Radio stream not configured yet.\n"
                 "Ask staff to set up RADIO_STREAM_URL.")


async def handle_dj_status(bot: "BaseBot", user: "User") -> None:
    """!djstatus  —  lock state, queue size, and now playing (public)."""
    lock   = "🔒 LOCKED" if _dj_locked() else "🔓 open"
    total  = _total_active()
    qmax   = _queue_max()
    now    = _get_nowplaying()
    if now:
        dur = f" [{now['duration']}]" if now.get("duration") else ""
        np  = f"▶️ {now['title'][:50]}{dur}\n   by @{now['username'][:15]}"
    else:
        np = "▶️ Nothing playing"
    await _w(
        bot, user.id,
        f"🎵 DJ Status:\n"
        f"Requests: {lock} | Queue: {total}/{qmax}\n"
        f"{np}"
    )


def _get_history(limit: int = 5) -> list[dict]:
    """Return last `limit` completed songs (played or skipped), newest first."""
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT title, username, status FROM dj_requests "
            "WHERE status IN ('played','skipped') "
            "ORDER BY requested_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


async def handle_dj_history(bot: "BaseBot", user: "User") -> None:
    """!djhistory  —  last 5 played/skipped songs (public)."""
    rows = _get_history(5)
    if not rows:
        await _w(bot, user.id,
                 "🎵 No play history yet. Request a song with !request!")
        return
    lines = ["🎵 Recent plays:"]
    for i, r in enumerate(rows, 1):
        tag = "⏭" if r["status"] == "skipped" else "✅"
        lines.append(f"{i}. {tag} {r['title'][:45]} (@{r['username'][:12]})")
    await _w(bot, user.id, "\n".join(lines)[:249])


def _get_top_requests(limit: int = 5) -> list[dict]:
    """Return top `limit` most-requested songs by distinct title (all time)."""
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT title, COUNT(*) AS cnt FROM dj_requests "
            "GROUP BY lower(title) ORDER BY cnt DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


async def handle_dj_toprequests(bot: "BaseBot", user: "User") -> None:
    """!toprequests  —  most requested songs all time (public)."""
    rows = _get_top_requests(5)
    if not rows:
        await _w(bot, user.id,
                 "🎵 No requests yet. Be the first with !request <song>!")
        return
    lines = ["🏆 Top requests:"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {r['title'][:48]} x{r['cnt']}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_dj_upnext(bot: "BaseBot", user: "User") -> None:
    """!upnext  —  show next 3 queued songs (public)."""
    upcoming = _get_queue(limit=3)
    if not upcoming:
        await _w(bot, user.id,
                 "🎵 Nothing queued. Use !request <song> to be first!")
        return
    lines = ["🎵 Up Next:"]
    for i, r in enumerate(upcoming, 1):
        dur = f" [{r['duration']}]" if r.get("duration") else ""
        lines.append(f"{i}. {r['title'][:44]}{dur} (@{r['username'][:12]})")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_dj_stats(bot: "BaseBot", user: "User") -> None:
    """!djstats  —  aggregate DJ statistics (public)."""
    s = _get_djstats()
    await _w(
        bot, user.id,
        f"📊 DJ Stats:\n"
        f"Total requests: {s['total']}\n"
        f"Top requester: @{s['top_name']} ({s['top_cnt']} songs)\n"
        f"Queue: {s['queue']}/{_queue_max()} | Uptime: {s['uptime']}"
    )


async def handle_dj_favorite(bot: "BaseBot", user: "User") -> None:
    """!favorite  —  save the currently playing song to favorites (public)."""
    now = _get_nowplaying()
    if not now:
        await _w(bot, user.id,
                 "🎵 Nothing is playing right now.\n"
                 "Request a song with !request <song>!")
        return
    added = _add_favorite(
        user.id, user.username,
        now["title"], now.get("youtube_url", ""),
    )
    if added:
        await _w(bot, user.id, f"⭐ Favorited: {now['title'][:55]}")
    else:
        await _w(bot, user.id,
                 f"⭐ Already in your favorites: {now['title'][:50]}")


async def handle_dj_favorites(bot: "BaseBot", user: "User") -> None:
    """!favorites  —  list your saved songs (public)."""
    rows = _get_user_favorites(user.id, limit=5)
    if not rows:
        await _w(bot, user.id,
                 "⭐ No favorites yet!\n"
                 "Use !favorite while a song is playing to save it.")
        return
    lines = ["⭐ Your favorites:"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {r['title'][:52]}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_dj_repeat(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!repeat on|off  —  loop the current song when it ends (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        cur = "ON" if _dj_repeat() else "OFF"
        await _w(bot, user.id,
                 f"🔁 Repeat is currently {cur}.\nUsage: !repeat on|off")
        return
    state = args[1].lower()
    db.set_room_setting(_CFG_REPEAT, state)
    if state == "on":
        await _w(bot, user.id, "🔁 Repeat ON — current song will loop.")
    else:
        await _w(bot, user.id, "🔁 Repeat OFF.")


async def handle_dj_shuffle(bot: "BaseBot", user: "User") -> None:
    """!shuffle  —  randomise the pending queue order (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT id FROM dj_requests WHERE status='pending' "
            "ORDER BY requested_at ASC"
        ).fetchall()
        if len(rows) < 2:
            conn.close()
            await _w(bot, user.id,
                     "🔀 Need at least 2 pending songs to shuffle.")
            return
        ids = [r["id"] for r in rows]
        _random.shuffle(ids)
        for i, rid in enumerate(ids):
            conn.execute(
                "UPDATE dj_requests SET requested_at=datetime('now',?) WHERE id=?",
                (f"-{len(ids) - i} seconds", rid),
            )
        conn.commit()
        conn.close()
    except Exception:
        await _w(bot, user.id, "⚠️ Shuffle failed. Try again.")
        return
    await _w(bot, user.id, f"🔀 Shuffled {len(rows)} songs!")
    await _chat(bot, f"🔀 Queue shuffled! ({len(rows)} songs)")


async def handle_dj_autoplay(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!autoplay on|off  —  auto-search related songs when queue empties (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        cur = "ON" if _dj_autoplay() else "OFF"
        await _w(bot, user.id,
                 f"🤖 Autoplay is currently {cur}.\nUsage: !autoplay on|off")
        return
    state = args[1].lower()
    db.set_room_setting(_CFG_AUTOPLAY, state)
    if state == "on":
        await _w(bot, user.id,
                 "🤖 Autoplay ON — I'll queue related songs when the queue empties.")
    else:
        await _w(bot, user.id, "🤖 Autoplay OFF.")


async def handle_dj_vibes(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!djvibes on|off  —  toggle DJ personality hype lines between songs (admin+)."""
    if not is_admin(user.username):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        cur = "ON" if _dj_personality() else "OFF"
        await _w(bot, user.id,
                 f"🎤 DJ vibes are currently {cur}.\nUsage: !djvibes on|off")
        return
    state = args[1].lower()
    db.set_room_setting(_CFG_PERSONALITY, state)
    if state == "on":
        await _w(bot, user.id, "🎤 DJ vibes ON — hype lines between songs!")
        await _chat(bot, "🎤 DJ DUDU vibes mode activated! Let's gooo! 🔥")
    else:
        await _w(bot, user.id, "🎤 DJ vibes OFF.")


async def handle_dj_help(bot: "BaseBot", user: "User") -> None:
    """!djhelp  —  list all DJ commands."""
    await _w(
        bot, user.id,
        "🎵 DJ Commands:\n"
        "!request/!req/!sr/!song | !pick <1-5>\n"
        "!np | !upnext | !queue | !skipvote | !favorite\n"
        "!favorites | !djstats | !djstatus | !radio\n"
        "Staff: !skip !repeat !shuffle !stopmusic !djconfig\n"
        "Admin: !djlock !djclear !djremove !autoplay !djvibes"
    )
