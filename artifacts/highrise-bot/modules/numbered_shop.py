"""
modules/numbered_shop.py
------------------------
Numbered shop session system.

Players view a numbered shop page, then buy by number instead of typing full IDs.

Session lasts 10 minutes. Confirmation required for purchases >= threshold.

Player commands:
  /buy <number>            — buy item number from last shop viewed
  /buyitem <number>        — alias
  /purchase <number>       — alias
  /confirmbuy <code>       — confirm a pending expensive purchase
  /cancelbuy <code>        — cancel a pending purchase
  /shop next / prev / page <n>
  /badgemarket next / prev
  /eventshop next / prev

Admin commands:
  /shopadmin [badges|titles|event]
  /shoptest <username> <shop_type>
  /setshopconfirm <amount>
  /seteventconfirm <amount>
"""

import random
import string

import database as db
from highrise import BaseBot, User
from modules.permissions import is_admin, is_owner


_COIN_THRESHOLD  = 100_000   # default confirmation threshold for coins
_EVENT_THRESHOLD = 500       # default for event coins


def _can_manage(u: str) -> bool:
    return is_admin(u) or is_owner(u)


def _coin_thresh() -> int:
    try:
        return int(db.get_bot_setting("shop_confirm_threshold", str(_COIN_THRESHOLD)))
    except Exception:
        return _COIN_THRESHOLD


def _event_thresh() -> int:
    try:
        return int(db.get_bot_setting("event_confirm_threshold", str(_EVENT_THRESHOLD)))
    except Exception:
        return _EVENT_THRESHOLD


def _fmt(val: int) -> str:
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val // 1_000}K"
    return f"{val:,}"


def _mk_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


# ---------------------------------------------------------------------------
# /buy <number>  /buyitem <number>  /purchase <number>
# ---------------------------------------------------------------------------

async def handle_buy_number(bot: BaseBot, user: User, args: list[str]) -> None:
    """Handle /buy <integer> from a shop session."""
    raw = args[1] if len(args) > 1 else ""
    if not raw.isdigit():
        # Not a number — fall through (caller should call old handle_buy instead)
        return False  # signal: not handled

    n = int(raw)
    session = db.get_shop_session(user.username)
    if session is None:
        await bot.highrise.send_whisper(
            user.id, "Open a shop first. Example: /shop badges"
        )
        return True

    items = session["items"]
    item  = next((i for i in items if i["num"] == n), None)
    if item is None:
        await bot.highrise.send_whisper(user.id, "Invalid number. Open the shop again.")
        return True

    db.ensure_user(user.id, user.username)
    shop_type = item.get("shop_type") or session["shop_type"]
    currency  = item.get("currency", "coins")
    price     = item.get("price", 0)

    # --- General shop: category selection (opens sub-shop) ---
    if shop_type == "general":
        cat = item.get("category", "")
        if cat == "badges":
            from modules.badge_market import handle_shop_badges
            await handle_shop_badges(bot, user, ["shop", "badges", "1"])
        elif cat == "titles":
            from modules.shop import handle_shop
            await handle_shop(bot, user, ["shop", "titles"])
        elif cat == "vip":
            await bot.highrise.send_whisper(user.id, "💎 VIP: contact an admin to purchase. /vipstatus to check yours.")
        return True

    # --- Market: delegate to badgebuy ---
    if shop_type == "market_badges":
        listing_id = item.get("listing_id")
        if not listing_id:
            await bot.highrise.send_whisper(user.id, "Invalid listing. Open the market again.")
            return True
        from modules.badge_market import handle_badgebuy
        await handle_badgebuy(bot, user, ["badgebuy", str(listing_id)])
        return True

    # --- Confirmation gate ---
    if currency == "coins" and price >= _coin_thresh():
        await _create_confirmation(bot, user, item, shop_type)
        return True
    if currency == "event_coins" and price >= _event_thresh():
        await _create_confirmation(bot, user, item, shop_type)
        return True

    # --- Execute purchase ---
    await _execute_purchase(bot, user, item, shop_type)
    return True


async def _create_confirmation(bot, user, item, shop_type) -> None:
    code  = _mk_code()
    price = item.get("price", 0)
    name  = item.get("name") or item.get("item_id") or "item"
    cur   = item.get("currency", "coins")
    listing_id = item.get("listing_id")

    db.save_pending_purchase(
        code, user.username, shop_type,
        item.get("item_id", ""), name, price, cur, listing_id
    )

    unit = "EC" if cur == "event_coins" else "c"
    await bot.highrise.send_whisper(
        user.id,
        f"Confirm {_fmt(price)}{unit} purchase of {name}:\n"
        f"/confirmbuy {code}\n"
        f"(expires in 5 min)"
    )


async def _execute_purchase(bot, user, item, shop_type) -> None:
    item_id  = item.get("item_id", "")
    currency = item.get("currency", "coins")

    if shop_type == "badges":
        from modules.badge_market import handle_buy_badge
        await handle_buy_badge(bot, user, item_id)

    elif shop_type == "titles":
        from modules.shop import handle_buy
        await handle_buy(bot, user, ["buy", "title", item_id])

    elif shop_type == "event":
        from modules.events import handle_buyevent
        await handle_buyevent(bot, user, ["buyevent", item_id])

    else:
        await bot.highrise.send_whisper(user.id, "Unknown shop type. Open the shop again.")


# ---------------------------------------------------------------------------
# /confirmbuy <code>
# ---------------------------------------------------------------------------

async def handle_confirmbuy(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: /confirmbuy <code>")
        return

    code = args[1].upper().strip()
    pend = db.get_pending_purchase(code)

    if pend is None:
        await bot.highrise.send_whisper(user.id, "Purchase expired or not found. Open the shop again.")
        return

    if pend["username"].lower() != user.username.lower():
        await bot.highrise.send_whisper(user.id, "This confirmation is not yours.")
        return

    db.delete_pending_purchase(code)

    shop_type  = pend["shop_type"]
    item_id    = pend["item_id"]
    listing_id = pend.get("listing_id")
    currency   = pend["currency"]

    db.ensure_user(user.id, user.username)

    if shop_type == "market_badges" and listing_id:
        from modules.badge_market import handle_badgebuy
        await handle_badgebuy(bot, user, ["badgebuy", str(listing_id)])
        return

    fake_item = {
        "item_id":  item_id,
        "name":     pend["item_name"],
        "price":    pend["price"],
        "currency": currency,
    }
    await _execute_purchase(bot, user, fake_item, shop_type)


# ---------------------------------------------------------------------------
# /cancelbuy <code>
# ---------------------------------------------------------------------------

async def handle_cancelbuy(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: /cancelbuy <code>")
        return

    code = args[1].upper().strip()
    pend = db.get_pending_purchase(code)

    if pend is None:
        await bot.highrise.send_whisper(user.id, "Purchase not found or already expired.")
        return

    if pend["username"].lower() != user.username.lower() and not _can_manage(user.username):
        await bot.highrise.send_whisper(user.id, "This confirmation is not yours.")
        return

    db.delete_pending_purchase(code)
    await bot.highrise.send_whisper(user.id, f"✅ Purchase {code} cancelled.")


# ---------------------------------------------------------------------------
# /shop next / prev / page <n>
# ---------------------------------------------------------------------------

async def handle_shop_nav(bot: BaseBot, user: User, args: list[str]) -> None:
    """Handle /shop next, /shop prev, /shop page <n>."""
    sub = args[1].lower() if len(args) > 1 else ""

    if sub in ("next", "prev", "page"):
        session = db.get_shop_session(user.username)
        shop_type = session["shop_type"] if session else "badges"
        cur_page  = session["page"]      if session else 1

        if sub == "next":
            new_page = cur_page + 1
        elif sub == "prev":
            new_page = max(1, cur_page - 1)
        else:
            raw = args[2] if len(args) > 2 else "1"
            new_page = int(raw) if raw.isdigit() else 1

        if shop_type == "badges":
            from modules.badge_market import handle_shop_badges
            await handle_shop_badges(bot, user, ["shop", "badges", str(new_page)])
        elif shop_type == "titles":
            from modules.shop import handle_shop
            await handle_shop(bot, user, ["shop", "titles", str(new_page)])
        elif shop_type == "event":
            from modules.events import handle_eventshop
            await handle_eventshop(bot, user)
        else:
            from modules.badge_market import handle_shop_badges
            await handle_shop_badges(bot, user, ["shop", "badges", str(new_page)])
        return

    # Fall through to general shop handler
    from modules.shop import handle_shop
    await handle_shop(bot, user, args)


# ---------------------------------------------------------------------------
# /badgemarket next / prev
# ---------------------------------------------------------------------------

async def handle_badgemarket_nav(bot: BaseBot, user: User, args: list[str]) -> None:
    sub = args[1].lower() if len(args) > 1 else ""
    if sub in ("next", "prev"):
        session   = db.get_shop_session(user.username)
        cur_page  = session["page"] if session and session.get("shop_type") == "market_badges" else 1
        new_page  = (cur_page + 1) if sub == "next" else max(1, cur_page - 1)
        from modules.badge_market import handle_badgemarket
        await handle_badgemarket(bot, user, ["badgemarket", str(new_page)])
    else:
        from modules.badge_market import handle_badgemarket
        await handle_badgemarket(bot, user, args)


# ---------------------------------------------------------------------------
# /eventshop next / prev
# ---------------------------------------------------------------------------

async def handle_eventshop_nav(bot: BaseBot, user: User, args: list[str]) -> None:
    # Event shop is a single page, just re-show it
    from modules.events import handle_eventshop
    await handle_eventshop(bot, user)


# ---------------------------------------------------------------------------
# /shopadmin [badges|titles|event]  /shoptest <user> <type>
# ---------------------------------------------------------------------------

async def handle_shopadmin(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        return
    sub = args[1].lower() if len(args) > 1 else ""

    if sub == "badges":
        from modules.badge_market import handle_shop_badges
        await handle_shop_badges(bot, user, ["shop", "badges", "1"])
    elif sub == "titles":
        from modules.shop import handle_shop
        await handle_shop(bot, user, ["shop", "titles", "1"])
    elif sub == "event":
        from modules.events import handle_eventshop
        await handle_eventshop(bot, user)
    else:
        await bot.highrise.send_whisper(
            user.id,
            "🛍️ Shop Admin\n"
            "/shopadmin badges — badge catalog\n"
            "/shopadmin titles — title catalog\n"
            "/shopadmin event  — event shop\n"
            "/shoptest <user> <badges|titles|event>"
        )


async def handle_shoptest(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        return
    if len(args) < 3:
        await bot.highrise.send_whisper(user.id, "Usage: /shoptest <username> <badges|titles|event>")
        return

    target    = args[1].strip().lstrip("@")
    shop_type = args[2].lower()

    # Create a fake User object with target username for display
    class _FakeUser:
        def __init__(self, uid, uname):
            self.id       = uid
            self.username = uname

    rec = db.get_user_by_username(target)
    uid = rec["user_id"] if rec else user.id
    fake_user = _FakeUser(uid, target)

    await bot.highrise.send_whisper(user.id, f"🛍️ Simulating /shop {shop_type} for @{target}:")

    if shop_type == "badges":
        from modules.badge_market import handle_shop_badges
        await handle_shop_badges(bot, fake_user, ["shop", "badges", "1"])
    elif shop_type == "titles":
        from modules.shop import handle_shop
        await handle_shop(bot, fake_user, ["shop", "titles", "1"])
    elif shop_type == "event":
        from modules.events import handle_eventshop
        await handle_eventshop(bot, fake_user)
    else:
        await bot.highrise.send_whisper(user.id, "Shop type: badges, titles, or event.")


# ---------------------------------------------------------------------------
# /setshopconfirm <amount>  /seteventconfirm <amount>
# ---------------------------------------------------------------------------

async def handle_setshopconfirm(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        return
    if len(args) < 2:
        cur = _coin_thresh()
        await bot.highrise.send_whisper(user.id, f"Current confirm threshold: {cur:,}c. Set: /setshopconfirm <amount>")
        return
    raw = args[1].strip()
    if not raw.isdigit():
        await bot.highrise.send_whisper(user.id, "Amount must be a positive integer.")
        return
    db.set_bot_setting("shop_confirm_threshold", raw)
    await bot.highrise.send_whisper(user.id, f"✅ Confirm threshold set to {int(raw):,}c.")


async def handle_seteventconfirm(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        return
    if len(args) < 2:
        cur = _event_thresh()
        await bot.highrise.send_whisper(user.id, f"Current event confirm threshold: {cur} EC. Set: /seteventconfirm <amount>")
        return
    raw = args[1].strip()
    if not raw.isdigit():
        await bot.highrise.send_whisper(user.id, "Amount must be a positive integer.")
        return
    db.set_bot_setting("event_confirm_threshold", raw)
    await bot.highrise.send_whisper(user.id, f"✅ Event confirm threshold set to {int(raw)} EC.")
