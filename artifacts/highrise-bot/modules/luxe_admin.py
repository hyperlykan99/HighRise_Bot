"""modules/luxe_admin.py — Owner/admin Luxe Ticket management commands (3.4A)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot, User

import database as db
from modules.permissions import is_owner, can_manage_economy
from modules.luxe import (
    get_luxe_balance,
    add_luxe_balance,
    deduct_luxe_balance,
    log_luxe_transaction,
)


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, str(msg)[:249])


def _resolve_target(username: str) -> dict | None:
    name = username.lstrip("@").strip().lower()
    return db.get_user_by_username(name)


def _get_recent_ticket_logs(user_id: str, limit: int = 5) -> list[dict]:
    conn = db.get_connection()
    rows = conn.execute(
        """SELECT type, amount, details, created_at
           FROM premium_transactions
           WHERE user_id=?
             AND type IN ('gold_tip_reward','owner_addtickets','owner_removetickets',
                          'owner_settickets','admin_sendtickets',
                          'admin_grant','admin_revoke')
           ORDER BY id DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# !addtickets @user amount   — owner only
# ---------------------------------------------------------------------------

async def handle_addtickets(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !addtickets @user amount")
        return
    target_name = args[1].lstrip("@").strip()
    try:
        amount = int(args[2])
        if amount < 1:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "⚠️ Amount must be a positive integer.")
        return
    rec = _resolve_target(target_name)
    if not rec:
        await _w(bot, user.id, f"@{target_name} not found in DB.")
        return
    bal_before = get_luxe_balance(rec["user_id"])
    new_bal = add_luxe_balance(rec["user_id"], rec["username"], amount)
    log_luxe_transaction(
        rec["user_id"], rec["username"],
        "owner_addtickets", amount, "luxe",
        f"by @{user.username} | before={bal_before} after={new_bal}",
    )
    print(f"[TICKET ADMIN] addtickets actor={user.username} target={rec['username']} "
          f"amount={amount} before={bal_before} after={new_bal}")
    await _w(bot, user.id,
             f"✅ Added {amount:,} 🎫 Luxe Tickets to @{rec['username']}.\n"
             f"New balance: {new_bal:,} 🎫")


# ---------------------------------------------------------------------------
# !removetickets @user amount   — owner only
# ---------------------------------------------------------------------------

async def handle_removetickets(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !removetickets @user amount")
        return
    target_name = args[1].lstrip("@").strip()
    try:
        amount = int(args[2])
        if amount < 1:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "⚠️ Amount must be a positive integer.")
        return
    rec = _resolve_target(target_name)
    if not rec:
        await _w(bot, user.id, f"@{target_name} not found in DB.")
        return
    bal_before = get_luxe_balance(rec["user_id"])
    actual = min(amount, bal_before)
    if actual > 0:
        deduct_luxe_balance(rec["user_id"], rec["username"], actual)
    new_bal = get_luxe_balance(rec["user_id"])
    log_luxe_transaction(
        rec["user_id"], rec["username"],
        "owner_removetickets", actual, "luxe",
        f"by @{user.username} | before={bal_before} after={new_bal}",
    )
    print(f"[TICKET ADMIN] removetickets actor={user.username} target={rec['username']} "
          f"amount={actual} before={bal_before} after={new_bal}")
    await _w(bot, user.id,
             f"✅ Removed {actual:,} 🎫 from @{rec['username']}.\n"
             f"New balance: {new_bal:,} 🎫")


# ---------------------------------------------------------------------------
# !settickets @user amount   — owner only
# ---------------------------------------------------------------------------

async def handle_settickets(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !settickets @user amount")
        return
    target_name = args[1].lstrip("@").strip()
    try:
        amount = int(args[2])
        if amount < 0:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "⚠️ Amount must be a non-negative integer.")
        return
    rec = _resolve_target(target_name)
    if not rec:
        await _w(bot, user.id, f"@{target_name} not found in DB.")
        return
    bal_before = get_luxe_balance(rec["user_id"])
    if amount >= bal_before:
        diff = amount - bal_before
        if diff > 0:
            add_luxe_balance(rec["user_id"], rec["username"], diff)
    else:
        diff = bal_before - amount
        deduct_luxe_balance(rec["user_id"], rec["username"], diff)
    new_bal = get_luxe_balance(rec["user_id"])
    log_luxe_transaction(
        rec["user_id"], rec["username"],
        "owner_settickets", amount, "luxe",
        f"by @{user.username} | before={bal_before} after={new_bal}",
    )
    print(f"[TICKET ADMIN] settickets actor={user.username} target={rec['username']} "
          f"set_to={amount} before={bal_before} after={new_bal}")
    await _w(bot, user.id,
             f"✅ Set @{rec['username']} Luxe Tickets to {new_bal:,} 🎫.")


# ---------------------------------------------------------------------------
# !sendtickets @user amount   — owner/admin
# ---------------------------------------------------------------------------

async def handle_sendtickets(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not (is_owner(user.username) or can_manage_economy(user.username)):
        await _w(bot, user.id, "🔒 Admin+ only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !sendtickets @user amount")
        return
    target_name = args[1].lstrip("@").strip()
    try:
        amount = int(args[2])
        if amount < 1:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "⚠️ Amount must be a positive integer.")
        return
    rec = _resolve_target(target_name)
    if not rec:
        await _w(bot, user.id, f"@{target_name} not found in DB.")
        return
    bal_before = get_luxe_balance(rec["user_id"])
    new_bal = add_luxe_balance(rec["user_id"], rec["username"], amount)
    log_luxe_transaction(
        rec["user_id"], rec["username"],
        "admin_sendtickets", amount, "luxe",
        f"by @{user.username} | before={bal_before} after={new_bal}",
    )
    print(f"[TICKET ADMIN] sendtickets actor={user.username} target={rec['username']} "
          f"amount={amount} before={bal_before} after={new_bal}")
    await _w(bot, user.id,
             f"✅ Sent {amount:,} 🎫 Luxe Tickets to @{rec['username']}.\n"
             f"New balance: {new_bal:,} 🎫")


# ---------------------------------------------------------------------------
# !ticketbalance @user   — owner/admin
# ---------------------------------------------------------------------------

async def handle_ticketbalance(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not (is_owner(user.username) or can_manage_economy(user.username)):
        await _w(bot, user.id, "🔒 Admin+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !ticketbalance @user")
        return
    target_name = args[1].lstrip("@").strip()
    rec = _resolve_target(target_name)
    if not rec:
        await _w(bot, user.id, f"@{target_name} not found in DB.")
        return
    bal = get_luxe_balance(rec["user_id"])
    await _w(bot, user.id, f"🎫 @{rec['username']} has {bal:,} Luxe Tickets.")


# ---------------------------------------------------------------------------
# !ticketlogs @user   — owner/admin
# ---------------------------------------------------------------------------

async def handle_ticketlogs(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not (is_owner(user.username) or can_manage_economy(user.username)):
        await _w(bot, user.id, "🔒 Admin+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !ticketlogs @user")
        return
    target_name = args[1].lstrip("@").strip()
    rec = _resolve_target(target_name)
    if not rec:
        await _w(bot, user.id, f"@{target_name} not found in DB.")
        return
    rows = _get_recent_ticket_logs(rec["user_id"], limit=5)
    if not rows:
        await _w(bot, user.id, f"🎫 No ticket logs for @{rec['username']} yet.")
        return
    await _w(bot, user.id, f"🎫 Recent ticket logs for @{rec['username']}:")
    for r in rows:
        sign  = "-" if r["type"] == "owner_removetickets" else "+"
        actor = "system"
        if "by @" in r["details"]:
            actor = r["details"].split("by @")[1].split(" |")[0]
        line = f"{sign}{r['amount']:,} {r['type']} by @{actor}"
        await _w(bot, user.id, line[:249])


# ---------------------------------------------------------------------------
# !ticketadmin   — show help (admin+)
# ---------------------------------------------------------------------------

async def handle_ticketadmin(bot: "BaseBot", user: "User") -> None:
    if not (is_owner(user.username) or can_manage_economy(user.username)):
        await _w(bot, user.id, "🔒 Admin+ only.")
        return
    await _w(bot, user.id,
             "🎫 Ticket Admin\n"
             "!addtickets @user amount\n"
             "!removetickets @user amount\n"
             "!settickets @user amount\n"
             "!sendtickets @user amount\n"
             "!ticketbalance @user\n"
             "!ticketlogs @user")


# ---------------------------------------------------------------------------
# !ticketrate   — show current ticket rate (public)
# ---------------------------------------------------------------------------

async def handle_ticketrate(bot: "BaseBot", user: "User") -> None:
    await _w(bot, user.id,
             "💎 Ticket Rate: 1g = 1 🎫 Luxe Ticket. No bonus. "
             "Convert to 🪙 ChillCoins with !buycoins.")
