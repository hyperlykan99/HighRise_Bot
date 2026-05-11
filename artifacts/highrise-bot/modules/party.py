"""
modules/party.py
----------------
Party Mode — enhanced room hype when staff activates it.

Staff/Manager+ commands:
  !party on             — activate party mode
  !party off            — deactivate party mode
  !party status         — show current party mode status
  !party announce [msg] — send a highlighted party announcement

When party mode is ON:
  - Gold Rain pace switches to 'party' default
  - Hype messages are active on a safe cooldown
  - Sponsor messages are highlighted
  - DJ/event announcements are easier via !party announce

All messages obey the 249-char cap.
"""
from __future__ import annotations
import asyncio
import time
from highrise import BaseBot, User

import database as db
from modules.permissions import is_manager


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# State helpers (room_settings table)
# ---------------------------------------------------------------------------

def is_party_mode() -> bool:
    return db.get_room_setting("party_mode", "0") == "1"


def _set_party_mode(on: bool) -> None:
    db.set_room_setting("party_mode", "1" if on else "0")


# ---------------------------------------------------------------------------
# Hype cooldown (module-level simple timer — non-persistent)
# ---------------------------------------------------------------------------

_last_hype_chat: float = 0.0
_HYPE_COOLDOWN_SECS: int = 300   # 5 minutes between auto-hype messages


async def maybe_party_hype(bot: BaseBot) -> None:
    """Called opportunistically; sends a hype message if party is ON and cooldown passed."""
    global _last_hype_chat
    if not is_party_mode():
        return
    now = time.time()
    if now - _last_hype_chat < _HYPE_COOLDOWN_SECS:
        return
    _last_hype_chat = now
    try:
        await bot.highrise.chat(
            "🎉 Party Mode is LIVE! Dance, play, and have fun!\n"
            "Type !help to see what you can do."
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_party(bot: BaseBot, user: User, args: list[str]) -> None:
    sub = args[1].lower() if len(args) >= 2 else "status"

    if sub == "on":
        if not is_manager(user.username):
            await _w(bot, user.id, "Manager+ only.")
            return
        _set_party_mode(True)
        await _w(bot, user.id, "🎉 Party Mode: ON")
        try:
            await bot.highrise.chat(
                "🎉 PARTY MODE ACTIVATED!\n"
                "Special events, Gold Rain, and more — let's go!"
            )
        except Exception:
            pass

    elif sub == "off":
        if not is_manager(user.username):
            await _w(bot, user.id, "Manager+ only.")
            return
        _set_party_mode(False)
        await _w(bot, user.id, "Party Mode: OFF")
        try:
            await bot.highrise.chat("Party Mode has ended. Thanks for joining!")
        except Exception:
            pass

    elif sub == "announce":
        if not is_manager(user.username):
            await _w(bot, user.id, "Manager+ only.")
            return
        msg_parts = args[2:] if len(args) >= 3 else []
        if not msg_parts:
            await _w(bot, user.id, "Usage: !party announce <message>")
            return
        msg = " ".join(msg_parts)
        full_msg = f"🎉 Party Announcement\n{msg}"
        try:
            await bot.highrise.chat(full_msg[:249])
        except Exception:
            pass
        await _w(bot, user.id, "✅ Party announcement sent.")

    else:
        # status
        on   = is_party_mode()
        pace = db.get_room_setting("gold_rain_pace", "normal")
        await _w(bot, user.id,
                 f"🎉 Party Mode\n"
                 f"Status: {'ON' if on else 'OFF'}\n"
                 f"Gold Rain Pace: {pace}\n"
                 f"Hype Cooldown: 5m\n"
                 f"Commands: !party on|off|announce [msg]")
