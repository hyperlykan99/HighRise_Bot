"""
modules/casino_settings.py
Casino Control Panel for the Highrise Mini Game Bot.

Commands (manager / admin / owner only):
  /casinosettings          — send overview page 1 + page 2
  /casinosettings 1        — page 1: enabled status + bet ranges (both games)
  /casinosettings 2        — page 2: win/loss limits + timers (both games)
  /casinosettings bj       — Casual BJ detail only
  /casinosettings rbj      — Realistic BJ detail only
  /casinolimits            — bet ranges + daily win/loss limits at a glance
  /casinotoggles           — enabled + win/loss limit toggle states
  /casinotoggles <bj|rbj> <winlimit|losslimit> <on|off>  — set a toggle
  /setbjlimits  <minbet> <maxbet> <winlimit> <losslimit>
  /setrbjlimits <minbet> <maxbet> <winlimit> <losslimit>

All messages are kept under 250 characters.
All settings are read from and written to SQLite via database.py.
/bj settings and /rbj settings read from the same DB functions.
"""
from __future__ import annotations

from highrise import BaseBot, User

import database as db
from modules.permissions import can_manage_games


# ── helpers ───────────────────────────────────────────────────────────────────

def _w(bot: BaseBot, uid: str, msg: str):
    return bot.highrise.send_whisper(uid, msg)


def _on(val) -> str:
    return "ON" if int(val) else "OFF"


def _fmt(n) -> str:
    """Format integer without commas (short style for compact messages)."""
    return str(int(n))


# ── overview pages ────────────────────────────────────────────────────────────

def _page1(bj: dict, rbj: dict) -> str:
    """
    Page 1: enabled status + bet ranges for both games.
    Example:
      🎰 Casino 1
      BJ: ON | RBJ: ON
      BJ bet: 10-1000c
      RBJ bet: 50-5000c
    """
    return (
        f"🎰 Casino 1\n"
        f"BJ: {_on(bj.get('bj_enabled', 1))} | "
        f"RBJ: {_on(rbj.get('rbj_enabled', 1))}\n"
        f"BJ bet: {_fmt(bj.get('min_bet', 10))}-{_fmt(bj.get('max_bet', 1000))}c\n"
        f"RBJ bet: {_fmt(rbj.get('min_bet', 10))}-{_fmt(rbj.get('max_bet', 1000))}c"
    )


def _page2(bj: dict, rbj: dict) -> str:
    """
    Page 2: daily win/loss limits + turn timers for both games.
    Example:
      🎰 Casino 2
      BJ W/L: 5000/3000
      RBJ W/L: 10000/5000
      Timers: BJ 20s | RBJ 20s
    """
    return (
        f"🎰 Casino 2\n"
        f"BJ W/L: {_fmt(bj.get('bj_daily_win_limit', 5000))}/"
        f"{_fmt(bj.get('bj_daily_loss_limit', 3000))}\n"
        f"RBJ W/L: {_fmt(rbj.get('rbj_daily_win_limit', 5000))}/"
        f"{_fmt(rbj.get('rbj_daily_loss_limit', 3000))}\n"
        f"Timers: BJ {bj.get('bj_turn_timer', 20)}s | "
        f"RBJ {rbj.get('rbj_turn_timer', 20)}s"
    )


# ── /casinosettings [1|2|bj|rbj] ──────────────────────────────────────────────

async def handle_casinosettings(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return

    sub = args[1].lower() if len(args) > 1 else ""
    bj  = db.get_bj_settings()
    rbj = db.get_rbj_settings()

    if sub == "1":
        await _w(bot, user.id, _page1(bj, rbj))

    elif sub == "2":
        await _w(bot, user.id, _page2(bj, rbj))

    elif sub == "bj":
        # Casual BJ detail view
        await _w(bot, user.id,
            f"🎰 Casual BJ\n"
            f"Status: {_on(bj.get('bj_enabled', 1))} | "
            f"Timer: {bj.get('bj_turn_timer', 20)}s\n"
            f"Bet: {_fmt(bj.get('min_bet', 10))}c-{_fmt(bj.get('max_bet', 1000))}c\n"
            f"Win cap: {_fmt(bj.get('bj_daily_win_limit', 5000))}c "
            f"({_on(bj.get('bj_win_limit_enabled', 1))}) | "
            f"Loss cap: {_fmt(bj.get('bj_daily_loss_limit', 3000))}c "
            f"({_on(bj.get('bj_loss_limit_enabled', 1))})"
        )

    elif sub == "rbj":
        # Realistic BJ detail view
        await _w(bot, user.id,
            f"🎰 Realistic BJ\n"
            f"Status: {_on(rbj.get('rbj_enabled', 1))} | "
            f"Timer: {rbj.get('rbj_turn_timer', 20)}s\n"
            f"Bet: {_fmt(rbj.get('min_bet', 10))}c-{_fmt(rbj.get('max_bet', 1000))}c\n"
            f"Win cap: {_fmt(rbj.get('rbj_daily_win_limit', 5000))}c "
            f"({_on(rbj.get('rbj_win_limit_enabled', 1))}) | "
            f"Loss cap: {_fmt(rbj.get('rbj_daily_loss_limit', 3000))}c "
            f"({_on(rbj.get('rbj_loss_limit_enabled', 1))})"
        )

    else:
        # No argument: send both overview pages
        await _w(bot, user.id, _page1(bj, rbj))
        await _w(bot, user.id, _page2(bj, rbj))


# ── /casinolimits ──────────────────────────────────────────────────────────────

async def handle_casinolimits(bot: BaseBot, user: User) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return

    bj  = db.get_bj_settings()
    rbj = db.get_rbj_settings()
    await _w(bot, user.id,
        f"🎰 Limits\n"
        f"BJ bet {_fmt(bj.get('min_bet', 10))}-{_fmt(bj.get('max_bet', 1000))} | "
        f"W/L {_fmt(bj.get('bj_daily_win_limit', 5000))}/"
        f"{_fmt(bj.get('bj_daily_loss_limit', 3000))}\n"
        f"RBJ bet {_fmt(rbj.get('min_bet', 10))}-{_fmt(rbj.get('max_bet', 1000))} | "
        f"W/L {_fmt(rbj.get('rbj_daily_win_limit', 5000))}/"
        f"{_fmt(rbj.get('rbj_daily_loss_limit', 3000))}"
    )


# ── /casinotoggles [bj|rbj] [winlimit|losslimit] [on|off] ─────────────────────

async def handle_casinotoggles(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return

    # Setter mode: /casinotoggles <bj|rbj> <winlimit|losslimit> <on|off>
    if len(args) >= 4:
        game   = args[1].lower()
        toggle = args[2].lower()
        state  = args[3].lower()
        if state not in ("on", "off"):
            await _w(bot, user.id,
                "Usage: /casinotoggles <bj|rbj> <winlimit|losslimit> <on|off>")
            return
        val = 1 if state == "on" else 0
        if game == "bj":
            if toggle in ("winlimit", "win"):
                db.set_bj_setting("bj_win_limit_enabled", val)
                await _w(bot, user.id, f"✅ BJ win limit: {state.upper()}.")
            elif toggle in ("losslimit", "loss"):
                db.set_bj_setting("bj_loss_limit_enabled", val)
                await _w(bot, user.id, f"✅ BJ loss limit: {state.upper()}.")
            else:
                await _w(bot, user.id,
                    "Toggle: winlimit or losslimit. "
                    "Example: /casinotoggles bj winlimit off")
        elif game == "rbj":
            if toggle in ("winlimit", "win"):
                db.set_rbj_setting("rbj_win_limit_enabled", val)
                await _w(bot, user.id, f"✅ RBJ win limit: {state.upper()}.")
            elif toggle in ("losslimit", "loss"):
                db.set_rbj_setting("rbj_loss_limit_enabled", val)
                await _w(bot, user.id, f"✅ RBJ loss limit: {state.upper()}.")
            else:
                await _w(bot, user.id,
                    "Toggle: winlimit or losslimit. "
                    "Example: /casinotoggles rbj losslimit on")
        else:
            await _w(bot, user.id,
                "Game must be bj or rbj. "
                "Example: /casinotoggles bj winlimit off")
        return

    # Viewer mode
    bj  = db.get_bj_settings()
    rbj = db.get_rbj_settings()
    bj_win  = _on(bj.get("bj_win_limit_enabled",  1))
    bj_loss = _on(bj.get("bj_loss_limit_enabled", 1))
    rbj_win  = _on(rbj.get("rbj_win_limit_enabled",  1))
    rbj_loss = _on(rbj.get("rbj_loss_limit_enabled", 1))
    await _w(bot, user.id,
        f"🎰 Toggles\n"
        f"BJ {_on(bj.get('bj_enabled', 1))} | RBJ {_on(rbj.get('rbj_enabled', 1))}\n"
        f"BJ win/loss {bj_win}/{bj_loss}\n"
        f"RBJ win/loss {rbj_win}/{rbj_loss}"
    )


# ── /setbjlimits <minbet> <maxbet> <winlimit> <losslimit> ─────────────────────

async def handle_setbjlimits(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return

    if len(args) < 5 or not all(a.isdigit() for a in args[1:5]):
        await _w(bot, user.id,
            "Usage: /setbjlimits <minbet> <maxbet> <winlimit> <losslimit>\n"
            "Example: /setbjlimits 10 1000 5000 3000")
        return

    minbet  = int(args[1])
    maxbet  = int(args[2])
    winlim  = int(args[3])
    losslim = int(args[4])

    errors: list[str] = []
    if minbet < 1:      errors.append("Min bet must be ≥ 1.")
    if maxbet < minbet: errors.append("Max bet must be ≥ min bet.")
    if winlim < 1:      errors.append("Win limit must be > 0.")
    if losslim < 1:     errors.append("Loss limit must be > 0.")
    if errors:
        await _w(bot, user.id, " ".join(errors))
        return

    db.set_bj_setting("min_bet",             minbet)
    db.set_bj_setting("max_bet",             maxbet)
    db.set_bj_setting("bj_daily_win_limit",  winlim)
    db.set_bj_setting("bj_daily_loss_limit", losslim)

    await _w(bot, user.id,
        f"✅ BJ limits saved.\n"
        f"Bet: {minbet}-{maxbet}c | W/L: {winlim}/{losslim}"
    )


# ── /setrbjlimits <minbet> <maxbet> <winlimit> <losslimit> ────────────────────

async def handle_setrbjlimits(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Managers and above only.")
        return

    if len(args) < 5 or not all(a.isdigit() for a in args[1:5]):
        await _w(bot, user.id,
            "Usage: /setrbjlimits <minbet> <maxbet> <winlimit> <losslimit>\n"
            "Example: /setrbjlimits 50 5000 10000 5000")
        return

    minbet  = int(args[1])
    maxbet  = int(args[2])
    winlim  = int(args[3])
    losslim = int(args[4])

    errors: list[str] = []
    if minbet < 1:      errors.append("Min bet must be ≥ 1.")
    if maxbet < minbet: errors.append("Max bet must be ≥ min bet.")
    if winlim < 1:      errors.append("Win limit must be > 0.")
    if losslim < 1:     errors.append("Loss limit must be > 0.")
    if errors:
        await _w(bot, user.id, " ".join(errors))
        return

    db.set_rbj_setting("min_bet",              minbet)
    db.set_rbj_setting("max_bet",              maxbet)
    db.set_rbj_setting("rbj_daily_win_limit",  winlim)
    db.set_rbj_setting("rbj_daily_loss_limit", losslim)

    await _w(bot, user.id,
        f"✅ RBJ limits saved.\n"
        f"Bet: {minbet}-{maxbet}c | W/L: {winlim}/{losslim}"
    )
