"""
modules/gold.py
---------------
Real Highrise gold tipping system.

Owner-only commands:
  /goldtip <user> <amt>
  /goldrefund <user> <amt> [reason]
  /goldrain <amt> [count]
  /goldrainall <amt>
  /goldwallet
  /goldtips
  /goldtx <user>
  /pendinggold
  /confirmgoldtip <code>
  /setgoldrainstaff on/off
  /setgoldrainmax <amt>

Rules:
- Only owners may send gold (admins/managers/mods cannot).
- tip_user is called once per gold-bar denomination.
- Exact denomination decomposition; rejects non-exact amounts.
- Totals >= confirm_above (default 100g) require /confirmgoldtip.
- /goldrainall always requires /confirmgoldtip.
- Confirmations expire after 60 seconds.
- Every transaction is logged to gold_transactions DB table.
"""

import asyncio
import random
import string
import time
import uuid
from typing import Optional

import database as db
from modules.permissions import (
    is_owner, is_admin, is_manager, is_moderator,
)

# ---------------------------------------------------------------------------
# Gold denomination table — (gold_value, sdk_label) largest first
# ---------------------------------------------------------------------------
_DENOMS: list[tuple[int, str]] = [
    (10_000, "gold_bar_10k"),
    (5_000,  "gold_bar_5000"),
    (1_000,  "gold_bar_1k"),
    (500,    "gold_bar_500"),
    (100,    "gold_bar_100"),
    (50,     "gold_bar_50"),
    (10,     "gold_bar_10"),
    (5,      "gold_bar_5"),
    (1,      "gold_bar_1"),
]


def decompose_gold(amount: int) -> Optional[list[str]]:
    """
    Return a flat list of denomination label strings that add up to exactly
    `amount` gold using the fewest bars, or None if the amount cannot be formed.
    """
    if amount <= 0:
        return None
    bars: list[str] = []
    remaining = amount
    for value, label in _DENOMS:
        count = remaining // value
        if count:
            bars.extend([label] * count)
            remaining -= value * count
    if remaining != 0:
        return None
    return bars


# ---------------------------------------------------------------------------
# Room user cache  { username_lower → (user_id, display_username) }
# ---------------------------------------------------------------------------
_room_cache: dict[str, tuple[str, str]] = {}
_bot_user_id: str = ""
_bot_username: str = ""
# Other bots discovered in room — excluded from gold rain targets
_known_bot_ids: set[str] = set()


def set_bot_identity(user_id: str, username: str = "") -> None:
    """Called from on_start so we know which user to exclude from rain."""
    global _bot_user_id, _bot_username
    _bot_user_id = user_id
    _bot_username = username


def get_bot_user_id() -> str:
    """Return the bot's own Highrise user ID (set at on_start)."""
    return _bot_user_id


def add_to_room_cache(user_id: str, username: str) -> None:
    _room_cache[username.lower()] = (user_id, username)


def remove_from_room_cache(user_id: str) -> None:
    to_remove = [k for k, (uid, _) in _room_cache.items() if uid == user_id]
    for k in to_remove:
        del _room_cache[k]


async def refresh_room_cache(bot) -> None:
    """Fetch the live room user list and rebuild the cache."""
    try:
        resp = await bot.highrise.get_room_users()
        if hasattr(resp, "content"):
            _room_cache.clear()
            for ru, _ in resp.content:
                _room_cache[ru.username.lower()] = (ru.id, ru.username)
    except Exception as exc:
        print(f"[GOLD] refresh_room_cache error: {exc}")


def _get_eligible_players(include_staff: bool = True) -> list[tuple[str, str]]:
    """Return [(user_id, username)] eligible for gold rain.
    Excludes: this bot, any other known bots, and (optionally) staff roles.
    """
    result: list[tuple[str, str]] = []
    for _key, (uid, uname) in _room_cache.items():
        if uid == _bot_user_id:
            continue
        if uid in _known_bot_ids:
            continue
        if not include_staff:
            if (is_owner(uname) or is_admin(uname)
                    or is_manager(uname) or is_moderator(uname)):
                continue
        result.append((uid, uname))
    return result


def _register_bot_id(user_id: str, username: str) -> None:
    """Mark a user as a bot so they are excluded from future gold rain targets."""
    _known_bot_ids.add(user_id)
    # Also evict from room cache so /goldraineligible doesn't show them
    key = username.lower()
    if key in _room_cache and _room_cache[key][0] == user_id:
        del _room_cache[key]
    print(f"[GOLD] Detected bot in room: @{username} (id={user_id}) — excluded from gold rain.")


# ---------------------------------------------------------------------------
# Pending confirmations  { code → {"action", "context", "expires"} }
# ---------------------------------------------------------------------------
_pending: dict[str, dict] = {}
_CONFIRM_TTL = 60  # seconds


def _gen_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


def _store_pending(action: str, context: dict) -> str:
    code = _gen_code()
    _pending[code] = {
        "action":  action,
        "context": context,
        "expires": time.time() + _CONFIRM_TTL,
    }
    return code


def _pop_pending(code: str) -> Optional[dict]:
    entry = _pending.pop(code.upper(), None)
    if entry is None:
        return None
    if time.time() > entry["expires"]:
        return None        # expired; already removed from dict
    return entry


def _clean_expired() -> None:
    now = time.time()
    expired = [k for k, v in _pending.items() if now > v["expires"]]
    for k in expired:
        del _pending[k]


# ---------------------------------------------------------------------------
# Gold settings helpers
# ---------------------------------------------------------------------------
def _get_setting(key: str, default: str) -> str:
    val = db.get_gold_setting(key)
    return val if val is not None else default


def _include_staff() -> bool:
    return _get_setting("goldrain_include_staff", "true") == "true"


def _confirm_above() -> int:
    return int(_get_setting("goldrain_require_confirm_above", "100"))


def _max_total() -> int:
    return int(_get_setting("goldrain_max_total", "1000"))


# ---------------------------------------------------------------------------
# Core send helper
# ---------------------------------------------------------------------------
async def _send_gold_bars(bot, user_id: str, bars: list[str]) -> tuple[bool, str]:
    """
    Call tip_user once per bar.
    Returns (True, "")           — full success
            (False, "insufficient_funds") — out of gold
            (False, "bot_user")           — target is another bot (skip silently)
            (False, <reason>)             — other failure
    """
    for bar in bars:
        try:
            result = await bot.highrise.tip_user(user_id, bar)
            if result == "insufficient_funds":
                return False, "insufficient_funds"
            if hasattr(result, "result") and result.result == "insufficient_funds":
                return False, "insufficient_funds"
            # Detect "Bots can't tip other bots" from Error object or string
            result_str = str(result)
            if "can't tip other bots" in result_str.lower() or "bots can" in result_str.lower():
                return False, "bot_user"
            if not isinstance(result, str):
                return False, result_str
        except Exception as exc:
            exc_str = str(exc)
            if "can't tip other bots" in exc_str.lower() or "bots can" in exc_str.lower():
                return False, "bot_user"
            return False, exc_str
    return True, ""


# ---------------------------------------------------------------------------
# Internal executors  (called after confirmation or directly when < threshold)
# ---------------------------------------------------------------------------
async def _execute_goldtip(
    bot, user, target_id: str, target_display: str,
    amount: int, bars: list[str], reason: str, action_type: str,
) -> None:
    ok, err = await _send_gold_bars(bot, target_id, bars)
    denom_str = ",".join(bars)
    if ok:
        await bot.highrise.chat(f"✅ Sent {amount}g to @{target_display}.")
        db.log_gold_tx(
            action_type, user.username, target_display, target_id,
            amount, reason, "success", denom_str, "", "",
        )
        print(f"[GOLD] {action_type}: @{user.username} → @{target_display} {amount}g OK")
    else:
        if err == "insufficient_funds":
            msg = "❌ Insufficient gold. Tip failed."
        else:
            msg = f"❌ Tip failed: {err[:80]}"
        await bot.highrise.send_whisper(user.id, msg)
        db.log_gold_tx(
            action_type, user.username, target_display, target_id,
            amount, reason, "failed", denom_str, "", err,
        )
        print(f"[GOLD] {action_type} FAILED: @{user.username} → @{target_display} — {err}")


async def _execute_goldrain(
    bot, user, amount: int, bars: list[str], chosen: list[tuple[str, str]],
    action_type: str = "goldrain",
) -> None:
    batch_id = str(uuid.uuid4())[:8]
    sent = failed = skipped = 0
    for uid, uname in chosen:
        ok, err = await _send_gold_bars(bot, uid, bars)
        if err == "bot_user":
            _register_bot_id(uid, uname)
            skipped += 1
            continue
        status = "success" if ok else "failed"
        db.log_gold_tx(
            action_type, user.username, uname, uid,
            amount, "", status, ",".join(bars), batch_id, err,
        )
        if ok:
            sent += 1
            if len(chosen) <= 3:
                await bot.highrise.chat(f"🌧️ {amount}g sent to @{uname}!")
        else:
            failed += 1
            print(
                f"[GOLD] {action_type} FAILED → @{uname} (id={uid}) "
                f"amount={amount}g bars={bars} err={err!r}"
            )
    real_targets = len(chosen) - skipped
    if real_targets == 0:
        await bot.highrise.send_whisper(user.id, "No eligible players to rain on (only bots found).")
        return
    if failed:
        summary = f"🌧️ Gold rain done: {sent} sent, {failed} failed. Check console."
    else:
        summary = f"🌧️ Gold rain done: {sent} players got {amount}g."
    await bot.highrise.chat(summary[:249])


async def _execute_goldrainall(
    bot, sender_username: str, sender_id: str,
    amount: int, bars: list[str], eligible: list[tuple[str, str]],
    action_type: str = "goldrainall",
) -> None:
    batch_id = str(uuid.uuid4())[:8]
    sent = failed = skipped = 0
    for uid, uname in eligible:
        ok, err = await _send_gold_bars(bot, uid, bars)
        if err == "bot_user":
            _register_bot_id(uid, uname)
            skipped += 1
            continue
        status = "success" if ok else "failed"
        db.log_gold_tx(
            action_type, sender_username, uname, uid,
            amount, "", status, ",".join(bars), batch_id, err,
        )
        if ok:
            sent += 1
        else:
            failed += 1
            print(
                f"[GOLD] {action_type} FAILED → @{uname} (id={uid}) "
                f"amount={amount}g bars={bars} err={err!r}"
            )
    if failed:
        summary = f"🌧️ Gold rain done: {sent} sent, {failed} failed. Check console."
    else:
        summary = f"🌧️ Gold rain done: {sent} players got {amount}g."
    await bot.highrise.chat(summary[:249])


# ---------------------------------------------------------------------------
# /goldtip <username> <amount>
# /goldrefund <username> <amount> [reason]
# ---------------------------------------------------------------------------
async def handle_goldtip(bot, user, args: list[str], action_type: str = "goldtip") -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can send gold.")
        db.log_gold_tx(
            "denied", user.username,
            args[1].lstrip("@") if len(args) > 1 else "?",
            "", 0, "", "denied", "", "", "Not owner",
        )
        return

    if len(args) < 3:
        await bot.highrise.send_whisper(
            user.id, f"Usage: /{action_type} <username> <amount>"
        )
        return

    target_raw = args[1].lstrip("@")
    try:
        amount = int(args[2])
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Amount must be a whole number.")
        return
    if amount <= 0:
        await bot.highrise.send_whisper(user.id, "Amount must be positive.")
        return

    await refresh_room_cache(bot)
    entry = _room_cache.get(target_raw.lower())
    if not entry:
        await bot.highrise.send_whisper(user.id, "Player must be in room for gold tip.")
        db.log_gold_tx(
            action_type, user.username, target_raw, "",
            amount, "", "failed", "", "", "Not in room",
        )
        return

    target_id, target_display = entry
    if target_id == _bot_user_id:
        await bot.highrise.send_whisper(user.id, "Cannot tip the bot.")
        return

    bars = decompose_gold(amount)
    if bars is None:
        await bot.highrise.send_whisper(
            user.id, f"Cannot form exact {amount}g from valid denominations."
        )
        return

    reason = " ".join(args[3:]) if len(args) > 3 else ""

    if amount >= _confirm_above():
        code = _store_pending(action_type, {
            "target_id":      target_id,
            "target":         target_display,
            "amount":         amount,
            "bars":           bars,
            "reason":         reason,
            "sender":         user.username,
            "sender_id":      user.id,
        })
        await bot.highrise.send_whisper(
            user.id,
            f"Confirm {amount}g to @{target_display}: /confirmgoldtip {code} (60s)"
        )
        return

    await _execute_goldtip(
        bot, user, target_id, target_display, amount, bars, reason, action_type
    )


async def handle_goldrefund(bot, user, args: list[str]) -> None:
    await handle_goldtip(bot, user, args, action_type="goldrefund")


# ---------------------------------------------------------------------------
# /goldrain <amount> [count]
# ---------------------------------------------------------------------------
async def handle_goldrain(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can send gold.")
        return

    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: /goldrain <amount> [count]")
        return

    try:
        amount = int(args[1])
        count  = int(args[2]) if len(args) > 2 else 1
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Amount and count must be whole numbers.")
        return

    if amount <= 0 or count <= 0:
        await bot.highrise.send_whisper(user.id, "Amount and count must be positive.")
        return

    bars = decompose_gold(amount)
    if bars is None:
        await bot.highrise.send_whisper(
            user.id, f"Cannot form exact {amount}g from valid denominations."
        )
        return

    total = amount * count
    if total > _max_total():
        await bot.highrise.send_whisper(
            user.id, f"Total {total}g exceeds the {_max_total()}g rain limit."
        )
        return

    await refresh_room_cache(bot)
    eligible = _get_eligible_players(_include_staff())

    if not eligible:
        await bot.highrise.send_whisper(user.id, "Not enough eligible players in room.")
        return

    if count > len(eligible):
        await bot.highrise.send_whisper(
            user.id, f"Only {len(eligible)} eligible players available."
        )
        count = len(eligible)
        total = amount * count

    chosen = random.sample(eligible, count)
    await _execute_goldrain(bot, user, amount, bars, chosen)


# ---------------------------------------------------------------------------
# /goldrainall <amount>
# ---------------------------------------------------------------------------
async def handle_goldrainall(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can send gold.")
        return

    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: /goldrainall <amount>")
        return

    try:
        amount = int(args[1])
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Amount must be a whole number.")
        return
    if amount <= 0:
        await bot.highrise.send_whisper(user.id, "Amount must be positive.")
        return

    bars = decompose_gold(amount)
    if bars is None:
        await bot.highrise.send_whisper(
            user.id, f"Cannot form exact {amount}g from valid denominations."
        )
        return

    await refresh_room_cache(bot)
    eligible = _get_eligible_players(_include_staff())

    if not eligible:
        await bot.highrise.send_whisper(user.id, "Not enough eligible players in room.")
        return

    total = amount * len(eligible)
    if total > _max_total():
        await bot.highrise.send_whisper(
            user.id,
            f"Total {total}g ({len(eligible)} players) exceeds the {_max_total()}g limit."
        )
        return

    await bot.highrise.send_whisper(
        user.id, f"🌧️ Sending {amount}g to {len(eligible)} players..."
    )
    await _execute_goldrainall(bot, user.username, user.id, amount, bars, eligible)


# ---------------------------------------------------------------------------
# /goldwallet
# ---------------------------------------------------------------------------
async def handle_goldwallet(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can view the gold wallet.")
        return
    try:
        resp = await bot.highrise.get_wallet()
        if hasattr(resp, "content"):
            gold    = next((c.amount for c in resp.content if c.type == "gold"), 0)
            bubbles = next((c.amount for c in resp.content if c.type == "bubbles"), 0)
            await bot.highrise.send_whisper(
                user.id,
                f"💰 Bot Wallet — Gold: {gold}g | Bubbles: {bubbles}"
            )
        else:
            await bot.highrise.send_whisper(user.id, "Could not fetch wallet.")
    except Exception as exc:
        await bot.highrise.send_whisper(user.id, f"Wallet error: {str(exc)[:80]}")


# ---------------------------------------------------------------------------
# /goldtips  (last 8 transactions)
# ---------------------------------------------------------------------------
async def handle_goldtips(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can view gold logs.")
        return
    rows = db.get_gold_transactions(limit=8)
    if not rows:
        await bot.highrise.send_whisper(user.id, "No gold transactions yet.")
        return
    lines = []
    for r in rows:
        ts = r["timestamp"][:10]
        lines.append(
            f"{ts} {r['action_type']} {r['amount_gold']}g"
            f"→@{r['receiver_username']} [{r['status']}]"
        )
    await bot.highrise.send_whisper(
        user.id, "📋 Recent gold txs:\n" + "\n".join(lines[:6])
    )


# ---------------------------------------------------------------------------
# /goldtx <username>
# ---------------------------------------------------------------------------
async def handle_goldtx(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can view gold logs.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: /goldtx <username>")
        return
    target = args[1].lstrip("@")
    rows = db.get_gold_transactions_by_user(target, limit=5)
    if not rows:
        await bot.highrise.send_whisper(user.id, f"No gold txs found for @{target}.")
        return
    lines = []
    for r in rows:
        ts = r["timestamp"][:10]
        lines.append(
            f"{ts} {r['action_type']} {r['amount_gold']}g [{r['status']}]"
        )
    await bot.highrise.send_whisper(
        user.id, f"📋 @{target} gold txs:\n" + "\n".join(lines)
    )


# ---------------------------------------------------------------------------
# /pendinggold
# ---------------------------------------------------------------------------
async def handle_pendinggold(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can view pending gold.")
        return
    _clean_expired()
    if not _pending:
        await bot.highrise.send_whisper(user.id, "No pending gold confirmations.")
        return
    lines = []
    for code, entry in _pending.items():
        ctx    = entry["context"]
        amount = ctx.get("amount", "?")
        lines.append(f"{code}: {entry['action']} {amount}g")
    await bot.highrise.send_whisper(
        user.id, "⏳ Pending confirmations:\n" + "\n".join(lines[:5])
    )


# ---------------------------------------------------------------------------
# /confirmgoldtip <code>
# ---------------------------------------------------------------------------
async def handle_confirmgoldtip(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can confirm gold tips.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: /confirmgoldtip <code>")
        return

    code  = args[1].upper()
    entry = _pop_pending(code)
    if not entry:
        await bot.highrise.send_whisper(user.id, "Invalid or expired confirmation code.")
        return

    action = entry["action"]
    ctx    = entry["context"]

    if action in ("goldtip", "goldrefund"):
        await _execute_goldtip(
            bot, user,
            ctx["target_id"], ctx["target"],
            ctx["amount"], ctx["bars"],
            ctx.get("reason", ""), action,
        )
    elif action == "goldrain":
        await _execute_goldrain(bot, user, ctx["amount"], ctx["bars"], ctx["chosen"])
    elif action == "goldrainall":
        await _execute_goldrainall(
            bot,
            ctx["sender"], ctx["sender_id"],
            ctx["amount"], ctx["bars"], ctx["eligible"],
        )
    else:
        await bot.highrise.send_whisper(user.id, "Unknown action type.")


# ---------------------------------------------------------------------------
# /setgoldrainstaff on/off
# ---------------------------------------------------------------------------
async def handle_setgoldrainstaff(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can change gold settings.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await bot.highrise.send_whisper(user.id, "Usage: /setgoldrainstaff on/off")
        return
    val   = "true" if args[1].lower() == "on" else "false"
    state = "included in" if val == "true" else "excluded from"
    db.set_gold_setting("goldrain_include_staff", val)
    await bot.highrise.send_whisper(user.id, f"✅ Staff are now {state} gold rain.")


# ---------------------------------------------------------------------------
# /setgoldrainmax <amount>
# ---------------------------------------------------------------------------
async def handle_setgoldrainmax(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can change gold settings.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: /setgoldrainmax <amount>")
        return
    try:
        val = int(args[1])
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Amount must be a whole number.")
        return
    if val < 10:
        await bot.highrise.send_whisper(user.id, "Minimum max total is 10g.")
        return
    db.set_gold_setting("goldrain_max_total", str(val))
    await bot.highrise.send_whisper(user.id, f"✅ Max gold rain total set to {val}g.")


# ---------------------------------------------------------------------------
# /goldraineligible
# ---------------------------------------------------------------------------
async def handle_goldraineligible(bot, user, args: list[str]) -> None:
    """Show who is currently eligible for gold rain."""
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can use this.")
        return
    await refresh_room_cache(bot)
    eligible = _get_eligible_players(_include_staff())
    if not eligible:
        await bot.highrise.send_whisper(user.id, "No eligible players in room.")
        return
    names = ", ".join(f"@{uname}" for _, uname in eligible)
    msg = f"Eligible ({len(eligible)}): {names}"
    await bot.highrise.send_whisper(user.id, msg[:249])


# ---------------------------------------------------------------------------
# /goldhelp
# ---------------------------------------------------------------------------
async def handle_goldhelp(bot, user, args: list[str]) -> None:
    msg = (
        "👑 Gold Cmds (Owner only)\n"
        "/goldtip <user> <amt>\n"
        "/goldrefund <user> <amt> [reason]\n"
        "/goldrain <amt> [count]  /goldrainall <amt>\n"
        "/goldraineligible\n"
        "/setgoldrainstaff on|off  /setgoldrainmax <amt>\n"
        "/goldwallet  /goldtips  /goldtx <user>"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])
