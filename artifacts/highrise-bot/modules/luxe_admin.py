"""modules/luxe_admin.py — Owner/admin Luxe Ticket management commands (3.4A)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot, User

import database as db
from modules.permissions import is_owner, can_manage_economy, is_admin
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


def _is_staff(username: str) -> bool:
    return is_owner(username) or can_manage_economy(username) or is_admin(username)


def _get_recent_ticket_logs(user_id: str, limit: int = 5) -> list[dict]:
    conn = db.get_connection()
    rows = conn.execute(
        """SELECT type, amount, details, created_at
           FROM premium_transactions
           WHERE user_id=?
             AND type IN ('gold_tip_reward','owner_addtickets','owner_removetickets',
                          'owner_settickets','admin_sendtickets','player_sendtickets',
                          'admin_grant','admin_revoke','coinpack_purchase')
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
    db.insert_luxe_ticket_log(
        "admin_add", rec["user_id"], rec["username"],
        amount=amount, balance_after=new_bal,
        reason=f"addtickets by @{user.username}",
    )
    print(f"[TICKET ADMIN] addtickets actor={user.username} target={rec['username']} "
          f"amount={amount} before={bal_before} after={new_bal}")
    await _w(bot, user.id,
             f"✅ Added {amount:,} 🎫 to @{rec['username']}.\n"
             f"Balance: {new_bal:,} 🎫")


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
    db.insert_luxe_ticket_log(
        "admin_remove", rec["user_id"], rec["username"],
        amount=actual, balance_after=new_bal,
        reason=f"removetickets by @{user.username}",
    )
    print(f"[TICKET ADMIN] removetickets actor={user.username} target={rec['username']} "
          f"amount={actual} before={bal_before} after={new_bal}")
    await _w(bot, user.id,
             f"✅ Removed {actual:,} 🎫 from @{rec['username']}.\n"
             f"Balance: {new_bal:,} 🎫")


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
    db.insert_luxe_ticket_log(
        "admin_set", rec["user_id"], rec["username"],
        amount=amount, balance_after=new_bal,
        reason=f"settickets by @{user.username}",
    )
    print(f"[TICKET ADMIN] settickets actor={user.username} target={rec['username']} "
          f"set_to={amount} before={bal_before} after={new_bal}")
    await _w(bot, user.id,
             f"✅ Set @{rec['username']} Luxe Tickets to {new_bal:,} 🎫.")


# ---------------------------------------------------------------------------
# !sendtickets @user amount   — admin send (no balance check) OR player send
# ---------------------------------------------------------------------------

async def handle_sendtickets(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """Admin/owner send tickets to another player — no balance deducted from sender."""
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
    db.insert_luxe_ticket_log(
        "admin_send", rec["user_id"], rec["username"],
        target_user_id=user.id, target_username=user.username,
        amount=amount, balance_after=new_bal,
        reason=f"admin_send by @{user.username}",
    )
    print(f"[TICKET ADMIN] sendtickets actor={user.username} target={rec['username']} "
          f"amount={amount} before={bal_before} after={new_bal}")
    await _w(bot, user.id,
             f"🎟️ Sent {amount:,} 🎫 to @{rec['username']}.\n"
             f"Balance: {new_bal:,} 🎫")


# ---------------------------------------------------------------------------
# !p2psendtickets @user amount   — player-to-player ticket send
# ---------------------------------------------------------------------------

async def handle_player_sendtickets(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """Player sends their own tickets to another player."""
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
    if target_name.lower() == user.username.lower():
        await _w(bot, user.id, "⚠️ Cannot send tickets to yourself.")
        return
    rec = _resolve_target(target_name)
    if not rec:
        await _w(bot, user.id, f"@{target_name} not found in DB.")
        return
    sender_bal = get_luxe_balance(user.id)
    if sender_bal < amount:
        await _w(bot, user.id,
                 f"⚠️ Not enough 🎫.\n"
                 f"You have: {sender_bal:,} 🎫\n"
                 f"Need: {amount:,} 🎫")
        return
    if not deduct_luxe_balance(user.id, user.username, amount):
        await _w(bot, user.id, "⚠️ Transaction failed. Try again.")
        return
    new_sender_bal = get_luxe_balance(user.id)
    new_recv_bal   = add_luxe_balance(rec["user_id"], rec["username"], amount)
    log_luxe_transaction(
        user.id, user.username,
        "player_sendtickets", amount, "luxe",
        f"sent_to={rec['username']} sender_after={new_sender_bal}",
    )
    log_luxe_transaction(
        rec["user_id"], rec["username"],
        "player_sendtickets_recv", amount, "luxe",
        f"from={user.username} recv_after={new_recv_bal}",
    )
    db.insert_luxe_ticket_log(
        "player_send", user.id, user.username,
        target_user_id=rec["user_id"], target_username=rec["username"],
        amount=amount, balance_after=new_sender_bal,
        reason=f"p2p send to @{rec['username']}",
    )
    db.insert_luxe_ticket_log(
        "player_recv", rec["user_id"], rec["username"],
        target_user_id=user.id, target_username=user.username,
        amount=amount, balance_after=new_recv_bal,
        reason=f"p2p recv from @{user.username}",
    )
    print(f"[TICKET P2P] sender={user.username} receiver={rec['username']} amount={amount}")
    await _w(bot, user.id,
             f"🎟️ Sent {amount:,} 🎫 to @{rec['username']}.\n"
             f"Your balance: {new_sender_bal:,} 🎫")
    try:
        await _w(bot, rec["user_id"],
                 f"🎟️ @{user.username} sent you {amount:,} 🎫 Luxe Tickets!\n"
                 f"Balance: {new_recv_bal:,} 🎫")
    except Exception:
        pass


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
# !ticketlogs @user | !ticketlogs last | !ticketlogs ref [ref_id]
# ---------------------------------------------------------------------------

async def handle_ticketlogs(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not (is_owner(user.username) or can_manage_economy(user.username)):
        await _w(bot, user.id, "🔒 Admin+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage:\n"
                 "!ticketlogs @user\n"
                 "!ticketlogs last\n"
                 "!ticketlogs ref [ref_id]")
        return

    sub = args[1].lower().lstrip("@")

    if sub == "last":
        rows = db.get_luxe_ticket_logs(limit=8)
        if not rows:
            await _w(bot, user.id, "🎫 No ticket log entries yet.")
            return
        await _w(bot, user.id, f"🎫 Last {len(rows)} ticket changes:")
        for r in rows:
            sign = "+" if r["action"] not in ("admin_remove", "player_send", "coinpack_purchase") else "-"
            ts = r["created_at"][:16] if r.get("created_at") else "?"
            line = f"{sign}{r['amount']:,} {r['action']} @{r['username']} [{ts}]"
            await _w(bot, user.id, line[:249])
        return

    if sub == "ref":
        ref = args[2].strip() if len(args) >= 3 else ""
        if not ref:
            await _w(bot, user.id, "Usage: !ticketlogs ref [ref_id]")
            return
        rows = db.get_luxe_ticket_logs(ref_id=ref, limit=5)
        if not rows:
            await _w(bot, user.id, f"🎫 No logs found for ref: {ref}")
            return
        for r in rows:
            ts = r["created_at"][:16] if r.get("created_at") else "?"
            await _w(bot, user.id,
                     f"🎫 {r['action']} @{r['username']} {r['amount']:,} "
                     f"bal={r['balance_after']:,} [{ts}]")
        return

    # User lookup
    rec = _resolve_target(sub)
    if not rec:
        await _w(bot, user.id, f"@{sub} not found in DB.")
        return
    rows = db.get_luxe_ticket_logs(user_id=rec["user_id"], limit=6)
    if not rows:
        # Fall back to premium_transactions
        pt_rows = _get_recent_ticket_logs(rec["user_id"], limit=5)
        if not pt_rows:
            await _w(bot, user.id, f"🎫 No ticket logs for @{rec['username']} yet.")
            return
        await _w(bot, user.id, f"🎫 Ticket logs for @{rec['username']}:")
        for r in pt_rows:
            sign  = "-" if r["type"] == "owner_removetickets" else "+"
            actor = "system"
            if "by @" in (r.get("details") or ""):
                actor = r["details"].split("by @")[1].split(" |")[0]
            line = f"{sign}{r['amount']:,} {r['type']} by @{actor}"
            await _w(bot, user.id, line[:249])
        return

    bal = get_luxe_balance(rec["user_id"])
    await _w(bot, user.id,
             f"🎫 Ticket logs for @{rec['username']} | Bal: {bal:,}")
    for r in rows:
        sign = "+" if r["action"] not in ("admin_remove", "player_send", "coinpack_purchase") else "-"
        ts = r["created_at"][:16] if r.get("created_at") else "?"
        line = f"{sign}{r['amount']:,} {r['action']} [{ts}]"
        await _w(bot, user.id, line[:249])


# ---------------------------------------------------------------------------
# !ticketlog   — player's own last 5 ticket changes
# ---------------------------------------------------------------------------

async def handle_ticketlog_self(bot: "BaseBot", user: "User") -> None:
    rows = db.get_luxe_ticket_logs(user_id=user.id, limit=5)
    if not rows:
        # Fall back to premium_transactions
        pt_rows = _get_recent_ticket_logs(user.id, limit=5)
        if not pt_rows:
            await _w(bot, user.id, "🎫 No ticket changes recorded yet.")
            return
        await _w(bot, user.id, "🎫 Your recent ticket changes:")
        for r in pt_rows:
            sign = "-" if r["type"] in ("owner_removetickets", "player_sendtickets") else "+"
            line = f"{sign}{r['amount']:,} {r['type']}"
            await _w(bot, user.id, line[:249])
        return
    bal = get_luxe_balance(user.id)
    await _w(bot, user.id,
             f"🎫 Your ticket history | Balance: {bal:,} 🎫")
    for r in rows:
        sign = "+" if r["action"] not in ("admin_remove", "player_send", "coinpack_purchase") else "-"
        ts   = r["created_at"][:10] if r.get("created_at") else "?"
        line = f"{sign}{r['amount']:,} {r['action']} [{ts}]"
        await _w(bot, user.id, line[:249])


# ---------------------------------------------------------------------------
# !tiplogs @user | !tiplogs last   — admin: show tip awards
# ---------------------------------------------------------------------------

async def handle_tiplogs(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not (is_owner(user.username) or can_manage_economy(user.username)):
        await _w(bot, user.id, "🔒 Admin+ only.")
        return

    sub = args[1].lower().lstrip("@") if len(args) >= 2 else "last"

    if sub == "last":
        rows = db.get_luxe_ticket_logs(action="tip_award", limit=8)
        if not rows:
            # Fall back to gold_tip_events
            conn = db.get_connection()
            gt_rows = conn.execute(
                "SELECT from_username, gold_amount, created_at, event_id "
                "FROM gold_tip_events ORDER BY id DESC LIMIT 8"
            ).fetchall()
            conn.close()
            if not gt_rows:
                await _w(bot, user.id, "🎟️ No tip logs found yet.")
                return
            await _w(bot, user.id, "🎟️ Recent tip awards (gold_tip_events):")
            for r in gt_rows:
                ts = r["created_at"][:16] if r.get("created_at") else "?"
                coins_equiv = int(r["gold_amount"])
                line = (f"@{r['from_username']} tipped {r['gold_amount']:g}g "
                        f"→ {coins_equiv:,} 🎫 [{ts}]")
                await _w(bot, user.id, line[:249])
            return
        await _w(bot, user.id, f"🎟️ Last {len(rows)} tip awards:")
        for r in rows:
            ts  = r["created_at"][:16] if r.get("created_at") else "?"
            ref = r.get("ref_id", "")[:12]
            line = f"+{r['amount']:,} 🎫 @{r['username']} [{ts}] ref:{ref}"
            await _w(bot, user.id, line[:249])
        return

    # User lookup
    rec = db.get_user_by_username(sub)
    if not rec:
        await _w(bot, user.id, f"@{sub} not found in DB.")
        return
    # Check luxe_ticket_logs first
    rows = db.get_luxe_ticket_logs(user_id=rec["user_id"], action="tip_award", limit=8)
    if rows:
        await _w(bot, user.id, f"🎟️ Tip logs for @{rec['username']}:")
        for r in rows:
            ts  = r["created_at"][:16] if r.get("created_at") else "?"
            ref = r.get("ref_id", "")[:12]
            line = f"+{r['amount']:,} 🎫 [{ts}] ref:{ref}"
            await _w(bot, user.id, line[:249])
        return
    # Fall back to gold_tip_events
    conn = db.get_connection()
    gt_rows = conn.execute(
        "SELECT gold_amount, receiving_bot, created_at, event_id "
        "FROM gold_tip_events WHERE LOWER(from_username)=LOWER(?) "
        "ORDER BY id DESC LIMIT 8",
        (rec["username"],),
    ).fetchall()
    conn.close()
    if not gt_rows:
        await _w(bot, user.id, f"🎟️ No tip logs for @{rec['username']} yet.")
        return
    total_g = sum(r["gold_amount"] for r in gt_rows)
    await _w(bot, user.id,
             f"🎟️ Tip logs for @{rec['username']} (total: {total_g:g}g):")
    for r in gt_rows:
        ts = r["created_at"][:16] if r.get("created_at") else "?"
        coins_equiv = int(r["gold_amount"])
        line = (f"{r['gold_amount']:g}g → {coins_equiv:,} 🎫 "
                f"via {r['receiving_bot']} [{ts}]")
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
             "!ticketlogs @user | last | ref [id]")
    await _w(bot, user.id,
             "🎫 Coin Pack Admin\n"
             "!coinpackadmin\n"
             "!setcoinpack [id] [tickets] [coins]\n"
             "!tiplogs last | @user")


# ---------------------------------------------------------------------------
# !ticketrate   — show current ticket rate (public)
# ---------------------------------------------------------------------------

async def handle_ticketrate(bot: "BaseBot", user: "User") -> None:
    await _w(bot, user.id,
             "💎 Ticket Rate: 1g = 1 🎫 Luxe Ticket. No bonus. "
             "Convert to 🪙 ChillCoins with !buypack.")
