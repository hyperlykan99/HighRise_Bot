"""
modules/luxe.py
---------------
🎫 Luxe Tickets — premium room currency (3.1I ADDON, updated 3.1I-U).

Earn by tipping Highrise Gold to any bot.
Spend in the numbered Luxe Shop (!luxeshop).
Stackable auto time — buy 1h/3h/5h permits that add seconds to a persistent pool.
"""
from __future__ import annotations
import datetime as _dt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot
    from highrise.models import User

import database as db
from modules.permissions import is_admin, is_owner

_w  = lambda bot, uid, msg: bot.highrise.send_whisper(uid, msg[:249])
_fc = lambda n: f"{n:,}"


def _fmt_secs(secs: int) -> str:
    """Format seconds as 'Xh Ym' or 'Ym' or '0m'."""
    secs = max(0, int(secs))
    h    = secs // 3600
    m    = (secs % 3600) // 60
    if h > 0 and m > 0:
        return f"{h}h {m}m"
    if h > 0:
        return f"{h}h"
    return f"{m}m"


# ---------------------------------------------------------------------------
# Numbered shop catalogue  (stable numbers — never reorder)
# ---------------------------------------------------------------------------

# item_key → (number, display_name, category, default_price_tickets, default_duration_secs)
_SHOP_ITEMS: dict[str, tuple[int, str, str, int, int]] = {
    "vip":          (1,  "VIP Pass",          "vip",     500,  30 * 86400),
    "automine1h":   (2,  "Auto-Mine 1h",       "mining",  100,  3600),
    "automine3h":   (3,  "Auto-Mine 3h",       "mining",  250,  10800),
    "automine5h":   (4,  "Auto-Mine 5h",       "mining",  400,  18000),
    "autofish1h":   (5,  "Auto-Fish 1h",       "fishing", 100,  3600),
    "autofish3h":   (6,  "Auto-Fish 3h",       "fishing", 250,  10800),
    "autofish5h":   (7,  "Auto-Fish 5h",       "fishing", 400,  18000),
    "luckyhour":    (8,  "Lucky Hour Boost",   "boosts",  150,  3600),
    "treasurehour": (9,  "Treasure Hour Boost","boosts",  200,  3600),
    "smallcoins":   (10, "Small ChillCoins",   "coins",   50,   0),
    "mediumcoins":  (11, "Medium ChillCoins",  "coins",   100,  0),
    "largecoins":   (12, "Large ChillCoins",   "coins",   250,  0),
}
# Reverse: number → key
_NUM_TO_KEY: dict[int, str] = {v[0]: k for k, v in _SHOP_ITEMS.items()}

_DEFAULT_COINPACKS: dict[str, tuple[int, int]] = {
    "smallcoins":  (50,   50_000),
    "mediumcoins": (100, 125_000),
    "largecoins":  (250, 350_000),
}


# ---------------------------------------------------------------------------
# DB helpers — balances
# ---------------------------------------------------------------------------

def get_luxe_balance(user_id: str) -> int:
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT luxe_tickets FROM premium_balances WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    return int(row["luxe_tickets"]) if row else 0


def _ensure_premium_row(user_id: str, username: str) -> None:
    conn = db.get_connection()
    conn.execute(
        """INSERT OR IGNORE INTO premium_balances
           (user_id, username, luxe_tickets, updated_at)
           VALUES (?, ?, 0, datetime('now'))""",
        (user_id, username.lower()),
    )
    conn.commit()
    conn.close()


def add_luxe_balance(user_id: str, username: str, amount: int) -> int:
    _ensure_premium_row(user_id, username)
    conn = db.get_connection()
    conn.execute(
        """UPDATE premium_balances
           SET luxe_tickets = luxe_tickets + ?,
               updated_at   = datetime('now')
           WHERE user_id = ?""",
        (max(0, amount), user_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT luxe_tickets FROM premium_balances WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    return int(row["luxe_tickets"]) if row else 0


def deduct_luxe_balance(user_id: str, username: str, amount: int) -> bool:
    _ensure_premium_row(user_id, username)
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT luxe_tickets FROM premium_balances WHERE user_id=?", (user_id,)
    ).fetchone()
    bal  = int(row["luxe_tickets"]) if row else 0
    if bal < amount:
        conn.close()
        return False
    conn.execute(
        """UPDATE premium_balances
           SET luxe_tickets = luxe_tickets - ?,
               updated_at   = datetime('now')
           WHERE user_id = ?""",
        (amount, user_id),
    )
    conn.commit()
    conn.close()
    return True


def log_luxe_transaction(
    user_id: str, username: str,
    tx_type: str, amount: int, currency: str, details: str = "",
) -> None:
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT INTO premium_transactions
               (user_id, username, type, amount, currency, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (user_id, username.lower(), tx_type, amount, currency, details),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# DB helpers — settings / prices / durations
# ---------------------------------------------------------------------------

def get_luxe_setting(key: str, default: str = "") -> str:
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT value FROM premium_settings WHERE key=?", (key,)
    ).fetchone()
    conn.close()
    return row["value"] if row else default


def set_luxe_setting(key: str, value: str) -> None:
    conn = db.get_connection()
    conn.execute(
        """INSERT INTO premium_settings (key, value, updated_at)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(key)
           DO UPDATE SET value=excluded.value,
                         updated_at=excluded.updated_at""",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_luxe_rate() -> int:
    try:
        return max(1, int(get_luxe_setting("luxe_rate", "1")))
    except ValueError:
        return 1


def get_luxe_price(item_key: str) -> int:
    default = _SHOP_ITEMS.get(item_key, (0, "", "", 0, 0))[3]
    try:
        return max(1, int(get_luxe_setting(f"price_{item_key}", str(default))))
    except ValueError:
        return default


def get_luxe_duration(item_key: str) -> int:
    """Return item duration in seconds (from settings or default)."""
    default = _SHOP_ITEMS.get(item_key, (0, "", "", 0, 0))[4]
    try:
        return max(0, int(get_luxe_setting(f"duration_{item_key}", str(default))))
    except ValueError:
        return default


def get_coinpack(size_key: str) -> tuple[int, int]:
    """Returns (ticket_cost, coins_awarded)."""
    defaults = _DEFAULT_COINPACKS.get(size_key, (0, 0))
    try:
        cost   = int(get_luxe_setting(f"coinpack_{size_key}_tickets", str(defaults[0])))
        reward = int(get_luxe_setting(f"coinpack_{size_key}_coins",   str(defaults[1])))
        return (cost, reward)
    except ValueError:
        return defaults


def get_vip_luxe_duration() -> int:
    try:
        return max(1, int(get_luxe_setting("vip_duration_days", "30")))
    except ValueError:
        return 30


# ---------------------------------------------------------------------------
# Luxe auto time helpers (wrappers over db.*)
# ---------------------------------------------------------------------------

def get_mine_luxe_time(user_id: str) -> int:
    return db.get_luxe_auto_time(user_id, "mining")


def get_fish_luxe_time(user_id: str) -> int:
    return db.get_luxe_auto_time(user_id, "fishing")


def add_mine_luxe_time(user_id: str, username: str, seconds: int) -> int:
    return db.add_luxe_auto_time(user_id, username, "mining", seconds)


def add_fish_luxe_time(user_id: str, username: str, seconds: int) -> int:
    return db.add_luxe_auto_time(user_id, username, "fishing", seconds)


# ---------------------------------------------------------------------------
# Internal: deliver items
# ---------------------------------------------------------------------------

async def _deliver_vip(bot: "BaseBot", user: "User") -> None:
    duration_days = get_vip_luxe_duration()
    now_dt       = _dt.datetime.now(_dt.timezone.utc)
    existing_exp = db.get_room_setting(f"vip_expires_{user.id}", "")

    if existing_exp and db.owns_item(user.id, "vip"):
        try:
            old_dt = _dt.datetime.fromisoformat(existing_exp)
            if old_dt.tzinfo is None:
                old_dt = old_dt.replace(tzinfo=_dt.timezone.utc)
            base_dt = max(now_dt, old_dt)
            action  = "Extended"
        except Exception:
            base_dt = now_dt
            action  = "Activated"
    else:
        base_dt = now_dt
        action  = "Activated"

    new_exp_dt  = base_dt + _dt.timedelta(days=duration_days)
    new_exp_str = new_exp_dt.strftime("%Y-%m-%d")
    db.grant_item(user.id, "vip", "vip")
    db.set_room_setting(f"vip_expires_{user.id}", new_exp_str)
    db.log_admin_action(user.username, user.username, "buyvip_luxe", "", f"{duration_days}d")

    price    = get_luxe_price("vip")
    rem_days = (new_exp_dt - now_dt).days
    title    = "👑 VIP Extended" if action == "Extended" else "👑 VIP Purchased"
    await _w(bot, user.id,
             f"{title}\n"
             f"Cost: {_fc(price)} 🎫\n"
             f"Added: {duration_days}d\n"
             f"Remaining: {rem_days}d\n"
             f"Expires: {new_exp_str}")

    try:
        print(f"[ECONOMY ALERT TRIGGER] type=vip_purchase "
              f"user=@{user.username} action={action} duration={duration_days}d")
        from modules.staff_alerts import queue_staff_alert  # noqa: PLC0415
        _alert_msg = (
            f"💰 Economy Alert\n"
            f"VIP Purchase\n"
            f"User: @{user.username}\n"
            f"Package: VIP {duration_days}d ({action})\n"
            f"Cost: {price:,} 🎫"
        )[:249]
        queue_staff_alert("economy", _alert_msg)
    except Exception:
        pass


async def _deliver_auto_time(
    bot: "BaseBot", user: "User", item_key: str
) -> None:
    """Add auto time seconds to the player's pool."""
    info    = _SHOP_ITEMS[item_key]
    dur_sec = get_luxe_duration(item_key)
    if item_key.startswith("automine"):
        new_secs = add_mine_luxe_time(user.id, user.username, dur_sec)
        label    = "Auto-Mine"
    else:
        new_secs = add_fish_luxe_time(user.id, user.username, dur_sec)
        label    = "Auto-Fish"
    await _w(bot, user.id,
             f"⛏️ {label} Time Added: {_fmt_secs(dur_sec)}\n"
             f"Total Available: {_fmt_secs(new_secs)}\n"
             f"Use !automine luxe / !autofish luxe to start.")


async def _deliver_boost(bot: "BaseBot", user: "User", item_key: str) -> None:
    expires = (
        _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)
    ).isoformat()
    if item_key == "luckyhour":
        try:
            db.add_player_boost(user.id, user.username, "luck", "mining",  5, expires, "luckyhour")
            db.add_player_boost(user.id, user.username, "luck", "fishing", 5, expires, "luckyhour")
        except Exception:
            pass
        await _w(bot, user.id,
                 "🍀 Lucky Hour Activated!\n"
                 "Duration: 1h  |  +5 luck mining & fishing")
    else:
        try:
            db.add_player_boost(user.id, user.username, "value", "mining",  10, expires, "treasurehour")
            db.add_player_boost(user.id, user.username, "value", "fishing", 10, expires, "treasurehour")
        except Exception:
            pass
        await _w(bot, user.id,
                 "💎 Treasure Hour Activated!\n"
                 "Duration: 1h  |  +10% value bonus mining & fishing")


async def _deliver_coinpack(bot: "BaseBot", user: "User", item_key: str) -> None:
    cost, coins = get_coinpack(item_key)
    if coins <= 0:
        await _w(bot, user.id, "⚠️ Coin pack not configured. Ask staff.")
        return
    db.add_balance(user.id, coins)
    log_luxe_transaction(user.id, user.username, "buycoins_award", coins, "coins", item_key)
    new_bal = db.get_balance(user.id)
    await _w(bot, user.id,
             f"✅ Purchased {_fc(coins)} 🪙!\n"
             f"Coin balance: {_fc(new_bal)} 🪙")


# ---------------------------------------------------------------------------
# Core purchase logic
# ---------------------------------------------------------------------------

async def _do_purchase(
    bot: "BaseBot", user: "User", item_key: str, qty: int = 1
) -> None:
    """Validate, deduct tickets, deliver item (qty times for time items)."""
    info  = _SHOP_ITEMS.get(item_key)
    if not info:
        await _w(bot, user.id, f"⚠️ Item not found. Use !luxeshop.")
        return

    cat = info[2]
    if cat == "coins":
        price, _ = get_coinpack(item_key)
    else:
        price = get_luxe_price(item_key)
    total = price * qty
    bal   = get_luxe_balance(user.id)

    if bal < total:
        await _w(bot, user.id,
                 f"⚠️ Not enough 🎫.\n"
                 f"Need: {_fc(total)} 🎫\n"
                 f"You have: {_fc(bal)} 🎫")
        return

    if not deduct_luxe_balance(user.id, user.username, total):
        await _w(bot, user.id, "⚠️ Transaction failed. Try again.")
        return

    log_luxe_transaction(user.id, user.username, "purchase", total, "luxe",
                         f"{item_key} x{qty}")

    # Deliver
    if cat == "vip":
        for _ in range(qty):
            await _deliver_vip(bot, user)
    elif cat in ("mining", "fishing"):
        # For time items, accumulate all seconds first, then confirm
        dur_sec = get_luxe_duration(item_key)
        total_sec = dur_sec * qty
        if cat == "mining":
            new_secs = db.add_luxe_auto_time(user.id, user.username, "mining", total_sec)
        else:
            new_secs = db.add_luxe_auto_time(user.id, user.username, "fishing", total_sec)
        _item_name = info[1]
        await _w(bot, user.id,
                 f"✅ Luxe Purchase\n"
                 f"Item: {_item_name}\n"
                 f"Cost: {_fc(total)} 🎫\n"
                 f"Added: {_fmt_secs(total_sec)}\n"
                 f"Total: {_fmt_secs(new_secs)}")
    elif cat == "boosts":
        for _ in range(qty):
            await _deliver_boost(bot, user, item_key)
    elif cat == "coins":
        cost_u, coins  = get_coinpack(item_key)
        total_coins    = coins * qty
        _lux_after     = get_luxe_balance(user.id)       # after deduction
        _coins_before  = db.get_balance(user.id)
        db.add_balance(user.id, total_coins)
        log_luxe_transaction(user.id, user.username, "buycoins_award",
                             total_coins, "coins", item_key)
        _coins_after = db.get_balance(user.id)
        try:
            db.log_luxe_conversion(
                user_id=user.id,
                username=user.username.lower(),
                item_key=item_key,
                tickets_spent=total,
                coins_awarded=total_coins,
                luxe_balance_before=_lux_after + total,
                luxe_balance_after=_lux_after,
                coins_balance_before=_coins_before,
                coins_balance_after=_coins_after,
                status="success",
            )
        except Exception as _ce:
            print(f"[LUXE CONVERSION AUDIT] log error: {_ce!r}")
        print(f"[LUXE CONVERSION AUDIT] user={user.username} item={item_key} "
              f"spent={total} coins={total_coins} status=success")
        await _w(bot, user.id,
                 f"✅ Conversion Complete\n"
                 f"Spent: {_fc(total)} 🎫\n"
                 f"Received: {_fc(total_coins)} 🪙")


# ---------------------------------------------------------------------------
# !tickets / !luxe
# ---------------------------------------------------------------------------

async def handle_tickets(bot: "BaseBot", user: "User", args: list[str]) -> None:
    db.ensure_user(user.id, user.username)
    bal = get_luxe_balance(user.id)
    await _w(bot, user.id,
             f"🎫 Luxe Tickets\n"
             f"Balance: {_fc(bal)} 🎫\n"
             f"Earned from verified Gold tips.\n"
             f"Use !luxeshop.")
    await _w(bot, user.id,
             "Use 🎫 for VIP, Luxe auto time, boosts, and 🪙 packs.\n"
             "Convert: !buycoins max")


# ---------------------------------------------------------------------------
# !autotime / !minetime / !fishtime
# ---------------------------------------------------------------------------

async def handle_autotime(bot: "BaseBot", user: "User", args: list[str]) -> None:
    db.ensure_user(user.id, user.username)
    sub      = args[1].lower() if len(args) >= 2 else ""
    mine_sec = get_mine_luxe_time(user.id)
    fish_sec = get_fish_luxe_time(user.id)
    if sub in ("mine", "mining", "minetime"):
        if mine_sec > 0:
            await _w(bot, user.id,
                     f"⛏️ Luxe Mining Time\n"
                     f"Time left: {_fmt_secs(mine_sec)}\n"
                     f"Use !automine luxe to start.")
        else:
            await _w(bot, user.id,
                     f"⛏️ Luxe Mining Time\n"
                     f"Time left: 0m\n"
                     f"Buy from !luxeshop mining.")
    elif sub in ("fish", "fishing", "fishtime"):
        if fish_sec > 0:
            await _w(bot, user.id,
                     f"🎣 Luxe Fishing Time\n"
                     f"Time left: {_fmt_secs(fish_sec)}\n"
                     f"Use !autofish luxe to start.")
        else:
            await _w(bot, user.id,
                     f"🎣 Luxe Fishing Time\n"
                     f"Time left: 0m\n"
                     f"Buy from !luxeshop fishing.")
    else:
        if mine_sec == 0 and fish_sec == 0:
            await _w(bot, user.id,
                     f"🎫 Luxe Auto Time\n"
                     f"Mining: 0m\n"
                     f"Fishing: 0m\n"
                     f"Buy time in !luxeshop.")
        else:
            await _w(bot, user.id,
                     f"🎫 Luxe Auto Time\n"
                     f"Mining: {_fmt_secs(mine_sec)}\n"
                     f"Fishing: {_fmt_secs(fish_sec)}\n"
                     f"Use:\n!automine luxe\n!autofish luxe")


# ---------------------------------------------------------------------------
# !luxeshop / !premiumshop [category]
# ---------------------------------------------------------------------------

_CAT_ALIASES: dict[str, str] = {
    "mine": "mining", "mining": "mining",
    "fish": "fishing", "fishing": "fishing",
    "boost": "boosts", "boosts": "boosts",
    "coin": "coins", "coins": "coins",
    "vip": "vip",
}


async def handle_luxeshop(bot: "BaseBot", user: "User", args: list[str] = None) -> None:
    cat = None
    if args and len(args) >= 2:
        cat = _CAT_ALIASES.get(args[1].lower())

    items = sorted(_SHOP_ITEMS.items(), key=lambda x: x[1][0])

    if cat:
        # Category filter — one message
        filtered = [(k, v) for k, v in items if v[2] == cat]
        if not filtered:
            await _w(bot, user.id, "⚠️ No items in that category. Try: mining fishing boosts coins vip")
            return
        cat_headers = {
            "mining": "⛏️ Luxe Mining",
            "fishing": "🎣 Luxe Fishing",
            "boosts": "🍀 Luxe Boosts",
            "coins": "🪙 ChillCoin Packs",
            "vip": "👑 VIP",
        }
        header = cat_headers.get(cat, f"🎫 Luxe {cat.capitalize()}")
        lines  = [header]
        for k, (num, name, _, _, _) in filtered:
            price = get_luxe_price(k)
            if cat == "coins":
                _, coins = get_coinpack(k)
                lines.append(f"{num}. {_fc(price)} 🎫 = {_fc(coins)} 🪙")
            else:
                lines.append(f"{num}. {name} — {_fc(price)} 🎫")
        if cat == "vip":
            lines.append("Use !buyluxe 1")
            lines.append("Check: !vip")
        elif cat == "coins":
            lines.append("Use !buycoins max")
        else:
            lines.append("Use !buyluxe [#]")
        await _w(bot, user.id, "\n".join(lines)[:249])
        return

    # Full shop — 3 pages, ≤220 chars each
    dur   = get_vip_luxe_duration()
    p_vip = get_luxe_price("vip")
    p_am1 = get_luxe_price("automine1h")
    p_am3 = get_luxe_price("automine3h")
    p_am5 = get_luxe_price("automine5h")
    p_af1 = get_luxe_price("autofish1h")
    p_af3 = get_luxe_price("autofish3h")
    p_af5 = get_luxe_price("autofish5h")
    p_lh  = get_luxe_price("luckyhour")
    p_th  = get_luxe_price("treasurehour")
    cs_t, cs_c = get_coinpack("smallcoins")
    cm_t, cm_c = get_coinpack("mediumcoins")
    cl_t, cl_c = get_coinpack("largecoins")

    await _w(bot, user.id,
             f"🎫 Luxe Shop\n"
             f"1. VIP Pass — {_fc(p_vip)} 🎫\n"
             f"2. Auto-Mine 1h — {_fc(p_am1)} 🎫\n"
             f"3. Auto-Mine 3h — {_fc(p_am3)} 🎫\n"
             f"4. Auto-Mine 5h — {_fc(p_am5)} 🎫")
    await _w(bot, user.id,
             f"5. Auto-Fish 1h — {_fc(p_af1)} 🎫\n"
             f"6. Auto-Fish 3h — {_fc(p_af3)} 🎫\n"
             f"7. Auto-Fish 5h — {_fc(p_af5)} 🎫\n"
             f"8. Lucky Hour — {_fc(p_lh)} 🎫")
    await _w(bot, user.id,
             f"9. Treasure Hour — {_fc(p_th)} 🎫\n"
             f"10. Small Coins — {_fc(cs_t)} 🎫 = {_fc(cs_c)} 🪙\n"
             f"11. Medium Coins — {_fc(cm_t)} 🎫 = {_fc(cm_c)} 🪙\n"
             f"12. Large Coins — {_fc(cl_t)} 🎫 = {_fc(cl_c)} 🪙\n"
             f"Buy: !buyluxe [#]")


# ---------------------------------------------------------------------------
# !buyluxe / !buyticket [number_or_key] [qty]
# ---------------------------------------------------------------------------

async def handle_buyluxe(bot: "BaseBot", user: "User", args: list[str]) -> None:
    db.ensure_user(user.id, user.username)
    if len(args) < 2:
        await _w(bot, user.id,
                 "⚠️ Use: !buyluxe [number]\n"
                 "Example: !buyluxe 2\n"
                 "Browse: !luxeshop")
        return

    raw = args[1].strip()

    # Resolve by number
    item_key: str | None = None
    if raw.isdigit():
        num = int(raw)
        item_key = _NUM_TO_KEY.get(num)
        if not item_key:
            await _w(bot, user.id,
                     f"⚠️ Item #{num} not found. Use !luxeshop.")
            return
    else:
        # Resolve by key (backward compat + admin shorthand)
        k = raw.lower()
        if k in _SHOP_ITEMS:
            item_key = k
        else:
            # Alias old keys
            _aliases = {"small": "smallcoins", "medium": "mediumcoins", "large": "largecoins"}
            item_key = _aliases.get(k)
        if not item_key:
            await _w(bot, user.id,
                     f"⚠️ Item '{raw}' not found. Use !luxeshop.")
            return

    # Qty (optional, only for time items and coins)
    qty = 1
    if len(args) >= 3:
        try:
            qty = max(1, int(args[2]))
        except ValueError:
            qty = 1
        cat = _SHOP_ITEMS[item_key][2]
        if cat not in ("mining", "fishing", "coins", "boosts"):
            # VIP qty > 1 is fine (it extends), so allow it
            pass

    await _do_purchase(bot, user, item_key, qty)


# Legacy alias — keep for backward compat
async def handle_buyticket(bot: "BaseBot", user: "User", args: list[str]) -> None:
    await handle_buyluxe(bot, user, args)


# ---------------------------------------------------------------------------
# !buycoins (legacy) — keep working
# ---------------------------------------------------------------------------

# In-memory pending buycoins confirmations {user_id: {total_cost, total_coins, pack_summary, expires, source}}
_pending_buycoins: dict[str, dict] = {}


async def _do_buycoins_max(
    bot: "BaseBot",
    user: "User",
    _silent: bool = False,
    _whisper_prefix: str = "",
) -> None:
    """Buy the best combination of coin packs using all available Luxe Tickets.
    Always goes through a confirmation step."""
    import time as _time
    db.ensure_user(user.id, user.username)
    bal = get_luxe_balance(user.id)
    if bal <= 0:
        if not _silent:
            await _w(bot, user.id,
                     "⚠️ No 🎟️ to spend.\n"
                     "Tip the bot to earn Luxe Tickets!")
        return

    cl_t, cl_c = get_coinpack("largecoins")
    cm_t, cm_c = get_coinpack("mediumcoins")
    cs_t, cs_c = get_coinpack("smallcoins")

    remaining   = bal
    total_cost  = 0
    total_coins = 0
    parts: list[str] = []

    for t, c, lbl in [(cl_t, cl_c, "Large"), (cm_t, cm_c, "Medium"), (cs_t, cs_c, "Small")]:
        if t > 0 and remaining >= t:
            qty        = remaining // t
            cost       = qty * t
            earned     = qty * c
            remaining -= cost
            total_cost  += cost
            total_coins += earned
            parts.append(f"{qty}x{lbl}")

    if total_cost == 0:
        min_t = min((t for t in [cs_t, cm_t, cl_t] if t > 0), default=50)
        if not _silent:
            await _w(bot, user.id,
                     f"⚠️ Not enough 🎟️. You have {_fc(bal)} 🎟️.\n"
                     f"Minimum: {min_t:,} 🎟️ for a pack.")
        return

    pack_summary = ", ".join(parts)
    _pending_buycoins[user.id] = {
        "total_cost":   total_cost,
        "total_coins":  total_coins,
        "pack_summary": pack_summary,
        "expires":      _time.time() + 120,
        "source":       "max",
    }
    await _w(bot, user.id,
             (f"🛒 Buycoins Preview\n"
              f"Spend: {total_cost:,} 🎟️\n"
              f"Receive: {total_coins:,} 🪙\n"
              f"Packs: {pack_summary}\n"
              f"!confirmbuycoins or !cancelbuycoins")[:249])


# ---------------------------------------------------------------------------
# !confirmbuycoins / !cancelbuycoins
# ---------------------------------------------------------------------------

async def handle_confirmbuycoins(bot: "BaseBot", user: "User") -> None:
    """!confirmbuycoins — execute a pending buycoins max or large-pack purchase."""
    import time as _time
    db.ensure_user(user.id, user.username)
    pending = _pending_buycoins.get(user.id)
    if not pending:
        await _w(bot, user.id,
                 "⚠️ No pending purchase.\n"
                 "Use !buycoins max or !buycoins 1/2/3 first.")
        return
    if _time.time() > pending["expires"]:
        _pending_buycoins.pop(user.id, None)
        await _w(bot, user.id, "⏱️ Confirmation timed out (2 min). Please retry.")
        return

    total_cost   = pending["total_cost"]
    total_coins  = pending["total_coins"]
    pack_summary = pending["pack_summary"]
    pack_id      = pending.get("pack_id")

    bal = get_luxe_balance(user.id)
    if bal < total_cost:
        _pending_buycoins.pop(user.id, None)
        await _w(bot, user.id,
                 f"⚠️ Balance changed. Need {total_cost:,} 🎟️, have {bal:,}.")
        return

    if not deduct_luxe_balance(user.id, user.username, total_cost):
        _pending_buycoins.pop(user.id, None)
        await _w(bot, user.id, "⚠️ Transaction failed. Try again.")
        return

    _pending_buycoins.pop(user.id, None)
    db.add_balance(user.id, total_coins)

    item_key = f"coin_pack_{pack_id}" if pack_id is not None else "buycoins_max"
    reason   = (f"pack{pack_id}:{total_coins}coins" if pack_id is not None
                else f"+{total_coins} coins")
    log_luxe_transaction(user.id, user.username, item_key, total_cost, "luxe", reason)
    try:
        lux_after   = get_luxe_balance(user.id)
        coins_after = db.get_balance(user.id)
        db.log_luxe_conversion(
            user_id=user.id,
            username=user.username.lower(),
            item_key=item_key,
            tickets_spent=total_cost,
            coins_awarded=total_coins,
            luxe_balance_before=lux_after + total_cost,
            luxe_balance_after=lux_after,
            coins_balance_before=coins_after - total_coins,
            coins_balance_after=coins_after,
            status="success",
        )
    except Exception as _ce:
        print(f"[COINPACK] log error: {_ce!r}")
    try:
        lux_after = get_luxe_balance(user.id)
        db.insert_luxe_ticket_log(
            "coinpack_confirm", user.id, user.username,
            amount=total_cost, balance_after=lux_after,
            reason=reason,
        )
    except Exception:
        pass
    print(f"[BUYCOINS] confirmed user={user.username} spent={total_cost} coins={total_coins}")
    await _w(bot, user.id,
             (f"✅ Confirmed!\n"
              f"Spent: {total_cost:,} 🎟️\n"
              f"Received: {total_coins:,} 🪙\n"
              f"Packs: {pack_summary}")[:249])

    try:
        print(f"[ECONOMY ALERT TRIGGER] type=luxe_conversion "
              f"user=@{user.username} spent={total_cost} coins={total_coins}")
        from modules.staff_alerts import queue_staff_alert  # noqa: PLC0415
        _alert_msg = (
            f"💰 Economy Alert\n"
            f"Luxe conversion\n"
            f"User: @{user.username}\n"
            f"Spent: {total_cost:,} 🎟️\n"
            f"Received: {total_coins:,} 🪙\n"
            f"Review: !ledger @{user.username}"
        )[:249]
        queue_staff_alert("economy", _alert_msg)
    except Exception:
        pass


async def handle_cancelbuycoins(bot: "BaseBot", user: "User") -> None:
    """!cancelbuycoins — cancel a pending buycoins purchase."""
    if user.id in _pending_buycoins:
        _pending_buycoins.pop(user.id)
        await _w(bot, user.id, "❌ Purchase cancelled.")
    else:
        await _w(bot, user.id, "⚠️ No pending purchase to cancel.")


# ---------------------------------------------------------------------------
# Auto-convert helpers
# ---------------------------------------------------------------------------

def _get_autoconvert(user_id: str) -> bool:
    """Return True if player has auto-convert enabled."""
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT enabled FROM player_auto_convert WHERE user_id=?",
            (user_id,)
        ).fetchone()
        return bool(row["enabled"]) if row else False
    except Exception:
        return False
    finally:
        conn.close()


def _set_autoconvert(user_id: str, username: str, enabled: bool) -> None:
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT INTO player_auto_convert (user_id, username, enabled, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                 enabled=excluded.enabled,
                 username=excluded.username,
                 updated_at=excluded.updated_at""",
            (user_id, username.lower(), 1 if enabled else 0)
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


async def handle_autoconvert(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!autoconvert coins on/off/status — auto-convert 🎫 to 🪙 on tip."""
    db.ensure_user(user.id, user.username)
    sub  = args[1].lower() if len(args) >= 2 else "status"
    sub2 = args[2].lower() if len(args) >= 3 else ""

    # !autoconvert status  OR  !autoconvert coins status
    if sub == "status" or (sub == "coins" and sub2 in ("status", "")):
        enabled = _get_autoconvert(user.id)
        status  = "ON" if enabled else "OFF"
        await _w(bot, user.id,
                 f"🔁 Auto-Convert\n"
                 f"Status: {status}\n"
                 f"When ON, verified Gold tips convert 🎫 to 🪙 automatically.")

    elif sub == "coins" and sub2 in ("on", "off"):
        enabled = sub2 == "on"
        _set_autoconvert(user.id, user.username, enabled)
        if enabled:
            await _w(bot, user.id,
                     "✅ Auto-Convert ON\n"
                     "Future verified Gold tips will convert 🎫 into 🪙 packs.")
        else:
            await _w(bot, user.id,
                     "✅ Auto-Convert OFF\n"
                     "Future tips stay as 🎫.")
    else:
        await _w(bot, user.id,
                 "🔁 Auto-Convert\n"
                 "!autoconvert coins on\n"
                 "!autoconvert coins off\n"
                 "!autoconvert status")


# ---------------------------------------------------------------------------
# !economydefaults — owner-only economy defaults preview/apply
# ---------------------------------------------------------------------------

_ECONOMY_DEFAULTS: dict[str, str] = {
    "daily_coins":     "1000",
    "trivia_reward":   "500",
    "scramble_reward": "500",
    "riddle_reward":   "750",
}
_ROOM_DEFAULTS: dict[str, str] = {
    "gold_tip_coins_per_gold": "1000",
    "gold_tip_enabled":        "1",
    "after_tip_menu":          "1",
    "gold_tip_min_amount":     "1.0",
}
_LUXE_SETTING_DEFAULTS: dict[str, str] = {
    "luxe_rate":          "1",
    "price_vip":          "500",
    "price_automine1h":   "100",
    "price_automine3h":   "250",
    "price_automine5h":   "400",
    "price_autofish1h":   "100",
    "price_autofish3h":   "250",
    "price_autofish5h":   "400",
    "price_luckyhour":    "150",
    "price_treasurehour": "200",
    "price_smallcoins":   "50",
    "price_mediumcoins":  "100",
    "price_largecoins":   "250",
    "coins_smallcoins":   "50000",
    "coins_mediumcoins":  "125000",
    "coins_largecoins":   "350000",
    "vip_duration_days":  "30",
    "dur_automine1h":     "60",
    "dur_automine3h":     "180",
    "dur_automine5h":     "300",
    "dur_autofish1h":     "60",
    "dur_autofish3h":     "180",
    "dur_autofish5h":     "300",
}
_MISSING = "__not_set__"


async def handle_economydefaults(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!economydefaults preview|apply — show or apply economy defaults (owner only)."""
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await _w(bot, user.id, "🔒 Owner only.")
        return

    sub = args[1].lower() if len(args) >= 2 else "preview"

    if sub == "preview":
        lines = ["📋 Economy Defaults (preview)"]
        for k, v in _ECONOMY_DEFAULTS.items():
            lines.append(f"  daily_coins={_ECONOMY_DEFAULTS['daily_coins']} "
                         f"trivia={_ECONOMY_DEFAULTS['trivia_reward']}")
            break
        await _w(bot, user.id,
                 f"📋 Economy Defaults\n"
                 f"daily_coins: {_ECONOMY_DEFAULTS['daily_coins']} 🪙\n"
                 f"trivia_reward: {_ECONOMY_DEFAULTS['trivia_reward']} 🪙\n"
                 f"gold_tip rate: {_ROOM_DEFAULTS['gold_tip_coins_per_gold']} 🪙/gold")
        await _w(bot, user.id,
                 f"Luxe: VIP {_LUXE_SETTING_DEFAULTS['price_vip']} 🎫 | "
                 f"Mine1h {_LUXE_SETTING_DEFAULTS['price_automine1h']} 🎫\n"
                 f"Coins: S={_LUXE_SETTING_DEFAULTS['coins_smallcoins']} "
                 f"M={_LUXE_SETTING_DEFAULTS['coins_mediumcoins']} "
                 f"L={_LUXE_SETTING_DEFAULTS['coins_largecoins']} 🪙\n"
                 f"Run !economydefaults apply to set missing values.")

    elif sub == "apply":
        changed: list[str] = []
        # economy_settings — only insert if missing
        conn = db.get_connection()
        try:
            for k, v in _ECONOMY_DEFAULTS.items():
                r = conn.execute(
                    "SELECT value FROM economy_settings WHERE key=?", (k,)
                ).fetchone()
                if r is None:
                    conn.execute(
                        "INSERT OR IGNORE INTO economy_settings (key, value) VALUES (?,?)",
                        (k, v)
                    )
                    changed.append(f"econ:{k}")
            conn.commit()
        except Exception as exc:
            print(f"[ECONDEFAULTS] DB error: {exc}")
        finally:
            conn.close()
        # room settings
        for k, v in _ROOM_DEFAULTS.items():
            cur = db.get_room_setting(k, _MISSING)
            if cur == _MISSING:
                db.set_room_setting(k, v)
                changed.append(f"room:{k}")
        # luxe settings
        for k, v in _LUXE_SETTING_DEFAULTS.items():
            cur = db.get_room_setting(f"luxe_{k}", _MISSING)
            if cur == _MISSING:
                set_luxe_setting(k, v)
                changed.append(f"luxe:{k}")
        n = len(changed)
        await _w(bot, user.id,
                 f"✅ Economy defaults applied: {n} value(s) set.\n"
                 f"Existing values were not overwritten.")
        if changed:
            await _w(bot, user.id, (", ".join(changed))[:249])
    else:
        await _w(bot, user.id,
                 "📋 Economy Defaults\n"
                 "!economydefaults preview\n"
                 "!economydefaults apply (owner only)")


async def handle_buycoins(bot: "BaseBot", user: "User", args: list[str]) -> None:
    db.ensure_user(user.id, user.username)
    _map = {"small": "smallcoins", "medium": "mediumcoins", "large": "largecoins"}
    sizes_legacy = ("small", "medium", "large", "max")

    # No argument — show quick help menu
    if len(args) < 2:
        await _w(bot, user.id,
                 "🪙 Buy Chill Coins\n"
                 "View packs: !packs\n"
                 "Buy: !buycoins small/medium/large\n"
                 "Max buy: !buycoins max")
        return

    raw = args[1].lower().strip()

    # Numbered pack — !buycoins 1 / !buycoins 2 / !buycoins 3
    if raw.isdigit():
        await _do_purchase_numbered_pack(bot, user, int(raw))
        return

    # Legacy: max
    if raw == "max":
        await _do_buycoins_max(bot, user)
        return

    # Legacy: small / medium / large
    if raw in _map:
        await _do_purchase(bot, user, _map[raw], 1)
        return

    # Unknown arg — show pack menu
    await handle_buypack(bot, user, args)


# ---------------------------------------------------------------------------
# !use (legacy route — redirect to clear messaging)
# ---------------------------------------------------------------------------

async def handle_use(bot: "BaseBot", user: "User", args: list[str]) -> None:
    db.ensure_user(user.id, user.username)
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: !use automine | !use autofish\n"
                 "Or use !automine luxe / !autofish luxe directly.")
        return
    item = args[1].lower()
    if item in ("automine", "automine1h"):
        secs = get_mine_luxe_time(user.id)
        if secs > 0:
            await _w(bot, user.id,
                     f"⛏️ You have {_fmt_secs(secs)} Auto-Mine time.\n"
                     f"Use !automine luxe to start.")
        else:
            await _w(bot, user.id,
                     f"⚠️ No Luxe Auto-Mine time.\n"
                     f"Buy from !luxeshop mining")
    elif item in ("autofish", "autofish1h"):
        secs = get_fish_luxe_time(user.id)
        if secs > 0:
            await _w(bot, user.id,
                     f"🎣 You have {_fmt_secs(secs)} Auto-Fish time.\n"
                     f"Use !autofish luxe to start.")
        else:
            await _w(bot, user.id,
                     f"⚠️ No Luxe Auto-Fish time.\n"
                     f"Buy from !luxeshop fishing")
    else:
        await _w(bot, user.id,
                 "Usage: !use automine | !use autofish\n"
                 "Or use !automine luxe / !autofish luxe directly.")


# ---------------------------------------------------------------------------
# Admin: !luxeadmin
# ---------------------------------------------------------------------------

def _can_luxe_admin(username: str) -> bool:
    return is_admin(username) or is_owner(username)


async def handle_luxeadmin(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _can_luxe_admin(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return

    sub = args[1].lower() if len(args) >= 2 else "help"

    if sub == "rate":
        rate = get_luxe_rate()
        await _w(bot, user.id,
                 f"🎫 Luxe Rate: {rate} 🎫 per Highrise Gold\n"
                 f"Change: !luxeadmin set rate <n>")

    elif sub == "prices":
        lines = ["🎫 Luxe Shop Prices"]
        for k, (num, name, _, _, _) in sorted(_SHOP_ITEMS.items(), key=lambda x: x[1][0]):
            lines.append(f"{num}. {name}: {_fc(get_luxe_price(k))} 🎫")
        # Split into two whispers to stay ≤249 chars
        half = len(lines) // 2
        await _w(bot, user.id, "\n".join(lines[:half + 1])[:249])
        await _w(bot, user.id, "\n".join(lines[half + 1:])[:249])

    elif sub == "set" and len(args) >= 4:
        what = args[2].lower()

        if what == "rate":
            try:
                val = int(args[3])
                if val < 1:
                    raise ValueError
            except ValueError:
                await _w(bot, user.id, "⚠️ Rate must be a positive integer.")
                return
            set_luxe_setting("luxe_rate", str(val))
            await _w(bot, user.id, f"✅ Luxe rate: 1 Gold = {val} 🎫")

        elif what == "price" and len(args) >= 5:
            # Accept number or item key
            raw_item = args[3].lower()
            if raw_item.isdigit():
                item_key = _NUM_TO_KEY.get(int(raw_item))
            else:
                item_key = raw_item if raw_item in _SHOP_ITEMS else None
            if not item_key:
                await _w(bot, user.id, "⚠️ Item not found. Use !luxeadmin prices to see list.")
                return
            try:
                val = int(args[4])
                if val < 1:
                    raise ValueError
            except ValueError:
                await _w(bot, user.id, "⚠️ Price must be a positive integer.")
                return
            set_luxe_setting(f"price_{item_key}", str(val))
            num, name, _, _, _ = _SHOP_ITEMS[item_key]
            await _w(bot, user.id, f"✅ #{num} {name}: {_fc(val)} 🎫")

        elif what == "duration" and len(args) >= 5:
            # accept item key (e.g. automine1h) and minutes
            raw_item = args[3].lower()
            if raw_item not in _SHOP_ITEMS:
                await _w(bot, user.id,
                         "⚠️ Item key required.\n"
                         "Example: !luxeadmin set duration automine1h 60")
                return
            try:
                mins = int(args[4])
                if mins < 1:
                    raise ValueError
            except ValueError:
                await _w(bot, user.id, "⚠️ Duration must be positive minutes.")
                return
            secs = mins * 60
            set_luxe_setting(f"duration_{raw_item}", str(secs))
            _, name, _, _, _ = _SHOP_ITEMS[raw_item]
            await _w(bot, user.id, f"✅ {name} duration: {mins}m")

        elif what == "coinpack" and len(args) >= 6:
            size_raw = args[3].lower()
            _map = {"small": "smallcoins", "medium": "mediumcoins", "large": "largecoins",
                    "smallcoins": "smallcoins", "mediumcoins": "mediumcoins", "largecoins": "largecoins"}
            size = _map.get(size_raw)
            if not size:
                await _w(bot, user.id, "Size must be small, medium, or large.")
                return
            try:
                tickets = int(args[4])
                coins   = int(args[5])
                if tickets < 1 or coins < 1:
                    raise ValueError
            except ValueError:
                await _w(bot, user.id, "⚠️ Tickets and coins must be positive integers.")
                return
            set_luxe_setting(f"coinpack_{size}_tickets", str(tickets))
            set_luxe_setting(f"coinpack_{size}_coins",   str(coins))
            await _w(bot, user.id,
                     f"✅ {size.capitalize()} pack: {_fc(tickets)} 🎫 → {_fc(coins)} 🪙")

        else:
            await _w(bot, user.id,
                     "!luxeadmin set rate <n>\n"
                     "!luxeadmin set price <#|key> <tickets>\n"
                     "!luxeadmin set duration <item> <mins>\n"
                     "!luxeadmin set coinpack <size> <t> <c>")

    elif sub == "grant" and len(args) >= 4:
        if not is_owner(user.username):
            await _w(bot, user.id, "🔒 Owner only.")
            return
        target_name = args[2].lstrip("@").strip()
        try:
            amount = int(args[3])
            if amount < 1:
                raise ValueError
        except ValueError:
            await _w(bot, user.id, "Amount must be a positive integer.")
            return
        rec = db.get_user_by_username(target_name)
        if not rec:
            await _w(bot, user.id, f"@{target_name} not found in DB.")
            return
        new_bal = add_luxe_balance(rec["user_id"], rec["username"], amount)
        log_luxe_transaction(rec["user_id"], rec["username"],
                             "admin_grant", amount, "luxe", f"by @{user.username}")
        await _w(bot, user.id,
                 f"✅ Granted {_fc(amount)} 🎫 to @{rec['username']}.\n"
                 f"New balance: {_fc(new_bal)} 🎫")

    elif sub == "revoke" and len(args) >= 4:
        if not is_owner(user.username):
            await _w(bot, user.id, "🔒 Owner only.")
            return
        target_name = args[2].lstrip("@").strip()
        try:
            amount = int(args[3])
            if amount < 1:
                raise ValueError
        except ValueError:
            await _w(bot, user.id, "Amount must be a positive integer.")
            return
        rec = db.get_user_by_username(target_name)
        if not rec:
            await _w(bot, user.id, f"@{target_name} not found in DB.")
            return
        cur_bal = get_luxe_balance(rec["user_id"])
        actual  = min(amount, cur_bal)
        if actual > 0:
            deduct_luxe_balance(rec["user_id"], rec["username"], actual)
        log_luxe_transaction(rec["user_id"], rec["username"],
                             "admin_revoke", actual, "luxe", f"by @{user.username}")
        new_bal = get_luxe_balance(rec["user_id"])
        await _w(bot, user.id,
                 f"✅ Revoked {_fc(actual)} 🎫 from @{rec['username']}.\n"
                 f"New balance: {_fc(new_bal)} 🎫")

    elif sub == "check" and len(args) >= 3:
        target_name = args[2].lstrip("@").strip()
        rec = db.get_user_by_username(target_name)
        if not rec:
            await _w(bot, user.id, f"@{target_name} not found in DB.")
            return
        bal      = get_luxe_balance(rec["user_id"])
        mine_sec = get_mine_luxe_time(rec["user_id"])
        fish_sec = get_fish_luxe_time(rec["user_id"])
        await _w(bot, user.id,
                 f"🎫 @{rec['username']}: {_fc(bal)} 🎫\n"
                 f"⛏️ Mine time: {_fmt_secs(mine_sec)}\n"
                 f"🎣 Fish time: {_fmt_secs(fish_sec)}")

    else:
        _rate      = get_luxe_rate()
        _n_items   = len(_SHOP_ITEMS)
        _atm_raw   = db.get_room_setting("after_tip_menu", "1")
        _atm_disp  = "ON" if _atm_raw == "1" else "OFF"
        await _w(bot, user.id,
                 f"🎫 Luxe Admin\n"
                 f"Rate: 1 Gold = {_rate} 🎫\n"
                 f"Items: {_n_items}\n"
                 f"After-tip menu: {_atm_disp}\n"
                 f"Auto-convert default: OFF")
        await _w(bot, user.id,
                 "!luxeadmin rate | prices\n"
                 "!luxeadmin set rate <n>\n"
                 "!luxeadmin set price <#|key> <tickets>\n"
                 "!luxeadmin set duration <item> <mins>\n"
                 "!luxeadmin grant/revoke/check @user")


# ---------------------------------------------------------------------------
# Admin: !vipadmin
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Numbered Coin Pack system  (packs 1/2/3 — configurable via !setcoinpack)
# ---------------------------------------------------------------------------

_COIN_PACKS_CACHE: dict[int, tuple[int, int]] = {}
_packs_loaded: bool = False

_DEFAULT_NUMBERED_PACKS: dict[int, tuple[int, int]] = {
    1: (100,   1_000),
    2: (500,   5_500),
    3: (5_000, 60_000),
}


def _load_coin_packs() -> None:
    global _packs_loaded
    try:
        db.init_default_coin_packs()
        rows = db.get_all_coin_packs()
        if rows:
            _COIN_PACKS_CACHE.clear()
            for r in rows:
                _COIN_PACKS_CACHE[int(r["pack_id"])] = (
                    int(r["ticket_cost"]),
                    int(r["chillcoins_amount"]),
                )
        else:
            _COIN_PACKS_CACHE.update(_DEFAULT_NUMBERED_PACKS)
    except Exception as e:
        print(f"[COIN PACKS] _load_coin_packs error: {e!r}")
        _COIN_PACKS_CACHE.update(_DEFAULT_NUMBERED_PACKS)
    _packs_loaded = True


def _ensure_packs_loaded() -> None:
    if not _packs_loaded:
        _load_coin_packs()


def get_numbered_pack(pack_id: int) -> tuple[int, int] | None:
    """Return (ticket_cost, chillcoins) for a numbered pack, or None."""
    _ensure_packs_loaded()
    result = db.get_coin_pack_by_id(pack_id)
    if result:
        _COIN_PACKS_CACHE[pack_id] = result
        return result
    return _COIN_PACKS_CACHE.get(pack_id)


def set_numbered_pack(pack_id: int, ticket_cost: int, chillcoins: int) -> None:
    """Persist and cache a numbered pack."""
    db.set_coin_pack(pack_id, ticket_cost, chillcoins)
    _COIN_PACKS_CACHE[pack_id] = (ticket_cost, chillcoins)


async def handle_buypack(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!buypack / !packs — show coin pack menu."""
    db.ensure_user(user.id, user.username)
    s_t, s_c = get_coinpack("smallcoins")
    m_t, m_c = get_coinpack("mediumcoins")
    l_t, l_c = get_coinpack("largecoins")
    await _w(bot, user.id,
             (f"📦 Coin Packs\n"
              f"small — {_fc(s_t)} 🎟️ → {_fc(s_c)} 🪙\n"
              f"medium — {_fc(m_t)} 🎟️ → {_fc(m_c)} 🪙\n"
              f"large — {_fc(l_t)} 🎟️ → {_fc(l_c)} 🪙\n"
              f"Buy: !buycoins small/medium/large")[:249])


async def handle_setcoinpack(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!setcoinpack [pack_id] [ticket_cost] [coins]  — admin only."""
    if not _can_luxe_admin(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    if len(args) < 4:
        await _w(bot, user.id,
                 "Usage: !setcoinpack [id] [tickets] [coins]\n"
                 "Example: !setcoinpack 2 500 5500")
        return
    try:
        pack_id  = int(args[1])
        cost     = int(args[2])
        coins_n  = int(args[3])
        if pack_id < 1 or pack_id > 10:
            raise ValueError("pack_id 1-10")
        if cost < 1 or coins_n < 1:
            raise ValueError("must be positive")
    except (ValueError, IndexError) as e:
        await _w(bot, user.id, f"⚠️ Invalid values: {e}")
        return
    set_numbered_pack(pack_id, cost, coins_n)
    print(f"[COINPACK ADMIN] setcoinpack id={pack_id} cost={cost} coins={coins_n} by={user.username}")
    await _w(bot, user.id,
             f"✅ Pack {pack_id} updated:\n"
             f"{_fc(cost)} 🎫 → {_fc(coins_n)} 🪙\n"
             f"!buypack to verify.")


async def handle_coinpackadmin(bot: "BaseBot", user: "User") -> None:
    """!coinpackadmin — show all coin pack configs (admin)."""
    if not _can_luxe_admin(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return
    _ensure_packs_loaded()
    rows = db.get_all_coin_packs()
    if not rows:
        rows = [
            {"pack_id": k, "ticket_cost": v[0], "chillcoins_amount": v[1], "enabled": 1}
            for k, v in sorted(_DEFAULT_NUMBERED_PACKS.items())
        ]
    lines = ["🎫 Coin Pack Admin"]
    for r in rows:
        status = "ON" if r.get("enabled", 1) else "OFF"
        lines.append(
            f"{r['pack_id']}) {_fc(r['ticket_cost'])} 🎫 → "
            f"{_fc(r['chillcoins_amount'])} 🪙 [{status}]"
        )
    lines.append("!setcoinpack [id] [tickets] [coins]")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def _do_purchase_numbered_pack(
    bot: "BaseBot", user: "User", pack_id: int
) -> None:
    """Buy a numbered coin pack by pack_id."""
    pack = get_numbered_pack(pack_id)
    if pack is None:
        await _w(bot, user.id,
                 f"⚠️ Pack {pack_id} not found. Use !buypack to see options.")
        return
    cost, coins = pack
    bal = get_luxe_balance(user.id)
    if bal < cost:
        await _w(bot, user.id,
                 f"⚠️ Not enough 🎟️.\n"
                 f"Pack {pack_id} costs: {_fc(cost)} 🎟️\n"
                 f"You have: {_fc(bal)} 🎟️")
        return
    # Large-purchase confirmation threshold (>100k tickets OR >50% of balance)
    if cost > 100_000 or (bal > 0 and cost > bal * 0.5):
        import time as _time
        _pending_buycoins[user.id] = {
            "total_cost":   cost,
            "total_coins":  coins,
            "pack_summary": f"pack {pack_id} ({_fc(coins)} 🪙)",
            "expires":      _time.time() + 120,
            "source":       "pack",
            "pack_id":      pack_id,
        }
        await _w(bot, user.id,
                 (f"🛒 Confirm Pack {pack_id}\n"
                  f"Spend: {_fc(cost)} 🎟️\n"
                  f"Receive: {_fc(coins)} 🪙\n"
                  f"Type !confirmbuycoins or !cancelbuycoins")[:249])
        return
    if not deduct_luxe_balance(user.id, user.username, cost):
        await _w(bot, user.id, "⚠️ Transaction failed. Try again.")
        return
    lux_after = get_luxe_balance(user.id)
    db.add_balance(user.id, coins)
    coins_after = db.get_balance(user.id)
    log_luxe_transaction(user.id, user.username, "coinpack_purchase",
                         cost, "luxe", f"pack{pack_id}:{coins}coins")
    try:
        db.log_luxe_conversion(
            user_id=user.id,
            username=user.username.lower(),
            item_key=f"coin_pack_{pack_id}",
            tickets_spent=cost,
            coins_awarded=coins,
            luxe_balance_before=lux_after + cost,
            luxe_balance_after=lux_after,
            coins_balance_before=coins_after - coins,
            coins_balance_after=coins_after,
            status="success",
        )
    except Exception as _ce:
        print(f"[COINPACK] log error: {_ce!r}")
    try:
        db.insert_luxe_ticket_log(
            "coinpack_purchase", user.id, user.username,
            amount=cost, balance_after=lux_after,
            reason=f"pack{pack_id}:{coins}coins",
        )
    except Exception:
        pass
    print(f"[COINPACK] user={user.username} pack={pack_id} spent={cost} coins={coins} status=success")
    await _w(bot, user.id,
             f"✅ @{user.username} bought Pack {pack_id}!\n"
             f"Received: {_fc(coins)} 🪙\n"
             f"Spent: {_fc(cost)} 🎫\n"
             f"Tickets left: {_fc(lux_after)} 🎫")


async def handle_luxehelp(bot: "BaseBot", user: "User") -> None:
    """!luxe / !luxehelp — player help for Luxe Tickets."""
    await _w(bot, user.id,
             "🎟️ Luxe Help\n"
             "Balance: !bal\n"
             "Packs: !packs\n"
             "Buy coins: !buycoins\n"
             "VIP: !vipstatus")


async def handle_vipadmin(bot: "BaseBot", user: "User", args: list[str]) -> None:
    if not _can_luxe_admin(user.username):
        await _w(bot, user.id, "🔒 Admin/owner only.")
        return

    sub = args[1].lower() if len(args) >= 2 else "settings"

    if sub == "settings":
        price    = get_luxe_price("vip")
        duration = get_vip_luxe_duration()
        await _w(bot, user.id,
                 f"👑 VIP Luxe Settings\n"
                 f"Price: {_fc(price)} 🎫\n"
                 f"Duration: {duration} days\n"
                 f"!vipadmin set price <tickets>\n"
                 f"!vipadmin set duration <days>")

    elif sub == "set" and len(args) >= 4:
        what = args[2].lower()
        if what == "price":
            try:
                val = int(args[3])
                if val < 1:
                    raise ValueError
            except ValueError:
                await _w(bot, user.id, "⚠️ Price must be a positive integer.")
                return
            set_luxe_setting("price_vip", str(val))
            await _w(bot, user.id, f"✅ VIP price: {_fc(val)} 🎫")

        elif what == "duration":
            try:
                val = int(args[3])
                if val < 1:
                    raise ValueError
            except ValueError:
                await _w(bot, user.id, "⚠️ Days must be a positive integer.")
                return
            set_luxe_setting("vip_duration_days", str(val))
            await _w(bot, user.id, f"✅ VIP duration: {val} days per purchase.")
        else:
            await _w(bot, user.id,
                     "!vipadmin settings\n"
                     "!vipadmin set price <tickets>\n"
                     "!vipadmin set duration <days>")
    else:
        await _w(bot, user.id,
                 "👑 VIP Admin\n"
                 "!vipadmin settings\n"
                 "!vipadmin set price <tickets>\n"
                 "!vipadmin set duration <days>")
