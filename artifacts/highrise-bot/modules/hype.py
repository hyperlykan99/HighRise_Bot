"""
modules/hype.py
---------------
Mythic+ Hype System for rare mining/fishing drops.

Rarity levels:
  mythic    — EmceeBot sends small congrats (5m cooldown per player)
  prismatic — All bots congratulate + subscriber notification (10m global cooldown)
  exotic    — Full room hype: pause games, all-bot messages,
               soft-mute (flag-based), notifications, then resume (30m global cooldown)

Public trigger (called from mining.py / fishing.py):
  await trigger_hype(bot, username, user_id, source, rarity, item_name, colored_name)

Staff commands:
  !hypesettings             — show hype config
  !hype mythic on|off       — toggle mythic hype
  !hype prismatic on|off    — toggle prismatic hype
  !hype exotic on|off       — toggle exotic hype
  !hype exoticmute on|off   — toggle exotic room soft-mute
  !hype exoticduration <s>  — exotic pause duration (10–30 s)
  !hypelog [latest|@user]   — view hype event log
  !hypeunlock               — emergency: clear hype lock + resume all (owner/admin)
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta
from highrise import BaseBot, User

import database as db
from modules.permissions import is_admin, is_manager


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Hype lock — in-memory flag (cleared by hypeunlock or auto-resume)
# ---------------------------------------------------------------------------

_HYPE_LOCK_ACTIVE: bool  = False
_HYPE_LOCK_REASON: str   = ""


def is_hype_locked() -> bool:
    return _HYPE_LOCK_ACTIVE


def get_hype_lock_reason() -> str:
    return _HYPE_LOCK_REASON


def _set_hype_lock(on: bool, reason: str = "") -> None:
    global _HYPE_LOCK_ACTIVE, _HYPE_LOCK_REASON
    _HYPE_LOCK_ACTIVE = on
    _HYPE_LOCK_REASON = reason
    # Also persist via room_settings so all bots see it
    db.set_room_setting("hype_lock", "1" if on else "0")


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------

def _get_hype_setting(key: str, default: str = "1") -> str:
    return db.get_room_setting(f"hype_{key}", default)


def _set_hype_setting(key: str, value: str) -> None:
    db.set_room_setting(f"hype_{key}", value)


def _mythic_on()    -> bool: return _get_hype_setting("mythic",   "1") == "1"
def _prismatic_on() -> bool: return _get_hype_setting("prismatic","1") == "1"
def _exotic_on()    -> bool: return _get_hype_setting("exotic",   "1") == "1"
def _exotic_mute()  -> bool: return _get_hype_setting("exoticmute","1") == "1"

def _exotic_duration() -> int:
    try:
        return max(10, min(30, int(_get_hype_setting("exoticduration", "15"))))
    except Exception:
        return 15


# ---------------------------------------------------------------------------
# Cooldown helpers
# ---------------------------------------------------------------------------

_COOLDOWNS: dict[str, float] = {}   # key → last_triggered epoch


def _cooldown_passed(key: str, seconds: int) -> bool:
    import time
    last = _COOLDOWNS.get(key, 0.0)
    return (time.time() - last) >= seconds


def _reset_cooldown(key: str) -> None:
    import time
    _COOLDOWNS[key] = time.time()


# ---------------------------------------------------------------------------
# Hype event logging
# ---------------------------------------------------------------------------

def _log_hype_event(username: str, user_id: str, source: str,
                     rarity: str, item_name: str, hype_type: str,
                     muted_room: bool = False, pause_duration: int = 0,
                     notif_sent: bool = False, error: str = "") -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO hype_events
                 (username, user_id, source, rarity, item_name, hype_type,
                  ts, muted_room, pause_duration, notif_sent, error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (username.lower(), user_id, source, rarity, item_name[:100],
             hype_type, datetime.now(timezone.utc).isoformat(),
             1 if muted_room else 0, pause_duration,
             1 if notif_sent else 0, error[:200]),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Cross-bot channel: hype reactions via big_announce pending system
# ---------------------------------------------------------------------------

_PRISMATIC_MSGS: dict[str, str] = {
    "banker":    "💰 That find is worth serious money!",
    "blackjack": "🃏 Even the casino stopped to watch that one!",
    "poker":     "♠️ Big table energy!",
    "security":  "🛡️ Verified rare moment. Respect!",
    "eventhost": "🎉 Room moment! Everyone say GG!",
    "miner":     "⛏️ Legendary miner energy!",
    "fisher":    "🎣 A find like that deserves the spotlight!",
}

_EXOTIC_MSGS: dict[str, str] = {
    "banker":    "💰 Jackpot energy! This moment is priceless.",
    "blackjack": "🃏 Cards down. Everyone look at this.",
    "poker":     "♠️ All bets paused. This is bigger than the table.",
    "security":  "🛡️ Room secured for Exotic celebration.",
    "eventhost": "🎉 EVERYONE GET READY — THIS IS A ROOM LEGEND MOMENT!",
    "miner":     "⛏️ THE MINE JUST SHOOK. EXOTIC ORE DISCOVERED!",
    "fisher":    "🎣 THE WATER WENT SILENT. EXOTIC CATCH CONFIRMED!",
}


def _queue_bot_reactions(category: str, rarity: str,
                           item_name: str, username: str,
                           msgs: dict[str, str]) -> None:
    """Store per-bot reaction messages in DB for other bots to pick up."""
    try:
        conn = db.get_connection()
        # Re-use big_announcement_logs for cross-bot messaging
        # Insert one pending entry; reaction poll will pick custom msgs
        conn.execute(
            """INSERT INTO big_announcement_logs
                 (category, rarity, item_name, item_emoji, username,
                  status, created_at)
               VALUES (?,?,?,?,?,'pending',?)""",
            (f"hype_{category}", rarity, item_name[:80], "✨",
             username.lower(), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Subscriber notification
# ---------------------------------------------------------------------------

async def _send_hype_notification(bot: BaseBot, source: str,
                                    username: str, colored_name: str) -> bool:
    """Send a subscriber notification for prismatic/exotic events."""
    category = "mining" if source == "mining" else "fishing"
    action   = "found" if source == "mining" else "caught"
    msg      = (f"🔔 Rare Room Moment\n"
                f"@{username} {action} {colored_name}!\n"
                f"Join the room now!")
    try:
        # Use the existing subscriber notification mechanism
        import sys, importlib
        _main = sys.modules.get("__main__") or importlib.import_module("__main__")
        send_fn = getattr(_main, "send_subscriber_notification_now", None)
        if send_fn:
            await send_fn(bot, category, msg[:249])
            return True
        # Fallback: try direct DB-based notification dispatch
        from modules.subscribers import queue_subscriber_notification
        queue_subscriber_notification(category, msg[:249])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main hype trigger
# ---------------------------------------------------------------------------

async def trigger_hype(bot: BaseBot, username: str, user_id: str,
                        source: str, rarity: str,
                        item_name: str, colored_name: str = "") -> None:
    """
    Called from mining.py / fishing.py after a rare drop is resolved.
    source: 'mining' | 'fishing'
    rarity: 'mythic' | 'prismatic' | 'exotic'
    colored_name: pre-formatted name with color tags (from format_ore_name etc.)
    """
    if rarity not in ("mythic", "prismatic", "exotic"):
        return

    display = colored_name or item_name

    if rarity == "mythic":
        await _do_mythic_hype(bot, username, user_id, source, item_name, display)
    elif rarity == "prismatic":
        await _do_prismatic_hype(bot, username, user_id, source, item_name, display)
    elif rarity == "exotic":
        await _do_exotic_hype(bot, username, user_id, source, item_name, display)


# ---------------------------------------------------------------------------
# Mythic hype — small EmceeBot congrats, per-player 5m cooldown
# ---------------------------------------------------------------------------

async def _do_mythic_hype(bot: BaseBot, username: str, user_id: str,
                            source: str, item_name: str, display: str) -> None:
    if not _mythic_on():
        return
    ck = f"mythic_{user_id}"
    if not _cooldown_passed(ck, 300):    # 5 min per player
        return
    _reset_cooldown(ck)
    import config as _cfg
    if _cfg.BOT_MODE not in ("host", "eventhost"):
        return    # only EmceeBot sends this
    try:
        await bot.highrise.chat(
            f"🌟 Mythic Find!\n"
            f"@{username} found {display}!\nGG!"
        )
    except Exception:
        pass
    _log_hype_event(username, user_id, source, "mythic", item_name, "mythic")


# ---------------------------------------------------------------------------
# Prismatic hype — all bots, 10m global cooldown, subscriber notification
# ---------------------------------------------------------------------------

async def _do_prismatic_hype(bot: BaseBot, username: str, user_id: str,
                               source: str, item_name: str, display: str) -> None:
    if not _prismatic_on():
        return
    if not _cooldown_passed("prismatic_global", 600):   # 10 min global
        return
    _reset_cooldown("prismatic_global")

    import config as _cfg
    mode = _cfg.BOT_MODE

    # EmceeBot sends the main announcement
    if mode in ("host", "eventhost"):
        try:
            await bot.highrise.chat(
                f"🌈 PRISMATIC FIND!\n"
                f"@{username} found {display}!"
            )
        except Exception:
            pass

    # Each bot sends their own reaction
    my_msg = _PRISMATIC_MSGS.get(mode)
    if my_msg and mode not in ("host", "eventhost"):
        try:
            await asyncio.sleep(1.5)
            await bot.highrise.chat(
                f"{my_msg} Congrats @{username}!"
            )
        except Exception:
            pass

    # Queue remaining bots via pending system (if host/EmceeBot)
    if mode in ("host", "eventhost"):
        _queue_bot_reactions(source, "prismatic", item_name, username, _PRISMATIC_MSGS)

    # Subscriber notification
    notif_sent = await _send_hype_notification(bot, source, username, display)
    _log_hype_event(username, user_id, source, "prismatic", item_name,
                    "prismatic", notif_sent=notif_sent)


# ---------------------------------------------------------------------------
# Exotic hype — full room event, soft-pause, all bots, 30m global cooldown
# ---------------------------------------------------------------------------

async def _do_exotic_hype(bot: BaseBot, username: str, user_id: str,
                            source: str, item_name: str, display: str) -> None:
    if not _exotic_on():
        return
    if not _cooldown_passed("exotic_global", 1800):    # 30 min global
        return
    _reset_cooldown("exotic_global")

    import config as _cfg
    mode     = _cfg.BOT_MODE
    duration = _exotic_duration()

    # Activate hype lock (soft-pause for all bots)
    _set_hype_lock(True, f"Exotic hype: @{username} {item_name}")

    try:
        # EmceeBot sends main hype announcement
        if mode in ("host", "eventhost"):
            try:
                await bot.highrise.chat(
                    f"🚨🚨 EXOTIC FIND 🚨🚨\n"
                    f"@{username} just found {display}!\n"
                    f"The room is witnessing history."
                )
            except Exception:
                pass

        # Each bot sends their own hype message
        my_msg = _EXOTIC_MSGS.get(mode)
        if my_msg and mode not in ("host", "eventhost"):
            try:
                await asyncio.sleep(2)
                await bot.highrise.chat(my_msg[:249])
            except Exception:
                pass

        # Queue other bots
        if mode in ("host", "eventhost"):
            _queue_bot_reactions(source, "exotic", item_name, username, _EXOTIC_MSGS)

        # Subscriber notification
        notif_sent = await _send_hype_notification(bot, source, username, display)

        # Wait for hype duration
        await asyncio.sleep(duration)

        _log_hype_event(username, user_id, source, "exotic", item_name,
                        "exotic", muted_room=_exotic_mute(),
                        pause_duration=duration, notif_sent=notif_sent)

        # Resume announcement from EmceeBot
        if mode in ("host", "eventhost"):
            try:
                await bot.highrise.chat(
                    f"✅ Hype break complete.\n"
                    f"Games resumed. GG @{username}!"
                )
            except Exception:
                pass

    finally:
        # ALWAYS clear hype lock
        _set_hype_lock(False)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_hypesettings(bot: BaseBot, user: User) -> None:
    dur = _exotic_duration()
    await _w(bot, user.id,
             f"🔥 Hype Settings\n"
             f"Mythic: {'ON' if _mythic_on() else 'OFF'}\n"
             f"Prismatic: {'ON' if _prismatic_on() else 'OFF'}\n"
             f"Exotic: {'ON' if _exotic_on() else 'OFF'}\n"
             f"Exotic Mute: {'ON' if _exotic_mute() else 'OFF'}\n"
             f"Exotic Duration: {dur}s\n"
             f"Hype Lock: {'ACTIVE' if is_hype_locked() else 'OFF'}")


async def handle_hype(bot: BaseBot, user: User, args: list[str]) -> None:
    if not is_manager(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage:\n"
                 "!hype mythic on|off\n"
                 "!hype prismatic on|off\n"
                 "!hype exotic on|off\n"
                 "!hype exoticmute on|off\n"
                 "!hype exoticduration <10-30>")
        return
    sub = args[1].lower()
    val = args[2].lower()

    if sub in ("mythic", "prismatic", "exotic"):
        if val not in ("on", "off"):
            await _w(bot, user.id, "Use on or off.")
            return
        _set_hype_setting(sub, "1" if val == "on" else "0")
        await _w(bot, user.id, f"✅ {sub.title()} hype: {val.upper()}")

    elif sub == "exoticmute":
        _set_hype_setting("exoticmute", "1" if val == "on" else "0")
        await _w(bot, user.id, f"✅ Exotic mute: {val.upper()}")

    elif sub == "exoticduration":
        try:
            secs = max(10, min(30, int(val)))
        except ValueError:
            await _w(bot, user.id, "Usage: !hype exoticduration <10-30>")
            return
        _set_hype_setting("exoticduration", str(secs))
        await _w(bot, user.id, f"✅ Exotic duration: {secs}s")

    else:
        await _w(bot, user.id, "Unknown hype setting. See !hypesettings")


async def handle_hypelog(bot: BaseBot, user: User, args: list[str]) -> None:
    if not is_manager(user.username):
        await _w(bot, user.id, "Manager+ only.")
        return
    sub = args[1].lower() if len(args) >= 2 else "latest"
    target = None
    if sub.startswith("@"):
        target = sub.lstrip("@")
    elif sub != "latest":
        target = sub

    try:
        conn = db.get_connection()
        if target:
            rows = conn.execute(
                "SELECT * FROM hype_events WHERE username=? "
                "ORDER BY id DESC LIMIT 10",
                (target.lower(),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM hype_events ORDER BY id DESC LIMIT 10"
            ).fetchall()
        conn.close()
    except Exception:
        rows = []

    if not rows:
        await _w(bot, user.id, "No hype events logged yet.")
        return
    lines = [f"🔥 Hype Log ({len(rows)})"]
    for r in list(rows)[:5]:
        ts = r["ts"][:16].replace("T", " ")
        lines.append(
            f"@{r['username']} {r['rarity']} [{r['source']}] {ts}"
        )
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_hypeunlock(bot: BaseBot, user: User) -> None:
    if not is_admin(user.username):
        await _w(bot, user.id, "Admin/Owner only.")
        return
    was_locked = is_hype_locked()
    _set_hype_lock(False)
    # Also clear DB setting
    db.set_room_setting("hype_lock", "0")
    await _w(bot, user.id,
             f"✅ Hype lock cleared.\nGames resumed.\n"
             f"Was locked: {'YES' if was_locked else 'NO (already clear)'}")


# Alias for main.py import compatibility
async def handle_hype_cmd(bot: BaseBot, user: User,
                           args: list[str]) -> None:
    await handle_hype(bot, user, args)
