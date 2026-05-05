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


async def _event_timer(bot: BaseBot, event_id: str) -> None:
    """Auto-stop the event after EVENT_DURATION seconds."""
    try:
        await asyncio.sleep(EVENT_DURATION)
        db.clear_active_event()
        name = EVENTS.get(event_id, {}).get("name", event_id)
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
             "🎪 Event Commands:\n"
             "/event — active event & time left\n"
             "/events — available event IDs\n"
             "/eventstatus — full event details\n"
             "/eventpoints — your event pts\n"
             "/eventshop — spend pts\n"
             "/buyevent <id>")


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
    """/startevent <event_id> — manager+, starts event for 1 hour."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    if len(args) < 2:
        ids = ", ".join(EVENTS.keys())
        await _w(bot, user.id, f"Usage: /startevent <id>\nIDs: {ids}"[:249])
        return

    event_id = args[1].lower()
    if event_id not in EVENTS:
        ids = ", ".join(EVENTS.keys())
        await _w(bot, user.id,
                 f"Unknown event: {event_id}\nValid IDs: {ids}"[:249])
        return

    _cancel_event_task()

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=EVENT_DURATION)
    ).isoformat()
    db.set_active_event(event_id, expires_at)

    global _event_task
    _event_task = asyncio.create_task(_event_timer(bot, event_id))

    ev   = EVENTS[event_id]
    name = ev["name"]
    desc = ev["desc"]
    try:
        await bot.highrise.chat(
            f"🎪 {name} is LIVE for 1 hour! {desc} "
            "Use /eventshop to see rewards!"
        )
    except Exception as exc:
        print(f"[EVENTS] startevent announce error: {exc}")


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
# /eventpoints
# ---------------------------------------------------------------------------

async def handle_eventpoints(bot: BaseBot, user: User) -> None:
    db.ensure_user(user.id, user.username)
    pts    = db.get_event_points(user.id)
    active = db.is_event_active()
    status = "🟢 Event ON" if active else "🔴 No event"
    await _w(bot, user.id,
             f"🎪 Event Points: {pts}pts | {status}\n"
             "Earn by winning games or playing BJ/RBJ during Casino Hour.")


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
    await _w(bot, user.id, _SHOP_MSG)


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
