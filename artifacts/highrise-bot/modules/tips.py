"""
modules/tips.py
---------------
Highrise gold tip → in-game coins conversion system.

Player commands:
  /tiprate        — show conversion rate and bonus tiers
  /tipstats       — your personal tip conversion history
  /tipleaderboard — top 10 gold tippers

Admin/owner commands:
  /settiprate <coins_per_gold>
  /settipcap  <daily_gold_cap>
  /settiptier <100|500|1000|5000> <bonus_pct>

Tip processing is triggered by the Highrise on_tip SDK event.

Dedup strategy (two layers):
  1. In-memory guard: same user_id + amount within 5 s → skip.
  2. DB guard: event_id_or_hash stored in tip_transactions → skip if seen.
     Hash = md5(user_id + "_" + str(gold) + "_" + 10-second bucket).

On successful conversion:
  • Public room chat: "💰 @user tipped Xg and received Y coins!"
  • Private whisper:  bonus %, cap info.
  • tip_conversions row (existing balance/ledger table).
  • tip_transactions row (spec-required dedup + audit table).
"""

import hashlib
import time

from highrise import BaseBot, User

import database as db
from modules.permissions import is_admin

# ---------------------------------------------------------------------------
# In-memory deduplication guard (first line of defense, very fast)
# ---------------------------------------------------------------------------
_recent: dict[str, float] = {}   # "uid_amount" → monotonic timestamp
_DEDUP_SECS = 5.0


def _dedup_key(user_id: str, amount: int) -> str:
    return f"{user_id}_{amount}"


def _in_memory_seen(user_id: str, amount: int) -> bool:
    key = _dedup_key(user_id, amount)
    now = time.monotonic()
    # Prune stale entries
    stale = [k for k, t in _recent.items() if now - t > _DEDUP_SECS]
    for k in stale:
        _recent.pop(k, None)
    if key in _recent:
        return True
    _recent[key] = now
    return False


def _make_event_hash(user_id: str, gold: int) -> str:
    """
    Deterministic hash for this tip event.
    Uses a 10-second time bucket so two identical tips > 10 s apart are
    treated as distinct events, while duplicate SDK firings within 10 s are
    caught as duplicates.
    """
    bucket = int(time.time()) // 10
    raw    = f"{user_id}_{gold}_{bucket}"
    return hashlib.md5(raw.encode()).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Bonus tier helper
# ---------------------------------------------------------------------------

def _bonus_pct(gold: int, s: dict) -> int:
    """Return the bonus percentage for a given gold amount."""
    if gold >= 5000:
        return int(s.get("tier_5000_bonus", 50))
    if gold >= 1000:
        return int(s.get("tier_1000_bonus", 30))
    if gold >= 500:
        return int(s.get("tier_500_bonus", 20))
    if gold >= 100:
        return int(s.get("tier_100_bonus", 10))
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

    - Only processes CurrencyItem tips of type 'gold'.
    - Non-gold tips (bubbles, items) are silently ignored.
    - Two-layer deduplication (in-memory + DB hash).
    - Enforces minimum tip amount and per-player daily cap.
    - Applies bonus tier multipliers.
    - Announces result publicly in room chat.
    - Records conversion in tip_conversions + tip_transactions + ledger.
    """
    try:
        from highrise import CurrencyItem  # local import avoids circular at module level

        # ── Debug log: full event data ────────────────────────────────────
        print(
            f"[TIP] Event: sender=@{sender.username}({sender.id}) "
            f"receiver=@{receiver.username}({receiver.id}) "
            f"tip={tip!r}"
        )

        if not isinstance(tip, CurrencyItem):
            print(f"[TIP] Ignored — not CurrencyItem (type={type(tip).__name__})")
            return
        if tip.type != "gold":
            print(f"[TIP] Ignored — currency type '{tip.type}' (not gold)")
            return

        gold = tip.amount
        s    = db.get_tip_settings()

        min_gold  = int(s.get("min_tip_gold",   10))
        daily_cap = int(s.get("daily_cap_gold", 10000))
        rate      = int(s.get("coins_per_gold", 10))

        print(f"[TIP] @{sender.username} tipped {gold}g (min={min_gold}, cap={daily_cap}, rate={rate})")

        # ── Below minimum ─────────────────────────────────────────────────
        if gold < min_gold:
            await _whisper(
                bot, sender.id,
                f"Tip received, but minimum for coin reward is {min_gold}g."
            )
            db.log_tip_transaction(
                sender.username, gold, 0, 0, "below_min", ""
            )
            print(f"[TIP] @{sender.username} below minimum ({gold}g < {min_gold}g)")
            return

        # ── Layer 1: in-memory dedup ──────────────────────────────────────
        if _in_memory_seen(sender.id, gold):
            print(f"[TIP] Duplicate (memory) ignored: @{sender.username} {gold}g")
            return

        # ── Layer 2: DB dedup via hash ────────────────────────────────────
        event_hash = _make_event_hash(sender.id, gold)
        if db.is_tip_duplicate(event_hash):
            print(f"[TIP] Duplicate (DB hash) ignored: @{sender.username} {gold}g hash={event_hash}")
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
                f"⚠️ Daily tip cap ({daily_cap:,}g) reached. Resets tomorrow!"
            )
            db.log_tip_transaction(
                sender.username, gold, 0, 0, "cap_reached", event_hash
            )
            print(f"[TIP] @{sender.username} daily cap reached ({daily_used}/{daily_cap}g)")
            return

        convertible = min(gold, remaining)
        over_cap    = gold - convertible

        # ── Bonus + coin calculation ──────────────────────────────────────
        bonus = _bonus_pct(convertible, s)
        base  = convertible * rate
        coins = base + round(base * bonus / 100)

        # ── Persist: tip_conversions (balance/ledger) + tip_transactions ──
        db.record_tip_conversion(sender.id, sender.username, convertible, bonus, coins)
        db.log_tip_transaction(
            sender.username, convertible, coins, bonus, "success", event_hash
        )

        print(f"[TIP] @{sender.username}: {convertible}g → {coins:,}c (+{bonus}%) OK")

        # ── Public room announcement ──────────────────────────────────────
        await _chat(
            bot,
            f"💰 @{sender.username} tipped {convertible:,}g and received {coins:,} coins!"
        )

        # ── Personal whisper with extra detail ────────────────────────────
        if bonus > 0:
            detail = f"💛 +{bonus}% bonus applied!"
        else:
            detail = "💛 Tip at 100g+ for a coin bonus!"

        if over_cap:
            detail += f" ({over_cap:,}g over daily cap, not converted.)"

        cap_left = max(0, daily_cap - daily_used - convertible)
        detail += f" Daily cap: {cap_left:,}g left."

        await _whisper(bot, sender.id, detail)

    except Exception as exc:
        print(f"[TIP] ERROR in process_tip_event: {exc!r}")


# ---------------------------------------------------------------------------
# Player commands
# ---------------------------------------------------------------------------

async def handle_tiprate(bot: BaseBot, user: User, _args) -> None:
    """/tiprate — show current gold-to-coins conversion rate."""
    s     = db.get_tip_settings()
    rate  = s.get("coins_per_gold",  "10")
    cap   = int(s.get("daily_cap_gold", 10000))
    t100  = s.get("tier_100_bonus",  "10")
    t500  = s.get("tier_500_bonus",  "20")
    t1000 = s.get("tier_1000_bonus", "30")
    t5000 = s.get("tier_5000_bonus", "50")
    msg = (
        f"💰 Tip Rate: 1g = {rate}c. Bonus starts at 100g.\n"
        f"100g: +{t100}% | 500g: +{t500}% | "
        f"1000g: +{t1000}% | 5000g: +{t5000}%\n"
        f"Daily cap: {cap:,}g"
    )
    await _whisper(bot, user.id, msg)


async def handle_tipstats(bot: BaseBot, user: User, _args) -> None:
    """/tipstats — personal tip conversion summary."""
    db.ensure_user(user.id, user.username)
    s         = db.get_tip_settings()
    cap       = int(s.get("daily_cap_gold", 10000))
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
# Admin/owner commands
# ---------------------------------------------------------------------------

async def handle_settiprate(bot: BaseBot, user: User, args: list) -> None:
    """/settiprate <coins_per_gold>  — admin/owner only."""
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
    """/settipcap <gold_amount>  — admin/owner only."""
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
    """/settiptier <100|500|1000|5000> <bonus_pct>  — admin/owner only."""
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
