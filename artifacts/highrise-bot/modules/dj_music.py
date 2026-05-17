"""
modules/dj_music.py
-------------------
Production-ready song-request queue for DJ_DUDU.

Bot mode : "dj"  (BOT_MODE=dj)   Display name: DJ_DUDU
All commands are owned exclusively by the dj bot.

Public commands:
  !request <song>      Search YouTube, whisper top 5 results
  !pick <1-5>          Confirm a search result → add to queue
  !queue / !djqueue    Show next 5 pending songs
  !np / !nowplaying    Show currently featured song + link
  !skipvote            Vote to skip current song
  !djhelp              Show all DJ commands

Manager commands:
  !skip / !djskip      Force-advance the queue
  !stopmusic           Clear all pending / playing entries
  !djconfig            Show current DJ settings

Admin commands:
  !djset <key> <val>   Change a DJ setting:
                         queuemax <1-50>      max queue size          (default 20)
                         cooldown <1-60>      request cooldown in min (default 5)
                         votethreshold <2-10> votes needed to auto-skip (default 3)

Architecture:
  • PlaybackBackend protocol  — plug in IcecastBackend later with zero handler changes
  • NullBackend               — active now (no-op, queue-only mode)
  • Explicit status='playing' — front-of-queue song is promoted; backend.play() called there
  • status state machine:     pending → playing → played | skipped
  • Search TTL (3 min)        — stale !pick calls are rejected cleanly
  • Duplicate guard           — exact URL match + normalised-title match block re-requests
  • Configurable via db.get_room_setting / db.set_room_setting
  • All DB ops use get_connection() — no module-level connection held open

DB table : dj_requests  (migration 3.2M + 3.2N in database.py)
Settings : room_settings keys  dj_queue_max | dj_cooldown_mins | dj_skipvote_threshold
"""
from __future__ import annotations

import asyncio
import re
import string
import time
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import database as db
from modules.permissions import can_manage_games, is_admin

if TYPE_CHECKING:
    from highrise import BaseBot, User


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
_CFG_COOLDOWN    = "dj_cooldown_mins"
_CFG_VOTE_THRESH = "dj_skipvote_threshold"

_CFG_DEFAULTS: dict[str, str] = {
    _CFG_QUEUE_MAX:   "20",
    _CFG_COOLDOWN:    "5",
    _CFG_VOTE_THRESH: "3",
}

# Friendly names and valid ranges for !djset
_CFG_META: dict[str, tuple[str, int, int]] = {
    # key → (setting_key, min_val, max_val)
    "queuemax":      (_CFG_QUEUE_MAX,   1,  50),
    "cooldown":      (_CFG_COOLDOWN,    1,  60),
    "votethreshold": (_CFG_VOTE_THRESH, 2,  10),
}


def _cfg(key: str) -> int:
    return int(db.get_room_setting(key, _CFG_DEFAULTS[key]))


def _queue_max()   -> int: return _cfg(_CFG_QUEUE_MAX)
def _cooldown()    -> int: return _cfg(_CFG_COOLDOWN)
def _vote_thresh() -> int: return _cfg(_CFG_VOTE_THRESH)


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


def _user_cooldown_mins(user_id: str) -> int:
    """
    Minutes since user's most recent request that is still pending/playing.
    Returns 999 if no recent request exists (i.e. cooldown has cleared).
    """
    try:
        conn = db.get_connection()
        row  = conn.execute(
            """SELECT (CAST(strftime('%s','now') AS INTEGER)
                       - CAST(strftime('%s', requested_at) AS INTEGER)) / 60 AS mins_ago
               FROM dj_requests
               WHERE user_id = ? AND status IN ('pending','playing')
               ORDER BY requested_at DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        conn.close()
        if row and row["mins_ago"] is not None:
            return int(row["mins_ago"])
    except Exception:
        pass
    return 999


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

def _yt_search_sync(query: str, max_results: int = 5) -> list[dict]:
    """Blocking YouTube search via yt-dlp.  Run in executor — do not await."""
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return []

    opts = {
        "quiet":           True,
        "no_warnings":     True,
        "extract_flat":    True,
        "default_search":  f"ytsearch{max_results}",
        "skip_download":   True,
        "ignoreerrors":    True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info    = ydl.extract_info(query, download=False) or {}
            entries = info.get("entries") or []
            results: list[dict] = []
            for e in entries[:max_results]:
                if not e:
                    continue
                secs = e.get("duration") or 0
                m, s = divmod(int(secs), 60)
                results.append({
                    "title":    (e.get("title") or "Unknown")[:80],
                    "url":      f"https://youtu.be/{e.get('id', '')}",
                    "duration": f"{m}:{s:02d}" if secs else "?:??",
                    "channel":  (e.get("uploader") or e.get("channel") or "")[:30],
                })
            return results
    except Exception:
        return []


async def _yt_search(query: str, max_results: int = 5) -> list[dict]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _yt_search_sync, query, max_results)


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
# Internal: advance + announce
# ---------------------------------------------------------------------------

async def _do_advance(bot: "BaseBot") -> None:
    """
    Advance the queue, fire backend.play(), post room announcement.
    Called by !skip, !skipvote (auto-skip), and future auto-advance hook.
    """
    nxt = _advance_queue()
    if nxt:
        await _backend.play(nxt.get("youtube_url", ""), nxt["title"])
        url_part = f"\n{nxt['youtube_url']}" if nxt.get("youtube_url") else ""
        await _chat(
            bot,
            f"⏭️ Now playing: {nxt['title'][:55]}"
            f" (@{nxt['username'][:15]}){url_part}"
        )
    else:
        await _backend.stop()
        await _chat(bot, "🎵 Queue finished. Use !request <song> to add more!")


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

    # Rate-limit check
    mins_ago = _user_cooldown_mins(user.id)
    cooldown = _cooldown()
    if mins_ago < cooldown:
        wait = cooldown - mins_ago
        await _w(bot, user.id, f"⏳ You can request again in {wait}m.")
        return

    # Queue cap check
    if _total_active() >= _queue_max():
        await _w(bot, user.id,
                 f"🚫 Queue is full ({_queue_max()} songs). Try again soon!")
        return

    await _w(bot, user.id, f"🔍 Searching: {query[:60]}…")

    results = await _yt_search(query)
    if not results:
        await _w(bot, user.id,
                 "⚠️ No results found. Try a different search term.")
        return

    _store_search(user.id, results)

    lines = ["🎵 Pick with !pick <number>:"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title'][:45]} [{r['duration']}]")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_dj_pick(
    bot: "BaseBot", user: "User", args: list[str],
) -> None:
    """!pick <1-5>  —  confirm a search result and add it to the queue."""
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

    # If this is the first song, promote it to 'playing' and fire backend
    promoted = _promote_front()
    if promoted:
        await _backend.play(promoted.get("youtube_url", ""), promoted["title"])

    dur = f" [{pick['duration']}]" if pick["duration"] else ""
    url = f"\n{pick['url']}" if pick["url"] else ""
    await _w(bot, user.id,
             f"✅ Added #{pos}: {pick['title'][:50]}{dur}{url}"[:249])

    name = user.username[:15]
    if promoted:
        await _chat(
            bot,
            f"🎵 @{name} requested: {pick['title'][:50]}{dur}\n"
            f"▶️ Now playing!{url}"[:249]
        )
    else:
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
    """!nowplaying / !np  —  show current song + link + skip vote status."""
    now = _get_nowplaying()
    if not now:
        await _w(bot, user.id,
                 "🎵 Nothing playing. Use !request <song> to add one!")
        return

    dur   = f" [{now['duration']}]" if now.get("duration") else ""
    url   = f"\n🔗 {now['youtube_url']}" if now.get("youtube_url") else ""
    votes = len(_skip_votes.get(now["id"], set()))
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
        f"▶️ Now Playing{dur}:\n"
        f"{now['title'][:60]}\n"
        f"Requested by @{now['username'][:15]}"
        f"{url}{vote_str}{nxt_str}"
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
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return

    cleared = _clear_queue()
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
    cd    = _cooldown()
    vt    = _vote_thresh()
    total = _total_active()
    bk    = type(_backend).__name__

    await _w(
        bot, user.id,
        f"🎛️ DJ Settings:\n"
        f"Queue: {total}/{qmax} | Cooldown: {cd}m | SkipVotes: {vt}\n"
        f"Backend: {bk}\n"
        f"Change with: !djset <queuemax|cooldown|votethreshold> <val>"
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


async def handle_dj_help(bot: "BaseBot", user: "User") -> None:
    """!djhelp  —  list all DJ commands."""
    await _w(
        bot, user.id,
        "🎵 DJ Commands:\n"
        "!request <song> — search YouTube\n"
        "!pick <1-5> — confirm result\n"
        "!queue — see upcoming songs\n"
        "!np — now playing + link\n"
        "!skipvote — vote to skip\n"
        "Staff: !skip !stopmusic !djconfig !djset"
    )
