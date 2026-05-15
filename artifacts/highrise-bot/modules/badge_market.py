"""
modules/badge_market.py
-----------------------
Emoji Badge Market System for Highrise Hangout Bot.

Player commands:
  /shop badges [page]          - browse purchasable badges
  /badgeinfo <id>              - badge details
  /buy badge <id>              - buy from bot shop
  /equip badge <id>            - equip owned badge
  /unequip badge               - remove equipped badge
  /mybadges                    - list your badges
  /badges [user]               - view user's badges (profile integration)
  /badgemarket [page]          - player market listings
  /badgelist <id> <price>      - list badge for sale
  /badgebuy <listing_id>       - buy from player market
  /badgecancel <listing_id>    - cancel your listing
  /mybadgelistings             - your active listings
  /badgeprices <id>            - recent sale prices

Admin/Owner commands:
  /addbadge <id> <emoji> <name> <rarity> <price>
  /editbadgeprice <id> <price>
  /setbadgepurchasable <id> on/off
  /setbadgetradeable <id> on/off
  /setbadgesellable <id> on/off
  /giveemojibadge <user> <emoji> <name>
  /badgecatalog [page]
  /badgeadmin <id>
  /setbadgemarketfee <percent>
  /badgemarketlogs [user]
"""

import database as db
from highrise import BaseBot, User
from modules.permissions import is_admin, is_owner

_MIN_PRICE    = 100
_MAX_PRICE    = 1_000_000_000
_PAGE_SIZE    = 8
_MARKET_PAGE  = 6
_BROWSE_SIZE  = 5   # badges per category page

# ── Category / filter metadata ──────────────────────────────────────────────

_CATEGORY_BADGE_IDS: dict[str, list[str]] = {
    "stars":      ["star", "glow", "sparkle", "stardust"],
    "hearts":     ["redheart","blueheart","greenheart","yellowheart","orangeheart",
                   "purpleheart","blackheart","whiteheart","brownheart","sparkleheart",
                   "growingheart","beatingheart","revolvingheart","twohearts",
                   "arrowheart","ribbonheart"],
    "faces":      ["smile","grin","laugh","beaming","squintlaugh","sweat","rofl",
                   "slightsmile","upsidedown","wink","blush","shades","starstruck",
                   "partying","halo","devil","ghost","skull","robot"],
    "sky":        ["fire","ice","lightning","moon","sun","rainbow","clover",
                   "cloud","wave","earth","earth2","earth3"],
    "animals":    ["dog","cat","mouse","hamster","rabbit","bear","koala","cow",
                   "pig","frog","monkey","bee","turtle","shark","dolphin","whale",
                   "wyrm","eagle","wolf","fox","panda","lion","tiger","unicorn",
                   "butterfly"],
    "food":       ["apple","orange","lemon","banana","watermelon","grapes",
                   "strawberry","cherry","peach","pineapple","coconut","avocado",
                   "burger","pizza","taco","sushi","donut","cookie","cake","lollipop"],
    "objects":    ["phone","gift","key","tophat","headphones","joystick","laptop",
                   "goldmedal","moneywings","wand","mask","phantom","lance"],
    "activities": ["soccer","basketball","football","baseball","tennis","volleyball",
                   "pingpong","boxing","fishing","pickaxe","microphone","musicnotes",
                   "palette","car","airplane","music","gamepad","diceroll","target"],
    "zodiac":     ["aries","taurus","gemini","cancer","leo","virgo","libra",
                   "scorpio","sagittarius","capricorn","aquarius","pisces"],
    "symbols":    ["check","cross","exclaim","question","hundred","bell",
                   "locked","unlocked","shield","sword","amulet","dna"],
    "room":       ["house","party","confetti","couch","bed","island","night",
                   "discoball","maledancer","femaledancer","goldcoin","moneybag",
                   "trophy","medal","goldbadge","mask"],
}

_CATEGORY_ALIASES: dict[str, str] = {
    "faces":"faces","face":"faces",
    "hearts":"hearts","heart":"hearts",
    "stars":"stars","star":"stars",
    "sky":"sky","nature":"sky","weather":"sky",
    "animals":"animals","animal":"animals","pets":"animals","pet":"animals",
    "food":"food","foods":"food","fruit":"food","fruits":"food",
    "objects":"objects","object":"objects","items":"objects",
    "activities":"activities","activity":"activities",
    "sports":"activities","sport":"activities",
    "zodiac":"zodiac","horoscope":"zodiac",
    "symbols":"symbols","symbol":"symbols",
    "room":"room","rooms":"room","highrise":"room",
}

_RARITY_TIERS: frozenset[str] = frozenset(
    {"common","uncommon","rare","epic","legendary","mythic","exclusive"}
)

_CATEGORY_LABELS: dict[str, str] = {
    "faces":"😀 Faces","hearts":"❤️ Hearts","stars":"⭐ Stars",
    "sky":"🌈 Sky & Fire","animals":"🐾 Animals","food":"🍎 Food",
    "objects":"🎁 Objects","activities":"⚽ Activities",
    "zodiac":"♈ Zodiac","symbols":"✅ Symbols","room":"🏠 Room",
    "common":"⚪ Common","uncommon":"🟢 Uncommon","rare":"💎 Rare",
    "epic":"🌌 Epic","legendary":"👑 Legendary","mythic":"🪽 Mythic",
}


def _fee_pct() -> float:
    raw = db.get_bot_setting("badge_market_fee_percent", "5")
    try:
        return float(raw)
    except (ValueError, TypeError):
        return 5.0


def _can_manage(username: str) -> bool:
    return is_admin(username) or is_owner(username)


def _short(val: int) -> str:
    """Format large numbers compactly."""
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val // 1_000}K"
    return str(val)


# ---------------------------------------------------------------------------
# /shop badges [page]
# ---------------------------------------------------------------------------

async def handle_shop_badges(bot: BaseBot, user: User, args: list[str] | None = None) -> None:
    """!badges / !badge — main badge menu (Part 2)."""
    await bot.highrise.send_whisper(user.id, (
        "🏷️ Badge Menu\n"
        "Browse: !badgeshop\n"
        "Owned: !mybadges\n"
        "Equip: !equipbadge badge_id\n"
        "Buy: !buybadge badge_id\n"
        "Search: !badgesearch crown\n"
        "Market: !badgemarket"
    )[:249])


async def handle_badgeshop_categories(bot: BaseBot, user: User) -> None:
    """!badgeshop (no args) — show category list (Part 3)."""
    await bot.highrise.send_whisper(user.id, (
        "🏷️ Badge Shop\n"
        "⭐ stars | ❤️ hearts | 😀 faces\n"
        "🐾 animals | 🍎 food | ⚽ activities\n"
        "🏠 room | ♈ zodiac | ✅ symbols\n"
        "💎 rare | 🌌 epic | 👑 legendary"
    )[:249])
    await bot.highrise.send_whisper(user.id, (
        "Browse: !badgeshop hearts\n"
        "Search: !badgesearch crown\n"
        "Buy: !buybadge badge_id"
    )[:249])


# ---------------------------------------------------------------------------
# Master router for !badges / !badgeshop
# ---------------------------------------------------------------------------

async def handle_badges_cmd_router(bot: BaseBot, user: User, args: list[str]) -> None:
    """Routes all !badges sub-commands to the correct handler."""
    first  = args[1].lower().strip() if len(args) > 1 else ""
    second = args[2].strip()         if len(args) > 2 else ""

    # No arg or bare digit → category menu
    if not first or first.isdigit():
        await handle_shop_badges(bot, user, args)
        return

    # @user → view their collection
    if first.startswith("@"):
        await handle_badges_view(bot, user, args)
        return

    # !badges search [query]
    if first == "search":
        query = " ".join(args[2:]).strip() if len(args) > 2 else ""
        if not query:
            await bot.highrise.send_whisper(user.id, "Usage: !badges search <name>")
        else:
            await handle_badge_search(bot, user, query)
        return

    # !badges available [page]
    if first in ("available", "unsold"):
        page = int(second) if second.isdigit() else 1
        await handle_badge_available(bot, user, page)
        return

    # !badges affordable [page]
    if first == "affordable":
        page = int(second) if second.isdigit() else 1
        await handle_badge_affordable(bot, user, page)
        return

    # !badges sold [page]
    if first == "sold":
        page = int(second) if second.isdigit() else 1
        await handle_badge_sold(bot, user, page)
        return

    # !badges next / !badges prev → redirect
    if first in ("next", "prev"):
        await bot.highrise.send_whisper(
            user.id,
            "Use direct pages:\n"
            "!badges animals 2\n"
            "!badges rare 1\n"
            "!badges search crown"
        )
        return

    # Rarity tier filter
    if first in _RARITY_TIERS:
        page = int(second) if second.isdigit() else 1
        db.ensure_user(user.id, user.username)
        await handle_badge_category(bot, user, first, page)
        return

    # Semantic category alias or direct name
    cat = _CATEGORY_ALIASES.get(first) or (first if first in _CATEGORY_BADGE_IDS else None)
    if cat:
        page = int(second) if second.isdigit() else 1
        db.ensure_user(user.id, user.username)
        await handle_badge_category(bot, user, cat, page)
        return

    # Fallback: treat as search term
    await handle_badge_search(bot, user, first)


# ---------------------------------------------------------------------------
# /badgeinfo <badge_id>
# ---------------------------------------------------------------------------

async def handle_badgeinfo(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await bot.highrise.send_whisper(
            user.id, "Usage: !badgeinfo badge_id\nTry !badgesearch name first."
        )
        return

    badge_id = args[1].lower().strip()
    row      = db.get_emoji_badge(badge_id)

    if row is None:
        await bot.highrise.send_whisper(
            user.id, f"⚠️ Badge not found. Try !badgesearch {badge_id[:15]}"
        )
        return

    db.ensure_user(user.id, user.username)
    owned       = db.owns_emoji_badge(user.username, badge_id)
    equipped_ids = db.get_equipped_ids(user.id)
    is_equipped = equipped_ids.get("badge_id") == badge_id
    listed      = db.is_badge_listed(user.username, badge_id)

    status_parts = []
    if listed:       status_parts.append("LISTED")
    elif is_equipped: status_parts.append("EQUIPPED")
    elif owned:      status_parts.append("OWNED")
    status_tag = f" [{', '.join(status_parts)}]" if status_parts else ""

    lines = [
        f"{row['emoji']} {row['name']}{status_tag}",
        f"ID: {badge_id}",
        f"Rarity: {row['rarity'].capitalize()}",
        f"Price: {row['price']:,} coins",
        f"Owned: {'Yes' if owned else 'No'}  Equipped: {'Yes' if is_equipped else 'No'}",
    ]
    if not owned and row["purchasable"]:
        lines.append(f"Buy: !buybadge {badge_id}")
    elif not row["purchasable"] and not owned:
        lines.append("Buy: Not available")
    if owned and not is_equipped and not listed:
        lines.append(f"Equip: !equipbadge {badge_id}")
    if row["tradeable"] and row["sellable"] and owned and not listed:
        lines.append(f"Sell: !sellbadge {badge_id} price")

    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /buy badge <badge_id>
# ---------------------------------------------------------------------------

async def handle_buy_badge(bot: BaseBot, user: User, badge_id: str) -> None:
    badge_id = badge_id.lower().strip()
    row      = db.get_emoji_badge(badge_id)

    if row is None:
        await bot.highrise.send_whisper(user.id, f"Badge '{badge_id}' not found. See !shop badges.")
        return

    if not row["purchasable"]:
        await bot.highrise.send_whisper(user.id, f"❌ {row['emoji']} {badge_id} is not for sale.")
        return

    db.ensure_user(user.id, user.username)

    if db.owns_emoji_badge(user.username, badge_id):
        await bot.highrise.send_whisper(
            user.id,
            f"You already own this badge.\n"
            f"Equip it: !equipbadge {badge_id}"
        )
        return

    price   = row["price"]
    balance = db.get_balance(user.id)
    if balance < price:
        await bot.highrise.send_whisper(
            user.id,
            f"⚠️ Not enough coins.\n"
            f"Price: {price:,} coins\n"
            f"Your balance: {balance:,} coins"
        )
        return

    # Deduct coins then grant
    success = db.buy_item(user.id, user.username, badge_id, "badge", price)
    if not success:
        await bot.highrise.send_whisper(user.id, "Purchase failed. Try again!")
        return

    db.grant_emoji_badge(user.username, badge_id, source="shop")
    # Enforce 1/1: mark badge sold — no longer purchasable from shop
    db.update_emoji_badge_field(badge_id, "purchasable", 0)
    new_bal = db.get_balance(user.id)
    await bot.highrise.send_whisper(
        user.id,
        f"✅ Bought badge: {row['emoji']} {row['name']} for {row['price']:,} coins.\n"
        f"Equip it: !equipbadge {badge_id}"
    )
    print(f"[BADGE] badge_buy user=@{user.username} badge={badge_id} price={row['price']}")
    db.log_badge_market_action(
        "purchased", user.username, "", badge_id, row["emoji"], price, 0, "shop"
    )


# ---------------------------------------------------------------------------
# /equip badge <badge_id>
# ---------------------------------------------------------------------------

async def handle_equip_badge(bot: BaseBot, user: User, badge_id: str) -> None:
    badge_id = badge_id.lower().strip()

    if not badge_id:
        await bot.highrise.send_whisper(user.id, "Usage: !equipbadge badge_id\nSee: !mybadges")
        return

    row = db.get_emoji_badge(badge_id)

    if row is None:
        await bot.highrise.send_whisper(
            user.id, f"⚠️ Badge not found. Try !badgesearch {badge_id[:15]}"
        )
        return

    db.ensure_user(user.id, user.username)

    if not db.owns_emoji_badge(user.username, badge_id):
        await bot.highrise.send_whisper(
            user.id,
            f"⚠️ You do not own that badge.\n"
            f"Buy it with: !buybadge {badge_id}"
        )
        return

    # Already equipped check
    equipped_ids = db.get_equipped_ids(user.id)
    if equipped_ids.get("badge_id") == badge_id:
        await bot.highrise.send_whisper(
            user.id, f"✅ That badge is already equipped."
        )
        return

    if db.is_badge_listed(user.username, badge_id):
        await bot.highrise.send_whisper(
            user.id, "⚠️ That badge is listed for sale. Use !cancelbadge to remove the listing first."
        )
        return

    db.equip_item(user.id, badge_id, "badge", row["emoji"])
    await bot.highrise.send_whisper(
        user.id, f"✅ Equipped badge: {row['emoji']} {row['name']}"
    )
    print(f"[BADGE] badge_equip user=@{user.username} badge={badge_id}")


# ---------------------------------------------------------------------------
# /unequip badge
# ---------------------------------------------------------------------------

async def handle_unequip_badge(bot: BaseBot, user: User) -> None:
    db.ensure_user(user.id, user.username)
    equipped_ids = db.get_equipped_ids(user.id)
    if not equipped_ids.get("badge_id"):
        await bot.highrise.send_whisper(user.id, "You do not have a badge equipped.")
        return
    db.clear_equipped_badge(user.id)
    await bot.highrise.send_whisper(user.id, "✅ Badge unequipped.")
    print(f"[BADGE] badge_unequip user=@{user.username}")


# ---------------------------------------------------------------------------
# /mybadges
# ---------------------------------------------------------------------------

async def handle_mybadges(bot: BaseBot, user: User, page: int = 1) -> None:
    """!mybadges [page] — list owned badges, 8 per page, with numbered index (Part 4)."""
    db.ensure_user(user.id, user.username)
    owned = db.get_user_emoji_badges(user.username)

    if not owned:
        await bot.highrise.send_whisper(
            user.id,
            "🏷️ Your Badges\n"
            "You do not own badges yet.\n"
            "Browse: !badgeshop"
        )
        return

    equipped_ids = db.get_equipped_ids(user.id)
    eq_id        = equipped_ids.get("badge_id") or ""
    eq_badge     = next((b for b in owned if b["badge_id"] == eq_id), None)

    page       = max(1, page)
    per_page   = 8
    total      = len(owned)
    total_pages = max(1, -(-total // per_page))
    page       = min(page, total_pages)
    start      = (page - 1) * per_page
    chunk      = owned[start:start + per_page]

    lines = [f"🏷️ Your Badges ({total}) p{page}/{total_pages}"]

    if eq_badge:
        eq_emoji = eq_badge.get("emoji") or ""
        eq_name  = eq_badge.get("name")  or eq_id
        lines.append(f"Equipped: {eq_emoji} {eq_name}")

    for i, b in enumerate(chunk, start=start + 1):
        emoji  = b.get("emoji") or ""
        name   = b.get("name")  or b["badge_id"]
        marker = " ★" if b["badge_id"] == eq_id else ""
        listed = " [M]" if db.is_badge_listed(user.username, b["badge_id"]) else ""
        lines.append(f"{i}) {b['badge_id']} {emoji} {name}{marker}{listed}")

    if total_pages > 1:
        nav = []
        if page > 1:           nav.append(f"!mybadges {page-1}")
        if page < total_pages: nav.append(f"!mybadges {page+1}")
        if nav: lines.append("  ".join(nav))

    lines.append("Equip: !equipbadge badge_id")
    lines.append("Unequip: !unequipbadge")

    msg = "\n".join(lines)
    if len(msg) > 249:
        # Trim to header + equipped + 4 items
        short = lines[:2 + (1 if eq_badge else 0) + 4]
        if total_pages > 1: short.append(f"!mybadges {min(page+1, total_pages)}")
        short.append("Equip: !equipbadge badge_id")
        msg = "\n".join(short)
    await bot.highrise.send_whisper(user.id, msg[:249])


# ---------------------------------------------------------------------------
# /badges [username]  — public badge view (respects inventory privacy)
# ---------------------------------------------------------------------------

async def handle_badge_category(
    bot: BaseBot, user: User, category: str, page: int = 1
) -> None:
    """Browse badges by semantic category or rarity tier — 5 per page, whispered."""
    label   = _CATEGORY_LABELS.get(category, category.capitalize())
    page    = max(1, page)

    if category in _RARITY_TIERS:
        rows, total_pages = db.get_emoji_badges_page(
            page=page, per_page=_BROWSE_SIZE, purchasable_only=True, rarity=category
        )
    elif category in _CATEGORY_BADGE_IDS:
        rows, total_pages = db.get_emoji_badges_by_ids(
            _CATEGORY_BADGE_IDS[category], page=page, per_page=_BROWSE_SIZE
        )
    else:
        await bot.highrise.send_whisper(user.id, f"Category '{category}' not found. See: !badges")
        return

    if not rows:
        await bot.highrise.send_whisper(
            user.id, f"{label} — no available badges.\nBrowse: !badges"
        )
        return

    lines = [f"{label} p{page}/{total_pages}"]
    for i, r in enumerate(rows, start=1):
        owned       = db.owns_emoji_badge(user.username, r["badge_id"])
        eq_ids      = db.get_equipped_ids(user.id)
        is_equipped = eq_ids.get("badge_id") == r["badge_id"]
        listed      = db.is_badge_listed(user.username, r["badge_id"])
        if listed:       tag = " LISTED"
        elif is_equipped: tag = " EQUIPPED"
        elif owned:      tag = " OWNED"
        else:            tag = ""
        lines.append(f"{i}) {r['badge_id']} {r['emoji']} {r['name']} — {_short(r['price'])}c{tag}")

    nav = []
    if page > 1:             nav.append(f"!badgeshop {category} {page-1}")
    if page < total_pages:   nav.append(f"!badgeshop {category} {page+1}")
    if nav: lines.append("  ".join(nav))
    lines.append("Buy: !buybadge badge_id")
    lines.append("Info: !badgeinfo badge_id")

    msg = "\n".join(lines)
    await bot.highrise.send_whisper(user.id, msg[:249])


async def handle_badge_search(bot: BaseBot, user: User, query: str) -> None:
    """!badgesearch <text> — full-text badge search (Part 9)."""
    query = query.strip()
    if not query:
        await bot.highrise.send_whisper(user.id, "Usage: !badgesearch name\nExample: !badgesearch crown")
        return
    rows = db.search_emoji_badges(query, purchasable_only=True, limit=5)
    if not rows:
        await bot.highrise.send_whisper(
            user.id,
            f"No badges found for \"{query[:20]}\".\n"
            f"Try: !badgeshop"
        )
        return

    db.ensure_user(user.id, user.username)
    lines = [f"🔎 Badge Search: {query[:15]}"]
    last_id = None
    for i, r in enumerate(rows, 1):
        owned   = db.owns_emoji_badge(user.username, r["badge_id"])
        tag     = " [OWNED]" if owned else ""
        lines.append(f"{i}) {r['badge_id']} {r['emoji']} {r['name']} — {_short(r['price'])}c{tag}")
        last_id = r["badge_id"]

    if last_id:
        lines.append(f"Buy: !buybadge {last_id}")
        lines.append(f"Info: !badgeinfo {last_id}")
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])
    print(f"[BADGE] badge_search user=@{user.username} query={query!r} results={len(rows)}")


async def handle_badge_available(bot: BaseBot, user: User, page: int = 1) -> None:
    """!badges available [page] — show purchasable badges, 5 per page."""
    page = max(1, page)
    rows, total_pages = db.get_emoji_badges_page(
        page=page, per_page=_BROWSE_SIZE, purchasable_only=True
    )
    if not rows:
        await bot.highrise.send_whisper(user.id, "No available badges right now.")
        return

    db.ensure_user(user.id, user.username)
    lines = [f"✅ Available p{page}/{total_pages}"]
    for r in rows:
        bid   = r.get("rowid") or 0
        owned = db.owns_emoji_badge(user.username, r["badge_id"])
        tick  = "✅" if owned else ""
        lines.append(f"B{bid:03d} {r['emoji']}{tick} {r['name']} — {_short(r['price'])}c")

    nav = []
    if page > 1:           nav.append(f"!badges available {page-1}")
    if page < total_pages: nav.append(f"!badges available {page+1}")
    if nav: lines.append("  ".join(nav))
    lines.append("Buy: !buybadge <id>")
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


async def handle_badge_affordable(bot: BaseBot, user: User, page: int = 1) -> None:
    """!badges affordable [page] — badges the player can afford."""
    page = max(1, page)
    db.ensure_user(user.id, user.username)
    balance = db.get_balance(user.id)
    rows, total_pages = db.get_affordable_badges(balance, page=page, per_page=_BROWSE_SIZE)
    if not rows:
        bal_str = f"{balance:,}c"
        await bot.highrise.send_whisper(
            user.id, f"💰 No badges in your price range ({bal_str}).\n!badges common"
        )
        return

    lines = [f"💰 Affordable p{page}/{total_pages}"]
    for r in rows:
        bid   = r.get("rowid") or 0
        owned = db.owns_emoji_badge(user.username, r["badge_id"])
        tick  = "✅" if owned else ""
        lines.append(f"B{bid:03d} {r['emoji']}{tick} {r['name']} — {_short(r['price'])}c")

    nav = []
    if page > 1:           nav.append(f"!badges affordable {page-1}")
    if page < total_pages: nav.append(f"!badges affordable {page+1}")
    if nav: lines.append("  ".join(nav))
    lines.append("Buy: !buybadge <id>")
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


async def handle_badge_sold(bot: BaseBot, user: User, page: int = 1) -> None:
    """!badges sold [page] — browse sold/claimed badges (collectibles view)."""
    page = max(1, page)
    rows, total_pages = db.get_sold_badges(page=page, per_page=_BROWSE_SIZE)
    if not rows:
        await bot.highrise.send_whisper(user.id, "No sold badges yet.")
        return

    lines = [f"🔴 Sold p{page}/{total_pages}"]
    for r in rows:
        bid = r.get("rowid") or 0
        lines.append(f"B{bid:03d} {r['emoji']} {r['name']} — {_short(r['price'])}c")

    nav = []
    if page > 1:           nav.append(f"!badges sold {page-1}")
    if page < total_pages: nav.append(f"!badges sold {page+1}")
    if nav: lines.append("  ".join(nav))
    lines.append("Market: !badgemarket")
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


async def handle_badge_help(bot: BaseBot, user: User) -> None:
    """!badgehelp — player badge commands (Part 12)."""
    await bot.highrise.send_whisper(user.id, (
        "🏷️ Badge Commands\n"
        "Browse: !badgeshop\n"
        "Owned: !mybadges\n"
        "Equip: !equipbadge badge_id\n"
        "Buy: !buybadge badge_id\n"
        "Search: !badgesearch name\n"
        "Market: !badgemarket"
    )[:249])
    await bot.highrise.send_whisper(user.id, (
        "🏷️ More Commands\n"
        "Info: !badgeinfo badge_id\n"
        "Unequip: !unequipbadge\n"
        "Sell: !sellbadge badge_id price\n"
        "Cancel listing: !cancelbadge id\n"
        "Listings: !mybadgelistings\n"
        "Trade: !trade @user"
    )[:249])


async def handle_badgeadminhelp(bot: BaseBot, user: User) -> None:
    """!badgeadminhelp — admin badge commands (Part 12)."""
    if not _can_manage(user.username):
        await bot.highrise.send_whisper(user.id, "⚠️ Staff only.")
        return
    await bot.highrise.send_whisper(user.id, (
        "🛠️ Badge Admin\n"
        "!addbadge id emoji name rarity price\n"
        "!editbadgeprice id price\n"
        "!setbadgepurchasable id on/off\n"
        "!badgecatalog\n"
        "!giveemojibadge @user emoji name"
    )[:249])
    await bot.highrise.send_whisper(user.id, (
        "🛠️ More Admin\n"
        "!setbadgetradeable id on/off\n"
        "!setbadgesellable id on/off\n"
        "!badgeadmin id\n"
        "!setbadgemarketfee percent\n"
        "!badgemarketlogs [@user]\n"
        "!marketaudit  !clearbadgelocks"
    )[:249])


async def handle_badges_view(bot: BaseBot, user: User, args: list[str]) -> None:
    """!badges @user — view another player's badge collection (Part 10)."""
    target = args[1].strip().lstrip("@") if len(args) > 1 else user.username
    db.ensure_user(user.id, user.username)

    if target.lower() != user.username.lower():
        priv = db.get_profile_privacy(target)
        if not priv.get("show_inventory", 1) and not _can_manage(user.username):
            await bot.highrise.send_whisper(user.id, f"@{target}'s inventory is hidden.")
            return

    owned = db.get_user_emoji_badges(target)
    if not owned:
        await bot.highrise.send_whisper(user.id, f"@{target} has no badges yet.")
        return

    # Find equipped badge for this target
    target_rec    = db.get_user_by_username(target)
    target_uid    = target_rec["user_id"] if target_rec else None
    eq_line       = ""
    if target_uid:
        eq_ids = db.get_equipped_ids(target_uid)
        eq_badge_id = eq_ids.get("badge_id")
        if eq_badge_id:
            eq_row = db.get_emoji_badge(eq_badge_id)
            if eq_row:
                eq_line = f"Equipped: {eq_row['emoji']} {eq_row['name']}\n"

    await bot.highrise.send_whisper(
        user.id,
        f"🏷️ @{target}'s Badges\n"
        f"{eq_line}"
        f"Owned: {len(owned)} badges"
    )


# ---------------------------------------------------------------------------
# /badgemarket [page]
# ---------------------------------------------------------------------------

async def handle_badgemarket(bot: BaseBot, user: User, args: list[str]) -> None:
    sub    = args[1].lower().strip() if len(args) > 1 else "1"
    second = args[2].strip()         if len(args) > 2 else ""

    # !badgemarket search [query]
    if sub == "search":
        query = " ".join(args[2:]).strip() if len(args) > 2 else ""
        if not query:
            await bot.highrise.send_whisper(user.id, "Usage: !badgemarket search <name>")
            return
        rows, total_pages = db.search_badge_listings(query, page=1, per_page=3)
        label = f"🔎 Market: {query[:15]}"
        if not rows:
            await bot.highrise.send_whisper(user.id, f"{label}\nNo listings found.")
            return
        lines = [label]
        for r in rows:
            lines.append(f"#{r['id']} {r['emoji']} {r.get('badge_name',r['badge_id'])} {_short(r['price'])}c")
        lines.append("Buy: !badgebuy <listing#>")
        await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])
        return

    # !badgemarket cheap
    if sub == "cheap":
        page = int(second) if second.isdigit() else 1
        rows, total_pages = db.get_badge_listings_filtered(sort="cheap", page=page, per_page=3)
        label = f"💰 Market Cheap p{page}/{total_pages}"
        if not rows:
            await bot.highrise.send_whisper(user.id, f"{label}\nNo listings.")
            return
        lines = [label]
        for r in rows:
            lines.append(f"#{r['id']} {r['emoji']} {r.get('badge_name',r['badge_id'])} {_short(r['price'])}c")
        lines.append("Buy: !badgebuy <listing#>")
        await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])
        return

    # !badgemarket [rarity]
    if sub in _RARITY_TIERS:
        page = int(second) if second.isdigit() else 1
        rows, total_pages = db.get_badge_listings_filtered(rarity=sub, page=page, per_page=3)
        rlabel = _CATEGORY_LABELS.get(sub, sub.capitalize())
        label  = f"🏷️ Market {rlabel} p{page}/{total_pages}"
        if not rows:
            await bot.highrise.send_whisper(user.id, f"{label}\nNo {sub} listings.")
            return
        lines = [label]
        for r in rows:
            lines.append(f"#{r['id']} {r['emoji']} {r.get('badge_name',r['badge_id'])} {_short(r['price'])}c")
        nav = []
        if page > 1:   nav.append(f"!badgemarket {sub} {page-1}")
        if page < total_pages: nav.append(f"!badgemarket {sub} {page+1}")
        if nav: lines.append("  ".join(nav))
        lines.append("Buy: !badgebuy <listing#>")
        await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])
        return

    # !badgemarket next/prev → redirect
    if sub in ("next", "prev"):
        await bot.highrise.send_whisper(
            user.id,
            "Use direct pages:\n!badgemarket 2\n!badgemarket rare\n!badgemarket cheap\n!badgemarket search crown"
        )
        return

    # Default: paginated listing
    page = int(sub) if sub.isdigit() else 1
    page = max(1, page)

    rows, total_pages = db.get_active_badge_listings(page=page, per_page=_MARKET_PAGE)
    if page > max(1, total_pages):
        page = max(1, total_pages)
        rows, total_pages = db.get_active_badge_listings(page=page, per_page=_MARKET_PAGE)

    if not rows:
        await bot.highrise.send_whisper(
            user.id,
            "🏷️ Badge Market\n"
            "No listings yet.\n"
            "Sell: !sellbadge [badge] [price]\n"
            "Browse shop: !badges"
        )
        return

    session_items = []
    lines = [f"🏷️ Market {page}/{total_pages}"]
    for num, r in enumerate(rows, 1):
        lines.append(f"{num} {r['emoji']} {r['badge_id']} {_short(r['price'])}c @{r['seller_username'][:9]}")
        session_items.append({
            "num": num, "item_id": r["badge_id"], "listing_id": r["id"],
            "name": r["badge_id"], "emoji": r["emoji"], "price": r["price"],
            "currency": "coins", "seller": r["seller_username"], "shop_type": "market_badges",
        })

    nav = []
    if page > 1:             nav.append(f"!badgemarket {page-1}")
    if page < total_pages:   nav.append(f"!badgemarket {page+1}")
    footer_parts = ["Buy: !badgebuy [#]"]
    if nav: footer_parts.append("  ".join(nav))
    lines.append("  ".join(footer_parts))

    msg = "\n".join(lines)
    if len(msg) > 249:
        lines = [f"🏷️ Market {page}/{total_pages}"]
        for item in session_items:
            lines.append(f"{item['num']} {item['emoji']} {_short(item['price'])}c")
        lines.append("!badgebuy <#>  Filters: rare | cheap | search")
        msg = "\n".join(lines)[:249]

    db.save_shop_session(user.username, "market_badges", page, session_items)
    await bot.highrise.send_whisper(user.id, msg)


# ---------------------------------------------------------------------------
# /badgelist <badge_id> <price>
# ---------------------------------------------------------------------------

async def handle_badgelist(bot: BaseBot, user: User, args: list[str]) -> None:
    """!sellbadge <badge_id> <price> — list a badge on the player market."""
    if len(args) < 3:
        await bot.highrise.send_whisper(
            user.id,
            "Usage: !sellbadge <badge_id> <price>\n"
            "Example: !sellbadge crown 20000"
        )
        return

    badge_id  = args[1].lower().strip()
    price_raw = args[2].strip()

    if not price_raw.isdigit() or price_raw.startswith("-"):
        await bot.highrise.send_whisper(user.id, "⚠️ Price must be a positive number.")
        return

    price = int(price_raw)
    if price <= 0:
        await bot.highrise.send_whisper(user.id, "⚠️ Price must be a positive number.")
        return
    if price < _MIN_PRICE:
        await bot.highrise.send_whisper(user.id, f"⚠️ Minimum listing price: {_MIN_PRICE:,}c.")
        return
    if price > _MAX_PRICE:
        await bot.highrise.send_whisper(user.id, f"⚠️ Maximum listing price: {_short(_MAX_PRICE)}c.")
        return

    row = db.get_emoji_badge(badge_id)
    if row is None:
        # Try fuzzy search
        results = db.search_emoji_badges(badge_id, purchasable_only=False, limit=3)
        if results:
            hits = ", ".join(r["badge_id"] for r in results)
            await bot.highrise.send_whisper(
                user.id, f"⚠️ Badge '{badge_id}' not found. Did you mean: {hits}?"[:249]
            )
        else:
            await bot.highrise.send_whisper(user.id, f"⚠️ Badge '{badge_id}' not found.")
        return

    db.ensure_user(user.id, user.username)

    if not row["tradeable"] or not row["sellable"]:
        await bot.highrise.send_whisper(
            user.id,
            f"⚠️ {row['emoji']} {row['name']} is bound and cannot be sold."
        )
        return

    if not db.owns_emoji_badge(user.username, badge_id):
        await bot.highrise.send_whisper(
            user.id, f"⚠️ You do not own {row['emoji']} {row['name']}."
        )
        return

    # Check if locked
    badges    = db.get_user_emoji_badges(user.username)
    badge_row = next((b for b in badges if b["badge_id"] == badge_id), None)
    if badge_row and badge_row.get("locked"):
        await bot.highrise.send_whisper(
            user.id, "⚠️ This badge is locked and cannot be listed."
        )
        return

    if db.is_badge_listed(user.username, badge_id):
        # Find the existing listing so we can show its ID
        existing = db.get_user_badge_listings(user.username)
        eid = next((r["id"] for r in existing if r["badge_id"] == badge_id), "?")
        await bot.highrise.send_whisper(
            user.id,
            f"⚠️ Badge already listed.\n"
            f"Use !cancelbadge {eid} first."
        )
        return

    # Unequip before listing
    equipped = db.get_equipped_ids(user.id)
    if equipped.get("badge_id") == badge_id:
        await bot.highrise.send_whisper(
            user.id, "⚠️ Unequip badge first: !unequipbadge"
        )
        return

    listing_id = db.create_badge_listing(user.username, badge_id, row["emoji"], price)
    if listing_id < 0:
        await bot.highrise.send_whisper(user.id, "❌ Failed to create listing. Try again!")
        return

    db.log_badge_market_action(
        "listed", user.username, "", badge_id, row["emoji"], price, 0, "active"
    )
    await bot.highrise.send_whisper(
        user.id,
        f"✅ Badge listed!\n"
        f"{row['emoji']} {row['name']} — {price:,}c\n"
        f"Listing ID: #{listing_id}"
    )


# ---------------------------------------------------------------------------
# /badgebuy <listing_id>
# ---------------------------------------------------------------------------

async def handle_badgebuy(bot: BaseBot, user: User, args: list[str]) -> None:
    """!badgebuy <listing_id>  or  !buybadge listing <listing_id>"""
    # Support: !buybadge listing 12 → args = ["buybadge","listing","12"]
    raw = ""
    if len(args) >= 2:
        if args[1].lower() == "listing" and len(args) >= 3:
            raw = args[2].strip()
        else:
            raw = args[1].strip()

    if not raw or not raw.isdigit():
        await bot.highrise.send_whisper(
            user.id,
            "Usage: !buybadge listing <id>\n"
            "Browse: !badgemarket"
        )
        return

    listing_id = int(raw)
    listing    = db.get_badge_listing(listing_id)

    if listing is None or listing["status"] != "active":
        await bot.highrise.send_whisper(user.id, "⚠️ Listing not found or no longer available.")
        return

    if listing["seller_username"].lower() == user.username.lower():
        await bot.highrise.send_whisper(user.id, "⚠️ You cannot buy your own listing.")
        return

    db.ensure_user(user.id, user.username)

    fee_pct = _fee_pct()
    error   = db.buy_badge_listing(listing_id, user.username, fee_pct)
    if error:
        if "Not enough" in error:
            await bot.highrise.send_whisper(user.id, f"⚠️ {error}"[:249])
        else:
            await bot.highrise.send_whisper(user.id, f"❌ {error}"[:249])
        return

    fee  = max(0, int(listing["price"] * fee_pct / 100))
    net  = listing["price"] - fee

    badge_name = listing.get("badge_name") or listing["badge_id"]
    await bot.highrise.send_whisper(
        user.id,
        f"✅ Badge bought!\n"
        f"{listing['emoji']} {badge_name} from @{listing['seller_username']}\n"
        f"Price: {listing['price']:,}c"
    )

    # Notify seller
    try:
        seller_note = (
            f"💰 Your badge sold!\n"
            f"{listing['emoji']} {badge_name} — {listing['price']:,}c\n"
            f"(Fee {fee:,}c — you got {net:,}c)"
        )
        seller_row = db.get_user_by_username(listing["seller_username"])
        if seller_row and seller_row.get("user_id"):
            await bot.highrise.send_whisper(seller_row["user_id"], seller_note[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /badgecancel <listing_id>
# ---------------------------------------------------------------------------

async def handle_badgecancel(bot: BaseBot, user: User, args: list[str]) -> None:
    """!cancelbadge <listing_id> — cancel your own listing (or owner/admin any listing)."""
    if len(args) < 2 or not args[1].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: !cancelbadge <listing_id>")
        return

    listing_id = int(args[1])
    # Fetch listing before cancel so we can show the badge name
    listing_pre = db.get_badge_listing(listing_id)

    is_staff = _can_manage(user.username)
    error    = db.cancel_badge_listing(listing_id, user.username, is_staff)

    if error:
        await bot.highrise.send_whisper(user.id, f"❌ {error}"[:249])
        return

    if listing_pre:
        badge_name = listing_pre.get("badge_name") or listing_pre["badge_id"]
        emoji      = listing_pre["emoji"]
        db.log_badge_market_action(
            "cancelled", user.username, "", listing_pre["badge_id"],
            emoji, listing_pre["price"], 0, "cancelled"
        )
        await bot.highrise.send_whisper(
            user.id,
            f"✅ Badge listing cancelled.\n"
            f"{emoji} {badge_name} returned to inventory."
        )
    else:
        await bot.highrise.send_whisper(user.id, "✅ Listing cancelled.")


# ---------------------------------------------------------------------------
# /mybadgelistings
# ---------------------------------------------------------------------------

async def handle_mybadgelistings(bot: BaseBot, user: User, args: list[str] | None = None) -> None:
    """!mylistings — show caller's active market listings."""
    db.ensure_user(user.id, user.username)
    rows = db.get_user_badge_listings(user.username)

    if not rows:
        await bot.highrise.send_whisper(
            user.id,
            "🏷️ Your Listings\n"
            "No active badge listings.\n"
            "Sell: !sellbadge <badge_id> <price>"
        )
        return

    lines = ["🏷️ Your Listings"]
    for r in rows:
        badge_name = r.get("badge_name") or r["badge_id"]
        lines.append(f"#{r['id']} {r['emoji']} {badge_name} — {r['price']:,}c")
    lines.append("Cancel: !cancelbadge <id>")

    msg = "\n".join(lines)
    await bot.highrise.send_whisper(user.id, msg[:249])


# ---------------------------------------------------------------------------
# /badgeprices <badge_id>
# ---------------------------------------------------------------------------

async def handle_badgeprices(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: !badgeprices <badge_id>")
        return

    badge_id = args[1].lower().strip()
    row      = db.get_emoji_badge(badge_id)
    if row is None:
        await bot.highrise.send_whisper(user.id, f"Badge '{badge_id}' not found.")
        return

    prices = db.get_badge_recent_prices(badge_id, limit=5)
    if not prices:
        await bot.highrise.send_whisper(
            user.id, f"{row['emoji']} {badge_id} — no recent sales. Shop: {row['price']:,}c"
        )
        return

    price_str = ", ".join(_short(p) for p in prices)
    await bot.highrise.send_whisper(
        user.id, f"{row['emoji']} {badge_id} recent: {price_str}c | Shop: {row['price']:,}c"[:249]
    )


# ===========================================================================
# ADMIN / OWNER COMMANDS
# ===========================================================================

# ---------------------------------------------------------------------------
# /addbadge <id> <emoji> <name> <rarity> <price>
# ---------------------------------------------------------------------------

async def handle_addbadge(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        return
    if len(args) < 6:
        await bot.highrise.send_whisper(
            user.id, "Usage: !addbadge <id> <emoji> <name> <rarity> <price>\n"
                     "Rarities: common uncommon rare epic legendary mythic exclusive"
        )
        return

    badge_id = args[1].lower().strip()
    emoji    = args[2].strip()
    name     = args[3].strip()
    rarity   = args[4].lower().strip()
    price_s  = args[5].strip()

    valid_rarities = {"common","uncommon","rare","epic","legendary","mythic","exclusive","event","staff","vip"}
    if rarity not in valid_rarities:
        await bot.highrise.send_whisper(user.id, f"Invalid rarity. Use: {', '.join(sorted(valid_rarities))}")
        return

    if not price_s.isdigit():
        await bot.highrise.send_whisper(user.id, "Price must be a non-negative integer.")
        return

    price       = int(price_s)
    purchasable = 0 if rarity in {"exclusive","event","staff","vip"} else 1
    tradeable   = 0 if rarity in {"exclusive","staff"} else 1
    sellable    = tradeable
    source      = rarity if rarity in {"exclusive","event","staff","vip"} else "shop"

    ok = db.add_emoji_badge(
        badge_id, emoji, name, rarity, price,
        purchasable, tradeable, sellable, source, user.username
    )
    if ok:
        await bot.highrise.send_whisper(
            user.id, f"✅ Added {emoji} {name} ({badge_id}) [{rarity}] {price:,}c."
        )
        db.log_badge_market_action("admin_added", user.username, "", badge_id, emoji, price, 0, "added")
    else:
        await bot.highrise.send_whisper(user.id, f"❌ Badge '{badge_id}' already exists or error.")


# ---------------------------------------------------------------------------
# /editbadgeprice <badge_id> <price>
# ---------------------------------------------------------------------------

async def handle_editbadgeprice(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        return
    if len(args) < 3:
        await bot.highrise.send_whisper(user.id, "Usage: !editbadgeprice <badge_id> <price>")
        return

    badge_id = args[1].lower().strip()
    price_s  = args[2].strip()
    if not price_s.isdigit():
        await bot.highrise.send_whisper(user.id, "Price must be a non-negative integer.")
        return

    row = db.get_emoji_badge(badge_id)
    if row is None:
        await bot.highrise.send_whisper(user.id, f"Badge '{badge_id}' not found.")
        return

    db.update_emoji_badge_field(badge_id, "price", int(price_s))
    await bot.highrise.send_whisper(
        user.id, f"✅ {row['emoji']} {badge_id} price set to {int(price_s):,}c."
    )
    db.log_badge_market_action("admin_edited", user.username, "", badge_id, row["emoji"], int(price_s), 0, "price_updated")


# ---------------------------------------------------------------------------
# /setbadgepurchasable /setbadgetradeable /setbadgesellable
# ---------------------------------------------------------------------------

async def handle_setbadgeflag(
    bot: BaseBot, user: User, args: list[str], field: str
) -> None:
    if not _can_manage(user.username):
        return
    if len(args) < 3:
        await bot.highrise.send_whisper(user.id, f"Usage: !{field} <badge_id> on/off")
        return

    badge_id = args[1].lower().strip()
    toggle   = args[2].lower().strip()
    if toggle not in ("on", "off"):
        await bot.highrise.send_whisper(user.id, "Use on or off.")
        return

    row = db.get_emoji_badge(badge_id)
    if row is None:
        await bot.highrise.send_whisper(user.id, f"Badge '{badge_id}' not found.")
        return

    val = 1 if toggle == "on" else 0
    db.update_emoji_badge_field(badge_id, field, val)
    label = field.replace("_", " ").title()
    await bot.highrise.send_whisper(
        user.id, f"✅ {row['emoji']} {badge_id} {label} set to {toggle.upper()}."
    )


# ---------------------------------------------------------------------------
# /givebadge <username> <badge_id>  (emoji badge system)
# ---------------------------------------------------------------------------

async def handle_givebadge_emoji(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        return
    if len(args) < 3:
        await bot.highrise.send_whisper(user.id, "Usage: !givebadge <username> <badge_id>")
        return

    target   = args[1].strip().lstrip("@")
    badge_id = args[2].lower().strip()
    row      = db.get_emoji_badge(badge_id)

    if row is None:
        await bot.highrise.send_whisper(user.id, f"Badge '{badge_id}' not found.")
        return

    if db.owns_emoji_badge(target, badge_id):
        await bot.highrise.send_whisper(user.id, f"@{target} already owns {row['emoji']} {badge_id}.")
        return

    db.grant_emoji_badge(target, badge_id, source="admin")
    db.log_badge_market_action("admin_granted", user.username, target, badge_id, row["emoji"], 0, 0, "granted")
    await bot.highrise.send_whisper(
        user.id, f"✅ Gave {row['emoji']} {badge_id} to @{target}."
    )


# ---------------------------------------------------------------------------
# /removebadgefrom <username> <badge_id>
# ---------------------------------------------------------------------------

async def handle_removebadge_from(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        return
    if len(args) < 3:
        await bot.highrise.send_whisper(user.id, "Usage: !removebadgefrom <username> <badge_id>")
        return

    target   = args[1].strip().lstrip("@")
    badge_id = args[2].lower().strip()
    row      = db.get_emoji_badge(badge_id)
    emoji    = row["emoji"] if row else badge_id

    db.revoke_emoji_badge(target, badge_id)
    db.log_badge_market_action("admin_removed", user.username, target, badge_id, emoji, 0, 0, "removed")
    await bot.highrise.send_whisper(user.id, f"✅ Removed {emoji} {badge_id} from @{target}.")


# ---------------------------------------------------------------------------
# /giveemojibadge <username> <emoji> <name>
# ---------------------------------------------------------------------------

async def handle_giveemojibadge(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        return
    if len(args) < 4:
        await bot.highrise.send_whisper(
            user.id, "Usage: !giveemojibadge <username> <emoji> <name>"
        )
        return

    target = args[1].strip().lstrip("@")
    emoji  = args[2].strip()
    name   = " ".join(args[3:]).strip()

    # Build badge_id from name (lowercase, no spaces)
    badge_id = name.lower().replace(" ", "_")[:24]

    # Create badge if it doesn't exist
    existing = db.get_emoji_badge(badge_id)
    if existing is None:
        db.add_emoji_badge(
            badge_id, emoji, name, "exclusive", 0,
            purchasable=0, tradeable=0, sellable=0,
            source="exclusive", created_by=user.username
        )

    if db.owns_emoji_badge(target, badge_id):
        await bot.highrise.send_whisper(user.id, f"@{target} already has {emoji} {badge_id}.")
        return

    db.grant_emoji_badge(target, badge_id, source="admin", locked=1)
    db.log_badge_market_action("admin_granted", user.username, target, badge_id, emoji, 0, 0, "exclusive_grant")
    await bot.highrise.send_whisper(user.id, f"✅ Created & gave {emoji} {name} ({badge_id}) to @{target}.")


# ---------------------------------------------------------------------------
# /badgecatalog [page]  — full catalog including non-purchasable
# ---------------------------------------------------------------------------

async def handle_badgecatalog(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        return
    raw  = args[1] if len(args) > 1 else "1"
    page = int(raw) if raw.isdigit() else 1

    rows, total_pages = db.get_emoji_badges_page(page=page, per_page=_PAGE_SIZE, purchasable_only=False)

    if not rows:
        await bot.highrise.send_whisper(user.id, "No badges in catalog.")
        return

    lines = [f"📋 Catalog {page}/{total_pages}"]
    for r in rows:
        p = "✓" if r["purchasable"] else "✗"
        lines.append(f"{r['emoji']} {r['badge_id']} {_short(r['price'])}c [{r['rarity']}] P:{p}")
    if page < total_pages:
        lines.append(f"More: /badgecatalog {page + 1}")

    msg = "\n".join(lines)
    if len(msg) > 249:
        lines = lines[:5]
        lines.append(f"More: /badgecatalog {page + 1}")
        msg = "\n".join(lines)

    await bot.highrise.send_whisper(user.id, msg)


# ---------------------------------------------------------------------------
# /badgeadmin <badge_id>
# ---------------------------------------------------------------------------

async def handle_badgeadmin(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: !badgeadmin <badge_id>")
        return

    badge_id = args[1].lower().strip()
    row      = db.get_emoji_badge(badge_id)
    if row is None:
        await bot.highrise.send_whisper(user.id, f"Badge '{badge_id}' not found.")
        return

    msg = (
        f"{row['emoji']} {row['name']} ({badge_id})\n"
        f"Rarity: {row['rarity']}  Price: {row['price']:,}c\n"
        f"Buy:{row['purchasable']} Trade:{row['tradeable']} Sell:{row['sellable']}\n"
        f"Source: {row['source']}  By: {row['created_by'] or 'system'}"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


# ---------------------------------------------------------------------------
# /setbadgemarketfee <percent>
# ---------------------------------------------------------------------------

async def handle_setbadgemarketfee(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        return
    if len(args) < 2:
        fee = _fee_pct()
        await bot.highrise.send_whisper(user.id, f"Current market fee: {fee}%. Set: /setbadgemarketfee <0-50>")
        return

    raw = args[1].strip()
    try:
        pct = float(raw)
    except ValueError:
        await bot.highrise.send_whisper(user.id, "Fee must be a number 0-50.")
        return

    if not 0 <= pct <= 50:
        await bot.highrise.send_whisper(user.id, "Fee must be 0-50.")
        return

    db.set_bot_setting("badge_market_fee_percent", str(pct))
    await bot.highrise.send_whisper(user.id, f"✅ Badge market fee set to {pct}%.")


# ---------------------------------------------------------------------------
# /badgemarketlogs [username]
# ---------------------------------------------------------------------------

async def handle_badgemarketlogs(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        return

    target = args[1].strip().lstrip("@") if len(args) > 1 else None
    logs   = db.get_badge_market_logs(username=target, limit=8)

    if not logs:
        label = f"@{target}" if target else "market"
        await bot.highrise.send_whisper(user.id, f"No badge market logs for {label}.")
        return

    lines = [f"📋 Badge Logs{' @' + target if target else ''} ({len(logs)})"]
    for lg in logs:
        ts    = (lg.get("timestamp") or "")[:16]
        emoji = lg.get("emoji") or "?"
        lines.append(
            f"{ts} {lg['action']} {emoji}{lg['badge_id']} "
            f"s:{lg['seller_username']} b:{lg['buyer_username']} {lg['price']:,}c"
        )

    msg = "\n".join(lines)
    # Split if too long
    if len(msg) > 249:
        part1 = "\n".join(lines[:5])
        part2 = "\n".join(lines[5:])
        await bot.highrise.send_whisper(user.id, part1[:249])
        if part2:
            await bot.highrise.send_whisper(user.id, part2[:249])
        return

    await bot.highrise.send_whisper(user.id, msg)


# ===========================================================================
# 3.1E  NEW BADGE COMMANDS
# ===========================================================================

# ---------------------------------------------------------------------------
# !buybadge [badge_id or name] — player-friendly direct badge purchase
# ---------------------------------------------------------------------------

async def handle_buybadge_cmd(bot: BaseBot, user: User, args: list[str]) -> None:
    """!buybadge [B###/badge_id/name] — buy a badge from the shop."""
    if len(args) < 2:
        await bot.highrise.send_whisper(
            user.id, "Usage: !buybadge <B###>  Browse: !badges"
        )
        return
    raw = args[1].strip()

    # B### display ID (e.g. B042 or b042)
    if raw.upper().startswith("B") and raw[1:].isdigit():
        rowid = int(raw[1:])
        row   = db.get_emoji_badge_by_rowid(rowid)
        if row is None:
            await bot.highrise.send_whisper(user.id, f"⚠️ Badge {raw.upper()} not found.")
            return
        await handle_buy_badge(bot, user, row["badge_id"])
        return

    query = " ".join(args[1:]).strip().lower()
    row   = db.get_emoji_badge(query)
    if row is None:
        row = db.find_emoji_badge_by_name(query)
    if row is None:
        results = db.search_emoji_badges(query, purchasable_only=True, limit=3)
        if results:
            hits = ", ".join(
                f"B{r.get('rowid',0):03d} {r['emoji']} {r['name']}" for r in results
            )
            await bot.highrise.send_whisper(
                user.id, f"Did you mean:\n{hits}\nUse: !buybadge B###"[:249]
            )
        else:
            await bot.highrise.send_whisper(
                user.id, f"⚠️ Badge '{query[:20]}' not found. Browse: !badges"
            )
        return
    await handle_buy_badge(bot, user, row["badge_id"])


# ---------------------------------------------------------------------------
# !staffbadge [emoji] [name] — staff creates a bound badge for themselves
# ---------------------------------------------------------------------------

async def handle_staffbadge(bot: BaseBot, user: User, args: list[str]) -> None:
    """!staffbadge [emoji] [name] — manager+: create & equip a bound staff badge."""
    from modules.permissions import is_manager, is_admin, is_owner
    if not (is_manager(user.username) or is_admin(user.username) or is_owner(user.username)):
        await bot.highrise.send_whisper(user.id, "Staff (manager+) only.")
        return
    if len(args) < 3:
        await bot.highrise.send_whisper(
            user.id, "Usage: !staffbadge [emoji] [name]"
        )
        return
    emoji    = args[1].strip()
    name     = " ".join(args[2:]).strip()
    slug     = name.lower().replace(" ", "_")[:14]
    badge_id = f"staff_{user.username.lower()[:8]}_{slug}"

    existing = db.get_emoji_badge(badge_id)
    if existing is None:
        db.add_emoji_badge(
            badge_id, emoji, name, "staff", 0,
            purchasable=0, tradeable=0, sellable=0,
            source="staff_created", created_by=user.username,
        )

    if not db.owns_emoji_badge(user.username, badge_id):
        db.grant_emoji_badge(user.username, badge_id, source="staff_created", locked=1)

    db.equip_item(user.id, badge_id, "badge", emoji)
    await bot.highrise.send_whisper(
        user.id,
        f"✅ Staff badge set: {emoji} {name}\n"
        f"ID: {badge_id}  🔒 Bound"
    )


# ---------------------------------------------------------------------------
# !emojitest [page] — owner-only paginated emoji viewer
# ---------------------------------------------------------------------------

_EMOJI_TEST_LIST: list[str] = [
    "😀","😃","😄","😁","😆","😅","😂","🙂","🙃","😉",
    "😊","😎","🤩","🥳","😇","😈","👻","💀","🤖","❤️",
    "🧡","💛","💚","💙","💜","🖤","🤍","🤎","💖","💗",
    "💓","💞","💕","💘","💝","⭐","🌟","✨","⚡","🔥",
    "🌙","☀️","🌈","☁️","❄️","🌊","🌌","🪐","🌍","🌎",
    "🌏","🐶","🐱","🐭","🐹","🐰","🦊","🐻","🐼","🐨",
    "🐯","🦁","🐮","🐷","🐸","🐵","🐺","🐉","🦄","🦋",
    "🐝","🐢","🦈","🐬","🐳","🍎","🍊","🍋","🍌","🍉",
    "🍇","🍓","🍒","🍑","🍍","🥥","🥑","🍔","🍕","🌮",
    "🍣","🍩","🍪","🍰","🍭","💎","👑","🎩","🎧","🎮",
    "🕹️","📱","💻","🛡️","⚔️","🏆","🥇","🎲","🎯","🎁",
    "🔑","💰","💸","⚽","🏀","🏈","⚾","🎾","🏐","🏓",
    "🥊","🎣","⛏️","🎤","🎵","🎶","🎨","🚗","✈️","🚀",
    "♈","♉","♊","♋","♌","♍","♎","♏","♐","♑","♒","♓",
    "✅","❌","❗","❓","💯","🔔","🔒","🔓","🏠","🏝️",
    "🌃","🎉","🎊","🪩","🛋️","🛏️","🕺","💃","🧿","🪽",
]


_EMOJI_CATEGORY_LISTS: dict[str, list[str]] = {
    "faces":      ["😀","😃","😄","😁","😆","😅","😂","🙂","🙃","😉",
                   "😊","😎","🤩","🥳","😇","😈","👻","💀","🤖"],
    "hearts":     ["❤️","🧡","💛","💚","💙","💜","🖤","🤍","🤎","💖",
                   "💗","💓","💞","💕","💘","💝"],
    "stars":      ["⭐","🌟","✨","💫"],
    "sky":        ["🔥","❄️","⚡","🌙","☀️","🌈","🍀","☁️","🌊","🌍","🌎","🌏"],
    "animals":    ["🐶","🐱","🐭","🐹","🐰","🦊","🐻","🐼","🐨","🐯","🦁",
                   "🐮","🐷","🐸","🐵","🐺","🐉","🦄","🦋","🐝","🐢","🦈","🐬","🐳"],
    "food":       ["🍎","🍊","🍋","🍌","🍉","🍇","🍓","🍒","🍑","🍍",
                   "🥥","🥑","🍔","🍕","🌮","🍣","🍩","🍪","🍰","🍭"],
    "objects":    ["💎","👑","🎩","🎧","🕹️","📱","💻","🛡️","⚔️","🎁","🔑","💰","💸","🪄"],
    "activities": ["⚽","🏀","🏈","⚾","🎾","🏐","🏓","🥊","🎣","⛏️","🎤","🎵","🎶","🎨","🚗","✈️","🚀","🎮","🎲","🎯"],
    "zodiac":     ["♈","♉","♊","♋","♌","♍","♎","♏","♐","♑","♒","♓"],
    "symbols":    ["✅","❌","❗","❓","💯","🔔","🔒","🔓","🧿","🧬"],
    "room":       ["🏠","🏝️","🌃","🎉","🎊","🪩","🛋️","🛏️","🕺","💃","🏆","🥇","🎖️"],
}


async def handle_emojitest(bot: BaseBot, user: User, args: list[str]) -> None:
    """!emojitest [category|page] — owner-only paginated emoji viewer."""
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Owner only.")
        return

    raw = args[1].lower() if len(args) > 1 else "1"

    # Category mode: !emojitest animals
    cat = _CATEGORY_ALIASES.get(raw) or (raw if raw in _EMOJI_CATEGORY_LISTS else None)
    if cat and cat in _EMOJI_CATEGORY_LISTS:
        emojis = _EMOJI_CATEGORY_LISTS[cat]
        label  = _CATEGORY_LABELS.get(cat, cat.capitalize())
        msg    = f"🧪 {label} ({len(emojis)})\n" + " ".join(emojis)
        await bot.highrise.send_whisper(user.id, msg[:249])
        return

    # Page mode: !emojitest 2
    page        = int(raw) if raw.isdigit() else 1
    per_page    = 20
    total_pages = max(1, -(-len(_EMOJI_TEST_LIST) // per_page))
    page        = max(1, min(page, total_pages))
    start       = (page - 1) * per_page
    chunk       = _EMOJI_TEST_LIST[start:start + per_page]
    msg = f"🧪 Emoji p{page}/{total_pages}\n" + " ".join(chunk)
    await bot.highrise.send_whisper(user.id, msg[:249])


# ---------------------------------------------------------------------------
# !disableemoji [badge_id] — owner-only: hide badge from shop
# ---------------------------------------------------------------------------

async def handle_disableemoji(bot: BaseBot, user: User, args: list[str]) -> None:
    """!disableemoji [badge_id] — owner: hide a badge from the badge shop."""
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Owner only.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: !disableemoji <badge_id>")
        return
    badge_id = args[1].strip().lower()
    row = db.get_emoji_badge(badge_id)
    if row is None:
        await bot.highrise.send_whisper(user.id, f"Badge '{badge_id}' not found.")
        return
    db.set_emoji_badge_enabled(badge_id, 0)
    await bot.highrise.send_whisper(
        user.id, f"✅ {row['emoji']} {badge_id} hidden from badge shop."
    )


# ---------------------------------------------------------------------------
# !enableemoji [badge_id] — owner-only: restore badge to shop
# ---------------------------------------------------------------------------

async def handle_enableemoji(bot: BaseBot, user: User, args: list[str]) -> None:
    """!enableemoji [badge_id] — owner: restore a badge to the badge shop."""
    from modules.permissions import is_owner
    if not is_owner(user.username):
        await bot.highrise.send_whisper(user.id, "Owner only.")
        return
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: !enableemoji <badge_id>")
        return
    badge_id = args[1].strip().lower()
    row = db.get_emoji_badge(badge_id)
    if row is None:
        await bot.highrise.send_whisper(user.id, f"Badge '{badge_id}' not found.")
        return
    db.set_emoji_badge_enabled(badge_id, 1)
    await bot.highrise.send_whisper(
        user.id, f"✅ {row['emoji']} {badge_id} restored to badge shop."
    )


# ===========================================================================
# MARKET ALIAS COMMANDS (3.1F)
# ===========================================================================

async def handle_marketsearch(bot: BaseBot, user: User, args: list[str]) -> None:
    """!marketsearch <text> — search active badge market listings."""
    query = " ".join(args[1:]).strip() if len(args) > 1 else ""
    if not query:
        await bot.highrise.send_whisper(user.id, "Usage: !marketsearch <name>")
        return
    rows, _ = db.search_badge_listings(query, page=1, per_page=3)
    label   = f"🔎 Market: {query[:15]}"
    if not rows:
        await bot.highrise.send_whisper(user.id, f"🔎 No market listings found.")
        return
    lines = [label]
    for r in rows:
        badge_name = r.get("badge_name") or r["badge_id"]
        lines.append(f"#{r['id']} {r['emoji']} {badge_name} — {_short(r['price'])}c")
    lines.append("Buy: !buybadge listing <id>")
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


async def handle_marketfilter(bot: BaseBot, user: User, args: list[str]) -> None:
    """!marketfilter <rarity|cheap|affordable> [page] — filter market listings."""
    filt = args[1].lower().strip() if len(args) > 1 else ""
    page = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1

    if not filt:
        await bot.highrise.send_whisper(
            user.id,
            "Usage: !marketfilter <rarity|cheap|affordable>\n"
            "Examples: !marketfilter rare  !marketfilter cheap"
        )
        return

    if filt == "affordable":
        db.ensure_user(user.id, user.username)
        balance = db.get_balance(user.id)
        rows, total = db.get_badge_listings_filtered(
            sort="cheap", page=page, per_page=3,
            max_price=balance
        )
        label = f"💰 Affordable p{page}/{total}"
    elif filt == "cheap":
        rows, total = db.get_badge_listings_filtered(sort="cheap", page=page, per_page=3)
        label = f"💰 Cheap p{page}/{total}"
    elif filt in _RARITY_TIERS:
        rows, total = db.get_badge_listings_filtered(rarity=filt, page=page, per_page=3)
        label = f"{_CATEGORY_LABELS.get(filt, filt.capitalize())} p{page}/{total}"
    else:
        await bot.highrise.send_whisper(
            user.id,
            f"⚠️ Unknown filter '{filt}'.\n"
            "Try: cheap, affordable, common, rare, epic, legendary"
        )
        return

    if not rows:
        await bot.highrise.send_whisper(user.id, f"{label}\nNo listings found.")
        return

    lines = [label]
    for r in rows:
        badge_name = r.get("badge_name") or r["badge_id"]
        lines.append(f"#{r['id']} {r['emoji']} {badge_name} — {_short(r['price'])}c")
    nav = []
    if page > 1:   nav.append(f"!marketfilter {filt} {page-1}")
    if page < total: nav.append(f"!marketfilter {filt} {page+1}")
    if nav: lines.append("  ".join(nav))
    lines.append("Buy: !buybadge listing <id>")
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


# ===========================================================================
# OWNER/ADMIN MARKET AUDIT (3.1F)
# ===========================================================================

async def handle_marketaudit(bot: BaseBot, user: User) -> None:
    """!marketaudit — owner/admin: badge market health snapshot."""
    if not _can_manage(user.username):
        await bot.highrise.send_whisper(user.id, "Owner/admin only.")
        return
    stats = db.get_market_audit_stats()
    issues = stats["orphans"]
    lines  = [
        "🏷️ Market Audit",
        f"Active Listings: {stats['active']}",
        f"Trades: {stats['trades']}",
        f"Issues (orphans): {issues}",
    ]
    if issues:
        lines.append("Fix: !clearbadgelocks")
    else:
        lines.append("Issues: none")
    await bot.highrise.send_whisper(user.id, "\n".join(lines)[:249])


async def handle_marketdebug(bot: BaseBot, user: User, args: list[str]) -> None:
    """!marketdebug <listing_id> — owner/admin: show full listing detail."""
    if not _can_manage(user.username):
        await bot.highrise.send_whisper(user.id, "Owner/admin only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: !marketdebug <listing_id>")
        return
    lid  = int(args[1])
    info = db.get_badge_listing_detail(lid)
    if info is None:
        await bot.highrise.send_whisper(user.id, f"⚠️ Listing #{lid} not found.")
        return
    owns_str = "YES" if info["seller_still_owns"] else "❌ NO"
    msg = (
        f"🔍 Listing #{lid}\n"
        f"{info['emoji']} {info['badge_id']} — {_short(info['price'])}c\n"
        f"Seller: @{info['seller_username']}  Status: {info['status']}\n"
        f"Seller still owns: {owns_str}"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


async def handle_forcelistingcancel(bot: BaseBot, user: User, args: list[str]) -> None:
    """!forcelistingcancel <listing_id> — owner/admin: force-cancel any listing."""
    if not _can_manage(user.username):
        await bot.highrise.send_whisper(user.id, "Owner/admin only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: !forcelistingcancel <listing_id>")
        return
    lid   = int(args[1])
    error = db.force_cancel_badge_listing(lid, user.username)
    if error:
        await bot.highrise.send_whisper(user.id, f"❌ {error}"[:249])
    else:
        await bot.highrise.send_whisper(user.id, f"✅ Listing #{lid} force-cancelled.")


async def handle_clearbadgelocks(bot: BaseBot, user: User) -> None:
    """!clearbadgelocks — owner/admin: cancel orphaned listings where badge was already moved."""
    if not _can_manage(user.username):
        await bot.highrise.send_whisper(user.id, "Owner/admin only.")
        return
    fixed = db.clear_stale_badge_locks(user.username)
    if fixed:
        await bot.highrise.send_whisper(
            user.id, f"✅ Cleared {fixed} stale listing(s). Badges unlocked."
        )
    else:
        await bot.highrise.send_whisper(user.id, "✅ No stale locks found.")


# ===========================================================================
# P2P BADGE TRADE SYSTEM (3.1F)
# ===========================================================================

_TRADE_TIMEOUT_SECS = 300  # 5 minutes


def _trade_is_a(trade: dict, user_id: str) -> bool:
    return trade["user_a_id"] == user_id


async def handle_trade_start(bot: BaseBot, user: User, args: list[str]) -> None:
    """!trade @user — initiate a badge trade with another player."""
    db.expire_stale_trades()

    if db.get_active_trade_for_user(user.id):
        await bot.highrise.send_whisper(
            user.id,
            "⚠️ You already have an active trade.\n"
            "Use !tradeview or !tradecancel first."
        )
        return

    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: !trade @player")
        return

    target_name = args[1].lstrip("@").strip().lower()
    if target_name == user.username.lower():
        await bot.highrise.send_whisper(user.id, "⚠️ You cannot trade with yourself.")
        return

    target_row = db.get_user_by_username(target_name)
    if not target_row or not target_row.get("user_id"):
        await bot.highrise.send_whisper(user.id, f"⚠️ Player @{target_name} not found.")
        return
    target_id = target_row["user_id"]

    if db.get_active_trade_for_user(target_id):
        await bot.highrise.send_whisper(
            user.id, f"⚠️ @{target_name} already has an active trade."
        )
        return

    db.ensure_user(user.id, user.username)
    tid = db.create_badge_trade(user.id, user.username, target_id, target_name)
    if tid < 0:
        await bot.highrise.send_whisper(user.id, "❌ Failed to start trade. Try again.")
        return

    await bot.highrise.send_whisper(
        user.id,
        f"🤝 Trade started with @{target_name}\n"
        f"Use !tradeadd <badge> or !tradecoins <amount>.\n"
        f"Both players must confirm with !tradeconfirm."
    )
    try:
        await bot.highrise.send_whisper(
            target_id,
            f"🤝 @{user.username} wants to trade.\n"
            f"Use !tradeview, !tradeadd <badge>, or !tradecancel."
        )
    except Exception:
        pass


async def handle_tradeadd(bot: BaseBot, user: User, args: list[str]) -> None:
    """!tradeadd <badge_id> — add a badge to your active trade offer."""
    db.expire_stale_trades()
    trade = db.get_active_trade_for_user(user.id)
    if not trade:
        await bot.highrise.send_whisper(
            user.id, "⚠️ No active trade. Start one: !trade @player"
        )
        return

    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: !tradeadd <badge_id>")
        return

    raw      = " ".join(args[1:]).strip().lower()
    row      = db.get_emoji_badge(raw) or db.find_emoji_badge_by_name(raw)
    if row is None:
        results = db.search_emoji_badges(raw, purchasable_only=False, limit=3)
        if results:
            hits = ", ".join(r["badge_id"] for r in results)
            await bot.highrise.send_whisper(
                user.id, f"⚠️ Badge not found. Try: {hits}"[:249]
            )
        else:
            await bot.highrise.send_whisper(user.id, f"⚠️ Badge '{raw}' not found.")
        return

    badge_id = row["badge_id"]

    if not row["tradeable"] or not row["sellable"]:
        await bot.highrise.send_whisper(
            user.id, "⚠️ This badge is bound and cannot be traded."
        )
        return

    if not db.owns_emoji_badge(user.username, badge_id):
        await bot.highrise.send_whisper(
            user.id, f"⚠️ You do not own {row['emoji']} {row['name']}."
        )
        return

    # Check locked
    badges    = db.get_user_emoji_badges(user.username)
    badge_row = next((b for b in badges if b["badge_id"] == badge_id), None)
    if badge_row and badge_row.get("locked"):
        await bot.highrise.send_whisper(user.id, "⚠️ This badge is locked.")
        return

    if db.is_badge_listed(user.username, badge_id):
        await bot.highrise.send_whisper(
            user.id,
            "⚠️ This badge is listed on the market.\n"
            "Cancel the listing first with !cancelbadge <id>."
        )
        return

    ok = db.set_trade_badge(trade["id"], user.id, badge_id, row["emoji"])
    if not ok:
        await bot.highrise.send_whisper(user.id, "❌ Failed to add badge. Try again.")
        return

    await bot.highrise.send_whisper(
        user.id,
        f"✅ Added to trade:\n"
        f"{row['emoji']} {row['name']}\n"
        f"(Both confirmations reset)"
    )


async def handle_tradecoins(bot: BaseBot, user: User, args: list[str]) -> None:
    """!tradecoins <amount> — add coins to your active trade offer."""
    db.expire_stale_trades()
    trade = db.get_active_trade_for_user(user.id)
    if not trade:
        await bot.highrise.send_whisper(
            user.id, "⚠️ No active trade. Start one: !trade @player"
        )
        return

    if len(args) < 2 or not args[1].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: !tradecoins <amount>")
        return

    amount = int(args[1])
    if amount <= 0:
        await bot.highrise.send_whisper(user.id, "⚠️ Amount must be a positive number.")
        return

    db.ensure_user(user.id, user.username)
    balance = db.get_balance(user.id)
    if balance < amount:
        await bot.highrise.send_whisper(
            user.id, f"⚠️ Not enough coins. You have {balance:,}c."
        )
        return

    ok = db.set_trade_coins(trade["id"], user.id, amount)
    if not ok:
        await bot.highrise.send_whisper(user.id, "❌ Failed to set coins. Try again.")
        return

    await bot.highrise.send_whisper(
        user.id,
        f"✅ Added coins to trade: {amount:,}c\n"
        f"(Both confirmations reset)"
    )


async def handle_tradeview(bot: BaseBot, user: User) -> None:
    """!tradeview — view current trade state."""
    db.expire_stale_trades()
    trade = db.get_active_trade_for_user(user.id)
    if not trade:
        await bot.highrise.send_whisper(
            user.id, "⚠️ No active trade. Start one: !trade @player"
        )
        return

    items    = {r["user_id"]: r for r in db.get_trade_items(trade["id"])}
    coins    = {r["user_id"]: r["amount"] for r in db.get_trade_coins(trade["id"])}
    a_id, b_id = trade["user_a_id"], trade["user_b_id"]
    a_name, b_name = trade["user_a_name"], trade["user_b_name"]

    def _offer_str(uid: str) -> str:
        parts = []
        item  = items.get(uid)
        if item:
            parts.append(f"{item['emoji']} {item['badge_id']}")
        amt = coins.get(uid, 0)
        if amt > 0:
            parts.append(f"{amt:,}c")
        return " + ".join(parts) if parts else "(nothing)"

    a_conf = "✅" if trade["user_a_confirmed"] else "❌"
    b_conf = "✅" if trade["user_b_confirmed"] else "❌"

    msg = (
        f"🤝 Trade\n"
        f"@{a_name} offers: {_offer_str(a_id)}\n"
        f"@{b_name} offers: {_offer_str(b_id)}\n"
        f"Confirm: @{a_name} {a_conf} | @{b_name} {b_conf}"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


async def handle_tradeconfirm(bot: BaseBot, user: User) -> None:
    """!tradeconfirm — confirm your side of the trade."""
    db.expire_stale_trades()
    trade = db.get_active_trade_for_user(user.id)
    if not trade:
        await bot.highrise.send_whisper(
            user.id, "⚠️ No active trade. Start one: !trade @player"
        )
        return

    is_a   = _trade_is_a(trade, user.id)
    other_id   = trade["user_b_id"] if is_a else trade["user_a_id"]
    other_name = trade["user_b_name"] if is_a else trade["user_a_name"]

    db.confirm_trade_user(trade["id"], user.id, is_a)

    # Re-fetch to check if both confirmed
    trade = db.get_active_trade_for_user(user.id)
    if not trade:
        return

    if trade["user_a_confirmed"] and trade["user_b_confirmed"]:
        error = db.execute_badge_trade(trade["id"])
        if error:
            await bot.highrise.send_whisper(user.id, f"❌ Trade failed: {error}"[:249])
            try:
                await bot.highrise.send_whisper(
                    other_id, f"❌ Trade failed: {error}"[:249]
                )
            except Exception:
                pass
            return

        # Success — tell both sides what they got
        items = {r["user_id"]: r for r in db.get_trade_items(trade["id"])}
        coins = {r["user_id"]: r["amount"] for r in db.get_trade_coins(trade["id"])}

        my_id    = user.id
        their_id = other_id

        def _got_str(uid: str) -> str:
            parts = []
            item  = items.get(uid)
            if item:
                parts.append(f"{item['emoji']} {item['badge_id']}")
            amt = coins.get(uid, 0)
            if amt > 0:
                parts.append(f"{amt:,}c")
            return " + ".join(parts) if parts else "nothing"

        await bot.highrise.send_whisper(
            user.id,
            f"✅ Trade complete!\n"
            f"You received: {_got_str(their_id)}"
        )
        try:
            await bot.highrise.send_whisper(
                other_id,
                f"✅ Trade complete!\n"
                f"You received: {_got_str(my_id)}"
            )
        except Exception:
            pass
    else:
        await bot.highrise.send_whisper(
            user.id,
            f"✅ You confirmed.\n"
            f"Waiting for @{other_name}."
        )
        try:
            await bot.highrise.send_whisper(
                other_id,
                f"🤝 @{user.username} confirmed the trade.\n"
                f"Use !tradeconfirm to complete."
            )
        except Exception:
            pass


async def handle_tradecancel(bot: BaseBot, user: User) -> None:
    """!tradecancel — cancel your active trade."""
    db.expire_stale_trades()
    trade = db.get_active_trade_for_user(user.id)
    if not trade:
        await bot.highrise.send_whisper(user.id, "⚠️ No active trade to cancel.")
        return

    other_id   = trade["user_b_id"] if _trade_is_a(trade, user.id) else trade["user_a_id"]
    other_name = trade["user_b_name"] if _trade_is_a(trade, user.id) else trade["user_a_name"]

    db.cancel_badge_trade(trade["id"])
    await bot.highrise.send_whisper(user.id, "✅ Trade cancelled.")
    try:
        await bot.highrise.send_whisper(
            other_id,
            f"⚠️ Trade cancelled by @{user.username}."
        )
    except Exception:
        pass


# ===========================================================================
# HELP PAGES (3.1F)
# ===========================================================================

async def handle_market_help(bot: BaseBot, user: User) -> None:
    """!help market — badge market commands."""
    msg = (
        "🏷️ Market Help\n"
        "!badgemarket — player listings\n"
        "!badgemarket search [name]\n"
        "!badgemarket cheap\n"
        "!sellbadge [badge] [price]\n"
        "!buybadge listing [id]\n"
        "!mylistings"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


async def handle_trade_help(bot: BaseBot, user: User) -> None:
    """!help trade — badge trade commands."""
    msg = (
        "🤝 Trading\n"
        "!trade @user — start trade\n"
        "!tradeadd [badge] — offer badge\n"
        "!tradecoins [amount] — offer coins\n"
        "!tradeview — see offers\n"
        "!tradeconfirm — confirm\n"
        "!tradecancel — cancel"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])
