"""
modules/shop.py
---------------
Badge and title shop for the Mini Game Bot.

Players spend coins on badges (emojis) and titles, then equip them to
personalise how they appear in every public room announcement:

    🔥 @PlayerName [High Roller]

The catalog is defined here in Python — no DB table needed since items don't
change at runtime. The database only tracks what each player owns and has
equipped.

Commands handled here:
    /shop              — top-level help
    /shop badges       — list all badges with IDs and prices
    /shop titles       — list all titles with IDs and prices
    /buy badge <id>    — purchase a badge
    /buy title <id>    — purchase a title
    /equip badge <id>  — equip an owned badge
    /equip title <id>  — equip an owned title
    /myitems           — show owned badges and titles
"""

from highrise import BaseBot, User
import database as db


# ---------------------------------------------------------------------------
# Shop catalog — edit prices/descriptions here, add rows to expand the shop
# ---------------------------------------------------------------------------

BADGES: dict[str, dict] = {
    "star":      {"display": "⭐", "price": 75,   "description": "A rising star!"},
    "moon":      {"display": "🌙", "price": 75,   "description": "Night owl."},
    "butterfly": {"display": "🦋", "price": 75,   "description": "Flutter on!"},
    "wave":      {"display": "🌊", "price": 75,   "description": "Go with the flow."},
    "fire":      {"display": "🔥", "price": 100,  "description": "Stay hot!"},
    "target":    {"display": "🎯", "price": 125,  "description": "Always on point."},
    "gamer":     {"display": "🎮", "price": 150,  "description": "True gamer."},
    "crown":     {"display": "👑", "price": 250,  "description": "Royalty vibes."},
    "diamond":   {"display": "💎", "price": 300,  "description": "Shine bright!"},
    "dragon":    {"display": "🐉", "price": 400,  "description": "Legendary energy."},
}

TITLES: dict[str, dict] = {
    "newcomer":       {"display": "[Newcomer]",       "price": 50,   "description": "Just joined the fun!"},
    "rookie_gambler": {"display": "[Rookie Gambler]", "price": 150,  "description": "Getting started."},
    "trivia_master":  {"display": "[Trivia Master]",  "price": 350,  "description": "Full of knowledge."},
    "word_wizard":    {"display": "[Word Wizard]",    "price": 350,  "description": "Unscramble anything."},
    "riddle_solver":  {"display": "[Riddle Solver]",  "price": 350,  "description": "Loves a mystery."},
    "coin_collector": {"display": "[Coin Collector]", "price": 450,  "description": "All about the coins."},
    "high_roller":    {"display": "[High Roller]",    "price": 600,  "description": "Big bets, big wins."},
    "veteran":        {"display": "[Veteran]",        "price": 700,  "description": "Seen it all."},
    "champion":       {"display": "[Champion]",       "price": 900,  "description": "Top of the game."},
    "legend":         {"display": "[Legend]",         "price": 1500, "description": "Need I say more?"},
}


def _get_item(item_type: str, item_id: str) -> dict | None:
    """Return the catalog entry for an item, or None if not found."""
    return (BADGES if item_type == "badge" else TITLES).get(item_id)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_shop(bot: BaseBot, user: User, args: list[str]):
    """
    /shop              — top-level categories
    /shop badges       — list all badges
    /shop titles       — list all titles
    """
    sub = args[1].lower() if len(args) > 1 else ""

    if sub == "badges":
        lines = ["-- Badges (appear before @name) --"]
        for bid, data in BADGES.items():
            lines.append(
                f"  {data['display']}  {bid:<14}  {data['price']:>4} coins  — {data['description']}"
            )
        lines.append("Buy: /buy badge <id>     Equip: /equip badge <id>")
        await bot.highrise.send_whisper(user.id, "\n".join(lines))

    elif sub == "titles":
        lines = ["-- Titles (appear after @name) --"]
        for tid, data in TITLES.items():
            lines.append(
                f"  {data['display']:<20}  {tid:<16}  {data['price']:>5} coins  — {data['description']}"
            )
        lines.append("Buy: /buy title <id>     Equip: /equip title <id>")
        await bot.highrise.send_whisper(user.id, "\n".join(lines))

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
            f"Unknown {item_type}: '{item_id}'.  Use /shop {item_type}s to see what's available."
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
            f"Not enough coins!  {item['display']} costs {item['price']} coins "
            f"but you only have {balance}."
        )
        return

    success = db.buy_item(user.id, user.username, item_id, item_type, item["price"])
    if success:
        new_balance = db.get_balance(user.id)
        await bot.highrise.send_whisper(
            user.id,
            f"✅ Purchased {item['display']}  {item_id}!\n"
            f"Balance: {new_balance} coins\n"
            f"Equip it with: /equip {item_type} {item_id}"
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
            f"Buy it first: /buy {item_type} {item_id}  ({item['price']} coins)"
        )
        return

    db.equip_item(user.id, item_id, item_type, item["display"])
    display_name = db.get_display_name(user.id, user.username)
    await bot.highrise.send_whisper(
        user.id,
        f"✅ Equipped!  You now appear as:  {display_name}"
    )


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
        lines.append(f"Badges owned: {badge_str}")
    else:
        lines.append("Badges: none yet — browse with /shop badges")

    if my_titles:
        title_str = "  |  ".join(
            TITLES[o["item_id"]]["display"]
            for o in my_titles if o["item_id"] in TITLES
        )
        lines.append(f"Titles owned: {title_str}")
    else:
        lines.append("Titles: none yet — browse with /shop titles")

    await bot.highrise.send_whisper(user.id, "\n".join(lines))
