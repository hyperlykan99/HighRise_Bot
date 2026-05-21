"""modules/radio_rewards.py
==========================
Lightweight radio engagement stats, rewards, and leaderboards.

Tables (lazy-bootstrapped — no migration list entry required):
  radio_user_stats  — per-user counters + radio_points
  radio_song_stats  — per-song counters
  radio_reward_log  — dedup ledger + daily-cap tracking

Point schedule (configurable via _POINT_MAP):
  request         +5   daily cap only (no per-song dedup)
  like            +1   once per user per song
  dislike         +1   once per user per song
  favorite        +2   once per user per song
  playlist_create +3   once per user per playlist name
  playlist_add    +1   once per user per playlist+song
  playlist_play   +2   daily cap only

Daily cap: 50 radio points per user per day.
"""
from __future__ import annotations
import time
from typing import TYPE_CHECKING

import database as db

if TYPE_CHECKING:
    from highrise import BaseBot
    from highrise.models import User

_LOG       = "[RADIO_REWARDS]"
_DAILY_CAP = 50

_POINT_MAP: dict[str, int] = {
    "request":         5,
    "like":            1,
    "dislike":         1,
    "favorite":        2,
    "playlist_create": 3,
    "playlist_add":    1,
    "playlist_play":   2,
}

_PER_SONG_ACTIONS   = frozenset(("like", "dislike", "favorite"))
_PER_TARGET_ACTIONS = frozenset(("playlist_create", "playlist_add"))

_STAT_COL: dict[str, str] = {
    "request":         "requests_count",
    "like":            "likes_count",
    "dislike":         "dislikes_count",
    "favorite":        "favorites_count",
    "playlist_create": "playlists_created",
    "playlist_add":    "playlist_songs_added",
    "playlist_play":   "playlist_plays",
}

_SONG_COL: dict[str, str] = {
    "request":     "request_count",
    "like":        "like_count",
    "dislike":     "dislike_count",
    "favorite":    "favorite_count",
    "playlist_add": "playlist_add_count",
}

_DB_READY = False


# ─── Lazy table bootstrap ─────────────────────────────────────────────────────

def _bootstrap() -> None:
    global _DB_READY
    if _DB_READY:
        return
    try:
        conn = db.get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS radio_user_stats (
                user_id              TEXT    PRIMARY KEY,
                username             TEXT    NOT NULL DEFAULT '',
                requests_count       INTEGER NOT NULL DEFAULT 0,
                likes_count          INTEGER NOT NULL DEFAULT 0,
                dislikes_count       INTEGER NOT NULL DEFAULT 0,
                favorites_count      INTEGER NOT NULL DEFAULT 0,
                playlists_created    INTEGER NOT NULL DEFAULT 0,
                playlist_songs_added INTEGER NOT NULL DEFAULT 0,
                playlist_plays       INTEGER NOT NULL DEFAULT 0,
                radio_points         INTEGER NOT NULL DEFAULT 0,
                last_updated         TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS radio_song_stats (
                song_key           TEXT    PRIMARY KEY,
                title              TEXT    NOT NULL DEFAULT '',
                artist             TEXT    NOT NULL DEFAULT '',
                request_count      INTEGER NOT NULL DEFAULT 0,
                like_count         INTEGER NOT NULL DEFAULT 0,
                dislike_count      INTEGER NOT NULL DEFAULT 0,
                favorite_count     INTEGER NOT NULL DEFAULT 0,
                playlist_add_count INTEGER NOT NULL DEFAULT 0,
                last_played_at     TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS radio_reward_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL DEFAULT '',
                username    TEXT    NOT NULL DEFAULT '',
                action      TEXT    NOT NULL DEFAULT '',
                song_key    TEXT    NOT NULL DEFAULT '',
                target_key  TEXT    NOT NULL DEFAULT '',
                points      INTEGER NOT NULL DEFAULT 0,
                rewarded_at TEXT    NOT NULL DEFAULT (datetime('now')),
                reward_day  TEXT    NOT NULL DEFAULT (date('now'))
            )
        """)
        conn.commit()
        conn.close()
        _DB_READY = True
    except Exception as exc:
        print(f"{_LOG} bootstrap error: {exc!r}")


# ─── Core reward recorder ─────────────────────────────────────────────────────

def record_reward(
    user_id:    str,
    username:   str,
    action:     str,
    song_key:   str = "",
    target_key: str = "",
) -> int:
    """
    Record one radio engagement action.

    Returns the points actually awarded (0 = deduped or capped).
    Safe to call from async handlers via asyncio loop executor or direct call
    (SQLite is thread-safe in serialized mode which is the default).
    """
    _bootstrap()
    points = _POINT_MAP.get(action, 0)
    if points <= 0:
        return 0

    today = time.strftime("%Y-%m-%d")
    song_key   = (song_key   or "")[:150]
    target_key = (target_key or "")[:150]

    try:
        conn = db.get_connection()

        # Per-song dedup (like / dislike / favorite)
        if action in _PER_SONG_ACTIONS and song_key:
            row = conn.execute(
                "SELECT id FROM radio_reward_log "
                "WHERE user_id=? AND action=? AND song_key=?",
                (user_id, action, song_key),
            ).fetchone()
            if row:
                conn.close()
                return 0

        # Per-target dedup (playlist_create / playlist_add)
        if action in _PER_TARGET_ACTIONS and target_key:
            row = conn.execute(
                "SELECT id FROM radio_reward_log "
                "WHERE user_id=? AND action=? AND target_key=?",
                (user_id, action, target_key),
            ).fetchone()
            if row:
                conn.close()
                return 0

        # Daily cap check
        day_pts = (conn.execute(
            "SELECT COALESCE(SUM(points),0) FROM radio_reward_log "
            "WHERE user_id=? AND reward_day=?",
            (user_id, today),
        ).fetchone() or (0,))[0]
        if day_pts >= _DAILY_CAP:
            conn.close()
            return 0
        points = min(points, _DAILY_CAP - day_pts)

        # Log reward entry
        conn.execute(
            "INSERT INTO radio_reward_log "
            "(user_id, username, action, song_key, target_key, points, reward_day) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, username.lower(), action, song_key, target_key, points, today),
        )

        # Upsert user stats row
        conn.execute(
            "INSERT INTO radio_user_stats (user_id, username) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "username=excluded.username, last_updated=datetime('now')",
            (user_id, username.lower()),
        )
        stat_col = _STAT_COL.get(action)
        if stat_col:
            conn.execute(
                f"UPDATE radio_user_stats "
                f"SET {stat_col}={stat_col}+1, "
                f"radio_points=radio_points+?, "
                f"last_updated=datetime('now') "
                f"WHERE user_id=?",
                (points, user_id),
            )
        else:
            conn.execute(
                "UPDATE radio_user_stats "
                "SET radio_points=radio_points+?, last_updated=datetime('now') "
                "WHERE user_id=?",
                (points, user_id),
            )

        # Update song stats (best-effort)
        song_col = _SONG_COL.get(action)
        if song_key and song_col:
            conn.execute(
                "INSERT INTO radio_song_stats (song_key) VALUES (?) "
                "ON CONFLICT(song_key) DO UPDATE "
                f"SET {song_col}={song_col}+1",
                (song_key,),
            )

        conn.commit()
        conn.close()
        return points

    except Exception as exc:
        print(f"{_LOG} record_reward error action={action!r}: {exc!r}")
        try:
            conn.close()
        except Exception:
            pass
        return 0


def update_song_info(song_key: str, title: str, artist: str = "") -> None:
    """Store display title/artist for a song_key (best-effort, non-blocking)."""
    _bootstrap()
    if not song_key:
        return
    try:
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO radio_song_stats (song_key, title, artist) VALUES (?, ?, ?) "
            "ON CONFLICT(song_key) DO UPDATE SET "
            "title=CASE WHEN excluded.title != '' THEN excluded.title ELSE title END, "
            "artist=CASE WHEN excluded.artist != '' THEN excluded.artist ELSE artist END",
            (song_key[:150], title[:120], artist[:80]),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"{_LOG} update_song_info error: {exc!r}")


# ─── Read helpers ─────────────────────────────────────────────────────────────

def get_user_stats(user_id: str) -> dict:
    """Return stats dict for a user (all zeros if no record)."""
    _bootstrap()
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT requests_count, likes_count, dislikes_count, "
                "favorites_count, playlists_created, playlist_songs_added, "
                "playlist_plays, radio_points "
                "FROM radio_user_stats WHERE user_id=?",
                (user_id,),
            ).fetchone()
    except Exception:
        row = None
    if row:
        return {
            "requests":   row[0], "likes":    row[1],
            "dislikes":   row[2], "favorites": row[3],
            "playlists":  row[4], "pl_songs": row[5],
            "pl_plays":   row[6], "points":   row[7],
        }
    return {
        "requests": 0, "likes": 0, "dislikes": 0, "favorites": 0,
        "playlists": 0, "pl_songs": 0, "pl_plays": 0, "points": 0,
    }


def top_listeners(limit: int = 5) -> list:
    """Users with most radio_points, descending."""
    _bootstrap()
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT username, radio_points FROM radio_user_stats "
                "WHERE radio_points > 0 "
                "ORDER BY radio_points DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"username": r[0], "points": r[1]} for r in rows]
    except Exception:
        return []


def top_requesters(limit: int = 5) -> list:
    """Users with most successful requests, descending."""
    _bootstrap()
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT username, requests_count FROM radio_user_stats "
                "WHERE requests_count > 0 "
                "ORDER BY requests_count DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"username": r[0], "count": r[1]} for r in rows]
    except Exception:
        return []


def top_songs(limit: int = 5) -> list:
    """Songs with most requests, descending."""
    _bootstrap()
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT song_key, title, request_count FROM radio_song_stats "
                "WHERE request_count > 0 "
                "ORDER BY request_count DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"song_key": r[0], "title": r[1], "count": r[2]} for r in rows]
    except Exception:
        return []


# ─── Whisper helper ───────────────────────────────────────────────────────────

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception as exc:
        print(f"{_LOG} whisper error: {exc!r}")


# ─── Command handlers ─────────────────────────────────────────────────────────

async def handle_radiostats(bot: "BaseBot", user: "User", args: list) -> None:
    """!radiostats — show your radio engagement stats."""
    uid = user.id
    s   = get_user_stats(uid)
    if s["points"] == 0 and s["requests"] == 0 and s["likes"] == 0:
        await _w(bot, uid,
                 "🎧 Radio Stats\n"
                 "No radio activity yet. Use !play to request a song.")
        return
    msg = (
        f"🎧 Radio Stats\n"
        f"Requests: {s['requests']}\n"
        f"Likes: {s['likes']} | Dislikes: {s['dislikes']}\n"
        f"Favorites: {s['favorites']}\n"
        f"Playlists: {s['playlists']}\n"
        f"🏆 {s['points']} radio pts"
    )
    await _w(bot, uid, msg)


async def handle_toplisteners(bot: "BaseBot", user: "User", _args: list) -> None:
    """!toplisteners — users with the most radio points."""
    rows = top_listeners(limit=5)
    if not rows:
        await _w(bot, user.id, "🎧 No listener stats yet. Start engaging to earn points!")
        return
    lines = ["🎧 Top Listeners"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. @{r['username'][:18]} — {r['points']} pts")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_toprequests(bot: "BaseBot", user: "User", _args: list) -> None:
    """!toprequests — users with the most successful song requests."""
    rows = top_requesters(limit=5)
    if not rows:
        await _w(bot, user.id, "💿 No request stats yet. Use !play to request a song!")
        return
    lines = ["💿 Top Requesters"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. @{r['username'][:18]} — {r['count']} requests")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ─── Top liked / top disliked helpers ────────────────────────────────────────

def top_liked_songs(limit: int = 5) -> list:
    """Songs with the most likes, sourced from dj_ratings + radio_song_stats title."""
    _bootstrap()
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT dr.song_key, "
                "COALESCE(NULLIF(rss.title,''), dr.song_key) AS disp_title, "
                "COALESCE(rss.artist,'') AS disp_artist, "
                "COUNT(*) AS cnt "
                "FROM dj_ratings dr "
                "LEFT JOIN radio_song_stats rss ON dr.song_key = rss.song_key "
                "WHERE dr.rating='like' "
                "GROUP BY dr.song_key ORDER BY cnt DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"song_key": r[0], "title": r[1], "artist": r[2], "count": r[3]}
                for r in rows]
    except Exception:
        return []


def top_disliked_songs(limit: int = 5) -> list:
    """Songs with the most dislikes, sourced from dj_ratings + radio_song_stats title."""
    _bootstrap()
    try:
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT dr.song_key, "
                "COALESCE(NULLIF(rss.title,''), dr.song_key) AS disp_title, "
                "COALESCE(rss.artist,'') AS disp_artist, "
                "COUNT(*) AS cnt "
                "FROM dj_ratings dr "
                "LEFT JOIN radio_song_stats rss ON dr.song_key = rss.song_key "
                "WHERE dr.rating='dislike' "
                "GROUP BY dr.song_key ORDER BY cnt DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"song_key": r[0], "title": r[1], "artist": r[2], "count": r[3]}
                for r in rows]
    except Exception:
        return []


def _fmt_song_line(i: int, row: dict, label: str) -> str:
    """Format one leaderboard song line. Title prettified; no raw file paths exposed."""
    raw   = row.get("title") or row.get("song_key") or "Unknown"
    # Prettify a lowercased song_key (replace hyphens, apply title-case)
    title  = raw.replace("-", " ").replace("_", " ").strip()
    title  = title[:1].upper() + title[1:] if title else "Unknown"
    title  = title[:26]
    artist = (row.get("artist") or "")[:14].strip()
    n      = row["count"]
    core   = f"{i}. {title}"
    if artist:
        core += f" — {artist}"
    return f"{core} • {n} {label}"


async def handle_topliked(bot: "BaseBot", user: "User", _args: list) -> None:
    """!topliked — top 5 songs by most likes."""
    rows = top_liked_songs(limit=5)
    if not rows:
        await _w(bot, user.id, "👍 No song ratings yet. React with !like while a song plays!")
        return
    items      = [_fmt_song_line(i, r, "likes") for i, r in enumerate(rows, 1)]
    header     = "👍 Top Liked Songs"
    chunks     = [items[i:i + 3] for i in range(0, len(items), 3)]
    total_pgs  = len(chunks)
    for pg, chunk in enumerate(chunks, 1):
        hdr = f"{header} {pg}/{total_pgs}" if total_pgs > 1 else header
        await _w(bot, user.id, (hdr + "\n" + "\n".join(chunk))[:249])
        if pg < total_pgs:
            await asyncio.sleep(0.1)


async def handle_topdisliked(bot: "BaseBot", user: "User", _args: list) -> None:
    """!topdisliked — top 5 songs by most dislikes."""
    rows = top_disliked_songs(limit=5)
    if not rows:
        await _w(bot, user.id, "👎 No dislike ratings yet.")
        return
    items      = [_fmt_song_line(i, r, "dislikes") for i, r in enumerate(rows, 1)]
    header     = "👎 Top Disliked Songs"
    chunks     = [items[i:i + 3] for i in range(0, len(items), 3)]
    total_pgs  = len(chunks)
    for pg, chunk in enumerate(chunks, 1):
        hdr = f"{header} {pg}/{total_pgs}" if total_pgs > 1 else header
        await _w(bot, user.id, (hdr + "\n" + "\n".join(chunk))[:249])
        if pg < total_pgs:
            await asyncio.sleep(0.1)
