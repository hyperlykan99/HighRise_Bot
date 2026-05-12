"""
modules/weekly_lb.py
--------------------
/weeklylb /weeklyleaderboard — view current week's top players
/weeklyreset                 — archive week and announce winners
/weeklyrewards               — view configured reward payouts
/setweeklyreward             — configure a weekly reward payout
/weeklystatus                — show last/next reset info
"""
from __future__ import annotations
import datetime

import database as db
from modules.permissions import can_manage_economy


async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


_WEEK_CATEGORIES: dict[str, str] = {
    "fisher":  "Top Fisher",
    "racer":   "Top Race Winner",
    "tipper":  "Top Tipper",
    "earner":  "Top Coin Earner",
    "miner":   "Top Miner",
}

_VALID_REWARD_TYPES = ("coins", "gold_pending")


def _get_week_bounds() -> tuple[str, str]:
    today  = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


# ---------------------------------------------------------------------------
# /weeklylb  /weeklyleaderboard
# ---------------------------------------------------------------------------

async def handle_weeklylb(bot, user, args=None) -> None:
    """/weeklylb — current week's top players per category."""
    week_start, _week_end = _get_week_bounds()
    lines = [f"🏆 Weekly LB ({week_start})"]

    try:
        data = db.get_weekly_leaderboard_data(week_start)
        for cat, label in _WEEK_CATEGORIES.items():
            entry = data.get(cat)
            if entry:
                score = entry.get("score", "0")
                lines.append(f"{label}: @{entry['username']} — {score}")
            else:
                lines.append(f"{label}: —")
    except Exception as exc:
        lines.append(f"Error: {str(exc)[:60]}")

    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /weeklyreset
# ---------------------------------------------------------------------------

async def handle_weeklyreset(bot, user, args=None) -> None:
    """/weeklyreset — snapshot current winners and archive this week."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/owner only.")
        return

    week_start, week_end = _get_week_bounds()
    try:
        data           = db.get_weekly_leaderboard_data(week_start)
        saved          = 0
        announce_lines = ["🏆 Weekly Winners!"]

        for cat, label in _WEEK_CATEGORIES.items():
            entry = data.get(cat)
            if entry:
                db.save_weekly_snapshot(
                    week_start, week_end, cat, 1,
                    entry.get("user_id", ""),
                    entry.get("username", "?"),
                    str(entry.get("score", "")),
                )
                saved += 1
                announce_lines.append(f"{label}: @{entry['username']}")

        db.log_staff_action(
            user.id, user.username, "weekly_reset",
            "", "", f"week={week_start} saved={saved}"
        )

        if saved > 0:
            try:
                await bot.highrise.chat("\n".join(announce_lines)[:249])
            except Exception:
                pass

        await _w(bot, user.id, f"✅ Weekly reset done. {saved} winners archived.")

    except Exception as exc:
        await _w(bot, user.id, f"Reset error: {str(exc)[:100]}")


# ---------------------------------------------------------------------------
# /weeklyrewards
# ---------------------------------------------------------------------------

async def handle_weeklyrewards(bot, user, args=None) -> None:
    """/weeklyrewards — show configured weekly rewards."""
    rows = db.get_weekly_rewards()
    if not rows:
        await _w(bot, user.id, "🏆 No weekly rewards set.\nUse: /setweeklyreward")
        return
    lines = ["🏆 Weekly Rewards"]
    for r in rows:
        en = "✅" if r.get("enabled") else "❌"
        lines.append(
            f"{en} {r['category']} #{r['rank']}: "
            f"{r['reward_amount']} {r['reward_type']}"
        )
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /setweeklyreward <category> <rank> <coins|gold_pending> <amount>
# ---------------------------------------------------------------------------

async def handle_setweeklyreward(bot, user, args: list[str]) -> None:
    """/setweeklyreward <cat> <rank> <coins|gold_pending> <amount>"""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/owner only.")
        return

    if len(args) < 5:
        await _w(bot, user.id,
                 "Usage: !setweeklyreward <category> <rank> "
                 "<coins|gold_pending> <amount>")
        return

    category    = args[1].lower()
    if not args[2].isdigit():
        await _w(bot, user.id, "Rank must be a whole number.")
        return
    rank        = int(args[2])
    reward_type = args[3].lower()
    if reward_type not in _VALID_REWARD_TYPES:
        await _w(bot, user.id, "Type must be: coins or gold_pending")
        return
    if not args[4].isdigit():
        await _w(bot, user.id, "Amount must be a whole number.")
        return
    amount = int(args[4])

    db.set_weekly_reward(category, rank, reward_type, amount)
    await _w(bot, user.id,
             f"✅ Weekly reward set: {category} rank#{rank} = {amount} {reward_type}")


# ---------------------------------------------------------------------------
# /weeklystatus
# ---------------------------------------------------------------------------

async def handle_weeklystatus(bot, user, args=None) -> None:
    """/weeklystatus — current week bounds and last reset info."""
    week_start, week_end = _get_week_bounds()
    lines = [
        "🏆 Weekly Status",
        f"Current: {week_start} → {week_end}",
    ]
    try:
        snap = db.get_latest_weekly_snapshot()
        if snap:
            lines.append(f"Last Reset: {snap.get('created_at', '?')[:10]}")
            lines.append(f"Last Week: {snap.get('week_start', '?')}")
        else:
            lines.append("No reset on record.")
    except Exception:
        lines.append("No reset on record.")
    lines.append("Use !weeklyreset to archive.")
    await _w(bot, user.id, "\n".join(lines)[:249])
