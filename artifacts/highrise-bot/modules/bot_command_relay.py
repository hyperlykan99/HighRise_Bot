"""Cross-process bot command relay.

Dashboard and bot-to-bot actions are queued in ``bot_command_queue`` instead
of calling bot APIs directly.  Each bot claims only rows addressed to its mode,
username, or known aliases, then executes a small whitelist of structured
actions.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
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


def _require_dj_action(action: str) -> None:
    if _current_mode() != "dj":
        raise PermissionError(f"{action} may only run on DJ_DUDU")


async def _do_radio_skip(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_dj_action("radio_skip")
    return "music system is being rebuilt; radio skip is unavailable"


async def _do_radio_clear(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_dj_action("radio_clear")
    return "music system is being rebuilt; radio clear is unavailable"


async def _do_radio_cleanup(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_dj_action("radio_cleanup")
    return "music system is being rebuilt; radio cleanup is unavailable"


async def _do_radio_reload(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_dj_action("radio_reload")
    try:
        from modules import config_store
        config_store.clear_vibe_scan_cache()
    except Exception:
        pass
    return "radio reload acknowledged; cached vibe scan cleared where supported"


def _require_host_action(action: str) -> None:
    if _current_mode() != "host":
        raise PermissionError(f"{action} may only run on ChillTopiaMC")


def _require_security_action(action: str) -> None:
    if _current_mode() != "security":
        raise PermissionError(f"{action} may only run on KeanuShield")


async def _do_security_alert(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_security_action("security_alert")
    message = str(payload.get("message") or "").strip()
    if not message:
        raise ValueError("security_alert message is empty")
    await bot.highrise.chat(("🛡️ " + message)[:MAX_CHAT_CHARS])
    return "security alert sent"


async def _do_warn_user(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_security_action("warn_user")
    username = str(payload.get("username") or "").strip().lstrip("@")
    user_id = str(payload.get("user_id") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if not username or not reason:
        raise ValueError("warn_user requires username and reason")
    total = db.add_warning(user_id or username, username, requester_id or "dashboard", reason)
    await bot.highrise.chat((f"⚠️ @{username} warned: {reason}")[:MAX_CHAT_CHARS])
    return f"warning recorded for @{username}; total={total}"


async def _do_mute_user(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_security_action("mute_user")
    username = str(payload.get("username") or "").strip().lstrip("@")
    user_id = str(payload.get("user_id") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    try:
        minutes = int(payload.get("minutes") or 60)
    except Exception:
        minutes = 60
    minutes = max(1, min(minutes, 10080))
    if not username or not user_id:
        raise ValueError("mute_user requires username and user_id")
    db.mute_user(user_id, username, requester_id or "dashboard", minutes)
    await bot.highrise.chat((f"🔇 @{username} muted for {minutes}min. {reason}")[:MAX_CHAT_CHARS])
    return f"muted @{username} for {minutes}min"


async def _do_unmute_user(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_security_action("unmute_user")
    username = str(payload.get("username") or "").strip().lstrip("@")
    user_id = str(payload.get("user_id") or "").strip()
    if not user_id:
        raise ValueError("unmute_user requires user_id")
    removed = db.unmute_user(user_id)
    if username:
        await bot.highrise.chat((f"🔊 @{username} unmuted.")[:MAX_CHAT_CHARS])
    return f"unmute user_id={user_id}; removed={removed}"


async def _do_jail_user(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_security_action("jail_user")
    raise RuntimeError("jail_user helper missing")


async def _do_unjail_user(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_security_action("unjail_user")
    raise RuntimeError("unjail_user helper missing")


async def _do_event_start(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_host_action("event_start")
    from modules import events

    raw = _norm(payload.get("event_id"))
    event_id = events._resolve_event_arg(raw) or raw
    if not event_id or event_id not in events.EVENTS:
        raise ValueError(f"unknown event: {raw}")
    try:
        minutes = int(payload.get("minutes") or 30)
    except Exception:
        minutes = 30
    if minutes < 1 or minutes > 480:
        raise ValueError("minutes must be 1-480")

    ev = events.EVENTS[event_id]
    ev_type = ev.get("event_type", "room")
    if ev_type in ("mining", "fishing") or event_id in (events._MINING_EVENT_IDS | events._FISHING_EVENT_IDS):
        db.start_mining_event(event_id, requester_id or "dashboard", minutes)
    else:
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        db.set_active_event(event_id, expires_at)

    name = ev.get("name", event_id)
    emoji = "🎣" if ev_type == "fishing" else "⛏️" if ev_type == "mining" else ev.get("emoji", "🎉")
    await bot.highrise.chat((f"{emoji} {name} is live for {minutes}min. {ev.get('desc', '')}")[:MAX_CHAT_CHARS])
    try:
        db.add_event_history_entry(event_id, name, requester_id or "dashboard", False, minutes * 60)
    except Exception:
        pass
    return f"event started: {event_id} for {minutes}min"


async def _do_event_stop(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_host_action("event_stop")
    from modules import events

    target = _norm(payload.get("target") or "all")
    stopped: list[str] = []

    mine_ev = db.get_active_mining_event()
    if mine_ev:
        mine_eid = mine_ev.get("event_id", "")
        resolved = events._resolve_event_arg(target) or target
        if target in ("all", "mine", "mining") or resolved == mine_eid or target == mine_eid:
            db.stop_mining_event()
            stopped.append(mine_eid)

    gen_ev = db.get_active_event()
    if gen_ev:
        gen_eid = gen_ev.get("event_id", "")
        resolved = events._resolve_event_arg(target) or target
        if target == "all" or resolved == gen_eid or target == gen_eid:
            db.clear_active_event()
            stopped.append(gen_eid)

    if not stopped:
        return "no matching active event to stop"
    names = [events.EVENTS.get(eid, {}).get("name", eid) for eid in stopped]
    await bot.highrise.chat(("🛑 Event stopped: " + ", ".join(names))[:MAX_CHAT_CHARS])
    return "event stopped: " + ", ".join(stopped)


async def _do_event_schedule(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_host_action("event_schedule")
    from modules import events

    raw = _norm(payload.get("event_id"))
    event_id = events._resolve_event_arg(raw) or raw
    if not event_id or event_id not in events.EVENTS:
        raise ValueError(f"unknown event: {raw}")
    starts_at = str(payload.get("starts_at") or "").strip()
    if not starts_at:
        raise ValueError("starts_at is required")
    try:
        minutes = int(payload.get("minutes") or 30)
    except Exception:
        minutes = 30
    conn = db.get_connection()
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(scheduled_events)").fetchall()]
        if not cols:
            raise RuntimeError("scheduled_events table missing")
        insert_cols: list[str] = []
        values: list[Any] = []
        for col, value in (
            ("event_id", event_id),
            ("name", events.EVENTS[event_id].get("name", event_id)),
            ("title", events.EVENTS[event_id].get("name", event_id)),
            ("description", events.EVENTS[event_id].get("desc", "")),
            ("starts_at", starts_at),
            ("duration_minutes", str(minutes)),
            ("minutes", str(minutes)),
            ("status", "scheduled"),
            ("created_by", requester_id or "dashboard"),
            ("set_by", requester_id or "dashboard"),
        ):
            if col in cols:
                insert_cols.append(col)
                values.append(value)
        if "created_at" in cols:
            insert_cols.append("created_at")
        if "event_id" not in insert_cols and "name" not in insert_cols and "title" not in insert_cols:
            raise RuntimeError("scheduled_events has no event identifier column")
        if "starts_at" not in insert_cols:
            raise RuntimeError("scheduled_events.starts_at missing")
        placeholders = ["CURRENT_TIMESTAMP" if col == "created_at" else "?" for col in insert_cols]
        conn.execute(
            f"INSERT INTO scheduled_events ({', '.join(insert_cols)}) VALUES ({', '.join(placeholders)})",
            values,
        )
        conn.commit()
    finally:
        conn.close()
    return f"event scheduled: {event_id} at {starts_at} for {minutes}min"


async def _do_dancefloor_command(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_dj_action("dancefloor")
    from modules import emote_extras

    sub = str(payload.get("subcommand") or "").strip().lower()
    if sub not in {"start", "stop", "clear", "status", "emotes", "random", "randomtimed"}:
        raise ValueError("dancefloor subcommand is not allowed")
    if sub == "start":
        if not emote_extras._df_get_box():
            raise RuntimeError("dancefloor box missing; set points in-room first")
        if not (emote_extras._df_get_sequence() or emote_extras._df_get_emotes()):
            raise RuntimeError("dancefloor sequence missing")
        db.set_room_setting("dancefloor_active", "true")
        emote_extras._ensure_dancefloor_task(bot)
        return "dancefloor started"
    if sub == "stop":
        db.set_room_setting("dancefloor_active", "false")
        emote_extras._stop_dancefloor_task()
        return "dancefloor stopped"
    if sub == "clear":
        db.set_room_setting("dancefloor_active", "false")
        db.set_room_setting("dancefloor_p1", "")
        db.set_room_setting("dancefloor_p2", "")
        db.set_room_setting("dancefloor_box", "")
        db.set_room_setting("dancefloor_emotes", "")
        db.set_room_setting("dancefloor_sequence_json", "")
        emote_extras._stop_dancefloor_task()
        return "dancefloor cleared"
    if sub == "status":
        box = emote_extras._df_get_box()
        seq = emote_extras._df_get_sequence()
        active = emote_extras._df_is_active()
        inside = len(emote_extras._df_inside)
        return f"dancefloor active={active} box={'set' if box else 'unset'} sequence={len(seq)} inside={inside}"

    def _save_sequence(seq: list[dict], mode: str) -> str:
        if not seq:
            raise ValueError("no valid dancefloor emotes")
        emote_extras._df_set_sequence(seq, mode)
        db.set_room_setting("dancefloor_emotes", ",".join(str(s.get("alias", "")) for s in seq if s.get("alias")))
        return f"dancefloor sequence saved; mode={mode} steps={len(seq)}"

    if sub == "emotes":
        aliases_raw = payload.get("emotes")
        aliases = aliases_raw.replace(",", " ").split() if isinstance(aliases_raw, str) else aliases_raw
        if not isinstance(aliases, list) or not aliases:
            raise ValueError("dancefloor_sequence requires emotes")
        seq: list[dict] = []
        bad: list[str] = []
        for alias in [str(a).strip() for a in aliases if str(a).strip()]:
            ent = emote_extras._reg_get(alias)
            if ent and ent.get("player") and ent.get("id"):
                seq.append({"alias": ent.get("name") or alias, "eid": ent["id"], "seconds": None})
            else:
                bad.append(alias)
        if bad:
            print(f"[RELAY] dancefloor_sequence rejected={bad}")
        return _save_sequence(seq, "simple")
    if sub == "random":
        import random

        pool = []
        seen = set()
        for alias in emote_extras._reg_player_aliases():
            low = alias.lower()
            if low in seen:
                continue
            ent = emote_extras._reg_get(alias)
            if ent and ent.get("id"):
                seen.add(low)
                pool.append(alias)
        if not pool:
            raise RuntimeError("no player emotes available")
        try:
            count = int(payload.get("count") or len(pool))
        except Exception:
            count = len(pool)
        picks = random.sample(pool, min(max(1, count), len(pool)))
        seq = [{"alias": emote_extras._reg_get(a).get("name") or a, "eid": emote_extras._reg_get(a)["id"], "seconds": None} for a in picks]
        return _save_sequence(seq, "random")
    if sub == "randomtimed":
        import random

        pool = []
        seen = set()
        for alias in emote_extras._reg_player_aliases():
            low = alias.lower()
            if low in seen:
                continue
            ent = emote_extras._reg_get(alias)
            if ent and ent.get("id"):
                seen.add(low)
                pool.append(alias)
        if not pool:
            raise RuntimeError("no player emotes available")
        count_raw = str(payload.get("count") or "all").strip().lower()
        count = len(pool) if count_raw == "all" else max(1, int(count_raw))
        min_seconds = float(payload.get("min_seconds") or payload.get("seconds") or 5)
        max_seconds = float(payload.get("max_seconds") or min_seconds)
        if min_seconds < 0.5 or max_seconds < min_seconds:
            raise ValueError("invalid randomtimed seconds")
        picks = random.sample(pool, min(count, len(pool)))
        seq = []
        for alias in picks:
            ent = emote_extras._reg_get(alias)
            seq.append({
                "alias": ent.get("name") or alias,
                "eid": ent["id"],
                "seconds": min_seconds if min_seconds == max_seconds else round(random.uniform(min_seconds, max_seconds), 2),
            })
        return _save_sequence(seq, "randomtimed")
    raise ValueError("unsupported dancefloor command")


async def _do_dancefloor_start(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    return await _do_dancefloor_command(bot, {**payload, "subcommand": "start"}, requester_id)


async def _do_dancefloor_stop(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    return await _do_dancefloor_command(bot, {**payload, "subcommand": "stop"}, requester_id)


async def _do_dancefloor_clear(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    return await _do_dancefloor_command(bot, {**payload, "subcommand": "clear"}, requester_id)


async def _do_dancefloor_status(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    return await _do_dancefloor_command(bot, {**payload, "subcommand": "status"}, requester_id)


async def _do_dancefloor_sequence(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    return await _do_dancefloor_command(bot, {**payload, "subcommand": "emotes"}, requester_id)


async def _do_dancefloor_random(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    return await _do_dancefloor_command(bot, {**payload, "subcommand": "random"}, requester_id)


async def _do_dancefloor_randomtimed(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    return await _do_dancefloor_command(bot, {**payload, "subcommand": "randomtimed"}, requester_id)


async def _do_sync_start(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_dj_action("sync_start")
    from modules import emote_extras

    leader = str(payload.get("leader") or "").strip().lstrip("@")
    follower = str(payload.get("follower") or "").strip().lstrip("@")
    if follower and leader:
        raise ValueError("sync_start for a specific follower requires a live user context; use in-room !sync for that path")
    if not leader:
        raise ValueError("sync_start requires leader")
    from modules.live_bot_registry import live_bot_keys
    from modules.room_utils import _resolve_user_in_room

    bot_names = {str(n).lower() for n in (live_bot_keys() or [])}
    pair = await _resolve_user_in_room(bot, leader)
    if not pair:
        raise RuntimeError(f"leader @{leader} not found in room")
    leader_user, _ = pair
    if leader_user.username.lower() in bot_names:
        raise ValueError("bots cannot be sync leaders")
    try:
        resp = await bot.highrise.get_room_users()
        room_pairs = list(resp.content) if hasattr(resp, "content") else []
    except Exception as exc:
        raise RuntimeError(f"could not fetch room users: {exc!r}")
    count = 0
    for room_user, _pos in room_pairs:
        if room_user.username.lower() in bot_names or room_user.id == leader_user.id:
            continue
        emote_extras._unsubscribe_follower(room_user.id)
        try:
            from modules.emote_system import _cancel_player_loop
            _cancel_player_loop(room_user.id)
        except Exception:
            pass
        try:
            from modules.custom_emotes import stop_custom_permanent
            stop_custom_permanent(room_user.id, "dashboard_sync_start")
        except Exception:
            pass
        emote_extras._df_inside.discard(room_user.id)
        emote_extras._df_user_emote.pop(room_user.id, None)
        emote_extras._sync_leader_of[room_user.id] = leader_user.id
        emote_extras._sync_leader_name[room_user.id] = leader_user.username
        emote_extras._sync_follower_name[room_user.id] = room_user.username
        emote_extras._sync_followers.setdefault(leader_user.id, set()).add(room_user.id)
        emote_extras._sync_db_save(room_user.id, room_user.username, leader_user.id, leader_user.username)
        count += 1
    return f"sync all to @{leader_user.username}; followers={count}"


async def _do_sync_stop(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_dj_action("sync_stop")
    from modules import emote_extras

    scope = str(payload.get("scope") or "all").strip().lower()
    if scope != "all":
        raise ValueError("sync_stop currently supports scope=all only from dashboard")
    count = 0
    for follower_id in list(emote_extras._sync_leader_of.keys()):
        if emote_extras._unsubscribe_follower(follower_id) is not None:
            count += 1
    conn = db.get_connection()
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(sync_relations)").fetchall()]
        if "is_active" in cols:
            conn.execute("UPDATE sync_relations SET is_active=0, updated_at=datetime('now') WHERE is_active=1")
            count = max(count, conn.total_changes)
            conn.commit()
    finally:
        conn.close()
    return f"sync stopped for {count} active follower(s)"


async def _do_sync_persist(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    _require_dj_action("sync_persist")
    enabled = bool(payload.get("enabled"))
    conn = db.get_connection()
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(sync_relations)").fetchall()]
        if "persist_enabled" not in cols:
            raise RuntimeError("sync_relations.persist_enabled missing")
        conn.execute(
            "UPDATE sync_relations SET persist_enabled=?, updated_at=datetime('now') WHERE is_active=1",
            (1 if enabled else 0,),
        )
        changed = conn.total_changes
        conn.commit()
    finally:
        conn.close()
    return f"sync persistence set to {'on' if enabled else 'off'} for {changed} active relation(s)"


async def _do_botemote_set(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    emote = str(payload.get("emote") or "").strip()
    if not emote:
        raise ValueError("emote is required")
    emote_id, alias = _resolve_bot_emote(emote)
    return await _do_botemote(bot, {**payload, "emote_id": emote_id, "emote_name": alias}, requester_id)


async def _do_botemote_stop(bot: "BaseBot", payload: dict, requester_id: str) -> str:
    return await _do_stopbotemote(bot, payload, requester_id)


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
    "radio_skip": _do_radio_skip,
    "radio_clear": _do_radio_clear,
    "radio_cleanup": _do_radio_cleanup,
    "radio_reload": _do_radio_reload,
    "event_start": _do_event_start,
    "event_stop": _do_event_stop,
    "event_schedule": _do_event_schedule,
    "warn_user": _do_warn_user,
    "mute_user": _do_mute_user,
    "unmute_user": _do_unmute_user,
    "jail_user": _do_jail_user,
    "unjail_user": _do_unjail_user,
    "security_alert": _do_security_alert,
    "dancefloor_start": _do_dancefloor_start,
    "dancefloor_stop": _do_dancefloor_stop,
    "dancefloor_clear": _do_dancefloor_clear,
    "dancefloor_status": _do_dancefloor_status,
    "dancefloor_sequence": _do_dancefloor_sequence,
    "dancefloor_random": _do_dancefloor_random,
    "dancefloor_randomtimed": _do_dancefloor_randomtimed,
    "sync_start": _do_sync_start,
    "sync_stop": _do_sync_stop,
    "sync_persist": _do_sync_persist,
    "botemote_set": _do_botemote_set,
    "botemote_stop": _do_botemote_stop,
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
