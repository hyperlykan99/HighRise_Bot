"""modules/luxe_admin.py — Owner/admin Luxe Ticket management commands (3.4A)."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot
    from highrise.models import User

import database as db
from modules.permissions import is_owner, is_admin, is_manager
from modules.luxe import (
    get_luxe_balance, add_luxe_balance, deduct_luxe_balance,
    set_luxe_balance, log_luxe_transaction,
)


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, str(msg)[:249])


def _resolve_uid(raw_name: str) -> tuple[str | None, str]:
    """Look up user_id by username from DB. Returns (uid, canonical_username) or (None, '')."""
    name = raw_name.lower().lstrip("@")
    row  = db.get_user_by_username(name)
    if row:
        return row["user_id"], row["username"]
    return None, ""


def _parse_pos_int(s: str) -> int | None:
    try:
        v = int(s)
        return v if v > 0 else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# !addtickets @user amount  (owner only)
# ---------------------------------------------------------------------------

async def handle_addtickets(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !addtickets @user amount")
        return
    uid, uname = _resolve_uid(args[1])
    amount     = _parse_pos_int(args[2])
    if not uid:
        await _w(bot, user.id, f"User {args[1].lstrip('@')} not found in DB.")
        return
    if amount is None:
        await _w(bot, user.id, "\u26a0\ufe0f Enter a positive integer amount.")
        return
    new_bal = add_luxe_balance(uid, uname, amount)
    log_luxe_transaction(uid, uname, "admin_add", amount, "luxe",
                         f"by={user.username}")
    print(f"[TICKET ADMIN] add uid={uid} user={uname} amount={amount} by={user.username}")
    await _w(bot, user.id,
             f"\u2705 Added {amount:,} \U0001f3ab to @{uname}. Balance: {new_bal:,} \U0001f3ab.")


# ---------------------------------------------------------------------------
# !removetickets @user amount  (owner only)
# ---------------------------------------------------------------------------

async def handle_removetickets(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !removetickets @user amount")
        return
    uid, uname = _resolve_uid(args[1])
    amount     = _parse_pos_int(args[2])
    if not uid:
        await _w(bot, user.id, f"User {args[1].lstrip('@')} not found in DB.")
        return
    if amount is None:
        await _w(bot, user.id, "\u26a0\ufe0f Enter a positive integer amount.")
        return
    cur    = get_luxe_balance(uid)
    actual = min(amount, cur)   # no negative balances
    if actual > 0:
        deduct_luxe_balance(uid, uname, actual)
        log_luxe_transaction(uid, uname, "admin_remove", actual, "luxe",
                             f"by={user.username}")
    new_bal = get_luxe_balance(uid)
    print(f"[TICKET ADMIN] remove uid={uid} user={uname} amount={actual} by={user.username}")
    await _w(bot, user.id,
             f"\u2705 Removed {actual:,} \U0001f3ab from @{uname}. Balance: {new_bal:,} \U0001f3ab.")


# ---------------------------------------------------------------------------
# !settickets @user amount  (owner only)
# ---------------------------------------------------------------------------

async def handle_settickets(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !settickets @user amount")
        return
    uid, uname = _resolve_uid(args[1])
    if not uid:
        await _w(bot, user.id, f"User {args[1].lstrip('@')} not found in DB.")
        return
    try:
        amount = int(args[2])
        if amount < 0:
            raise ValueError
    except (ValueError, TypeError):
        await _w(bot, user.id, "\u26a0\ufe0f Enter a non-negative integer amount.")
        return
    old_bal = get_luxe_balance(uid)
    new_bal = set_luxe_balance(uid, uname, amount)
    log_luxe_transaction(uid, uname, "admin_set", amount, "luxe",
                         f"by={user.username} prev={old_bal}")
    print(f"[TICKET ADMIN] set uid={uid} user={uname} amount={new_bal} by={user.username}")
    await _w(bot, user.id,
             f"\u2705 Set @{uname} Luxe Tickets to {new_bal:,} \U0001f3ab.")


# ---------------------------------------------------------------------------
# !sendtickets @user amount  (admin+ — transfers from sender's balance)
# ---------------------------------------------------------------------------

async def handle_sendtickets(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not (is_owner(user.username) or is_admin(user.username)):
        await _w(bot, user.id, "Admin+ only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !sendtickets @user amount")
        return
    uid, uname = _resolve_uid(args[1])
    amount     = _parse_pos_int(args[2])
    if not uid:
        await _w(bot, user.id, f"User {args[1].lstrip('@')} not found in DB.")
        return
    if amount is None:
        await _w(bot, user.id, "\u26a0\ufe0f Enter a positive integer amount.")
        return
    if uid == user.id:
        await _w(bot, user.id, "\u26a0\ufe0f Cannot send tickets to yourself.")
        return
    # Admins must have enough; owners bypass balance check (admin gift)
    if not is_owner(user.username):
        bal = get_luxe_balance(user.id)
        if bal < amount:
            await _w(bot, user.id,
                     f"\u26a0\ufe0f You only have {bal:,} \U0001f3ab. Cannot send {amount:,}.")
            return
        deduct_luxe_balance(user.id, user.username, amount)
        log_luxe_transaction(user.id, user.username, "send_deduct", amount, "luxe",
                             f"to={uname}")
    add_luxe_balance(uid, uname, amount)
    log_luxe_transaction(uid, uname, "send_receive", amount, "luxe",
                         f"from={user.username}")
    new_bal = get_luxe_balance(uid)
    print(f"[TICKET ADMIN] send from={user.username} to={uname} amount={amount}")
    await _w(bot, user.id,
             f"\u2705 Sent {amount:,} \U0001f3ab to @{uname}. Their balance: {new_bal:,} \U0001f3ab.")


# ---------------------------------------------------------------------------
# !ticketadmin  (admin+)
# ---------------------------------------------------------------------------

async def handle_ticketadmin(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not (is_owner(user.username) or is_admin(user.username)):
        await _w(bot, user.id, "Admin+ only.")
        return
    await _w(bot, user.id,
             "\U0001f3ab Luxe Ticket Admin\n"
             "!addtickets @u amt | !removetickets @u amt\n"
             "!settickets @u amt | !sendtickets @u amt\n"
             "!ticketbalance @u | !ticketlogs [@u]")


# ---------------------------------------------------------------------------
# !ticketlogs [@user]  (admin+)
# ---------------------------------------------------------------------------

async def handle_ticketlogs(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not (is_owner(user.username) or is_admin(user.username)):
        await _w(bot, user.id, "Admin+ only.")
        return
    conn = db.get_connection()
    try:
        if len(args) >= 2:
            target = args[1].lstrip("@")
            uid_row = db.get_user_by_username(target.lower())
            if not uid_row:
                await _w(bot, user.id, f"User @{target} not found in DB.")
                return
            rows = conn.execute(
                """SELECT type, amount, details, created_at
                   FROM premium_transactions
                   WHERE user_id=? AND currency='luxe'
                   ORDER BY id DESC LIMIT 8""",
                (uid_row["user_id"],),
            ).fetchall()
            header = f"\U0001f3ab Ticket Logs: @{target}"
        else:
            rows = conn.execute(
                """SELECT username, type, amount, details, created_at
                   FROM premium_transactions
                   WHERE currency='luxe'
                   ORDER BY id DESC LIMIT 8""",
            ).fetchall()
            header = "\U0001f3ab Recent Ticket Logs"
    finally:
        conn.close()
    if not rows:
        await _w(bot, user.id, "No Luxe ticket logs yet.")
        return
    await _w(bot, user.id, header)
    for r in rows:
        rd   = dict(r)
        dt   = rd.get("created_at", "")[:10]
        amt  = rd.get("amount", 0)
        typ  = rd.get("type", "?")
        det  = rd.get("details", "")
        who  = f"@{rd['username']} " if "username" in rd else ""
        await _w(bot, user.id, f"{who}{typ}: {amt:+,} \U0001f3ab {det} [{dt}]"[:249])


# ---------------------------------------------------------------------------
# !ticketbalance @user  (manager+)
# ---------------------------------------------------------------------------

async def handle_ticketbalance(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not (is_owner(user.username) or is_admin(user.username) or is_manager(user.username)):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !ticketbalance @user")
        return
    uid, uname = _resolve_uid(args[1])
    if not uid:
        await _w(bot, user.id, f"User @{args[1].lstrip('@')} not found in DB.")
        return
    bal = get_luxe_balance(uid)
    await _w(bot, user.id, f"\U0001f3ab @{uname} has {bal:,} Luxe Tickets.")
