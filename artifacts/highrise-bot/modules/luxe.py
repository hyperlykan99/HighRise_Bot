"""
modules/luxe.py
---------------
🎫 Luxe Tickets — premium room currency (3.1I ADDON).

Players earn Luxe Tickets from verified Highrise Gold tips.
Tickets are spent in the Luxe Shop for premium items.
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

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_PRICES: dict[str, int] = {
    "automine1h":   100,
    "autofish1h":   100,
    "luckyhour":    150,
    "treasurehour": 200,
    "vip":          500,
}

_DEFAULT_COINPACKS: dict[str, tuple[int, int]] = {
    "small":  (50,   50_000),
    "medium": (100, 125_000),
    "large":  (250, 350_000),
}

_ITEM_LABELS: dict[str, str] = {
    "automine1h":   "1h Auto-Mining Permit",
    "autofish1h":   "1h Auto-Fishing Permit",
    "luckyhour":    "Lucky Hour Boost",
    "treasurehour": "Treasure Hour Boost",
    "vip":          "VIP Pass",
}


# ---------------------------------------------------------------------------
# DB helpers (all open their own connection — no shared state)
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
    """Credit Luxe Tickets. Returns new balance."""
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
    """Deduct Luxe Tickets. Returns True if successful, False if insufficient."""
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
    """Tickets awarded per Highrise Gold. Default 1."""
    try:
        return max(1, int(get_luxe_setting("luxe_rate", "1")))
    except ValueError:
        return 1


def get_luxe_price(item_key: str) -> int:
    default = _DEFAULT_PRICES.get(item_key, 0)
    try:
        return max(1, int(get_luxe_setting(f"price_{item_key}", str(default))))
    except ValueError:
        return default


def get_coinpack(size: str) -> tuple[int, int]:
    """Returns (ticket_cost, coins_awarded)."""
    defaults = _DEFAULT_COINPACKS.get(size, (0, 0))
    try:
        cost   = int(get_luxe_setting(f"coinpack_{size}_tickets", str(defaults[0])))
        reward = int(get_luxe_setting(f"coinpack_{size}_coins",   str(defaults[1])))
        return (cost, reward)
    except ValueError:
        return defaults


def get_vip_luxe_duration() -> int:
    """Days of VIP granted by a Luxe ticket VIP purchase."""
    try:
        return max(1, int(get_luxe_setting("vip_duration_days", "30")))
    except ValueError:
        return 30


def get_permit_count(user_id: str, permit_type: str) -> int:
    """permit_type: 'automine' or 'autofish'"""
    try:
        return max(0, int(get_luxe_setting(f"permits_{user_id}_{permit_type}", "0")))
    except ValueError:
        return 0


def add_permit(user_id: str, permit_type: str, count: int = 1) -> int:
    """Add permits. Returns new count."""
    current = get_permit_count(user_id, permit_type)
    new_count = current + count
    set_luxe_setting(f"permits_{user_id}_{permit_type}", str(new_count))
    return new_count


def use_permit(user_id: str, permit_type: str) -> bool:
    """Consume one permit. Returns True if successful."""
    current = get_permit_count(user_id, permit_type)
    if current < 1:
        return False
    set_luxe_setting(f"permits_{user_id}_{permit_type}", str(current - 1))
    return True


# ---------------------------------------------------------------------------
# Public player commands
# ---------------------------------------------------------------------------

async def handle_tickets(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!tickets !luxe — show Luxe Ticket balance + how to earn."""
    db.ensure_user(user.id, user.username)
    bal  = get_luxe_balance(user.id)
    rate = get_luxe_rate()
    am   = get_permit_count(user.id, "automine")
    af   = get_permit_count(user.id, "autofish")
    lines = [
        "🎫 Luxe Tickets",
        f"Balance: {_fc(bal)} 🎫",
        f"Earn: tip Highrise Gold ({rate} 🎫/gold)",
        "Spend: !luxeshop",
    ]
    if am or af:
        lines.append(f"Permits: ⛏️ {am}x mine  🎣 {af}x fish")
    await _w(bot, user.id, "\n".join(lines))


async def handle_luxeshop(bot: "BaseBot", user: "User") -> None:
    """!luxeshop !premiumshop — show Luxe Ticket shop."""
    p_am  = get_luxe_price("automine1h")
    p_af  = get_luxe_price("autofish1h")
    p_lh  = get_luxe_price("luckyhour")
    p_th  = get_luxe_price("treasurehour")
    p_vip = get_luxe_price("vip")
    dur   = get_vip_luxe_duration()
    cs_t, cs_c = get_coinpack("small")
    cm_t, cm_c = get_coinpack("medium")
    cl_t, cl_c = get_coinpack("large")
    await _w(bot, user.id,
             f"🎫 Luxe Shop\n"
             f"⛏️ 1h AutoMine — {_fc(p_am)}🎫 (!buyticket automine1h)\n"
             f"🎣 1h AutoFish — {_fc(p_af)}🎫 (!buyticket autofish1h)\n"
             f"🍀 Lucky Hour — {_fc(p_lh)}🎫 (!buyticket luckyhour)\n"
             f"💎 VIP {dur}d — {_fc(p_vip)}🎫 (!buyticket vip)")
    await _w(bot, user.id,
             f"🪙 ChillCoin Packs (!buycoins)\n"
             f"Small:  {_fc(cs_t)}🎫 → {_fc(cs_c)}🪙\n"
             f"Medium: {_fc(cm_t)}🎫 → {_fc(cm_c)}🪙\n"
             f"Large:  {_fc(cl_t)}🎫 → {_fc(cl_c)}🪙\n"
             f"!tickets — check your balance")


async def handle_buyticket(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!buyticket <item> — purchase a Luxe item."""
    db.ensure_user(user.id, user.username)
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: !buyticket <item>\n"
                 "Items: automine1h  autofish1h  luckyhour\n"
                 "       treasurehour  vip\n"
                 "Coin packs: !buycoins small/medium/large\n"
                 "Browse: !luxeshop")
        return

    item = args[1].lower().strip()
    if item not in _DEFAULT_PRICES:
        await _w(bot, user.id,
                 f"Unknown item: {item}\n"
                 "Items: automine1h autofish1h luckyhour\n"
                 "       treasurehour vip\n"
                 "Coin packs: !buycoins small/medium/large")
        return

    price = get_luxe_price(item)
    bal   = get_luxe_balance(user.id)
    if bal < price:
        await _w(bot, user.id,
                 f"⚠️ Not enough Luxe Tickets.\n"
                 f"Need: {_fc(price)} 🎫  You have: {_fc(bal)} 🎫\n"
                 f"Earn by tipping Highrise Gold to any bot.")
        return

    if not deduct_luxe_balance(user.id, user.username, price):
        await _w(bot, user.id, "⚠️ Transaction failed. Try again.")
        return

    log_luxe_transaction(user.id, user.username, "purchase", price, "luxe", item)

    if item in ("automine1h", "autofish1h"):
        permit_type = "automine" if item == "automine1h" else "autofish"
        count = add_permit(user.id, permit_type)
        label = "Auto-Mining" if item == "automine1h" else "Auto-Fishing"
        cmd   = "!use automine1h" if item == "automine1h" else "!use autofish1h"
        await _w(bot, user.id,
                 f"✅ 1h {label} Permit added!\n"
                 f"Permits held: {count}\n"
                 f"Use {cmd} when ready to start.")

    elif item == "luckyhour":
        expires = (
            _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)
        ).isoformat()
        try:
            db.add_player_boost(user.id, user.username, "luck",
                                "mining",  5, expires, "luckyhour")
            db.add_player_boost(user.id, user.username, "luck",
                                "fishing", 5, expires, "luckyhour")
        except Exception:
            pass
        await _w(bot, user.id,
                 "✅ Lucky Hour activated!\n"
                 "Duration: 1 hour\n"
                 "+5 luck for mining & fishing.\n"
                 "Good luck! 🍀")

    elif item == "treasurehour":
        expires = (
            _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=1)
        ).isoformat()
        try:
            db.add_player_boost(user.id, user.username, "value",
                                "mining",  10, expires, "treasurehour")
            db.add_player_boost(user.id, user.username, "value",
                                "fishing", 10, expires, "treasurehour")
        except Exception:
            pass
        await _w(bot, user.id,
                 "✅ Treasure Hour activated!\n"
                 "Duration: 1 hour\n"
                 "+10% value bonus for mining & fishing.")

    elif item == "vip":
        await _deliver_vip(bot, user)


async def _deliver_vip(bot: "BaseBot", user: "User") -> None:
    """Grant or extend VIP via Luxe Tickets."""
    duration_days = get_vip_luxe_duration()
    now_dt = _dt.datetime.now(_dt.timezone.utc)
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
    db.log_admin_action(user.username, user.username,
                        "buyvip_luxe", "", f"{duration_days}d")
    await _w(bot, user.id,
             f"💎 VIP {action}!\n"
             f"Duration added: {duration_days}d\n"
             f"Expires: {new_exp_str}\n"
             f"Perks: longer AutoMine/AutoFish + VIP status")


async def handle_buycoins(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!buycoins <small|medium|large> — buy ChillCoins with Luxe Tickets."""
    db.ensure_user(user.id, user.username)
    sizes = ("small", "medium", "large")

    if len(args) < 2 or args[1].lower() not in sizes:
        cs_t, cs_c = get_coinpack("small")
        cm_t, cm_c = get_coinpack("medium")
        cl_t, cl_c = get_coinpack("large")
        await _w(bot, user.id,
                 f"🪙 ChillCoin Packs\n"
                 f"Small:  {_fc(cs_t)}🎫 → {_fc(cs_c)}🪙\n"
                 f"Medium: {_fc(cm_t)}🎫 → {_fc(cm_c)}🪙\n"
                 f"Large:  {_fc(cl_t)}🎫 → {_fc(cl_c)}🪙\n"
                 f"Usage: !buycoins small/medium/large")
        return

    size = args[1].lower()
    cost, coins = get_coinpack(size)
    if cost <= 0 or coins <= 0:
        await _w(bot, user.id, f"Pack '{size}' is not configured. Ask staff.")
        return

    bal = get_luxe_balance(user.id)
    if bal < cost:
        await _w(bot, user.id,
                 f"⚠️ Not enough Luxe Tickets.\n"
                 f"Need: {_fc(cost)} 🎫  You have: {_fc(bal)} 🎫")
        return

    if not deduct_luxe_balance(user.id, user.username, cost):
        await _w(bot, user.id, "⚠️ Transaction failed. Try again.")
        return

    log_luxe_transaction(user.id, user.username, "buycoins", cost, "luxe", size)
    db.add_balance(user.id, coins)
    log_luxe_transaction(user.id, user.username, "buycoins_award",
                         coins, "coins", size)
    new_coins = db.get_balance(user.id)
    await _w(bot, user.id,
             f"✅ {size.capitalize()} pack purchased!\n"
             f"Paid: {_fc(cost)} 🎫  Got: {_fc(coins)} 🪙\n"
             f"Coin balance: {_fc(new_coins)} 🪙")


async def handle_use(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!use <automine1h|autofish1h> — use a purchased session permit."""
    db.ensure_user(user.id, user.username)
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: !use automine1h | !use autofish1h\n"
                 "Permits are bought at !luxeshop.")
        return

    item = args[1].lower()

    if item == "automine1h":
        am = get_permit_count(user.id, "automine")
        if am < 1:
            p = get_luxe_price("automine1h")
            await _w(bot, user.id,
                     f"⚠️ No Auto-Mining permits.\n"
                     f"Buy one: !buyticket automine1h ({_fc(p)} 🎫)")
            return
        if not use_permit(user.id, "automine"):
            await _w(bot, user.id, "⚠️ No permits available. Buy at !luxeshop.")
            return
        log_luxe_transaction(user.id, user.username,
                             "use_permit", 1, "permit", "automine1h")
        remaining = get_permit_count(user.id, "automine")
        from modules.mining import handle_automine
        await handle_automine(bot, user, ["automine", "on"])
        await _w(bot, user.id,
                 f"⛏️ Permit used! Session started.\n"
                 f"Remaining permits: {remaining}")

    elif item == "autofish1h":
        af = get_permit_count(user.id, "autofish")
        if af < 1:
            p = get_luxe_price("autofish1h")
            await _w(bot, user.id,
                     f"⚠️ No Auto-Fishing permits.\n"
                     f"Buy one: !buyticket autofish1h ({_fc(p)} 🎫)")
            return
        if not use_permit(user.id, "autofish"):
            await _w(bot, user.id, "⚠️ No permits available. Buy at !luxeshop.")
            return
        log_luxe_transaction(user.id, user.username,
                             "use_permit", 1, "permit", "autofish1h")
        remaining = get_permit_count(user.id, "autofish")
        from modules.fishing import handle_autofish
        await handle_autofish(bot, user, ["autofish", "on"])
        await _w(bot, user.id,
                 f"🎣 Permit used! Session started.\n"
                 f"Remaining permits: {remaining}")

    else:
        await _w(bot, user.id,
                 "Usage: !use automine1h | !use autofish1h\n"
                 "See !luxeshop for all items.")


# ---------------------------------------------------------------------------
# Admin: !luxeadmin
# ---------------------------------------------------------------------------

def _can_luxe_admin(username: str) -> bool:
    return is_admin(username) or is_owner(username)


async def handle_luxeadmin(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!luxeadmin — Luxe economy admin commands."""
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
        for key, label in _ITEM_LABELS.items():
            lines.append(f"{key}: {_fc(get_luxe_price(key))} 🎫")
        cs_t, cs_c = get_coinpack("small")
        cm_t, cm_c = get_coinpack("medium")
        cl_t, cl_c = get_coinpack("large")
        await _w(bot, user.id, "\n".join(lines)[:249])
        await _w(bot, user.id,
                 f"Coin packs:\n"
                 f"small  {_fc(cs_t)}🎫→{_fc(cs_c)}🪙\n"
                 f"medium {_fc(cm_t)}🎫→{_fc(cm_c)}🪙\n"
                 f"large  {_fc(cl_t)}🎫→{_fc(cl_c)}🪙")

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
            await _w(bot, user.id,
                     f"✅ Luxe rate set: 1 Gold = {val} 🎫 Luxe Ticket(s).")

        elif what == "price" and len(args) >= 5:
            item = args[3].lower()
            if item not in _DEFAULT_PRICES:
                valid = " ".join(_DEFAULT_PRICES)
                await _w(bot, user.id, f"Unknown item: {item}\nValid: {valid}"[:249])
                return
            try:
                val = int(args[4])
                if val < 1:
                    raise ValueError
            except ValueError:
                await _w(bot, user.id, "⚠️ Price must be a positive integer.")
                return
            set_luxe_setting(f"price_{item}", str(val))
            await _w(bot, user.id, f"✅ {item}: {_fc(val)} 🎫")

        elif what == "coinpack" and len(args) >= 6:
            size = args[3].lower()
            if size not in ("small", "medium", "large"):
                await _w(bot, user.id, "Size must be small, medium, or large.")
                return
            try:
                tickets = int(args[4])
                coins   = int(args[5])
                if tickets < 1 or coins < 1:
                    raise ValueError
            except ValueError:
                await _w(bot, user.id,
                         "⚠️ Tickets and coins must be positive integers.")
                return
            set_luxe_setting(f"coinpack_{size}_tickets", str(tickets))
            set_luxe_setting(f"coinpack_{size}_coins",   str(coins))
            await _w(bot, user.id,
                     f"✅ {size.capitalize()} pack: "
                     f"{_fc(tickets)}🎫 → {_fc(coins)}🪙")

        else:
            await _w(bot, user.id,
                     "!luxeadmin set rate <n>\n"
                     "!luxeadmin set price <item> <tickets>\n"
                     "!luxeadmin set coinpack <size> <tickets> <coins>")

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
                             "admin_grant", amount, "luxe",
                             f"by @{user.username}")
        await _w(bot, user.id,
                 f"✅ Granted {_fc(amount)}🎫 to @{rec['username']}.\n"
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
                             "admin_revoke", actual, "luxe",
                             f"by @{user.username}")
        new_bal = get_luxe_balance(rec["user_id"])
        await _w(bot, user.id,
                 f"✅ Revoked {_fc(actual)}🎫 from @{rec['username']}.\n"
                 f"New balance: {_fc(new_bal)} 🎫")

    elif sub == "check" and len(args) >= 3:
        target_name = args[2].lstrip("@").strip()
        rec = db.get_user_by_username(target_name)
        if not rec:
            await _w(bot, user.id, f"@{target_name} not found in DB.")
            return
        bal = get_luxe_balance(rec["user_id"])
        am  = get_permit_count(rec["user_id"], "automine")
        af  = get_permit_count(rec["user_id"], "autofish")
        await _w(bot, user.id,
                 f"🎫 @{rec['username']}: {_fc(bal)} Luxe Tickets\n"
                 f"Permits: ⛏️ {am}x mine  🎣 {af}x fish")

    else:
        await _w(bot, user.id,
                 "🎫 Luxe Admin\n"
                 "!luxeadmin rate\n"
                 "!luxeadmin prices\n"
                 "!luxeadmin set rate <n>\n"
                 "!luxeadmin set price <item> <tickets>")
        await _w(bot, user.id,
                 "!luxeadmin set coinpack <size> <t> <c>\n"
                 "!luxeadmin grant @user <n>  (owner)\n"
                 "!luxeadmin revoke @user <n> (owner)\n"
                 "!luxeadmin check @user")


# ---------------------------------------------------------------------------
# Admin: !vipadmin
# ---------------------------------------------------------------------------

async def handle_vipadmin(bot: "BaseBot", user: "User", args: list[str]) -> None:
    """!vipadmin — VIP Luxe price & duration admin."""
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
            await _w(bot, user.id, f"✅ VIP Luxe price set: {_fc(val)} 🎫")

        elif what == "duration":
            try:
                val = int(args[3])
                if val < 1:
                    raise ValueError
            except ValueError:
                await _w(bot, user.id, "⚠️ Days must be a positive integer.")
                return
            set_luxe_setting("vip_duration_days", str(val))
            await _w(bot, user.id,
                     f"✅ VIP Luxe duration set: {val} days per purchase.")

        else:
            await _w(bot, user.id,
                     "👑 VIP Admin\n"
                     "!vipadmin settings\n"
                     "!vipadmin set price <tickets>\n"
                     "!vipadmin set duration <days>")

    else:
        await _w(bot, user.id,
                 "👑 VIP Admin\n"
                 "!vipadmin settings\n"
                 "!vipadmin set price <tickets>\n"
                 "!vipadmin set duration <days>")
