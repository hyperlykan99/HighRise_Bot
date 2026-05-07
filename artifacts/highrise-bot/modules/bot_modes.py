"""
modules/bot_modes.py
--------------------
Bot Mode / Bot Outfit system for Highrise Hangout Room Bot.

Supports multiple logical bot personas (Host, Miner, Banker, DJ, etc.)
with message prefixes, outfit metadata, and multi-bot-ready design.

All messages ≤ 249 chars.
"""

import asyncio
import re
from contextvars import ContextVar
from highrise import BaseBot, User

import database as db
from modules.permissions import is_owner, is_admin, is_manager

# When True, outfit handlers raise on set_outfit failure so the delegated
# task runner can properly mark the task as failed.
_in_delegated_context: ContextVar[bool] = ContextVar("_in_delegated_context", default=False)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_BOT_ID = "main"

DEFAULT_MODES: dict[str, dict] = {
    "host":       {"mode_name": "Lounge Host",       "prefix": "🎙️ Host",       "title": "Lounge Host",        "description": "Welcomes players and helps with room commands.",              "outfit_name": "Host Outfit"},
    "miner":      {"mode_name": "Miner",             "prefix": "⛏️ Miner",      "title": "Mining Guide",       "description": "Helps players with mining, ores, tools, and contracts.",     "outfit_name": "Miner Outfit"},
    "banker":     {"mode_name": "Banker",            "prefix": "🏦 Banker",     "title": "Bank Assistant",     "description": "Handles coins, bank, balances, and transfers.",              "outfit_name": "Banker Outfit"},
    "dj":         {"mode_name": "DJ",                "prefix": "🎧 DJ",         "title": "Lounge DJ",          "description": "Music, events, emotes, dancing, and announcements.",         "outfit_name": "DJ Outfit"},
    "dealer":     {"mode_name": "Casino Dealer",     "prefix": "🎰 Dealer",     "title": "Casino Dealer",      "description": "Legacy casino fallback — handles BJ, RBJ, poker.",           "outfit_name": "Dealer Outfit"},
    "blackjack":  {"mode_name": "Blackjack Dealer",  "prefix": "🃏 Blackjack",  "title": "BJ/RBJ Dealer",      "description": "Handles Casual Blackjack and Realistic Blackjack.",          "outfit_name": "Blackjack Dealer Outfit"},
    "poker":      {"mode_name": "Poker Dealer",      "prefix": "♠️ Poker",      "title": "Poker Table Dealer", "description": "Handles poker, blinds, stacks, cards, and table state.",     "outfit_name": "Poker Dealer Outfit"},
    "security":   {"mode_name": "Security",          "prefix": "🛡️ Security",   "title": "Lounge Security",    "description": "Handles moderation, reports, warnings, and safety.",         "outfit_name": "Security Outfit"},
    "shopkeeper": {"mode_name": "Shopkeeper",        "prefix": "🛒 Shop",       "title": "Shopkeeper",         "description": "Handles badge shop, title shop, VIP shop, and markets.",     "outfit_name": "Shopkeeper Outfit"},
    "eventhost":  {"mode_name": "Event Host",        "prefix": "🎉 Event",      "title": "Event Host",         "description": "Handles events, gold rain, room games, and announcements.",  "outfit_name": "Event Host Outfit"},
}

# Category → mode_id mapping for format_bot_message
CATEGORY_MODE: dict[str, str] = {
    "mining":       "miner",
    "bank":         "banker",
    "economy":      "banker",
    "casino":       "host",
    "bj":           "blackjack",
    "rbj":          "blackjack",
    "poker":        "poker",
    "events":       "eventhost",
    "announcements":"eventhost",
    "moderation":   "security",
    "reports":      "security",
    "shop":         "shopkeeper",
    "badges":       "shopkeeper",
    "titles":       "shopkeeper",
    "vip":          "shopkeeper",
    "welcome":      "host",
    "help":         "host",
    "general":      "host",
    "emotes":       "dj",
    "dance":        "dj",
    "music":        "dj",
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _rs(key: str, default: str = "") -> str:
    return db.get_room_setting(key, default)


def _rset(key: str, value: str) -> None:
    db.set_room_setting(key, value)


def seed_bot_modes() -> None:
    conn = db.get_connection()
    for mode_id, m in DEFAULT_MODES.items():
        conn.execute(
            """INSERT OR IGNORE INTO bot_modes
               (mode_id, mode_name, prefix, title, description, outfit_name,
                outfit_data_json, enabled, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, '', 1, 'system', datetime('now'), datetime('now'))""",
            (mode_id, m["mode_name"], m["prefix"], m["title"], m["description"], m["outfit_name"]),
        )
    conn.commit()
    conn.close()


def get_mode_record(mode_id: str) -> dict | None:
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM bot_modes WHERE mode_id=?", (mode_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_modes(enabled_only: bool = True) -> list[dict]:
    conn = db.get_connection()
    q = "SELECT * FROM bot_modes"
    if enabled_only:
        q += " WHERE enabled=1"
    q += " ORDER BY mode_id"
    rows = conn.execute(q).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_bot_mode(bot_username: str = "") -> dict | None:
    bot_id = bot_username.lower() if bot_username else _DEFAULT_BOT_ID
    conn = db.get_connection()
    row = conn.execute(
        """SELECT bm.* FROM bot_mode_assignments bma
           JOIN bot_modes bm ON bma.mode_id=bm.mode_id
           WHERE bma.bot_id=? AND bma.active=1""",
        (bot_id,),
    ).fetchone()
    conn.close()
    if row:
        return dict(row)
    # fallback to host
    return get_mode_record("host")


def set_bot_mode(mode_id: str, bot_username: str = "", assigned_by: str = "system") -> bool:
    rec = get_mode_record(mode_id)
    if rec is None:
        return False
    bot_id = bot_username.lower() if bot_username else _DEFAULT_BOT_ID
    bname  = bot_username or "main"
    conn = db.get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO bot_mode_assignments
           (bot_id, bot_username, mode_id, active, assigned_by, assigned_at)
           VALUES (?, ?, ?, 1, ?, datetime('now'))""",
        (bot_id, bname, mode_id, assigned_by),
    )
    conn.execute(
        """INSERT INTO bot_outfit_logs
           (timestamp, actor_username, bot_username, mode_id, outfit_name, action, details)
           VALUES (datetime('now'), ?, ?, ?, ?, 'set_mode', 'mode switched')""",
        (assigned_by, bname, mode_id, rec["outfit_name"]),
    )
    conn.commit()
    conn.close()
    return True


def get_all_bot_assignments() -> list[dict]:
    conn = db.get_connection()
    rows = conn.execute(
        """SELECT bma.bot_id, bma.bot_username, bm.mode_name, bm.prefix
           FROM bot_mode_assignments bma
           JOIN bot_modes bm ON bma.mode_id=bm.mode_id
           WHERE bma.active=1""",
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def is_prefix_enabled() -> bool:
    return _rs("bot_prefix_enabled", "true") == "true"


def is_category_prefix_enabled() -> bool:
    return _rs("category_prefix_enabled", "true") == "true"


def format_bot_message(message: str, category: str | None = None) -> str:
    """Prepend mode prefix to message if enabled."""
    if not is_prefix_enabled():
        return message[:249]
    if category and is_category_prefix_enabled():
        mid = CATEGORY_MODE.get(category.lower())
        if mid:
            rec = get_mode_record(mid)
            if rec:
                prefix = rec["prefix"]
                return f"{prefix}: {message}"[:249]
    mode = get_bot_mode()
    if mode:
        return f"{mode['prefix']}: {message}"[:249]
    return message[:249]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _w(bot, uid, msg):
    return bot.highrise.send_whisper(uid, str(msg)[:249])


# ── Outfit serialisation helpers ─────────────────────────────────────────

def _item_to_dict(item) -> dict:
    return {
        "type":           getattr(item, "type",           "clothing"),
        "amount":         getattr(item, "amount",         1),
        "id":             item.id,
        "account_bound":  getattr(item, "account_bound",  False),
        "active_palette": getattr(item, "active_palette", None),
    }


def _dict_to_item(d: dict):
    from highrise import Item
    return Item(
        type          = d.get("type", "clothing"),
        amount        = int(d.get("amount", 1)),
        id            = str(d["id"]),
        account_bound = bool(d.get("account_bound", False)),
        active_palette= d.get("active_palette"),
    )


def _items_to_json(items) -> str:
    import json
    return json.dumps([_item_to_dict(i) for i in items])


def _json_to_items(s: str) -> list:
    import json
    return [_dict_to_item(d) for d in json.loads(s)]


def _outfit_item_count(outfit_data_json: str) -> int:
    if not outfit_data_json:
        return 0
    try:
        import json
        return len(json.loads(outfit_data_json))
    except Exception:
        return 0


async def _find_room_user_id(bot, username: str) -> str | None:
    """Return the user_id for a username if they are currently in the room."""
    try:
        resp = await bot.highrise.get_room_users()
        if hasattr(resp, "content"):
            for entry in resp.content:
                u = entry[0] if isinstance(entry, (tuple, list)) else entry
                if hasattr(u, "username") and u.username.lower() == username.lower():
                    return u.id
    except Exception:
        pass
    return None


def _can_switch(username: str) -> bool:
    if is_owner(username) or is_admin(username) or is_manager(username):
        return True
    return _rs("bot_mode_switch_allowed", "true") == "true"


# ---------------------------------------------------------------------------
# /botmode  /botmode <mode_id>
# ---------------------------------------------------------------------------

async def handle_botmode(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        mode = get_bot_mode()
        if mode:
            await _w(bot, user.id, f"🤖 Mode: {mode['prefix']} | {mode['mode_name']}")
        else:
            await _w(bot, user.id, "🤖 No mode set. Use /botmodes.")
        return

    mode_id = args[1].lower()
    if not _can_switch(user.username):
        await _w(bot, user.id, "Managers and above can switch bot mode.")
        return

    rec = get_mode_record(mode_id)
    if rec is None:
        modes = ", ".join(m["mode_id"] for m in get_all_modes())
        await _w(bot, user.id, f"Unknown mode. Available: {modes}")
        return

    set_bot_mode(mode_id, assigned_by=user.username)
    await _w(bot, user.id, f"✅ Bot mode set to {rec['prefix']}.")


# ---------------------------------------------------------------------------
# /botmodes
# ---------------------------------------------------------------------------

async def handle_botmodes(bot: BaseBot, user: User) -> None:
    modes = get_all_modes()
    ids = ", ".join(m["mode_id"] for m in modes)
    await _w(bot, user.id, f"Bot Modes: {ids}"[:249])


# ---------------------------------------------------------------------------
# /botprofile
# ---------------------------------------------------------------------------

async def handle_botprofile(bot: BaseBot, user: User) -> None:
    mode = get_bot_mode()
    if not mode:
        await _w(bot, user.id, "No bot mode configured.")
        return
    await _w(bot, user.id,
             f"🤖 {mode['prefix']} | {mode['mode_name']} | {mode['description']}"[:249])


# ---------------------------------------------------------------------------
# /botprefix on/off
# ---------------------------------------------------------------------------

async def handle_botprefix(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "on":
        _rset("bot_prefix_enabled", "true")
        await _w(bot, user.id, "✅ Bot prefixes ON.")
    elif sub == "off":
        _rset("bot_prefix_enabled", "false")
        await _w(bot, user.id, "⛔ Bot prefixes OFF.")
    else:
        state = "ON" if is_prefix_enabled() else "OFF"
        await _w(bot, user.id, f"Bot prefix: {state}. Use /botprefix on|off.")


# ---------------------------------------------------------------------------
# /categoryprefix on/off
# ---------------------------------------------------------------------------

async def handle_categoryprefix(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "on":
        _rset("category_prefix_enabled", "true")
        await _w(bot, user.id, "✅ Category prefixes ON.")
    elif sub == "off":
        _rset("category_prefix_enabled", "false")
        await _w(bot, user.id, "⛔ Category prefixes OFF.")
    else:
        state = "ON" if is_category_prefix_enabled() else "OFF"
        await _w(bot, user.id, f"Category prefix: {state}. Use /categoryprefix on|off.")


# ---------------------------------------------------------------------------
# /setbotprefix <mode_id> <prefix>
# ---------------------------------------------------------------------------

async def handle_setbotprefix(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /setbotprefix <mode_id> <prefix>")
        return
    mode_id = args[1].lower()
    prefix  = " ".join(args[2:])[:50]
    rec = get_mode_record(mode_id)
    if not rec:
        await _w(bot, user.id, f"Unknown mode: {mode_id}")
        return
    conn = db.get_connection()
    conn.execute("UPDATE bot_modes SET prefix=?, updated_at=datetime('now') WHERE mode_id=?",
                 (prefix, mode_id))
    conn.commit()
    conn.close()
    await _w(bot, user.id, f"✅ {rec['mode_name']} prefix updated to: {prefix}")


# ---------------------------------------------------------------------------
# /setbotdesc <mode_id> <description>
# ---------------------------------------------------------------------------

async def handle_setbotdesc(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /setbotdesc <mode_id> <description>")
        return
    mode_id = args[1].lower()
    desc    = " ".join(args[2:])[:200]
    rec = get_mode_record(mode_id)
    if not rec:
        await _w(bot, user.id, f"Unknown mode: {mode_id}")
        return
    conn = db.get_connection()
    conn.execute("UPDATE bot_modes SET description=?, updated_at=datetime('now') WHERE mode_id=?",
                 (desc, mode_id))
    conn.commit()
    conn.close()
    await _w(bot, user.id, f"✅ {rec['mode_name']} description updated.")


# ---------------------------------------------------------------------------
# /setbotoutfit <mode_id> <outfit_name>
# ---------------------------------------------------------------------------

async def handle_setbotoutfit(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /setbotoutfit <mode_id> <outfit_name>")
        return
    mode_id     = args[1].lower()
    outfit_name = " ".join(args[2:])[:100]
    rec = get_mode_record(mode_id)
    if not rec:
        await _w(bot, user.id, f"Unknown mode: {mode_id}")
        return
    conn = db.get_connection()
    conn.execute("UPDATE bot_modes SET outfit_name=?, updated_at=datetime('now') WHERE mode_id=?",
                 (outfit_name, mode_id))
    conn.commit()
    conn.close()
    await _w(bot, user.id, f"✅ {rec['mode_name']} outfit name saved: {outfit_name}")


# ---------------------------------------------------------------------------
# /botoutfit [mode_id]
# Public: no args → current mode details; with mode_id → that mode's details
# ---------------------------------------------------------------------------

async def handle_botoutfit(bot: BaseBot, user: User, args: list[str] = None) -> None:
    if args and len(args) >= 2:
        mode_id = args[1].lower()
        rec = get_mode_record(mode_id)
        if not rec:
            modes_list = ", ".join(m["mode_id"] for m in get_all_modes())
            await _w(bot, user.id, f"Unknown mode. Available: {modes_list}")
            return
        n = _outfit_item_count(rec.get("outfit_data_json") or "")
        has = f"YES ({n} items)" if n else "NO"
        upd = (rec.get("updated_at") or "")[:10]
        await _w(bot, user.id,
                 f"🎨 {rec['mode_id']} | \"{rec['outfit_name']}\" | Data: {has} | Updated: {upd}"[:249])
        return

    mode = get_bot_mode()
    if not mode:
        await _w(bot, user.id, "No bot mode configured.")
        return
    n = _outfit_item_count(mode.get("outfit_data_json") or "")
    has = f"YES ({n} items)" if n else "NO"
    upd = (mode.get("updated_at") or "")[:10]
    await _w(bot, user.id,
             f"🤖 Mode: {mode['mode_id']} | \"{mode['outfit_name']}\" | Data: {has} | Updated: {upd}"[:249])


# ---------------------------------------------------------------------------
# /botoutfits [page]
# Public: paginated list of all mode outfits, 5 per page
# ---------------------------------------------------------------------------

_OUTFITS_PAGE_SIZE = 5


async def handle_botoutfits(bot: BaseBot, user: User, args: list[str] = None) -> None:
    modes = get_all_modes(enabled_only=False)
    total = len(modes)
    pages = max(1, (total + _OUTFITS_PAGE_SIZE - 1) // _OUTFITS_PAGE_SIZE)

    page = 1
    if args and len(args) >= 2:
        try:
            page = int(args[1])
        except ValueError:
            page = 1
    page = max(1, min(page, pages))

    start = (page - 1) * _OUTFITS_PAGE_SIZE
    chunk = modes[start: start + _OUTFITS_PAGE_SIZE]

    await _w(bot, user.id,
             f"🎨 Outfits page {page}/{pages} — /botoutfits [page] for more")
    for i, m in enumerate(chunk, start=start + 1):
        n = _outfit_item_count(m.get("outfit_data_json") or "")
        data_str = f"{n} items" if n else "no data"
        upd = (m.get("updated_at") or "")[:10]
        await _w(bot, user.id,
                 f"  {i}. {m['mode_id']} | \"{m['outfit_name']}\" | {data_str} | {upd}"[:249])


# ---------------------------------------------------------------------------
# /dressbot <mode_id>
# Admin: apply saved outfit_data_json for that mode
# ---------------------------------------------------------------------------

async def handle_dressbot(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    USAGE = "Usage: /dressbot <mode_id> | /dressbot <bot_username> <mode_id>"
    if len(args) < 2:
        await _w(bot, user.id, USAGE)
        return

    delegated = _in_delegated_context.get()
    candidate = args[1].lower().lstrip("@")

    # Targeted form: /dressbot <bot_username> <mode_id>
    bot_cand_mode = db.get_bot_mode_for_username(candidate)
    if bot_cand_mode is not None and len(args) >= 3:
        from config import BOT_USERNAME
        target_bot = candidate
        mode_id    = args[2].lower()
        if target_bot != (BOT_USERNAME or "").lower():
            msg = f"This outfit change must be executed by @{target_bot}."
            if delegated:
                raise RuntimeError(msg)
            await _w(bot, user.id, msg[:249])
            return
        # Target is this bot — apply locally
    else:
        mode_id = candidate  # simple form: args[1] is the mode_id

    rec = get_mode_record(mode_id)
    if not rec:
        modes_list = ", ".join(m["mode_id"] for m in get_all_modes())
        msg = f"Unknown mode '{mode_id}'. Available: {modes_list}"
        if delegated:
            raise RuntimeError(msg)
        await _w(bot, user.id, msg[:249])
        return

    outfit_json = (rec.get("outfit_data_json") or "").strip()
    if not outfit_json or outfit_json in ("{}", "[]", ""):
        msg = (f"No saved outfit data for '{mode_id}'. "
               f"Dress manually, then use /savebotoutfit {mode_id}.")
        if delegated:
            raise RuntimeError(msg)
        await _w(bot, user.id, msg[:249])
        return

    try:
        items = _json_to_items(outfit_json)
        if not items:
            raise ValueError("empty item list")
        result = await bot.highrise.set_outfit(items)
        from highrise.models import Error
        if isinstance(result, Error):
            print(f"[OUTFIT] dressbot error for {mode_id}: {result}")
            raise RuntimeError(f"set_outfit failed: {str(result)[:80]}")
        set_bot_mode(mode_id, assigned_by=user.username)
        n = len(items)
        if not delegated:
            await _w(bot, user.id, f"✅ Dressed as {rec['prefix']} ({n} items applied).")
    except RuntimeError:
        raise
    except Exception as exc:
        print(f"[OUTFIT] dressbot exception for {mode_id}: {exc}")
        if delegated:
            raise RuntimeError(f"Outfit apply error: {str(exc)[:80]}") from exc
        await _w(bot, user.id, "Failed to apply outfit. Check /botoutfitlogs for details.")


# ---------------------------------------------------------------------------
# /savebotoutfit <mode_id> [name]
# Admin: save bot's current outfit; optional name renames outfit_name too
# ---------------------------------------------------------------------------

async def handle_savebotoutfit(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    USAGE = ("Usage: /savebotoutfit <mode_id> [name]"
             " | /savebotoutfit <bot_username> <mode_id> [name]")
    if len(args) < 2:
        await _w(bot, user.id, USAGE[:249])
        return

    delegated = _in_delegated_context.get()
    candidate = args[1].lower().lstrip("@")

    # Targeted form: /savebotoutfit <bot_username> <mode_id> [name]
    bot_cand_mode = db.get_bot_mode_for_username(candidate)
    if bot_cand_mode is not None:
        if len(args) < 3:
            await _w(bot, user.id, USAGE[:249])
            return
        from config import BOT_USERNAME
        target_bot = candidate
        mode_id    = args[2].lower()
        new_name   = " ".join(args[3:])[:100] if len(args) > 3 else ""
        if target_bot != (BOT_USERNAME or "").lower():
            msg = f"This save must be executed by @{target_bot}."
            if delegated:
                raise RuntimeError(msg)
            await _w(bot, user.id, msg[:249])
            return
        # Target is this bot — save locally
    else:
        # Old form: /savebotoutfit <mode_id> [name]
        mode_id  = candidate
        new_name = " ".join(args[2:])[:100] if len(args) > 2 else ""

    rec = get_mode_record(mode_id)
    if not rec:
        await _w(bot, user.id, f"Unknown mode: {mode_id}")
        return
    try:
        resp = await bot.highrise.get_my_outfit()
        from highrise.models import Error
        if isinstance(resp, Error):
            print(f"[OUTFIT] get_my_outfit error: {resp}")
            await _w(bot, user.id, "Could not fetch current outfit from the server.")
            return
        items = resp.outfit if hasattr(resp, "outfit") else []
        if not items:
            await _w(bot, user.id, "Bot is wearing no items — nothing to save.")
            return
        outfit_json  = _items_to_json(items)
        outfit_name  = new_name or rec["outfit_name"]
        conn = db.get_connection()
        conn.execute(
            "UPDATE bot_modes SET outfit_data_json=?, outfit_name=?, updated_at=datetime('now') WHERE mode_id=?",
            (outfit_json, outfit_name, mode_id),
        )
        conn.execute(
            """INSERT INTO bot_outfit_logs
               (timestamp, actor_username, bot_username, mode_id, outfit_name, action, details)
               VALUES (datetime('now'), ?, 'main', ?, ?, 'save_outfit', ?)""",
            (user.username, mode_id, outfit_name,
             f"saved {len(items)} items"),
        )
        conn.commit()
        conn.close()
        await _w(bot, user.id,
                 f"✅ Saved {len(items)} items → \"{outfit_name}\" ({mode_id}).")
    except Exception as exc:
        print(f"[OUTFIT] savebotoutfit exception for {mode_id}: {exc}")
        if delegated:
            raise RuntimeError(f"Save outfit error: {str(exc)[:80]}") from exc
        await _w(bot, user.id, "Failed to save outfit. Try again or check logs.")


# ---------------------------------------------------------------------------
# /copyoutfit <username> <mode_id>
# Admin: copy a room user's outfit into a bot mode's saved outfit_data_json
# ---------------------------------------------------------------------------

async def handle_copyoutfit(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /copyoutfit <username> <mode_id>")
        return

    target_name = args[1].lstrip("@")
    mode_id     = args[2].lower()

    rec = get_mode_record(mode_id)
    if not rec:
        await _w(bot, user.id, f"Unknown mode: {mode_id}")
        return

    await _w(bot, user.id, f"Looking for @{target_name} in the room…")
    target_id = await _find_room_user_id(bot, target_name)
    if not target_id:
        await _w(bot, user.id,
                 f"@{target_name} is not in the room. They must be present to copy their outfit.")
        return

    try:
        resp = await bot.highrise.get_user_outfit(target_id)
        from highrise.models import Error
        if isinstance(resp, Error):
            print(f"[OUTFIT] get_user_outfit error for {target_id}: {resp}")
            await _w(bot, user.id,
                     "Could not read that user's outfit from the server. They may need to be visible in the room.")
            return
        items = resp.outfit if hasattr(resp, "outfit") else []
        if not items:
            await _w(bot, user.id, f"@{target_name} appears to have no outfit items.")
            return
        outfit_json = _items_to_json(items)
        conn = db.get_connection()
        conn.execute(
            "UPDATE bot_modes SET outfit_data_json=?, updated_at=datetime('now') WHERE mode_id=?",
            (outfit_json, mode_id),
        )
        conn.execute(
            """INSERT INTO bot_outfit_logs
               (timestamp, actor_username, bot_username, mode_id, outfit_name, action, details)
               VALUES (datetime('now'), ?, 'main', ?, ?, 'copy_outfit', ?)""",
            (user.username, mode_id, rec["outfit_name"],
             f"copied {len(items)} items from @{target_name}"),
        )
        conn.commit()
        conn.close()
        await _w(bot, user.id,
                 f"✅ Copied @{target_name}'s outfit ({len(items)} items) → {mode_id}.")
    except Exception as exc:
        print(f"[OUTFIT] copyoutfit exception: {exc}")
        await _w(bot, user.id,
                 "Copying another user's outfit failed. They may need to be visible in the room.")


# ---------------------------------------------------------------------------
# /wearuseroutfit <username>
# Admin: make the bot immediately wear a room user's outfit
# ---------------------------------------------------------------------------

async def handle_wearuseroutfit(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    USAGE = ("Usage: /wearuseroutfit <source_username>"
             " | /wearuseroutfit <bot_username> <source_username>")
    if len(args) < 2:
        await _w(bot, user.id, USAGE[:249])
        return

    delegated = _in_delegated_context.get()
    candidate = args[1].lower().lstrip("@")

    # Targeted form: /wearuseroutfit <bot_username> <source_username>
    bot_cand_mode = db.get_bot_mode_for_username(candidate)
    if bot_cand_mode is not None and len(args) >= 3:
        from config import BOT_USERNAME
        target_bot  = candidate
        target_name = args[2].lstrip("@")
        if target_bot != (BOT_USERNAME or "").lower():
            msg = f"This outfit change must be executed by @{target_bot}."
            if delegated:
                raise RuntimeError(msg)
            await _w(bot, user.id, msg[:249])
            return
        # Target is this bot — apply locally
    else:
        target_name = args[1].lstrip("@")

    try:
        await _w(bot, user.id, f"Looking for @{target_name} in the room…")
    except Exception:
        pass
    target_id = await _find_room_user_id(bot, target_name)
    if not target_id:
        msg = f"@{target_name} is not in the room. They must be present for this command."
        if delegated:
            raise RuntimeError(msg)
        await _w(bot, user.id, msg)
        return

    try:
        resp = await bot.highrise.get_user_outfit(target_id)
        from highrise.models import Error
        if isinstance(resp, Error):
            print(f"[OUTFIT] get_user_outfit error for {target_id}: {resp}")
            raise RuntimeError("Could not read that user's outfit from the server.")
        items = resp.outfit if hasattr(resp, "outfit") else []
        if not items:
            raise RuntimeError(f"@{target_name} appears to have no outfit items.")
        result = await bot.highrise.set_outfit(items)
        if isinstance(result, Error):
            print(f"[OUTFIT] set_outfit error: {result}")
            raise RuntimeError(f"set_outfit failed: {str(result)[:80]}")
        try:
            conn = db.get_connection()
            conn.execute(
                """INSERT INTO bot_outfit_logs
                   (timestamp, actor_username, bot_username, mode_id, outfit_name, action, details)
                   VALUES (datetime('now'), ?, 'main', '', '', 'wear_user_outfit', ?)""",
                (user.username, f"wore @{target_name}'s outfit ({len(items)} items)"),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
        if not delegated:
            await _w(bot, user.id,
                     f"✅ Bot is now wearing @{target_name}'s outfit ({len(items)} items).")
    except RuntimeError:
        raise
    except Exception as exc:
        print(f"[OUTFIT] wearuseroutfit exception: {exc}")
        if delegated:
            raise RuntimeError(f"Outfit apply error: {str(exc)[:80]}") from exc
        await _w(bot, user.id,
                 "Could not apply that outfit. The user must be visible in the room.")


# ---------------------------------------------------------------------------
# /renamebotoutfit <mode_id> <name>
# Admin: rename the saved outfit_name for a mode (does not touch data)
# ---------------------------------------------------------------------------

async def handle_renamebotoutfit(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /renamebotoutfit <mode_id> <name>")
        return
    mode_id  = args[1].lower()
    new_name = " ".join(args[2:])[:100]
    rec = get_mode_record(mode_id)
    if not rec:
        await _w(bot, user.id, f"Unknown mode: {mode_id}")
        return
    conn = db.get_connection()
    conn.execute(
        "UPDATE bot_modes SET outfit_name=?, updated_at=datetime('now') WHERE mode_id=?",
        (new_name, mode_id),
    )
    conn.commit()
    conn.close()
    await _w(bot, user.id, f"✅ {mode_id} outfit renamed to \"{new_name}\".")


# ---------------------------------------------------------------------------
# /clearbotoutfit <mode_id>
# Admin: wipe outfit_data_json for a mode (keeps outfit_name and the mode itself)
# ---------------------------------------------------------------------------

async def handle_clearbotoutfit(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /clearbotoutfit <mode_id>")
        return
    mode_id = args[1].lower()
    rec = get_mode_record(mode_id)
    if not rec:
        await _w(bot, user.id, f"Unknown mode: {mode_id}")
        return
    n = _outfit_item_count(rec.get("outfit_data_json") or "")
    conn = db.get_connection()
    conn.execute(
        "UPDATE bot_modes SET outfit_data_json='', updated_at=datetime('now') WHERE mode_id=?",
        (mode_id,),
    )
    conn.execute(
        """INSERT INTO bot_outfit_logs
           (timestamp, actor_username, bot_username, mode_id, outfit_name, action, details)
           VALUES (datetime('now'), ?, 'main', ?, ?, 'clear_outfit', ?)""",
        (user.username, mode_id, rec["outfit_name"],
         f"cleared outfit data ({n} items removed)"),
    )
    conn.commit()
    conn.close()
    await _w(bot, user.id, f"✅ Outfit data cleared for {mode_id} (name kept).")


# ---------------------------------------------------------------------------
# /createbotmode <mode_id> <mode_name>
# ---------------------------------------------------------------------------

async def handle_createbotmode(bot: BaseBot, user: User, args: list[str]) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /createbotmode <mode_id> <mode_name>")
        return
    mode_id   = args[1].lower().strip()
    mode_name = " ".join(args[2:])[:60]
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT INTO bot_modes
               (mode_id, mode_name, prefix, title, description, outfit_name, enabled, created_by, created_at, updated_at)
               VALUES (?, ?, ?, '', '', '', 1, ?, datetime('now'), datetime('now'))""",
            (mode_id, mode_name, f"🤖 {mode_name}", user.username),
        )
        conn.commit()
        await _w(bot, user.id, f"✅ Mode '{mode_id}' created.")
    except Exception:
        await _w(bot, user.id, f"Mode '{mode_id}' already exists.")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# /deletebotmode <mode_id>
# ---------------------------------------------------------------------------

async def handle_deletebotmode(bot: BaseBot, user: User, args: list[str]) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /deletebotmode <mode_id>")
        return
    mode_id = args[1].lower()
    if mode_id in DEFAULT_MODES:
        await _w(bot, user.id, "Cannot delete a default bot mode.")
        return
    conn = db.get_connection()
    conn.execute("DELETE FROM bot_modes WHERE mode_id=?", (mode_id,))
    conn.commit()
    conn.close()
    await _w(bot, user.id, f"✅ Mode '{mode_id}' deleted.")


# ---------------------------------------------------------------------------
# /assignbotmode <bot_username> <mode_id>
# ---------------------------------------------------------------------------

async def handle_assignbotmode(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /assignbotmode <bot_username> <mode_id>")
        return
    bot_uname = args[1].lstrip("@")
    mode_id   = args[2].lower()
    rec = get_mode_record(mode_id)
    if not rec:
        await _w(bot, user.id, f"Unknown mode: {mode_id}")
        return
    conn = db.get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO bot_mode_assignments
           (bot_id, bot_username, mode_id, active, assigned_by, assigned_at)
           VALUES (lower(?), ?, ?, 1, ?, datetime('now'))""",
        (bot_uname, bot_uname, mode_id, user.username),
    )
    conn.commit()
    conn.close()
    await _w(bot, user.id, f"✅ {bot_uname} assigned to {rec['mode_name']} mode.")


# ---------------------------------------------------------------------------
# /bots
# ---------------------------------------------------------------------------

def get_current_mode_prefix() -> str:
    """Return the prefix string for the currently active bot mode assignment."""
    conn = db.get_connection()
    row = conn.execute(
        """SELECT bm.prefix FROM bot_mode_assignments bma
           JOIN bot_modes bm ON bma.mode_id = bm.mode_id
           WHERE bma.active = 1 LIMIT 1"""
    ).fetchone()
    conn.close()
    return row["prefix"] if row else ""


async def handle_bots(bot: BaseBot, user: User) -> None:
    assignments = get_all_bot_assignments()
    if not assignments:
        await _w(bot, user.id, "Only main bot registered.")
        return
    parts = [f"{a['bot_username']}={a['mode_name']}" for a in assignments]
    await _w(bot, user.id, ("🤖 Bots: " + " | ".join(parts))[:249])


# ---------------------------------------------------------------------------
# /botinfo <bot_username>
# ---------------------------------------------------------------------------

async def handle_botinfo(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /botinfo <bot_username>")
        return
    bname = args[1].lstrip("@")
    conn  = db.get_connection()
    row   = conn.execute(
        """SELECT bma.bot_username, bm.prefix, bm.mode_name, bm.description
           FROM bot_mode_assignments bma
           JOIN bot_modes bm ON bma.mode_id=bm.mode_id
           WHERE lower(bma.bot_id)=lower(?) AND bma.active=1""",
        (bname,),
    ).fetchone()
    conn.close()
    if not row:
        await _w(bot, user.id, f"No info for {bname}.")
        return
    await _w(bot, user.id,
             f"{row['bot_username']}: {row['prefix']} | {row['description']}"[:249])


# ---------------------------------------------------------------------------
# /botoutfitlogs
# ---------------------------------------------------------------------------

async def handle_botoutfitlogs(bot: BaseBot, user: User) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM bot_outfit_logs ORDER BY id DESC LIMIT 10"
    ).fetchall()
    conn.close()
    if not rows:
        await _w(bot, user.id, "No outfit logs yet.")
        return
    for r in rows[:5]:
        await _w(bot, user.id,
                 f"Log #{r['id']}: @{r['actor_username']} → {r['mode_id']} ({r['action']})"[:249])


# ---------------------------------------------------------------------------
# /botmodehelp
# ---------------------------------------------------------------------------

async def handle_botmodehelp(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id,
             "🤖 /botmode /botmodes /botprofile /bots "
             "| /botoutfit [mode] /botoutfits [page] "
             "| (admin) /dressbot /savebotoutfit /copyoutfit /wearuseroutfit "
             "/renamebotoutfit /clearbotoutfit "
             "| (per-bot) /copymyoutfit /copyoutfitfrom /savemyoutfit /wearoutfit "
             "/myoutfits /myoutfitstatus")


# ---------------------------------------------------------------------------
# Per-bot self-managing outfit commands (no cross-bot delegation)
# Each bot handles its own outfit when addressed directly.
# ---------------------------------------------------------------------------

async def handle_copymyoutfit(bot: BaseBot, user: User, args: list[str]) -> None:
    """/copymyoutfit — this bot copies the caller's current outfit onto itself."""
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    target_id = await _find_room_user_id(bot, user.username)
    if not target_id:
        await _w(bot, user.id, "You must be in the room for this command.")
        return
    try:
        resp = await bot.highrise.get_user_outfit(target_id)
        from highrise.models import Error
        if isinstance(resp, Error):
            await _w(bot, user.id,
                     "I can't copy your outfit with this SDK. "
                     "Dress me manually, then say 'save this outfit as <name>.'")
            return
        items = resp.outfit if hasattr(resp, "outfit") else []
        if not items:
            await _w(bot, user.id,
                     "I can't copy your outfit with this SDK. "
                     "Dress me manually, then say 'save this outfit as <name>.'")
            return
        from highrise.models import Error as _Err
        result = await bot.highrise.set_outfit(items)
        if isinstance(result, _Err):
            await _w(bot, user.id, f"Failed to apply outfit: {str(result)[:80]}")
            return
        await _w(bot, user.id, f"✅ Now wearing your outfit ({len(items)} items).")
    except Exception:
        await _w(bot, user.id,
                 "I can't copy user outfits with this SDK. "
                 "Dress me manually, then say 'save this outfit as <name>.'")


async def handle_copyoutfitfrom(bot: BaseBot, user: User, args: list[str]) -> None:
    """/copyoutfitfrom <username> — copy a room user's outfit onto this bot."""
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /copyoutfitfrom <username>")
        return
    source = args[1].lstrip("@")
    target_id = await _find_room_user_id(bot, source)
    if not target_id:
        await _w(bot, user.id,
                 f"@{source} is not in the room. They must be present for this command.")
        return
    try:
        resp = await bot.highrise.get_user_outfit(target_id)
        from highrise.models import Error
        if isinstance(resp, Error):
            await _w(bot, user.id,
                     "I can't copy that outfit with this SDK. "
                     "Dress me manually, then say 'save this outfit as <name>.'")
            return
        items = resp.outfit if hasattr(resp, "outfit") else []
        if not items:
            await _w(bot, user.id,
                     "I can't copy that outfit with this SDK. "
                     "Dress me manually, then say 'save this outfit as <name>.'")
            return
        from highrise.models import Error as _Err
        result = await bot.highrise.set_outfit(items)
        if isinstance(result, _Err):
            await _w(bot, user.id, f"Failed to apply outfit: {str(result)[:80]}")
            return
        await _w(bot, user.id, f"✅ Now wearing @{source}'s outfit ({len(items)} items).")
    except Exception:
        await _w(bot, user.id,
                 "I can't copy user outfits with this SDK. "
                 "Dress me manually, then say 'save this outfit as <name>.'")


async def handle_savemyoutfit(bot: BaseBot, user: User, args: list[str]) -> None:
    """/savemyoutfit <mode_id> — save this bot's current outfit under a mode name."""
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /savemyoutfit <mode_id>")
        return
    name = args[1].lower()
    rec  = get_mode_record(name)
    if not rec:
        modes = [m for m in get_all_modes()
                 if name in m.get("mode_id", "").lower()
                 or name in (m.get("outfit_name") or "").lower()]
        if len(modes) == 1:
            rec  = modes[0]
            name = rec["mode_id"]
    if not rec:
        await _w(bot, user.id,
                 f"Unknown mode '{name}'. Use /myoutfits to see available modes.")
        return
    try:
        resp = await bot.highrise.get_my_outfit()
        from highrise.models import Error
        if isinstance(resp, Error):
            await _w(bot, user.id, "Could not fetch current outfit from the server.")
            return
        items = resp.outfit if hasattr(resp, "outfit") else []
        if not items:
            await _w(bot, user.id, "I'm wearing no items — nothing to save.")
            return
        outfit_json = _items_to_json(items)
        conn = db.get_connection()
        conn.execute(
            "UPDATE bot_modes SET outfit_data_json=?, updated_at=datetime('now') "
            "WHERE mode_id=?",
            (outfit_json, name),
        )
        conn.execute(
            """INSERT INTO bot_outfit_logs
               (timestamp, actor_username, bot_username, mode_id, outfit_name, action, details)
               VALUES (datetime('now'), ?, 'main', ?, ?, 'save_outfit', ?)""",
            (user.username, name, rec.get("outfit_name", name), f"saved {len(items)} items"),
        )
        conn.commit()
        conn.close()
        await _w(bot, user.id, f"✅ Saved {len(items)} items as '{name}' outfit.")
    except Exception as exc:
        await _w(bot, user.id, f"Failed to save outfit: {str(exc)[:80]}")


async def handle_wearoutfit(bot: BaseBot, user: User, args: list[str]) -> None:
    """/wearoutfit <mode_id> — apply this bot's saved outfit for a mode."""
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /wearoutfit <mode_id>")
        return
    name = args[1].lower()
    rec  = get_mode_record(name)
    if not rec:
        await _w(bot, user.id,
                 f"No saved outfit named '{name}'. "
                 f"Dress me manually, then say 'save this outfit as {name}.'")
        return
    outfit_json = (rec.get("outfit_data_json") or "").strip()
    if not outfit_json or outfit_json in ("{}", "[]", ""):
        await _w(bot, user.id,
                 f"No outfit data saved for '{name}'. "
                 f"Dress me manually, then say 'save this outfit as {name}.'")
        return
    try:
        items = _json_to_items(outfit_json)
        if not items:
            raise ValueError("empty item list")
        from highrise.models import Error
        result = await bot.highrise.set_outfit(items)
        if isinstance(result, Error):
            await _w(bot, user.id, f"Failed to apply outfit: {str(result)[:80]}")
            return
        await _w(bot, user.id,
                 f"✅ Wearing '{rec.get('prefix', name)}' outfit ({len(items)} items).")
    except Exception as exc:
        await _w(bot, user.id, f"Failed to apply outfit: {str(exc)[:80]}")


async def handle_myoutfits(bot: BaseBot, user: User, args: list[str]) -> None:
    """/myoutfits — list saved outfit mode names for this bot."""
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    modes = get_all_modes()
    if not modes:
        await _w(bot, user.id, "No bot modes configured.")
        return
    parts = []
    for m in modes:
        mid      = m["mode_id"]
        has_data = bool((m.get("outfit_data_json") or "").strip())
        parts.append(f"{mid}:{'✓' if has_data else '—'}")
    await _w(bot, user.id, ("Outfits: " + " | ".join(parts))[:249])


async def handle_myoutfitstatus(bot: BaseBot, user: User, args: list[str]) -> None:
    """/myoutfitstatus — show outfit data status for all modes on this bot."""
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    from config import BOT_MODE as _CURRENT_MODE
    modes = get_all_modes()
    if not modes:
        await _w(bot, user.id, "No bot modes configured.")
        return
    saved   = [m["mode_id"] + "✓" for m in modes if (m.get("outfit_data_json") or "").strip()]
    no_data = [m["mode_id"] for m in modes if not (m.get("outfit_data_json") or "").strip()]
    msg = f"Mode={_CURRENT_MODE} | Saved: {', '.join(saved) or 'none'}"
    if no_data:
        msg += f" | Empty: {', '.join(no_data)}"
    await _w(bot, user.id, msg[:249])


async def handle_outfitredirect(bot: BaseBot, user: User, args: list[str]) -> None:
    """AI-only: redirect cross-bot outfit requests back to the target bot."""
    # args: ["outfitredirect", "<target_bot>", "<action_cmd>"]
    target = args[1] if len(args) > 1 else None
    action = args[2] if len(args) > 2 else "copymyoutfit"
    _phrases = {
        "copymyoutfit":   "copy my outfit",
        "copyoutfitfrom": "copy my outfit",
        "savemyoutfit":   "save this outfit",
        "wearoutfit":     "wear my outfit",
    }
    action_text = _phrases.get(action, "change my outfit")
    if target:
        disp = target.title()
        await _w(bot, user.id,
                 f"Please talk directly to @{target}: "
                 f"'{disp}, {action_text}.' "
                 f"Each bot manages its own outfit.")
    else:
        await _w(bot, user.id,
                 "Please talk directly to the target bot. "
                 "Each bot manages its own outfit.")


# ---------------------------------------------------------------------------
# Direct bot outfit chat listener
# ---------------------------------------------------------------------------
# Called early in on_chat for EVERY bot process (non-host modes).
# Detects "BotUsername, <outfit intent>" messages and handles them without
# going through EmceeBot's AI delegation path.
# ---------------------------------------------------------------------------

# Ordered intent patterns — checked in order, first match wins.
# Each entry: (compiled_pattern, command_str, arg_extractor_or_None)
_DIRECT_OUTFIT_INTENTS: list[tuple] = [
    # 1. "copy/wear/use my outfit" — must come BEFORE wear-<name>-outfit
    (re.compile(r"\b(?:copy|wear|use)\s+my\s+outfit\b", re.I),
     "copymyoutfit", None),

    # 2. "copy/wear @user's outfit"
    (re.compile(r"\b(?:copy|wear)\s+@?([A-Za-z]\w+)[''s]*\s+outfit\b", re.I),
     "copyoutfitfrom", lambda m: m.group(1).lstrip("@")),

    # 3. "save this/current/my outfit as <name>" / "remember this outfit as <name>"
    (re.compile(
        r"\b(?:save|remember)\s+(?:this|current|my)?\s*outfit\s+as\s+(\w+)\b",
        re.I),
     "savemyoutfit", lambda m: m.group(1).lower()),

    # 4. "wear <name> outfit" / "dress as <name>" / "use <name> outfit" /
    #    "switch to <name> outfit"
    (re.compile(
        r"\bwear\s+(?:the\s+)?(\w+)\s+outfit\b"
        r"|\bdress\s+(?:as|like)\s+(\w+)\b"
        r"|\buse\s+(?:the\s+)?(\w+)\s+outfit\b"
        r"|\bswitch\s+to\s+(?:the\s+)?(\w+)\s+(?:outfit|look)\b",
        re.I),
     "wearoutfit",
     lambda m: (m.group(1) or m.group(2) or m.group(3) or m.group(4)).lower()),

    # 5. "list outfits" / "outfit status" / "what outfit are you using"
    (re.compile(
        r"\b(?:list\s+outfits?|outfit\s+(?:status|list|info)"
        r"|what\s+outfit\s+(?:are\s+you|am\s+i)\b)",
        re.I),
     "myoutfitstatus", None),
]


def _is_this_bot_addressed(message: str) -> bool:
    """Return True if the message explicitly names this bot's username.

    Username resolution order (first non-empty wins):
    1. gold._bot_username — set from live room users at on_start
    2. config.BOT_USERNAME — from BOT_USERNAME env var (set by bot.py subprocess launcher)
    """
    from modules.gold import get_bot_username
    import config
    uname = (get_bot_username() or config.BOT_USERNAME or "").strip().lower()
    if not uname:
        return False
    low = message.lower().strip()
    # Match @username or bare word boundary
    if f"@{uname}" in low:
        return True
    if re.search(rf"\b{re.escape(uname)}\b", low):
        return True
    return False


async def handle_direct_bot_outfit_chat(bot, user, message: str) -> bool:
    """
    Detect and handle direct outfit commands for THIS bot.

    Run early in on_chat (before handle_ai_intercept) for non-host bots.
    Returns True if the message was handled (caller should return early).

    Host/eventhost/all bots skip this — they use the full AI path instead.
    """
    from config import BOT_MODE
    from modules.gold import get_bot_username
    import config as _cfg

    # Only run for non-host bot modes — host uses full AI path.
    if BOT_MODE in ("host", "eventhost", "all"):
        return False

    # Resolve the bot's actual runtime username (live room lookup > env var)
    uname   = (get_bot_username() or _cfg.BOT_USERNAME or "").strip().lower()
    matched = _is_this_bot_addressed(message)

    print(f"[DIRECT_OUTFIT] this_bot={uname} mode={BOT_MODE} msg={message!r}")
    print(f"[DIRECT_OUTFIT] target_detected={str(matched).lower()}")

    if not matched:
        return False

    # Strip the bot-name prefix and get the intent text
    text = re.sub(
        rf"^.*?@?{re.escape(uname)}[,\s:!]*",
        "", message.strip(), count=1, flags=re.I,
    ).strip() if uname else message.strip()

    # Find intent
    intent: str | None   = None
    arg_val: str | None  = None
    for pattern, cmd, extractor in _DIRECT_OUTFIT_INTENTS:
        m = pattern.search(text)
        if m:
            intent  = cmd
            arg_val = extractor(m) if extractor else None
            break

    print(f"[DIRECT_OUTFIT] intent={intent or 'none'}")

    if not intent:
        # Bot was addressed but no outfit intent — do NOT consume the message.
        # Let EmceeBot/AI handle it (e.g. "MC set @KeanuShield spawn here").
        return False

    # ── Intent confirmed — ack now ───────────────────────────────────────────
    display = uname.title() or "Bot"
    await bot.highrise.send_whisper(
        user.id, f"{display} heard you. Outfit system online.")
    print(f"[DIRECT_OUTFIT] ack sent to {user.username}")

    # Permission check
    if not (is_owner(user.username) or is_admin(user.username)):
        print(f"[DIRECT_OUTFIT] denied user={user.username}")
        await bot.highrise.send_whisper(user.id, "Owner/admin only.")
        return True

    # Dispatch to the relevant handler
    try:
        if intent == "copymyoutfit":
            await handle_copymyoutfit(bot, user, ["copymyoutfit"])
        elif intent == "copyoutfitfrom" and arg_val:
            await handle_copyoutfitfrom(bot, user, ["copyoutfitfrom", arg_val])
        elif intent == "savemyoutfit" and arg_val:
            await handle_savemyoutfit(bot, user, ["savemyoutfit", arg_val])
        elif intent == "wearoutfit" and arg_val:
            await handle_wearoutfit(bot, user, ["wearoutfit", arg_val])
        elif intent == "myoutfitstatus":
            await handle_myoutfitstatus(bot, user, ["myoutfitstatus"])
        else:
            await bot.highrise.send_whisper(
                user.id,
                f"What name? e.g. '{display}, {intent} security'")
        print(f"[DIRECT_OUTFIT] success intent={intent}")
    except Exception as exc:
        print(f"[DIRECT_OUTFIT] error intent={intent} exc={exc}")
        await bot.highrise.send_whisper(
            user.id, f"Outfit command failed: {str(exc)[:80]}")

    return True


# ---------------------------------------------------------------------------
# /directoutfittest <message>
# ---------------------------------------------------------------------------

async def handle_directoutfittest(bot, user, args: list[str]) -> None:
    """/directoutfittest <message> — show whether this bot would respond."""
    if not (is_owner(user.username) or is_admin(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /directoutfittest <message to test>")
        return

    from config import BOT_MODE
    from modules.gold import get_bot_username
    import config as _cfg
    msg     = " ".join(args[1:])
    uname   = (get_bot_username() or _cfg.BOT_USERNAME or "").strip().lower()
    matched = _is_this_bot_addressed(msg)
    skipped = BOT_MODE in ("host", "eventhost", "all")

    # Strip trigger and find intent
    text = re.sub(
        rf"^.*?@?{re.escape(uname)}[,\s:!]*",
        "", msg.strip(), count=1, flags=re.I,
    ).strip() if matched else msg

    intent: str | None = None
    for pattern, cmd, _ in _DIRECT_OUTFIT_INTENTS:
        if pattern.search(text):
            intent = cmd
            break

    allowed = is_owner(user.username) or is_admin(user.username)
    would_handle = matched and bool(intent) and not skipped

    line1 = (f"this_bot={uname} | bot_mode={BOT_MODE}"
             f" | uses_ai_path={str(skipped).lower()}")
    line2 = (f"target_detected={str(matched).lower()}"
             f" | intent={intent or 'none'}"
             f" | allowed={str(allowed).lower()}"
             f" | would_handle={str(would_handle).lower()}")
    await _w(bot, user.id, line1[:249])
    await _w(bot, user.id, line2[:249])
