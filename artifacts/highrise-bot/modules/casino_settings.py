"""
modules/casino_settings.py
Casino Control Panel commands for the Highrise Mini Game Bot.

Commands (manager / admin / owner only):
  /casinosettings [bj|rbj|1|2]           — view BJ/RBJ settings (both if no arg)
  /casinolimits                           — bet + daily win/loss limits at a glance
  /casinotoggles [bj|rbj] [win|loss] [on|off] — view or set enable/limit toggles
  /setbjlimits  <minbet> <maxbet> <winlimit> <losslimit>
  /setrbjlimits <minbet> <maxbet> <winlimit> <losslimit>

All messages kept under 250 characters.
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


# ── /casinosettings [bj|rbj|1|2] ──────────────────────────────────────────────

async def handle_casinosettings(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else ""

    if sub in ("bj", "1"):
        s = db.get_bj_settings()
        await _w(bot, user.id,
            f"🎰 BJ Settings\n"
            f"Status: {_on(s.get('bj_enabled', 1))} | Timer: {s.get('bj_turn_timer', 20)}s\n"
            f"Bet: {int(s.get('min_bet', 10)):,}c–{int(s.get('max_bet', 1000)):,}c\n"
            f"Win limit: {int(s.get('bj_daily_win_limit', 5000)):,}c ({_on(s.get('bj_win_limit_enabled', 1))})\n"
            f"Loss limit: {int(s.get('bj_daily_loss_limit', 3000)):,}c ({_on(s.get('bj_loss_limit_enabled', 1))})"
        )

    elif sub in ("rbj", "2"):
        s = db.get_rbj_settings()
        await _w(bot, user.id,
            f"🎰 RBJ Settings\n"
            f"Status: {_on(s.get('rbj_enabled', 1))} | Timer: {s.get('rbj_turn_timer', 20)}s\n"
            f"Bet: {int(s.get('min_bet', 10)):,}c–{int(s.get('max_bet', 1000)):,}c\n"
            f"Win limit: {int(s.get('rbj_daily_win_limit', 5000)):,}c ({_on(s.get('rbj_win_limit_enabled', 1))})\n"
            f"Loss limit: {int(s.get('rbj_daily_loss_limit', 3000)):,}c ({_on(s.get('rbj_loss_limit_enabled', 1))})"
        )

    else:
        bj  = db.get_bj_settings()
        rbj = db.get_rbj_settings()
        await _w(bot, user.id,
            f"🎰 BJ Settings (1/2)\n"
            f"Status: {_on(bj.get('bj_enabled', 1))} | Timer: {bj.get('bj_turn_timer', 20)}s\n"
            f"Bet: {int(bj.get('min_bet', 10)):,}c–{int(bj.get('max_bet', 1000)):,}c\n"
            f"Win: {int(bj.get('bj_daily_win_limit', 5000)):,}c | "
            f"Loss: {int(bj.get('bj_daily_loss_limit', 3000)):,}c/day"
        )
        await _w(bot, user.id,
            f"🎰 RBJ Settings (2/2)\n"
            f"Status: {_on(rbj.get('rbj_enabled', 1))} | Timer: {rbj.get('rbj_turn_timer', 20)}s\n"
            f"Bet: {int(rbj.get('min_bet', 10)):,}c–{int(rbj.get('max_bet', 1000)):,}c\n"
            f"Win: {int(rbj.get('rbj_daily_win_limit', 5000)):,}c | "
            f"Loss: {int(rbj.get('rbj_daily_loss_limit', 3000)):,}c/day"
        )


# ── /casinolimits ──────────────────────────────────────────────────────────────

async def handle_casinolimits(bot: BaseBot, user: User) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    bj  = db.get_bj_settings()
    rbj = db.get_rbj_settings()
    await _w(bot, user.id,
        f"🎰 Casino Limits\n"
        f"BJ:  {int(bj.get('min_bet', 10)):,}c–{int(bj.get('max_bet', 1000)):,}c | "
        f"Win: {int(bj.get('bj_daily_win_limit', 5000)):,}c | "
        f"Loss: {int(bj.get('bj_daily_loss_limit', 3000)):,}c\n"
        f"RBJ: {int(rbj.get('min_bet', 10)):,}c–{int(rbj.get('max_bet', 1000)):,}c | "
        f"Win: {int(rbj.get('rbj_daily_win_limit', 5000)):,}c | "
        f"Loss: {int(rbj.get('rbj_daily_loss_limit', 3000)):,}c"
    )


# ── /casinotoggles [bj|rbj] [winlimit|losslimit] [on|off] ─────────────────────

async def handle_casinotoggles(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    # Setter: /casinotoggles <bj|rbj> <winlimit|losslimit> <on|off>
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
                await _w(bot, user.id, "Toggle must be: winlimit or losslimit.")
        elif game == "rbj":
            if toggle in ("winlimit", "win"):
                db.set_rbj_setting("rbj_win_limit_enabled", val)
                await _w(bot, user.id, f"✅ RBJ win limit: {state.upper()}.")
            elif toggle in ("losslimit", "loss"):
                db.set_rbj_setting("rbj_loss_limit_enabled", val)
                await _w(bot, user.id, f"✅ RBJ loss limit: {state.upper()}.")
            else:
                await _w(bot, user.id, "Toggle must be: winlimit or losslimit.")
        else:
            await _w(bot, user.id, "Game must be: bj or rbj.")
        return

    # Viewer
    bj  = db.get_bj_settings()
    rbj = db.get_rbj_settings()
    await _w(bot, user.id,
        f"🎰 Casino Toggles\n"
        f"BJ: {_on(bj.get('bj_enabled', 1))} | RBJ: {_on(rbj.get('rbj_enabled', 1))}\n"
        f"BJ  wins: {_on(bj.get('bj_win_limit_enabled', 1))}  | "
        f"losses: {_on(bj.get('bj_loss_limit_enabled', 1))}\n"
        f"RBJ wins: {_on(rbj.get('rbj_win_limit_enabled', 1))} | "
        f"losses: {_on(rbj.get('rbj_loss_limit_enabled', 1))}"
    )


# ── /setbjlimits <minbet> <maxbet> <winlimit> <losslimit> ─────────────────────

async def handle_setbjlimits(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
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
        f"✅ BJ limits saved:\n"
        f"Bet: {minbet:,}c–{maxbet:,}c | "
        f"Win: {winlim:,}c | Loss: {losslim:,}c/day"
    )


# ── /setrbjlimits <minbet> <maxbet> <winlimit> <losslimit> ────────────────────

async def handle_setrbjlimits(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
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
        f"✅ RBJ limits saved:\n"
        f"Bet: {minbet:,}c–{maxbet:,}c | "
        f"Win: {winlim:,}c | Loss: {losslim:,}c/day"
    )
