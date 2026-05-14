"""
modules/ai_personalized_guidance.py — Personalized player progress guidance (3.3B).

Fetches safe player context (coins, level, tickets, missions, daily)
and returns 2–4 prioritized action recommendations.
All results contain private data → always whispered in smart mode.
"""
from __future__ import annotations

import datetime

import database as db


def _coins(user_id: str) -> int:
    try:
        return int(db.get_balance(user_id) or 0)
    except Exception:
        return 0


def _level(user_id: str) -> int:
    try:
        p = db.get_profile(user_id)
        return int(p.get("level", 1)) if p else 1
    except Exception:
        return 1


def _luxe_tickets(user_id: str) -> int:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT balance FROM luxe_tickets WHERE user_id=? LIMIT 1",
            (user_id,),
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _daily_claimed(user_id: str) -> bool:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT last_daily FROM players WHERE user_id=? LIMIT 1",
            (user_id,),
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return False
        last = str(row[0])[:10]
        return last == datetime.date.today().isoformat()
    except Exception:
        return False


def _open_daily_missions(user_id: str) -> int:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM missions "
            "WHERE user_id=? AND completed=0 AND period='daily'",
            (user_id,),
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def get_personalized_guidance(user_id: str, username: str) -> str:
    """
    Build a personalized 2-4 action recommendation whisper for the player.
    Always contains private data (contains_private=True).
    """
    coins   = _coins(user_id)
    level   = _level(user_id)
    tickets = _luxe_tickets(user_id)
    daily   = _daily_claimed(user_id)
    missions = _open_daily_missions(user_id)

    header = f"👤 Lv.{level} | 🪙{coins:,} | 🎫{tickets}"
    parts: list[str] = []

    if not daily:
        parts.append("🎁 !daily — claim your daily reward")
    if missions > 0:
        parts.append(f"📋 {missions} mission(s) open — !missions")
    if coins < 500:
        parts.append("⛏️ Mine for quick coins — !mine")
    elif coins < 3000:
        parts.append("⛏️ Keep mining or fishing — !mine / !fish")
    else:
        parts.append("🏆 Check !events for bonus rewards")
    if tickets < 5:
        parts.append("🎫 Earn Luxe Tickets from events — !events")
    elif tickets >= 10:
        parts.append(f"🎫 You have {tickets} tickets — browse !luxeshop")

    tips = parts[:4]
    body = "\n".join(tips) if tips else "Try !daily, !mine, !fish, or !events!"
    result = f"{header}\n{body}"
    return result[:249]


def summarize_progress(user_id: str) -> str:
    """One-line snapshot of player progress."""
    coins   = _coins(user_id)
    level   = _level(user_id)
    tickets = _luxe_tickets(user_id)
    missions = _open_daily_missions(user_id)
    daily   = _daily_claimed(user_id)

    daily_str    = "✅" if daily else "❌"
    mission_str  = f"{missions} open" if missions else "all done"
    msg = (
        f"📊 Your Progress:\n"
        f"Level {level} | 🪙{coins:,} ChillCoins | 🎫{tickets} Luxe Tickets\n"
        f"Daily {daily_str} | Missions: {mission_str}"
    )
    return msg[:249]
