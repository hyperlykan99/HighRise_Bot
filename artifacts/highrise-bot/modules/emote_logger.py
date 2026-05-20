"""
modules/emote_logger.py
-----------------------
Emote spy / logging mode for ChillTopia / DJ_DUDU.

Captures exact emote_id values from on_emote events so you can
identify the raw SDK IDs used by other bots or players.

Commands (DJ-only):
  !emotelog on|off                      — toggle logging
  !emotelogstatus                       — show state + entry count
  !lastemotes [n]                       — last N observed emotes (default 10)
  !clearemotelog                        — wipe in-memory + disk log
  !addobservedemote <alias> <emote_id>  — store alias → raw emote_id
  !testobservedemote <alias>            — resolve alias and send as bot self-emote
  !fakeemote <emote_id>                 — manually inject emote into pipeline (bypasses SDK)
  !debugemoteevents                     — full diagnostic: SDK ver, override status, counts

Persistence:
  data/emote_observed_log.json      — last 50 emote events
  data/emote_observed_aliases.json  — alias → emote_id map
"""
from __future__ import annotations

import json
import os
import re as _re
import time
from collections import deque
from typing import TYPE_CHECKING

from modules.permissions import is_admin, is_owner

if TYPE_CHECKING:
    from highrise import BaseBot, User

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_PATH   = os.path.join(_BASE_DIR, "data", "emote_observed_log.json")
_ALIAS_PATH = os.path.join(_BASE_DIR, "data", "emote_observed_aliases.json")

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
_log_enabled: bool = False
_emote_log: deque[dict] = deque(maxlen=50)
_observed_aliases: dict[str, str] = {}
_last_event_ts: float | None = None      # timestamp of the last captured on_emote
_total_events_seen: int = 0              # lifetime counter (resets on restart)

# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_log() -> None:
    global _emote_log
    try:
        with open(_LOG_PATH) as f:
            data = json.load(f)
        _emote_log = deque(data[-50:], maxlen=50)
    except Exception:
        _emote_log = deque(maxlen=50)


def _save_log() -> None:
    try:
        with open(_LOG_PATH, "w") as f:
            json.dump(list(_emote_log), f, indent=2)
    except Exception as e:
        print(f"[EMOTE_LOG] save error: {e!r}")


def _load_aliases() -> None:
    global _observed_aliases
    try:
        with open(_ALIAS_PATH) as f:
            _observed_aliases = json.load(f)
    except Exception:
        _observed_aliases = {}


def _save_aliases() -> None:
    try:
        with open(_ALIAS_PATH, "w") as f:
            json.dump(_observed_aliases, f, indent=2)
    except Exception as e:
        print(f"[EMOTE_LOG] alias save error: {e!r}")


# Load persisted state on module import
_load_log()
_load_aliases()

# ---------------------------------------------------------------------------
# Public API  (called from main.py on_emote + other modules)
# ---------------------------------------------------------------------------

def is_logging_enabled() -> bool:
    return _log_enabled


def set_logging(enabled: bool) -> None:
    global _log_enabled
    _log_enabled = enabled


def log_emote(username: str, user_id: str, emote_id: str,
              receiver: object = None) -> None:
    """Record one emote event.  Called from on_emote (always — logging flag gates storage)."""
    global _last_event_ts, _total_events_seen
    _last_event_ts = time.time()
    _total_events_seen += 1
    if not _log_enabled:
        return
    receiver_name = getattr(receiver, "username", None) if receiver else None
    entry: dict = {
        "ts":       int(time.time()),
        "username": username,
        "user_id":  user_id,
        "emote_id": emote_id,
    }
    if receiver_name:
        entry["receiver"] = receiver_name
    _emote_log.append(entry)
    rcv_part = f" receiver={receiver_name!r}" if receiver_name else ""
    print(f"[EMOTE_LOG] username={username!r} user_id={user_id!r} "
          f"emote_id={emote_id!r}{rcv_part}")
    _save_log()


def get_total_events_seen() -> int:
    return _total_events_seen


def get_last_event_ts() -> float | None:
    return _last_event_ts


def get_last_emotes(n: int = 10) -> list[dict]:
    items = list(_emote_log)
    return items[-n:] if n < len(items) else items


def clear_log() -> None:
    _emote_log.clear()
    _save_log()


def add_observed_alias(alias: str, emote_id: str) -> None:
    norm = _re.sub(r"[^a-z0-9]", "", alias.lower())
    _observed_aliases[norm] = emote_id
    _save_aliases()


def resolve_observed_alias(alias: str) -> str | None:
    norm = _re.sub(r"[^a-z0-9]", "", alias.lower())
    return _observed_aliases.get(norm)


def get_observed_aliases() -> dict[str, str]:
    return dict(_observed_aliases)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


def _is_admin_user(uname: str) -> bool:
    return is_admin(uname) or is_owner(uname)

# ---------------------------------------------------------------------------
# !emotelog on|off
# ---------------------------------------------------------------------------

async def handle_emotelog(bot: "BaseBot", user: "User", args: list) -> None:
    """!emotelog on|off — toggle emote spy logging."""
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        state = "ON" if _log_enabled else "OFF"
        await _w(bot, uid,
            f"Emote logging is currently {state}. "
            f"Usage: !emotelog on|off")
        return
    set_logging(args[1].lower() == "on")
    state = "ON" if _log_enabled else "OFF"
    detail = ("Capturing emote_id from every on_emote event."
              if _log_enabled else "No longer capturing.")
    await _w(bot, uid, f"✅ Emote logging {state}. {detail}")

# ---------------------------------------------------------------------------
# !emotelogstatus
# ---------------------------------------------------------------------------

async def handle_emotelogstatus(bot: "BaseBot", user: "User", args: list) -> None:
    """!emotelogstatus — show logging state and entry/alias counts."""
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    state     = "ON" if _log_enabled else "OFF"
    count     = len(_emote_log)
    alias_cnt = len(_observed_aliases)
    await _w(bot, uid,
        f"📊 Emote log: {state} | {count}/50 entries stored | "
        f"{alias_cnt} observed aliases")

# ---------------------------------------------------------------------------
# !lastemotes [n]
# ---------------------------------------------------------------------------

async def handle_lastemotes(bot: "BaseBot", user: "User", args: list) -> None:
    """!lastemotes [n] — show the last N observed emotes (default 10, max 20)."""
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    try:
        n = min(int(args[1]), 20) if len(args) >= 2 else 10
    except (ValueError, IndexError):
        n = 10

    entries = get_last_emotes(n)
    if not entries:
        await _w(bot, uid,
            "📭 No emotes logged yet. Use !emotelog on to start capturing.")
        return

    # Build numbered lines, newest first; send in ≤249-char whispers
    lines = []
    for i, e in enumerate(reversed(entries), 1):
        rcv = f" → @{e['receiver']}" if e.get("receiver") else ""
        lines.append(f"{i}. {e['username']} → {e['emote_id']}{rcv}")

    chunk = ""
    for line in lines:
        candidate = f"{chunk}\n{line}" if chunk else line
        if len(candidate) > 240:
            await _w(bot, uid, chunk)
            chunk = line
        else:
            chunk = candidate
    if chunk:
        await _w(bot, uid, chunk)

# ---------------------------------------------------------------------------
# !clearemotelog
# ---------------------------------------------------------------------------

async def handle_clearemotelog(bot: "BaseBot", user: "User", args: list) -> None:
    """!clearemotelog — wipe the in-memory and disk emote log."""
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    before = len(_emote_log)
    clear_log()
    await _w(bot, uid, f"🗑️ Cleared {before} emote log entries.")

# ---------------------------------------------------------------------------
# !addobservedemote <alias> <emote_id>
# ---------------------------------------------------------------------------

async def handle_addobservedemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!addobservedemote <alias> <emote_id> — persist alias → raw emote_id."""
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    if len(args) < 3:
        await _w(bot, uid,
            "Usage: !addobservedemote <alias> <emote_id>  "
            "e.g. !addobservedemote aerobics dance-aerobics")
        return
    alias    = args[1].strip().lower()
    emote_id = args[2].strip()
    norm     = _re.sub(r"[^a-z0-9]", "", alias)
    add_observed_alias(alias, emote_id)
    await _w(bot, uid,
        (f"✅ Observed alias saved: '{norm}' → {emote_id}\n"
         f"Use !testobservedemote {alias} to test it.\n"
         f"Use !addworkingemote {alias} to promote to verified list.")[:249])

# ---------------------------------------------------------------------------
# !testobservedemote <alias>
# ---------------------------------------------------------------------------

async def handle_testobservedemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!testobservedemote <alias> — resolve observed alias and send as bot self-emote once."""
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    if len(args) < 2:
        await _w(bot, uid,
            "Usage: !testobservedemote <alias>  "
            "e.g. !testobservedemote aerobics")
        return
    alias = args[1].strip().lower()
    eid   = resolve_observed_alias(alias)
    if not eid:
        await _w(bot, uid,
            (f"❓ No observed alias for '{alias}'. "
             f"Use !addobservedemote {alias} <emote_id> first.")[:249])
        return
    await _w(bot, uid, f"🔬 Testing observed '{alias}' → {eid} ...")
    try:
        await bot.highrise.send_emote(eid)
        await _w(bot, uid,
            (f"✅ SDK accepted: {eid}\n"
             f"If it animated, use !addworkingemote {alias} to promote.")[:249])
    except Exception as exc:
        err = str(exc)[:100]
        await _w(bot, uid, f"❌ SDK rejected: {eid} — {err}"[:249])

# ---------------------------------------------------------------------------
# !fakeemote <emote_id>
# ---------------------------------------------------------------------------

async def handle_fakeemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!fakeemote <emote_id> — inject a fake on_emote event to test the logger pipeline.

    Bypasses the Highrise server entirely. If the logger captures this, the
    pipeline is working and the server is simply not delivering real emote events.
    """
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return
    if len(args) < 2:
        await _w(bot, uid,
            "Usage: !fakeemote <emote_id>  "
            "e.g. !fakeemote dance-aerobics")
        return
    emote_id = args[1].strip()
    await _w(bot, uid,
        f"🧪 Injecting fake emote into pipeline: {emote_id}\n"
        f"Check !lastemotes and console for [RAW_ON_EMOTE] / [EMOTE_LOG].")
    # Call on_emote directly — simulates exactly what the SDK would do
    try:
        await bot.on_emote(user, emote_id, None)
        await _w(bot, uid,
            f"✅ Pipeline OK — fake emote processed. "
            f"Use !lastemotes to confirm capture.")
    except Exception as exc:
        err = str(exc)[:120]
        await _w(bot, uid, f"❌ Pipeline error: {err}"[:249])

# ---------------------------------------------------------------------------
# !debugemoteevents
# ---------------------------------------------------------------------------

async def handle_debugemoteevents(bot: "BaseBot", user: "User", args: list) -> None:
    """!debugemoteevents — full diagnostic for on_emote event delivery."""
    uid   = user.id
    uname = user.username
    if not _is_admin_user(uname):
        await _w(bot, uid, "👑 Admin only.")
        return

    # Gather diagnostics
    try:
        import pkg_resources as _pkg
        sdk_ver = _pkg.get_distribution("highrise-bot-sdk").version
    except Exception:
        sdk_ver = "unknown"

    try:
        from highrise import BaseBot as _BaseBot
        overridden = type(bot).on_emote is not _BaseBot.on_emote
    except Exception:
        overridden = None

    try:
        from highrise.__main__ import gather_subscriptions as _gs
        subs = _gs(bot)
        emote_subbed = "emote" in subs
    except Exception:
        subs = "?"
        emote_subbed = None

    total   = _total_events_seen
    stored  = len(_emote_log)
    log_on  = _log_enabled

    if _last_event_ts is not None:
        import datetime as _dt
        last_str = _dt.datetime.fromtimestamp(
            _last_event_ts,
            tz=_dt.timezone.utc
        ).strftime("%H:%M:%S UTC")
    else:
        last_str = "never"

    lines = [
        f"📡 Emote Event Diagnostics",
        f"SDK ver : {sdk_ver}",
        f"on_emote overridden: {'YES ✅' if overridden else 'NO ❌' if overridden is False else '?'}",
        f"emote subscribed   : {'YES ✅' if emote_subbed else 'NO ❌' if emote_subbed is False else '?'}",
        f"log mode   : {'ON' if log_on else 'OFF'}",
        f"total seen : {total} (since restart)",
        f"stored     : {stored}/50",
        f"last event : {last_str}",
    ]

    # Compatibility note
    if total == 0:
        lines.append(
            "⚠️ Zero events received. "
            "Server may not deliver emote events unless the bot is the receiver. "
            "Use !fakeemote to test the pipeline independently."
        )

    # Send in chunks ≤249
    chunk = ""
    for line in lines:
        candidate = f"{chunk}\n{line}" if chunk else line
        if len(candidate) > 240:
            await _w(bot, uid, chunk)
            chunk = line
        else:
            chunk = candidate
    if chunk:
        await _w(bot, uid, chunk)
