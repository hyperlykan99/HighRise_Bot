"""
modules/moderation.py
---------------------
Moderation actions for the Highrise Mini Game Bot.

Commands:

  Manager+ (can_manage_games):
    /mute <username> <minutes>   — silence a player for N minutes
    /unmute <username>           — remove an active mute
    /mutes                       — list all active mutes

  Moderator+ (can_moderate):
    /warn <username> <reason>    — add a warning to a player
    /warnings <username>         — show a player's warning history

  Admin+ (can_manage_economy):
    /clearwarnings <username>    — delete all warnings for a player

All messages ≤ 249 characters.
"""

from highrise import BaseBot, User

import database as db
from modules.permissions import can_moderate, can_manage_games, can_manage_economy


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


# ---------------------------------------------------------------------------
# /mute <username> <minutes>   (manager+)
# ---------------------------------------------------------------------------

async def handle_mute(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /mute <username> <minutes>")
        return

    target_name = args[1].lstrip("@").strip()
    if not args[2].isdigit() or int(args[2]) < 1:
        await _w(bot, user.id, "Minutes must be a positive number.")
        return

    minutes = int(args[2])
    if minutes > 10080:  # cap at 7 days
        await _w(bot, user.id, "Max mute duration is 10080 min (7 days).")
        return
    if target_name.lower() == user.username.lower():
        await _w(bot, user.id, "You cannot mute yourself.")
        return

    target = db.get_user_by_username(target_name)
    if target is None:
        await _w(bot, user.id, f"@{target_name} not found.")
        return

    db.mute_user(
        user_id        = target["user_id"],
        username       = target["username"],
        muted_by       = user.username,
        duration_minutes = minutes,
    )
    await _w(bot, user.id, f"🔇 @{target['username']} muted for {minutes} min.")


# ---------------------------------------------------------------------------
# /unmute <username>   (manager+)
# ---------------------------------------------------------------------------

async def handle_unmute(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /unmute <username>")
        return

    target_name = args[1].lstrip("@").strip()
    target = db.get_user_by_username(target_name)
    if target is None:
        await _w(bot, user.id, f"@{target_name} not found.")
        return

    removed = db.unmute_user(target["user_id"])
    if removed:
        await _w(bot, user.id, f"🔊 @{target['username']} unmuted.")
    else:
        await _w(bot, user.id, f"@{target['username']} is not muted.")


# ---------------------------------------------------------------------------
# /mutes   (manager+)
# ---------------------------------------------------------------------------

async def handle_mutes(bot: BaseBot, user: User) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return

    rows = db.get_all_active_mutes(limit=5)
    if not rows:
        await _w(bot, user.id, "🔇 No active mutes.")
        return

    lines = [f"🔇 Active mutes ({len(rows)}):"]
    for r in rows:
        lines.append(f"@{r['username'][:15]} — {r['mins_left']}m left")
    await _w(bot, user.id, "\n".join(lines))


# ---------------------------------------------------------------------------
# /warn <username> <reason>   (mod+)
# ---------------------------------------------------------------------------

async def handle_warn(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: /warn <username> <reason>")
        return

    target_name = args[1].lstrip("@").strip()
    reason      = " ".join(args[2:])[:100].strip()

    if not reason:
        await _w(bot, user.id, "Reason cannot be empty.")
        return
    if target_name.lower() == user.username.lower():
        await _w(bot, user.id, "You cannot warn yourself.")
        return

    target = db.get_user_by_username(target_name)
    if target is None:
        await _w(bot, user.id, f"@{target_name} not found.")
        return

    total = db.add_warning(
        user_id   = target["user_id"],
        username  = target["username"],
        warned_by = user.username,
        reason    = reason,
    )
    name  = target["username"][:15]
    rsn   = reason[:60]
    await _w(bot, user.id, f"⚠️ @{name} warned ({total} total). Reason: {rsn}")


# ---------------------------------------------------------------------------
# /warnings <username>   (mod+)
# ---------------------------------------------------------------------------

async def handle_warnings(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /warnings <username>")
        return

    target_name = args[1].lstrip("@").strip()
    rows, total = db.get_warnings(target_name, limit=5)

    if total == 0:
        await _w(bot, user.id, f"@{target_name} has no warnings.")
        return

    lines = [f"⚠️ @{target_name[:15]} — {total} warning(s):"]
    for r in rows:
        by  = r["warned_by"][:10]
        rsn = r["reason"][:40]
        lines.append(f"#{r['id']} {rsn} (by @{by})")
    await _w(bot, user.id, "\n".join(lines))


# ---------------------------------------------------------------------------
# /clearwarnings <username>   (admin+)
# ---------------------------------------------------------------------------

async def handle_clearwarnings(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /clearwarnings <username>")
        return

    target_name = args[1].lstrip("@").strip()
    cleared = db.clear_warnings(target_name)
    if cleared:
        await _w(bot, user.id, f"✅ Cleared {cleared} warning(s) for @{target_name}.")
    else:
        await _w(bot, user.id, f"@{target_name} has no warnings to clear.")
