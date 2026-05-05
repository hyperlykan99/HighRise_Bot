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

from highrise import BaseBot, User
import database as db


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
    "rookie":      {"display": "[Rookie]",       "price": 1_000,   "description": "+5 daily coins",               "benefits": {"daily_coins_bonus": 5}},
    "lucky":       {"display": "[Lucky]",         "price": 2_500,   "description": "+2% coinflip payout",         "benefits": {"coinflip_payout_pct": 2.0}},
    "grinder":     {"display": "[Grinder]",       "price": 5_000,   "description": "+10 XP/win",                  "benefits": {"xp_bonus": 10}},
    "trivia_king": {"display": "[Trivia King]",   "price": 15_000,  "description": "+10 coins per trivia win",    "benefits": {"trivia_bonus": 10}},
    "word_master": {"display": "[Word Master]",   "price": 15_000,  "description": "+10 coins per scramble win",  "benefits": {"scramble_bonus": 10}},
    "riddle_lord": {"display": "[Riddle Lord]",   "price": 15_000,  "description": "+10 coins per riddle win",    "benefits": {"riddle_bonus": 10}},
    "casino_rat":  {"display": "[Casino Rat]",    "price": 20_000,  "description": "+5% coinflip payout",         "benefits": {"coinflip_payout_pct": 5.0}},
    "high_roller": {"display": "[High Roller]",   "price": 50_000,  "description": "+10% coinflip payout",        "benefits": {"coinflip_payout_pct": 10.0}},
    "millionaire": {"display": "[Millionaire]",   "price": 100_000, "description": "+25 daily coins +25 XP/daily","benefits": {"daily_coins_bonus": 25, "daily_xp_bonus": 25}},
    "elite":       {"display": "[Elite]",           "price": 250_000, "description": "+15% all game rewards",       "benefits": {"game_reward_pct": 15.0}},
    "immortal":    {"display": "[Immortal]",      "price": 500_000, "description": "+20% game rewards +50 daily", "benefits": {"game_reward_pct": 20.0, "daily_coins_bonus": 50}},
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

async def handle_shop(bot: BaseBot, user: User, args: list[str]):
    """
    /shop              — top-level help
    /shop badges       — list all badges (2 messages)
    /shop titles       — list all titles (2 messages)
    """
    sub = args[1].lower() if len(args) > 1 else ""

    if sub == "badges":
        badge_items = list(BADGES.items())
        mid = len(badge_items) // 2 + 1

        def badge_line(bid: str, data: dict) -> str:
            return f"  {data['display']} {bid:<16} {data['price']:>6}c  {data['description']}"

        part1 = ["-- Badges 1/2 (before @name) --"]
        for bid, data in badge_items[:mid]:
            part1.append(badge_line(bid, data))
        await bot.highrise.send_whisper(user.id, "\n".join(part1))

        part2 = ["-- Badges 2/2 --"]
        for bid, data in badge_items[mid:]:
            part2.append(badge_line(bid, data))
        part2.append("Buy: /buy badge <id>   Equip: /equip badge <id>")
        await bot.highrise.send_whisper(user.id, "\n".join(part2))

    elif sub == "titles":
        title_items = list(TITLES.items())
        mid = len(title_items) // 2 + 1

        def title_line(tid: str, data: dict) -> str:
            return f"  {data['display']:<20} {tid:<14} {data['price']:>7}c  {data['description']}"

        part1 = ["-- Titles 1/2 (after @name) --"]
        for tid, data in title_items[:mid]:
            part1.append(title_line(tid, data))
        await bot.highrise.send_whisper(user.id, "\n".join(part1))

        part2 = ["-- Titles 2/2 --"]
        for tid, data in title_items[mid:]:
            part2.append(title_line(tid, data))
        part2.append("Buy: /buy title <id>   Equip: /equip title <id>")
        await bot.highrise.send_whisper(user.id, "\n".join(part2))

    else:
        await bot.highrise.send_whisper(user.id,
            "-- Shop --\n"
            "/shop badges    browse badges  (emojis before your name)\n"
            "/shop titles    browse titles  (text after your name)\n"
            "/myitems        see what you own & have equipped\n"
            "Buy:   /buy badge <id>    /buy title <id>\n"
            "Equip: /equip badge <id>  /equip title <id>"
        )


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
    db.ensure_user(user.id, user.username)
    owned   = db.get_owned_items(user.id)
    profile = db.get_profile(user.id)

    badge_eq = profile.get("equipped_badge") or "none"
    title_eq = profile.get("equipped_title") or "none"

    my_badges = [o for o in owned if o["item_type"] == "badge"]
    my_titles = [o for o in owned if o["item_type"] == "title"]

    lines = [f"-- {user.username}'s Items --"]
    lines.append(f"Equipped badge: {badge_eq}")
    lines.append(f"Equipped title: {title_eq}")
    lines.append("")

    if my_badges:
        badge_str = "  ".join(
            f"{BADGES[o['item_id']]['display']}({o['item_id']})"
            for o in my_badges if o["item_id"] in BADGES
        )
        lines.append(f"Badges: {badge_str}")
    else:
        lines.append("Badges: none  — /shop badges")

    if my_titles:
        title_str = "  |  ".join(
            TITLES[o["item_id"]]["display"]
            for o in my_titles if o["item_id"] in TITLES
        )
        lines.append(f"Titles: {title_str}")
    else:
        lines.append("Titles: none  — /shop titles")

    await bot.highrise.send_whisper(user.id, "\n".join(lines))
