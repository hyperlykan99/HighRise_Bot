"""Cross-process bot command relay.

Dashboard and bot-to-bot actions are queued in ``bot_command_queue`` instead
of calling bot APIs directly.  Each bot claims only rows addressed to its mode,
username, or known aliases, then executes a small whitelist of structured
actions.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import database as db

if TYPE_CHECKING:
    from highrise import BaseBot

POLL_INTERVAL_SEC = 2.0
MAX_CHAT_CHARS = 249

CANONICAL_BOTS = {
    "dj": "DJ_DUDU",
    "host": "ChillTopiaMC",
    "security": "KeanuShield",
    "blackjack": "AceSinatra",
    "poker": "ChipSoprano",
    "miner": "GreatestProspector",
    "fisher": "MasterAngler",
    "banker": "BankingBot",
}

TARGET_ALIASES = {
    "eventhost": "host",
    "shopkeeper": "banker",
    "fishing": "fisher",
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().lstrip("@")


def _current_mode() -> str:
    try:
        from config import BOT_MODE
        return _norm(BOT_MODE)
    except Exception:
        return ""


def _mode_key() -> str:
    try:
        from config import BOT_MODE
        return str(BOT_MODE or "").strip() or _current_mode()
    except Exception:
        return _current_mode()


def _canonical_mode_for_username(username: str) -> str:
    target = _norm(username)
    for mode, canonical_username in CANONICAL_BOTS.items():
        if _norm(canonical_username) == target:
            return mode
    return ""


def _own_aliases() -> list[str]:
    """Collect every target alias this subprocess may safely claim."""
    aliases: set[str] = set()
    mode = _current_mode()
    if mode:
        aliases.add(mode)
        canonical_username = CANONICAL_BOTS.get(mode)
        if canonical_username:
            aliases.add(_norm(canonical_username))
        for alias, alias_mode in TARGET_ALIASES.items():
            if alias_mode == mode:
                aliases.add(alias)
    try:
        from config import BOT_USERNAME
        if BOT_USERNAME:
            aliases.add(_norm(BOT_USERNAME))
            username_mode = _canonical_mode_for_username(BOT_USERNAME)
            if username_mode:
                aliases.add(username_mode)
    except Exception:
        pass
    try:
        from modules.gold import get_bot_username
        gu = get_bot_username()
        if gu:
            aliases.add(_norm(gu))
            username_mode = _canonical_mode_for_username(gu)
            if username_mode:
                aliases.add(username_mode)
    except Exception:
        pass
    aliases.discard("")
    aliases.discard("all")
    aliases.discard("main")
    return sorted(aliases)


def _self_display() -> str:
    """Return the human-readable @name for this bot."""
    try:
        from modules.gold import get_bot_username as _get_uname
        gu = _get_uname()
        if gu:
            return gu
    except Exception:
        pass
    try:
        from config import BOT_MODE, BOT_USERNAME
        return BOT_USERNAME or CANONICAL_BOTS.get(_norm(BOT_MODE), BOT_MODE)
    except Exception:
        return "unknown"


def _claimer() -> str:
    mode = _current_mode()
    return mode or _norm(_self_display()) or "unknown"


async def _do_return_home(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    try:
        from modules.room_utils import teleport_bot_to_saved_spawn
    except Exception:
        raise RuntimeError("return_home helper missing")
    ok = await teleport_bot_to_saved_spawn(
        bot,
        bot_username=_self_display(),
        bot_mode=_current_mode(),
        fallback_walk=True,
    )
    if ok:
        return f"{_self_display()} returned to saved spawn/home"
    raise RuntimeError("saved spawn missing or return_home failed")


async def _do_stopbotemote(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    from modules.emote_system import _bot_loops

    mode = _current_mode()
    key = _mode_key() or _self_display()
    task = _bot_loops.pop(key, None)
    if task and not task.done():
        task.cancel()
    try:
        db.set_room_setting(f"bot_emote_{mode}", "")
    except Exception:
        pass
    if requester_id:
        try:
            await bot.highrise.send_whisper(
                requester_id,
                f"@{_self_display()} stopped emote loop."[:MAX_CHAT_CHARS],
            )
        except Exception as exc:
            print(f"[RELAY] stop confirm whisper failed: {exc!r}")
    return f"{_self_display()} stop_emote completed"


async def _do_restart_requested(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    return "restart_requested acknowledged; PM2 restart must be handled by dashboard/owner"


async def _do_announce(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    if _current_mode() != "host":
        raise PermissionError("announce may only run on the host bot")
    message = str(payload.get("message") or "").strip()
    if not message:
        raise ValueError("announce message is empty")
    await bot.highrise.chat(message[:MAX_CHAT_CHARS])
    return "announcement sent"


def _resolve_bot_emote(emote: str) -> tuple[str, str]:
    from modules import emote_system

    emote_system.reload_emote_registry()
    alias = str(emote or "").strip()
    if not alias:
        raise ValueError("emote is required")
    emote_id = emote_system.ALL_BOT_EMOTES.get(emote_system._norm(alias))
    if not emote_id:
        raise ValueError(f"bot emote not found: {alias}")
    return emote_id, alias


async def _send_self_emote_loop(bot: "BaseBot", emote_id: str,
                                duration: float) -> None:
    from modules.emote_system import get_emote_time

    end_at = asyncio.get_event_loop().time() + min(max(duration, 0.0), 120.0)
    while asyncio.get_event_loop().time() < end_at:
        await bot.highrise.send_emote(emote_id)
        await asyncio.sleep(max(1.0, get_emote_time(emote_id)))


async def _do_trigger_emote(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    target = _norm(payload.get("target") or "self")
    if target not in ("", "self"):
        raise ValueError("trigger_emote currently supports target=self only")
    emote_id, alias = _resolve_bot_emote(str(payload.get("emote") or ""))
    try:
        duration = float(payload.get("duration") or 0)
    except Exception:
        duration = 0.0
    if duration > 0:
        asyncio.create_task(_send_self_emote_loop(bot, emote_id, duration))
        return f"started {alias} for {min(duration, 120.0):.0f}s"
    await bot.highrise.send_emote(emote_id)
    return f"triggered {alias}"


async def _do_botemote(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    """Start a registry-timed emote loop on this bot for the given emote_id."""
    from modules.emote_system import _bot_loops

    mode = _current_mode()
    key = _mode_key() or mode
    eid = str(payload.get("emote_id") or "").strip()
    emote_name = str(payload.get("emote_name") or eid).strip()
    if not eid:
        raise ValueError("emote_id is required")

    old = _bot_loops.pop(key, None)
    if old and not old.done():
        old.cancel()

    _ereg = None
    interval = 5.0
    try:
        from data import emote_registry as _ereg
        _ereg.reload()
        entry = _ereg.get_emote(emote_name) or _ereg.get_emote(eid)
        if entry:
            value = float(entry.get("time") or 0)
            if value > 0:
                interval = value
    except Exception as exc:
        print(f"[RELAY] registry lookup failed: {exc!r}")
    print(f"[BOT_LOOP_INTERVAL] alias={emote_name!r} id={eid!r} resolved={interval} source=registry")

    async def _loop() -> None:
        while True:
            sleep_time = interval
            try:
                if _ereg is not None:
                    entry = _ereg.get_emote(emote_name) or _ereg.get_emote(eid)
                    if entry:
                        value = float(entry.get("time") or 0)
                        if value > 0:
                            sleep_time = value
            except Exception:
                pass
            try:
                await bot.highrise.send_emote(eid)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[RELAY] emote loop err mode={mode!r} eid={eid!r}: {exc!r}")
            await asyncio.sleep(sleep_time)

    _bot_loops[key] = asyncio.create_task(_loop())
    try:
        db.set_room_setting(f"bot_emote_{mode}", eid)
        print(f"[BOT_EMOTE_PERSIST] mode={mode!r} eid={eid!r}")
    except Exception as exc:
        print(f"[BOT_EMOTE_PERSIST] save failed: {exc!r}")

    if requester_id:
        try:
            await bot.highrise.send_whisper(
                requester_id,
                f"@{_self_display()} looping {emote_name} every {interval}s"[:MAX_CHAT_CHARS],
            )
        except Exception as exc:
            print(f"[RELAY] confirm whisper failed: {exc!r}")
    return f"{_self_display()} looping {emote_name}"


DISPATCH = {
    "return_home": _do_return_home,
    "stop_emote": _do_stopbotemote,
    "restart_requested": _do_restart_requested,
    "announce": _do_announce,
    "trigger_emote": _do_trigger_emote,
    # Legacy in-room cross-bot emote relay actions.
    "botemote": _do_botemote,
    "stopbotemote": _do_stopbotemote,
}


async def _process_row(bot: "BaseBot", row: dict) -> None:
    cmd_id = row["id"]
    action = _norm(row.get("action"))
    requester = row.get("requester_id", "") or ""
    try:
        payload = json.loads(row.get("payload") or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    handler = DISPATCH.get(action)
    if handler is None:
        print(f"[RELAY] unknown action {action!r} for cmd #{cmd_id}")
        db.mark_bot_command_completed(cmd_id, status="unknown_action", message=f"Unknown action: {action}")
        return
    try:
        result = await handler(bot, payload, requester)
        db.complete_bot_command(cmd_id, result or "completed")
    except Exception as exc:
        print(f"[RELAY] handler error cmd #{cmd_id} action={action!r}: {exc!r}")
        db.fail_bot_command(cmd_id, str(exc))


async def poller_loop(bot: "BaseBot") -> None:
    """Run forever, polling the queue every few seconds."""
    claimer = _claimer()
    print(f"[RELAY] poller starting for {claimer} aliases={_own_aliases()}")
    while True:
        try:
            rows = db.claim_pending_bot_commands(_own_aliases(), claimer, limit=10)
            for row in rows:
                await _process_row(bot, row)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[RELAY] poller error: {exc!r}")
        await asyncio.sleep(POLL_INTERVAL_SEC)
