"""
modules/audit.py
----------------
Staff audit commands for the Mini Game Bot.

Commands:
  /audithelp               — list audit commands
  /audit <username>        — brief player overview
  /auditbank <username>    — bank transfer summary
  /auditcasino <username>  — casino stats
  /auditeconomy <username> — ledger / economy overview

Permission: moderator+ (can_moderate)
All outbound messages are capped at 249 characters.
"""

from highrise import BaseBot, User

import database as db
from modules.permissions import can_moderate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str):
    await bot.highrise.send_whisper(uid, msg[:249])


def _sign(n: int) -> str:
    """Format an integer with a leading + for non-negative values."""
    return f"+{n:,}" if n >= 0 else f"{n:,}"


async def _send_lines(bot: BaseBot, uid: str, lines: list[str]):
    """
    Join lines and split into ≤249-char whispers so nothing is truncated.
    Each line is emitted as-is if it already fits; otherwise it's split further.
    """
    chunk = ""
    for line in lines:
        candidate = (chunk + "\n" + line).lstrip("\n") if chunk else line
        if len(candidate) > 249:
            if chunk:
                await _w(bot, uid, chunk)
            chunk = line
        else:
            chunk = candidate
    if chunk:
        await _w(bot, uid, chunk)


def _resolve(args: list[str], bot_user_id: str) -> str | None:
    """Return cleaned target username from args, or None."""
    if len(args) < 2:
        return None
    return args[1].lstrip("@").strip()


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

AUDIT_HELP = (
    "🔍 Audit Commands\n"
    "/audit <user>\n"
    "/auditbank <user>\n"
    "/auditcasino <user>\n"
    "/auditeconomy <user>"
)


async def handle_audithelp(bot: BaseBot, user: User):
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    await _w(bot, user.id, AUDIT_HELP)


# ---------------------------------------------------------------------------
# /audit <username>  — brief summary
# ---------------------------------------------------------------------------

async def handle_audit(bot: BaseBot, user: User, args: list[str]):
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    target_name = _resolve(args, user.id)
    if not target_name:
        await _w(bot, user.id, "Usage: /audit <username>")
        return

    data = db.get_audit_full(target_name)
    if not data:
        await _w(bot, user.id, f"Player '{target_name}' not found.")
        return

    blocked  = "Yes" if data["bank_blocked"] else "No"
    net_str  = _sign(data["casino_net"])
    msg = (
        f"🔍 Audit: @{data['username']}\n"
        f"Bal: {data['balance']:,}c | Lvl: {data['level']}\n"
        f"Earned: {data['total_earned']:,}c\n"
        f"Sent: {data['total_sent']:,}c | Recv: {data['total_received']:,}c\n"
        f"Casino Net: {net_str}c\n"
        f"Blocked: {blocked} | Risk: {data['risk_count']}"
    )
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /auditbank <username>
# ---------------------------------------------------------------------------

async def handle_auditbank(bot: BaseBot, user: User, args: list[str]):
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    target_name = _resolve(args, user.id)
    if not target_name:
        await _w(bot, user.id, "Usage: /auditbank <username>")
        return

    target = db.get_user_by_username(target_name)
    if not target:
        await _w(bot, user.id, f"Player '{target_name}' not found.")
        return

    uid = target["user_id"]
    bus = db.get_bank_user_stats(uid)
    daily = db.get_daily_sent_today(uid)

    blocked = "Yes" if bus.get("bank_blocked") else "No"
    stats_msg = (
        f"🏦 Bank: @{target['username']}\n"
        f"Sent: {bus.get('total_sent', 0):,}c\n"
        f"Recv: {bus.get('total_received', 0):,}c\n"
        f"Daily Sent: {daily:,}c\n"
        f"Suspicious: {bus.get('suspicious_transfer_count', 0)}\n"
        f"Blocked: {blocked}"
    )
    await _w(bot, user.id, stats_msg)

    txs = db.get_transactions_for(uid, limit=5)
    if not txs:
        await _w(bot, user.id, "No recent transfers.")
        return

    lines = ["Recent Transfers:"]
    for tx in txs:
        if tx["sender_id"] == uid:
            lines.append(f"→ @{tx['receiver_username']}: {tx['amount_sent']:,}c")
        else:
            lines.append(f"← @{tx['sender_username']}: {tx['amount_received']:,}c")
    await _send_lines(bot, user.id, lines)


# ---------------------------------------------------------------------------
# /auditcasino <username>
# ---------------------------------------------------------------------------

async def handle_auditcasino(bot: BaseBot, user: User, args: list[str]):
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    target_name = _resolve(args, user.id)
    if not target_name:
        await _w(bot, user.id, "Usage: /auditcasino <username>")
        return

    target = db.get_user_by_username(target_name)
    if not target:
        await _w(bot, user.id, f"Player '{target_name}' not found.")
        return

    c = db.get_audit_casino_data(target["user_id"])
    msg = (
        f"🎰 Casino: @{target['username']}\n"
        f"BJ: {c['bj_wins']}W {c['bj_losses']}L {c['bj_pushes']}P"
        f" Net:{_sign(c['bj_net'])}c\n"
        f"RBJ: {c['rbj_wins']}W {c['rbj_losses']}L {c['rbj_pushes']}P"
        f" Net:{_sign(c['rbj_net'])}c\n"
        f"Casino Net: {_sign(c['casino_net'])}c\n"
        f"Today — BJ:{_sign(c['bj_daily'])}c  RBJ:{_sign(c['rbj_daily'])}c"
    )
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /auditeconomy <username>
# ---------------------------------------------------------------------------

async def handle_auditeconomy(bot: BaseBot, user: User, args: list[str]):
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    target_name = _resolve(args, user.id)
    if not target_name:
        await _w(bot, user.id, "Usage: /auditeconomy <username>")
        return

    target = db.get_user_by_username(target_name)
    if not target:
        await _w(bot, user.id, f"Player '{target_name}' not found.")
        return

    uid  = target["user_id"]
    econ = db.get_audit_economy_data(uid)

    gain_str = f"{_sign(econ['best_gain'])}c"    if econ["best_gain"]    else "none"
    loss_str = f"{_sign(econ['biggest_loss'])}c" if econ["biggest_loss"] else "none"
    summary = (
        f"💹 Economy: @{target['username']}\n"
        f"Best Gain: {gain_str}\n"
        f"Biggest Loss: {loss_str}"
    )
    await _w(bot, user.id, summary)

    recent = econ["recent"]
    if not recent:
        await _w(bot, user.id, "No ledger entries.")
        return

    lines = ["Recent Ledger:"]
    for e in recent:
        amt    = _sign(e["change_amount"])
        reason = e["reason"][:16]
        ts     = e["timestamp"][:10]
        lines.append(f"{amt}c {reason} {ts}")
    await _send_lines(bot, user.id, lines)
