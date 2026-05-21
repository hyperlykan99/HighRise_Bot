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
import asyncio
import re
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

def _sq(conn, sql: str, params: tuple = ()) -> int:
    """Run a scalar COUNT query, return 0 on any error."""
    try:
        return (conn.execute(sql, params).fetchone() or (0,))[0] or 0
    except Exception:
        return 0


def get_user_stats(user_id: str) -> dict:
    """
    Return stats dict for a user.
    Backfill-safe: uses max(stored_counter, live_count) so that users who
    were active before radio_user_stats existed still see their real data.
    """
    _bootstrap()
    stored = {
        "requests": 0, "likes": 0, "dislikes": 0, "favorites": 0,
        "playlists": 0, "pl_songs": 0, "pl_plays": 0, "points": 0,
    }
    try:
        conn = db.get_connection()

        # ── stored counters ───────────────────────────────────────────────
        row = conn.execute(
            "SELECT requests_count, likes_count, dislikes_count, "
            "favorites_count, playlists_created, playlist_songs_added, "
            "playlist_plays, radio_points "
            "FROM radio_user_stats WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if row:
            stored = {
                "requests":  row[0], "likes":    row[1],
                "dislikes":  row[2], "favorites": row[3],
                "playlists": row[4], "pl_songs":  row[5],
                "pl_plays":  row[6], "points":    row[7],
            }

        # ── live counts from source tables ────────────────────────────────
        live_likes     = _sq(conn,
            "SELECT COUNT(*) FROM dj_ratings WHERE user_id=? AND rating='like'",
            (user_id,))
        live_dislikes  = _sq(conn,
            "SELECT COUNT(*) FROM dj_ratings WHERE user_id=? AND rating='dislike'",
            (user_id,))
        live_favorites = _sq(conn,
            "SELECT COUNT(*) FROM dj_favorites WHERE user_id=?",
            (user_id,))
        live_playlists = _sq(conn,
            "SELECT COUNT(*) FROM radio_playlists WHERE user_id=?",
            (user_id,))
        live_pl_songs  = _sq(conn,
            "SELECT COUNT(*) FROM radio_playlist_songs WHERE user_id=?",
            (user_id,))
        live_requests  = _sq(conn,
            "SELECT COUNT(*) FROM yt_request_jobs WHERE user_id=?",
            (user_id,))

        conn.close()
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return stored

    # ── max(stored, live) so old activity is never hidden ─────────────────
    result = {
        "requests":  max(stored["requests"],  live_requests),
        "likes":     max(stored["likes"],     live_likes),
        "dislikes":  max(stored["dislikes"],  live_dislikes),
        "favorites": max(stored["favorites"], live_favorites),
        "playlists": max(stored["playlists"], live_playlists),
        "pl_songs":  max(stored["pl_songs"],  live_pl_songs),
        "pl_plays":  stored["pl_plays"],
        "points":    stored["points"],
    }

    # ── always compute derived estimate; use max(stored, derived) ────────
    derived = (
        result["requests"]  * _POINT_MAP["request"]
        + result["likes"]     * _POINT_MAP["like"]
        + result["dislikes"]  * _POINT_MAP["dislike"]
        + result["favorites"] * _POINT_MAP["favorite"]
        + result["playlists"] * _POINT_MAP["playlist_create"]
        + result["pl_songs"]  * _POINT_MAP["playlist_add"]
        + result["pl_plays"]  * _POINT_MAP["playlist_play"]
    )
    if derived > result["points"]:
        result["points"] = derived

    # ── write back if any field improved (MAX-only, never decreases) ──────
    needs_sync = (
        derived > stored["points"]
        or live_requests  > stored["requests"]
        or live_likes     > stored["likes"]
        or live_dislikes  > stored["dislikes"]
        or live_favorites > stored["favorites"]
        or live_playlists > stored["playlists"]
        or live_pl_songs  > stored["pl_songs"]
    )
    if needs_sync:
        _sync_user_stats_row(user_id, result)

    return result


def _sync_user_stats_row(user_id: str, s: dict) -> None:
    """
    Write corrected stats back to radio_user_stats using MAX for every column
    so this call can never lower any value — safe to call repeatedly.
    """
    try:
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO radio_user_stats "
            "(user_id, username, requests_count, likes_count, dislikes_count, "
            " favorites_count, playlists_created, playlist_songs_added, "
            " playlist_plays, radio_points) "
            "VALUES (?, '', ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "  requests_count       = MAX(requests_count,       excluded.requests_count), "
            "  likes_count          = MAX(likes_count,          excluded.likes_count), "
            "  dislikes_count       = MAX(dislikes_count,       excluded.dislikes_count), "
            "  favorites_count      = MAX(favorites_count,      excluded.favorites_count), "
            "  playlists_created    = MAX(playlists_created,    excluded.playlists_created), "
            "  playlist_songs_added = MAX(playlist_songs_added, excluded.playlist_songs_added), "
            "  playlist_plays       = MAX(playlist_plays,       excluded.playlist_plays), "
            "  radio_points         = MAX(radio_points,         excluded.radio_points), "
            "  last_updated         = datetime('now')",
            (user_id,
             s["requests"], s["likes"],    s["dislikes"],
             s["favorites"], s["playlists"], s["pl_songs"],
             s["pl_plays"],  s["points"]),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"{_LOG} _sync_user_stats_row error: {exc!r}")
        try:
            conn.close()
        except Exception:
            pass


def top_listeners(limit: int = 5) -> list:
    """
    Users with highest effective points, descending.
    Uses MAX(stored_points, derived_from_counts) inline so stale stored values
    never under-rank active users. Falls back to dj_ratings for users who have
    no radio_user_stats row at all.
    """
    _bootstrap()
    try:
        conn = db.get_connection()

        # Primary: radio_user_stats with inline derived-points guard
        rows = conn.execute(
            "SELECT username, "
            "  MAX(radio_points, "
            "      requests_count*5 + likes_count + dislikes_count "
            "      + favorites_count*2 + playlists_created*3 "
            "      + playlist_songs_added + playlist_plays*2"
            "  ) AS eff_pts "
            "FROM radio_user_stats "
            "WHERE radio_points > 0 OR requests_count > 0 "
            "   OR likes_count > 0 OR favorites_count > 0 "
            "ORDER BY eff_pts DESC LIMIT ?",
            (limit,),
        ).fetchall()

        if not rows:
            # Fallback: estimate from dj_ratings for users with no stats row
            rows = conn.execute(
                "SELECT username, "
                "  SUM(CASE WHEN rating='like'    THEN 1 ELSE 0 END)"
                " +SUM(CASE WHEN rating='dislike' THEN 1 ELSE 0 END) AS est "
                "FROM dj_ratings "
                "WHERE username != '' "
                "GROUP BY LOWER(username) "
                "ORDER BY est DESC LIMIT ?",
                (limit,),
            ).fetchall()

        conn.close()
        return [{"username": r[0], "points": int(r[1] or 0)} for r in rows if r[1]]
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return []


def top_requesters(limit: int = 5) -> list:
    """
    Users with the most successful requests, descending.
    Primary source: radio_user_stats.requests_count.
    Fallback: aggregate from yt_request_jobs when stats table is empty.
    """
    _bootstrap()
    try:
        conn = db.get_connection()

        rows = conn.execute(
            "SELECT username, requests_count FROM radio_user_stats "
            "WHERE requests_count > 0 ORDER BY requests_count DESC LIMIT ?",
            (limit,),
        ).fetchall()

        if not rows:
            rows = conn.execute(
                "SELECT username, COUNT(*) AS cnt "
                "FROM yt_request_jobs "
                "WHERE username != '' "
                "GROUP BY LOWER(username) "
                "ORDER BY cnt DESC LIMIT ?",
                (limit,),
            ).fetchall()

        conn.close()
        return [{"username": r[0], "count": int(r[1] or 0)} for r in rows if r[1]]
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        return []


def top_songs(limit: int = 5) -> list:
    """
    Songs with the most requests/plays, descending.
    Primary source: radio_song_stats.request_count.
    Fallback: aggregate from yt_request_jobs when stats table is empty.
    """
    _bootstrap()
    try:
        conn = db.get_connection()

        rows = conn.execute(
            "SELECT song_key, title, request_count FROM radio_song_stats "
            "WHERE request_count > 0 ORDER BY request_count DESC LIMIT ?",
            (limit,),
        ).fetchall()

        if not rows:
            # Derive from yt_request_jobs — song_key = LOWER(SUBSTR(title,1,150))
            rows = conn.execute(
                "SELECT LOWER(SUBSTR(title,1,150)) AS sk, title, COUNT(*) AS cnt "
                "FROM yt_request_jobs "
                "WHERE title != '' "
                "GROUP BY LOWER(SUBSTR(title,1,150)) "
                "ORDER BY cnt DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
            return [
                {"song_key": r[0], "title": r[1], "count": int(r[2] or 0)}
                for r in rows if r[2]
            ]

        conn.close()
        return [{"song_key": r[0], "title": r[1], "count": int(r[2] or 0)}
                for r in rows if r[2]]
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
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
        await _w(bot, user.id, "🎧 No listener data yet.")
        return
    lines = ["🎧 Top Listeners"]
    for r in rows[:5]:
        try:
            uname  = str(r.get("username") or "unknown")[:18]
            pts    = int(r.get("points") or 0)
            lines.append(f"{len(lines)}. @{uname} — {pts} pts")
        except Exception:
            lines.append(f"{len(lines)}. (data error)")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_toprequests(bot: "BaseBot", user: "User", _args: list) -> None:
    """!toprequests — users with the most successful song requests."""
    rows = top_requesters(limit=5)
    if not rows:
        await _w(bot, user.id, "💿 No requester data yet.")
        return
    lines = ["💿 Top Requesters"]
    for r in rows[:5]:
        try:
            uname = str(r.get("username") or "unknown")[:18]
            cnt   = int(r.get("count") or 0)
            lines.append(f"{len(lines)}. @{uname} — {cnt} requests")
        except Exception:
            lines.append(f"{len(lines)}. (data error)")
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


def _prettify(raw: str) -> str:
    """Lower-case song_key → readable title (hyphens/underscores → spaces, title-case)."""
    text = re.sub(r"[-_]", " ", raw or "")
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text.title() if text else ""


def _trunc(s: str, maxlen: int) -> str:
    """Truncate string with ellipsis if it exceeds maxlen."""
    s = s.strip()
    return s if len(s) <= maxlen else s[:maxlen - 1] + "…"


def _fmt_song_line(i: int, row: dict, label: str) -> str:
    """Format one leaderboard song line — fully defensive, ≤ 72 chars."""
    try:
        raw_title  = str(row.get("title") or row.get("song_key") or "")
        raw_artist = str(row.get("artist") or "")
        n          = int(row.get("count") or 0)

        title  = _prettify(raw_title) or "Unknown Title"
        title  = _trunc(title, 28)

        artist = _prettify(raw_artist) or "Unknown"
        artist = _trunc(artist, 18)

        # singular vs plural  (1 like, 2 likes)
        word = label[:-1] if (n == 1 and label.endswith("s")) else label
        return f"{i}. {title} — {artist} • {n} {word}"
    except Exception:
        return f"{i}. (data error)"


async def _send_song_pages(
    bot: "BaseBot",
    uid: str,
    header: str,
    rows: list,
    label: str,
) -> None:
    """Paginate formatted song rows (3 per page), every page ≤ 249 chars."""
    items: list[str] = []
    for i, r in enumerate(rows, 1):
        try:
            items.append(_fmt_song_line(i, r, label))
        except Exception:
            items.append(f"{i}. (data error)")

    chunks    = [items[k:k + 3] for k in range(0, len(items), 3)]
    total_pgs = len(chunks)
    for pg, chunk in enumerate(chunks, 1):
        hdr  = f"{header} {pg}/{total_pgs}" if total_pgs > 1 else header
        body = "\n".join(chunk)
        msg  = f"{hdr}\n{body}"
        # Hard-trim if somehow still over 249 (shouldn't happen with our limits)
        await _w(bot, uid, msg[:249])
        if pg < total_pgs:
            await asyncio.sleep(0.25)


async def handle_topliked(bot: "BaseBot", user: "User", _args: list) -> None:
    """!topliked — top 5 songs by most likes."""
    rows = top_liked_songs(limit=5)
    if not rows:
        await _w(bot, user.id, "👍 No liked songs yet.")
        return
    await _send_song_pages(bot, user.id, "👍 Top Liked Songs", rows, "likes")


async def handle_topdisliked(bot: "BaseBot", user: "User", _args: list) -> None:
    """!topdisliked — top 5 songs by most dislikes."""
    rows = top_disliked_songs(limit=5)
    if not rows:
        await _w(bot, user.id, "👎 No disliked songs yet.")
        return
    await _send_song_pages(bot, user.id, "👎 Top Disliked Songs", rows, "dislikes")
