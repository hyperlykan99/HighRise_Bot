"""
modules/missions.py
-------------------
Daily and weekly mission system.

Player commands:
  !missions      — see all missions + progress
  !daily         — daily missions only
  !weekly        — weekly missions only
  !claimmission  — claim any completed unclaimed mission
  !missionstatus — compact summary

Missions reset:
  Daily  — every day at UTC midnight (keyed by date string)
  Weekly — every Monday ISO week (keyed by year-Wnn)

Reward types: coins | xp | tickets (raffle)
"""
from __future__ import annotations
import database as db
from datetime import datetime, timezone
from highrise import BaseBot, User


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Period key helpers
# ---------------------------------------------------------------------------

def _day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _week_key() -> str:
    iso = datetime.now(timezone.utc).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


# ---------------------------------------------------------------------------
# Mission definitions
# ---------------------------------------------------------------------------

DAILY_MISSIONS: list[dict] = [
    {"id": "mine_10",   "label": "Mine 10 times",       "goal": 10, "reward_type": "coins",   "reward": 300},
    {"id": "fish_5",    "label": "Fish 5 times",         "goal": 5,  "reward_type": "coins",   "reward": 250},
    {"id": "bj_3",      "label": "Play 3 BJ hands",      "goal": 3,  "reward_type": "coins",   "reward": 200},
    {"id": "visit_1",   "label": "Visit the room",        "goal": 1,  "reward_type": "xp",      "reward": 50},
    {"id": "tip_once",  "label": "Send a tip/donate",     "goal": 1,  "reward_type": "tickets", "reward": 1},
]

WEEKLY_MISSIONS: list[dict] = [
    {"id": "visit_3",    "label": "Visit 3 days this week", "goal": 3,  "reward_type": "coins",   "reward": 1000},
    {"id": "mine_50",    "label": "Mine 50 times",           "goal": 50, "reward_type": "coins",   "reward": 800},
    {"id": "fish_30",    "label": "Fish 30 times",           "goal": 30, "reward_type": "coins",   "reward": 700},
    {"id": "raffle_once","label": "Enter a raffle",          "goal": 1,  "reward_type": "tickets", "reward": 2},
    {"id": "event_join", "label": "Join one event",          "goal": 1,  "reward_type": "coins",   "reward": 500},
]


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def _get_progress(user_id: str, mission_id: str, period_key: str) -> int:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT progress FROM mission_progress "
            "WHERE user_id=? AND mission_id=? AND period_key=?",
            (user_id, mission_id, period_key),
        ).fetchone()
        conn.close()
        return int(row["progress"]) if row else 0
    except Exception:
        return 0


def _is_claimed(user_id: str, mission_id: str, period_key: str) -> bool:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT id FROM mission_claims "
            "WHERE user_id=? AND mission_id=? AND period_key=?",
            (user_id, mission_id, period_key),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def add_mission_progress(user_id: str, username: str,
                          mission_id: str, amount: int = 1) -> None:
    """Called externally (mining, fishing, BJ, etc.) to increment mission progress."""
    try:
        conn = db.get_connection()
        for key in (_day_key(), _week_key()):
            conn.execute(
                """INSERT INTO mission_progress
                     (user_id, username, mission_id, period_key, progress)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(user_id, mission_id, period_key)
                   DO UPDATE SET progress = progress + ?,
                                 username = excluded.username""",
                (user_id, username.lower(), mission_id, key, amount, amount),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_all_mission_progress(user_id: str) -> dict[str, int]:
    """Return {mission_id: progress} for both daily and weekly periods."""
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT mission_id, SUM(progress) FROM mission_progress "
            "WHERE user_id=? AND period_key IN (?,?) "
            "GROUP BY mission_id",
            (user_id, _day_key(), _week_key()),
        ).fetchall()
        conn.close()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def _grant_tickets(user_id: str, username: str, amount: int) -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO raffle_tickets (user_id, username, amount)
               VALUES (?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET amount = amount + ?""",
            (user_id, username.lower(), amount, amount),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_ticket_balance(user_id: str) -> int:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT amount FROM raffle_tickets WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        return int(row["amount"]) if row else 0
    except Exception:
        return 0


def _claim_reward(user_id: str, username: str, mission_id: str,
                   period_key: str, reward_type: str, reward: int) -> bool:
    """Insert claim record and grant reward. Returns True on success."""
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT OR IGNORE INTO mission_claims
                 (user_id, username, mission_id, period_key,
                  reward_type, reward_amount, claimed_at)
               VALUES (?,?,?,?,?,?,?)""",
            (user_id, username.lower(), mission_id, period_key,
             reward_type, reward, datetime.now(timezone.utc).isoformat()),
        )
        changed = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
        conn.close()
        if not changed:
            return False
    except Exception:
        return False
    try:
        if reward_type == "coins":
            db.ensure_user(user_id, username)
            db.add_balance(user_id, reward)
        elif reward_type == "xp":
            db.ensure_user(user_id, username)
            db.add_xp(user_id, reward)
        elif reward_type == "tickets":
            _grant_tickets(user_id, username, reward)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_missions(bot: BaseBot, user: User) -> None:
    day_key  = _day_key()
    week_key = _week_key()
    lines = ["🎯 Missions | !claimmission to claim"]
    lines.append("Daily:")
    for m in DAILY_MISSIONS:
        prog = _get_progress(user.id, m["id"], day_key)
        g    = m["goal"]
        tag  = " (claimed)" if _is_claimed(user.id, m["id"], day_key) else \
               " ✅" if prog >= g else ""
        lines.append(f"  {m['label']}: {min(prog, g)}/{g}{tag}")
    await _w(bot, user.id, "\n".join(lines)[:249])
    lines2 = ["Weekly:"]
    for m in WEEKLY_MISSIONS:
        prog = _get_progress(user.id, m["id"], week_key)
        g    = m["goal"]
        tag  = " (claimed)" if _is_claimed(user.id, m["id"], week_key) else \
               " ✅" if prog >= g else ""
        lines2.append(f"  {m['label']}: {min(prog, g)}/{g}{tag}")
    await _w(bot, user.id, "\n".join(lines2)[:249])


async def handle_daily(bot: BaseBot, user: User) -> None:
    day_key = _day_key()
    lines   = ["🌅 Daily Missions (resets midnight UTC)"]
    for m in DAILY_MISSIONS:
        prog = _get_progress(user.id, m["id"], day_key)
        g    = m["goal"]
        clm  = _is_claimed(user.id, m["id"], day_key)
        if clm:
            tag = " ✅ claimed"
        elif prog >= g:
            tag = " ✅ use !claimmission"
        else:
            tag = ""
        lines.append(f"  {m['label']}: {min(prog, g)}/{g}{tag}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_weekly(bot: BaseBot, user: User) -> None:
    week_key = _week_key()
    lines    = ["📅 Weekly Missions (resets Monday UTC)"]
    for m in WEEKLY_MISSIONS:
        prog = _get_progress(user.id, m["id"], week_key)
        g    = m["goal"]
        clm  = _is_claimed(user.id, m["id"], week_key)
        if clm:
            tag = " ✅ claimed"
        elif prog >= g:
            tag = " ✅ use !claimmission"
        else:
            tag = ""
        lines.append(f"  {m['label']}: {min(prog, g)}/{g}{tag}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_claimmission(bot: BaseBot, user: User) -> None:
    day_key  = _day_key()
    week_key = _week_key()
    claimed_count = 0
    for period_key, missions in ((day_key, DAILY_MISSIONS),
                                  (week_key, WEEKLY_MISSIONS)):
        for m in missions:
            prog = _get_progress(user.id, m["id"], period_key)
            if prog < m["goal"]:
                continue
            if _is_claimed(user.id, m["id"], period_key):
                continue
            ok = _claim_reward(
                user.id, user.username, m["id"], period_key,
                m["reward_type"], m["reward"],
            )
            if ok:
                rtype = m["reward_type"]
                amt   = m["reward"]
                if rtype == "tickets":
                    label = f"{amt} raffle ticket(s)"
                else:
                    label = f"{amt:,} {rtype}"
                await _w(bot, user.id,
                         f"✅ Mission Reward Claimed\n"
                         f"{m['label']}\n"
                         f"Reward: {label}")
                claimed_count += 1
    if not claimed_count:
        await _w(bot, user.id,
                 "No completed missions ready to claim.\n"
                 "Check !missions for progress.")


async def handle_missionstatus(bot: BaseBot, user: User) -> None:
    day_key  = _day_key()
    week_key = _week_key()
    d_done  = sum(1 for m in DAILY_MISSIONS
                  if _get_progress(user.id, m["id"], day_key) >= m["goal"])
    d_clm   = sum(1 for m in DAILY_MISSIONS
                  if _is_claimed(user.id, m["id"], day_key))
    w_done  = sum(1 for m in WEEKLY_MISSIONS
                  if _get_progress(user.id, m["id"], week_key) >= m["goal"])
    w_clm   = sum(1 for m in WEEKLY_MISSIONS
                  if _is_claimed(user.id, m["id"], week_key))
    tickets = get_ticket_balance(user.id)
    await _w(bot, user.id,
             f"🎯 Mission Status @{user.username}\n"
             f"Daily: {d_done}/{len(DAILY_MISSIONS)} done, "
             f"{d_clm} claimed\n"
             f"Weekly: {w_done}/{len(WEEKLY_MISSIONS)} done, "
             f"{w_clm} claimed\n"
             f"Raffle Tickets: {tickets}")


# ---------------------------------------------------------------------------
# Aliases for main.py import compatibility
# ---------------------------------------------------------------------------

async def handle_daily_missions(bot: BaseBot, user: User,
                                 args: list[str]) -> None:
    await handle_daily(bot, user)


async def handle_weekly_missions(bot: BaseBot, user: User,
                                  args: list[str]) -> None:
    await handle_weekly(bot, user)


# ---------------------------------------------------------------------------
# Event hooks called from main.py on_user_join / mine / fish
# ---------------------------------------------------------------------------

async def on_user_join_missions(user_id: str, username: str) -> None:
    """Refresh mission cache or check daily reset on join."""
    try:
        import database as _db
        _db.ensure_user(user_id, username)
    except Exception:
        pass


async def on_mine_missions(user_id: str, username: str,
                            rarity: str = "", item_name: str = "") -> None:
    """Track mining activity for any active mine-based missions."""
    try:
        day_key  = _period_key("daily")
        week_key = _period_key("weekly")
        for m in DAILY_MISSIONS:
            if m.get("type") == "mine":
                _increment_progress(user_id, m["id"], day_key)
        for m in WEEKLY_MISSIONS:
            if m.get("type") == "mine":
                _increment_progress(user_id, m["id"], week_key)
    except Exception:
        pass


async def on_fish_missions(user_id: str, username: str,
                            rarity: str = "", item_name: str = "") -> None:
    """Track fishing activity for any active fish-based missions."""
    try:
        day_key  = _period_key("daily")
        week_key = _period_key("weekly")
        for m in DAILY_MISSIONS:
            if m.get("type") == "fish":
                _increment_progress(user_id, m["id"], day_key)
        for m in WEEKLY_MISSIONS:
            if m.get("type") == "fish":
                _increment_progress(user_id, m["id"], week_key)
    except Exception:
        pass
