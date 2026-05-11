"""
modules/economy_audit.py
------------------------
Economy audit and game price management commands.

Commands (manager+):
  !economyaudit [games|prices|risks|rewards]
  !gameprices
  !gameprice [game]
  !setgameprice [game] [setting] [value]   — admin/owner only
  !auditlog [economy|gold|rewards|commands] — staff
  !messageaudit [slash|help]               — manager+
"""
from __future__ import annotations

import database as db
from modules.permissions import is_owner, is_admin, is_manager, can_moderate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


def _can_audit(username: str) -> bool:
    return is_owner(username) or is_admin(username) or is_manager(username)


# Known games and their configurable settings
_GAME_SETTINGS: dict[str, list[str]] = {
    "blackjack": ["minbet", "maxbet", "countdown", "turntimer",
                  "dailywinlimit", "dailylosslimit"],
    "rbj":       ["minbet", "maxbet", "decks", "shuffle",
                  "dailywinlimit", "dailylosslimit"],
    "poker":     ["buyin", "blinds", "ante", "maxstack",
                  "dailywinlimit", "dailylosslimit"],
    "mining":    ["cooldown", "basepay"],
    "fishing":   ["cooldown", "basepay"],
}


# ---------------------------------------------------------------------------
# !economyaudit
# ---------------------------------------------------------------------------

async def handle_economyaudit(bot, user, args: list[str]) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else ""

    if sub == "games":
        lines = ["💰 Economy Audit — Games"]
        for game in _GAME_SETTINGS:
            prices = db.get_all_game_prices(game)
            if prices:
                parts = ", ".join(
                    f"{p['setting']}={p['value']}" for p in prices[:3]
                )
            else:
                parts = "defaults"
            lines.append(f"{game}: {parts}")
        await _w(bot, user.id, "\n".join(lines)[:249])

    elif sub in ("prices", "price"):
        all_prices = db.get_all_game_prices()
        if not all_prices:
            await _w(bot, user.id,
                     "💰 Economy Audit — Prices\n"
                     "No custom prices set. All games at defaults.")
            return
        lines = ["💰 Custom Game Prices"]
        for p in all_prices[:8]:
            lines.append(f"{p['game']} {p['setting']}: {p['value']}")
        await _w(bot, user.id, "\n".join(lines)[:249])

    elif sub in ("risks", "risk"):
        await _w(bot, user.id,
                 "💰 Economy Risk Areas\n"
                 "• BJ max bet vs daily loss limit\n"
                 "• Gold rain total vs winner count\n"
                 "• Daily reward vs max balance\n"
                 "Use !gameprices to review all settings")

    elif sub == "rewards":
        await _w(bot, user.id,
                 "💰 Reward Audit\n"
                 "!rewardlogs — recent payouts\n"
                 "!pendinggold — pending gold tips\n"
                 "!auditlog rewards — reward action log")

    else:
        await _w(bot, user.id,
                 "💰 Economy Audit\n"
                 "!economyaudit games\n"
                 "!economyaudit prices\n"
                 "!economyaudit risks\n"
                 "!economyaudit rewards\n"
                 "!gameprices\n"
                 "!auditlog economy")


# ---------------------------------------------------------------------------
# !gameprices
# ---------------------------------------------------------------------------

async def handle_gameprices(bot, user, args: list[str]) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    lines = ["🎮 Game Prices"]
    for game in _GAME_SETTINGS:
        prices  = db.get_all_game_prices(game)
        if prices:
            summary = ", ".join(f"{p['setting']}={p['value']}" for p in prices[:2])
        else:
            summary = "defaults"
        lines.append(f"  {game}: {summary}")
    lines.append("Use !gameprice [game] for details")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !gameprice [game]
# ---------------------------------------------------------------------------

async def handle_gameprice(bot, user, args: list[str]) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    if len(args) < 2:
        games = ", ".join(_GAME_SETTINGS)
        await _w(bot, user.id,
                 f"Usage: !gameprice [game]\n"
                 f"Games: {games}")
        return

    game   = args[1].lower()
    if game not in _GAME_SETTINGS:
        await _w(bot, user.id,
                 f"❌ Unknown game '{game}'.\n"
                 f"Games: {', '.join(_GAME_SETTINGS)}")
        return

    prices   = db.get_all_game_prices(game)
    price_map = {p["setting"]: p["value"] for p in prices}
    settings  = _GAME_SETTINGS[game]
    lines     = [f"🎮 {game} Settings"]
    for s in settings:
        val = price_map.get(s, "(default)")
        lines.append(f"  {s}: {val}")
    lines.append(f"Change: !setgameprice {game} [setting] [value]")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# !setgameprice [game] [setting] [value]
# ---------------------------------------------------------------------------

async def handle_setgameprice(bot, user, args: list[str]) -> None:
    if not (is_owner(user.username) or is_admin(user.username)):
        await _w(bot, user.id, "Admin/owner only.")
        return

    if len(args) < 4:
        await _w(bot, user.id,
                 "Usage: !setgameprice [game] [setting] [value]\n"
                 "Example: !setgameprice blackjack minbet 10")
        return

    game    = args[1].lower()
    setting = args[2].lower()
    raw_val = args[3]

    if game not in _GAME_SETTINGS:
        await _w(bot, user.id,
                 f"❌ Unknown game '{game}'.\n"
                 f"Games: {', '.join(_GAME_SETTINGS)}")
        return

    valid_settings = _GAME_SETTINGS[game]
    if setting not in valid_settings:
        await _w(bot, user.id,
                 f"❌ Unknown setting '{setting}' for {game}.\n"
                 f"Valid: {', '.join(valid_settings)}")
        return

    try:
        value = int(raw_val)
        if value < 0:
            raise ValueError("negative")
    except ValueError:
        await _w(bot, user.id, "❌ Value must be a non-negative whole number.")
        return

    old_price = db.get_game_price(game, setting, None)
    db.set_game_price(game, setting, value, user.username)
    db.log_economy_action(
        actor_username=user.username,
        action_type="setgameprice",
        game=game,
        setting=setting,
        old_value=str(old_price) if old_price is not None else "default",
        new_value=str(value),
    )

    old_str = str(old_price) if old_price is not None else "default"
    await _w(bot, user.id,
             f"✅ {game} {setting}: {old_str} → {value}\n"
             f"Change logged to audit log.")


# ---------------------------------------------------------------------------
# !messageaudit
# ---------------------------------------------------------------------------

async def handle_messageaudit(bot, user, args: list[str]) -> None:
    if not _can_audit(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else ""

    if sub == "slash":
        await _w(bot, user.id,
                 "🧾 Slash Audit\n"
                 "All help pages use ! prefix only.\n"
                 "Run !staffhelp and !help to verify.\n"
                 "Unknown cmd messages use !help.")

    elif sub == "help":
        await _w(bot, user.id,
                 "🧾 Help Audit Checks\n"
                 "• Help uses ! prefix only\n"
                 "• Commands show [arg] placeholders\n"
                 "• No staff cmds shown to players\n"
                 "• All pages ≤ 249 chars\n"
                 "• No unregistered commands listed")

    else:
        await _w(bot, user.id,
                 "🧾 Message Audit\n"
                 "!messageaudit slash — / prefix check\n"
                 "!messageaudit help — help page audit\n"
                 "!msgcap — check message cap limits\n"
                 "!commandissues — unrouted commands")
