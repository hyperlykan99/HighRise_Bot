"""
modules/tips.py
---------------
Highrise gold tip → in-game coins conversion system.

ROOT CAUSE (fixed here):
  When a player tips the bot gold bars from their inventory, the SDK fires
  on_tip with an Item(type='clothing', id='gold_bar_100', ...) — NOT a
  CurrencyItem.  The previous code returned early on isinstance(tip, CurrencyItem)
  check, silently dropping every real gold-bar tip.  Fixed by checking both
  CurrencyItem (direct gold currency) AND Item (gold bar items) via
  _extract_gold_from_tip().

Player commands:
  /tiprate        — show conversion rate and bonus tiers
  /tipstats       — your personal tip conversion history
  /tipleaderboard — top 10 gold tippers
  /debugtips      — owner-only live tip event diagnostics

Admin/owner commands:
  /settiprate <coins_per_gold>
  /settipcap  <daily_gold_cap>
  /settiptier <100|500|1000|5000> <bonus_pct>

Dedup strategy (two layers):
  1. In-memory guard: same user_id + amount within 5 s → skip.
  2. DB guard: event_id_or_hash stored in tip_transactions → skip if seen.

On successful conversion:
  • Public room chat:  "💰 @user tipped Xg and received Y coins!"
  • Private whisper:   bonus %, cap info.
  • tip_conversions row  (balance + ledger).
  • tip_transactions row (dedup + audit table).
"""

import hashlib
import time
from typing import Optional

from highrise import BaseBot, User

import database as db
from modules.permissions import is_owner, is_admin

# ---------------------------------------------------------------------------
# Gold-bar item ID → gold value map
# These IDs are exactly what Highrise server sends in the Item.id field when
# a player tips a gold bar from their inventory.
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

    Handles two cases:
      - CurrencyItem(type='gold', amount=X)  — direct gold currency tip
      - Item(type='clothing', id='gold_bar_*') — gold bar item tip from inventory
    """
    from highrise import CurrencyItem
    from highrise.models import Item

    if isinstance(tip, CurrencyItem):
        if tip.type == "gold":
            return tip.amount
        return None  # bubbles or other currency

    if isinstance(tip, Item):
        item_id = getattr(tip, "id", "") or ""
        # Try exact match first, then case-insensitive prefix scan
        gold = _GOLD_BAR_VALUES.get(item_id)
        if gold is None:
            item_lower = item_id.lower()
            for key, val in _GOLD_BAR_VALUES.items():
                if item_lower.startswith(key) or key in item_lower:
                    gold = val
                    break
        return gold  # None if not a gold bar

    return None  # unknown type


# ---------------------------------------------------------------------------
# /debugtips state  (in-memory — resets on bot restart)
# ---------------------------------------------------------------------------
_debug: dict = {
    "enabled":        True,
    "last_time":      None,   # float monotonic
    "last_wall_time": None,   # human-readable UTC string
    "last_sender":    None,
    "last_gold":      None,
    "last_tip_repr":  None,
    "last_error":     None,
    "event_count":    0,
}


def _record_debug_event(sender_username: str, gold: Optional[int], tip_repr: str) -> None:
    _debug["last_time"]      = time.monotonic()
    _debug["last_wall_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    _debug["last_sender"]    = sender_username
    _debug["last_gold"]      = gold
    _debug["last_tip_repr"]  = tip_repr[:120]
    _debug["event_count"]   += 1


def _record_debug_error(err: str) -> None:
    _debug["last_error"] = err[:120]


# ---------------------------------------------------------------------------
# In-memory deduplication guard (first line of defense, very fast)
# ---------------------------------------------------------------------------
_recent: dict[str, float] = {}   # "uid_amount" → monotonic timestamp
_DEDUP_SECS = 5.0


def _in_memory_seen(user_id: str, amount: int) -> bool:
    key = f"{user_id}_{amount}"
    now = time.monotonic()
    stale = [k for k, t in _recent.items() if now - t > _DEDUP_SECS]
    for k in stale:
        _recent.pop(k, None)
    if key in _recent:
        return True
    _recent[key] = now
    return False


def _make_event_hash(user_id: str, gold: int) -> str:
    """Stable hash for this event using a 10-second bucket."""
    bucket = int(time.time()) // 10
    raw    = f"{user_id}_{gold}_{bucket}"
    return hashlib.md5(raw.encode()).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Bonus tier helper
# ---------------------------------------------------------------------------

def _bonus_pct(gold: int, s: dict) -> int:
    if gold >= 5000:
        return int(s.get("tier_5000_bonus", 50))
    if gold >= 1000:
        return int(s.get("tier_1000_bonus", 30))
    if gold >= 500:
        return int(s.get("tier_500_bonus",  20))
    if gold >= 100:
        return int(s.get("tier_100_bonus",  10))
    return 0


# ---------------------------------------------------------------------------
# Messaging helpers
# ---------------------------------------------------------------------------

async def _whisper(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


async def _chat(bot: BaseBot, msg: str) -> None:
    await bot.highrise.chat(msg[:249])


# ---------------------------------------------------------------------------
# Tip event processor  — called from HangoutBot.on_tip
# ---------------------------------------------------------------------------

async def process_tip_event(bot: BaseBot, sender: User, receiver: User, tip) -> None:
    """
    Convert a Highrise gold tip into in-game coins.

    Handles BOTH:
      - CurrencyItem(type='gold')   → direct gold currency tip
      - Item(id='gold_bar_*')       → gold bar item tip from inventory  ← THE BUG FIX

    Two-layer deduplication (in-memory + DB hash).
    Announces result publicly in room chat.
    Records in tip_conversions + tip_transactions + ledger.
    """
    try:
        # ── Structured debug log (console only, never echoed to chat) ─────
        tip_class  = type(tip).__name__
        tip_kind   = getattr(tip, "type",   "?")
        tip_amt    = getattr(tip, "amount", "?")
        tip_iid    = getattr(tip, "id",     None)
        tip_repr   = repr(tip)

        print(
            f"[TIP:DETAIL] "
            f"class={tip_class} | type={tip_kind} | amount={tip_amt}"
            + (f" | item_id={tip_iid}" if tip_iid else "")
            + f" | sender=@{sender.username}({sender.id})"
            + f" | receiver=@{receiver.username}({receiver.id})"
            + f" | raw={tip_repr}"
        )

        # ── Extract gold value (handles CurrencyItem AND Item gold bars) ──
        gold = _extract_gold_from_tip(tip)

        _record_debug_event(sender.username, gold, tip_repr)

        if gold is None:
            print(
                f"[TIP] Skip — not a gold tip "
                f"(class={tip_class} type={tip_kind}"
                + (f" id={tip_iid}" if tip_iid else "")
                + ")"
            )
            return

        s         = db.get_tip_settings()
        min_gold  = int(s.get("min_tip_gold",   10))
        daily_cap = int(s.get("daily_cap_gold", 10_000))
        rate      = int(s.get("coins_per_gold", 10))

        print(
            f"[TIP] Processing: @{sender.username} | "
            f"gold={gold} | min={min_gold} | daily_cap={daily_cap} | rate={rate}c/g"
        )

        # ── Below minimum ─────────────────────────────────────────────────
        if gold < min_gold:
            await _whisper(
                bot, sender.id,
                f"🙏 Thanks for the tip! Minimum for coins is {min_gold}g."
            )
            db.log_tip_transaction(sender.username, gold, 0, 0, "below_min", "")
            print(f"[TIP] @{sender.username} below minimum ({gold}g < {min_gold}g)")
            return

        # ── Layer 1: in-memory dedup ──────────────────────────────────────
        if _in_memory_seen(sender.id, gold):
            print(f"[TIP] Duplicate (memory) ignored: @{sender.username} {gold}g")
            return

        # ── Layer 2: DB dedup via hash ────────────────────────────────────
        event_hash = _make_event_hash(sender.id, gold)
        if db.is_tip_duplicate(event_hash):
            print(f"[TIP] Duplicate (DB) ignored: @{sender.username} {gold}g hash={event_hash}")
            return

        # ── Ensure player row exists ──────────────────────────────────────
        db.ensure_user(sender.id, sender.username)
        db.ensure_bank_user(sender.id)

        # ── Daily cap check ───────────────────────────────────────────────
        daily_used = db.get_daily_gold_converted(sender.id)
        remaining  = max(0, daily_cap - daily_used)
        if remaining == 0:
            await _whisper(
                bot, sender.id,
                f"⚠️ Daily tip cap ({daily_cap:,}g) reached. Resets at midnight!"
            )
            db.log_tip_transaction(sender.username, gold, 0, 0, "cap_reached", event_hash)
            print(f"[TIP] @{sender.username} cap reached ({daily_used}/{daily_cap}g)")
            return

        convertible = min(gold, remaining)
        over_cap    = gold - convertible

        # ── Bonus + coin calculation ──────────────────────────────────────
        bonus = _bonus_pct(convertible, s)
        base  = convertible * rate
        coins = base + round(base * bonus / 100)

        # ── Persist: balance + ledger + audit ─────────────────────────────
        db.record_tip_conversion(sender.id, sender.username, convertible, bonus, coins)
        db.log_tip_transaction(
            sender.username, convertible, coins, bonus, "success", event_hash
        )

        print(f"[TIP] OK: @{sender.username} {convertible}g → {coins:,}c (+{bonus}%)")

        # ── Public room announcement ──────────────────────────────────────
        await _chat(
            bot,
            f"💰 @{sender.username} tipped {convertible:,}g and received {coins:,} coins!"
        )

        # ── Personal whisper with extra detail ────────────────────────────
        parts = []
        if bonus > 0:
            parts.append(f"+{bonus}% bonus applied!")
        else:
            parts.append("Tip 100g+ for a bonus!")
        if over_cap:
            parts.append(f"{over_cap:,}g over daily cap.")
        cap_left = max(0, daily_cap - daily_used - convertible)
        parts.append(f"Daily cap: {cap_left:,}g left.")
        await _whisper(bot, sender.id, "💛 " + " ".join(parts))

    except Exception as exc:
        _record_debug_error(repr(exc))
        print(f"[TIP] ERROR in process_tip_event: {exc!r}")


# ---------------------------------------------------------------------------
# Player commands
# ---------------------------------------------------------------------------

async def handle_tiprate(bot: BaseBot, user: User, _args) -> None:
    """/tiprate — show current rate and bonus tiers."""
    s     = db.get_tip_settings()
    rate  = s.get("coins_per_gold",  "10")
    cap   = int(s.get("daily_cap_gold", 10_000))
    t100  = s.get("tier_100_bonus",  "10")
    t500  = s.get("tier_500_bonus",  "20")
    t1000 = s.get("tier_1000_bonus", "30")
    t5000 = s.get("tier_5000_bonus", "50")
    msg = (
        f"💰 Tip Rate: 1g = {rate}c. Bonus starts at 100g.\n"
        f"100g:+{t100}% 500g:+{t500}% 1000g:+{t1000}% 5000g:+{t5000}%\n"
        f"Daily cap: {cap:,}g"
    )
    await _whisper(bot, user.id, msg)


async def handle_tipstats(bot: BaseBot, user: User, _args) -> None:
    """/tipstats — personal tip conversion summary."""
    db.ensure_user(user.id, user.username)
    s         = db.get_tip_settings()
    cap       = int(s.get("daily_cap_gold", 10_000))
    stats     = db.get_tip_stats(user.id)
    profile   = db.get_profile(user.id)
    remaining = max(0, cap - stats["today_gold"])
    tip_coins = profile.get("tip_coins_earned", stats["total_coins"])
    display   = db.get_display_name(user.id, user.username)
    msg = (
        f"💛 {display} Tips\n"
        f"Total: {stats['total_gold']:,}g → {tip_coins:,}c\n"
        f"Today: {stats['today_gold']:,}g | Cap left: {remaining:,}g"
    )
    await _whisper(bot, user.id, msg)


async def handle_tipleaderboard(bot: BaseBot, user: User, _args) -> None:
    """/tipleaderboard — top 10 gold tippers."""
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


# ---------------------------------------------------------------------------
# /debugtips  (owner only)
# ---------------------------------------------------------------------------

async def handle_debugtips(bot: BaseBot, user: User, _args) -> None:
    """/debugtips — owner-only live tip diagnostics."""
    if not is_owner(user.username):
        await _whisper(bot, user.id, "Owner only.")
        return

    # Verify on_tip is subscribed in SDK
    handler_ok = callable(getattr(bot, "on_tip", None))

    if not handler_ok:
        await _whisper(bot, user.id, "Tip detection not supported by this SDK setup.")
        return

    count     = _debug["event_count"]
    wall      = _debug["last_wall_time"] or "never"
    sender    = _debug["last_sender"]    or "none"
    gold      = _debug["last_gold"]
    gold_str  = f"{gold}g" if gold is not None else "n/a"
    tip_raw   = _debug["last_tip_repr"]  or "none"
    err       = _debug["last_error"]     or "none"

    lines = [
        f"🔍 Tip Debug",
        f"Handler: on_tip ({'OK' if handler_ok else 'MISSING'})",
        f"Events seen: {count}",
        f"Last event: {wall}",
        f"Last sender: @{sender}",
        f"Last gold: {gold_str}",
        f"Last raw: {tip_raw[:60]}",
        f"Last error: {err[:60]}",
    ]
    await _whisper(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# Admin/owner commands
# ---------------------------------------------------------------------------

async def handle_settiprate(bot: BaseBot, user: User, args: list) -> None:
    if not is_admin(user.username):
        await _whisper(bot, user.id, "Admins only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _whisper(bot, user.id, "Usage: /settiprate <coins_per_gold>")
        return
    val = int(args[1])
    if not (1 <= val <= 1000):
        await _whisper(bot, user.id, "❌ Rate must be 1–1,000.")
        return
    db.set_tip_setting("coins_per_gold", str(val))
    await _whisper(bot, user.id, f"✅ Tip rate: 1 gold = {val} coins.")


async def handle_settipcap(bot: BaseBot, user: User, args: list) -> None:
    if not is_admin(user.username):
        await _whisper(bot, user.id, "Admins only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _whisper(bot, user.id, "Usage: /settipcap <gold_amount>")
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
        await _whisper(bot, user.id, "Usage: /settiptier <100|500|1000|5000> <bonus%>")
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
        await _whisper(bot, user.id, "❌ Bonus must be a whole number (0–200).")
        return
    pct = int(args[2])
    if not (0 <= pct <= 200):
        await _whisper(bot, user.id, "❌ Bonus must be 0–200%.")
        return
    db.set_tip_setting(_TIER_KEYS[tier], str(pct))
    await _whisper(bot, user.id, f"✅ {tier}g tier bonus set to +{pct}%.")
