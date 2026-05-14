"""
modules/audit.py
----------------
Staff audit commands for the Mini Game Bot.

Commands:
  !audit                   — help menu
  !audit recent            — recent audit activity (economy, safety, mod)
  !audit @user             — brief player overview (shortcut)
  !audit user @user        — same as above
  !audit economy @user     — ledger / economy overview
  !audit commands @user    — command-usage overview (placeholder)
  !auditbank @user         — bank transfer summary
  !auditcasino @user       — casino stats

Permission: moderator+ (can_moderate)
All outbound messages are capped at 249 characters.
Currency: 🪙 ChillCoins  |  🎫 Luxe Tickets  (never bare "c")
"""

from highrise import BaseBot, User

import database as db
from modules.permissions import can_moderate, is_admin, is_owner


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


# Reserved words that must never be treated as usernames
_SUBCMDS = {"recent", "user", "economy", "commands", "help"}


def _resolve_target(args: list[str]) -> str | None:
    """
    Extract a target username from args, skipping subcommand keywords.
    Returns the cleaned username or None.
    """
    for part in args[1:]:
        clean = part.lstrip("@").strip().lower()
        if clean and clean not in _SUBCMDS:
            return part.lstrip("@").strip()
    return None


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

AUDIT_HELP = (
    "🔍 Audit Commands\n"
    "!audit recent\n"
    "!audit @user\n"
    "!audit user @user\n"
    "!audit economy @user\n"
    "!auditbank @user\n"
    "!auditcasino @user"
)


async def handle_audithelp(bot: BaseBot, user: User):
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    await _w(bot, user.id, AUDIT_HELP)


# ---------------------------------------------------------------------------
# !audit recent   — recent multi-source activity (admin+)
# ---------------------------------------------------------------------------

async def _handle_audit_recent(bot: BaseBot, user: User) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admins and owners only.")
        return

    lines = ["📋 Recent Audit"]
    try:
        conn = db.get_connection()

        # Last 4 economy transactions
        tx_rows = conn.execute(
            """SELECT username, currency, amount, direction, source
               FROM economy_transactions
               ORDER BY id DESC LIMIT 4"""
        ).fetchall()
        for r in tx_rows:
            sign  = "+" if r["direction"] == "credit" else "-"
            icon  = "🎫" if "luxe" in r["currency"].lower() or "ticket" in r["currency"].lower() else "🪙"
            lines.append(
                f"{r['source'][:14]} — @{r['username'][:12]}"
                f" — {sign}{r['amount']:,} {icon}"
            )

        # Last 2 safety alerts
        al_rows = conn.execute(
            """SELECT username, alert_type, blocked
               FROM safety_alerts
               ORDER BY id DESC LIMIT 2"""
        ).fetchall()
        for r in al_rows:
            tag = "BLOCKED" if r["blocked"] else "logged"
            lines.append(f"Safety — @{r['username'][:12]} — {r['alert_type']} [{tag}]")

        # Last 2 moderation actions
        mod_rows = conn.execute(
            """SELECT staff_name, target_name, action, reason
               FROM moderation_logs
               ORDER BY id DESC LIMIT 2"""
        ).fetchall()
        for r in mod_rows:
            lines.append(
                f"Mod:{r['action']} — @{r['target_name'][:12]}"
                f" — {r['reason'][:24]}"
            )

        conn.close()
    except Exception as exc:
        lines.append(f"Error reading logs: {exc!r}"[:60])

    if len(lines) == 1:
        lines.append("No recent audit events.")

    await _send_lines(bot, user.id, lines)


# ---------------------------------------------------------------------------
# !audit / !audit @user / !audit user @user  — brief summary (mod+)
# ---------------------------------------------------------------------------

async def handle_audit(bot: BaseBot, user: User, args: list[str]):
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return

    # Parse subcommand (args[1] if present)
    sub = args[1].lower().strip() if len(args) > 1 else ""

    # ── !audit (no args) → help menu ────────────────────────────────────────
    if not sub:
        await _w(bot, user.id, AUDIT_HELP)
        return

    # ── !audit recent ────────────────────────────────────────────────────────
    if sub == "recent":
        await _handle_audit_recent(bot, user)
        return

    # ── !audit economy @user ─────────────────────────────────────────────────
    if sub == "economy":
        target_name = _resolve_target(args[1:])  # skip "economy" keyword
        if not target_name:
            await _w(bot, user.id, "Usage: !audit economy @user")
            return
        await handle_auditeconomy(bot, user, ["audit", target_name])
        return

    # ── !audit commands @user ─────────────────────────────────────────────────
    if sub == "commands":
        target_name = _resolve_target(args[1:])
        if not target_name:
            await _w(bot, user.id, "Usage: !audit commands @user")
            return
        await _w(bot, user.id,
                 f"📋 Cmd Audit: @{target_name[:20]}\n"
                 f"Command history tracking not yet available.\n"
                 f"Use !audit economy @user for ledger data.")
        return

    # ── !audit user @user  or  !audit @user ──────────────────────────────────
    # At this point sub is either "user" or a raw username (starts with @ or text)
    if sub == "user":
        target_name = _resolve_target(args[1:])
    else:
        # sub is the username itself (e.g. !audit @player)
        target_name = sub.lstrip("@").strip() or None

    if not target_name:
        await _w(bot, user.id, AUDIT_HELP)
        return

    # Ensure user exists in DB
    db.resolve_or_create_user(target_name)

    data = db.get_audit_full(target_name)
    if not data:
        await _w(bot, user.id, f"@{target_name} has no audit data yet.")
        return

    blocked = "Yes" if data["bank_blocked"] else "No"
    net_str = _sign(data["casino_net"])
    msg = (
        f"🔍 Audit: @{data['username']}\n"
        f"Bal: {data['balance']:,} 🪙 | Lvl: {data['level']}\n"
        f"Earned: {data['total_earned']:,} 🪙\n"
        f"Sent: {data['total_sent']:,} 🪙 | Recv: {data['total_received']:,} 🪙\n"
        f"Casino Net: {net_str} 🪙\n"
        f"Blocked: {blocked} | Risk: {data['risk_count']}"
    )
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# !auditbank @user
# ---------------------------------------------------------------------------

async def handle_auditbank(bot: BaseBot, user: User, args: list[str]):
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    target_name = _resolve_target(args)
    if not target_name:
        await _w(bot, user.id, "Usage: !auditbank @user")
        return

    target = db.resolve_or_create_user(target_name)
    if not target:
        await _w(bot, user.id, "❌ Invalid username.")
        return

    uid   = target["user_id"]
    bus   = db.get_bank_user_stats(uid)
    daily = db.get_daily_sent_today(uid)

    blocked   = "Yes" if bus.get("bank_blocked") else "No"
    stats_msg = (
        f"🏦 Bank: @{target['username']}\n"
        f"Sent: {bus.get('total_sent', 0):,} 🪙\n"
        f"Recv: {bus.get('total_received', 0):,} 🪙\n"
        f"Daily Sent: {daily:,} 🪙\n"
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
            lines.append(f"→ @{tx['receiver_username']}: {tx['amount_sent']:,} 🪙")
        else:
            lines.append(f"← @{tx['sender_username']}: {tx['amount_received']:,} 🪙")
    await _send_lines(bot, user.id, lines)


# ---------------------------------------------------------------------------
# !auditcasino @user
# ---------------------------------------------------------------------------

async def handle_auditcasino(bot: BaseBot, user: User, args: list[str]):
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    target_name = _resolve_target(args)
    if not target_name:
        await _w(bot, user.id, "Usage: !auditcasino @user")
        return

    target = db.resolve_or_create_user(target_name)
    if not target:
        await _w(bot, user.id, "❌ Invalid username.")
        return

    c = db.get_audit_casino_data(target["user_id"])
    msg = (
        f"🎰 Casino: @{target['username']}\n"
        f"BJ: {c['bj_wins']}W {c['bj_losses']}L {c['bj_pushes']}P"
        f" Net:{_sign(c['bj_net'])} 🪙\n"
        f"RBJ: {c['rbj_wins']}W {c['rbj_losses']}L {c['rbj_pushes']}P"
        f" Net:{_sign(c['rbj_net'])} 🪙\n"
        f"Casino Net: {_sign(c['casino_net'])} 🪙\n"
        f"Today — BJ:{_sign(c['bj_daily'])} 🪙  RBJ:{_sign(c['rbj_daily'])} 🪙"
    )
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# !auditeconomy @user
# ---------------------------------------------------------------------------

async def handle_auditeconomy(bot: BaseBot, user: User, args: list[str]):
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    target_name = _resolve_target(args)
    if not target_name:
        await _w(bot, user.id, "Usage: !auditeconomy @user")
        return

    target = db.resolve_or_create_user(target_name)
    if not target:
        await _w(bot, user.id, f"Player '{target_name}' not found.")
        return

    uid  = target["user_id"]
    econ = db.get_audit_economy_data(uid)

    gain_str = f"{_sign(econ['best_gain'])} 🪙"    if econ["best_gain"]    else "none"
    loss_str = f"{_sign(econ['biggest_loss'])} 🪙" if econ["biggest_loss"] else "none"
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
        lines.append(f"{amt} 🪙 {reason} {ts}")
    await _send_lines(bot, user.id, lines)
