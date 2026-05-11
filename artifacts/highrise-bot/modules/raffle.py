"""
modules/raffle.py
-----------------
VIP/Supporter raffle system.

Player commands:
  !raffle              — show active raffle info
  !raffle enter        — enter the active raffle (costs 1 ticket)
  !raffle status       — show your entry status
  !raffle winners      — recent winner log

Staff/Manager+ commands:
  !startraffle [prize] — start a new raffle with optional prize description
  !endraffle           — end active raffle without picking winner
  !rafflepick          — pick a random winner from entries
  !rafflereset         — clear entries and reset raffle

Raffle tickets are earned via missions and events.
Free players can earn tickets through missions; VIP/supporters get a small bonus.
"""
from __future__ import annotations
import asyncio
import random
from datetime import datetime, timezone
from highrise import BaseBot, User

import database as db
from modules.permissions import is_manager


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_active_raffle() -> dict | None:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT * FROM raffles WHERE status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _get_entry_count(raffle_id: int) -> int:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT SUM(tickets_used) FROM raffle_entries WHERE raffle_id=?",
            (raffle_id,),
        ).fetchone()
        conn.close()
        return int(row[0]) if row and row[0] else 0
    except Exception:
        return 0


def _player_tickets_in(raffle_id: int, user_id: str) -> int:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT SUM(tickets_used) FROM raffle_entries "
            "WHERE raffle_id=? AND user_id=?",
            (raffle_id, user_id),
        ).fetchone()
        conn.close()
        return int(row[0]) if row and row[0] else 0
    except Exception:
        return 0


def _get_ticket_balance(user_id: str) -> int:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT amount FROM raffle_tickets WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        return int(row["amount"]) if row else 0
    except Exception:
        return 0


def _spend_ticket(user_id: str, username: str, amount: int = 1) -> bool:
    try:
        bal = _get_ticket_balance(user_id)
        if bal < amount:
            return False
        conn = db.get_connection()
        conn.execute(
            "UPDATE raffle_tickets SET amount = amount - ? WHERE user_id=?",
            (amount, user_id),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


def _add_entry(raffle_id: int, user_id: str, username: str, tickets: int) -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO raffle_entries
                 (raffle_id, user_id, username, tickets_used, entered_at)
               VALUES (?,?,?,?,?)""",
            (raffle_id, user_id, username.lower(), tickets,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _get_recent_winners(limit: int = 5) -> list[dict]:
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT * FROM raffles WHERE status='completed' "
            "AND winner_name != '' ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _pick_weighted_winner(raffle_id: int) -> dict | None:
    """Pick a winner, weighted by ticket count."""
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT user_id, username, tickets_used "
            "FROM raffle_entries WHERE raffle_id=?",
            (raffle_id,),
        ).fetchall()
        conn.close()
        if not rows:
            return None
        pool: list[tuple[str, str]] = []
        for r in rows:
            pool.extend([(r["user_id"], r["username"])] * int(r["tickets_used"]))
        if not pool:
            return None
        uid, uname = random.choice(pool)
        return {"user_id": uid, "username": uname}
    except Exception:
        return None


def _close_raffle(raffle_id: int, winner_id: str, winner_name: str) -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            "UPDATE raffles SET status='completed', winner_id=?, winner_name=?, "
            "ended_at=? WHERE id=?",
            (winner_id, winner_name,
             datetime.now(timezone.utc).isoformat(), raffle_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _end_raffle(raffle_id: int) -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            "UPDATE raffles SET status='ended', ended_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), raffle_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_raffle(bot: BaseBot, user: User, args: list[str]) -> None:
    sub = args[1].lower() if len(args) >= 2 else ""
    if sub == "enter":
        await _handle_raffle_enter(bot, user)
    elif sub == "status":
        await _handle_raffle_status(bot, user)
    elif sub == "winners":
        await _handle_raffle_winners(bot, user)
    else:
        await _handle_raffle_info(bot, user)


async def _handle_raffle_info(bot: BaseBot, user: User) -> None:
    r = _get_active_raffle()
    if not r:
        await _w(bot, user.id,
                 "🎟️ No active raffle right now.\n"
                 "Check back soon or type !raffle winners.")
        return
    entries = _get_entry_count(r["id"])
    tickets = _get_ticket_balance(user.id)
    await _w(bot, user.id,
             f"🎟️ Raffle\n"
             f"Prize: {r['prize']}\n"
             f"Total entries (tickets): {entries:,}\n"
             f"Your tickets: {tickets}\n"
             f"Enter: !raffle enter (costs 1 ticket)")


async def _handle_raffle_enter(bot: BaseBot, user: User) -> None:
    r = _get_active_raffle()
    if not r:
        await _w(bot, user.id, "🎟️ No active raffle to enter right now.")
        return
    tickets = _get_ticket_balance(user.id)
    if tickets < 1:
        await _w(bot, user.id,
                 "🎟️ You have no raffle tickets.\n"
                 "Earn tickets via !missions or events.")
        return
    ok = _spend_ticket(user.id, user.username, 1)
    if not ok:
        await _w(bot, user.id, "🎟️ Not enough tickets to enter.")
        return
    _add_entry(r["id"], user.id, user.username, 1)
    my_total = _player_tickets_in(r["id"], user.id)
    await _w(bot, user.id,
             f"✅ You entered the raffle!\n"
             f"Prize: {r['prize']}\n"
             f"Your entries: {my_total}")


async def _handle_raffle_status(bot: BaseBot, user: User) -> None:
    r = _get_active_raffle()
    tickets = _get_ticket_balance(user.id)
    if not r:
        await _w(bot, user.id,
                 f"🎟️ No active raffle.\n"
                 f"Your raffle tickets: {tickets}")
        return
    my_entries = _player_tickets_in(r["id"], user.id)
    total      = _get_entry_count(r["id"])
    await _w(bot, user.id,
             f"🎟️ Raffle Status\n"
             f"Prize: {r['prize']}\n"
             f"Your entries: {my_entries}\n"
             f"Total entries: {total:,}\n"
             f"Your ticket balance: {tickets}")


async def _handle_raffle_winners(bot: BaseBot, user: User) -> None:
    rows = _get_recent_winners(5)
    if not rows:
        await _w(bot, user.id, "🏆 No raffle winners yet.")
        return
    lines = ["🏆 Recent Raffle Winners"]
    for r in rows:
        lines.append(f"@{r['winner_name']} — {r['prize']}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_startraffle(bot: BaseBot, user: User, args: list[str]) -> None:
    if not is_manager(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if _get_active_raffle():
        await _w(bot, user.id,
                 "⚠️ A raffle is already active.\n"
                 "Use !endraffle first.")
        return
    prize = " ".join(args[1:]) if len(args) >= 2 else "Surprise Prize"
    try:
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO raffles (prize, status, created_at) VALUES (?,?,?)",
            (prize[:100], "active", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        await _w(bot, user.id, f"Error: {exc}")
        return
    try:
        await bot.highrise.chat(
            f"🎟️ Raffle Started!\nPrize: {prize[:80]}\n"
            f"Enter with !raffle enter (costs 1 ticket)"
        )
    except Exception:
        pass
    await _w(bot, user.id, f"✅ Raffle started. Prize: {prize[:80]}")


async def handle_endraffle(bot: BaseBot, user: User) -> None:
    if not is_manager(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    r = _get_active_raffle()
    if not r:
        await _w(bot, user.id, "No active raffle.")
        return
    _end_raffle(r["id"])
    await _w(bot, user.id, "✅ Raffle ended (no winner picked).")


async def handle_rafflepick(bot: BaseBot, user: User) -> None:
    if not is_manager(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    r = _get_active_raffle()
    if not r:
        await _w(bot, user.id, "No active raffle.")
        return
    winner = _pick_weighted_winner(r["id"])
    if not winner:
        await _w(bot, user.id, "No entries yet — cannot pick winner.")
        return
    _close_raffle(r["id"], winner["user_id"], winner["username"])
    try:
        await bot.highrise.chat(
            f"🎟️ Raffle Winner!\n"
            f"🎉 Congratulations @{winner['username']}!\n"
            f"Prize: {r['prize']}"
        )
    except Exception:
        pass
    await _w(bot, user.id,
             f"✅ Winner: @{winner['username']}\nPrize: {r['prize']}")


async def handle_rafflereset(bot: BaseBot, user: User) -> None:
    if not is_manager(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    r = _get_active_raffle()
    if r:
        _end_raffle(r["id"])
    try:
        conn = db.get_connection()
        conn.execute(
            "DELETE FROM raffle_entries WHERE raffle_id IN "
            "(SELECT id FROM raffles WHERE status IN ('active','ended'))"
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    await _w(bot, user.id, "✅ Raffle reset. Entries cleared.")
