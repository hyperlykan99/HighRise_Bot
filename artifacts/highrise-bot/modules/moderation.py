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
from modules.permissions import can_moderate, can_manage_games, can_manage_economy, is_admin, is_owner
from modules.automod import reset_tracker, get_tracker_status, automod_offense_count
from modules.safety import _log_mod_action


def _get_mod_setting(key: str, default: str = "") -> str:
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT value FROM moderation_settings WHERE key = ?", (key,)
        ).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default


def _set_mod_setting(key: str, value: str) -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO moderation_settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[MOD] set_mod_setting error: {exc!r}")


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


# ---------------------------------------------------------------------------
# /mute <username> <minutes>   (manager+)
# ---------------------------------------------------------------------------

async def handle_mute(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !mute @user <minutes> [reason]")
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

    reason_str = " ".join(args[3:]).strip() if len(args) > 3 else ""
    db.mute_user(
        user_id        = target["user_id"],
        username       = target["username"],
        muted_by       = user.username,
        duration_minutes = minutes,
    )
    _log_mod_action(
        staff_id=user.id, staff_name=user.username,
        target_id=target["user_id"], target_name=target["username"],
        action="mute", reason=reason_str or "muted by staff",
        duration_minutes=minutes,
    )
    name = target["username"][:15]
    rsn  = reason_str or "muted by staff"
    await _w(bot, user.id,
             f"🔇 Muted\n@{name} for {minutes}m\nReason: {rsn[:60]}")
    try:
        await _w(bot, target["user_id"],
                 f"🔇 Your bot commands are muted for {minutes}m.\n"
                 f"Reason: {rsn[:60]}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /unmute <username>   (manager+)
# Clears ALL mute sources: DB mute, automod in-memory tracker, automod warns.
# ---------------------------------------------------------------------------

async def handle_unmute(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !unmute <username>")
        return

    target_name = args[1].lstrip("@").strip()
    target = db.get_user_by_username(target_name)
    if target is None:
        await _w(bot, user.id, f"@{target_name} not found.")
        return

    uid   = target["user_id"]
    uname = target["username"]

    db_removed = db.unmute_user(uid)
    am_warns   = db.clear_automod_warnings(uname)
    reset_tracker(uid)

    await _w(bot, user.id,
             f"🔊 Unmuted\n@{uname[:15]} can use bot commands again.")
    try:
        await _w(bot, uid,
                 "🔊 Your mute has been lifted. Bot commands restored.")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /mutestatus <username>   (manager+)
# Shows all active mute sources for a user.
# ---------------------------------------------------------------------------

async def handle_mutestatus(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !mutestatus <username>")
        return

    target_name = args[1].lstrip("@").strip()
    target = db.get_user_by_username(target_name)
    if target is None:
        await _w(bot, user.id, f"@{target_name} not found.")
        return

    uid   = target["user_id"]
    uname = target["username"]

    mute    = db.get_active_mute(uid)
    warns   = automod_offense_count(uname)
    tracker = get_tracker_status(uid)

    if mute:
        by       = mute.get("muted_by", "?")[:12]
        mute_str = f"YES — {mute['mins_left']}m left (by {by})"
    else:
        mute_str = "none"

    tracker_str = f"{tracker['cmd_count']} cmds/30s" if tracker["active"] else "clear"

    msg = (
        f"📋 @{uname[:15]} mute status:\n"
        f"DB mute: {mute_str}\n"
        f"AutoMod warns: {warns}\n"
        f"Tracker: {tracker_str}"
    )
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /forceunmute <username>   (admin+)
# Nuclear option — clears all mute sources.  Same as enhanced /unmute but
# requires admin rank so it can override mutes issued by managers.
# ---------------------------------------------------------------------------

async def handle_forceunmute(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "🔒 Admin only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !forceunmute <username>")
        return

    target_name = args[1].lstrip("@").strip()
    target = db.get_user_by_username(target_name)
    if target is None:
        await _w(bot, user.id, f"@{target_name} not found.")
        return

    uid   = target["user_id"]
    uname = target["username"]

    db_removed = db.unmute_user(uid)
    am_warns   = db.clear_automod_warnings(uname)
    reset_tracker(uid)

    parts = []
    if db_removed:
        parts.append("DB mute ✓")
    if am_warns:
        parts.append(f"{am_warns} AM warn(s) ✓")
    parts.append("AM tracker ✓")
    cleared = " | ".join(parts)
    await _w(bot, user.id, f"🔊 @{uname[:15]} force-unmuted. Cleared: {cleared}")


# ---------------------------------------------------------------------------
# /mutes   (manager+)
# ---------------------------------------------------------------------------

async def handle_mutes(bot: BaseBot, user: User) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
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
        await _w(bot, user.id, "🔒 Staff only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !warn @user [reason]")
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
    _log_mod_action(
        staff_id=user.id, staff_name=user.username,
        target_id=target["user_id"], target_name=target["username"],
        action="warn", reason=reason,
    )
    name = target["username"][:15]
    rsn  = reason[:60]
    await _w(bot, user.id,
             f"⚠️ Warning Added\n@{name} ({total} total)\nReason: {rsn}")
    try:
        await _w(bot, target["user_id"],
                 f"⚠️ You received a warning.\nReason: {rsn}\n"
                 f"Use !rules to review room rules.")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /warnings <username>   (mod+)
# ---------------------------------------------------------------------------

async def handle_warnings(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !warnings <username>")
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
        await _w(bot, user.id, "🔒 Admin only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !clearwarnings <username>")
        return

    target_name = args[1].lstrip("@").strip()
    cleared = db.clear_warnings(target_name)
    if cleared:
        await _w(bot, user.id, f"✅ Cleared {cleared} warning(s) for @{target_name}.")
    else:
        await _w(bot, user.id, f"@{target_name} has no warnings to clear.")


# ---------------------------------------------------------------------------
# /rules   (public)
# ---------------------------------------------------------------------------

async def handle_rules(bot: BaseBot, user: User) -> None:
    rules = _get_mod_setting(
        "room_rules",
        "📜 Rules: Be respectful. No spam. No scams. Staff decisions are final.",
    )
    await _w(bot, user.id, rules[:249])


# ---------------------------------------------------------------------------
# /setrules <message>   (admin+)
# ---------------------------------------------------------------------------

async def handle_setrules(bot: BaseBot, user: User, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !setrules <message>")
        return

    new_rules = " ".join(args[1:]).strip()
    if not new_rules:
        await _w(bot, user.id, "Rules message cannot be empty.")
        return
    if len(new_rules) > 220:
        await _w(bot, user.id, "Rules message too long. Max 220 characters.")
        return

    _set_mod_setting("room_rules", new_rules)
    await _w(bot, user.id, f"✅ Room rules updated: {new_rules[:80]}...")


# ---------------------------------------------------------------------------
# /automod [on|off]   (manager+)
# ---------------------------------------------------------------------------

async def handle_automod(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return

    if len(args) < 2:
        # Show current status
        enabled   = _get_mod_setting("automod_enabled", "1") == "1"
        max_cmds  = _get_mod_setting("max_commands", "8")
        max_same  = _get_mod_setting("max_same_message", "3")
        max_rep   = _get_mod_setting("max_reports", "3")
        status    = "ON" if enabled else "OFF"
        await _w(bot, user.id,
                 f"🛡️ AutoMod: {status}\n"
                 f"Max cmds/30s: {max_cmds} | Same msg: {max_same}\n"
                 f"Max reports/10m: {max_rep}\n"
                 f"Use !automod on or /automod off")
        return

    sub = args[1].lower().strip()
    if sub == "on":
        _set_mod_setting("automod_enabled", "1")
        await _w(bot, user.id, "🛡️ AutoMod enabled.")
    elif sub == "off":
        _set_mod_setting("automod_enabled", "0")
        await _w(bot, user.id, "🛡️ AutoMod disabled.")
    else:
        await _w(bot, user.id, "Usage: !automod on | /automod off")
