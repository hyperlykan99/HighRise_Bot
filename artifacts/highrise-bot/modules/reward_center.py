"""
modules/reward_center.py
------------------------
/rewardpending  /pendingrewards — list pending manual gold rewards
/rewardlogs                     — recent reward history
/markrewardpaid <id|@user>      — mark a reward as paid_manual
/economyreport                  — anti-inflation economy snapshot

All BankerBot-owned; manager+ permission.
"""
from __future__ import annotations

import database as db
from modules.permissions import can_manage_economy


async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


# ---------------------------------------------------------------------------
# /rewardpending  /pendingrewards
# ---------------------------------------------------------------------------

async def handle_rewardpending(bot, user, args=None) -> None:
    """/rewardpending — list pending manual gold rewards."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/owner only.")
        return

    rows = db.get_pending_race_winners_for_banker(limit=10)
    if not rows:
        await _w(bot, user.id, "💰 No pending rewards. All clear!")
        return

    lines = [f"💰 Pending ({len(rows)})"]
    for r in rows:
        amt = r.get("gold_amount", 0)
        tv  = r.get("target_value") or r.get("race_target") or "?"
        lines.append(f"#{r['id']} @{r['username']} — {tv} — {amt:g}g")
    lines.append("!markrewardpaid [id] to confirm.")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /rewardlogs
# ---------------------------------------------------------------------------

async def handle_rewardlogs(bot, user, args=None) -> None:
    """/rewardlogs — recent reward history (last 10)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/owner only.")
        return

    rows = db.get_recent_race_winners(limit=10)
    if not rows:
        await _w(bot, user.id, "💰 No reward logs found.")
        return

    lines = ["💰 Reward Logs"]
    for r in rows:
        status = r.get("payout_status", "?")[:14]
        amt    = r.get("gold_amount", 0)
        lines.append(f"@{r['username']} {amt:g}g [{status}]")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /markrewardpaid <id|@username>
# ---------------------------------------------------------------------------

async def handle_markrewardpaid(bot, user, args: list[str]) -> None:
    """/markrewardpaid <id|@username> — mark reward(s) as paid."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/owner only.")
        return

    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: !markrewardpaid <id> or /markrewardpaid @username")
        return

    target = args[1].lstrip("@").strip()
    marked = 0

    if target.isdigit():
        winner_id = int(target)
        ok = db.mark_race_winner_paid_manual(winner_id, user.username)
        if ok:
            marked = 1
            db.log_staff_action(
                user.id, user.username, "mark_reward_paid",
                "", "", f"race_winner_id={winner_id}"
            )
        else:
            await _w(bot, user.id, f"Reward #{winner_id} not found or already paid.")
            return
    else:
        rows = db.get_pending_race_winners_by_username(target, limit=10)
        for r in rows:
            db.mark_race_winner_paid_manual(r["id"], user.username)
            marked += 1
        if marked > 0:
            db.log_staff_action(
                user.id, user.username, "mark_reward_paid",
                "", target, f"marked={marked}"
            )

    if marked == 0:
        await _w(bot, user.id, f"No pending rewards found for {target}.")
    else:
        await _w(bot, user.id, f"✅ {marked} reward(s) marked as paid.")


# ---------------------------------------------------------------------------
# /economyreport
# ---------------------------------------------------------------------------

async def handle_economyreport(bot, user, args=None) -> None:
    """/economyreport — daily economy health snapshot."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/owner only.")
        return

    try:
        report = db.get_economy_report_today()
    except Exception as exc:
        await _w(bot, user.id, f"Report error: {str(exc)[:80]}")
        return

    fish_earned = report.get("fish_earned_today", 0)
    gold_conv   = report.get("gold_converted_today", 0)
    race_gold   = report.get("race_gold_today", 0)
    p2p_count   = report.get("p2p_transfers_today", 0)
    pending_ct  = report.get("pending_gold_count", 0)

    warn = ""
    if fish_earned > 5_000_000:
        warn = "\n⚠️ High fish payouts today."
    if gold_conv > 50:
        warn += f"\n⚠️ {gold_conv:g}g converted."

    msg = (
        f"📈 Economy Report\n"
        f"Fish Payouts: {_fmt(fish_earned)}c\n"
        f"Gold Converted: {gold_conv:g}g\n"
        f"Race Gold: {race_gold:g}g\n"
        f"P2P Transfers: {p2p_count}\n"
        f"Pending Gold Rewards: {pending_ct}"
        + warn
    )
    await _w(bot, user.id, msg[:249])
