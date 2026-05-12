"""
modules/tips.py
---------------
Highrise gold tip → in-game coins conversion.

ROOT CAUSES (both fixed):
  1. isinstance(tip, CurrencyItem) early-return dropped all Item (gold-bar) tips.
     Fixed by _extract_gold_from_tip() which handles both types.
  2. add_ledger_entry() passed related_user=None to a NOT NULL column, crashing
     every tip silently inside the outer try/except.
     Fixed in database.py (default changed to "") AND by announcing in chat
     BEFORE any DB write — so the player always sees the result even if a
     ledger write later fails.

Player commands:
  /tiprate            — conversion rate + bonus tiers
  /tipstats           — your personal tip history
  /tipleaderboard     — top 10 gold tippers
  /debugtips          — owner-only live diagnostics

Admin commands:
  /settiprate <coins_per_gold>
  /settipcap  <daily_gold_cap>
  /settiptier <100|500|1000|5000> <bonus_pct>
"""

import hashlib
import time
from typing import Optional

from highrise import BaseBot, User

import database as db
from modules.permissions import is_owner, is_admin

# ---------------------------------------------------------------------------
# Gold-bar item ID → gold value map
# These IDs are exactly what Highrise server sends in Item.id when a player
# tips a gold bar from their inventory.
# ---------------------------------------------------------------------------
_GOLD_BAR_VALUES: dict[str, int] = {
    "gold_bar_1":     1,
    "gold_bar_5":     5,
    "gold_bar_10":    10,
    "gold_bar_50":    50,
    "gold_bar_100":   100,
    "gold_bar_500":   500,
    "gold_bar_1k":    1_000,
    "gold_bar_5000":  5_000,
    "gold_bar_10k":   10_000,
}


def _extract_gold_from_tip(tip) -> Optional[int]:
    """
    Return the gold amount for any tip object, or None if not gold-convertible.

    Handles:
      • CurrencyItem(type='gold', amount=X)  — direct gold currency
      • Item(type='clothing', id='gold_bar_*') — gold bar from inventory
    """
    try:
        from highrise import CurrencyItem
        from highrise.models import Item

        if isinstance(tip, CurrencyItem):
            # Highrise sends type='earned_gold' for gold bar tips, not 'gold'
            if tip.type in ("gold", "earned_gold"):
                return tip.amount
            return None

        if isinstance(tip, Item):
            item_id = getattr(tip, "id", "") or ""
            # Exact match first
            gold = _GOLD_BAR_VALUES.get(item_id)
            if gold is None:
                # Substring scan for non-standard ID formats
                item_lower = item_id.lower()
                for key, val in _GOLD_BAR_VALUES.items():
                    if key in item_lower:
                        gold = val
                        break
            return gold

    except Exception as e:
        print(f"[TIP] _extract_gold_from_tip error: {e!r}")

    return None


# ---------------------------------------------------------------------------
# SDK / runtime constants (read once at import time)
# ---------------------------------------------------------------------------
try:
    import importlib.metadata as _imeta
    _SDK_VERSION: str = _imeta.version("highrise-bot-sdk")
except Exception:
    _SDK_VERSION = "unknown"

_RUN_COMMAND: str = "cd artifacts/highrise-bot && python3 bot.py  (bot.py → main.run())"


# ---------------------------------------------------------------------------
# /debugtips state  (in-memory — resets on bot restart)
# ---------------------------------------------------------------------------
_debug: dict = {
    # tip-specific
    "event_count":       0,
    "last_wall_time":    None,
    "last_sender":       None,
    "last_gold":         None,
    "last_tip_repr":     None,
    "last_error":        None,
    # cross-handler: tracks which handler fired most recently
    "last_handler_name": None,
    "last_handler_time": None,
    "last_handler_repr": None,
}


def record_debug_event(sender_username: str, gold: Optional[int], tip_repr: str) -> None:
    """Called from process_tip_event when a valid tip arrives."""
    _debug["last_wall_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    _debug["last_sender"]    = sender_username
    _debug["last_gold"]      = gold
    _debug["last_tip_repr"]  = tip_repr[:120]
    _debug["event_count"]   += 1


def record_debug_any_event(handler_name: str, raw_repr: str) -> None:
    """Called from every event hook so /debugtips can show the last handler fired."""
    _debug["last_handler_name"] = handler_name
    _debug["last_handler_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    _debug["last_handler_repr"] = raw_repr[:120]


def record_debug_error(err: str) -> None:
    _debug["last_error"] = err[:120]


# ---------------------------------------------------------------------------
# In-memory dedup (fast first line of defense — 10-second window)
# ---------------------------------------------------------------------------
_recent: dict[str, float] = {}
_DEDUP_SECS = 10.0


def _in_memory_seen(user_id: str, amount: int) -> bool:
    key = f"{user_id}_{amount}"
    now = time.monotonic()
    # Expire old entries
    stale = [k for k, t in _recent.items() if now - t > _DEDUP_SECS]
    for k in stale:
        _recent.pop(k, None)
    if key in _recent:
        return True
    _recent[key] = now
    return False


def _make_event_hash(user_id: str, gold: int) -> str:
    bucket = int(time.time()) // 10
    return hashlib.md5(f"{user_id}_{gold}_{bucket}".encode()).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Bonus tier
# ---------------------------------------------------------------------------

def _bonus_pct(gold: int, s: dict) -> int:
    if gold >= 5000: return int(s.get("tier_5000_bonus", 50))
    if gold >= 1000: return int(s.get("tier_1000_bonus", 30))
    if gold >= 500:  return int(s.get("tier_500_bonus",  20))
    if gold >= 100:  return int(s.get("tier_100_bonus",  10))
    return 0


# ---------------------------------------------------------------------------
# Messaging helpers (hard-cap at 249 chars)
# ---------------------------------------------------------------------------

async def _chat(bot: BaseBot, msg: str) -> None:
    try:
        await bot.highrise.chat(msg[:249])
    except Exception as e:
        print(f"[TIP] _chat error: {e!r}")


async def _whisper(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception as e:
        print(f"[TIP] _whisper error: {e!r}")


# ---------------------------------------------------------------------------
# Core tip processor  — called from HangoutBot.on_tip
# ---------------------------------------------------------------------------

async def process_tip_event(bot: BaseBot, sender: User, receiver: User, tip) -> None:
    """
    Convert a Highrise gold tip into in-game coins.

    ORDER OF OPERATIONS (important):
      1. Extract gold amount (handles CurrencyItem AND Item gold bars)
      2. Validate minimum / dedup
      3. Announce in room chat   ← FIRST, before any DB write
      4. Save to DB              ← after announcement; individual errors logged
    """
    try:
        # ── Full console log (never echoed to chat) ───────────────────────
        tip_class = type(tip).__name__
        tip_kind  = getattr(tip, "type",   "?")
        tip_iid   = getattr(tip, "id",     None)
        print(
            f"[TIP:DETAIL] class={tip_class} | type={tip_kind}"
            + (f" | item_id={tip_iid}" if tip_iid else "")
            + f" | sender=@{sender.username}({sender.id})"
            + f" | receiver=@{receiver.username}({receiver.id})"
            + f" | raw={tip!r}"
        )

        # ── Extract gold value ────────────────────────────────────────────
        gold = _extract_gold_from_tip(tip)
        record_debug_event(sender.username, gold, repr(tip))

        if gold is None:
            print(
                f"[TIP] Skip — not a gold tip "
                f"(class={tip_class} type={tip_kind}"
                + (f" id={tip_iid}" if tip_iid else "") + ")"
            )
            return

        # ── Load settings (all have safe defaults) ────────────────────────
        try:
            s = db.get_tip_settings()
        except Exception as e:
            print(f"[TIP] get_tip_settings error: {e!r} — using defaults")
            s = {}

        min_gold  = int(s.get("min_tip_gold",   10))
        daily_cap = int(s.get("daily_cap_gold", 10_000))
        rate      = int(s.get("coins_per_gold", 10))

        print(f"[TIP] Processing: @{sender.username} | gold={gold} | min={min_gold} | rate={rate}c/g")

        # ── Below minimum ─────────────────────────────────────────────────
        if gold < min_gold:
            await _whisper(
                bot, sender.id,
                f"🙏 Thanks @{sender.username}! Minimum for coin reward is {min_gold}g."
            )
            _safe_log_transaction(sender.username, gold, 0, 0, "below_min", "")
            print(f"[TIP] @{sender.username} below min ({gold}g < {min_gold}g)")
            return

        # ── In-memory dedup ───────────────────────────────────────────────
        if _in_memory_seen(sender.id, gold):
            print(f"[TIP] Duplicate (memory) ignored: @{sender.username} {gold}g")
            return

        # ── DB dedup ──────────────────────────────────────────────────────
        event_hash = _make_event_hash(sender.id, gold)
        try:
            if db.is_tip_duplicate(event_hash):
                print(f"[TIP] Duplicate (DB) ignored: @{sender.username} {gold}g")
                return
        except Exception as e:
            print(f"[TIP] is_tip_duplicate error (skipping dedup check): {e!r}")

        # ── Daily cap ─────────────────────────────────────────────────────
        daily_used = 0
        try:
            daily_used = db.get_daily_gold_converted(sender.id)
        except Exception as e:
            print(f"[TIP] get_daily_gold_converted error (skipping cap): {e!r}")

        remaining = max(0, daily_cap - daily_used)
        if remaining == 0:
            await _whisper(
                bot, sender.id,
                f"⚠️ Daily tip cap ({daily_cap:,}g) reached. Resets at midnight!"
            )
            _safe_log_transaction(sender.username, gold, 0, 0, "cap_reached", event_hash)
            print(f"[TIP] @{sender.username} cap reached ({daily_used}/{daily_cap}g)")
            return

        convertible = min(gold, remaining)

        # ── Bonus + coins ─────────────────────────────────────────────────
        bonus = _bonus_pct(convertible, s)
        base  = convertible * rate
        coins = base + round(base * bonus / 100)

        # ── ANNOUNCE IN CHAT FIRST (before any DB write) ──────────────────
        await _chat(
            bot,
            f"💰 @{sender.username} tipped {convertible:,}g and received {coins:,} coins!"
        )
        print(f"[TIP] OK: @{sender.username} {convertible}g → {coins:,}c (+{bonus}%)")

        # ── Personal whisper ──────────────────────────────────────────────
        parts = []
        if bonus > 0:
            parts.append(f"+{bonus}% bonus!")
        else:
            parts.append("Tip 100g+ for a bonus!")
        cap_left = max(0, daily_cap - daily_used - convertible)
        parts.append(f"Daily cap left: {cap_left:,}g.")
        await _whisper(bot, sender.id, "💛 " + " ".join(parts))

        # ── DB writes (each independently wrapped) ────────────────────────
        try:
            db.ensure_user(sender.id, sender.username)
        except Exception as e:
            print(f"[TIP] ensure_user error: {e!r}")

        try:
            db.ensure_bank_user(sender.id)
        except Exception as e:
            print(f"[TIP] ensure_bank_user error: {e!r}")

        try:
            db.record_tip_conversion(sender.id, sender.username, convertible, bonus, coins)
        except Exception as e:
            print(f"[TIP] record_tip_conversion error: {e!r}")
            record_debug_error(repr(e))

        _safe_log_transaction(sender.username, convertible, coins, bonus, "success", event_hash)

        # ── Auto-subscribe tipper to notifications ─────────────────────────
        try:
            auto_sub = s.get("tip_auto_sub", "1") == "1"
            if auto_sub:
                existing_sub = db.get_subscriber(sender.username)
                already_unsubbed = (
                    existing_sub is not None and not existing_sub.get("subscribed")
                )
                resubscribe = s.get("tip_resubscribe", "0") == "1"

                if already_unsubbed and not resubscribe:
                    print(f"[TIP] @{sender.username} previously unsubscribed; not resubscribing.")
                else:
                    db.upsert_subscriber(sender.username.lower(), sender.id)
                    db.set_subscribed(sender.username.lower(), True)
                    db.mark_tip_auto_subscribed(sender.username)
                    has_dm = (
                        existing_sub
                        and bool(existing_sub.get("conversation_id"))
                        and existing_sub.get("dm_available")
                    )
                    if has_dm:
                        await _whisper(
                            bot, sender.id,
                            "✅ Subscribed! You'll receive outside-room notifications."
                        )
                    else:
                        await _whisper(
                            bot, sender.id,
                            "📩 DM me 'subscribe' once to get alerts outside the room."
                        )
                    print(f"[TIP] @{sender.username} auto-subscribed from tip.")
        except Exception as sub_exc:
            print(f"[TIP] auto-subscribe error: {sub_exc!r}")

    except Exception as exc:
        record_debug_error(repr(exc))
        print(f"[TIP] UNHANDLED ERROR in process_tip_event: {exc!r}")
        import traceback
        traceback.print_exc()


def _safe_log_transaction(
    username: str, gold: int, coins: int, bonus: int, status: str, event_hash: str
) -> None:
    try:
        db.log_tip_transaction(username, gold, coins, bonus, status, event_hash)
    except Exception as e:
        print(f"[TIP] log_tip_transaction error: {e!r}")


# ---------------------------------------------------------------------------
# Player commands
# ---------------------------------------------------------------------------

async def handle_tiprate(bot: BaseBot, user: User, _args) -> None:
    s     = db.get_tip_settings()
    rate  = s.get("coins_per_gold",  "10")
    cap   = int(s.get("daily_cap_gold", 10_000))
    t100  = s.get("tier_100_bonus",  "10")
    t500  = s.get("tier_500_bonus",  "20")
    t1000 = s.get("tier_1000_bonus", "30")
    t5000 = s.get("tier_5000_bonus", "50")
    await _whisper(bot, user.id,
        f"💰 Tip Rate: 1g={rate}c | Bonus: 100g+{t100}% 500g+{t500}% "
        f"1k+{t1000}% 5k+{t5000}% | Daily cap: {cap:,}g"
    )


async def handle_tipstats(bot: BaseBot, user: User, _args) -> None:
    try:
        db.ensure_user(user.id, user.username)
        s       = db.get_tip_settings()
        cap     = int(s.get("daily_cap_gold", 10_000))
        stats   = db.get_tip_stats(user.id)
        remaining = max(0, cap - stats["today_gold"])
        await _whisper(bot, user.id,
            f"💛 @{user.username} Tips\n"
            f"Total: {stats['total_gold']:,}g → {stats['total_coins']:,}c\n"
            f"Today: {stats['today_gold']:,}g | Cap left: {remaining:,}g"
        )
    except Exception as e:
        await _whisper(bot, user.id, "⚠️ Error fetching tip stats.")
        print(f"[TIP] handle_tipstats error: {e!r}")


async def handle_tipleaderboard(bot: BaseBot, user: User, _args) -> None:
    try:
        rows = db.get_tip_leaderboard(10)
        if not rows:
            await _whisper(bot, user.id, "💛 No tips recorded yet!")
            return
        msg = "💛 Top Tippers:"
        for i, r in enumerate(rows, 1):
            line = f"\n{i}. @{r['username'][:14]}: {r['total_gold']:,}g"
            if len(msg) + len(line) > 245:
                break
            msg += line
        await _whisper(bot, user.id, msg)
    except Exception as e:
        await _whisper(bot, user.id, "⚠️ Error fetching leaderboard.")
        print(f"[TIP] handle_tipleaderboard error: {e!r}")


# ---------------------------------------------------------------------------
# /debugtips  (owner only)
# ---------------------------------------------------------------------------

async def handle_debugtips(bot: BaseBot, user: User, _args) -> None:
    """
    /debugtips  (owner only)
    Shows SDK version, run command, event subscription, all handler statuses,
    last event fired across all hooks, last tip details, and last error.
    Split into two whispers to stay under 249 chars each.
    """
    if not is_owner(user.username):
        await _whisper(bot, user.id, "Owner only.")
        return

    # ── SDK / subscription info ───────────────────────────────────────────
    try:
        from highrise.__main__ import gather_subscriptions
        subs = gather_subscriptions(bot)
    except Exception:
        subs = "unknown"

    # ── Handler availability ──────────────────────────────────────────────
    handlers = ["on_tip", "on_reaction", "on_channel", "on_emote", "on_whisper"]
    handler_status = " ".join(
        f"{h.replace('on_', '')}:{'✓' if callable(getattr(bot, h, None)) else '✗'}"
        for h in handlers
    )
    on_tip_ok = callable(getattr(bot, "on_tip", None))

    # ── Tip-specific state ────────────────────────────────────────────────
    count    = _debug["event_count"]
    wall     = _debug["last_wall_time"] or "never"
    sender   = _debug["last_sender"]    or "none"
    gold_str = f"{_debug['last_gold']}g" if _debug["last_gold"] is not None else "n/a"
    raw_str  = (_debug["last_tip_repr"] or "none")[:40]
    err_str  = (_debug["last_error"]    or "none")[:40]

    # ── Cross-handler last event ──────────────────────────────────────────
    lh_name  = _debug["last_handler_name"] or "none"
    lh_time  = _debug["last_handler_time"] or "never"

    no_tip_warning = (
        "\n⚠️ No tip detected. Check tip_reaction sub."
        if count == 0 else ""
    )

    # Whisper 1: system info
    msg1 = (
        f"🔍 TipDebug (1/2)\n"
        f"SDK: {_SDK_VERSION}\n"
        f"cmd: python3 bot.py\n"
        f"subs: {subs}\n"
        f"handlers: {handler_status}\n"
        f"on_tip installed: {'yes' if on_tip_ok else 'NO'}"
    )
    await _whisper(bot, user.id, msg1)

    # Whisper 2: live state
    msg2 = (
        f"🔍 TipDebug (2/2)\n"
        f"tips seen: {count}\n"
        f"last on_tip: {wall}\n"
        f"last sender: @{sender}\n"
        f"last amount: {gold_str}\n"
        f"last raw: {raw_str}\n"
        f"last handler: {lh_name} @ {lh_time}\n"
        f"last error: {err_str}"
        f"{no_tip_warning}"
    )
    await _whisper(bot, user.id, msg2)


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

async def handle_settiprate(bot: BaseBot, user: User, args: list) -> None:
    if not is_admin(user.username):
        await _whisper(bot, user.id, "Admins only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _whisper(bot, user.id, "Usage: !settiprate <coins_per_gold>")
        return
    val = int(args[1])
    if not (1 <= val <= 1000):
        await _whisper(bot, user.id, "❌ Rate must be 1–1,000.")
        return
    db.set_tip_setting("coins_per_gold", str(val))
    await _whisper(bot, user.id, f"✅ Tip rate: 1g = {val} coins.")


async def handle_settipcap(bot: BaseBot, user: User, args: list) -> None:
    if not is_admin(user.username):
        await _whisper(bot, user.id, "Admins only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _whisper(bot, user.id, "Usage: !settipcap <gold_amount>")
        return
    val = int(args[1])
    if not (100 <= val <= 1_000_000):
        await _whisper(bot, user.id, "❌ Cap must be 100–1,000,000.")
        return
    db.set_tip_setting("daily_cap_gold", str(val))
    await _whisper(bot, user.id, f"✅ Daily tip cap set to {val:,}g.")


async def handle_settiptier(bot: BaseBot, user: User, args: list) -> None:
    if not is_admin(user.username):
        await _whisper(bot, user.id, "Admins only.")
        return
    if len(args) < 3:
        await _whisper(bot, user.id, "Usage: !settiptier <100|500|1000|5000> <bonus%>")
        return
    _TIER_KEYS = {
        "100":  "tier_100_bonus",
        "500":  "tier_500_bonus",
        "1000": "tier_1000_bonus",
        "5000": "tier_5000_bonus",
    }
    tier = args[1]
    if tier not in _TIER_KEYS:
        await _whisper(bot, user.id, "❌ Tier must be 100, 500, 1000, or 5000.")
        return
    if not args[2].isdigit():
        await _whisper(bot, user.id, "❌ Bonus must be a whole number.")
        return
    pct = int(args[2])
    if not (0 <= pct <= 200):
        await _whisper(bot, user.id, "❌ Bonus must be 0–200%.")
        return
    db.set_tip_setting(_TIER_KEYS[tier], str(pct))
    await _whisper(bot, user.id, f"✅ {tier}g tier bonus set to +{pct}%.")


async def handle_settipautosub(bot: BaseBot, user: User, args: list) -> None:
    """/settipautosub on/off — toggle auto-subscribe on gold tip (admin+)."""
    if not is_admin(user.username):
        await _whisper(bot, user.id, "Admins only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        current = db.get_tip_settings().get("tip_auto_sub", "1")
        label = "ON" if current == "1" else "OFF"
        await _whisper(bot, user.id, f"Tip auto-subscribe is currently {label}. Use !settipautosub on|off")
        return
    val = "1" if args[1].lower() == "on" else "0"
    db.set_tip_setting("tip_auto_sub", val)
    label = "ON" if val == "1" else "OFF"
    await _whisper(bot, user.id, f"✅ Tip auto-subscribe set to {label}.")


async def handle_settipresubscribe(bot: BaseBot, user: User, args: list) -> None:
    """/settipresubscribe on/off — allow tips to resubscribe manual opt-outs (admin+)."""
    if not is_admin(user.username):
        await _whisper(bot, user.id, "Admins only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        current = db.get_tip_settings().get("tip_resubscribe", "0")
        label = "ON" if current == "1" else "OFF"
        await _whisper(bot, user.id, f"Tip resubscribe is currently {label}. Use !settipresubscribe on|off")
        return
    val = "1" if args[1].lower() == "on" else "0"
    db.set_tip_setting("tip_resubscribe", val)
    label = "ON" if val == "1" else "OFF"
    await _whisper(bot, user.id, f"✅ Tip resubscribe set to {label}.")
