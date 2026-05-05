"""
admin.py
--------
Admin commands shared across all bot modes.

Any bot that needs admin functionality imports handle_admin_command from here.
Checking whether the caller is an admin is done in main.py (or each bot's
entry point) before calling these functions — this module does not re-check.

Commands handled:
  /addcoins    <username> <amount>  — give coins to any player
  /removecoins <username> <amount>  — take coins from any player
  /resetgame                        — clear any stuck active game
  /announce    <message>            — post a room-wide announcement

To add a new admin command:
  1. Write an async function here (e.g. async def _cmd_ban(...))
  2. Add it as a branch in handle_admin_command()
  3. Add the command name to ADMIN_COMMANDS in your bot's main.py
"""

from highrise import BaseBot, User
import database as db
import games   # needed so /resetgame can clear active game state


async def handle_admin_command(bot: BaseBot, user: User, cmd: str, args: list[str]):
    """
    Dispatch an admin command to the correct private handler.

    Parameters
    ----------
    cmd  : the command name in lowercase
    args : the full parsed argument list (args[0] == cmd)
    """
    if cmd == "addcoins":
        await _cmd_addcoins(bot, user, args)

    elif cmd == "removecoins":
        await _cmd_removecoins(bot, user, args)

    elif cmd == "resetgame":
        await _cmd_resetgame(bot, user)

    elif cmd == "announce":
        await _cmd_announce(bot, user, args)


# ---------------------------------------------------------------------------
# Private handlers
# ---------------------------------------------------------------------------

async def _cmd_addcoins(bot: BaseBot, user: User, args: list[str]):
    """
    Give coins to a registered player.
    Usage: /addcoins <username> <amount>
    """
    # args = ["addcoins", "playername", "100"]
    if len(args) < 3 or not args[2].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: /addcoins <username> <amount>")
        return

    target_username = args[1]
    amount          = int(args[2])

    if amount <= 0:
        await bot.highrise.send_whisper(user.id, "Amount must be greater than 0.")
        return

    target = db.get_user_by_username(target_username)
    if not target:
        await bot.highrise.send_whisper(
            user.id,
            f"Player '{target_username}' not found. They need to chat in the room first."
        )
        return

    db.adjust_balance(target["user_id"], amount)
    new_balance = db.get_balance(target["user_id"])

    await bot.highrise.send_whisper(
        user.id,
        f"Added {amount} coins to {target['username']}. Their balance is now {new_balance}."
    )


async def _cmd_removecoins(bot: BaseBot, user: User, args: list[str]):
    """
    Remove coins from a registered player. Balance is clamped to 0.
    Usage: /removecoins <username> <amount>
    """
    if len(args) < 3 or not args[2].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: /removecoins <username> <amount>")
        return

    target_username = args[1]
    amount          = int(args[2])

    if amount <= 0:
        await bot.highrise.send_whisper(user.id, "Amount must be greater than 0.")
        return

    target = db.get_user_by_username(target_username)
    if not target:
        await bot.highrise.send_whisper(
            user.id,
            f"Player '{target_username}' not found."
        )
        return

    db.adjust_balance(target["user_id"], -amount)
    new_balance = db.get_balance(target["user_id"])

    await bot.highrise.send_whisper(
        user.id,
        f"Removed {amount} coins from {target['username']}. Their balance is now {new_balance}."
    )


async def _cmd_resetgame(bot: BaseBot, user: User):
    """
    Clear all active mini-game state.
    Useful if a game got stuck and nobody can start a new one.
    """
    games.reset_all_games()
    await bot.highrise.chat("[Admin] All active games have been reset.")


async def _cmd_announce(bot: BaseBot, user: User, args: list[str]):
    """
    Post a public announcement to the room.
    Usage: /announce <message>
    """
    message_text = " ".join(args[1:]).strip()
    if not message_text:
        await bot.highrise.send_whisper(user.id, "Usage: /announce <message>")
        return

    await bot.highrise.chat(f"[Announcement] {message_text}")
