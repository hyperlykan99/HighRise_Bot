"""
modules/gold_tips.py
--------------------
Gold tip migration: BankingBot handles all gold-tip conversion.

Flow:
  1. Any bot receives a gold tip via on_tip.
  2. Receiving bot whispers acknowledgement and logs the event.
  3. If this bot IS the banker, it also converts gold → coins,
     credits the player's balance, and confirms.
  4. Duplicate protection via gold_tip_events.event_id.

Commands:
  /goldtipsettings   — show settings (staff)
  /setgoldrate <n>   — set coins_per_gold (admin+)
  /goldtiplogs       — recent tip log (staff)
  /mygoldtips        — player's own tip history
  /goldtipstatus     — quick status (all)
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import database as db
from highrise import BaseBot, User
from modules.permissions import is_admin, is_owner, is_manager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, str(msg)[:249])


def _can_staff(username: str) -> bool:
    return is_manager(username) or is_admin(username) or is_owner(username)


def _can_admin(username: str) -> bool:
    return is_admin(username) or is_owner(username)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# DB helpers — gold_tip_events + room_settings
# ---------------------------------------------------------------------------

_DEFAULT_RATE = 1000  # coins per 1 gold bar
_DEFAULT_MIN  = 1.0   # minimum gold tip amount


def get_coins_per_gold() -> int:
    try:
        return int(db.get_room_setting("gold_tip_coins_per_gold",
                                       str(_DEFAULT_RATE)))
    except ValueError:
        return _DEFAULT_RATE


def get_min_gold_tip() -> float:
    try:
        return float(db.get_room_setting("gold_tip_min_amount", str(_DEFAULT_MIN)))
    except ValueError:
        return _DEFAULT_MIN


def gold_tip_enabled() -> bool:
    return db.get_room_setting("gold_tip_enabled", "1") == "1"


def _make_event_id(from_uid: str, receiving_bot: str,
                   gold_amount: float, ts_bucket: str) -> str:
    raw = f"{from_uid}|{receiving_bot.lower()}|{gold_amount}|{ts_bucket}"
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def _insert_tip_event(
    event_id: str,
    from_uid: str,
    from_uname: str,
    receiving_bot: str,
    gold_amount: float,
    coins: int,
    rate: float,
    processed_by: str,
    status: str,
) -> bool:
    """Insert tip event. Returns False if duplicate (already exists)."""
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT INTO gold_tip_events
               (event_id, from_user_id, from_username, receiving_bot,
                gold_amount, coins_converted, conversion_rate,
                processed_by, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (event_id, from_uid, from_uname.lower(), receiving_bot.lower(),
             gold_amount, coins, rate, processed_by.lower(), status),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def _get_recent_tips(limit: int = 10) -> list[dict]:
    conn = db.get_connection()
    rows = conn.execute(
        """SELECT from_username, receiving_bot, gold_amount,
                  coins_converted, conversion_rate, status, created_at
           FROM gold_tip_events
           ORDER BY id DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _get_player_tips(username: str, limit: int = 10) -> list[dict]:
    conn = db.get_connection()
    rows = conn.execute(
        """SELECT receiving_bot, gold_amount, coins_converted,
                  conversion_rate, status, created_at
           FROM gold_tip_events
           WHERE lower(from_username)=lower(?)
           ORDER BY id DESC LIMIT ?""",
        (username, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Core tip handler — called from on_tip for each bot
# ---------------------------------------------------------------------------

async def handle_incoming_gold_tip(
    bot: BaseBot,
    sender: User,
    receiving_bot_username: str,
    gold_amount: float,
    sdk_event_id: str | None = None,
) -> None:
    """
    Called when any bot receives a gold tip.
    - Non-banker bots: acknowledge only.
    - Banker bot: acknowledge + process conversion.
    """
    if not gold_tip_enabled():
        print(f"[GOLDTIP] Disabled — ignoring tip from @{sender.username} "
              f"({gold_amount} gold via {receiving_bot_username})")
        return

    min_tip = get_min_gold_tip()
    if gold_amount < min_tip:
        print(f"[GOLDTIP] Below minimum ({gold_amount}g < {min_tip:g}g) — ignoring "
              f"tip from @{sender.username}")
        return

    rate  = get_coins_per_gold()
    coins = int(gold_amount * rate)

    # Build dedup key
    ts_bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    event_id  = sdk_event_id or _make_event_id(
        sender.id, receiving_bot_username, gold_amount, ts_bucket
    )

    print(f"[GOLDTIP] Received: from=@{sender.username} "
          f"bot={receiving_bot_username} gold={gold_amount} "
          f"event_id={event_id}")

    inserted = _insert_tip_event(
        event_id, sender.id, sender.username,
        receiving_bot_username, gold_amount, coins,
        float(rate), receiving_bot_username, "rewarded",
    )
    if not inserted:
        print(f"[GOLDTIP] Duplicate skipped: event_id={event_id}")
        return

    # Credit balance
    try:
        db.ensure_user(sender.id, sender.username)
        db.add_balance(sender.id, coins)
    except Exception as exc:
        print(f"[GOLDTIP] Balance credit error: {exc}")

    # Public thank-you
    msg = (f"💛 Thank you @{sender.username} for the {gold_amount:g} gold tip! "
           f"You received {coins:,} coins.")
    try:
        await bot.highrise.chat(msg[:249])
    except Exception:
        pass
    print(f"[GOLDTIP] Rewarded: @{sender.username} +{coins:,} coins "
          f"(event_id={event_id})")


# ---------------------------------------------------------------------------
# /goldtipsettings
# ---------------------------------------------------------------------------

async def handle_goldtipsettings(bot: BaseBot, user: User) -> None:
    """/goldtipsettings — show gold tip conversion settings."""
    if not _can_staff(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    enabled = "ON" if gold_tip_enabled() else "OFF"
    rate    = get_coins_per_gold()
    await _w(bot, user.id,
             f"<#FFD700>💰 Gold Tip Settings<#FFFFFF>\n"
             f"Enabled: {enabled} | Rate: {rate:,} coins/gold | "
             f"Dedup: ON (event_id hash)")


# ---------------------------------------------------------------------------
# /setgoldrate <coins_per_gold>
# ---------------------------------------------------------------------------

async def handle_setgoldrate(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setgoldrate <coins_per_gold> — set gold tip conversion rate (admin+)."""
    if not _can_admin(user.username):
        await _w(bot, user.id, "Admin+ only.")
        return
    if len(args) < 2:
        cur = get_coins_per_gold()
        await _w(bot, user.id,
                 f"Current gold rate: {cur:,} coins/gold. "
                 f"Usage: /setgoldrate <amount>")
        return
    try:
        val = int(args[1])
        if val < 1:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "⚠️ Enter a positive integer.")
        return
    db.set_room_setting("gold_tip_coins_per_gold", str(val))
    await _w(bot, user.id, f"✅ Gold rate set: {val:,} coins per gold.")


# ---------------------------------------------------------------------------
# /goldtiplogs
# ---------------------------------------------------------------------------

async def handle_goldtiplogs(bot: BaseBot, user: User) -> None:
    """/goldtiplogs — recent gold tip conversion log (staff)."""
    if not _can_staff(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    rows = _get_recent_tips(limit=8)
    if not rows:
        await _w(bot, user.id, "No gold tip events logged yet.")
        return
    await _w(bot, user.id, f"<#FFD700>💰 Gold Tip Log<#FFFFFF> (last {len(rows)})")
    for r in rows:
        dt  = r["created_at"][:16] if r.get("created_at") else "?"
        line = (f"@{r['from_username']} → {r['gold_amount']:g}g "
                f"= {r['coins_converted']:,}c via {r['receiving_bot']} [{dt}]")
        await _w(bot, user.id, line[:249])


# ---------------------------------------------------------------------------
# /mygoldtips
# ---------------------------------------------------------------------------

async def handle_mygoldtips(bot: BaseBot, user: User) -> None:
    """/mygoldtips — show your gold tip conversion history."""
    rows = _get_player_tips(user.username, limit=8)
    if not rows:
        await _w(bot, user.id, "You have no gold tip records yet.")
        return
    total_gold  = sum(r["gold_amount"]  for r in rows)
    total_coins = sum(r["coins_converted"] for r in rows)
    await _w(bot, user.id,
             f"<#FFD700>💰 My Gold Tips<#FFFFFF>: "
             f"{total_gold:g} gold → {total_coins:,} coins total")
    for r in rows[:5]:
        dt   = r["created_at"][:10] if r.get("created_at") else "?"
        line = (f"{r['gold_amount']:g}g → {r['coins_converted']:,}c "
                f"via {r['receiving_bot']} ({dt})")
        await _w(bot, user.id, line[:249])


# ---------------------------------------------------------------------------
# /goldtipstatus
# ---------------------------------------------------------------------------

async def handle_goldtipstatus(bot: BaseBot, user: User) -> None:
    """/goldtipstatus — quick gold tip status (all players)."""
    enabled = "ON" if gold_tip_enabled() else "OFF"
    rate    = get_coins_per_gold()
    min_g   = get_min_gold_tip()
    await _w(bot, user.id,
             f"<#FFD700>💰 Gold Tips<#FFFFFF>: {enabled} | "
             f"Rate: {rate:,} coins/gold | Min: {min_g:g}g | "
             f"Handled by: BankingBot")


# ---------------------------------------------------------------------------
# /tipcoinrate
# ---------------------------------------------------------------------------

async def handle_tipcoinrate(bot: BaseBot, user: User) -> None:
    """/tipcoinrate — show current tip coin rate (manager+)."""
    if not _can_staff(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    rate  = get_coins_per_gold()
    min_g = get_min_gold_tip()
    await _w(bot, user.id,
             f"<#FFD700>💰 Tip Coin Rate<#FFFFFF>: {rate:,} coins per 1 gold | "
             f"Min tip: {min_g:g}g")


# ---------------------------------------------------------------------------
# /settipcoinrate <coins_per_gold>
# ---------------------------------------------------------------------------

async def handle_settipcoinrate(bot: BaseBot, user: User, args: list[str]) -> None:
    """/settipcoinrate <amount> — set tip coin rate (admin+)."""
    if not _can_admin(user.username):
        await _w(bot, user.id, "Admin+ only.")
        return
    if len(args) < 2:
        cur = get_coins_per_gold()
        await _w(bot, user.id,
                 f"Current tip coin rate: {cur:,} coins/gold. "
                 f"Usage: /settipcoinrate <amount>")
        return
    try:
        val = int(args[1])
        if val < 1:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "⚠️ Enter a positive integer.")
        return
    db.set_room_setting("gold_tip_coins_per_gold", str(val))
    await _w(bot, user.id, f"✅ Tip coin rate set: {val:,} coins per gold.")


# ---------------------------------------------------------------------------
# /bottiplogs
# ---------------------------------------------------------------------------

async def handle_bottiplogs(bot: BaseBot, user: User) -> None:
    """/bottiplogs — recent bot tip log (staff)."""
    if not _can_staff(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    rows = _get_recent_tips(limit=8)
    if not rows:
        await _w(bot, user.id, "No bot tip events logged yet.")
        return
    await _w(bot, user.id, f"<#FFD700>💰 Bot Tip Log<#FFFFFF> (last {len(rows)})")
    for r in rows:
        dt   = r["created_at"][:16] if r.get("created_at") else "?"
        line = (f"@{r['from_username']} → {r['gold_amount']:g}g "
                f"= {r['coins_converted']:,}c via {r['receiving_bot']} [{dt}]")
        await _w(bot, user.id, line[:249])


# ---------------------------------------------------------------------------
# /mingoldtip
# ---------------------------------------------------------------------------

async def handle_mingoldtip(bot: BaseBot, user: User) -> None:
    """/mingoldtip — show minimum gold tip setting (staff)."""
    if not _can_staff(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    min_g = get_min_gold_tip()
    rate  = get_coins_per_gold()
    await _w(bot, user.id,
             f"<#FFD700>💰 Min Gold Tip<#FFFFFF>: {min_g:g}g | "
             f"Rate: {rate:,} coins/gold")


# ---------------------------------------------------------------------------
# /setmingoldtip <amount>
# ---------------------------------------------------------------------------

async def handle_setmingoldtip(bot: BaseBot, user: User, args: list[str]) -> None:
    """/setmingoldtip <amount> — set minimum gold tip (admin+)."""
    if not _can_admin(user.username):
        await _w(bot, user.id, "Admin+ only.")
        return
    if len(args) < 2:
        cur = get_min_gold_tip()
        await _w(bot, user.id,
                 f"Current min gold tip: {cur:g}g. Usage: /setmingoldtip <amount>")
        return
    try:
        val = float(args[1])
        if val < 0.01:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "⚠️ Enter a valid amount ≥ 0.01.")
        return
    db.set_room_setting("gold_tip_min_amount", str(val))
    await _w(bot, user.id, f"✅ Min gold tip set: {val:g} gold.")


# ---------------------------------------------------------------------------
# Leaderboard helpers
# ---------------------------------------------------------------------------

def _get_top_tippers(bot_filter: str | None = None, limit: int = 10) -> list[dict]:
    """Return top tippers, optionally filtered to a specific receiving_bot."""
    conn = db.get_connection()
    try:
        if bot_filter:
            rows = conn.execute(
                "SELECT from_username, SUM(gold_amount) AS total, COUNT(*) AS tips "
                "FROM gold_tip_events WHERE LOWER(receiving_bot)=? "
                "GROUP BY from_user_id ORDER BY total DESC LIMIT ?",
                (bot_filter, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT from_username, SUM(gold_amount) AS total, COUNT(*) AS tips "
                "FROM gold_tip_events "
                "GROUP BY from_user_id ORDER BY total DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_top_receivers(limit: int = 10) -> list[dict]:
    """Return top tip receivers (bots) sorted by total gold received."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT receiving_bot, SUM(gold_amount) AS total, COUNT(*) AS tips "
            "FROM gold_tip_events "
            "GROUP BY receiving_bot ORDER BY total DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /tiplb  /tipleaderboard  /bottiplb  /bottipleaderboard [botname]
# ---------------------------------------------------------------------------

async def handle_tiplb(bot: BaseBot, user: User, args: list[str]) -> None:
    """/tiplb [botname] — top players who tipped bots (BankerBot)."""
    bot_filter: str | None = None
    if len(args) > 1:
        last = args[-1].lower().lstrip("@")
        if last not in ("bot", "tiplb", "tipleaderboard",
                        "bottiplb", "bottipleaderboard"):
            bot_filter = last

    rows = _get_top_tippers(bot_filter)
    if bot_filter:
        title = f"Top Tippers — {bot_filter}"
    else:
        title = "Top Bot Tippers"

    if not rows:
        await _w(bot, user.id,
                 f"<#FFD700>🏆 {title}<#FFFFFF>: No tips recorded yet.")
        return
    await _w(bot, user.id, f"<#FFD700>🏆 {title}<#FFFFFF>")
    for i, r in enumerate(rows, 1):
        cnt  = r["tips"]
        line = (f"{i}. @{r['from_username']} — {r['total']:g}g | "
                f"{cnt} tip{'s' if cnt != 1 else ''}")
        await _w(bot, user.id, line[:249])


# ---------------------------------------------------------------------------
# /roomtiplb  /roomtipleaderboard  /alltiplb  /alltipleaderboard
# ---------------------------------------------------------------------------

async def handle_roomtiplb(bot: BaseBot, user: User) -> None:
    """/roomtiplb — room-wide tip leaderboard (bot tips; SDK note)."""
    rows  = _get_top_tippers()
    hdr   = "<#FFD700>🏆 Top Room Tippers<#FFFFFF>"
    note  = "Note: Highrise SDK does not expose player-to-player tips. Showing bot tips only."

    if not rows:
        await _w(bot, user.id, f"{hdr}: No tips recorded yet.")
        return
    await _w(bot, user.id, hdr)
    await _w(bot, user.id, note[:249])
    for i, r in enumerate(rows, 1):
        cnt  = r["tips"]
        line = (f"{i}. @{r['from_username']} — {r['total']:g}g | "
                f"{cnt} tip{'s' if cnt != 1 else ''}")
        await _w(bot, user.id, line[:249])


# ---------------------------------------------------------------------------
# /tipreceiverlb  /topreceivers
# ---------------------------------------------------------------------------

async def handle_tipreceiverlb(bot: BaseBot, user: User) -> None:
    """/tipreceiverlb — top tip receivers."""
    rows = _get_top_receivers()
    hdr  = "<#FFD700>🏆 Top Tip Receivers<#FFFFFF>"

    if not rows:
        await _w(bot, user.id, f"{hdr}: No tips recorded yet.")
        return
    await _w(bot, user.id, hdr)
    for i, r in enumerate(rows, 1):
        cnt  = r["tips"]
        line = (f"{i}. {r['receiving_bot']} — {r['total']:g}g | "
                f"{cnt} tip{'s' if cnt != 1 else ''}")
        await _w(bot, user.id, line[:249])
