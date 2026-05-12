"""
modules/room_utils.py
---------------------
Room Utility Core — teleport, spawns, emotes, social, hearts, follow,
alerts, welcome, intervals, repeat, player lists, extended moderation,
room settings, and logs.

All messages ≤ 249 chars.
"""

import asyncio
import random
import time
from datetime import datetime, timezone

from highrise import BaseBot, User
from highrise.models import Position

import database as db
from modules.permissions import (
    is_owner, is_admin, is_manager, is_moderator, can_moderate,
)

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

# user_id → Position (updated from on_user_move and on_user_join)
_user_positions: dict[str, Position] = {}

# user_id → unix timestamp of last position update
_user_position_times: dict[str, float] = {}

# Active emote loop tasks: username.lower() → asyncio.Task
_emote_loops: dict[str, asyncio.Task] = {}

# Follow task
_follow_task: asyncio.Task | None = None

# Repeat message task
_repeat_task: asyncio.Task | None = None
_repeat_status: dict = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _w(bot, uid, msg):
    return bot.highrise.send_whisper(uid, str(msg)[:249])


def _rs(key: str, default: str = "") -> str:
    return db.get_room_setting(key, default)


def _rset(key: str, value: str) -> None:
    db.set_room_setting(key, value)


def update_user_position(user_id: str, position: Position) -> None:
    """Called from on_user_move / on_user_join in main.py to keep position cache fresh."""
    _user_positions[user_id] = position
    _user_position_times[user_id] = time.time()


def _get_room_users_cached() -> dict[str, tuple[str, Position]]:
    """Returns {user_id: (username, position)} from module cache."""
    return {}  # positions stored from on_user_move events


def _fmt(v: int) -> str:
    return f"{v:,}" if v < 1_000_000 else f"{v/1_000_000:.1f}M"


async def _get_all_room_users(bot: BaseBot) -> list[tuple[User, Position]]:
    """Fetch current room users from Highrise API."""
    try:
        resp = await bot.highrise.get_room_users()
        content = resp.content if hasattr(resp, "content") else []
        return list(content)
    except Exception:
        return []


async def _resolve_user_in_room(bot: BaseBot, username: str) -> tuple[User, Position] | None:
    """Find a user by username in the current room."""
    users = await _get_all_room_users(bot)
    uname_lower = username.lower()
    for u, pos in users:
        if u.username.lower() == uname_lower:
            return (u, pos)
    return None


def _can_manage_room(username: str) -> bool:
    return is_owner(username) or is_admin(username) or is_manager(username)


def _can_mod(username: str) -> bool:
    return can_moderate(username)


# ---------------------------------------------------------------------------
# TELEPORT SYSTEM  (Part 8 & 9)
# ---------------------------------------------------------------------------

async def handle_tpme(bot: BaseBot, user: User, args: list[str]) -> None:
    if _rs("self_teleport_enabled", "false") != "true":
        await _w(bot, user.id, "Self teleport is OFF.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !tpme <spawn>  |  Spawns: /spawns")
        return
    await _teleport_to_spawn(bot, user, user.username, user.id, args[1])


async def handle_tp(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !tp <username> <spawn>")
        return
    target_name = args[1].lstrip("@")
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, user.id, f"@{target_name} not in room.")
        return
    target_user, _ = pair
    await _teleport_to_spawn(bot, user, target_user.username, target_user.id, args[2])


async def handle_tphere(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !tphere <username>")
        return
    target_name = args[1].lstrip("@")
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, user.id, f"@{target_name} not in room.")
        return
    target_user, _ = pair
    # Teleport target to user's position
    my_pos = _user_positions.get(user.id)
    if not my_pos:
        await _w(bot, user.id, "Cannot get your position. Move first then try again.")
        return
    try:
        await bot.highrise.teleport(target_user.id, my_pos)
        db.log_room_action(user.username, target_user.username, "tphere", "")
        await _w(bot, user.id, f"✅ Brought @{target_user.username} to you.")
    except Exception as e:
        await _w(bot, user.id, f"Teleport failed: {e!s}"[:249])


async def handle_goto(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !goto <username>")
        return
    target_name = args[1].lstrip("@")
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, user.id, f"@{target_name} not in room.")
        return
    target_user, target_pos = pair
    try:
        await bot.highrise.teleport(user.id, target_pos)
        db.log_room_action(user.username, target_user.username, "goto", "")
        await _w(bot, user.id, f"✅ Teleported you to @{target_user.username}.")
    except Exception as e:
        await _w(bot, user.id, f"Teleport failed: {e!s}"[:249])


async def handle_bring(bot: BaseBot, user: User, args: list[str]) -> None:
    await handle_tphere(bot, user, args)


async def handle_bringall(bot: BaseBot, user: User) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if _rs("group_teleport_enabled", "true") != "true":
        await _w(bot, user.id, "Group teleport is OFF.")
        return
    my_pos = _user_positions.get(user.id)
    if not my_pos:
        await _w(bot, user.id, "Cannot get your position. Move first then try again.")
        return
    users = await _get_all_room_users(bot)
    count = 0
    for u, _ in users:
        if u.id == user.id:
            continue
        try:
            await bot.highrise.teleport(u.id, my_pos)
            count += 1
        except Exception:
            pass
    db.log_room_action(user.username, "all", "bringall", f"count={count}")
    await _w(bot, user.id, f"✅ Brought {count} players to you.")


async def handle_tpall(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !tpall <spawn>")
        return
    spawn_name = args[1].lower()
    spawn = db.get_spawn(spawn_name)
    if not spawn:
        await _w(bot, user.id, f"Spawn '{spawn_name}' not found. Use !spawns.")
        return
    if _rs("group_teleport_enabled", "true") != "true":
        await _w(bot, user.id, "Group teleport is OFF.")
        return
    users = await _get_all_room_users(bot)
    pos   = Position(spawn["x"], spawn["y"], spawn["z"], spawn["facing"])
    count = 0
    for u, _ in users:
        try:
            await bot.highrise.teleport(u.id, pos)
            count += 1
        except Exception:
            pass
    db.log_room_action(user.username, "all", "tpall", f"spawn={spawn_name} count={count}")
    await _w(bot, user.id, f"✅ Sent {count} players to {spawn_name}.")


async def _teleport_to_spawn(bot: BaseBot, actor: User, target_name: str,
                              target_id: str, spawn_name: str) -> None:
    spawn = db.get_spawn(spawn_name.lower())
    if not spawn:
        await _w(bot, actor.id, f"Spawn '{spawn_name}' not found. Use !spawns.")
        return
    pos = Position(spawn["x"], spawn["y"], spawn["z"], spawn["facing"])
    try:
        await bot.highrise.teleport(target_id, pos)
        db.log_room_action(actor.username, target_name, "teleport", f"spawn={spawn_name}")
        await _w(bot, actor.id, f"✅ Teleported @{target_name} to {spawn_name}.")
    except Exception as e:
        await _w(bot, actor.id, f"Teleport failed: {e!s}"[:249])


async def handle_selftp(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "on":
        _rset("self_teleport_enabled", "true")
        await _w(bot, user.id, "✅ Self-teleport ON.")
    elif sub == "off":
        _rset("self_teleport_enabled", "false")
        await _w(bot, user.id, "⛔ Self-teleport OFF.")
    else:
        state = _rs("self_teleport_enabled", "false")
        await _w(bot, user.id, f"Self-teleport: {'ON' if state == 'true' else 'OFF'}. Use !selftp on|off.")


async def handle_groupteleport(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "on":
        _rset("group_teleport_enabled", "true")
        await _w(bot, user.id, "✅ Group teleport ON.")
    elif sub == "off":
        _rset("group_teleport_enabled", "false")
        await _w(bot, user.id, "⛔ Group teleport OFF.")
    else:
        state = _rs("group_teleport_enabled", "true")
        await _w(bot, user.id, f"Group teleport: {'ON' if state == 'true' else 'OFF'}. Use !groupteleport on|off.")


async def handle_tprole(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !tprole <role> <spawn>")
        return
    role_name  = args[1].lower()
    spawn_name = args[2].lower()
    spawn = db.get_spawn(spawn_name)
    if not spawn:
        await _w(bot, user.id, f"Spawn '{spawn_name}' not found.")
        return
    pos   = Position(spawn["x"], spawn["y"], spawn["z"], spawn["facing"])
    users = await _get_all_room_users(bot)
    count = 0
    for u, _ in users:
        matches = False
        if role_name == "vip" and db.owns_item(u.id, "vip"):
            matches = True
        elif role_name in {"owner", "admin", "manager", "mod", "moderator", "staff"}:
            from modules.permissions import is_owner as _io, is_admin as _ia, is_manager as _im, is_moderator as _imod
            if role_name == "owner" and _io(u.username):
                matches = True
            elif role_name == "admin" and _ia(u.username):
                matches = True
            elif role_name in {"manager"} and _im(u.username):
                matches = True
            elif role_name in {"mod", "moderator"} and _imod(u.username):
                matches = True
            elif role_name == "staff" and can_moderate(u.username):
                matches = True
        if matches:
            try:
                await bot.highrise.teleport(u.id, pos)
                count += 1
            except Exception:
                pass
    await _w(bot, user.id, f"✅ Sent {count} {role_name} players to {spawn_name}.")


async def handle_tpvip(bot: BaseBot, user: User, args: list[str]) -> None:
    args_new = [args[0], "vip"] + (args[1:] if len(args) > 1 else [])
    await handle_tprole(bot, user, args_new)


async def handle_tpstaff(bot: BaseBot, user: User, args: list[str]) -> None:
    args_new = [args[0], "staff"] + (args[1:] if len(args) > 1 else [])
    await handle_tprole(bot, user, args_new)


# ---------------------------------------------------------------------------
# SPAWN SYSTEM  (Part 9)
# ---------------------------------------------------------------------------

async def handle_spawns(bot: BaseBot, user: User) -> None:
    spawns = db.get_all_spawns()
    if not spawns:
        await _w(bot, user.id, "📍 No spawns saved. Use !setspawn <name>.")
        return
    names = ", ".join(s["spawn_name"] for s in spawns)
    await _w(bot, user.id, f"📍 Spawns: {names}"[:249])


async def handle_spawn(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        default = _rs("default_spawn", "lounge")
        await _w(bot, user.id, f"Default spawn: {default}. Use !spawn <name> or /spawns.")
        return
    if _rs("self_teleport_enabled", "false") == "true":
        await _teleport_to_spawn(bot, user, user.username, user.id, args[1])
    else:
        await _w(bot, user.id, "Self teleport is OFF. Ask staff for a teleport.")


async def handle_setspawn(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !setspawn <name>  (saves your current position)")
        return
    name   = args[1].lower()
    my_pos = _user_positions.get(user.id)
    if not my_pos:
        await _w(bot, user.id,
                 "Cannot read your position. Use !setspawncoords <name> <x> <y> <z>.")
        return
    db.save_spawn(name, my_pos.x, my_pos.y, my_pos.z,
                  getattr(my_pos, "facing", "FrontLeft"), user.username)
    db.log_room_action(user.username, "", "setspawn", f"name={name}")
    await _w(bot, user.id, f"✅ Spawn '{name}' saved at ({my_pos.x:.1f},{my_pos.y:.1f},{my_pos.z:.1f}).")


async def handle_delspawn(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !delspawn <name>")
        return
    name = args[1].lower()
    db.delete_spawn(name)
    await _w(bot, user.id, f"✅ Spawn '{name}' deleted.")


async def handle_spawninfo(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !spawninfo <name>")
        return
    sp = db.get_spawn(args[1])
    if not sp:
        await _w(bot, user.id, f"Spawn '{args[1]}' not found.")
        return
    await _w(bot, user.id,
             f"📍 {sp['spawn_name']}: ({sp['x']:.1f},{sp['y']:.1f},{sp['z']:.1f}) "
             f"facing={sp['facing']} by @{sp['created_by']}"[:249])


async def handle_setspawncoords(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 5:
        await _w(bot, user.id, "Usage: !setspawncoords <name> <x> <y> <z>")
        return
    name = args[1].lower()
    try:
        x, y, z = float(args[2]), float(args[3]), float(args[4])
    except ValueError:
        await _w(bot, user.id, "x y z must be numbers.")
        return
    facing = args[5] if len(args) > 5 else "FrontLeft"
    db.save_spawn(name, x, y, z, facing, user.username)
    await _w(bot, user.id, f"✅ Spawn '{name}' set to ({x:.1f},{y:.1f},{z:.1f}).")


async def handle_savepos(bot: BaseBot, user: User, args: list[str]) -> None:
    await handle_setspawn(bot, user, args)


# ---------------------------------------------------------------------------
# EMOTE SYSTEM  (Part 12)
# ---------------------------------------------------------------------------

EMOTE_LIST = [
    # Basic / always available
    "emote-wave", "emote-greet", "emote-hello",
    "emote-dance", "emote-dance2", "emote-dance3", "emote-dance4",
    "emote-sit", "emote-sit2", "emote-clap",
    "emote-point", "emote-laugh", "emote-salute", "emote-blowkiss",
    "emote-idle_loop", "emote-idle_look", "emote-idle_enthusiastic",
    # Reactions
    "emote-yes", "emote-no", "emote-thumbsup", "emote-thumbsdown",
    "emote-shrug", "emote-sorry", "emote-angry", "emote-sad",
    "emote-cry", "emote-celebrate", "emote-surprise",
    # Social
    "emote-hug", "emote-kiss", "emote-highfive", "emote-handshake",
    "emote-bow", "emote-curtsy", "emote-curtsey",
    "emote-flex", "emote-pose", "emote-pose2",
    # Fun / party
    "emote-snowball", "emote-snowangel",
    "emote-telekinesis", "emote-float", "emote-levitate",
    "emote-headbang", "emote-headbang2",
    "emote-sleep", "emote-sniff",
    # Seasonal / events
    "emote-zombie", "emote-witch", "emote-ghost",
    "emote-fly", "emote-spin",
    "emote-magic", "emote-magic2",
]

# Friendly descriptions for /emoteinfo
_EMOTE_DESCRIPTIONS: dict[str, str] = {
    "emote-wave":             "👋 A friendly wave.",
    "emote-greet":            "🙋 Greeting gesture.",
    "emote-hello":            "👋 Enthusiastic hello.",
    "emote-dance":            "💃 Classic dance move.",
    "emote-dance2":           "🕺 Alternate dance style.",
    "emote-dance3":           "💃 Party dance.",
    "emote-dance4":           "🎉 Celebration dance.",
    "emote-sit":              "🪑 Sit on the floor.",
    "emote-sit2":             "🪑 Cross-legged sit.",
    "emote-clap":             "👏 Applause.",
    "emote-point":            "👉 Point forward.",
    "emote-laugh":            "😄 Laugh out loud.",
    "emote-salute":           "💂 Military salute.",
    "emote-blowkiss":         "😘 Blow a kiss.",
    "emote-idle_loop":        "😐 Idle standing (resets emote).",
    "emote-idle_look":        "👀 Looking around casually.",
    "emote-idle_enthusiastic":"🤩 Excited idle stance.",
    "emote-yes":              "✅ Nodding yes.",
    "emote-no":               "❌ Shaking head no.",
    "emote-thumbsup":         "👍 Thumbs up.",
    "emote-thumbsdown":       "👎 Thumbs down.",
    "emote-shrug":            "🤷 Shrug.",
    "emote-sorry":            "🙏 Apologetic bow.",
    "emote-angry":            "😡 Angry stomp.",
    "emote-sad":              "😢 Sad slump.",
    "emote-cry":              "😭 Crying.",
    "emote-celebrate":        "🎊 Celebration jump.",
    "emote-surprise":         "😲 Surprised reaction.",
    "emote-hug":              "🤗 Hugging motion.",
    "emote-kiss":             "💋 Kissing motion.",
    "emote-highfive":         "🙌 High five.",
    "emote-handshake":        "🤝 Handshake.",
    "emote-bow":              "🙇 Respectful bow.",
    "emote-curtsy":           "👸 Curtsy.",
    "emote-curtsey":          "👸 Curtsey variant.",
    "emote-flex":             "💪 Bicep flex.",
    "emote-pose":             "📸 Cool pose.",
    "emote-pose2":            "📸 Alternate pose.",
    "emote-snowball":         "❄️ Throw a snowball.",
    "emote-snowangel":        "⛄ Make a snow angel.",
    "emote-telekinesis":      "🔮 Telekinesis gesture.",
    "emote-float":            "✨ Floating animation.",
    "emote-levitate":         "🌟 Levitate.",
    "emote-headbang":         "🤘 Headbang.",
    "emote-headbang2":        "🤘 Alternate headbang.",
    "emote-sleep":            "😴 Sleeping.",
    "emote-sniff":            "👃 Sniffing.",
    "emote-zombie":           "🧟 Zombie walk.",
    "emote-witch":            "🧙 Witch casting.",
    "emote-ghost":            "👻 Ghost float.",
    "emote-fly":              "🕊️ Flying animation.",
    "emote-spin":             "🌀 Spinning.",
    "emote-magic":            "✨ Magic cast.",
    "emote-magic2":           "✨ Alternate magic cast.",
}

_EMOTE_PAGE_SIZE = 10


async def handle_emotes(bot: BaseBot, user: User, args: list[str] | None = None) -> None:
    """List emotes with pagination: /emotes [page]."""
    page_num = 1
    if args and len(args) >= 2 and args[1].isdigit():
        page_num = max(1, int(args[1]))
    short  = [e.replace("emote-", "") for e in EMOTE_LIST]
    total  = len(short)
    pages  = max(1, (total + _EMOTE_PAGE_SIZE - 1) // _EMOTE_PAGE_SIZE)
    page_num = min(page_num, pages)
    start  = (page_num - 1) * _EMOTE_PAGE_SIZE
    chunk  = short[start:start + _EMOTE_PAGE_SIZE]
    await _w(bot, user.id,
             f"💃 Emotes ({page_num}/{pages}): " + ", ".join(chunk) +
             (f" | /emotes {page_num+1} for more" if page_num < pages else ""))


async def handle_emote(bot: BaseBot, user: User, args: list[str]) -> None:
    if _rs("public_emotes_enabled", "true") != "true":
        await _w(bot, user.id, "Public emotes are OFF.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !emote <id>  |  List: /emotes")
        return
    eid = args[1]
    if not eid.startswith("emote-"):
        eid = "emote-" + eid
    try:
        await bot.highrise.send_emote(eid, user.id)
        await _w(bot, user.id, "💃 Emote started.")
    except Exception:
        await _w(bot, user.id, "Emote control not supported by current API.")


async def handle_stopemote(bot: BaseBot, user: User) -> None:
    try:
        await bot.highrise.send_emote("emote-idle_loop", user.id)
        await _w(bot, user.id, "Emote stopped.")
    except Exception:
        await _w(bot, user.id, "Emote control not supported by current API.")


async def handle_dance(bot: BaseBot, user: User) -> None:
    await handle_emote(bot, user, ["/emote", "dance"])


async def handle_wave(bot: BaseBot, user: User) -> None:
    await handle_emote(bot, user, ["/emote", "wave"])


async def handle_sit(bot: BaseBot, user: User) -> None:
    await handle_emote(bot, user, ["/emote", "sit"])


async def handle_clap(bot: BaseBot, user: User) -> None:
    await handle_emote(bot, user, ["/emote", "clap"])


async def handle_forceemote(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if _rs("force_emotes_enabled", "true") != "true":
        await _w(bot, user.id, "Force emotes are OFF.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !forceemote <username> <emote_id>")
        return
    target_name = args[1].lstrip("@")
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, user.id, f"@{target_name} not in room.")
        return
    target_user, _ = pair
    eid = args[2]
    if not eid.startswith("emote-"):
        eid = "emote-" + eid
    try:
        await bot.highrise.send_emote(eid, target_user.id)
        db.log_room_action(user.username, target_user.username, "forceemote", eid)
        await _w(bot, user.id, f"✅ Emote sent to @{target_user.username}.")
    except Exception:
        await _w(bot, user.id, "Emote control not supported by current API.")


async def handle_forceemoteall(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !forceemoteall <emote_id>")
        return
    eid = args[1]
    if not eid.startswith("emote-"):
        eid = "emote-" + eid
    users = await _get_all_room_users(bot)
    count = 0
    for u, _ in users:
        try:
            await bot.highrise.send_emote(eid, u.id)
            count += 1
        except Exception:
            pass
    await _w(bot, user.id, f"✅ Emote sent to {count} players.")


async def handle_loopemote(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if _rs("loop_emotes_enabled", "true") != "true":
        await _w(bot, user.id, "Emote loops are OFF.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !loopemote <emote_id>  or  /loopemote <user> <emote_id>")
        return
    # /loopemote <emote_id>  or  /loopemote <username> <emote_id>
    if len(args) >= 3:
        target_name = args[1].lstrip("@")
        eid = args[2]
        pair = await _resolve_user_in_room(bot, target_name)
        if not pair:
            await _w(bot, user.id, f"@{target_name} not in room.")
            return
        target_user, _ = pair
        target_id = target_user.id
        loop_key  = target_user.username.lower()
    else:
        eid       = args[1]
        target_id = user.id
        loop_key  = user.username.lower()

    if not eid.startswith("emote-"):
        eid = "emote-" + eid

    interval = int(_rs("emote_loop_interval_seconds", "8"))

    # Cancel existing loop
    if loop_key in _emote_loops and not _emote_loops[loop_key].done():
        _emote_loops[loop_key].cancel()

    async def _loop():
        while True:
            try:
                await bot.highrise.send_emote(eid, target_id)
            except Exception:
                pass
            await asyncio.sleep(interval)

    _emote_loops[loop_key] = asyncio.create_task(_loop())
    db.log_room_action(user.username, loop_key, "loopemote", eid)
    await _w(bot, user.id, f"🔁 Looping {eid}. Stop: /stoploop.")


async def handle_stoploop(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) >= 2:
        loop_key = args[1].lstrip("@").lower()
    else:
        loop_key = user.username.lower()

    task = _emote_loops.get(loop_key)
    if task and not task.done():
        task.cancel()
        del _emote_loops[loop_key]
        await _w(bot, user.id, f"✅ Loop stopped for {loop_key}.")
    else:
        await _w(bot, user.id, f"No active loop for {loop_key}.")


async def handle_stopallloops(bot: BaseBot, user: User) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    count = 0
    for key, task in list(_emote_loops.items()):
        if not task.done():
            task.cancel()
            count += 1
    _emote_loops.clear()
    await _w(bot, user.id, f"✅ Stopped {count} emote loops.")


async def handle_synchost(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if _rs("sync_dance_enabled", "true") != "true":
        await _w(bot, user.id, "Sync dance is OFF.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !synchost <username>")
        return
    target_name = args[1].lstrip("@")
    _rset("sync_host_username", target_name)
    db.log_room_action(user.username, target_name, "synchost", "")
    await _w(bot, user.id, f"✅ Sync host set to @{target_name}.")


async def handle_syncdance(bot: BaseBot, user: User, args: list[str]) -> None:
    await handle_synchost(bot, user, args)


async def handle_stopsync(bot: BaseBot, user: User) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    _rset("sync_host_username", "")
    await _w(bot, user.id, "✅ Sync dance stopped.")


async def handle_publicemotes(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "on":
        _rset("public_emotes_enabled", "true")
        await _w(bot, user.id, "✅ Public emotes ON.")
    elif sub == "off":
        _rset("public_emotes_enabled", "false")
        await _w(bot, user.id, "⛔ Public emotes OFF.")
    else:
        await _w(bot, user.id, "Usage: !publicemotes on|off")


async def handle_forceemotes(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub == "on":
        _rset("force_emotes_enabled", "true")
        await _w(bot, user.id, "✅ Force emotes ON.")
    elif sub == "off":
        _rset("force_emotes_enabled", "false")
        await _w(bot, user.id, "⛔ Force emotes OFF.")
    else:
        await _w(bot, user.id, "Usage: !forceemotes on|off")


async def handle_setemoteloopinterval(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !setemoteloopinterval <seconds>")
        return
    sec = max(3, int(args[1]))
    _rset("emote_loop_interval_seconds", str(sec))
    await _w(bot, user.id, f"✅ Emote loop interval set to {sec}s.")


# ---------------------------------------------------------------------------
# HEART REACTIONS  (Part 13)
# ---------------------------------------------------------------------------

async def handle_heart(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        totals = db.get_heart_totals(user.username)
        await _w(bot, user.id,
                 f"💖 @{user.username}: {totals['hearts_received']} hearts received.")
        return
    target_name = args[1].lstrip("@")
    if target_name.lower() == user.username.lower():
        await _w(bot, user.id, "You can't heart yourself!")
        return
    wait = db.get_heart_cooldown_remaining(user.username, target_name)
    if wait > 0:
        await _w(bot, user.id, f"⏳ Heart cooldown: {int(wait)}s remaining.")
        return
    # Find target in room
    pair = await _resolve_user_in_room(bot, target_name)
    if pair:
        target_user, _ = pair
        try:
            await bot.highrise.react("heart", target_user.id)
        except Exception:
            pass
    result      = db.give_heart(user.username, target_name)
    sender_disp = db.get_display_name(user.id, user.username)
    target_disp = db.get_display_name_by_username(target_name)
    await bot.highrise.chat(
        f"💖 {sender_disp} sent a heart to {target_disp}. "
        f"{target_disp} has {result['total']} hearts."[:249]
    )


async def handle_hearts(bot: BaseBot, user: User, args: list[str]) -> None:
    target = args[1].lstrip("@") if len(args) > 1 else user.username
    totals = db.get_heart_totals(target)
    await _w(bot, user.id,
             f"💖 @{target}: {totals['hearts_received']} hearts received, "
             f"{totals['hearts_given']} given.")


async def handle_heartlb(bot: BaseBot, user: User) -> None:
    rows = db.get_heart_leaderboard()
    if not rows:
        await _w(bot, user.id, "💖 No hearts yet!")
        return
    lines = ["💖 Heart Leaderboard"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. @{r['username']}: {r['hearts_received']}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_giveheart(bot: BaseBot, user: User, args: list[str]) -> None:
    await handle_heart(bot, user, args)


async def handle_reactheart(bot: BaseBot, user: User, args: list[str]) -> None:
    await handle_heart(bot, user, args)


# ---------------------------------------------------------------------------
# SOCIAL ACTIONS  (Part 14)
# ---------------------------------------------------------------------------

_SOCIAL_MSGS = {
    "hug":      "🤗 {a} hugged {b}.",
    "kiss":     "😘 {a} kissed {b}.",
    "slap":     "👋 {a} slapped {b} playfully.",
    "punch":    "🥊 {a} playfully punched {b}.",
    "highfive": "🙌 {a} high-fived {b}!",
    "boop":     "👆 {a} booped {b}'s nose.",
    "waveat":   "👋 {a} waved at {b}!",
    "cheer":    "🎉 {a} cheered for {b}!",
}


async def _do_social(bot: BaseBot, user: User, args: list[str], action: str) -> None:
    if _rs("social_enabled", "true") != "true":
        await _w(bot, user.id, "Social actions are OFF.")
        return
    if len(args) < 2:
        await _w(bot, user.id, f"Usage: !{action} <username>")
        return
    target_name = args[1].lstrip("@")
    if target_name.lower() == user.username.lower():
        await _w(bot, user.id, "You can't do that to yourself.")
        return
    if not db.is_social_enabled(target_name):
        await _w(bot, user.id, f"@{target_name} has social actions OFF.")
        return
    if db.is_social_blocked(user.username, target_name):
        await _w(bot, user.id, f"@{target_name} has you blocked.")
        return
    a_disp = db.get_display_name(user.id, user.username)
    b_disp = db.get_display_name_by_username(target_name)
    msg = _SOCIAL_MSGS.get(action, f"{a_disp} → {b_disp}").format(
        a=a_disp, b=b_disp
    )
    try:
        await bot.highrise.chat(msg[:249])
    except Exception:
        pass
    conn = db.get_connection()
    conn.execute(
        """INSERT INTO room_social_logs (timestamp, actor_username, target_username, action, message)
           VALUES (datetime('now'), ?, ?, ?, ?)""",
        (user.username.lower(), target_name.lower(), action, msg),
    )
    conn.commit()
    conn.close()


async def handle_hug(bot: BaseBot, user: User, args: list[str]) -> None:
    await _do_social(bot, user, args, "hug")

async def handle_kiss(bot: BaseBot, user: User, args: list[str]) -> None:
    await _do_social(bot, user, args, "kiss")

async def handle_slap(bot: BaseBot, user: User, args: list[str]) -> None:
    await _do_social(bot, user, args, "slap")

async def handle_punch(bot: BaseBot, user: User, args: list[str]) -> None:
    await _do_social(bot, user, args, "punch")

async def handle_highfive(bot: BaseBot, user: User, args: list[str]) -> None:
    await _do_social(bot, user, args, "highfive")

async def handle_boop(bot: BaseBot, user: User, args: list[str]) -> None:
    await _do_social(bot, user, args, "boop")

async def handle_waveat(bot: BaseBot, user: User, args: list[str]) -> None:
    await _do_social(bot, user, args, "waveat")

async def handle_cheer(bot: BaseBot, user: User, args: list[str]) -> None:
    await _do_social(bot, user, args, "cheer")


async def handle_social(bot: BaseBot, user: User, args: list[str]) -> None:
    sub = args[1].lower() if len(args) > 1 else ""
    if sub in {"off", "disable"}:
        db.set_social_enabled(user.username, False)
        await _w(bot, user.id, "⛔ Social actions OFF for you.")
    elif sub in {"on", "enable"}:
        db.set_social_enabled(user.username, True)
        await _w(bot, user.id, "✅ Social actions ON.")
    else:
        state = "ON" if db.is_social_enabled(user.username) else "OFF"
        await _w(bot, user.id, f"Social actions: {state}. Use !social on|off.")


async def handle_blocksocial(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !blocksocial <username>")
        return
    target = args[1].lstrip("@")
    db.set_social_block(user.username, target, True)
    await _w(bot, user.id, f"✅ @{target} blocked from social actions.")


async def handle_unblocksocial(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !unblocksocial <username>")
        return
    target = args[1].lstrip("@")
    db.set_social_block(user.username, target, False)
    await _w(bot, user.id, f"✅ @{target} unblocked.")


# ---------------------------------------------------------------------------
# BOT FOLLOW  (Part 15)
# ---------------------------------------------------------------------------

async def handle_followme(bot: BaseBot, user: User) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if _rs("bot_follow_enabled", "true") != "true":
        await _w(bot, user.id, "Bot follow is not supported by current API.")
        return
    await _start_follow(bot, user.username)
    await _w(bot, user.id, f"🤖 Following @{user.username}.")


async def handle_follow(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !follow <username>")
        return
    target_name = args[1].lstrip("@")
    if _rs("bot_follow_enabled", "true") != "true":
        await _w(bot, user.id, "Bot follow is not supported by current API.")
        return
    await _start_follow(bot, target_name)
    db.log_room_action(user.username, target_name, "follow", "")
    await _w(bot, user.id, f"🤖 Following @{target_name}.")


async def _start_follow(bot: BaseBot, target_username: str) -> None:
    global _follow_task
    if _follow_task and not _follow_task.done():
        _follow_task.cancel()
    db.set_follow_state(target_username, True)
    interval = int(_rs("follow_interval_seconds", "3"))

    async def _follow_loop():
        while True:
            try:
                pos = _user_positions.get(_get_uid_from_cache(target_username))
                if pos:
                    await bot.highrise.walk_to(pos)
            except Exception:
                pass
            await asyncio.sleep(interval)

    _follow_task = asyncio.create_task(_follow_loop())


def _get_uid_from_cache(username: str) -> str:
    """Look up user_id from position cache (best-effort)."""
    from modules.gold import _room_cache
    entry = _room_cache.get(username.lower())
    return entry[0] if entry else ""


async def handle_stopfollow(bot: BaseBot, user: User) -> None:
    global _follow_task
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if _follow_task and not _follow_task.done():
        _follow_task.cancel()
        _follow_task = None
    db.set_follow_state("", False)
    db.log_room_action(user.username, "", "stopfollow", "")
    await _w(bot, user.id, "🛑 Bot follow stopped.")


async def handle_followstatus(bot: BaseBot, user: User) -> None:
    state = db.get_follow_state()
    if state and state["enabled"]:
        await _w(bot, user.id, f"🤖 Following @{state['target_username']}.")
    else:
        await _w(bot, user.id, "🛑 Bot is not following anyone.")


# ---------------------------------------------------------------------------
# PLAYER LISTS  (Part 11)
# ---------------------------------------------------------------------------

async def handle_players(bot: BaseBot, user: User) -> None:
    users = await _get_all_room_users(bot)
    names = [f"@{u.username}" for u, _ in users]
    total = len(names)
    chunk = ", ".join(names[:8])
    extra = f" +{total-8} more" if total > 8 else ""
    await _w(bot, user.id, f"👥 Room: {chunk}{extra}. Total {total}."[:249])


async def handle_roomlist(bot: BaseBot, user: User) -> None:
    await handle_players(bot, user)


async def handle_online(bot: BaseBot, user: User) -> None:
    await handle_players(bot, user)


async def handle_staffonline(bot: BaseBot, user: User) -> None:
    users = await _get_all_room_users(bot)
    staff = [f"@{u.username}" for u, _ in users if can_moderate(u.username)]
    if staff:
        await _w(bot, user.id, f"🛡️ Staff online: {', '.join(staff)}"[:249])
    else:
        await _w(bot, user.id, "No staff currently in room.")


async def handle_vipsinroom(bot: BaseBot, user: User) -> None:
    users = await _get_all_room_users(bot)
    vips  = []
    for u, _ in users:
        row = db.get_user_by_username(u.username)
        if row and db.owns_item(row["user_id"], "vip"):
            vips.append(f"@{u.username}")
    if vips:
        await _w(bot, user.id, f"💎 VIPs in room: {', '.join(vips)}"[:249])
    else:
        await _w(bot, user.id, "No VIPs currently in room.")


async def handle_rolelist(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !rolelist <role>  (owner/admin/manager/mod/vip)")
        return
    role = args[1].lower()
    users = await _get_all_room_users(bot)
    found = []
    for u, _ in users:
        if role == "owner" and is_owner(u.username):
            found.append(f"@{u.username}")
        elif role == "admin" and is_admin(u.username):
            found.append(f"@{u.username}")
        elif role == "manager" and is_manager(u.username):
            found.append(f"@{u.username}")
        elif role in {"mod", "moderator"} and is_moderator(u.username):
            found.append(f"@{u.username}")
        elif role == "vip":
            row = db.get_user_by_username(u.username)
            if row and db.owns_item(row["user_id"], "vip"):
                found.append(f"@{u.username}")
    if found:
        await _w(bot, user.id, f"{role}: {', '.join(found)}"[:249])
    else:
        await _w(bot, user.id, f"No {role} currently in room.")


# ---------------------------------------------------------------------------
# ALERTS & ANNOUNCEMENTS  (Part 16)
# ---------------------------------------------------------------------------

async def handle_alert(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !alert <message>")
        return
    msg = " ".join(args[1:])
    await bot.highrise.chat(f"🚨 Alert: {msg}"[:249])
    db.log_room_action(user.username, "room", "alert", msg[:100])


async def handle_announce(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !announce <message>")
        return
    msg = " ".join(args[1:])
    await bot.highrise.chat(f"📣 {msg}"[:249])
    db.log_room_action(user.username, "room", "announce", msg[:100])


async def handle_staffalert(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Moderators and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !staffalert <message>")
        return
    msg  = " ".join(args[1:])
    room_users = await _get_all_room_users(bot)
    count = 0
    for u, _ in room_users:
        if can_moderate(u.username):
            try:
                await bot.highrise.send_whisper(u.id, f"🛡️ Staff Alert: {msg}"[:249])
                count += 1
            except Exception:
                pass
    await _w(bot, user.id, f"✅ Staff alert sent to {count} staff members.")


async def handle_vipalert(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !vipalert <message>")
        return
    msg  = " ".join(args[1:])
    room_users = await _get_all_room_users(bot)
    count = 0
    for u, _ in room_users:
        row = db.get_user_by_username(u.username)
        if row and db.owns_item(row["user_id"], "vip"):
            try:
                await bot.highrise.send_whisper(u.id, f"💎 VIP: {msg}"[:249])
                count += 1
            except Exception:
                pass
    await _w(bot, user.id, f"✅ VIP alert sent to {count} VIPs.")


async def handle_roomalert(bot: BaseBot, user: User, args: list[str]) -> None:
    await handle_alert(bot, user, args)


async def handle_clearalerts(bot: BaseBot, user: User) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    await _w(bot, user.id, "✅ Alerts cleared (no persistent alerts to clear).")


# ---------------------------------------------------------------------------
# WELCOME SYSTEM  (Part 17)
# ---------------------------------------------------------------------------

async def handle_welcome(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        enabled = _rs("welcome_enabled", "true") == "true"
        msg     = _rs("welcome_message", "👋 Welcome to the Lounge!")
        await _w(bot, user.id,
                 f"Welcome: {'ON' if enabled else 'OFF'}\n"
                 f"Msg: {msg[:100]}\nEdit: /setwelcome <msg>")
        return
    sub = args[1].lower()
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if sub == "on":
        _rset("welcome_enabled", "true")
        await _w(bot, user.id, "✅ Welcome ON.")
    elif sub == "off":
        _rset("welcome_enabled", "false")
        await _w(bot, user.id, "⛔ Welcome OFF.")


async def handle_setwelcome(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !setwelcome <message>")
        return
    msg = " ".join(args[1:])[:200]
    _rset("welcome_message", msg)
    db.log_room_action(user.username, "", "setwelcome", msg[:80])
    await _w(bot, user.id, "✅ Welcome message updated.")


async def handle_welcometest(bot: BaseBot, user: User) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    msg = _rs("welcome_message", "👋 Welcome to the Lounge! Type !help to get started.")
    try:
        await bot.highrise.send_whisper(user.id, msg[:249])
    except Exception:
        await bot.highrise.chat(f"Test welcome: {msg}"[:249])


async def handle_resetwelcome(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !resetwelcome <username>")
        return
    target = args[1].lstrip("@")
    db.reset_welcome_seen(target)
    await _w(bot, user.id, f"✅ @{target} welcome reset.")


async def handle_welcomeinterval(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !welcomeinterval <minutes>|on|off")
        return
    sub = args[1].lower()
    if sub == "on":
        _rset("welcome_interval_enabled", "true")
        await _w(bot, user.id, "✅ Welcome interval ON.")
    elif sub == "off":
        _rset("welcome_interval_enabled", "false")
        await _w(bot, user.id, "⛔ Welcome interval OFF.")
    elif sub.isdigit():
        mins = max(10, int(sub))
        _rset("welcome_interval_minutes", str(mins))
        await _w(bot, user.id, f"✅ Welcome interval set to {mins}m.")
    else:
        await _w(bot, user.id, "Usage: !welcomeinterval <minutes>|on|off")


async def send_welcome_if_needed(bot: BaseBot, user: User) -> None:
    """Called from on_user_join to conditionally send a welcome message."""
    if _rs("welcome_enabled", "true") != "true":
        return
    if db.has_been_welcomed(user.username):
        return
    msg = _rs(
        "welcome_message",
        "👋 Welcome to ChillTopia!\n"
        "!help — command menu\n"
        "!balance — coins  !mine / !fish — earn\n"
        "!bet [amount] — blackjack\n"
        "!tele list — spots  !subscribe — notifs\n"
        "Have fun and chill!",
    )
    try:
        await bot.highrise.send_whisper(user.id, msg[:249])
    except Exception:
        pass
    db.mark_welcomed(user.username)


# ---------------------------------------------------------------------------
# INTERVAL MESSAGES  (Part 18)
# ---------------------------------------------------------------------------

async def handle_intervals(bot: BaseBot, user: User) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    rows = db.get_all_intervals()
    if not rows:
        await _w(bot, user.id, "No intervals set. Use !addinterval <min> <msg>.")
        return
    for r in rows[:6]:
        state = "ON" if r["enabled"] else "OFF"
        await _w(bot, user.id,
                 f"#{r['id']} [{state}] every {r['interval_minutes']}m: {r['message'][:80]}")


async def handle_addinterval(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !addinterval <minutes> <message>")
        return
    if not args[1].isdigit():
        await _w(bot, user.id, "Minutes must be a positive number.")
        return
    mins = max(int(_rs("min_interval_minutes", "10")), int(args[1]))
    msg  = " ".join(args[2:])[:200]
    new_id = db.add_interval(msg, mins, user.username)
    db.log_room_action(user.username, "", "addinterval", f"id={new_id} mins={mins}")
    await _w(bot, user.id, f"✅ Interval #{new_id} added every {mins}m.")


async def handle_delinterval(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !delinterval <id>")
        return
    db.delete_interval(int(args[1]))
    await _w(bot, user.id, f"✅ Interval #{args[1]} deleted.")


async def handle_interval(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !interval on|off <id>")
        return
    sub = args[1].lower()
    if not args[2].isdigit():
        await _w(bot, user.id, "ID must be a number.")
        return
    iid = int(args[2])
    if sub == "on":
        db.toggle_interval(iid, True)
        await _w(bot, user.id, f"✅ Interval #{iid} enabled.")
    elif sub == "off":
        db.toggle_interval(iid, False)
        await _w(bot, user.id, f"⛔ Interval #{iid} disabled.")
    else:
        await _w(bot, user.id, "Usage: !interval on|off <id>")


async def handle_intervaltest(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: !intervaltest <id>")
        return
    iid  = int(args[1])
    rows = [r for r in db.get_all_intervals() if r["id"] == iid]
    if not rows:
        await _w(bot, user.id, f"Interval #{iid} not found.")
        return
    await bot.highrise.chat(rows[0]["message"][:249])
    await _w(bot, user.id, f"✅ Interval #{iid} test sent.")


_interval_loop_task: asyncio.Task | None = None


async def start_interval_loop(bot: BaseBot) -> None:
    """Background loop — call this from before_start."""
    global _interval_loop_task
    if _interval_loop_task and not _interval_loop_task.done():
        print("[INTERVAL] Loop already running — skipping duplicate start (reconnect).")
        return

    async def _loop():
        while True:
            try:
                rows = db.get_all_intervals()
                for r in rows:
                    if not r["enabled"]:
                        continue
                    mins = r["interval_minutes"]
                    last = r["last_sent_at"]
                    if last:
                        try:
                            from datetime import datetime, timezone
                            dt   = datetime.strptime(last, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                            diff = (datetime.now(timezone.utc) - dt).total_seconds() / 60
                            if diff < mins:
                                continue
                        except Exception:
                            pass
                    await bot.highrise.chat(r["message"][:249])
                    db.mark_interval_sent(r["id"])
            except Exception:
                pass
            await asyncio.sleep(60)

    _interval_loop_task = asyncio.create_task(_loop())


# ---------------------------------------------------------------------------
# REPEAT MESSAGE  (Part 20)
# ---------------------------------------------------------------------------

async def handle_repeatmsg(bot: BaseBot, user: User, args: list[str]) -> None:
    global _repeat_task, _repeat_status
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    if len(args) < 4:
        await _w(bot, user.id, "Usage: !repeatmsg <count> <seconds> <message>")
        return
    if not args[1].isdigit() or not args[2].isdigit():
        await _w(bot, user.id, "Count and seconds must be positive integers.")
        return
    count   = min(5, int(args[1]))
    seconds = max(int(_rs("repeat_min_seconds", "10")), int(args[2]))
    msg     = " ".join(args[3:])[:120]

    if _repeat_task and not _repeat_task.done():
        await _w(bot, user.id, "A repeat is already running. Use !stoprepeat first.")
        return

    _repeat_status = {"count": count, "seconds": seconds, "msg": msg, "remaining": count}

    async def _repeat():
        for i in range(count):
            try:
                await bot.highrise.chat(msg[:249])
            except Exception:
                pass
            _repeat_status["remaining"] = count - i - 1
            if i < count - 1:
                await asyncio.sleep(seconds)

    _repeat_task = asyncio.create_task(_repeat())
    db.log_room_action(user.username, "room", "repeatmsg", f"count={count} sec={seconds}")
    await _w(bot, user.id, f"✅ Repeat started: {count} messages every {seconds}s.")


async def handle_stoprepeat(bot: BaseBot, user: User) -> None:
    global _repeat_task
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    if _repeat_task and not _repeat_task.done():
        _repeat_task.cancel()
        _repeat_task = None
    await _w(bot, user.id, "🛑 Repeat stopped.")


async def handle_repeatstatus(bot: BaseBot, user: User) -> None:
    if not is_owner(user.username):
        await _w(bot, user.id, "Owner only.")
        return
    if _repeat_task and not _repeat_task.done():
        rem = _repeat_status.get("remaining", "?")
        msg = _repeat_status.get("msg", "")[:50]
        await _w(bot, user.id, f"🔁 Repeat active — {rem} left: \"{msg}\"")
    else:
        await _w(bot, user.id, "No active repeat.")


# ---------------------------------------------------------------------------
# ROOM SETTINGS  (Part 21)
# ---------------------------------------------------------------------------

async def handle_roomsettings(bot: BaseBot, user: User, args: list[str]) -> None:
    page = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    from modules.bot_modes import get_bot_mode
    mode = get_bot_mode()
    mode_name = mode["mode_name"] if mode else "None"

    if page == 1:
        welcome  = "ON" if _rs("welcome_enabled", "true") == "true" else "OFF"
        selftp   = "ON" if _rs("self_teleport_enabled", "false") == "true" else "OFF"
        emotes   = "ON" if _rs("public_emotes_enabled", "true") == "true" else "OFF"
        social   = "ON" if _rs("social_enabled", "true") == "true" else "OFF"
        await _w(bot, user.id,
                 f"Room: Welcome {welcome} | SelfTP {selftp} | "
                 f"Emotes {emotes} | Social {social} | Bot {mode_name}")
    else:
        intervals  = "ON" if any(r["enabled"] for r in db.get_all_intervals()) else "OFF"
        follow     = "ON" if db.get_follow_state() else "OFF"
        prefix     = "ON" if _rs("bot_prefix_enabled", "true") == "true" else "OFF"
        hcd        = _rs("heart_cooldown_seconds", "60")
        await _w(bot, user.id,
                 f"Room2: Intervals {intervals} | Follow {follow} | "
                 f"Prefix {prefix} | HeartCD {hcd}s")


async def handle_setroomsetting(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !setroomsetting <key> <value>")
        return
    key = args[1].lower()
    val = args[2]
    _rset(key, val)
    await _w(bot, user.id, f"✅ {key} = {val}")


# ---------------------------------------------------------------------------
# BOOST / MIC  (Part 19)
# ---------------------------------------------------------------------------

async def handle_boostroom(bot: BaseBot, user: User) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    try:
        await bot.highrise.buy_room_boost(1)
        await _w(bot, user.id, "✅ Room boost activated.")
    except Exception:
        await _w(bot, user.id, "Room boost is not supported by current API.")


async def handle_startmic(bot: BaseBot, user: User) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    await _w(bot, user.id, "Mic control is not supported by current API.")


async def handle_micstatus(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id, "Mic control is not supported by current API.")


# ---------------------------------------------------------------------------
# ROOM LOGS  (Part 23)
# ---------------------------------------------------------------------------

async def handle_roomlogs(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admin and above only.")
        return
    target = args[1].lstrip("@") if len(args) > 1 else ""
    logs   = db.get_room_action_logs(target, limit=8)
    if not logs:
        await _w(bot, user.id, "No room logs.")
        return
    for r in logs[:5]:
        line = f"Log #{r['id']}: @{r['actor_username']} {r['action']}"
        if r["target_username"]:
            line += f" → @{r['target_username']}"
        await _w(bot, user.id, line[:249])


# ---------------------------------------------------------------------------
# EXTENDED MODERATION  (Part 10)
# ---------------------------------------------------------------------------

async def handle_kick(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Moderators and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !kick <username> [reason]")
        return
    target_name = args[1].lstrip("@")
    reason      = " ".join(args[2:]) if len(args) > 2 else "No reason"
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, user.id, f"@{target_name} not in room.")
        return
    target_user, _ = pair
    # Prevent kicking higher role
    if is_owner(target_user.username) and not is_owner(user.username):
        await _w(bot, user.id, "Cannot kick a higher role.")
        return
    if is_admin(target_user.username) and not (is_owner(user.username) or is_admin(user.username)):
        await _w(bot, user.id, "Cannot kick a higher role.")
        return
    try:
        await bot.highrise.moderate_room(target_user.id, "kick")
        db.log_room_action(user.username, target_user.username, "kick", reason)
        _kicked_disp = db.get_display_name(target_user.id, target_user.username)
        await _w(bot, user.id, f"✅ @{target_user.username} kicked.")
        await bot.highrise.chat(f"🚫 {_kicked_disp} was kicked."[:249])
    except Exception:
        await _w(bot, user.id, "Kick not supported by current API.")


async def handle_ban(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Moderators and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !ban <username> [reason]")
        return
    target_name = args[1].lstrip("@")
    reason      = " ".join(args[2:]) if len(args) > 2 else "No reason"
    _check_role_guard(user, target_name)
    # DB ban (permanent bot-level)
    _safe_room_ban(target_name, user.username, reason)
    # Try API ban
    pair = await _resolve_user_in_room(bot, target_name)
    api_ok = False
    if pair:
        try:
            await bot.highrise.moderate_room(pair[0].id, "ban")
            api_ok = True
        except Exception:
            pass
    db.log_room_action(user.username, target_name, "ban", reason)
    suffix = "" if api_ok else " (bot-level only)"
    await _w(bot, user.id, f"⛔ @{target_name} banned{suffix}.")


async def handle_tempban(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Moderators and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !tempban <username> <minutes> [reason]")
        return
    target_name = args[1].lstrip("@")
    if not args[2].isdigit():
        await _w(bot, user.id, "Minutes must be a positive number.")
        return
    minutes = int(args[2])
    reason  = " ".join(args[3:]) if len(args) > 3 else "No reason"
    _safe_room_ban(target_name, user.username, reason, minutes)
    pair = await _resolve_user_in_room(bot, target_name)
    if pair:
        try:
            await bot.highrise.moderate_room(pair[0].id, "ban", minutes)
        except Exception:
            pass
    db.log_room_action(user.username, target_name, "tempban", f"{minutes}m: {reason}")
    await _w(bot, user.id, f"⛔ @{target_name} temp banned for {minutes}m.")


async def handle_unban(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Moderators and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !unban <username>")
        return
    target_name = args[1].lstrip("@")
    db.room_unban_user(target_name)
    pair = await _resolve_user_in_room(bot, target_name)
    if pair:
        try:
            await bot.highrise.moderate_room(pair[0].id, "unban")
        except Exception:
            pass
    db.log_room_action(user.username, target_name, "unban", "")
    await _w(bot, user.id, f"✅ @{target_name} unbanned.")


async def handle_bans(bot: BaseBot, user: User) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Moderators and above only.")
        return
    bans = db.get_all_room_bans()
    if not bans:
        await _w(bot, user.id, "No active bans.")
        return
    lines = ["⛔ Active bans:"]
    for b in bans[:6]:
        perm = "perm" if b["permanent"] else f"until {b['banned_until'][:16] if b['banned_until'] else '?'}"
        lines.append(f"@{b['username']} ({perm}): {b['reason'][:40]}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_modlog(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Moderators and above only.")
        return
    target = args[1].lstrip("@") if len(args) > 1 else ""
    logs   = db.get_room_action_logs(target, limit=8)
    if not logs:
        await _w(bot, user.id, "No mod logs found.")
        return
    for r in logs[:5]:
        await _w(bot, user.id,
                 f"#{r['id']} @{r['actor_username']} → {r['action']} "
                 f"@{r['target_username']}"[:249])


def _check_role_guard(actor: User, target_name: str) -> None:
    """Raise if actor tries to moderate higher role (best-effort)."""
    pass  # handled inline above for now


def _safe_room_ban(username: str, banned_by: str, reason: str,
                   minutes: int | None = None) -> None:
    try:
        conn = db.get_connection()
        perm = 0 if minutes else 1
        if minutes:
            conn.execute(
                """INSERT OR REPLACE INTO room_bans
                   (username, banned_by, reason, banned_until, permanent, created_at)
                   VALUES (lower(?), ?, ?, datetime('now', ? || ' minutes'), ?, datetime('now'))""",
                (username, banned_by, reason, str(minutes), perm),
            )
        else:
            conn.execute(
                """INSERT OR REPLACE INTO room_bans
                   (username, banned_by, reason, banned_until, permanent, created_at)
                   VALUES (lower(?), ?, ?, NULL, 1, datetime('now'))""",
                (username, banned_by, reason),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[ROOM] _safe_room_ban error: {e!r}")


# ---------------------------------------------------------------------------
# HELP MENUS  (Part 22)
# ---------------------------------------------------------------------------

async def handle_roomhelp(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id,
             "🏠 Room\n"
             "!players — room list\n"
             "!spawns — spawn list\n"
             "!roomsettings — settings\n"
             "!welcome — welcome info\n"
             "!roomhelp | !teleporthelp | !emotehelp")


async def handle_teleporthelp(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id,
             "📍 Teleport\n"
             "!tpme [spawn] — self TP\n"
             "!tp [user] [spawn] — staff TP\n"
             "!bring [user] — bring here\n"
             "!tpall [spawn] — all\n"
             "!spawns — list spawns")


async def handle_emoteinfo(bot: BaseBot, user: User, args: list[str]) -> None:
    """/emoteinfo <name> — show description and full emote-ID for a known emote."""
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !emoteinfo <name>  e.g. /emoteinfo dance")
        return
    raw = args[1].lower().strip()
    eid = raw if raw.startswith("emote-") else f"emote-{raw}"
    desc = _EMOTE_DESCRIPTIONS.get(eid)
    if desc:
        in_list = "✅ In emote list." if eid in EMOTE_LIST else "⚠️ Not in list but may work."
        await _w(bot, user.id,
                 f"💃 {eid}\n{desc}\n{in_list}\nUse: !emote {raw}")
    else:
        close = [e for e in EMOTE_LIST if raw in e.replace("emote-", "")][:4]
        hint  = "Similar: " + ", ".join(e.replace("emote-", "") for e in close) if close else ""
        await _w(bot, user.id,
                 f"❓ Unknown emote '{raw}'. {hint}\n!emotes to list all."[:249])


async def handle_emotehelp(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id,
             "💃 Emotes\n"
             "!emotes [page] — list\n"
             "!emote [id] — use\n"
             "!emoteinfo [id] — details\n"
             "!loopemote [id] — loop\n"
             "!forceemote [user] [id]")


async def handle_alerthelp(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id,
             "📣 Alerts\n"
             "!announce [msg] — room\n"
             "!alert [msg] — urgent\n"
             "!staffalert [msg]\n"
             "!vipalert [msg]\n"
             "!dmnotify [user] [msg]")


async def handle_welcomehelp(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id,
             "👋 Welcome\n"
             "!welcome on|off\n"
             "!setwelcome [msg]\n"
             "!welcometest\n"
             "!welcomeinterval [min]\n"
             "!resetwelcome [user]")


async def handle_socialhelp(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id,
             "💬 Social\n"
             "!hug  !kiss  !slap  !punch\n"
             "!highfive  !boop  !waveat  !cheer\n"
             "!heart [user]\n"
             "!social off — disable\n"
             "!blocksocial [user]")


# ---------------------------------------------------------------------------
# Bot spawn system
# ---------------------------------------------------------------------------

async def handle_setbotspawn(bot: BaseBot, user: User, args: list[str]) -> None:
    """!setbotspawn @BotName <spawn_name> — save a named spawn for a specific bot."""
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: !setbotspawn @BotName <spawn_name>\n"
                 "The spawn must already exist (!spawns to list).")
        return
    bot_username = args[1].lstrip("@").lower()
    spawn_name   = args[2].lower()
    spawn = db.get_spawn(spawn_name)
    if not spawn:
        await _w(bot, user.id,
                 f"Spawn '{spawn_name}' not found. !spawns to list saves.")
        return
    x, y, z = spawn["x"], spawn["y"], spawn["z"]
    facing   = spawn.get("facing", "FrontRight")
    db.set_bot_spawn(bot_username, spawn_name, x, y, z, facing, user.username)
    msg = (
        f"🤖 Bot Spawn Saved\n"
        f"Bot: @{bot_username}\n"
        f"Position: {x} {y} {z} {facing}"
    )
    await _w(bot, user.id, msg[:249])


async def handle_setbotspawnhere(bot: BaseBot, user: User, args: list[str]) -> None:
    """!setbotspawnhere @BotName — save bot's spawn at the command user's position."""
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !setbotspawnhere @BotName")
        return
    bot_username = args[1].lstrip("@").lower()

    # Primary: use cached position (keyed by user_id)
    pos = _user_positions.get(user.id)

    # Fallback: live SDK room user fetch
    if not pos:
        try:
            resp = await bot.highrise.get_room_users()
            if hasattr(resp, "content"):
                for ru, rp in resp.content:
                    if ru.id == user.id:
                        if isinstance(rp, Position):
                            pos = rp
                            _user_positions[user.id]      = pos
                            _user_position_times[user.id] = time.time()
                        break
        except Exception:
            pass

    if not pos:
        await _w(bot, user.id,
                 "I don't have your position yet. Walk a few steps, then try again.")
        return

    x, y, z = pos.x, pos.y, pos.z
    facing   = getattr(pos, "facing", "FrontRight")
    db.set_bot_spawn(bot_username, "custom", x, y, z, str(facing), user.username)

    # Teleport the target bot to that position right now if it's in the room
    moved = "NO"
    try:
        result = await _resolve_user_in_room(bot, bot_username)
        if result:
            target_user, _ = result
            await bot.highrise.teleport(target_user.id, pos)
            moved = "YES"
    except Exception:
        pass

    msg = (
        f"🤖 Bot Spawn Saved\n"
        f"Bot: @{bot_username}\n"
        f"Moved Here: {moved}\n"
        f"Auto-return on rejoin: YES"
    )
    await _w(bot, user.id, msg[:249])


async def handle_botspawns(bot: BaseBot, user: User) -> None:
    """!botspawns — list all known bots with saved/not-set spawn status."""
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return

    saved_map = {s["bot_username"]: s for s in db.list_bot_spawns()}

    # Collect known bot usernames from bot_instances table
    seen: list[str] = []
    seen_lower: set[str] = set()
    for inst in db.get_bot_instances():
        bn = (inst.get("bot_username") or "").strip()
        if bn and bn.lower() not in seen_lower:
            seen.append(bn)
            seen_lower.add(bn.lower())
    # Also include any saved bots not in bot_instances
    for bn in saved_map:
        if bn.lower() not in seen_lower:
            seen.append(bn)
            seen_lower.add(bn.lower())

    if not seen:
        await _w(bot, user.id, "No bots registered yet. Use !setbotspawnhere @BotName.")
        return

    lines = ["🤖 Bot Spawns"]
    for bn in seen:
        status = "saved" if bn.lower() in saved_map else "not set"
        lines.append(f"@{bn} — {status}")

    chunk = lines[0]
    for line in lines[1:]:
        candidate = chunk + "\n" + line
        if len(candidate) <= 249:
            chunk = candidate
        else:
            await _w(bot, user.id, chunk)
            chunk = line
    if chunk:
        await _w(bot, user.id, chunk)


async def handle_clearbotspawn(bot: BaseBot, user: User, args: list[str]) -> None:
    """!clearbotspawn @BotName — remove a bot's saved spawn."""
    if not _can_manage_room(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !clearbotspawn @BotName")
        return
    bot_username = args[1].lstrip("@").lower()
    removed = db.clear_bot_spawn(bot_username)
    if removed:
        await _w(bot, user.id, f"🗑️ Bot Spawn Cleared\nBot: @{bot_username}")
    else:
        await _w(bot, user.id, f"No spawn found for @{bot_username}.")


async def apply_bot_spawn(bot: BaseBot, bot_username: str) -> None:
    """Teleport the bot to its saved spawn. Called from on_start."""
    from modules.gold import get_bot_username as _get_live_uname
    live_uname = _get_live_uname()
    if not live_uname:
        # Fall back to passed-in name (may still be empty on very early calls)
        live_uname = bot_username
    if not live_uname:
        return
    row = db.get_bot_spawn(live_uname)
    if not row:
        print(f"[BOT_SPAWN] bot={live_uname} spawn_found=false")
        return
    print(f"[BOT_SPAWN] bot={live_uname} spawn_found=true "
          f"x={row['x']} y={row['y']} z={row['z']}")
    try:
        from highrise.models import Position
        pos = Position(x=row["x"], y=row["y"], z=row["z"])
        await bot.highrise.walk_to(pos)
        print(f"[BOT_SPAWN] bot={live_uname} moved=success "
              f"spawn={row['spawn_name']}")
    except Exception as exc:
        print(f"[BOT_SPAWN] bot={live_uname} moved=fail reason={exc}")


async def handle_mypos(bot: BaseBot, user: User, args: list[str]) -> None:
    """/mypos — show the requester's last tracked position."""
    pos = _user_positions.get(user.id)
    ts  = _user_position_times.get(user.id)

    # SDK live fallback
    if not pos:
        try:
            resp = await bot.highrise.get_room_users()
            if hasattr(resp, "content"):
                for ru, rp in resp.content:
                    if ru.id == user.id:
                        if isinstance(rp, Position):
                            pos = rp
                            _user_positions[user.id]      = pos
                            _user_position_times[user.id] = time.time()
                            ts = _user_position_times[user.id]
                        break
        except Exception:
            pass

    if not pos:
        await _w(bot, user.id,
                 "Position not tracked yet. Walk a few steps and try again.")
        return

    age = f"{int(time.time() - ts)}s ago" if ts else "unknown"
    facing = getattr(pos, "facing", "?")
    await _w(bot, user.id,
             f"📍 Your pos: x={pos.x:.1f} y={pos.y:.1f} z={pos.z:.1f} "
             f"facing={facing} | updated {age}")


async def handle_positiondebug(bot: BaseBot, user: User, args: list[str]) -> None:
    """/positiondebug <username> — show last tracked position for a user (admin+)."""
    if not is_admin(user.username) and not is_owner(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !positiondebug <username>")
        return
    target_name = args[1].lstrip("@")

    # Find target user_id from room cache
    from modules.gold import _room_cache
    entry = _room_cache.get(target_name.lower())
    if not entry:
        await _w(bot, user.id, f"@{target_name} not in room cache.")
        return
    target_id, display_name = entry

    pos = _user_positions.get(target_id)
    ts  = _user_position_times.get(target_id)

    if not pos:
        await _w(bot, user.id, f"@{display_name}: no position tracked yet.")
        return

    age = f"{int(time.time() - ts)}s ago" if ts else "unknown"
    facing = getattr(pos, "facing", "?")
    await _w(bot, user.id,
             f"📍 @{display_name}: x={pos.x:.1f} y={pos.y:.1f} z={pos.z:.1f} "
             f"facing={facing} | updated {age}")
