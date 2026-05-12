"""
modules/display_settings.py
----------------------------
Player name display-format settings commands.

Allows staff to toggle whether equipped badge emojis and title labels
appear when the bot mentions a player's name in any message.

Changes take effect immediately (cache is cleared on each /set command).

Commands:
  /displaybadges on|off  — show or hide badge emoji in player name display
  /displaytitles on|off  — show or hide title in player name display
  /displayformat         — show current display settings and live preview

Owner: host
Permission: manager+
"""

import database as db
from modules.permissions import is_manager, is_admin, is_owner


def _get_user_row(username: str):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT user_id, username, equipped_badge, equipped_title "
        "FROM users WHERE LOWER(username) = LOWER(?)",
        (username,)
    ).fetchone()
    conn.close()
    return row


def _is_manager_plus(username: str) -> bool:
    return is_manager(username) or is_admin(username) or is_owner(username)


async def _w(bot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


async def handle_displaybadges(bot, user, args: list[str]) -> None:
    """/displaybadges on|off — show or hide badge emoji in player names."""
    if not _is_manager_plus(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub in ("on", "true", "enable", "1"):
        db.set_room_setting("display_badges_enabled", "true")
        db.invalidate_display_cache()
        await _w(bot, user.id,
            "✅ Badges ON. Equipped badge emojis will show in player names.")
    elif sub in ("off", "false", "disable", "0"):
        db.set_room_setting("display_badges_enabled", "false")
        db.invalidate_display_cache()
        await _w(bot, user.id,
            "✅ Badges OFF. Player names will show without badge emojis.")
    else:
        cur = db.get_room_setting("display_badges_enabled", "true")
        state = "ON" if cur == "true" else "OFF"
        await _w(bot, user.id,
            f"Badge display is currently {state}. Usage: !displaybadges on | off")


async def handle_displaytitles(bot, user, args: list[str]) -> None:
    """/displaytitles on|off — show or hide title labels in player names."""
    if not _is_manager_plus(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    sub = args[1].lower() if len(args) > 1 else ""
    if sub in ("on", "true", "enable", "1"):
        db.set_room_setting("display_titles_enabled", "true")
        db.invalidate_display_cache()
        await _w(bot, user.id,
            "✅ Titles ON. Equipped title labels will show in player names.")
    elif sub in ("off", "false", "disable", "0"):
        db.set_room_setting("display_titles_enabled", "false")
        db.invalidate_display_cache()
        await _w(bot, user.id,
            "✅ Titles OFF. Player names will show without titles.")
    else:
        cur = db.get_room_setting("display_titles_enabled", "true")
        state = "ON" if cur == "true" else "OFF"
        await _w(bot, user.id,
            f"Title display is currently {state}. Usage: !displaytitles on | off")


async def handle_displayformat(bot, user, args: list[str]) -> None:
    """/displayformat — show current player-name display configuration."""
    if not _is_manager_plus(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    badges = db.get_room_setting("display_badges_enabled", "true") == "true"
    titles = db.get_room_setting("display_titles_enabled", "true") == "true"
    b_state = "ON" if badges else "OFF"
    t_state = "ON" if titles else "OFF"
    if badges and titles:
        fmt = "<badge> [title] @username"
    elif badges:
        fmt = "<badge> @username"
    elif titles:
        fmt = "[title] @username"
    else:
        fmt = "@username"
    await _w(bot, user.id,
        f"📛 Display Format — Badge: {b_state} | Title: {t_state}")
    await _w(bot, user.id, f"Format: {fmt}")
    await _w(bot, user.id,
        "Change: /displaybadges on|off | /displaytitles on|off")


async def handle_displaytest(bot, user, args: list[str]) -> None:
    """/displaytest <username> — show how a player's name is formatted."""
    if not _is_manager_plus(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    target = args[1].lstrip("@").strip() if len(args) > 1 else user.username
    row = _get_user_row(target)
    if row is None:
        await _w(bot, user.id,
            f"No record for @{target}. Player must chat in room first.")
        return
    raw_badge  = row["equipped_badge"] or "(none)"
    raw_title  = (row["equipped_title"] or "(none)").strip("[]")
    badges_on  = db.get_room_setting("display_badges_enabled", "true") == "true"
    titles_on  = db.get_room_setting("display_titles_enabled", "true") == "true"
    display    = db.get_display_name(row["user_id"], row["username"])
    b_state    = "ON" if badges_on else "OFF"
    t_state    = "ON" if titles_on else "OFF"
    await _w(bot, user.id, f"Raw: @{row['username']}")
    await _w(bot, user.id, f"Badge: {raw_badge} | Title: {raw_title}")
    await _w(bot, user.id, f"Badges: {b_state} | Titles: {t_state}")
    await _w(bot, user.id, f"Display: {display}")
