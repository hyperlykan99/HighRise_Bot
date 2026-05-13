"""
modules/leaderboards.py
-----------------------
Player-facing leaderboard commands.

Commands (all whispered, all ≤ 249 chars):
  !toprich    — richest players by coin balance
  !topminers  — top miners by mining XP
  !topfishers — top fishers by catch count
  !topstreaks — top daily streak holders
"""
from __future__ import annotations

from highrise import BaseBot, User

import database as db


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg)


def _fc(n: int) -> str:
    return f"{n:,}"


# ---------------------------------------------------------------------------
# !toprich
# ---------------------------------------------------------------------------

async def handle_toprich(bot: BaseBot, user: User) -> None:
    """!toprich — top players by coin balance."""
    rows = db.get_top_balances(limit=5)
    if not rows:
        await _w(bot, user.id, "💰 Richest Players\nNo coin data yet. Play games to earn!")
        return
    lines = ["💰 Richest Players"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. @{r['username']} — {_fc(r['balance'])}c")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !topminers
# ---------------------------------------------------------------------------

async def handle_topminers(bot: BaseBot, user: User) -> None:
    """!topminers — top players by mining XP."""
    rows = db.get_top_miners(limit=5)
    if not rows:
        await _w(bot, user.id, "⛏️ Top Miners\nNo mining data yet. Type !mine to start!")
        return
    lines = ["⛏️ Top Miners"]
    for i, r in enumerate(rows, 1):
        mxp = r.get("mining_xp", 0)
        lv  = r.get("mining_level", 1)
        lines.append(f"{i}. @{r['username']} — Lv {lv} | {_fc(mxp)} MXP")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !topfishers
# ---------------------------------------------------------------------------

async def handle_topfishers(bot: BaseBot, user: User) -> None:
    """!topfishers — top players by fishing catch count."""
    try:
        bot_filter = db._get_bot_name_filter()
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT username, total_catches, fishing_level "
            "FROM fish_profiles ORDER BY total_catches DESC LIMIT ?",
            (30,),
        ).fetchall()
        conn.close()
        filtered = [
            dict(r)
            for r in rows
            if r["username"].lower() not in bot_filter
        ][:5]
    except Exception:
        filtered = []
    if not filtered:
        await _w(bot, user.id, "🎣 Top Fishers\nNo fishing data yet. Type !fish to start!")
        return
    lines = ["🎣 Top Fishers"]
    for i, r in enumerate(filtered, 1):
        catches = r.get("total_catches", 0)
        lv      = r.get("fishing_level", 1)
        lines.append(f"{i}. @{r['username']} — Lv {lv} | {_fc(catches)} fish")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !topstreaks
# ---------------------------------------------------------------------------

async def handle_topstreaks(bot: BaseBot, user: User) -> None:
    """!topstreaks — top players by best daily streak."""
    rows = db.get_top_streaks(limit=5)
    if not rows:
        await _w(bot, user.id, "🔥 Top Daily Streaks\nNo streaks yet. Type !daily to start!")
        return
    lines = ["🔥 Top Daily Streaks"]
    for i, r in enumerate(rows, 1):
        best = r.get("best_streak") or r.get("streak", 0)
        lines.append(f"{i}. @{r['username']} — {best}-day best streak")
    await _w(bot, user.id, "\n".join(lines)[:249])
