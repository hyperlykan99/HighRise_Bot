"""
modules/bot_modes.py
--------------------
Bot Mode / Bot Outfit system for Highrise Hangout Room Bot.

Supports multiple logical bot personas (Host, Miner, Banker, DJ, etc.)
with message prefixes, outfit metadata, and multi-bot-ready design.

All messages ≤ 249 chars.
"""

import asyncio
from highrise import BaseBot, User

import database as db
from modules.permissions import is_owner, is_admin, is_manager

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_BOT_ID = "main"

DEFAULT_MODES: dict[str, dict] = {
    "host":       {"mode_name": "Lounge Host",    "prefix": "🎙️ Host",    "title": "Lounge Host",      "description": "Welcomes players and helps with room commands.",           "outfit_name": "Host Outfit"},
    "miner":      {"mode_name": "Miner",          "prefix": "⛏️ Miner",   "title": "Mining Guide",     "description": "Helps players with mining, ores, tools, and contracts.",    "outfit_name": "Miner Outfit"},
    "banker":     {"mode_name": "Banker",         "prefix": "🏦 Banker",  "title": "Bank Assistant",   "description": "Handles coins, bank, balances, and transfers.",             "outfit_name": "Banker Outfit"},
    "dj":         {"mode_name": "DJ",             "prefix": "🎧 DJ",      "title": "Lounge DJ",        "description": "Music, events, emotes, dancing, and announcements.",        "outfit_name": "DJ Outfit"},
    "dealer":     {"mode_name": "Casino Dealer",  "prefix": "🎰 Dealer",  "title": "Casino Dealer",    "description": "Handles BJ, RBJ, poker, and casino settings.",              "outfit_name": "Dealer Outfit"},
    "security":   {"mode_name": "Security",       "prefix": "🛡️ Security","title": "Lounge Security",  "description": "Handles moderation, reports, warnings, and safety.",        "outfit_name": "Security Outfit"},
    "shopkeeper": {"mode_name": "Shopkeeper",     "prefix": "🛒 Shop",    "title": "Shopkeeper",       "description": "Handles badge shop, title shop, VIP shop, and markets.",    "outfit_name": "Shopkeeper Outfit"},
    "eventhost":  {"mode_name": "Event Host",     "prefix": "🎉 Event",   "title": "Event Host",       "description": "Handles events, gold rain, room games, and announcements.", "outfit_name": "Event Host Outfit"},
}

# Category → mode_id mapping for format_bot_message
CATEGORY_MODE: dict[str, str] = {
    "mining":       "miner",
    "bank":         "banker",
    "economy":      "banker",
    "casino":       "dealer",
    "bj":           "dealer",
    "rbj":          "dealer",
    "poker":        "dealer",
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
# /botoutfit   /botoutfits
# ---------------------------------------------------------------------------

async def handle_botoutfit(bot: BaseBot, user: User) -> None:
    mode = get_bot_mode()
    if not mode:
        await _w(bot, user.id, "No bot mode configured.")
        return
    await _w(bot, user.id,
             f"🤖 Current outfit: {mode['outfit_name']} (Mode: {mode['mode_name']})")


async def handle_botoutfits(bot: BaseBot, user: User) -> None:
    modes = get_all_modes()
    parts = [f"{m['mode_id']}: {m['outfit_name']}" for m in modes]
    # split if long
    chunk = parts[:5]
    rest  = parts[5:]
    await _w(bot, user.id, ("🤖 Outfits: " + " | ".join(chunk))[:249])
    if rest:
        await _w(bot, user.id, (" | ".join(rest))[:249])


# ---------------------------------------------------------------------------
# /dressbot <mode_id>  |  /dressbot manual <outfit_name>
# ---------------------------------------------------------------------------

async def handle_dressbot(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /dressbot <mode_id>  or  /dressbot manual <outfit_name>")
        return

    sub = args[1].lower()
    if sub == "manual":
        outfit_name = " ".join(args[2:]) if len(args) > 2 else ""
        if not outfit_name:
            await _w(bot, user.id, "Usage: /dressbot manual <outfit_name>")
            return
        await _w(bot, user.id, "Mode changed. Outfit change not supported by API.")
        return

    mode_id = sub
    rec = get_mode_record(mode_id)
    if not rec:
        modes_list = ", ".join(m["mode_id"] for m in get_all_modes())
        await _w(bot, user.id, f"Unknown mode. Available: {modes_list}")
        return

    # Switch logical mode
    set_bot_mode(mode_id, assigned_by=user.username)

    # Try to apply outfit via API if outfit_data_json is set
    outfit_json = rec.get("outfit_data_json") or ""
    if outfit_json and outfit_json != "{}":
        try:
            import json
            outfit_data = json.loads(outfit_json)
            await bot.highrise.set_outfit(outfit_data)
            await _w(bot, user.id, f"✅ Dressed as {rec['prefix']}.")
        except Exception:
            await _w(bot, user.id,
                     f"Mode: {rec['prefix']}. Outfit data failed to apply.")
    else:
        await _w(bot, user.id,
                 f"Mode: {rec['prefix']}. Outfit change not supported by API.")


# ---------------------------------------------------------------------------
# /savebotoutfit <mode_id>
# ---------------------------------------------------------------------------

async def handle_savebotoutfit(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /savebotoutfit <mode_id>")
        return
    mode_id = args[1].lower()
    rec = get_mode_record(mode_id)
    if not rec:
        await _w(bot, user.id, f"Unknown mode: {mode_id}")
        return
    try:
        import json
        outfit = await bot.highrise.get_my_outfit()
        if outfit is None:
            raise ValueError("empty outfit")
        outfit_items = outfit.outfit if hasattr(outfit, "outfit") else outfit
        outfit_json  = json.dumps([item.__dict__ if hasattr(item, "__dict__") else str(item) for item in outfit_items])
        conn = db.get_connection()
        conn.execute("UPDATE bot_modes SET outfit_data_json=?, updated_at=datetime('now') WHERE mode_id=?",
                     (outfit_json, mode_id))
        conn.execute(
            """INSERT INTO bot_outfit_logs
               (timestamp, actor_username, bot_username, mode_id, outfit_name, action, details)
               VALUES (datetime('now'), ?, 'main', ?, ?, 'save_outfit', 'saved current outfit')""",
            (user.username, mode_id, rec["outfit_name"]),
        )
        conn.commit()
        conn.close()
        await _w(bot, user.id, f"✅ Outfit saved for {rec['mode_name']}.")
    except Exception:
        await _w(bot, user.id, "Saving current outfit is not supported by API.")


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
             "🤖 Bot Modes\n"
             "/botmode - current\n"
             "/botmodes - list\n"
             "/botmode <id> - switch\n"
             "/dressbot <id> - outfit\n"
             "/botprofile - info")
