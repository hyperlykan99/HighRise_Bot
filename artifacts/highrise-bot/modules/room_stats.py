"""
modules/room_stats.py
---------------------
Room activity tracking and statistics.

Tracks joins/leaves per day.

Player commands:
  !roomstats   — today's activity summary
  !todaystats  — alias for !roomstats
  !weekstats   — this week's summary
  !peak        — peak player count info

Staff commands:
  !activehours — most active hours today (admin+)

Called from on_user_join / on_user_leave in main.py:
  record_room_join(user_id, username)
  record_room_leave(user_id, username)
"""
from __future__ import annotations
from datetime import datetime, timezone
from highrise import BaseBot, User

import database as db
from modules.permissions import is_admin


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Internal tracking
# ---------------------------------------------------------------------------

def _day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _week_key() -> str:
    iso = datetime.now(timezone.utc).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _hour_key() -> str:
    return str(datetime.now(timezone.utc).hour)


def record_room_join(user_id: str, username: str) -> None:
    """Call from on_user_join."""
    day = _day_key()
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO room_activity
                 (user_id, username, event_type, day_key, hour_key, ts)
               VALUES (?,?,?,?,?,?)""",
            (user_id, username.lower(), "join", day,
             _hour_key(), datetime.now(timezone.utc).isoformat()),
        )
        # upsert unique visitors
        conn.execute(
            """INSERT INTO room_daily_stats (day_key, unique_visitors, total_joins)
               VALUES (?,1,1)
               ON CONFLICT(day_key) DO UPDATE SET
                 total_joins = total_joins + 1""",
            (day,),
        )
        # track unique visitors separately
        conn.execute(
            """INSERT OR IGNORE INTO room_daily_visitors (day_key, user_id)
               VALUES (?,?)""",
            (day, user_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def record_room_leave(user_id: str, username: str) -> None:
    """Call from on_user_leave."""
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO room_activity
                 (user_id, username, event_type, day_key, hour_key, ts)
               VALUES (?,?,?,?,?,?)""",
            (user_id, username.lower(), "leave", _day_key(),
             _hour_key(), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def update_peak_players(current_count: int) -> None:
    """Call periodically or on each join with current room user count."""
    day = _day_key()
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO room_daily_stats (day_key, peak_players)
               VALUES (?,?)
               ON CONFLICT(day_key) DO UPDATE SET
                 peak_players = MAX(peak_players, ?)""",
            (day, current_count, current_count),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Stat helpers
# ---------------------------------------------------------------------------

def _get_today_stats() -> dict:
    day = _day_key()
    defaults = {
        "day_key": day, "unique_visitors": 0,
        "total_joins": 0, "peak_players": 0,
    }
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT * FROM room_daily_stats WHERE day_key=?", (day,)
        ).fetchone()
        # unique visitors from dedupe table
        uv = conn.execute(
            "SELECT COUNT(*) FROM room_daily_visitors WHERE day_key=?", (day,)
        ).fetchone()
        conn.close()
        d = dict(row) if row else defaults.copy()
        d["unique_visitors"] = uv[0] if uv else 0
        return d
    except Exception:
        return defaults


def _get_week_stats() -> dict:
    week = _week_key()
    try:
        conn = db.get_connection()
        row = conn.execute(
            """SELECT
                 SUM(total_joins)    AS total_joins,
                 MAX(peak_players)   AS peak_players
               FROM room_daily_stats
               WHERE day_key LIKE ?""",
            (week[:7] + "%",),
        ).fetchone()
        uv = conn.execute(
            """SELECT COUNT(DISTINCT user_id) FROM room_daily_visitors
               WHERE day_key LIKE ?""",
            (week[:7] + "%",),
        ).fetchone()
        conn.close()
        return {
            "total_joins":    int(row["total_joins"])  if row and row["total_joins"]  else 0,
            "peak_players":   int(row["peak_players"]) if row and row["peak_players"] else 0,
            "unique_visitors": int(uv[0]) if uv else 0,
        }
    except Exception:
        return {"total_joins": 0, "peak_players": 0, "unique_visitors": 0}


def _get_top_miner_today() -> str:
    day = _day_key()
    try:
        conn = db.get_connection()
        row = conn.execute(
            """SELECT username FROM mining_sessions
               WHERE date(started_at) = ?
               GROUP BY user_id, username
               ORDER BY COUNT(*) DESC LIMIT 1""",
            (day,),
        ).fetchone()
        conn.close()
        return row["username"] if row else "—"
    except Exception:
        return "—"


def _get_top_fisher_today() -> str:
    day = _day_key()
    try:
        conn = db.get_connection()
        row = conn.execute(
            """SELECT username FROM fishing_sessions
               WHERE date(started_at) = ?
               GROUP BY user_id, username
               ORDER BY COUNT(*) DESC LIMIT 1""",
            (day,),
        ).fetchone()
        conn.close()
        return row["username"] if row else "—"
    except Exception:
        return "—"


def _get_active_hours_today() -> list[tuple[int, int]]:
    """Return top 5 hours by join count for today."""
    day = _day_key()
    try:
        conn = db.get_connection()
        rows = conn.execute(
            """SELECT CAST(hour_key AS INTEGER) AS h, COUNT(*) AS cnt
               FROM room_activity
               WHERE day_key=? AND event_type='join'
               GROUP BY hour_key ORDER BY cnt DESC LIMIT 5""",
            (day,),
        ).fetchall()
        conn.close()
        return [(r["h"], r["cnt"]) for r in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_roomstats(bot: BaseBot, user: User) -> None:
    s = _get_today_stats()
    top_miner  = _get_top_miner_today()
    top_fisher = _get_top_fisher_today()
    await _w(bot, user.id,
             f"📊 Room Stats Today ({s['day_key']})\n"
             f"Unique Visitors: {s['unique_visitors']:,}\n"
             f"Peak Players: {s['peak_players']:,}\n"
             f"Total Joins: {s['total_joins']:,}\n"
             f"Top Miner: @{top_miner}\n"
             f"Top Fisher: @{top_fisher}")


async def handle_weekstats(bot: BaseBot, user: User) -> None:
    s = _get_week_stats()
    await _w(bot, user.id,
             f"📊 Room Stats This Week\n"
             f"Unique Visitors: {s['unique_visitors']:,}\n"
             f"Peak Players: {s['peak_players']:,}\n"
             f"Total Joins: {s['total_joins']:,}")


async def handle_peak(bot: BaseBot, user: User) -> None:
    s = _get_today_stats()
    sw = _get_week_stats()
    await _w(bot, user.id,
             f"📈 Peak Players\n"
             f"Today: {s['peak_players']:,}\n"
             f"This Week: {sw['peak_players']:,}")


async def handle_activehours(bot: BaseBot, user: User) -> None:
    if not is_admin(user.username):
        await _w(bot, user.id, "Admin+ only.")
        return
    hours = _get_active_hours_today()
    if not hours:
        await _w(bot, user.id, "No join activity logged today yet.")
        return
    lines = ["⏰ Most Active Hours Today (UTC)"]
    for h, cnt in hours:
        lines.append(f"  {h:02d}:00 — {cnt} joins")
    await _w(bot, user.id, "\n".join(lines)[:249])


# Alias for main.py compatibility
async def handle_todaystats(bot: BaseBot, user: User,
                             args: list[str]) -> None:
    await handle_roomstats(bot, user)


# ---------------------------------------------------------------------------
# Event hooks called from main.py on_user_join / on_user_leave
# ---------------------------------------------------------------------------

async def on_user_join_stats(user_id: str, username: str) -> None:
    """Record a join event in room_activity and update daily stats."""
    try:
        import database as _db
        import datetime as _dt
        now      = _dt.datetime.now(_dt.timezone.utc)
        day_key  = now.strftime("%Y-%m-%d")
        hour_key = now.strftime("%Y-%m-%d-%H")
        conn = _db.get_connection()
        conn.execute(
            "INSERT INTO room_activity(user_id, username, event_type, "
            "day_key, hour_key) VALUES(?,?,?,?,?)",
            (user_id, username, "join", day_key, hour_key),
        )
        conn.execute(
            "INSERT OR IGNORE INTO room_daily_visitors(day_key, user_id) "
            "VALUES(?,?)",
            (day_key, user_id),
        )
        conn.execute(
            "INSERT INTO room_daily_stats(day_key, unique_visitors, total_joins, "
            "peak_players) VALUES(?,1,1,0) "
            "ON CONFLICT(day_key) DO UPDATE SET "
            "total_joins=total_joins+1, "
            "unique_visitors=(SELECT COUNT(*) FROM room_daily_visitors "
            "WHERE day_key=excluded.day_key)",
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


async def on_user_leave_stats(user_id: str, username: str) -> None:
    """Record a leave event in room_activity."""
    try:
        import database as _db
        import datetime as _dt
        now      = _dt.datetime.now(_dt.timezone.utc)
        day_key  = now.strftime("%Y-%m-%d")
        hour_key = now.strftime("%Y-%m-%d-%H")
        conn = _db.get_connection()
        conn.execute(
            "INSERT INTO room_activity(user_id, username, event_type, "
            "day_key, hour_key) VALUES(?,?,?,?,?)",
            (user_id, username, "leave", day_key, hour_key),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
