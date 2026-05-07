"""
modules/events.py
-----------------
Limited-time event reward system for the Highrise Mini Game Bot.

Event types (1 hour each):
  double_xp     — 2x XP from all games
  double_coins  — 2x coins from all games
  casino_hour   — +1 event pt per BJ/RBJ round
  tax_free_bank — no tax on /send transfers
  trivia_party  — bonus event pts for trivia wins
  shop_sale     — 20% off all shop items

Event points are a separate currency earned only during active events.
Casino Hour: +1 pt per BJ/RBJ round.
Trivia Party: +1 bonus pt per trivia/scramble/riddle win.

Commands (public):
  /event          — show active event & time left, or "No event active."
  /events         — list available event IDs
  /eventhelp      — show event commands
  /eventstatus    — detailed active event status
  /eventpoints    — your event point balance
  /eventshop      — event shop catalog
  /buyevent <id>  — purchase an event shop item

Commands (manager/admin/owner):
  /startevent <event_id>  — start named event for 1 hour
  /stopevent              — stop the active event immediately
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

from highrise import BaseBot, User

import database as db
from modules.permissions import can_manage_economy


# ---------------------------------------------------------------------------
# Event catalog
# ---------------------------------------------------------------------------

EVENTS: dict[str, dict] = {
    "double_xp": {
        "name": "Double XP",
        "desc": "Earn 2x XP from all games for 1 hour.",
    },
    "double_coins": {
        "name": "Double Coins",
        "desc": "Earn 2x coins from all games for 1 hour.",
    },
    "casino_hour": {
        "name": "Casino Hour",
        "desc": "+1 event point per BJ/RBJ round during this hour.",
    },
    "tax_free_bank": {
        "name": "Tax-Free Banking",
        "desc": "No tax on /send transfers for 1 hour.",
    },
    "trivia_party": {
        "name": "Trivia Party",
        "desc": "Bonus event points for trivia/scramble/riddle wins.",
    },
    "shop_sale": {
        "name": "Shop Sale",
        "desc": "20% off all shop items for 1 hour.",
    },
    "admins_blessing": {
        "name": "Admin's Blessing",
        "desc": "2x XP, 2x Coins, 2x Mining, Tax-Free, 20% Shop Sale, +1 event pt/round. "
                "All boosts active simultaneously!",
    },
}

EVENT_DURATION = 3600  # seconds (1 hour)


# ---------------------------------------------------------------------------
# Event shop catalog  (kept from original)
# ---------------------------------------------------------------------------

EVENT_BADGES: dict[str, dict] = {
    "event_star_badge": {
        "display":     "🌟",
        "event_cost":  50,
        "item_type":   "badge",
        "description": "Event cosmetic",
        "price":       0,
    },
    "party_badge": {
        "display":     "🎉",
        "event_cost":  75,
        "item_type":   "badge",
        "description": "Event cosmetic",
        "price":       0,
    },
}

EVENT_TITLES: dict[str, dict] = {
    "casino_night_title": {
        "display":     "[Casino Night]",
        "event_cost":  100,
        "item_type":   "title",
        "description": "Event cosmetic",
        "price":       0,
    },
    "trivia_champ_title": {
        "display":     "[Trivia Champ]",
        "event_cost":  100,
        "item_type":   "title",
        "description": "Event cosmetic",
        "price":       0,
    },
    "og_guest_title": {
        "display":     "[OG Guest]",
        "event_cost":  250,
        "item_type":   "title",
        "description": "Event cosmetic",
        "price":       0,
    },
}

ALL_EVENT_ITEMS: dict[str, dict] = {**EVENT_BADGES, **EVENT_TITLES}


# ---------------------------------------------------------------------------
# Event effect helper  (imported by games, bank, shop, blackjack)
# ---------------------------------------------------------------------------

def get_event_effect() -> dict:
    """
    Return active event multipliers. Safe to call from any module.

    Keys (defaults when no event is active):
      coins           float  1.0   — multiply game coin rewards by this
      xp              float  1.0   — multiply game XP awards by this
      trivia_coins_pct float 0.0   — extra % on trivia/scramble/riddle coins
      tax_free        bool  False  — zero /send tax when True
      shop_discount   float 0.0   — fraction to subtract from shop prices
      casino_bet_mult float 1.0   — multiply BJ/RBJ max_bet by this
    """
    info = db.get_active_event()
    base: dict = {
        "coins":           1.0,
        "xp":              1.0,
        "trivia_coins_pct": 0.0,
        "tax_free":        False,
        "shop_discount":   0.0,
        "casino_bet_mult": 1.0,
    }
    if not info:
        return base
    eid = info["event_id"]
    if eid == "double_xp":
        base["xp"] = 2.0
    elif eid == "double_coins":
        base["coins"] = 2.0
    elif eid == "casino_hour":
        base["casino_bet_mult"] = 2.0
    elif eid == "tax_free_bank":
        base["tax_free"] = True
    elif eid == "trivia_party":
        base["trivia_coins_pct"] = 0.5   # +50% coins on game wins
    elif eid == "shop_sale":
        base["shop_discount"] = 0.20     # 20% off shop items
    elif eid == "admins_blessing":
        base["xp"]              = 2.0
        base["coins"]           = 2.0
        base["tax_free"]        = True
        base["shop_discount"]   = 0.20
        base["casino_bet_mult"] = 2.0
        base["trivia_coins_pct"] = 0.50
        base["mining_boost"]    = True
    return base


# ---------------------------------------------------------------------------
# Asyncio timer
# ---------------------------------------------------------------------------

_event_task: asyncio.Task | None = None


def _cancel_event_task() -> None:
    global _event_task
    if _event_task and not _event_task.done():
        _event_task.cancel()
    _event_task = None


async def _event_timer(
    bot: BaseBot, event_id: str, sleep_seconds: float = EVENT_DURATION
) -> None:
    """
    Auto-stop the event after sleep_seconds.
    Accepts a shorter duration when resuming after a bot restart.
    """
    try:
        await asyncio.sleep(sleep_seconds)
        db.clear_active_event()
        name = EVENTS.get(event_id, {}).get("name", event_id)
        print(f"[EVENTS] Timer expired: '{event_id}' ended naturally.")
        try:
            await bot.highrise.chat(
                f"⏰ {name} event has ended! Thanks for participating. "
                "Use /eventshop to spend your event points."
            )
        except Exception as exc:
            print(f"[EVENTS] timer chat error: {exc}")
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


def _time_remaining(expires_at_iso: str) -> str:
    try:
        expires = datetime.fromisoformat(expires_at_iso)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        secs = max(0, int((expires - datetime.now(timezone.utc)).total_seconds()))
        m, s = divmod(secs, 60)
        h, m = divmod(m, 60)
        return f"{h}h {m}m" if h else f"{m}m {s}s"
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# Public commands
# ---------------------------------------------------------------------------

async def handle_event(bot: BaseBot, user: User) -> None:
    """/event — show active event and time left."""
    info = db.get_active_event()
    if not info:
        await _w(bot, user.id,
                 "No event active. Staff can start one with /startevent <id>.")
        return
    ev   = EVENTS.get(info["event_id"], {})
    name = ev.get("name", info["event_id"])
    desc = ev.get("desc", "")
    left = _time_remaining(info["expires_at"])
    await _w(bot, user.id,
             f"🎪 Active: {name}\n"
             f"{desc}\n"
             f"⏰ Time left: {left}")


async def handle_events(bot: BaseBot, user: User) -> None:
    """/events — list available event IDs."""
    lines = "\n".join(f"• {eid} — {ev['name']}" for eid, ev in EVENTS.items())
    await _w(bot, user.id, f"🎪 Event IDs:\n{lines}"[:249])


async def handle_eventhelp(bot: BaseBot, user: User) -> None:
    """/eventhelp — show event command reference."""
    await _w(bot, user.id,
             "🎉 Events\n"
             "/event /events\n"
             "Auto events every 1h.\n"
             "Staff: /startevent /stopevent /autoevents\n"
             "/eventstatus /eventpoints\n"
             "/eventshop /buyevent <id>")


async def handle_eventstatus(bot: BaseBot, user: User) -> None:
    """/eventstatus — detailed active event info."""
    info = db.get_active_event()
    if not info:
        await _w(bot, user.id, "🔴 No event is currently active.")
        return
    ev   = EVENTS.get(info["event_id"], {})
    name = ev.get("name", info["event_id"])
    desc = ev.get("desc", "")
    left = _time_remaining(info["expires_at"])
    pts  = db.get_event_points(user.id)
    await _w(bot, user.id,
             f"🟢 Event: {name} [{info['event_id']}]\n"
             f"{desc}\n"
             f"⏰ Remaining: {left}\n"
             f"Your event pts: {pts}")


# ---------------------------------------------------------------------------
# Staff commands
# ---------------------------------------------------------------------------

async def handle_startevent(bot: BaseBot, user: User, args: list[str]) -> None:
    """/startevent <event_id> [minutes] — manager+; optional custom duration."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    if len(args) < 2:
        ids = ", ".join(EVENTS.keys())
        await _w(bot, user.id, f"Usage: /startevent <id> [mins]\nIDs: {ids}"[:249])
        return

    event_id = args[1].lower()
    if event_id not in EVENTS:
        ids = ", ".join(EVENTS.keys())
        await _w(bot, user.id,
                 f"Unknown event: {event_id}\nValid IDs: {ids}"[:249])
        return

    duration = EVENT_DURATION
    if len(args) >= 3 and args[2].isdigit():
        mins = int(args[2])
        if 1 <= mins <= 480:
            duration = mins * 60
        else:
            await _w(bot, user.id, "Duration must be 1-480 minutes.")
            return

    _cancel_event_task()

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=duration)
    ).isoformat()
    db.set_active_event(event_id, expires_at)

    global _event_task
    _event_task = asyncio.create_task(_event_timer(bot, event_id, duration))

    ev        = EVENTS[event_id]
    name      = ev["name"]
    desc      = ev["desc"]
    dur_label = f"{duration // 60}min" if duration != EVENT_DURATION else "1 hour"
    try:
        await bot.highrise.chat(
            f"🎪 {name} is LIVE for {dur_label}! {desc[:100]} "
            "Use /eventshop for rewards!"[:249]
        )
    except Exception as exc:
        print(f"[EVENTS] startevent announce error: {exc}")


async def handle_adminsblessing(bot: BaseBot, user: User, args: list[str]) -> None:
    """/adminsblessing [minutes] — shortcut for /startevent admins_blessing."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    mins_arg = args[1] if len(args) >= 2 else "60"
    await handle_startevent(bot, user, [args[0], "admins_blessing", mins_arg])


async def handle_stopevent(bot: BaseBot, user: User) -> None:
    """/stopevent — manager+, stop the active event immediately."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    info = db.get_active_event()
    if not info:
        await _w(bot, user.id, "No event is currently active.")
        return

    _cancel_event_task()
    db.clear_active_event()

    name = EVENTS.get(info["event_id"], {}).get("name", info["event_id"])
    try:
        await bot.highrise.chat(f"🛑 {name} event stopped by staff.")
    except Exception as exc:
        print(f"[EVENTS] stopevent announce error: {exc}")


# ---------------------------------------------------------------------------
# Event resume / autogame status helpers
# ---------------------------------------------------------------------------

async def handle_eventresume(bot: BaseBot, user: User) -> None:
    """/eventresume — re-announce the active event if one is running."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    info = db.get_active_event()
    if not info:
        await _w(bot, user.id, "No event is currently active.")
        return
    ev   = EVENTS.get(info["event_id"], {})
    name = ev.get("name", info["event_id"])
    desc = ev.get("desc", "")
    try:
        await bot.highrise.chat(
            f"🎪 {name} event is ACTIVE! {desc[:100]} "
            "Use /eventshop for rewards!"[:249]
        )
    except Exception as exc:
        print(f"[EVENTS] eventresume announce error: {exc}")
    await _w(bot, user.id, f"✅ Re-announced: {name}")


async def handle_autogamestatus(bot: BaseBot, user: User) -> None:
    """/autogamestatus — show auto-game loop settings."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    bj_on  = db.get_room_setting("auto_bj_enabled", "0")
    rbj_on = db.get_room_setting("auto_rbj_enabled", "0")
    pk_on  = db.get_room_setting("auto_poker_enabled", "0")
    ev_on  = db.get_room_setting("auto_events_enabled", "0")
    ev_int = db.get_room_setting("auto_event_interval_hours", "2")
    await _w(bot, user.id,
             f"🎮 AutoGames: BJ={bj_on} RBJ={rbj_on} Poker={pk_on} | "
             f"AutoEvents={ev_on} every {ev_int}h")


async def handle_autogameresume(bot: BaseBot, user: User) -> None:
    """/autogameresume — trigger auto-game and auto-event loops to re-check."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    from modules.auto_games import start_auto_game_loop, start_auto_event_loop
    start_auto_game_loop(bot)
    start_auto_event_loop(bot)
    await _w(bot, user.id, "✅ Auto-game and auto-event loops restarted.")


# ---------------------------------------------------------------------------
# Startup recovery — called from HangoutBot.on_start
# ---------------------------------------------------------------------------

async def startup_event_check(bot: BaseBot) -> None:
    """
    Called once at bot startup (after DB is initialised).

    Reads raw event_settings from SQLite and decides:
      - If no event was flagged active → nothing to do.
      - If active but expires_at has already passed → clear DB, log.
      - If active and time remains → restart the async timer for the
        remaining seconds so the event ends cleanly.

    Does NOT call get_active_event() because that auto-clears the DB
    before we can compute how much time is left.
    """
    global _event_task

    conn = db.get_connection()
    rows = {
        r["key"]: r["value"]
        for r in conn.execute("SELECT key, value FROM event_settings").fetchall()
    }
    conn.close()

    if rows.get("event_active") != "1":
        return  # no lingering event

    event_id   = rows.get("event_name", "")
    expires_at = rows.get("event_expires_at", "")

    if not event_id or not expires_at:
        db.clear_active_event()
        print("[EVENTS] Startup: malformed event record, cleared.")
        return

    try:
        exp = datetime.fromisoformat(expires_at)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
    except Exception:
        db.clear_active_event()
        print("[EVENTS] Startup: unparseable expires_at, cleared.")
        return

    now       = datetime.now(timezone.utc)
    remaining = (exp - now).total_seconds()

    if remaining <= 0:
        # Event expired during downtime
        db.clear_active_event()
        name = EVENTS.get(event_id, {}).get("name", event_id)
        print(f"[EVENTS] Startup: '{event_id}' expired during downtime, cleared.")
        try:
            await bot.highrise.chat(
                f"🎉 Event ended: {name}. Use /eventshop to spend your pts!"
            )
        except Exception as exc:
            print(f"[EVENTS] Startup announce error: {exc}")
        return

    # Event is still live — restart the countdown timer
    _cancel_event_task()
    _event_task = asyncio.create_task(
        _event_timer(bot, event_id, remaining)
    )
    name = EVENTS.get(event_id, {}).get("name", event_id)
    m, s = divmod(int(remaining), 60)
    h, m = divmod(m, 60)
    left = f"{h}h {m}m" if h else f"{m}m {s}s"
    print(f"[EVENTS] Startup: resumed '{event_id}' — {left} remaining.")


# ---------------------------------------------------------------------------
# /eventpoints
# ---------------------------------------------------------------------------

async def handle_eventpoints(bot: BaseBot, user: User, args: list[str] | None = None) -> None:
    from modules.permissions import is_manager, can_manage_economy
    db.ensure_user(user.id, user.username)

    # /eventpoints <username>  — manager/admin/owner can view others
    if args and len(args) >= 2:
        if not (is_manager(user.username) or can_manage_economy(user.username)):
            await _w(bot, user.id, "Manager, admin, or owner only to view other players.")
            return
        target_name = args[1].lstrip("@").strip()
        pts = db.get_event_points_for_user(target_name)
        if pts is None:
            await _w(bot, user.id, f"❌ @{target_name} not found.")
            return
        active = db.is_event_active()
        status = "🟢 Event ON" if active else "🔴 No event"
        await _w(bot, user.id, f"🎟️ @{target_name} event coins: {pts:,} | {status}")
        return

    pts    = db.get_event_points(user.id)
    active = db.is_event_active()
    status = "🟢 Event ON" if active else "🔴 No event"
    await _w(bot, user.id, f"🎟️ Event coins: {pts:,} | {status}")


# ---------------------------------------------------------------------------
# /eventshop
# ---------------------------------------------------------------------------

_SHOP_MSG = (
    "🎪 Event Shop:\n"
    "🌟 event_star_badge 50pts\n"
    "🎉 party_badge 75pts\n"
    "[Casino Night] casino_night_title 100pts\n"
    "[Trivia Champ] trivia_champ_title 100pts\n"
    "[OG Guest] og_guest_title 250pts\n"
    "Use /buyevent <id>"
)


async def handle_eventshop(bot: BaseBot, user: User) -> None:
    """Show numbered event shop and save session."""
    import database as _db

    active = _db.is_event_active()
    status = "🟢 Active" if active else "🔴 No event"

    session_items = []
    lines = [f"🎪 Event Shop — {status}"]

    for num, (item_id, item) in enumerate(ALL_EVENT_ITEMS.items(), 1):
        display = item["display"]
        cost    = item["event_cost"]
        lines.append(f"{num} {display} {item_id} {cost}EC")
        session_items.append({
            "num":       num,
            "item_id":   item_id,
            "name":      display,
            "emoji":     display,
            "price":     cost,
            "currency":  "event_coins",
            "shop_type": "event",
        })

    lines.append("Buy: /buy <#>")
    msg = "\n".join(lines)
    if len(msg) > 249:
        msg = msg[:249]

    _db.save_shop_session(user.username, "event", 1, session_items)
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /buyevent <item_id>
# ---------------------------------------------------------------------------

async def handle_buyevent(bot: BaseBot, user: User, args: list[str]) -> None:
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /buyevent <item_id>  |  See /eventshop")
        return

    db.ensure_user(user.id, user.username)
    item_id = args[1].lower()
    item    = ALL_EVENT_ITEMS.get(item_id)

    if item is None:
        await _w(bot, user.id, f"Unknown item: {item_id}  |  See /eventshop")
        return

    if not db.is_event_active():
        await _w(bot, user.id, "No event is active right now!")
        return

    cost = item["event_cost"]
    pts  = db.get_event_points(user.id)

    if pts < cost:
        await _w(bot, user.id,
                 f"Not enough pts! Need {cost}, you have {pts}.")
        return

    result = db.buy_event_item(user.id, user.username, item_id, item["item_type"], cost)

    if result == "duplicate":
        await _w(bot, user.id,
                 f"Already own {item['display']} {item_id}. "
                 f"Equip: /equip {item['item_type']} {item_id}")
    elif result == "ok":
        new_pts = db.get_event_points(user.id)
        await _w(bot, user.id,
                 f"✅ Bought {item['display']} {item_id}! "
                 f"Pts left: {new_pts}. "
                 f"Equip: /equip {item['item_type']} {item_id}")
    elif result == "no_points":
        await _w(bot, user.id,
                 f"Not enough pts! Need {cost}, you have {pts}.")
    else:
        await _w(bot, user.id, "Purchase failed. Try again!")
