"""
modules/tele.py
---------------
User-facing teleport, tag, and role-spawn system.

! is the canonical prefix for all commands documented here.

Everyone (subject to spot permission):
  !tele list
  !tele <spot>

Manager+ only:
  !tele @username
  !tele @username <x> <y> <z> [facing]
  !tele permission <spot> everyone|subs|vip|staff|managers|owner
  !tele role <role> <spot>
  !tele group <group> <spot>
  !tele tag <tag> [<spot>]
  !summon @username
  !create tele <spot>
  !delete tele <spot>
  !setrolespawn <role> here
  !setrolespawn <role> <x> <y> <z> [facing]
  !rolespawn <role>
  !rolespawns
  !delrolespawn <role>
  !tag create <tag>
  !tag delete <tag>
  !tag add <tag> @username
  !tag remove <tag> @username
  !tag list
  !tag members <tag>
  !tag spawn <tag> here
  !tag spawn <tag> <x> <y> <z> [facing]
  !tag delspawn <tag>
  !tag allowedit <tag> on|off
  !tag setspawn <tag> here   (member-callable if allowedit ON)
"""
from __future__ import annotations

import asyncio

from highrise import BaseBot, User
from highrise.models import Position

import database as db
from modules.permissions import (
    is_owner, is_admin, is_manager, can_moderate,
)
from modules.room_utils import _user_positions, _resolve_user_in_room, _get_all_room_users

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


def _can_manage(username: str) -> bool:
    return is_owner(username) or is_admin(username) or is_manager(username)


_VALID_PERMS = ("everyone", "subs", "vip", "staff", "managers", "owner")


def _check_spot_permission(username: str, permission: str) -> bool:
    p = (permission or "everyone").lower()
    if p == "everyone":
        return True
    if p == "owner":
        return is_owner(username)
    if p == "managers":
        return _can_manage(username)
    if p == "staff":
        return can_moderate(username)
    if p == "vip":
        try:
            vips = db.get_vip_list()
            return username.lower() in [v.lower() for v in vips]
        except Exception:
            return can_moderate(username)
    if p == "subs":
        try:
            row = db.get_subscriber(username.lower())
            return bool(row and row.get("subscribed"))
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------
# !tele — main dispatcher
# ---------------------------------------------------------------------------

async def handle_tele(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await _w(bot, user.id,
                 "🌀 Teleport\n"
                 "!tele list — see all spots\n"
                 "!tele <spot> — go to spot\n"
                 "!tele @player — go to player (staff)\n"
                 "!tele @player <x> <y> <z> — send to coords")
        return

    sub = args[1].lower()

    if sub == "list":
        await _handle_tele_list(bot, user)
    elif sub == "permission":
        await _handle_tele_permission(bot, user, args)
    elif sub == "role":
        await _handle_tele_role(bot, user, args, "role")
    elif sub == "group":
        await _handle_tele_role(bot, user, args, "group")
    elif sub == "tag":
        await _handle_tele_tag(bot, user, args)
    elif sub.startswith("@"):
        await _handle_tele_to_player(bot, user, args)
    else:
        await _handle_tele_to_spot(bot, user, sub)


# ---------------------------------------------------------------------------
# !tele list
# ---------------------------------------------------------------------------

async def _handle_tele_list(bot: BaseBot, user: User) -> None:
    spots = db.get_all_spawns()
    if not spots:
        await _w(bot, user.id,
                 "🌀 No teleport spots saved yet.\n"
                 "Use !create tele <spot>")
        return
    chunk: list[str] = ["🌀 Saved Teleports"]
    for i, s in enumerate(spots, 1):
        perm = s.get("permission", "everyone") or "everyone"
        line = f"{i}. {s['spawn_name']} — {perm}"
        if len("\n".join(chunk) + "\n" + line) > 248:
            await _w(bot, user.id, "\n".join(chunk))
            chunk = []
        chunk.append(line)
    if chunk:
        await _w(bot, user.id, "\n".join(chunk))


# ---------------------------------------------------------------------------
# !tele <spot>
# ---------------------------------------------------------------------------

async def _handle_tele_to_spot(bot: BaseBot, user: User, spot: str) -> None:
    spawn = db.get_spawn(spot)
    if not spawn:
        await _w(bot, user.id,
                 "❌ Teleport spot not found.\n"
                 "Use !tele list to see saved teleports.")
        return
    perm = spawn.get("permission", "everyone") or "everyone"
    if not _check_spot_permission(user.username, perm):
        await _w(bot, user.id, "🔒 This teleport is restricted.")
        return
    pos = Position(spawn["x"], spawn["y"], spawn["z"],
                   spawn.get("facing", "FrontLeft"))
    try:
        await bot.highrise.teleport(user.id, pos)
        await _w(bot, user.id, f"🌀 Teleporting you to {spot}...")
    except Exception as exc:
        await _w(bot, user.id, f"❌ Teleport failed: {str(exc)[:60]}")


# ---------------------------------------------------------------------------
# !tele @username  /  !tele @username x y z [facing]
# ---------------------------------------------------------------------------

async def _handle_tele_to_player(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    raw = args[1].lstrip("@").lower()

    if len(args) >= 5:
        try:
            x, y, z = float(args[2]), float(args[3]), float(args[4])
        except (ValueError, IndexError):
            await _w(bot, user.id, "Usage: !tele @username <x> <y> <z> [facing]")
            return
        facing = args[5] if len(args) > 5 else "FrontLeft"
        result = await _resolve_user_in_room(bot, raw)
        if not result:
            await _w(bot, user.id, f"❌ @{raw} is not in the room.")
            return
        target, _ = result
        try:
            await bot.highrise.teleport(target.id, Position(x, y, z, facing))
            await _w(bot, user.id,
                     f"✅ Teleported @{target.username} to ({x:.1f},{y:.1f},{z:.1f}).")
        except Exception as exc:
            await _w(bot, user.id, f"❌ Teleport failed: {str(exc)[:60]}")
        return

    result = await _resolve_user_in_room(bot, raw)
    if not result:
        await _w(bot, user.id, f"❌ @{raw} is not in the room.")
        return
    target, target_pos = result
    try:
        await bot.highrise.teleport(user.id, target_pos)
        await _w(bot, user.id, f"✅ Teleported you to @{target.username}.")
    except Exception as exc:
        await _w(bot, user.id, f"❌ Teleport failed: {str(exc)[:60]}")


# ---------------------------------------------------------------------------
# !tele permission <spot> <level>
# ---------------------------------------------------------------------------

async def _handle_tele_permission(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 4:
        await _w(bot, user.id,
                 "Usage: !tele permission <spot> <level>\n"
                 f"Levels: {' | '.join(_VALID_PERMS)}")
        return
    spot  = args[2].lower()
    level = args[3].lower()
    if level not in _VALID_PERMS:
        await _w(bot, user.id,
                 f"❌ Invalid level.\nUse: {' | '.join(_VALID_PERMS)}")
        return
    if not db.get_spawn(spot):
        await _w(bot, user.id, f"❌ Spot '{spot}' not found.")
        return
    db.set_spawn_permission(spot, level)
    await _w(bot, user.id, f"✅ '{spot}' permission set to: {level}")


# ---------------------------------------------------------------------------
# !tele role / !tele group
# ---------------------------------------------------------------------------

async def _handle_tele_role(bot: BaseBot, user: User, args: list[str],
                             label: str) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 4:
        await _w(bot, user.id, f"Usage: !tele {label} <role> <spot>")
        return
    role_name = args[2].lower()
    spot_name = args[3].lower()
    spawn = db.get_spawn(spot_name)
    if not spawn:
        await _w(bot, user.id, f"❌ Spot '{spot_name}' not found.")
        return
    pos = Position(spawn["x"], spawn["y"], spawn["z"],
                   spawn.get("facing", "FrontLeft"))
    _ROLE_CHECKS: dict = {
        "owner":    is_owner,
        "admin":    is_admin,
        "manager":  is_manager,
        "staff":    can_moderate,
        "all":      lambda _: True,
        "everyone": lambda _: True,
    }
    check_fn = _ROLE_CHECKS.get(role_name)
    if not check_fn:
        await _w(bot, user.id,
                 f"❌ Unknown role '{role_name}'.\n"
                 "Roles: owner | admin | manager | staff | all")
        return
    users = await _get_all_room_users(bot)
    count = 0
    for u, _ in users:
        if check_fn(u.username):
            try:
                await bot.highrise.teleport(u.id, pos)
                count += 1
                await asyncio.sleep(0.05)
            except Exception:
                pass
    await _w(bot, user.id,
             f"✅ Sent {count} {role_name} players to {spot_name}.")


# ---------------------------------------------------------------------------
# !tele tag <tag> [<spot>]
# ---------------------------------------------------------------------------

async def _handle_tele_tag(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !tele tag <tag> [<spot>]")
        return
    tag_name = args[2].lower()
    tag = db.get_tag(tag_name)
    if not tag:
        await _w(bot, user.id, f"❌ Tag '{tag_name}' not found.")
        return
    members = db.get_tag_members(tag_name)
    if not members:
        await _w(bot, user.id, f"❌ Tag '{tag_name}' has no members.")
        return
    member_ids = {m["user_id"] for m in members}

    if len(args) >= 4:
        spot_name = args[3].lower()
        spawn = db.get_spawn(spot_name)
        if not spawn:
            await _w(bot, user.id, f"❌ Spot '{spot_name}' not found.")
            return
        pos = Position(spawn["x"], spawn["y"], spawn["z"],
                       spawn.get("facing", "FrontLeft"))
        dest_label = spot_name
    elif tag.get("spawn_x") is not None:
        pos = Position(
            tag["spawn_x"], tag["spawn_y"], tag["spawn_z"],
            tag.get("spawn_facing", "FrontLeft"),
        )
        dest_label = f"{tag_name} spawn"
    else:
        await _w(bot, user.id,
                 f"❌ No spot given and tag '{tag_name}' has no spawn set.")
        return

    users = await _get_all_room_users(bot)
    count = 0
    for u, _ in users:
        if u.id in member_ids:
            try:
                await bot.highrise.teleport(u.id, pos)
                count += 1
                await asyncio.sleep(0.05)
            except Exception:
                pass
    await _w(bot, user.id,
             f"✅ Sent {count} '{tag_name}' members to {dest_label}.")


# ---------------------------------------------------------------------------
# !create tele <spot>
# ---------------------------------------------------------------------------

async def handle_create_tele(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !create tele <spot>")
        return
    name   = args[2].lower()
    my_pos = _user_positions.get(user.id)
    if not my_pos:
        await _w(bot, user.id,
                 "🌀 Teleport Save Failed\n"
                 "Move your avatar first, then try again.")
        return
    db.save_spawn(name, my_pos.x, my_pos.y, my_pos.z,
                  getattr(my_pos, "facing", "FrontLeft"), user.username)
    await _w(bot, user.id,
             f"🌀 Teleport Saved\nSpot: {name}\n"
             f"Position updated to your current location.")


# ---------------------------------------------------------------------------
# !delete tele <spot>
# ---------------------------------------------------------------------------

async def handle_delete_tele(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !delete tele <spot>")
        return
    name = args[2].lower()
    if not db.get_spawn(name):
        await _w(bot, user.id, "❌ Teleport spot not found.")
        return
    db.delete_spawn(name)
    await _w(bot, user.id, f"🗑️ Teleport Deleted\nSpot: {name}")


# ---------------------------------------------------------------------------
# !summon @username
# ---------------------------------------------------------------------------

async def handle_summon(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !summon @username")
        return
    raw    = args[1].lstrip("@").lower()
    my_pos = _user_positions.get(user.id)
    if not my_pos:
        await _w(bot, user.id,
                 "❌ Cannot read your position. Move your avatar first.")
        return
    result = await _resolve_user_in_room(bot, raw)
    if not result:
        await _w(bot, user.id, f"❌ @{raw} is not in the room.")
        return
    target, _ = result
    try:
        await bot.highrise.teleport(target.id, my_pos)
        await _w(bot, user.id, f"✅ Summoned @{target.username} to you.")
    except Exception as exc:
        await _w(bot, user.id, f"❌ Summon failed: {str(exc)[:60]}")


# ---------------------------------------------------------------------------
# Role spawns
# ---------------------------------------------------------------------------

async def handle_setrolespawn(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: !setrolespawn <role> here\n"
                 "       !setrolespawn <role> <x> <y> <z> [facing]")
        return
    role = args[1].lower()
    if args[2].lower() == "here":
        my_pos = _user_positions.get(user.id)
        if not my_pos:
            await _w(bot, user.id,
                     "❌ Cannot read your position. Move first.")
            return
        x, y, z = my_pos.x, my_pos.y, my_pos.z
        facing  = getattr(my_pos, "facing", "FrontLeft")
    elif len(args) >= 5:
        try:
            x, y, z = float(args[2]), float(args[3]), float(args[4])
        except ValueError:
            await _w(bot, user.id, "x y z must be numbers.")
            return
        facing = args[5] if len(args) > 5 else "FrontLeft"
    else:
        await _w(bot, user.id,
                 "Usage: !setrolespawn <role> here\n"
                 "       !setrolespawn <role> <x> <y> <z> [facing]")
        return
    db.save_role_spawn(role, x, y, z, facing, user.username)
    await _w(bot, user.id,
             f"✅ Role spawn for '{role}' set to ({x:.1f},{y:.1f},{z:.1f}).")


async def handle_rolespawn(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !rolespawn <role>")
        return
    role = args[1].lower()
    rs   = db.get_role_spawn(role)
    if not rs:
        await _w(bot, user.id,
                 f"❌ No spawn set for role '{role}'.\n"
                 f"Use !setrolespawn {role} here")
        return
    pos = Position(rs["x"], rs["y"], rs["z"],
                   rs.get("facing", "FrontLeft"))
    try:
        await bot.highrise.teleport(user.id, pos)
        await _w(bot, user.id, f"🌀 Teleporting you to {role} spawn...")
    except Exception as exc:
        await _w(bot, user.id, f"❌ Teleport failed: {str(exc)[:60]}")


async def handle_rolespawns(bot: BaseBot, user: User) -> None:
    rows = db.get_all_role_spawns()
    if not rows:
        await _w(bot, user.id,
                 "📍 No role spawns saved yet.\n"
                 "Use !setrolespawn <role> here")
        return
    lines = ["📍 Role Spawns"]
    for r in rows:
        lines.append(
            f"{r['role']}: ({r['x']:.1f},{r['y']:.1f},{r['z']:.1f})"
        )
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_delrolespawn(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !delrolespawn <role>")
        return
    role = args[1].lower()
    if not db.get_role_spawn(role):
        await _w(bot, user.id, f"❌ No spawn found for role '{role}'.")
        return
    db.delete_role_spawn(role)
    await _w(bot, user.id, f"🗑️ Role spawn for '{role}' deleted.")


# ---------------------------------------------------------------------------
# Tag system
# ---------------------------------------------------------------------------

async def handle_tag(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await _tag_help(bot, user)
        return
    sub = args[1].lower()
    dispatch = {
        "create":    _tag_create,
        "delete":    _tag_delete,
        "add":       _tag_add,
        "remove":    _tag_remove,
        "list":      _tag_list,
        "members":   _tag_members,
        "spawn":     _tag_spawn,
        "delspawn":  _tag_delspawn,
        "allowedit": _tag_allowedit,
        "setspawn":  _tag_setspawn,
    }
    fn = dispatch.get(sub)
    if fn:
        await fn(bot, user, args)
    else:
        await _tag_help(bot, user)


async def _tag_help(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id,
             "🏷️ Tag Commands\n"
             "!tag create <tag>\n"
             "!tag add <tag> @user\n"
             "!tag remove <tag> @user\n"
             "!tag list\n"
             "!tag members <tag>\n"
             "!tag delete <tag>\n"
             "!tag spawn <tag> here\n"
             "!tag setspawn <tag> here\n"
             "!tag allowedit <tag> on|off\n"
             "!tele tag <tag> [<spot>]")


async def _tag_create(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !tag create <tag>")
        return
    name = args[2].lower()
    if db.get_tag(name):
        await _w(bot, user.id, f"❌ Tag '{name}' already exists.")
        return
    db.create_tag(name, user.username)
    await _w(bot, user.id, f"✅ Tag '{name}' created.")


async def _tag_delete(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !tag delete <tag>")
        return
    name = args[2].lower()
    if not db.get_tag(name):
        await _w(bot, user.id, f"❌ Tag '{name}' not found.")
        return
    db.delete_tag(name)
    await _w(bot, user.id, f"🗑️ Tag '{name}' deleted.")


async def _tag_add(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 4:
        await _w(bot, user.id, "Usage: !tag add <tag> @username")
        return
    name = args[2].lower()
    raw  = args[3].lstrip("@").lower()
    if not db.get_tag(name):
        await _w(bot, user.id, f"❌ Tag '{name}' not found.")
        return
    result = await _resolve_user_in_room(bot, raw)
    if result:
        target, _ = result
        uid, uname = target.id, target.username.lower()
    else:
        row = db.get_subscriber(raw)
        if not row:
            await _w(bot, user.id, f"❌ Player @{raw} not found.")
            return
        uid   = row.get("user_id", "")
        uname = raw
    db.add_tag_member(name, uid, uname, user.username)
    await _w(bot, user.id, f"✅ @{uname} added to tag '{name}'.")


async def _tag_remove(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 4:
        await _w(bot, user.id, "Usage: !tag remove <tag> @username")
        return
    name  = args[2].lower()
    raw   = args[3].lstrip("@").lower()
    if not db.get_tag(name):
        await _w(bot, user.id, f"❌ Tag '{name}' not found.")
        return
    db.remove_tag_member(name, raw)
    await _w(bot, user.id, f"✅ @{raw} removed from tag '{name}'.")


async def _tag_list(bot: BaseBot, user: User, args: list[str]) -> None:
    tags = db.get_all_tags()
    if not tags:
        await _w(bot, user.id,
                 "🏷️ No tags yet.\nUse !tag create <tag>")
        return
    lines = ["🏷️ Tags"]
    for t in tags:
        note = " [editable]" if t.get("allow_member_edit") else ""
        lines.append(f"{t['tag_name']}{note}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def _tag_members(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !tag members <tag>")
        return
    name = args[2].lower()
    if not db.get_tag(name):
        await _w(bot, user.id, f"❌ Tag '{name}' not found.")
        return
    members = db.get_tag_members(name)
    if not members:
        await _w(bot, user.id, f"🏷️ Tag '{name}' has no members.")
        return
    names = ", ".join(f"@{m['username']}" for m in members)
    await _w(bot, user.id, f"🏷️ {name}: {names}"[:249])


async def _tag_spawn(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 4:
        await _w(bot, user.id,
                 "Usage: !tag spawn <tag> here\n"
                 "       !tag spawn <tag> <x> <y> <z> [facing]")
        return
    name = args[2].lower()
    if not db.get_tag(name):
        await _w(bot, user.id, f"❌ Tag '{name}' not found.")
        return
    if args[3].lower() == "here":
        my_pos = _user_positions.get(user.id)
        if not my_pos:
            await _w(bot, user.id,
                     "❌ Cannot read your position. Move first.")
            return
        x, y, z = my_pos.x, my_pos.y, my_pos.z
        facing  = getattr(my_pos, "facing", "FrontLeft")
    elif len(args) >= 6:
        try:
            x, y, z = float(args[3]), float(args[4]), float(args[5])
        except ValueError:
            await _w(bot, user.id, "x y z must be numbers.")
            return
        facing = args[6] if len(args) > 6 else "FrontLeft"
    else:
        await _w(bot, user.id,
                 "Usage: !tag spawn <tag> here\n"
                 "       !tag spawn <tag> <x> <y> <z> [facing]")
        return
    db.set_tag_spawn(name, x, y, z, facing)
    await _w(bot, user.id,
             f"✅ Tag '{name}' spawn set to ({x:.1f},{y:.1f},{z:.1f}).")


async def _tag_delspawn(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !tag delspawn <tag>")
        return
    name = args[2].lower()
    if not db.get_tag(name):
        await _w(bot, user.id, f"❌ Tag '{name}' not found.")
        return
    db.set_tag_spawn(name, None, None, None, None)
    await _w(bot, user.id, f"✅ Spawn cleared for tag '{name}'.")


async def _tag_allowedit(bot: BaseBot, user: User, args: list[str]) -> None:
    if not _can_manage(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 4 or args[3].lower() not in ("on", "off"):
        await _w(bot, user.id, "Usage: !tag allowedit <tag> on|off")
        return
    name    = args[2].lower()
    enabled = args[3].lower() == "on"
    if not db.get_tag(name):
        await _w(bot, user.id, f"❌ Tag '{name}' not found.")
        return
    db.set_tag_allow_edit(name, enabled)
    await _w(bot, user.id,
             f"✅ Tag '{name}' member spawn edit: {'ON' if enabled else 'OFF'}")


async def _tag_setspawn(bot: BaseBot, user: User, args: list[str]) -> None:
    """Member-callable if allowedit is ON; always allowed for managers."""
    if len(args) < 4 or args[3].lower() != "here":
        await _w(bot, user.id, "Usage: !tag setspawn <tag> here")
        return
    name = args[2].lower()
    tag  = db.get_tag(name)
    if not tag:
        await _w(bot, user.id, f"❌ Tag '{name}' not found.")
        return
    is_mgr = _can_manage(user.username)
    if not is_mgr:
        members   = db.get_tag_members(name)
        is_member = any(
            m["username"].lower() == user.username.lower() for m in members
        )
        if not is_member:
            await _w(bot, user.id, f"❌ You are not a member of '{name}'.")
            return
        if not tag.get("allow_member_edit"):
            await _w(bot, user.id,
                     f"❌ Member spawn editing is OFF for '{name}'.")
            return
    my_pos = _user_positions.get(user.id)
    if not my_pos:
        await _w(bot, user.id,
                 "❌ Cannot read your position. Move first.")
        return
    db.set_tag_spawn(name, my_pos.x, my_pos.y, my_pos.z,
                     getattr(my_pos, "facing", "FrontLeft"))
    await _w(bot, user.id,
             f"✅ Tag '{name}' spawn updated to your position.")


# ---------------------------------------------------------------------------
# !teleporthelp (! style)
# ---------------------------------------------------------------------------

async def handle_teleporthelp_tele(bot: BaseBot, user: User) -> None:
    await _w(bot, user.id,
             "🌀 Teleport Help\n"
             "!tele list\n"
             "!tele <spot>\n"
             "!tele @username\n"
             "!tele @username <x> <y> <z>\n"
             "!summon @username\n"
             "Staff:\n"
             "!create tele <spot>\n"
             "!delete tele <spot>\n"
             "!tele permission <spot> level\n"
             "!tele role <role> <spot>")
    await _w(bot, user.id,
             "🏷️ Tags:\n"
             "!tag create <tag>\n"
             "!tag add <tag> @user\n"
             "!tag spawn <tag> here\n"
             "!tele tag <tag>\n"
             "Role Spawns:\n"
             "!setrolespawn <role> here\n"
             "!rolespawns\n"
             "!rolespawn <role>\n"
             "!delrolespawn <role>")
