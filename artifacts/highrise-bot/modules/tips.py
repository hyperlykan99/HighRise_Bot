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
"""

import time

from highrise import BaseBot, User

import database as db
from modules.permissions import is_admin

# ---------------------------------------------------------------------------
# In-memory deduplication guard
# Same user + same amount within 5 s → ignored (SDK fires once but be safe).
# ---------------------------------------------------------------------------
_recent: dict[str, float] = {}   # "uid_amount" → monotonic timestamp
_DEDUP_SECS = 5.0


def _dedup_key(user_id: str, amount: int) -> str:
    return f"{user_id}_{amount}"


def _already_processed(user_id: str, amount: int) -> bool:
    key = _dedup_key(user_id, amount)
    now = time.monotonic()
    stale = [k for k, t in _recent.items() if now - t > _DEDUP_SECS]
    for k in stale:
        _recent.pop(k, None)
    if key in _recent:
        return True
    _recent[key] = now
    return False


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
# Whisper helper
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


# ---------------------------------------------------------------------------
# Tip event processor  — called from HangoutBot.on_tip
# ---------------------------------------------------------------------------

async def process_tip_event(bot: BaseBot, sender: User, receiver: User, tip) -> None:
    """
    Convert a Highrise gold tip into in-game coins.

    - Only processes CurrencyItem tips of type 'gold'.
    - Non-gold tips (bubbles, items) are silently ignored.
    - Enforces minimum tip amount and per-player daily cap.
    - Applies bonus tier multipliers.
    - Records conversion in SQLite and the economy ledger.
    """
    from highrise import CurrencyItem  # local import avoids circular at module level

    if not isinstance(tip, CurrencyItem):
        return
    if tip.type != "gold":
        return

    gold = tip.amount
    s = db.get_tip_settings()

    min_gold  = int(s.get("min_tip_gold", 10))
    daily_cap = int(s.get("daily_cap_gold", 10000))
    rate      = int(s.get("coins_per_gold", 10))

    if gold < min_gold:
        await _w(
            bot, sender.id,
            f"💛 Thanks! Min for conversion is {min_gold}g. "
            f"Tip at least {min_gold}g to earn coins."
        )
        return

    # Deduplicate
    if _already_processed(sender.id, gold):
        print(f"[TIP] Duplicate ignored: @{sender.username} {gold}g")
        return

    db.ensure_user(sender.id, sender.username)
    db.ensure_bank_user(sender.id)

    # Apply daily cap
    daily_used  = db.get_daily_gold_converted(sender.id)
    remaining   = max(0, daily_cap - daily_used)
    if remaining == 0:
        await _w(
            bot, sender.id,
            f"⚠️ Daily tip cap ({daily_cap:,}g) reached. Resets tomorrow!"
        )
        return

    convertible = min(gold, remaining)
    over_cap    = gold - convertible          # gold not converted due to cap

    # Bonus calculation
    bonus = _bonus_pct(convertible, s)
    base  = convertible * rate
    coins = base + round(base * bonus / 100)

    db.record_tip_conversion(sender.id, sender.username, convertible, bonus, coins)

    # Confirmation whisper
    if bonus > 0:
        msg = f"💛 {convertible:,}g → {coins:,}c (+{bonus}% bonus)! Balance updated."
    else:
        msg = f"💛 {convertible:,}g → {coins:,}c! Balance updated."
    if over_cap:
        msg += f" ({over_cap:,}g over daily cap.)"

    await _w(bot, sender.id, msg)
    print(f"[TIP] @{sender.username}: {convertible}g → {coins}c (+{bonus}%)")


# ---------------------------------------------------------------------------
# Player commands
# ---------------------------------------------------------------------------

async def handle_tiprate(bot: BaseBot, user: User, _args) -> None:
    """/tiprate — show current gold-to-coins conversion rate."""
    s = db.get_tip_settings()
    rate  = s.get("coins_per_gold",  "10")
    cap   = int(s.get("daily_cap_gold", 10000))
    t100  = s.get("tier_100_bonus",  "10")
    t500  = s.get("tier_500_bonus",  "20")
    t1000 = s.get("tier_1000_bonus", "30")
    t5000 = s.get("tier_5000_bonus", "50")
    msg = (
        f"💰 Tip Rate: 1 gold = {rate} coins. Bonuses start at 100g.\n"
        f"100g: +{t100}% | 500g: +{t500}% | "
        f"1000g: +{t1000}% | 5000g: +{t5000}%\n"
        f"Daily cap: {cap:,}g"
    )
    await _w(bot, user.id, msg)


async def handle_tipstats(bot: BaseBot, user: User, _args) -> None:
    """/tipstats — personal tip conversion summary."""
    db.ensure_user(user.id, user.username)
    s       = db.get_tip_settings()
    cap     = int(s.get("daily_cap_gold", 10000))
    stats   = db.get_tip_stats(user.id)
    profile = db.get_profile(user.id)
    remaining = max(0, cap - stats["today_gold"])

    # tip_coins_earned from the users table is the authoritative running total
    tip_coins = profile.get("tip_coins_earned", stats["total_coins"])

    display = db.get_display_name(user.id, user.username)
    msg = (
        f"💛 {display} Tips\n"
        f"Total: {stats['total_gold']:,}g → {tip_coins:,}c\n"
        f"Today: {stats['today_gold']:,}g | Cap left: {remaining:,}g\n"
        f"Note: tip coins don't count toward send eligibility."
    )
    await _w(bot, user.id, msg)


async def handle_tipleaderboard(bot: BaseBot, user: User, _args) -> None:
    """/tipleaderboard — top 10 gold tippers."""
    rows = db.get_tip_leaderboard(10)
    if not rows:
        await _w(bot, user.id, "💛 No tips recorded yet!")
        return

    header = "💛 Top Tippers:"
    msg    = header
    for i, r in enumerate(rows, 1):
        line = f"\n{i}. @{r['username'][:14]}: {r['total_gold']:,}g"
        if len(msg) + len(line) > 245:
            break
        msg += line

    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# Admin/owner commands
# ---------------------------------------------------------------------------

async def handle_settiprate(bot: BaseBot, user: User, args: list) -> None:
    """/settiprate <coins_per_gold>  — admin/owner only."""
    if not is_admin(user.username):
        await _w(bot, user.id, "Admins only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /settiprate <coins_per_gold>")
        return
    val = int(args[1])
    if not (1 <= val <= 1000):
        await _w(bot, user.id, "❌ Rate must be 1–1,000.")
        return
    db.set_tip_setting("coins_per_gold", str(val))
    await _w(bot, user.id, f"✅ Tip rate: 1 gold = {val} coins.")


async def handle_settipcap(bot: BaseBot, user: User, args: list) -> None:
    """/settipcap <gold_amount>  — admin/owner only."""
    if not is_admin(user.username):
        await _w(bot, user.id, "Admins only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /settipcap <gold_amount>")
        return
    val = int(args[1])
    if not (100 <= val <= 1_000_000):
        await _w(bot, user.id, "❌ Cap must be 100–1,000,000.")
        return
    db.set_tip_setting("daily_cap_gold", str(val))
    await _w(bot, user.id, f"✅ Daily tip cap set to {val:,}g.")


async def handle_settiptier(bot: BaseBot, user: User, args: list) -> None:
    """/settiptier <100|500|1000|5000> <bonus_pct>  — admin/owner only."""
    if not is_admin(user.username):
        await _w(bot, user.id, "Admins only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /settiptier <100|500|1000|5000> <bonus%>")
        return

    _TIER_KEYS = {
        "100":  "tier_100_bonus",
        "500":  "tier_500_bonus",
        "1000": "tier_1000_bonus",
        "5000": "tier_5000_bonus",
    }
    tier = args[1]
    if tier not in _TIER_KEYS:
        await _w(bot, user.id, "❌ Tier must be 100, 500, 1000, or 5000.")
        return
    if not args[2].isdigit():
        await _w(bot, user.id, "❌ Bonus must be a whole number (0–200).")
        return
    pct = int(args[2])
    if not (0 <= pct <= 200):
        await _w(bot, user.id, "❌ Bonus must be 0–200%.")
        return

    db.set_tip_setting(_TIER_KEYS[tier], str(pct))
    await _w(bot, user.id, f"✅ {tier}g tier bonus set to +{pct}%.")
