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
    is_owner, is_admin, is_manager, is_moderator, can_moderate,
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

# ---------------------------------------------------------------------------
# Known bot username list + eligibility helper
# ---------------------------------------------------------------------------
_KNOWN_BOT_USERNAMES: frozenset[str] = frozenset({
    # Spec-listed names
    "emceebot", "bankerbot", "greatestprospector", "masterangler",
    "blackjackbot", "pokerbot", "securitybot", "acesinatra",
    # Actual running bot usernames observed in room
    "bankingbot", "chipsoprano", "keanushield", "dj_dudu",
    # Host bot renamed variants
    "chilltopia", "chilltopiamc",
})


def _goldtip_bots_allowed() -> bool:
    """Return True when owner has enabled bot tipping for testing."""
    try:
        return db.get_room_setting("goldtip_allow_bots", "0") == "1"
    except Exception:
        return False


def is_gold_tip_eligible_user(user_id: str, username: str) -> bool:
    """
    Return True only for real players eligible to receive gold tips/rain.
    Excludes: this bot's own ID, runtime-detected bot IDs, known bot usernames,
    and any username ending with 'bot'.
    When goldtip_allow_bots=1 (owner testing mode), always returns True.
    """
    if user_id and user_id == _bot_user_id:
        return False
    if user_id and user_id in _known_bot_ids:
        return False
    if _goldtip_bots_allowed():
        return True
    ul = username.lower()
    if ul in _KNOWN_BOT_USERNAMES:
        return False
    if ul.endswith("bot"):
        return False
    return True


def _count_bots_in_room() -> int:
    """Return number of room cache entries that fail the eligibility check."""
    return sum(
        1 for _, (uid, uname) in _room_cache.items()
        if not is_gold_tip_eligible_user(uid, uname)
    )


def _get_goldtip_all_pace_interval() -> int:
    """Get pace interval (seconds) for /goldtip all from Gold Rain settings."""
    try:
        pace = (db.get_gold_rain_setting("goldrain_pace") or "normal").lower()
        if pace == "slow":
            return 15
        if pace == "party":
            return 1
        if pace == "custom":
            raw = db.get_gold_rain_setting("goldrain_custom_interval") or "5"
            return max(1, min(300, int(raw)))
    except Exception:
        pass
    return 5


def set_bot_identity(user_id: str, username: str = "") -> None:
    """Called from on_start so we know which user to exclude from rain."""
    global _bot_user_id, _bot_username
    _bot_user_id = user_id
    if username:
        _bot_username = username


def get_bot_user_id() -> str:
    """Return the bot's own Highrise user ID (set at on_start)."""
    return _bot_user_id


def get_bot_username() -> str:
    """Return the bot's own Highrise username (populated after on_start room fetch)."""
    return _bot_username


def add_to_room_cache(user_id: str, username: str) -> None:
    _room_cache[username.lower()] = (user_id, username)


def remove_from_room_cache(user_id: str) -> None:
    to_remove = [k for k, (uid, _) in _room_cache.items() if uid == user_id]
    for k in to_remove:
        del _room_cache[k]


async def refresh_room_cache(bot) -> None:
    """Fetch the live room user list and rebuild the cache.
    Also resolves the bot's own username if not yet known.
    """
    global _bot_username
    try:
        resp = await bot.highrise.get_room_users()
        if hasattr(resp, "content"):
            _room_cache.clear()
            for ru, _ in resp.content:
                _room_cache[ru.username.lower()] = (ru.id, ru.username)
                # Auto-discover bot's own username by matching user ID
                if ru.id == _bot_user_id and not _bot_username:
                    _bot_username = ru.username
                    print(f"[GOLD] Bot username resolved: {_bot_username}")
    except Exception as exc:
        print(f"[GOLD] refresh_room_cache error: {exc}")


def _get_eligible_players(include_staff: bool = True) -> list[tuple[str, str]]:
    """Return [(user_id, username)] eligible for gold rain.
    Excludes: this bot, all known bots (by ID and username), and optionally staff.
    """
    result: list[tuple[str, str]] = []
    for _key, (uid, uname) in _room_cache.items():
        if not is_gold_tip_eligible_user(uid, uname):
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
# Shared automated reward helper  (used by first-find, future reward systems)
# ---------------------------------------------------------------------------
async def process_gold_tip_reward(
    bot,
    target_user_id: str,
    target_username: str,
    gold_amount: int,
    reason: str = "",
    source: str = "first_find",
    created_by: str = "system",
) -> tuple[str, str]:
    """
    Send gold to a player automatically (no confirmation, no permission check).
    Designed for automated reward systems such as First Find.

    Returns:
        ("paid_gold",           "")        — tip sent successfully
        ("pending_manual_gold", human_err) — tip failed; manual tip needed
        ("failed",              human_err) — hard error (logged)

    Notes:
        - BankerBot must have sufficient gold in its wallet.
        - Does NOT require the player to be in the room (SDK decides).
        - Logs every attempt to gold_transactions via db.log_gold_tx.
    """
    if gold_amount < 1:
        return "pending_manual_gold", "Amount below minimum (1 gold)"

    bars = decompose_gold(gold_amount)
    if bars is None:
        return "pending_manual_gold", f"Cannot form exact {gold_amount}g from denominations"

    try:
        ok, err = await _send_gold_bars(bot, target_user_id, bars)
    except Exception as exc:
        err_msg = str(exc)[:120]
        db.log_gold_tx(
            source, created_by, target_username, target_user_id,
            gold_amount, reason, "failed", ",".join(bars), "", err_msg,
        )
        return "pending_manual_gold", err_msg

    if ok:
        db.log_gold_tx(
            source, created_by, target_username, target_user_id,
            gold_amount, reason, "success", ",".join(bars), "", "",
        )
        return "paid_gold", ""
    else:
        if err == "insufficient_funds":
            human_err = "Insufficient bot gold"
        elif err == "bot_user":
            human_err = "Target is a bot"
        else:
            human_err = err[:80]
        db.log_gold_tx(
            source, created_by, target_username, target_user_id,
            gold_amount, reason, "failed", ",".join(bars), "", err,
        )
        return "pending_manual_gold", human_err


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
        await bot.highrise.chat(f"💰 Gold Tip Sent — @{target_display} received {amount}g.")
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
        await bot.highrise.send_whisper(
            user.id,
            "🔒 Owner only.\nGold tipping can only be used by the owner.",
        )
        db.log_gold_tx(
            "denied", user.username,
            args[1].lstrip("@") if len(args) > 1 else "?",
            "", 0, "", "denied", "", "", "Not owner",
        )
        return

    # /goldtip all <amount> [instant] subcommand
    if len(args) >= 2 and args[1].lower() == "all":
        await _handle_goldtip_all(bot, user, args)
        return

    if len(args) < 3:
        await bot.highrise.send_whisper(
            user.id,
            f"Usage: !{action_type} <username> <amount>\n"
            "Or: /goldtip all <amount>",
        )
        return

    target_raw = args[1].lstrip("@")
    try:
        amount = int(args[2])
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Amount must be a whole number.")
        return
    if amount < 1:
        await bot.highrise.send_whisper(user.id, "💰 Gold Tip — Minimum tip is 1 gold.")
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

    # Block direct bot tip when bot tipping is disabled
    if not is_gold_tip_eligible_user(target_id, target_raw):
        if not _goldtip_bots_allowed():
            await bot.highrise.send_whisper(
                user.id,
                "🚫 Bot tipping is disabled.\n"
                "Gold can only be tipped to real players.",
            )
            try:
                db.log_gold_tx(
                    "blocked_bot_tip", user.username, target_raw, target_id,
                    amount, "", "blocked_bot", "", "", "Bot tipping disabled",
                )
            except Exception:
                pass
            return

    bars = decompose_gold(amount)
    if bars is None:
        await bot.highrise.send_whisper(
            user.id, f"Cannot form exact {amount}g from valid denominations."
        )
        return

    reason = " ".join(args[3:]) if len(args) > 3 else ""

    await _execute_goldtip(
        bot, user, target_id, target_display, amount, bars, reason, action_type
    )


async def handle_goldrefund(bot, user, args: list[str]) -> None:
    await handle_goldtip(bot, user, args, action_type="goldrefund")


# ---------------------------------------------------------------------------
# /goldtipbots [on|off]  — owner-only bot tipping toggle
# ---------------------------------------------------------------------------

async def handle_goldtipbots(bot, user, args: list[str]) -> None:
    """/goldtipbots [on|off] — view or toggle bot tipping (owner only)."""
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Owner only.")
        return

    if len(args) < 2:
        allowed = _goldtip_bots_allowed()
        status = "ON" if allowed else "OFF"
        if allowed:
            msg = (f"🤖 Gold Tip Bots\nBot tipping: {status}\n"
                   "Owner testing mode enabled. Bots may be tipped.")
        else:
            msg = (f"🤖 Gold Tip Bots\nBot tipping: {status}\n"
                   "Bots are excluded from Gold Tip and Gold Rain.")
        await bot.highrise.send_whisper(user.id, msg[:249])
        return

    val = args[1].lower()
    if val not in ("on", "off"):
        await bot.highrise.send_whisper(user.id, "Usage: !goldtipbots on|off")
        return

    enabled = val == "on"
    db.set_room_setting("goldtip_allow_bots", "1" if enabled else "0")
    if enabled:
        msg = "🤖 Bot tipping: ON\nOwner testing mode enabled. Bots may be tipped."
    else:
        msg = "🤖 Bot tipping: OFF\nBots excluded from Gold Tip and Gold Rain."
    await bot.highrise.send_whisper(user.id, msg[:249])


# ---------------------------------------------------------------------------
# /goldtip all <amount> [instant]  — tip every real player in room
# ---------------------------------------------------------------------------

async def _handle_goldtip_all(bot, user, args: list[str]) -> None:
    """
    Handle /goldtip all <amount> [instant].
    Tips every eligible real player currently in the room.
    Uses Gold Rain pace interval between tips (unless 'instant' flag is given).
    """
    if not is_owner(user.username):
        await bot.highrise.send_whisper(
            user.id,
            "🔒 Owner only.\nMass gold tipping can only be used by the owner.",
        )
        return

    if len(args) < 3:
        await bot.highrise.send_whisper(
            user.id,
            "Usage: !goldtip all <amount>\nExample: /goldtip all 1",
        )
        return

    try:
        amount = int(args[2])
        if amount < 1:
            raise ValueError
    except ValueError:
        await bot.highrise.send_whisper(
            user.id, "🚫 Invalid amount.\nExample: /goldtip all 1",
        )
        return

    bars = decompose_gold(amount)
    if bars is None:
        await bot.highrise.send_whisper(
            user.id, f"Cannot form exact {amount}g from valid denominations.",
        )
        return

    instant = len(args) > 3 and args[3].lower() == "instant"

    # Refresh room, build eligible list with bot exclusion
    await refresh_room_cache(bot)
    eligible: list[tuple[str, str]] = []
    bots_excluded = 0
    seen_ids: set[str] = set()
    for _k, (uid, uname) in list(_room_cache.items()):
        if uid in seen_ids:
            continue
        seen_ids.add(uid)
        if not is_gold_tip_eligible_user(uid, uname):
            bots_excluded += 1
        else:
            eligible.append((uid, uname))

    if not eligible:
        await bot.highrise.send_whisper(
            user.id,
            f"🚫 Gold Tip All\nNo eligible real players found in the room.\n"
            f"Bots excluded: {bots_excluded}",
        )
        return

    total_gold = amount * len(eligible)
    max_gold = _max_total()
    if total_gold > max_gold:
        await bot.highrise.send_whisper(
            user.id,
            f"🚫 Gold Tip All Blocked\nTotal required: {total_gold}g\n"
            f"Max allowed: {max_gold}g\nUse a smaller amount.",
        )
        return

    pace_interval = 0 if instant else _get_goldtip_all_pace_interval()

    # Public start announcement
    try:
        ann = (
            f"💰 Gold Tip All Started!\nTarget: All players  Amount: {amount}g each\n"
            f"Eligible: {len(eligible)}  Bots Excluded: {bots_excluded}\n"
            f"Total: {total_gold}g"
        )
        await bot.highrise.chat(ann[:249])
    except Exception:
        pass

    paid = 0
    pending_manual = 0

    for i, (uid, uname) in enumerate(eligible):
        ok, err = await _send_gold_bars(bot, uid, bars)
        if err == "bot_user":
            _register_bot_id(uid, uname)
            bots_excluded += 1
            try:
                db.log_gold_tx(
                    "goldtip_all", user.username, uname, uid,
                    amount, "", "blocked_bot", ",".join(bars), "", "Target is a bot",
                )
            except Exception:
                pass
        elif ok:
            paid += 1
            try:
                db.log_gold_tx(
                    "goldtip_all", user.username, uname, uid,
                    amount, "", "success", ",".join(bars), "", "",
                )
            except Exception:
                pass
            try:
                await bot.highrise.chat(f"💸 Tipped @{uname} {amount}g gold!")
            except Exception:
                pass
        else:
            pending_manual += amount
            try:
                db.log_gold_tx(
                    "goldtip_all", user.username, uname, uid,
                    amount, "", "pending_manual", ",".join(bars), "", err[:80],
                )
            except Exception:
                pass
            try:
                await bot.highrise.chat(f"💸 Logged @{uname} for {amount}g gold!")
            except Exception:
                pass

        # Pace delay between tips (skip after last one)
        if pace_interval > 0 and i < len(eligible) - 1:
            try:
                await asyncio.sleep(pace_interval)
            except asyncio.CancelledError:
                break

    # Audit log entry
    try:
        db.log_gold_tx(
            "goldtip_all_audit", user.username, "[all]", "",
            paid * amount,
            f"eligible={len(eligible)} bots_excluded={bots_excluded} "
            f"paid={paid} pending={pending_manual // max(amount, 1)}",
            "complete" if pending_manual == 0 else "partial",
            "", "", "",
        )
    except Exception:
        pass

    # Whisper final summary only to the tipper
    summary = (
        f"💰 Gold Tip All Complete!\n"
        f"Players tipped: {paid}\n"
        f"Amount each: {amount}g\n"
        f"Total: {paid * amount}g\n"
        f"Bots excluded: {bots_excluded}\n"
        f"Failed: 0"
    )
    if pending_manual > 0:
        summary += f"\nPending manual: {pending_manual}g"
    await bot.highrise.send_whisper(user.id, summary[:249])


async def handle_tipall(bot, user, args: list[str]) -> None:
    """/tipall <amount> — alias for /goldtip all <amount>."""
    new_args = ["goldtip", "all"] + args[1:]
    await _handle_goldtip_all(bot, user, new_args)


async def handle_goldtipall(bot, user, args: list[str]) -> None:
    """/goldtipall <amount> — alias for /goldtip all <amount>."""
    new_args = ["goldtip", "all"] + args[1:]
    await _handle_goldtip_all(bot, user, new_args)


# ---------------------------------------------------------------------------
# /goldrain <amount> [count]
# ---------------------------------------------------------------------------
async def handle_goldrain(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can send gold.")
        return

    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: !goldrain <amount> [count]")
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
        await bot.highrise.send_whisper(user.id, "Usage: !goldrainall <amount>")
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
        await bot.highrise.send_whisper(user.id, "Usage: !goldtx <username>")
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
        await bot.highrise.send_whisper(user.id, "Usage: !confirmgoldtip <code>")
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
        await bot.highrise.send_whisper(user.id, "Usage: !setgoldrainstaff on/off")
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
        await bot.highrise.send_whisper(user.id, "Usage: !setgoldrainmax <amount>")
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
# Role / title / badge filter helpers
# ---------------------------------------------------------------------------
_VALID_ROLES = frozenset({
    "owner", "admin", "manager", "moderator", "staff", "player", "vip",
})


def _get_players_by_role(role: str) -> list[tuple[str, str]]:
    """
    Return [(uid, uname)] in room matching *role*.
    Excludes this bot and any known bots.
    Roles: owner | admin | manager | moderator | staff | player | vip
    """
    result: list[tuple[str, str]] = []
    for _key, (uid, uname) in _room_cache.items():
        if not is_gold_tip_eligible_user(uid, uname):
            continue
        if role == "owner":
            match = is_owner(uname)
        elif role == "admin":
            match = is_admin(uname)
        elif role == "manager":
            match = is_manager(uname)
        elif role == "moderator":
            match = is_moderator(uname)
        elif role == "staff":
            match = can_moderate(uname)
        elif role == "player":
            match = not can_moderate(uname) and not is_owner(uname)
        elif role == "vip":
            sub = db.get_subscriber(uname.lower())
            match = bool(sub and sub.get("subscribed"))
        else:
            match = False
        if match:
            result.append((uid, uname))
    return result


def _get_players_by_title(title_id: str) -> list[tuple[str, str]]:
    """Return [(uid, uname)] in room whose equipped_title_id matches (case-insensitive)."""
    result: list[tuple[str, str]] = []
    tid = title_id.lower().strip()
    for _key, (uid, uname) in _room_cache.items():
        if not is_gold_tip_eligible_user(uid, uname):
            continue
        info = db.get_equipped_ids(uid)
        if (info.get("title_id") or "").lower() == tid:
            result.append((uid, uname))
    return result


def _get_players_by_badge(badge_id: str) -> list[tuple[str, str]]:
    """Return [(uid, uname)] in room whose equipped_badge_id matches (case-insensitive)."""
    result: list[tuple[str, str]] = []
    bid = badge_id.lower().strip()
    for _key, (uid, uname) in _room_cache.items():
        if not is_gold_tip_eligible_user(uid, uname):
            continue
        info = db.get_equipped_ids(uid)
        if (info.get("badge_id") or "").lower() == bid:
            result.append((uid, uname))
    return result


async def _handle_targeted_rain(
    bot, user, eligible: list[tuple[str, str]],
    amount: int, bars: list[str], count: int | None,
    action_type: str, label: str,
) -> None:
    """
    Shared executor for role/vip/title/badge goldrain.
    count=None  → rain on every player in *eligible* list.
    count=N     → randomly sample N from *eligible* list.
    """
    if not eligible:
        await bot.highrise.send_whisper(user.id, f"No {label} players currently in room.")
        return

    if count is None:
        # Rain on the whole group
        total = amount * len(eligible)
        if total > _max_total():
            await bot.highrise.send_whisper(
                user.id,
                f"Total {total}g ({len(eligible)} players) exceeds the {_max_total()}g limit."
            )
            return
        await bot.highrise.send_whisper(
            user.id, f"🌧️ Sending {amount}g to {len(eligible)} {label} players..."
        )
        await _execute_goldrainall(
            bot, user.username, user.id, amount, bars, eligible, action_type
        )
    else:
        # Rain on a random subset
        if count > len(eligible):
            await bot.highrise.send_whisper(
                user.id, f"Only {len(eligible)} {label} players available."
            )
            count = len(eligible)
        total = amount * count
        if total > _max_total():
            await bot.highrise.send_whisper(
                user.id,
                f"Total {total}g ({count} players) exceeds the {_max_total()}g limit."
            )
            return
        chosen = random.sample(eligible, count)
        await _execute_goldrain(bot, user, amount, bars, chosen, action_type)


# ---------------------------------------------------------------------------
# /goldrainrole <role> <amount> [count]
# ---------------------------------------------------------------------------
async def handle_goldrainrole(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can send gold.")
        return
    if len(args) < 3:
        await bot.highrise.send_whisper(
            user.id,
            f"Usage: !goldrainrole <role> <amount> [count]\n"
            f"Roles: {', '.join(sorted(_VALID_ROLES))}"
        )
        return

    role = args[1].lower()
    if role not in _VALID_ROLES:
        await bot.highrise.send_whisper(
            user.id,
            f"Unknown role '{role}'. Valid: {', '.join(sorted(_VALID_ROLES))}"
        )
        return

    try:
        amount = int(args[2])
        count  = int(args[3]) if len(args) > 3 else None
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Amount and count must be whole numbers.")
        return

    if amount <= 0 or (count is not None and count <= 0):
        await bot.highrise.send_whisper(user.id, "Amount and count must be positive.")
        return

    bars = decompose_gold(amount)
    if bars is None:
        await bot.highrise.send_whisper(
            user.id, f"Cannot form exact {amount}g from valid denominations."
        )
        return

    await refresh_room_cache(bot)
    eligible = _get_players_by_role(role)
    await _handle_targeted_rain(
        bot, user, eligible, amount, bars, count,
        f"goldrain_{role}", role,
    )


# ---------------------------------------------------------------------------
# /goldrainvip <amount> [count]
# ---------------------------------------------------------------------------
async def handle_goldrainvip(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can send gold.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: !goldrainvip <amount> [count]")
        return

    try:
        amount = int(args[1])
        count  = int(args[2]) if len(args) > 2 else None
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Amount and count must be whole numbers.")
        return

    if amount <= 0 or (count is not None and count <= 0):
        await bot.highrise.send_whisper(user.id, "Amount and count must be positive.")
        return

    bars = decompose_gold(amount)
    if bars is None:
        await bot.highrise.send_whisper(
            user.id, f"Cannot form exact {amount}g from valid denominations."
        )
        return

    await refresh_room_cache(bot)
    eligible = _get_players_by_role("vip")
    await _handle_targeted_rain(
        bot, user, eligible, amount, bars, count, "goldrain_vip", "VIP",
    )


# ---------------------------------------------------------------------------
# /goldraintitle <title_id> <amount> [count]
# ---------------------------------------------------------------------------
async def handle_goldraintitle(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can send gold.")
        return
    if len(args) < 3:
        await bot.highrise.send_whisper(
            user.id, "Usage: !goldraintitle <title_id> <amount> [count]"
        )
        return

    title_id = args[1].lower()
    try:
        amount = int(args[2])
        count  = int(args[3]) if len(args) > 3 else None
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Amount and count must be whole numbers.")
        return

    if amount <= 0 or (count is not None and count <= 0):
        await bot.highrise.send_whisper(user.id, "Amount and count must be positive.")
        return

    bars = decompose_gold(amount)
    if bars is None:
        await bot.highrise.send_whisper(
            user.id, f"Cannot form exact {amount}g from valid denominations."
        )
        return

    await refresh_room_cache(bot)
    eligible = _get_players_by_title(title_id)
    await _handle_targeted_rain(
        bot, user, eligible, amount, bars, count,
        "goldrain_title", f"[{title_id}] title",
    )


# ---------------------------------------------------------------------------
# /goldrainbadge <badge_id> <amount> [count]
# ---------------------------------------------------------------------------
async def handle_goldrainbadge(bot, user, args: list[str]) -> None:
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can send gold.")
        return
    if len(args) < 3:
        await bot.highrise.send_whisper(
            user.id, "Usage: !goldrainbadge <badge_id> <amount> [count]"
        )
        return

    badge_id = args[1].lower()
    try:
        amount = int(args[2])
        count  = int(args[3]) if len(args) > 3 else None
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Amount and count must be whole numbers.")
        return

    if amount <= 0 or (count is not None and count <= 0):
        await bot.highrise.send_whisper(user.id, "Amount and count must be positive.")
        return

    bars = decompose_gold(amount)
    if bars is None:
        await bot.highrise.send_whisper(
            user.id, f"Cannot form exact {amount}g from valid denominations."
        )
        return

    await refresh_room_cache(bot)
    eligible = _get_players_by_badge(badge_id)
    await _handle_targeted_rain(
        bot, user, eligible, amount, bars, count,
        "goldrain_badge", f"{badge_id} badge",
    )


# ---------------------------------------------------------------------------
# /goldrainlist <type> [id]
# ---------------------------------------------------------------------------
async def handle_goldrainlist(bot, user, args: list[str]) -> None:
    """
    Preview who would receive a targeted gold rain without sending anything.
    Usage:
      /goldrainlist role <role>
      /goldrainlist title <title_id>
      /goldrainlist badge <badge_id>
      /goldrainlist vip
    """
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Only owners can use this.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(
            user.id,
            "Usage: !goldrainlist role <role> | title <id> | badge <id> | vip"
        )
        return

    list_type = args[1].lower()
    await refresh_room_cache(bot)

    if list_type == "vip":
        eligible = _get_players_by_role("vip")
        label = "VIP"
    elif list_type == "role":
        if len(args) < 3:
            await bot.highrise.send_whisper(
                user.id, f"Usage: !goldrainlist role <role>\nRoles: {', '.join(sorted(_VALID_ROLES))}"
            )
            return
        role = args[2].lower()
        if role not in _VALID_ROLES:
            await bot.highrise.send_whisper(
                user.id, f"Unknown role '{role}'. Valid: {', '.join(sorted(_VALID_ROLES))}"
            )
            return
        eligible = _get_players_by_role(role)
        label = role
    elif list_type == "title":
        if len(args) < 3:
            await bot.highrise.send_whisper(user.id, "Usage: !goldrainlist title <title_id>")
            return
        eligible = _get_players_by_title(args[2])
        label = f"[{args[2]}] title"
    elif list_type == "badge":
        if len(args) < 3:
            await bot.highrise.send_whisper(user.id, "Usage: !goldrainlist badge <badge_id>")
            return
        eligible = _get_players_by_badge(args[2])
        label = f"{args[2]} badge"
    else:
        await bot.highrise.send_whisper(
            user.id, "Type must be: role | title | badge | vip"
        )
        return

    if not eligible:
        await bot.highrise.send_whisper(user.id, f"No {label} players currently in room.")
        return
    names = ", ".join(f"@{uname}" for _, uname in eligible)
    msg = f"{label} ({len(eligible)}): {names}"
    await bot.highrise.send_whisper(user.id, msg[:249])


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
    bots_excluded = _count_bots_in_room()
    if not eligible:
        await bot.highrise.send_whisper(
            user.id,
            f"🌧️ Gold Rain Eligible\nNo eligible players in room.\n"
            f"Bots excluded: {bots_excluded}",
        )
        return
    names = ", ".join(f"@{uname}" for _, uname in eligible)
    msg = (
        f"🌧️ Gold Rain Eligible\n"
        f"Players: {len(eligible)}  Bots excluded: {bots_excluded}\n" + names
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


# ---------------------------------------------------------------------------
# /goldhelp
# ---------------------------------------------------------------------------
async def handle_goldhelp(bot, user, args: list[str]) -> None:
    msg = (
        "👑 Gold Cmds (Owner)\n"
        "/goldtip <user> <amt>  /goldtip all <amt>\n"
        "/tipall <amt>  /goldrefund <user> <amt>\n"
        "/goldtipbots on|off  /goldrain <amt> [count]\n"
        "/goldrainall <amt>  /goldrainrole <role> <amt>\n"
        "/goldrainvip <amt>  /goldraineligible\n"
        "/setgoldrainstaff on|off  /setgoldrainmax <amt>"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])
