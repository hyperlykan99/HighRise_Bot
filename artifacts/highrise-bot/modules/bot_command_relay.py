"""Cross-process bot command relay.

When two bots live in different subprocesses they can't share a Python dict.
This module gives every bot a 1-second poller that reads pending commands
from the `bot_command_queue` table and executes the ones addressed to itself.

Currently supported actions:
    botemote      payload = {"emote_id": str, "loop": bool}
    stopbotemote  payload = {}
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import database as db

if TYPE_CHECKING:
    from highrise import BaseBot

POLL_INTERVAL_SEC = 1.0


def _own_aliases() -> list[str]:
    """Collect every alias this subprocess answers to (mode + usernames)."""
    aliases: set[str] = set()
    try:
        from config import BOT_MODE, BOT_USERNAME
        if BOT_MODE:
            aliases.add(BOT_MODE.strip().lower())
        if BOT_USERNAME:
            aliases.add(BOT_USERNAME.strip().lower().lstrip("@"))
    except Exception:
        pass
    try:
        from modules.gold import get_bot_username
        gu = get_bot_username()
        if gu:
            aliases.add(gu.strip().lower().lstrip("@"))
    except Exception:
        pass
    try:
        from modules.live_bot_registry import live_bot_keys
        for k in live_bot_keys():
            aliases.add(k)
    except Exception:
        pass
    return [a for a in aliases if a]


def _self_display() -> str:
    """Return the human-readable @name for this bot."""
    from config import BOT_MODE, BOT_USERNAME
    try:
        from modules.gold import get_bot_username as _get_uname
        gu = _get_uname()
        if gu:
            return gu
    except Exception:
        pass
    return BOT_USERNAME or BOT_MODE


async def _do_botemote(bot: "BaseBot", payload: dict, requester_id: str) -> None:
    """Start the 5s emote loop on THIS bot for the given emote_id."""
    from modules.emote_system import _bot_loops
    from config import BOT_MODE

    eid        = (payload.get("emote_id")   or "").strip()
    emote_name = (payload.get("emote_name") or eid).strip()
    if not eid:
        return

    # Cancel any existing loop on this bot.
    old = _bot_loops.pop(BOT_MODE, None)
    if old and not old.done():
        old.cancel()

    _eid = eid

    # Single canonical timing function — same one used everywhere.
    try:
        from data.emote_timings import get_emote_time as _get_emote_time
    except Exception:
        def _get_emote_time(emote_id: str, fallback: float = 5.0) -> float:  # type: ignore[misc]
            return fallback

    async def _loop() -> None:
        while True:
            try:
                await bot.highrise.send_emote(_eid)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[RELAY] emote loop err mode={BOT_MODE!r} eid={_eid!r}: {exc!r}")
            await asyncio.sleep(_get_emote_time(_eid))

    _bot_loops[BOT_MODE] = asyncio.create_task(_loop())

    # Confirm to the admin who requested the emote.
    if requester_id:
        try:
            disp = _self_display()
            t    = _get_emote_time(_eid)
            msg  = f"✅ @{disp} looping {emote_name} every {t}s"
            await bot.highrise.send_whisper(requester_id, msg[:249])
        except Exception as exc:
            print(f"[RELAY] confirm whisper failed: {exc!r}")


async def _do_stopbotemote(bot: "BaseBot", payload: dict, requester_id: str) -> None:
    from modules.emote_system import _bot_loops
    from config import BOT_MODE

    task = _bot_loops.pop(BOT_MODE, None)
    if task and not task.done():
        task.cancel()

    if requester_id:
        try:
            disp = _self_display()
            await bot.highrise.send_whisper(
                requester_id, f"✅ @{disp} stopped emote loop.")
        except Exception as exc:
            print(f"[RELAY] stop confirm whisper failed: {exc!r}")


_DISPATCH = {
    "botemote":     _do_botemote,
    "stopbotemote": _do_stopbotemote,
}


async def _process_row(bot: "BaseBot", row: dict) -> None:
    cmd_id      = row["id"]
    action      = row["action"]
    requester   = row.get("requester_id", "") or ""
    try:
        payload = json.loads(row.get("payload") or "{}")
    except Exception:
        payload = {}
    handler = _DISPATCH.get(action)
    if handler is None:
        print(f"[RELAY] unknown action {action!r} for cmd #{cmd_id}")
        db.mark_bot_command_completed(cmd_id, status="unknown_action")
        return
    try:
        await handler(bot, payload, requester)
        db.mark_bot_command_completed(cmd_id, status="completed")
    except Exception as exc:
        print(f"[RELAY] handler error cmd #{cmd_id} action={action!r}: {exc!r}")
        db.mark_bot_command_completed(cmd_id, status="error")


async def poller_loop(bot: "BaseBot") -> None:
    """Run forever — every POLL_INTERVAL_SEC seconds check the queue."""
    from config import BOT_MODE
    claimer = BOT_MODE or "unknown"
    print(f"[RELAY] poller starting for {claimer} aliases={_own_aliases()}")
    while True:
        try:
            aliases = _own_aliases()
            rows = db.claim_pending_bot_commands(aliases, claimer)
            for r in rows:
                await _process_row(bot, r)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[RELAY] poller error: {exc!r}")
        await asyncio.sleep(POLL_INTERVAL_SEC)
