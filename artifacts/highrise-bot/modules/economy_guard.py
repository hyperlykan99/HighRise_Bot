"""
modules/economy_guard.py
------------------------
Economy abuse protection and audit tools.

Staff commands:
  !economylock on     — pause risky economy payouts
  !economylock off    — resume normal economy
  !economylock status — show lock status + recent flags
  !audit suspicious   — list recent suspicious activity
  !playeraudit [user] — full audit for a specific player
  !payoutaudit        — recent large payouts
  !rewardaudit        — recent reward claim log

Detection:
  - Duplicate payout attempts
  - Rapid reward claims (>10 in 1 hour)
  - Negative balance events
  - Large manual balance edits
  - Repeated VIP purchase issues
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from highrise import BaseBot, User

import database as db
from modules.permissions import is_manager, is_admin


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Economy lock helpers
# ---------------------------------------------------------------------------

def is_economy_locked() -> bool:
    return db.get_room_setting("economy_lock", "0") == "1"


def _set_economy_lock(on: bool) -> None:
    db.set_room_setting("economy_lock", "1" if on else "0")


# ---------------------------------------------------------------------------
# Suspicious activity logging
# ---------------------------------------------------------------------------

def log_suspicious(user_id: str, username: str,
                    activity_type: str, details: str = "") -> None:
    """Log a suspicious activity event. Called from other modules."""
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO suspicious_activity
                 (user_id, username, activity_type, details, ts, resolved)
               VALUES (?,?,?,?,?,0)""",
            (user_id, username.lower(), activity_type, details[:200],
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _get_recent_suspicious(limit: int = 10) -> list[dict]:
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT * FROM suspicious_activity ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_player_suspicious(username: str, hours: int = 24) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT * FROM suspicious_activity "
            "WHERE LOWER(username)=? AND ts>=? ORDER BY id DESC LIMIT 20",
            (username.lower(), since),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_suspicious_count_today() -> int:
    since = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM suspicious_activity WHERE ts>=?", (since,)
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _get_reward_stats_today(user_id: str) -> dict:
    since = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        conn = db.get_connection()
        # Mission claims today
        mc = conn.execute(
            "SELECT COUNT(*) FROM mission_claims WHERE user_id=? AND claimed_at>=?",
            (user_id, since),
        ).fetchone()
        conn.close()
        return {"mission_claims": int(mc[0]) if mc else 0}
    except Exception:
        return {"mission_claims": 0}


def _get_gold_rain_wins_today(user_id: str) -> int:
    since = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM gold_rain_winners "
            "WHERE user_id=? AND won_at>=?",
            (user_id, since),
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_economylock(bot: BaseBot, user: User, args: list[str]) -> None:
    sub = args[1].lower() if len(args) >= 2 else "status"

    if sub == "on":
        if not is_manager(user.username):
            await _w(bot, user.id, "Manager+ only.")
            return
        _set_economy_lock(True)
        await _w(bot, user.id,
                 "🛡️ Economy Lock: ON\n"
                 "Risky payouts paused.\n"
                 "Staff commands still work.")
        try:
            await bot.highrise.chat(
                "🛡️ Economy protection mode active.\n"
                "Some rewards are temporarily paused."
            )
        except Exception:
            pass

    elif sub == "off":
        if not is_manager(user.username):
            await _w(bot, user.id, "Manager+ only.")
            return
        _set_economy_lock(False)
        await _w(bot, user.id, "🛡️ Economy Lock: OFF\nPayouts resumed.")

    else:
        locked = is_economy_locked()
        susp   = _get_suspicious_count_today()
        ts     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        await _w(bot, user.id,
                 f"🛡️ Economy Lock\n"
                 f"Status: {'ON' if locked else 'OFF'}\n"
                 f"Suspicious Logs Today: {susp}\n"
                 f"Last Check: {ts}")


async def handle_audit_suspicious(bot: BaseBot, user: User) -> None:
    if not is_manager(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    rows = _get_recent_suspicious(8)
    if not rows:
        await _w(bot, user.id, "✅ No suspicious activity logged.")
        return
    lines = [f"⚠️ Suspicious Activity (last {len(rows)})"]
    for r in rows[:5]:
        ts   = r["ts"][:16].replace("T", " ")
        lines.append(f"@{r['username']} — {r['activity_type']} — {ts}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_playeraudit(bot: BaseBot, user: User, args: list[str]) -> None:
    if not is_manager(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    target = args[1].lstrip("@") if len(args) >= 2 else user.username
    susp   = _get_player_suspicious(target)
    stats  = {"mission_claims": 0, "gold_rain_wins": 0}
    try:
        row = db.get_user_by_username(target)
        if row:
            rw = _get_reward_stats_today(row["user_id"])
            stats["mission_claims"] = rw.get("mission_claims", 0)
            stats["gold_rain_wins"] = _get_gold_rain_wins_today(row["user_id"])
    except Exception:
        pass
    flag = "YES ⚠️" if susp else "NO"
    await _w(bot, user.id,
             f"🧾 Player Audit: @{target}\n"
             f"Rewards Today: {stats['mission_claims']}\n"
             f"Gold Rain Wins Today: {stats['gold_rain_wins']}\n"
             f"Suspicious Flags (24h): {len(susp)}\n"
             f"Suspicious: {flag}")


async def handle_payoutaudit(bot: BaseBot, user: User) -> None:
    if not is_manager(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        conn = db.get_connection()
        rows = conn.execute(
            """SELECT u.username, ABS(t.amount) AS amt
               FROM transactions t
               JOIN users u ON u.user_id = t.user_id
               WHERE t.ts >= ? AND t.amount > 0
               ORDER BY t.amount DESC LIMIT 8""",
            (today,),
        ).fetchall()
        conn.close()
    except Exception:
        rows = []
    if not rows:
        await _w(bot, user.id, "No payout data today.")
        return
    lines = ["💰 Top Payouts Today"]
    for r in rows:
        lines.append(f"@{r['username']}: {r['amt']:,} coins")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_rewardaudit(bot: BaseBot, user: User) -> None:
    if not is_manager(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        conn = db.get_connection()
        rows = conn.execute(
            """SELECT username, mission_id, reward_type, reward_amount
               FROM mission_claims WHERE claimed_at >= ?
               ORDER BY id DESC LIMIT 10""",
            (today,),
        ).fetchall()
        conn.close()
    except Exception:
        rows = []
    if not rows:
        await _w(bot, user.id, "No reward claims logged today.")
        return
    lines = [f"📋 Reward Claims Today ({len(rows)})"]
    for r in rows[:8]:
        lines.append(
            f"@{r['username']}: {r['mission_id']} "
            f"→ {r['reward_amount']:,} {r['reward_type']}"
        )
    await _w(bot, user.id, "\n".join(lines)[:249])
