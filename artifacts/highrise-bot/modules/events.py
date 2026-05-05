"""
modules/events.py
-----------------
Limited-time event reward system for the Highrise Mini Game Bot.

Event points are a separate currency earned only during active events:
  - Winning a mini game (trivia/scramble/riddle)  → +1 pt
  - Completing a BJ/RBJ round during Casino Hour  → +1 pt

Casino Hour is defined as when BJ or RBJ is enabled by staff.

Commands (players):
  /eventpoints          — show your event point balance
  /eventshop            — list event shop items and costs
  /buyevent <item_id>   — purchase an event shop item

Commands (admin+):
  /eventstart           — open the event (enable point earning)
  /eventstop            — close the event

Event shop items are cosmetic only and can be equipped with /equip.
"""

from highrise import BaseBot, User

import database as db
from modules.permissions import can_manage_economy


# ---------------------------------------------------------------------------
# Event shop catalog
# ---------------------------------------------------------------------------

EVENT_BADGES: dict[str, dict] = {
    "event_star_badge": {
        "display":    "🌟",
        "event_cost": 50,
        "item_type":  "badge",
        "description": "Event cosmetic",
        "price": 0,
    },
    "party_badge": {
        "display":    "🎉",
        "event_cost": 75,
        "item_type":  "badge",
        "description": "Event cosmetic",
        "price": 0,
    },
}

EVENT_TITLES: dict[str, dict] = {
    "casino_night_title": {
        "display":    "[Casino Night]",
        "event_cost": 100,
        "item_type":  "title",
        "description": "Event cosmetic",
        "price": 0,
    },
    "trivia_champ_title": {
        "display":    "[Trivia Champ]",
        "event_cost": 100,
        "item_type":  "title",
        "description": "Event cosmetic",
        "price": 0,
    },
    "og_guest_title": {
        "display":    "[OG Guest]",
        "event_cost": 250,
        "item_type":  "title",
        "description": "Event cosmetic",
        "price": 0,
    },
}

ALL_EVENT_ITEMS: dict[str, dict] = {**EVENT_BADGES, **EVENT_TITLES}


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


# ---------------------------------------------------------------------------
# /eventpoints
# ---------------------------------------------------------------------------

async def handle_eventpoints(bot: BaseBot, user: User) -> None:
    db.ensure_user(user.id, user.username)
    pts    = db.get_event_points(user.id)
    active = db.is_event_active()
    status = "🟢 Event is ON" if active else "🔴 No active event"
    await _w(bot, user.id,
             f"🎪 Event Points: {pts}pts | {status}\n"
             f"Earn by winning games or playing BJ/RBJ during Casino Hour.")


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

    cost    = item["event_cost"]
    pts     = db.get_event_points(user.id)

    if pts < cost:
        await _w(bot, user.id,
                 f"Not enough event points! Need {cost}pts, you have {pts}pts.")
        return

    result = db.buy_event_item(user.id, user.username, item_id, item["item_type"], cost)

    if result == "duplicate":
        await _w(bot, user.id,
                 f"You already own {item['display']} {item_id}! "
                 f"Equip with: /equip {item['item_type']} {item_id}")
    elif result == "ok":
        new_pts = db.get_event_points(user.id)
        await _w(bot, user.id,
                 f"✅ Purchased {item['display']} {item_id}! "
                 f"Remaining pts: {new_pts}. "
                 f"Equip: /equip {item['item_type']} {item_id}")
    elif result == "no_points":
        await _w(bot, user.id,
                 f"Not enough event points! Need {cost}pts, you have {pts}pts.")
    else:
        await _w(bot, user.id, "Purchase failed. Try again!")


# ---------------------------------------------------------------------------
# /eventstart  (admin+)
# ---------------------------------------------------------------------------

async def handle_eventstart(bot: BaseBot, user: User) -> None:
    if not can_manage_economy(user.id):
        await _w(bot, user.id, "Admin only.")
        return
    db.set_event_active(True)
    await bot.highrise.chat(
        "🎪 Event is LIVE! Earn event points by winning games "
        "and playing BJ/RBJ. Use /eventshop to see rewards!"
    )


# ---------------------------------------------------------------------------
# /eventstop  (admin+)
# ---------------------------------------------------------------------------

async def handle_eventstop(bot: BaseBot, user: User) -> None:
    if not can_manage_economy(user.id):
        await _w(bot, user.id, "Admin only.")
        return
    db.set_event_active(False)
    await _w(bot, user.id, "🎪 Event stopped.")
