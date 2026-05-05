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
# ---------------------------------------------------------------------------

BADGES: dict[str, dict] = {
    "star_badge":      {"display": "⭐", "price": 250,    "description": "+1 XP/win",              "benefits": {"xp_bonus": 1}},
    "heart_badge":     {"display": "💖", "price": 250,    "description": "Cosmetic only"},
    "music_badge":     {"display": "🎵", "price": 500,    "description": "Cosmetic only"},
    "fire_badge":      {"display": "🔥", "price": 500,    "description": "+2 XP/win",              "benefits": {"xp_bonus": 2}},
    "dice_badge":      {"display": "🎲", "price": 750,    "description": "+2% coinflip payout",    "benefits": {"coinflip_payout_pct": 2.0}},
    "skull_badge":     {"display": "💀", "price": 1_500,  "description": "+3 XP/win",              "benefits": {"xp_bonus": 3}},
    "lightning_badge": {"display": "⚡", "price": 2_000,  "description": "-5s coinflip cooldown",  "benefits": {"cooldown_reduction": 5}},
    "gem_badge":       {"display": "💎", "price": 3_000,  "description": "+5 daily coins",         "benefits": {"daily_coins_bonus": 5}},
    "crown_badge":     {"display": "👑", "price": 5_000,  "description": "+5 XP/win",              "benefits": {"xp_bonus": 5}},
    "angel_badge":     {"display": "😇", "price": 8_000,  "description": "+10 daily coins",        "benefits": {"daily_coins_bonus": 10}},
    "dragon_badge":    {"display": "🐉", "price": 10_000, "description": "+10 XP/win",             "benefits": {"xp_bonus": 10}},
    "demon_badge":     {"display": "😈", "price": 15_000, "description": "+5% coinflip payout",    "benefits": {"coinflip_payout_pct": 5.0}},
    "trophy_badge":    {"display": "🏆", "price": 25_000, "description": "+15 XP/win",             "benefits": {"xp_bonus": 15}},
}

TITLES: dict[str, dict] = {
    "rookie":      {"display": "[Rookie]",       "price": 1_000,   "description": "+5 daily coins",                          "benefits": {"daily_coins_bonus": 5}},
    "lucky":       {"display": "[Lucky]",         "price": 2_500,   "description": "+2% coinflip win bonus payout",           "benefits": {"coinflip_payout_pct": 2.0}},
    "grinder":     {"display": "[Grinder]",       "price": 5_000,   "description": "+10 XP bonus from game wins",             "benefits": {"xp_bonus": 10}},
    "trivia_king": {"display": "[Trivia King]",   "price": 15_000,  "description": "+10 coins per trivia win",                "benefits": {"trivia_bonus": 10}},
    "word_master": {"display": "[Word Master]",   "price": 15_000,  "description": "+10 coins per scramble win",              "benefits": {"scramble_bonus": 10}},
    "riddle_lord": {"display": "[Riddle Lord]",   "price": 15_000,  "description": "+10 coins per riddle win",                "benefits": {"riddle_bonus": 10}},
    "casino_rat":  {"display": "[Casino Rat]",    "price": 20_000,  "description": "+5% casino payout bonus",                 "benefits": {"coinflip_payout_pct": 5.0}},
    "high_roller": {"display": "[High Roller]",   "price": 50_000,  "description": "+10% casino payout bonus",                "benefits": {"coinflip_payout_pct": 10.0}},
    "millionaire": {"display": "[Millionaire]",   "price": 100_000, "description": "+25 daily coins and +25 XP from daily",   "benefits": {"daily_coins_bonus": 25, "daily_xp_bonus": 25}},
    "elite":       {"display": "[Elite]",         "price": 250_000, "description": "+15% all game coin rewards",              "benefits": {"game_reward_pct": 15.0}},
    "immortal":    {"display": "[Immortal]",      "price": 500_000, "description": "+20% all game coin rewards +50 daily",    "benefits": {"game_reward_pct": 20.0, "daily_coins_bonus": 50}},
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
    return (BADGES if item_type == "badge" else TITLES).get(item_id)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

_PAGE_SIZE = 5


async def _send_catalog_page(
    bot, user, catalog: dict, item_type: str, args: list[str]
) -> None:
    """Send one page of 5 items from a badge or title catalog."""
    items       = list(catalog.items())
    total_pages = max(1, math.ceil(len(items) / _PAGE_SIZE))

    # Parse the optional page number
    raw_page = args[2] if len(args) > 2 else "1"
    if not raw_page.isdigit():
        await bot.highrise.send_whisper(
            user.id, f"Invalid page. Use /shop {item_type}s 1 to {total_pages}."
        )
        return
    page = int(raw_page)
    if page < 1 or page > total_pages:
        await bot.highrise.send_whisper(
            user.id, f"Pages 1-{total_pages}. Use /shop {item_type}s <page>."
        )
        return

    start = (page - 1) * _PAGE_SIZE
    chunk = items[start : start + _PAGE_SIZE]

    label = "Badges (before @name)" if item_type == "badge" else "Titles (after @name)"
    lines = [f"-- {label}  {page}/{total_pages} --"]

    for item_id, data in chunk:
        display = data.get("display", "?")
        price   = data.get("price", 0)
        lines.append(f"  {display} {item_id}  {price:,}c")

    if page < total_pages:
        lines.append(f"More: /shop {item_type}s {page + 1}")
    else:
        lines.append(f"Buy: /buy {item_type} <id>   Equip: /equip {item_type} <id>")

    await bot.highrise.send_whisper(user.id, "\n".join(lines))


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
                "-- Shop --\n"
                "/shop badges    browse badges  (before your name)\n"
                "/shop titles    browse titles  (after your name)\n"
                "/myitems        see what you own & have equipped\n"
                "Buy:   /buy badge <id>    /buy title <id>\n"
                "Equip: /equip badge <id>  /equip title <id>"
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
            "Usage: /buy badge <id>  or  /buy title <id>\n"
            "See: /shop badges  or  /shop titles"
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
            f"Unknown {item_type}: '{item_id}'.  Use /shop {item_type}s to see options."
        )
        return

    db.ensure_user(user.id, user.username)

    if db.owns_item(user.id, item_id):
        await bot.highrise.send_whisper(
            user.id,
            f"You already own {item['display']} {item_id}!  "
            f"Use /equip {item_type} {item_id} to equip it."
        )
        return

    balance = db.get_balance(user.id)
    if balance < item["price"]:
        await bot.highrise.send_whisper(
            user.id,
            f"Not enough coins!  {item['display']} costs {item['price']:,} coins "
            f"but you only have {balance:,}."
        )
        return

    success = db.buy_item(user.id, user.username, item_id, item_type, item["price"])
    if success:
        new_balance = db.get_balance(user.id)
        await bot.highrise.send_whisper(
            user.id,
            f"✅ Purchased {item['display']}  {item_id}!\n"
            f"Balance: {new_balance:,} coins\n"
            f"Equip with: /equip {item_type} {item_id}"
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
            user.id, "Usage: /equip badge <id>  or  /equip title <id>"
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
            f"Unknown {item_type}: '{item_id}'.  Use /shop {item_type}s to see options."
        )
        return

    db.ensure_user(user.id, user.username)

    if not db.owns_item(user.id, item_id):
        await bot.highrise.send_whisper(
            user.id,
            f"You don't own {item['display']} {item_id}!  "
            f"Buy it first: /buy {item_type} {item_id}  ({item['price']:,} coins)"
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

        badge_disp = f"{BADGES[badge_id]['display']} {badge_id}" if badge_id in BADGES else "none"
        title_disp = f"{TITLES[title_id]['display']} {title_id}" if title_id in TITLES else "none"

        my_badges = [o["item_id"] for o in owned if o["item_type"] == "badge" and o["item_id"] in BADGES]
        my_titles = [o["item_id"] for o in owned if o["item_type"] == "title" and o["item_id"] in TITLES]

        def _compact(items: list[str], limit: int = 3) -> str:
            shown = items[:limit]
            rest  = len(items) - limit
            text  = ", ".join(shown)
            return text + (f" +{rest}" if rest > 0 else "")

        b_list = _compact(my_badges) if my_badges else "none — /shop badges"
        t_list = _compact(my_titles) if my_titles else "none — /shop titles"

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
                user.id, "Item not found. Check /shop badges or /shop titles."
            )
            return

        db.ensure_user(user.id, user.username)
        owned    = db.owns_item(user.id, badge_id)
        equipped = db.get_equipped_ids(user.id)["badge_id"] == badge_id

        await bot.highrise.send_whisper(user.id,
            f"-- {item['display']} {badge_id} --\n"
            f"Price: {item['price']:,}c\n"
            f"Benefit: {item.get('description', 'Cosmetic only')}\n"
            f"Owned: {'Yes' if owned else 'No'}  "
            f"Equipped: {'Yes' if equipped else 'No'}"
        )
    except Exception as exc:
        print(f"[SHOP] badgeinfo error for {user.username}: {exc}")
        try:
            await bot.highrise.send_whisper(
                user.id, "Item not found. Check /shop badges or /shop titles."
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
                user.id, "Item not found. Check /shop badges or /shop titles."
            )
            return

        db.ensure_user(user.id, user.username)
        owned    = db.owns_item(user.id, title_id)
        equipped = db.get_equipped_ids(user.id)["title_id"] == title_id

        await bot.highrise.send_whisper(user.id,
            f"-- {item['display']} {title_id} --\n"
            f"Price: {item['price']:,}c\n"
            f"Benefit: {item.get('description', 'Cosmetic only')}\n"
            f"Owned: {'Yes' if owned else 'No'}  "
            f"Equipped: {'Yes' if equipped else 'No'}"
        )
    except Exception as exc:
        print(f"[SHOP] titleinfo error for {user.username}: {exc}")
        try:
            await bot.highrise.send_whisper(
                user.id, "Item not found. Check /shop badges or /shop titles."
            )
        except Exception:
            pass
