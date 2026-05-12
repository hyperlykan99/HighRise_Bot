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

async def handle_shop_badges(bot: BaseBot, user: User, args: list[str]) -> None:
    raw  = args[2] if len(args) > 2 else "1"
    page = int(raw) if raw.isdigit() else 1
    page = max(1, page)

    rows, total_pages = db.get_emoji_badges_page(page=page, per_page=_PAGE_SIZE, purchasable_only=True)

    if page > total_pages:
        page = total_pages
        rows, total_pages = db.get_emoji_badges_page(page=page, per_page=_PAGE_SIZE, purchasable_only=True)

    if not rows:
        await bot.highrise.send_whisper(user.id, "No badges in shop yet. Check back later!")
        return

    db.ensure_user(user.id, user.username)
    session_items = []
    lines = [f"🏷️ Badges {page}/{total_pages}"]

    for num, r in enumerate(rows, 1):
        owned = db.owns_emoji_badge(user.username, r["badge_id"])
        tick  = "✅" if owned else ""
        lines.append(f"{num} {tick}{r['emoji']} {r['badge_id']} {_short(r['price'])}c")
        session_items.append({
            "num":       num,
            "item_id":   r["badge_id"],
            "name":      r["name"],
            "emoji":     r["emoji"],
            "price":     r["price"],
            "currency":  "coins",
            "shop_type": "badges",
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
        lines = [f"🏷️ Badges {page}/{total_pages}"]
        for item in session_items:
            tick = "✅" if db.owns_emoji_badge(user.username, item["item_id"]) else ""
            lines.append(f"{item['num']} {tick}{item['emoji']} {_short(item['price'])}c")
        lines.append("Buy: /buy <#>  More: /shop next")
        msg = "\n".join(lines)[:249]

    db.save_shop_session(user.username, "badges", page, session_items)
    await bot.highrise.send_whisper(user.id, msg)


# ---------------------------------------------------------------------------
# /badgeinfo <badge_id>
# ---------------------------------------------------------------------------

async def handle_badgeinfo(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: !badgeinfo <badge_id>")
        return

    badge_id = args[1].lower().strip()
    row      = db.get_emoji_badge(badge_id)

    if row is None:
        await bot.highrise.send_whisper(user.id, f"Badge '{badge_id}' not found. See !shop badges.")
        return

    db.ensure_user(user.id, user.username)
    owned    = db.owns_emoji_badge(user.username, badge_id)
    listed   = db.is_badge_listed(user.username, badge_id)
    purchase = "YES" if row["purchasable"] else "NO"
    trade    = "YES" if row["tradeable"]   else "NO"
    status   = " [LISTED]" if listed else (" [OWNED]" if owned else "")

    msg = (
        f"{row['emoji']} {row['name']} ({badge_id}){status}\n"
        f"Rarity: {row['rarity'].capitalize()}  Price: {row['price']:,}c\n"
        f"Buy: {purchase}  Trade: {trade}"
    )
    await bot.highrise.send_whisper(user.id, msg[:249])


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
            user.id, f"You already own {row['emoji']} {badge_id}! Use !equip badge {badge_id}."
        )
        return

    price   = row["price"]
    balance = db.get_balance(user.id)
    if balance < price:
        await bot.highrise.send_whisper(
            user.id, f"Need {price:,}c — you have {balance:,}c."
        )
        return

    # Deduct coins then grant
    success = db.buy_item(user.id, user.username, badge_id, "badge", price)
    if not success:
        await bot.highrise.send_whisper(user.id, "Purchase failed. Try again!")
        return

    db.grant_emoji_badge(user.username, badge_id, source="shop")
    new_bal = db.get_balance(user.id)
    await bot.highrise.send_whisper(
        user.id,
        f"✅ Bought {row['emoji']} {badge_id}! Balance: {new_bal:,}c\n"
        f"Equip: /equip badge {badge_id}"
    )
    db.log_badge_market_action(
        "purchased", user.username, "", badge_id, row["emoji"], price, 0, "shop"
    )


# ---------------------------------------------------------------------------
# /equip badge <badge_id>
# ---------------------------------------------------------------------------

async def handle_equip_badge(bot: BaseBot, user: User, badge_id: str) -> None:
    badge_id = badge_id.lower().strip()
    row      = db.get_emoji_badge(badge_id)

    if row is None:
        await bot.highrise.send_whisper(user.id, f"Badge '{badge_id}' not found.")
        return

    db.ensure_user(user.id, user.username)

    if not db.owns_emoji_badge(user.username, badge_id):
        await bot.highrise.send_whisper(
            user.id, f"You don't own {row['emoji']} {badge_id}. Buy: /buy badge {badge_id}."
        )
        return

    if db.is_badge_listed(user.username, badge_id):
        await bot.highrise.send_whisper(
            user.id, "That badge is listed on the market. /badgecancel first."
        )
        return

    db.equip_item(user.id, badge_id, "badge", row["emoji"])
    display = db.get_display_name(user.id, user.username)
    await bot.highrise.send_whisper(user.id, f"✅ Equipped {row['emoji']}. You appear as: {display}"[:249])


# ---------------------------------------------------------------------------
# /unequip badge
# ---------------------------------------------------------------------------

async def handle_unequip_badge(bot: BaseBot, user: User) -> None:
    db.ensure_user(user.id, user.username)
    db.clear_equipped_badge(user.id)
    await bot.highrise.send_whisper(user.id, "✅ Badge unequipped.")


# ---------------------------------------------------------------------------
# /mybadges
# ---------------------------------------------------------------------------

async def handle_mybadges(bot: BaseBot, user: User) -> None:
    db.ensure_user(user.id, user.username)
    owned = db.get_user_emoji_badges(user.username)

    if not owned:
        await bot.highrise.send_whisper(
            user.id, "You have no emoji badges. Browse: /shop badges"
        )
        return

    equipped = db.get_equipped_ids(user.id)
    eq_id    = equipped.get("badge_id") or ""

    parts = []
    for b in owned:
        marker = " *" if b["badge_id"] == eq_id else ""
        listed = " [M]" if db.is_badge_listed(user.username, b["badge_id"]) else ""
        emoji  = b.get("emoji") or b["badge_id"]
        parts.append(f"{emoji}{marker}{listed}")

    preview = " ".join(parts)
    if len(preview) > 200:
        preview = " ".join(parts[:10]) + f" +{len(parts)-10} more"

    msg = f"Your badges ({len(owned)}): {preview}\n* = equipped  [M] = on market"
    await bot.highrise.send_whisper(user.id, msg[:249])


# ---------------------------------------------------------------------------
# /badges [username]  — public badge view (respects inventory privacy)
# ---------------------------------------------------------------------------

async def handle_badges_view(bot: BaseBot, user: User, args: list[str]) -> None:
    target = args[1].strip().lstrip("@") if len(args) > 1 else user.username
    db.ensure_user(user.id, user.username)

    if target.lower() != user.username.lower():
        priv = db.get_profile_privacy(target)
        if not priv.get("show_inventory", 1) and not _can_manage(user.username):
            await bot.highrise.send_whisper(user.id, f"@{target}'s inventory is hidden.")
            return

    owned = db.get_user_emoji_badges(target)
    if not owned:
        await bot.highrise.send_whisper(user.id, f"@{target} has no emoji badges.")
        return

    equipped_row = db.get_profile(None) if False else None
    emojis = [b.get("emoji") or b["badge_id"] for b in owned]
    preview = " ".join(emojis)
    if len(preview) > 180:
        preview = " ".join(emojis[:12]) + f" +{len(emojis)-12}"

    await bot.highrise.send_whisper(
        user.id, f"@{target}'s badges ({len(owned)}): {preview}"[:249]
    )


# ---------------------------------------------------------------------------
# /badgemarket [page]
# ---------------------------------------------------------------------------

async def handle_badgemarket(bot: BaseBot, user: User, args: list[str]) -> None:
    raw  = args[1] if len(args) > 1 else "1"
    page = int(raw) if raw.isdigit() else 1
    page = max(1, page)

    rows, total_pages = db.get_active_badge_listings(page=page, per_page=_MARKET_PAGE)

    if page > max(1, total_pages):
        page = max(1, total_pages)
        rows, total_pages = db.get_active_badge_listings(page=page, per_page=_MARKET_PAGE)

    if not rows:
        await bot.highrise.send_whisper(
            user.id, "🏷️ Badge Market — no active listings.\nSell: /badgelist <id> <price>"
        )
        return

    session_items = []
    lines = [f"🏷️ Market {page}/{total_pages}"]
    for num, r in enumerate(rows, 1):
        seller_short = r["seller_username"][:10]
        lines.append(f"{num} {r['emoji']} {r['badge_id']} {_short(r['price'])}c @{seller_short}")
        session_items.append({
            "num":        num,
            "item_id":    r["badge_id"],
            "listing_id": r["id"],
            "name":       r["badge_id"],
            "emoji":      r["emoji"],
            "price":      r["price"],
            "currency":   "coins",
            "seller":     r["seller_username"],
            "shop_type":  "market_badges",
        })

    nav = []
    if page > 1:
        nav.append("!badgemarket prev")
    if page < total_pages:
        nav.append("!badgemarket next")
    footer = "Buy: !badgebuy [#]" + (f"  {' | '.join(nav)}" if nav else "")
    lines.append(footer)

    msg = "\n".join(lines)
    if len(msg) > 249:
        lines = [f"🏷️ Market {page}/{total_pages}"]
        for item in session_items:
            lines.append(f"{item['num']} {item['emoji']} {_short(item['price'])}c @{item['seller'][:8]}")
        lines.append("Buy: /badgebuy <#>  More: /badgemarket next")
        msg = "\n".join(lines)[:249]

    db.save_shop_session(user.username, "market_badges", page, session_items)
    await bot.highrise.send_whisper(user.id, msg)


# ---------------------------------------------------------------------------
# /badgelist <badge_id> <price>
# ---------------------------------------------------------------------------

async def handle_badgelist(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 3:
        await bot.highrise.send_whisper(user.id, "Usage: !badgelist <badge_id> <price>")
        return

    badge_id  = args[1].lower().strip()
    price_raw = args[2].strip()

    if not price_raw.isdigit():
        await bot.highrise.send_whisper(user.id, "Price must be a positive number.")
        return

    price = int(price_raw)
    if price < _MIN_PRICE:
        await bot.highrise.send_whisper(user.id, f"Minimum listing price: {_MIN_PRICE}c.")
        return
    if price > _MAX_PRICE:
        await bot.highrise.send_whisper(user.id, f"Maximum listing price: {_short(_MAX_PRICE)}c.")
        return

    row = db.get_emoji_badge(badge_id)
    if row is None:
        await bot.highrise.send_whisper(user.id, f"Badge '{badge_id}' not found.")
        return

    db.ensure_user(user.id, user.username)

    if not db.owns_emoji_badge(user.username, badge_id):
        await bot.highrise.send_whisper(user.id, f"You don't own {row['emoji']} {badge_id}.")
        return

    if not row["tradeable"] or not row["sellable"]:
        await bot.highrise.send_whisper(user.id, f"❌ {row['emoji']} {badge_id} cannot be sold.")
        return

    # Check if badge is locked
    badges = db.get_user_emoji_badges(user.username)
    badge_row = next((b for b in badges if b["badge_id"] == badge_id), None)
    if badge_row and badge_row.get("locked"):
        await bot.highrise.send_whisper(user.id, "That badge is locked and cannot be listed.")
        return

    if db.is_badge_listed(user.username, badge_id):
        await bot.highrise.send_whisper(user.id, f"{row['emoji']} {badge_id} is already listed.")
        return

    # Check if currently equipped — unequip first
    equipped = db.get_equipped_ids(user.id)
    if equipped.get("badge_id") == badge_id:
        await bot.highrise.send_whisper(
            user.id, f"Unequip badge first: /unequip badge"
        )
        return

    listing_id = db.create_badge_listing(user.username, badge_id, row["emoji"], price)
    if listing_id < 0:
        await bot.highrise.send_whisper(user.id, "Failed to create listing. Try again!")
        return

    db.log_badge_market_action(
        "listed", user.username, "", badge_id, row["emoji"], price, 0, "active"
    )
    await bot.highrise.send_whisper(
        user.id, f"✅ Listed {row['emoji']} {badge_id} for {price:,}c. Listing #{listing_id}."
    )


# ---------------------------------------------------------------------------
# /badgebuy <listing_id>
# ---------------------------------------------------------------------------

async def handle_badgebuy(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2 or not args[1].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: !badgebuy <listing_id>")
        return

    listing_id = int(args[1])
    listing    = db.get_badge_listing(listing_id)

    if listing is None or listing["status"] != "active":
        await bot.highrise.send_whisper(user.id, "Listing not found or already sold.")
        return

    if listing["seller_username"].lower() == user.username.lower():
        await bot.highrise.send_whisper(user.id, "You cannot buy your own listing.")
        return

    db.ensure_user(user.id, user.username)

    fee_pct = _fee_pct()
    error   = db.buy_badge_listing(listing_id, user.username, fee_pct)
    if error:
        await bot.highrise.send_whisper(user.id, f"❌ {error}"[:249])
        return

    fee  = max(0, int(listing["price"] * fee_pct / 100))
    net  = listing["price"] - fee

    await bot.highrise.send_whisper(
        user.id,
        f"✅ Bought {listing['emoji']} {listing['badge_id']} for {listing['price']:,}c!\n"
        f"Equip: /equip badge {listing['badge_id']}"
    )

    # Notify seller if possible
    try:
        seller_note = (
            f"🏷️ Your {listing['emoji']} {listing['badge_id']} sold for "
            f"{listing['price']:,}c. Fee {fee:,}c. You got {net:,}c."
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
    if len(args) < 2 or not args[1].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: !badgecancel <listing_id>")
        return

    listing_id = int(args[1])
    is_staff   = _can_manage(user.username)
    error      = db.cancel_badge_listing(listing_id, user.username, is_staff)

    if error:
        await bot.highrise.send_whisper(user.id, f"❌ {error}"[:249])
        return

    listing = db.get_badge_listing(listing_id)
    emoji   = listing["emoji"] if listing else "?"
    db.log_badge_market_action(
        "cancelled", user.username, "", listing["badge_id"] if listing else "?",
        emoji, listing["price"] if listing else 0, 0, "cancelled"
    )
    await bot.highrise.send_whisper(user.id, "✅ Listing cancelled. Badge returned to inventory.")


# ---------------------------------------------------------------------------
# /mybadgelistings
# ---------------------------------------------------------------------------

async def handle_mybadgelistings(bot: BaseBot, user: User) -> None:
    db.ensure_user(user.id, user.username)
    rows = db.get_user_badge_listings(user.username)

    if not rows:
        await bot.highrise.send_whisper(
            user.id, "No active listings. List: /badgelist <id> <price>"
        )
        return

    lines = [f"Your listings ({len(rows)}):"]
    for r in rows:
        lines.append(f"#{r['id']} {r['emoji']} {r['badge_id']} {r['price']:,}c")

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
