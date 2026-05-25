"""
modules/shop.py
---------------
Badge and title shop for the Mini Game Bot.

Players spend coins on badges (emojis) and titles, then equip them to
personalise how they appear in every public announcement:

    🔥 @PlayerName [High Roller]

Only the currently-equipped badge and title grant benefits — owning an
item gives no bonus until it is equipped.

Benefits stack from badge + title and are capped:
  - Max XP bonus per game win:      +50 XP
  - Max daily coins bonus:          +100 coins
  - Max coinflip payout bonus:      +15%
  - Max game reward bonus:          +20%

Commands handled here:
    /shop              — top-level help
    /shop badges       — list all badges (sent as 2 whispers for length)
    /shop titles       — list all titles  (sent as 2 whispers for length)
    /buy badge <id>    — purchase a badge
    /buy title <id>    — purchase a title
    /equip badge <id>  — equip an owned badge
    /equip title <id>  — equip an owned title
    /myitems           — show owned badges and titles
"""

import math
from highrise import BaseBot, User
import database as db
from modules.achievements import check_achievements


# ---------------------------------------------------------------------------
# Shop catalog
# Each item may include a "benefits" dict with any subset of benefit keys.
# Items without "benefits" (or with an empty dict) are cosmetic-only.
# Phase 2 sink pacing: starter cosmetics are 5k-25k, mid-tier flex goals
# are 50k-250k, and prestige titles start at 750k+.
# ---------------------------------------------------------------------------

BADGE_PRICE_STARTER = 5_000
BADGE_PRICE_ENTRY = 7_500
BADGE_PRICE_EARLY = 12_500
BADGE_PRICE_MID = 25_000
BADGE_PRICE_PREMIUM = 75_000
BADGE_PRICE_PRESTIGE = 125_000

TITLE_PRICE_BEGINNER = 10_000
TITLE_PRICE_ENTRY = 20_000
TITLE_PRICE_EARLY = 25_000
TITLE_PRICE_MID = 50_000
TITLE_PRICE_ADVANCED = 75_000
TITLE_PRICE_UPPER = 150_000
TITLE_PRICE_HIGH = 250_000
TITLE_PRICE_PREMIUM = 750_000
TITLE_PRICE_PRESTIGE = 1_500_000

BADGES: dict[str, dict] = {
    # ── Beginner tier ───────────────────────────────────────────────────────
    "star_badge":      {"display": "⭐", "price": BADGE_PRICE_STARTER,  "description": "+1 XP/win",              "benefits": {"xp_bonus": 1}},
    "heart_badge":     {"display": "💖", "price": BADGE_PRICE_STARTER,  "description": "Cosmetic only"},
    "music_badge":     {"display": "🎵", "price": BADGE_PRICE_ENTRY,    "description": "Cosmetic only"},
    "fire_badge":      {"display": "🔥", "price": BADGE_PRICE_ENTRY,    "description": "+2 XP/win",              "benefits": {"xp_bonus": 2}},
    "dice_badge":      {"display": "🎲", "price": TITLE_PRICE_BEGINNER, "description": "+2% coinflip payout",    "benefits": {"coinflip_payout_pct": 2.0}},
    # ── Early grind tier ────────────────────────────────────────────────────
    "skull_badge":     {"display": "💀", "price": BADGE_PRICE_EARLY,    "description": "+3 XP/win",              "benefits": {"xp_bonus": 3}},
    "lightning_badge": {"display": "⚡", "price": 15_000,               "description": "-5s coinflip cooldown",  "benefits": {"cooldown_reduction": 5}},
    "gem_badge":       {"display": "💎", "price": 20_000,               "description": "+5 daily coins",         "benefits": {"daily_coins_bonus": 5}},
    # ── Mid-game tier ───────────────────────────────────────────────────────
    "crown_badge":     {"display": "👑", "price": BADGE_PRICE_MID,      "description": "+5 XP/win",              "benefits": {"xp_bonus": 5}},
    "angel_badge":     {"display": "😇", "price": 35_000,               "description": "+10 daily coins",        "benefits": {"daily_coins_bonus": 10}},
    "dragon_badge":    {"display": "🐉", "price": TITLE_PRICE_MID,      "description": "+10 XP/win",             "benefits": {"xp_bonus": 10}},
    # ── Upper cosmetic/prestige tier ────────────────────────────────────────
    "demon_badge":     {"display": "😈", "price": BADGE_PRICE_PREMIUM,  "description": "+5% coinflip payout",    "benefits": {"coinflip_payout_pct": 5.0}},
    "trophy_badge":    {"display": "🏆", "price": BADGE_PRICE_PRESTIGE, "description": "+15 XP/win",             "benefits": {"xp_bonus": 15}},
}

TITLES: dict[str, dict] = {
    # ── Entry tier ──────────────────────────────────────────────────────────
    "rookie":      {"display": "[Lounge Rookie]", "price": TITLE_PRICE_BEGINNER, "description": "Fresh face in the ChillTopia lounge | +5 daily coins",             "benefits": {"daily_coins_bonus": 5}},
    "lucky":       {"display": "[Lucky Vibe]",    "price": TITLE_PRICE_ENTRY,   "description": "Good energy follows you into every room | +2% coinflip payout",      "benefits": {"coinflip_payout_pct": 2.0}},
    "grinder":     {"display": "[Rising Regular]","price": TITLE_PRICE_EARLY,   "description": "Known face, steady grind, better wins | +10 XP from game wins",     "benefits": {"xp_bonus": 10}},
    # ── Mid tier — game-specific prestige ───────────────────────────────────
    "trivia_king": {"display": "[Trivia Icon]",   "price": TITLE_PRICE_MID,     "description": "The answer everyone waits for | +10 trivia coins",                "benefits": {"trivia_bonus": 10}},
    "word_master": {"display": "[Wordplay Star]", "price": TITLE_PRICE_MID,     "description": "Quick with letters, quicker with wins | +10 scramble coins",       "benefits": {"scramble_bonus": 10}},
    "riddle_lord": {"display": "[Mystery Maven]", "price": TITLE_PRICE_MID,     "description": "Puzzle-room reputation | +10 riddle coins",                     "benefits": {"riddle_bonus": 10}},
    "casino_rat":  {"display": "[Lucky Lounge]",  "price": TITLE_PRICE_ADVANCED,"description": "Casino-table confidence | +5% casino payout",                   "benefits": {"coinflip_payout_pct": 5.0}},
    # ── Upper tier — high-commitment goals ──────────────────────────────────
    "high_roller": {"display": "[Velvet VIP]",    "price": TITLE_PRICE_UPPER,   "description": "High-stakes lounge energy | +10% casino payout",                 "benefits": {"coinflip_payout_pct": 10.0}},
    "millionaire": {"display": "[Penthouse Flex]","price": TITLE_PRICE_HIGH,    "description": "Wealthy room presence | +25 daily coins and +25 daily XP",       "benefits": {"daily_coins_bonus": 25, "daily_xp_bonus": 25}},
    # ── Endgame tier — long-term prestige ───────────────────────────────────
    "elite":       {"display": "[Penthouse Elite]","price": TITLE_PRICE_PREMIUM, "description": "Top-floor status | +15% all game coin rewards",                  "benefits": {"game_reward_pct": 15.0}},
    "immortal":    {"display": "[Metaverse Legend]","price": TITLE_PRICE_PRESTIGE,"description": "A name the whole room recognizes | +20% game coins, +50 daily",  "benefits": {"game_reward_pct": 20.0, "daily_coins_bonus": 50}},
}


# ---------------------------------------------------------------------------
# Benefit system
# ---------------------------------------------------------------------------

DEFAULT_BENEFITS: dict = {
    "xp_bonus":            0,     # flat XP added to every game win
    "daily_coins_bonus":   0,     # flat coins added to /daily reward
    "daily_xp_bonus":      0,     # flat XP added to /daily reward
    "coinflip_payout_pct": 0.0,   # % bonus on coinflip WIN payout
    "game_reward_pct":     0.0,   # % bonus on trivia/scramble/riddle coins
    "trivia_bonus":        0,     # flat extra coins on trivia wins
    "scramble_bonus":      0,     # flat extra coins on scramble wins
    "riddle_bonus":        0,     # flat extra coins on riddle wins
    "cooldown_reduction":  0,     # seconds subtracted from coinflip cooldown
}

_CAPS: dict = {
    "xp_bonus":            50,
    "daily_coins_bonus":   100,
    "coinflip_payout_pct": 15.0,
    "game_reward_pct":     20.0,
}


def get_player_benefits(user_id: str) -> dict:
    """
    Return the stacked, capped benefits from the player's equipped badge + title.
    Cosmetic-only items (no 'benefits' key) contribute nothing.
    Always safe to call — returns all-zero dict if player has nothing equipped.
    """
    equipped = db.get_equipped_ids(user_id)
    benefits = dict(DEFAULT_BENEFITS)

    for item_id, item_type in [
        (equipped.get("badge_id"), "badge"),
        (equipped.get("title_id"), "title"),
    ]:
        if not item_id:
            continue
        catalog = BADGES if item_type == "badge" else TITLES
        item = catalog.get(item_id)
        if not item:
            continue
        for key, val in item.get("benefits", {}).items():
            if key in benefits:
                benefits[key] += val

    for key, cap in _CAPS.items():
        benefits[key] = min(benefits[key], cap)

    return benefits


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_item(item_type: str, item_id: str) -> dict | None:
    from modules.events     import EVENT_BADGES, EVENT_TITLES
    from modules.reputation import REP_TITLES
    catalog       = BADGES       if item_type == "badge" else TITLES
    event_catalog = EVENT_BADGES if item_type == "badge" else EVENT_TITLES
    rep_catalog   = {}           if item_type == "badge" else REP_TITLES
    return (catalog.get(item_id)
            or event_catalog.get(item_id)
            or rep_catalog.get(item_id))


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

_PAGE_SIZE = 5


def _fmt_p(val: int) -> str:
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M 🪙"
    if val >= 1_000:
        return f"{val // 1_000}K 🪙"
    return f"{val:,} 🪙"


async def _send_catalog_page(
    bot, user, catalog: dict, item_type: str, args: list[str]
) -> None:
    """Send one page of 5 numbered items from a badge or title catalog."""
    items       = list(catalog.items())
    total_pages = max(1, math.ceil(len(items) / _PAGE_SIZE))

    # Parse the optional page number
    raw_page = args[2] if len(args) > 2 else "1"
    if not raw_page.isdigit():
        raw_page = "1"
    page = max(1, min(int(raw_page), total_pages))

    start = (page - 1) * _PAGE_SIZE
    chunk = items[start : start + _PAGE_SIZE]

    db.ensure_user(user.id, user.username)
    session_items = []
    shop_type = f"{item_type}s"   # "badges" or "titles"
    label     = "Titles" if item_type == "title" else "Badges"
    lines     = [f"🏷️ {label} {page}/{total_pages}"]

    for num, (item_id, data) in enumerate(chunk, 1):
        display = data.get("display", "?")
        price   = data.get("price", 0)
        owned   = db.owns_item(user.id, item_id)
        tick    = "✅" if owned else ""
        lines.append(f"{num} {tick}{display} {item_id} {_fmt_p(price)}")
        session_items.append({
            "num":       num,
            "item_id":   item_id,
            "name":      display,
            "emoji":     "",
            "price":     price,
            "currency":  "coins",
            "shop_type": shop_type,
        })

    nav = []
    if page > 1:
        nav.append("!shop prev")
    if page < total_pages:
        nav.append("!shop next")
    footer = "Buy: !buy [#]" + (f"  {' | '.join(nav)}" if nav else "")
    lines.append(footer)

    msg = "\n".join(lines)
    if len(msg) > 249:
        lines = [f"🏷️ {label} {page}/{total_pages}"]
        for item in session_items:
            owned = db.owns_item(user.id, item["item_id"])
            tick  = "✅" if owned else ""
            lines.append(f"{item['num']} {tick}{item['name']} {_fmt_p(item['price'])}")
        lines.append("Buy: !buy <#>  More: !shop next")
        msg = "\n".join(lines)[:249]

    db.save_shop_session(user.username, shop_type, page, session_items)
    await bot.highrise.send_whisper(user.id, msg)


async def handle_shop(bot: BaseBot, user: User, args: list[str]):
    """
    /shop              — top-level help
    /shop badges [n]   — page n of badges  (5 per page, default 1)
    /shop titles [n]   — page n of titles  (5 per page, default 1)
    """
    sub = args[1].lower() if len(args) > 1 else ""

    if sub == "badges":
        try:
            await _send_catalog_page(bot, user, BADGES, "badge", args)
        except Exception as exc:
            print(f"[SHOP] /shop badges error for {user.username}: {exc}")
            try:
                await bot.highrise.send_whisper(
                    user.id, "Shop badges had an error. Please tell the owner."
                )
            except Exception:
                pass

    elif sub == "titles":
        try:
            await _send_catalog_page(bot, user, TITLES, "title", args)
        except Exception as exc:
            print(f"[SHOP] /shop titles error for {user.username}: {exc}")
            try:
                await bot.highrise.send_whisper(
                    user.id, "Shop titles had an error. Please tell the owner."
                )
            except Exception:
                pass

    else:
        try:
            await bot.highrise.send_whisper(user.id,
                "🛍️ ChillTopia Shop\n"
                "🏷️ Titles/Flex: !titleshop\n"
                "🎖️ Badges: !badgeshop\n"
                "💎 VIP perks: !vip\n"
                "🎟️ Luxe: !luxe\n"
                "🎒 Owned: !myitems"
            )
        except Exception as exc:
            print(f"[SHOP] /shop help error for {user.username}: {exc}")


async def handle_buy(bot: BaseBot, user: User, args: list[str]):
    """
    /buy badge <id>
    /buy title <id>
    """
    if len(args) < 3:
        await bot.highrise.send_whisper(
            user.id,
            "Usage: !buy badge <id>  or  !buy title <id>\n"
            "See: !shop badges  or  !shop titles"
        )
        return

    item_type = args[1].lower()
    item_id   = args[2].lower()

    if item_type not in ("badge", "title"):
        await bot.highrise.send_whisper(user.id, "Type must be badge or title.")
        return

    item = _get_item(item_type, item_id)
    if item is None:
        await bot.highrise.send_whisper(
            user.id,
            f"Unknown {item_type}: '{item_id}'.  Use !shop {item_type}s to see options."
        )
        return

    if item.get("event_cost") is not None:
        await bot.highrise.send_whisper(
            user.id, f"Use !buyevent {item_id} to get this event item."
        )
        return

    db.ensure_user(user.id, user.username)

    if db.owns_item(user.id, item_id):
        await bot.highrise.send_whisper(
            user.id,
            f"You already own {item['display']} {item_id}!  "
            f"Use !equip {item_type} {item_id} to equip it."
        )
        return

    # Apply shop_sale event discount if active
    from modules.events import get_event_effect
    _ev       = get_event_effect()
    raw_price = item["price"]
    if _ev["shop_discount"] > 0:
        price = max(1, int(raw_price * (1.0 - _ev["shop_discount"])))
    else:
        price = raw_price

    balance = db.get_balance(user.id)
    if balance < price:
        discount_note = f" (sale: {int(_ev['shop_discount']*100)}% off)" if _ev["shop_discount"] > 0 else ""
        await bot.highrise.send_whisper(
            user.id,
            f"Not enough coins!  {item['display']} costs {price:,} coins{discount_note} "
            f"but you only have {balance:,}."
        )
        return

    success = db.buy_item(user.id, user.username, item_id, item_type, price)
    if success:
        new_balance   = db.get_balance(user.id)
        discount_note = f" 🏷️ {int(_ev['shop_discount']*100)}% sale!" if _ev["shop_discount"] > 0 else ""
        print(
            "[ECON_SINK] "
            f"source=shop_purchase user_id={user.id} username={user.username} "
            f"item_type={item_type} item_id={item_id} amount={price} "
            f"raw_price={raw_price} discount_pct={_ev['shop_discount'] * 100:.0f} "
            f"balance_after={new_balance}"
        )
        await bot.highrise.send_whisper(
            user.id,
            f"✅ Purchased {item['display']}  {item_id}!{discount_note}\n"
            f"Paid: {price:,} 🪙 | Balance: {new_balance:,} 🪙\n"
            f"Equip: !equip {item_type} {item_id}"
        )
        await check_achievements(bot, user, "purchase")
        from modules.quests import track_quest
        track_quest(user.id, "shop_buy")
    else:
        await bot.highrise.send_whisper(user.id, "Purchase failed. Try again!")


async def handle_equip(bot: BaseBot, user: User, args: list[str]):
    """
    /equip badge <id>
    /equip title <id>
    """
    if len(args) < 3:
        await bot.highrise.send_whisper(
            user.id, "Usage: !equip badge <id>  or  !equip title <id>"
        )
        return

    item_type = args[1].lower()
    item_id   = args[2].lower()

    if item_type not in ("badge", "title"):
        await bot.highrise.send_whisper(user.id, "Type must be badge or title.")
        return

    item = _get_item(item_type, item_id)
    if item is None:
        await bot.highrise.send_whisper(
            user.id,
            f"Unknown {item_type}: '{item_id}'.  Use !shop {item_type}s to see options."
        )
        return

    db.ensure_user(user.id, user.username)

    if not db.owns_item(user.id, item_id):
        if item.get("event_cost") is not None:
            hint = f"Get it from the event shop: !buyevent {item_id}"
        elif item.get("rep_threshold") is not None:
            hint = f"Earn {item['rep_threshold']} reputation to unlock it."
        else:
            hint = f"Buy it first: !buy {item_type} {item_id}  ({item['price']:,} coins)"
        await bot.highrise.send_whisper(
            user.id, f"You don't own {item['display']} {item_id}!  {hint}"
        )
        return

    db.equip_item(user.id, item_id, item_type, item["display"])
    display_name = db.get_display_name(user.id, user.username)

    benefit_text = item.get("description", "")
    msg = f"✅ Equipped!  You now appear as:  {display_name}"
    if benefit_text and benefit_text != "Cosmetic only":
        msg += f"\nBonus active: {benefit_text}"
    await bot.highrise.send_whisper(user.id, msg)


async def handle_myitems(bot: BaseBot, user: User):
    """Show the player's owned badges and titles, and which are equipped."""
    try:
        db.ensure_user(user.id, user.username)
        owned    = db.get_owned_items(user.id)
        equipped = db.get_equipped_ids(user.id)

        badge_id = equipped.get("badge_id") or ""
        title_id = equipped.get("title_id") or ""

        def _disp(itype: str, iid: str) -> str:
            it = _get_item(itype, iid) if iid else None
            return f"{it['display']} {iid}" if it else "none"

        badge_disp = _disp("badge", badge_id)
        title_disp = _disp("title", title_id)

        my_badges = [o["item_id"] for o in owned if o["item_type"] == "badge" and _get_item("badge", o["item_id"])]
        my_titles = [o["item_id"] for o in owned if o["item_type"] == "title" and _get_item("title", o["item_id"])]

        def _compact(items: list[str], limit: int = 3) -> str:
            shown = items[:limit]
            rest  = len(items) - limit
            text  = ", ".join(shown)
            return text + (f" +{rest}" if rest > 0 else "")

        b_list = _compact(my_badges) if my_badges else "none — !shop badges"
        t_list = _compact(my_titles) if my_titles else "none — !shop titles"

        await bot.highrise.send_whisper(user.id, "\n".join([
            f"-- {user.username}'s Items --",
            f"Badge: {badge_disp}  Title: {title_disp}",
            f"Badges({len(my_badges)}): {b_list}",
            f"Titles({len(my_titles)}): {t_list}",
        ]))
    except Exception as exc:
        print(f"[SHOP] myitems error for {user.username}: {exc}")
        try:
            await bot.highrise.send_whisper(user.id, "Could not load your items. Try again!")
        except Exception:
            pass


async def handle_badgeinfo(bot: BaseBot, user: User, args: list[str]) -> None:
    """/badgeinfo <badge_id> — full details for a single badge."""
    try:
        badge_id = args[1].lower() if len(args) > 1 else ""
        item = BADGES.get(badge_id)
        if item is None:
            await bot.highrise.send_whisper(
                user.id, "Item not found. Check !shop badges or !shop titles."
            )
            return

        db.ensure_user(user.id, user.username)
        owned    = db.owns_item(user.id, badge_id)
        equipped = db.get_equipped_ids(user.id)["badge_id"] == badge_id

        await bot.highrise.send_whisper(user.id,
            f"-- {item['display']} {badge_id} --\n"
            f"Price: {item['price']:,} 🪙\n"
            f"Benefit: {item.get('description', 'Cosmetic only')}\n"
            f"Owned: {'Yes' if owned else 'No'}  "
            f"Equipped: {'Yes' if equipped else 'No'}"
        )
    except Exception as exc:
        print(f"[SHOP] badgeinfo error for {user.username}: {exc}")
        try:
            await bot.highrise.send_whisper(
                user.id, "Item not found. Check !shop badges or !shop titles."
            )
        except Exception:
            pass


async def handle_titleinfo(bot: BaseBot, user: User, args: list[str]) -> None:
    """/titleinfo <title_id> — full details for a single title."""
    try:
        title_id = args[1].lower() if len(args) > 1 else ""
        item = TITLES.get(title_id)
        if item is None:
            await bot.highrise.send_whisper(
                user.id, "Item not found. Check !shop badges or !shop titles."
            )
            return

        db.ensure_user(user.id, user.username)
        owned    = db.owns_item(user.id, title_id)
        equipped = db.get_equipped_ids(user.id)["title_id"] == title_id

        await bot.highrise.send_whisper(user.id,
            f"-- {item['display']} {title_id} --\n"
            f"Price: {item['price']:,} 🪙\n"
            f"Benefit: {item.get('description', 'Cosmetic only')}\n"
            f"Owned: {'Yes' if owned else 'No'}  "
            f"Equipped: {'Yes' if equipped else 'No'}"
        )
    except Exception as exc:
        print(f"[SHOP] titleinfo error for {user.username}: {exc}")
        try:
            await bot.highrise.send_whisper(
                user.id, "Item not found. Check !shop badges or !shop titles."
            )
        except Exception:
            pass
