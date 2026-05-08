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
    # ── Mining-specific events (EventHost owned, stored in mining event table) ──
    "lucky_rush": {
        "name": "Lucky Rush",
        "desc": "Mining luck +25% — better Rare+ drops for all miners.",
        "event_type": "mining",
    },
    "heavy_ore_rush": {
        "name": "Heavy Ore Rush",
        "desc": "+25% heavier ore weights — bigger ores, higher values.",
        "event_type": "mining",
    },
    "ore_value_surge": {
        "name": "Ore Value Surge",
        "desc": "Ore value 1.5x — sell big!",
        "event_type": "mining",
    },
    "double_mxp": {
        "name": "Double Mining XP",
        "desc": "Mining XP 2x — level up faster.",
        "event_type": "mining",
    },
    "mining_haste": {
        "name": "Mining Haste",
        "desc": "Mine cooldown -25% — mine faster.",
        "event_type": "mining",
    },
    "legendary_rush": {
        "name": "Legendary Rush",
        "desc": "+50% Legendary+ drop chance.",
        "event_type": "mining",
    },
    "prismatic_hunt": {
        "name": "Prismatic Hunt",
        "desc": "+100% Prismatic drop chance — chase the rainbow.",
        "event_type": "mining",
    },
    "exotic_hunt": {
        "name": "Exotic Hunt",
        "desc": "+100% Exotic drop chance — rarest finds await.",
        "event_type": "mining",
    },
    "admins_mining_blessing": {
        "name": "Admin's Mining Blessing",
        "desc": (
            "All mining boosts: +50% luck & weight, 2x value & MXP, "
            "-25% cooldown, +50% Leg+, +100% Pris & Exotic."
        ),
        "event_type": "mining",
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

def _apply_mining_event_effects(base: dict, event_id: str) -> None:
    """
    Populate mining-specific effect keys in *base* for a given mining event_id.
    All mining keys must already exist in *base* with defaults before calling.
    """
    if event_id == "lucky_rush":
        base["mining_luck_boost"] = max(base["mining_luck_boost"], 0.25)
    elif event_id == "heavy_ore_rush":
        base["weight_luck_boost"] = max(base["weight_luck_boost"], 0.25)
    elif event_id == "ore_value_surge":
        base["ore_value_multiplier"] = max(base["ore_value_multiplier"], 1.5)
    elif event_id in ("double_mxp", "mining_double_mxp"):
        base["mxp_multiplier"] = max(base["mxp_multiplier"], 2.0)
    elif event_id == "mining_haste":
        base["cooldown_reduction"] = max(base["cooldown_reduction"], 0.25)
    elif event_id == "legendary_rush":
        base["legendary_plus_chance_boost"] = max(
            base["legendary_plus_chance_boost"], 0.50
        )
    elif event_id == "prismatic_hunt":
        base["prismatic_chance_boost"] = max(base["prismatic_chance_boost"], 1.0)
    elif event_id == "exotic_hunt":
        base["exotic_chance_boost"] = max(base["exotic_chance_boost"], 1.0)
    elif event_id == "admins_mining_blessing":
        base["mining_luck_boost"]            = max(base["mining_luck_boost"], 0.50)
        base["weight_luck_boost"]            = max(base["weight_luck_boost"], 0.50)
        base["ore_value_multiplier"]         = max(base["ore_value_multiplier"], 2.0)
        base["mxp_multiplier"]               = max(base["mxp_multiplier"], 2.0)
        base["cooldown_reduction"]           = max(base["cooldown_reduction"], 0.25)
        base["legendary_plus_chance_boost"]  = max(
            base["legendary_plus_chance_boost"], 0.50
        )
        base["prismatic_chance_boost"]       = max(base["prismatic_chance_boost"], 1.0)
        base["exotic_chance_boost"]          = max(base["exotic_chance_boost"], 1.0)


def get_event_effect() -> dict:
    """
    Return active event multipliers. Safe to call from any module.

    General keys (defaults when no event is active):
      coins                     float  1.0   — multiply game coin rewards
      xp                        float  1.0   — multiply game XP awards
      trivia_coins_pct          float  0.0   — extra % on trivia/scramble/riddle
      tax_free                  bool   False — zero /send tax when True
      shop_discount             float  0.0   — fraction off shop prices
      casino_bet_mult           float  1.0   — multiply BJ/RBJ max_bet

    Mining-specific keys:
      mining_luck_boost         float  0.0   — relative bonus to rare+ drop rates
      weight_luck_boost         float  0.0   — relative bonus to ore weight roll
      ore_value_multiplier      float  1.0   — multiply final ore value
      mxp_multiplier            float  1.0   — multiply MXP earned
      cooldown_reduction        float  0.0   — fraction to reduce cooldown (0.25=25%)
      legendary_plus_chance_boost float 0.0 — relative boost to legendary+ drops
      prismatic_chance_boost    float  0.0   — relative boost to prismatic drops
      exotic_chance_boost       float  0.0   — relative boost to exotic drops
      mining_boost              bool   False — legacy: Admin's Blessing 2x mining
    """
    info = db.get_active_event()
    base: dict = {
        "coins":                       1.0,
        "xp":                          1.0,
        "trivia_coins_pct":            0.0,
        "tax_free":                    False,
        "shop_discount":               0.0,
        "casino_bet_mult":             1.0,
        # Mining-specific keys
        "mining_luck_boost":           0.0,
        "weight_luck_boost":           0.0,
        "ore_value_multiplier":        1.0,
        "mxp_multiplier":              1.0,
        "cooldown_reduction":          0.0,
        "legendary_plus_chance_boost": 0.0,
        "prismatic_chance_boost":      0.0,
        "exotic_chance_boost":         0.0,
        "mining_boost":                False,
    }
    if info:
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
            base["trivia_coins_pct"] = 0.5
        elif eid == "shop_sale":
            base["shop_discount"] = 0.20
        elif eid == "admins_blessing":
            base["xp"]               = 2.0
            base["coins"]            = 2.0
            base["tax_free"]         = True
            base["shop_discount"]    = 0.20
            base["casino_bet_mult"]  = 2.0
            base["trivia_coins_pct"] = 0.50
            base["mining_boost"]     = True

    # Also apply active mining event effects
    try:
        mine_ev = db.get_active_mining_event()
        if mine_ev:
            _apply_mining_event_effects(base, mine_ev.get("event_id", ""))
    except Exception:
        pass

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


# ---------------------------------------------------------------------------
# Mining event helpers
# ---------------------------------------------------------------------------

_MINING_EVENT_IDS = {
    "lucky_rush", "heavy_ore_rush", "ore_value_surge", "double_mxp",
    "mining_haste", "legendary_rush", "prismatic_hunt", "exotic_hunt",
    "admins_mining_blessing",
}

# Also include legacy mining events that live in VALID_MINING_EVENTS
_ALL_MINE_IDS = _MINING_EVENT_IDS | {
    "double_ore", "lucky_hour", "energy_free", "meteor_rush",
}


def _start_mining_event(event_id: str, started_by: str, duration_mins: int) -> None:
    """Write a mining event via the existing db.start_mining_event interface."""
    db.start_mining_event(event_id, started_by, duration_mins)


def _format_mining_event_effects(event_id: str) -> str:
    """Return a readable one-liner showing what a mining event does."""
    _map = {
        "lucky_rush":            "🍀 Luck +25%",
        "heavy_ore_rush":        "⚖️ Weight +25%",
        "ore_value_surge":       "💰 Value 1.5x",
        "double_mxp":            "⭐ MXP 2x",
        "mining_haste":          "⏳ Cooldown -25%",
        "legendary_rush":        "💎 Leg+ chance +50%",
        "prismatic_hunt":        "🌈 Prismatic chance +100%",
        "exotic_hunt":           "🔴 Exotic chance +100%",
        "admins_mining_blessing": (
            "🍀+50% ⚖️+50% 💰2x ⭐2x ⏳-25% 💎+50% 🌈+100% 🔴+100%"
        ),
        "double_ore":   "Ore qty 2x",
        "lucky_hour":   "Rare chance +50%",
        "energy_free":  "0 energy cost",
        "meteor_rush":  "Ultra rare chance 2x",
    }
    return _map.get(event_id, event_id)


# ---------------------------------------------------------------------------
# /mineevents  /mineboosts  /luckstatus  — public mining event status
# ---------------------------------------------------------------------------

async def handle_mineevents(bot: BaseBot, user: User) -> None:
    """/mineevents — list all available mining events."""
    lines = ["<#66CCFF>⛏️ Mining Events<#FFFFFF>"]
    for eid in sorted(_ALL_MINE_IDS):
        ev = EVENTS.get(eid, {})
        name = ev.get("name", eid)
        eff  = _format_mining_event_effects(eid)
        lines.append(f"• {eid} — {eff}"[:80])
    lines.append("Start: /startevent <id> <mins>")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_mineboosts(bot: BaseBot, user: User) -> None:
    """/mineboosts — show active mining event boosts."""
    mine_ev = db.get_active_mining_event()
    effect  = get_event_effect()
    boost_lines: list[str] = []

    if mine_ev:
        eid  = mine_ev.get("event_id", "?")
        ev   = EVENTS.get(eid, {})
        name = ev.get("name", eid)
        boost_lines.append(f"Event: {name}")
        boost_lines.append(_format_mining_event_effects(eid))

    if effect.get("mining_boost"):
        boost_lines.append("Admin's Blessing: 2x mining qty active")

    # Show individual active boosts
    if effect.get("mining_luck_boost", 0) > 0:
        pct = int(effect["mining_luck_boost"] * 100)
        boost_lines.append(f"🍀 Luck +{pct}%")
    if effect.get("weight_luck_boost", 0) > 0:
        pct = int(effect["weight_luck_boost"] * 100)
        boost_lines.append(f"⚖️ Weight +{pct}%")
    if effect.get("ore_value_multiplier", 1.0) > 1.0:
        boost_lines.append(f"💰 Value {effect['ore_value_multiplier']}x")
    if effect.get("mxp_multiplier", 1.0) > 1.0:
        boost_lines.append(f"⭐ MXP {effect['mxp_multiplier']}x")
    if effect.get("cooldown_reduction", 0) > 0:
        pct = int(effect["cooldown_reduction"] * 100)
        boost_lines.append(f"⏳ Cooldown -{pct}%")
    if effect.get("legendary_plus_chance_boost", 0) > 0:
        pct = int(effect["legendary_plus_chance_boost"] * 100)
        boost_lines.append(f"💎 Leg+ +{pct}%")
    if effect.get("prismatic_chance_boost", 0) > 0:
        pct = int(effect["prismatic_chance_boost"] * 100)
        boost_lines.append(f"🌈 Prismatic +{pct}%")
    if effect.get("exotic_chance_boost", 0) > 0:
        pct = int(effect["exotic_chance_boost"] * 100)
        boost_lines.append(f"🔴 Exotic +{pct}%")

    if not boost_lines:
        await _w(bot, user.id, "⛏️ No active mining boosts right now.")
        return
    header = "<#66CCFF>⛏️ Mining Boosts<#FFFFFF>"
    await _w(bot, user.id, (header + "\n" + " | ".join(boost_lines))[:249])


async def handle_luckstatus(bot: BaseBot, user: User) -> None:
    """/luckstatus — shorthand for /mineboosts."""
    await handle_mineboosts(bot, user)


# ---------------------------------------------------------------------------
# /miningblessing  /luckevent  /miningevent  — event start shortcuts
# ---------------------------------------------------------------------------

async def handle_miningblessing(bot: BaseBot, user: User, args: list[str]) -> None:
    """/miningblessing [minutes] — start Admin's Mining Blessing."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    mins = 30
    if len(args) >= 2 and args[1].isdigit():
        mins = max(1, min(480, int(args[1])))
    _start_mining_event("admins_mining_blessing", user.username, mins)
    eff = _format_mining_event_effects("admins_mining_blessing")
    await _w(bot, user.id,
             f"✅ Admin's Mining Blessing started ({mins}m)!\n{eff}"[:249])
    try:
        await bot.highrise.chat(
            f"🔥 Admin's Mining Blessing is LIVE for {mins}m! "
            "All mining boosts active. /mine now!"[:249]
        )
    except Exception:
        pass


async def handle_luckevent(bot: BaseBot, user: User, args: list[str]) -> None:
    """/luckevent [minutes] — start Lucky Rush mining event."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    mins = 30
    if len(args) >= 2 and args[1].isdigit():
        mins = max(1, min(480, int(args[1])))
    _start_mining_event("lucky_rush", user.username, mins)
    await _w(bot, user.id, f"✅ Lucky Rush started ({mins}m)! 🍀 Luck +25%.")
    try:
        await bot.highrise.chat(
            f"🍀 Lucky Rush is LIVE for {mins}m! Mine Rare+ ore easier. /mine"[:249]
        )
    except Exception:
        pass


async def handle_miningevent_start(
    bot: BaseBot, user: User, args: list[str]
) -> None:
    """/miningevent <event_id> [minutes] — start any mining event."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        ids = ", ".join(sorted(_ALL_MINE_IDS))
        await _w(bot, user.id, f"Usage: /miningevent <id> [mins]\nIDs: {ids}"[:249])
        return
    eid = args[1].lower()
    if eid not in _ALL_MINE_IDS:
        ids = ", ".join(sorted(_ALL_MINE_IDS))
        await _w(bot, user.id,
                 f"Unknown mining event: {eid}\nValid: {ids}"[:249])
        return
    mins = 30
    if len(args) >= 3 and args[2].isdigit():
        mins = max(1, min(480, int(args[2])))
    _start_mining_event(eid, user.username, mins)
    ev   = EVENTS.get(eid, {})
    name = ev.get("name", eid)
    eff  = _format_mining_event_effects(eid)
    await _w(bot, user.id, f"✅ {name} started ({mins}m)!\n{eff}"[:249])
    try:
        await bot.highrise.chat(
            f"⛏️ {name} is LIVE for {mins}m! {eff} /mine now!"[:249]
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /eventmanager  /eventpanel  /eventeffects  — Event Manager panel
# ---------------------------------------------------------------------------

async def handle_eventmanager(bot: BaseBot, user: User) -> None:
    """/eventmanager — show event manager overview."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    gen_ev   = db.get_active_event()
    mine_ev  = db.get_active_mining_event()
    gen_name = "None"
    gen_left = ""
    if gen_ev:
        ev       = EVENTS.get(gen_ev["event_id"], {})
        gen_name = ev.get("name", gen_ev["event_id"])
        gen_left = " (" + _time_remaining(gen_ev["expires_at"]) + ")"
    mine_name = "None"
    if mine_ev:
        ev        = EVENTS.get(mine_ev.get("event_id", ""), {})
        mine_name = ev.get("name", mine_ev.get("event_id", "?"))

    await _w(bot, user.id,
             f"<#FFD700>🎪 Event Manager<#FFFFFF>\n"
             f"Room event: {gen_name}{gen_left}\n"
             f"Mining event: {mine_name}\n"
             f"/eventpanel for full detail | /eventeffects for active boosts"[:249])


async def handle_eventpanel(bot: BaseBot, user: User) -> None:
    """/eventpanel — detailed event panel for staff."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    gen_ev  = db.get_active_event()
    mine_ev = db.get_active_mining_event()
    ev_on   = db.get_room_setting("auto_events_enabled", "0")
    ev_int  = db.get_room_setting("auto_event_interval_hours", "2")

    lines = ["<#FFD700>🎪 Event Panel<#FFFFFF>"]
    if gen_ev:
        ev   = EVENTS.get(gen_ev["event_id"], {})
        name = ev.get("name", gen_ev["event_id"])
        left = _time_remaining(gen_ev["expires_at"])
        lines.append(f"Room: {name} | {left} left")
    else:
        lines.append("Room event: OFF")

    if mine_ev:
        ev   = EVENTS.get(mine_ev.get("event_id", ""), {})
        name = ev.get("name", mine_ev.get("event_id", "?"))
        eff  = _format_mining_event_effects(mine_ev.get("event_id", ""))
        lines.append(f"Mining: {name} | {eff}"[:80])
    else:
        lines.append("Mining event: OFF")

    lines.append(
        f"AutoEvents: {'ON' if ev_on == '1' else 'OFF'} every {ev_int}h"
    )
    lines.append(
        "/startevent <id> <mins> | /stopevent\n"
        "/miningevent <id> <mins>"
    )
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_eventeffects(bot: BaseBot, user: User) -> None:
    """/eventeffects — show all currently active event effects."""
    eff = get_event_effect()
    lines = ["<#FFD700>✨ Active Event Effects<#FFFFFF>"]
    if eff["coins"] != 1.0:
        lines.append(f"💰 Coins: {eff['coins']}x")
    if eff["xp"] != 1.0:
        lines.append(f"⭐ XP: {eff['xp']}x")
    if eff["tax_free"]:
        lines.append("🏦 Tax-Free Banking")
    if eff["shop_discount"] > 0:
        lines.append(f"🛍️ Shop -{int(eff['shop_discount']*100)}%")
    if eff["casino_bet_mult"] != 1.0:
        lines.append(f"🎰 Casino bet {eff['casino_bet_mult']}x")
    if eff["mining_luck_boost"] > 0:
        lines.append(f"🍀 Mining luck +{int(eff['mining_luck_boost']*100)}%")
    if eff["weight_luck_boost"] > 0:
        lines.append(f"⚖️ Weight +{int(eff['weight_luck_boost']*100)}%")
    if eff["ore_value_multiplier"] > 1.0:
        lines.append(f"💎 Ore value {eff['ore_value_multiplier']}x")
    if eff["mxp_multiplier"] > 1.0:
        lines.append(f"⛏️ MXP {eff['mxp_multiplier']}x")
    if eff["cooldown_reduction"] > 0:
        lines.append(f"⏳ Cooldown -{int(eff['cooldown_reduction']*100)}%")
    if eff["legendary_plus_chance_boost"] > 0:
        lines.append(f"👑 Leg+ +{int(eff['legendary_plus_chance_boost']*100)}%")
    if eff["prismatic_chance_boost"] > 0:
        lines.append(f"🌈 Prismatic +{int(eff['prismatic_chance_boost']*100)}%")
    if eff["exotic_chance_boost"] > 0:
        lines.append(f"🔴 Exotic +{int(eff['exotic_chance_boost']*100)}%")
    if eff.get("mining_boost"):
        lines.append("⚒️ Admin's Blessing: 2x ore qty")
    if len(lines) == 1:
        lines.append("No active effects.")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /autoeventstatus  /autoeventadd  /autoeventremove  /autoeventinterval
# ---------------------------------------------------------------------------

async def handle_autoeventstatus(bot: BaseBot, user: User) -> None:
    """/autoeventstatus — show auto-event configuration."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    ev_on  = db.get_room_setting("auto_events_enabled", "0")
    ev_int = db.get_room_setting("auto_event_interval_hours", "2")
    pool_raw = db.get_room_setting("auto_event_pool", "")
    pool = pool_raw.split(",") if pool_raw else []
    pool_str = ", ".join(pool) if pool else "default rotation"
    await _w(bot, user.id,
             f"<#FFD700>🎪 Auto-Events<#FFFFFF>\n"
             f"Status: {'ON' if ev_on == '1' else 'OFF'}\n"
             f"Interval: every {ev_int}h\n"
             f"Pool: {pool_str}"[:249])


async def handle_autoeventadd(bot: BaseBot, user: User, args: list[str]) -> None:
    """/autoeventadd <event_id> — add event to auto-rotation pool."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /autoeventadd <event_id>")
        return
    eid = args[1].lower()
    if eid not in EVENTS:
        await _w(bot, user.id, f"Unknown event: {eid}")
        return
    pool_raw = db.get_room_setting("auto_event_pool", "")
    pool = [e for e in pool_raw.split(",") if e] if pool_raw else []
    if eid not in pool:
        pool.append(eid)
        db.set_room_setting("auto_event_pool", ",".join(pool))
    await _w(bot, user.id, f"✅ Added {eid} to auto-event pool. Pool: {len(pool)} event(s).")


async def handle_autoeventremove(bot: BaseBot, user: User, args: list[str]) -> None:
    """/autoeventremove <event_id> — remove event from auto-rotation pool."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /autoeventremove <event_id>")
        return
    eid = args[1].lower()
    pool_raw = db.get_room_setting("auto_event_pool", "")
    pool = [e for e in pool_raw.split(",") if e] if pool_raw else []
    if eid in pool:
        pool.remove(eid)
        db.set_room_setting("auto_event_pool", ",".join(pool))
        await _w(bot, user.id, f"✅ Removed {eid}. Pool now: {len(pool)} event(s).")
    else:
        await _w(bot, user.id, f"{eid} not in pool.")


async def handle_autoeventinterval(bot: BaseBot, user: User, args: list[str]) -> None:
    """/autoeventinterval <hours> — set auto-event rotation interval."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2 or not args[1].isdigit():
        await _w(bot, user.id, "Usage: /autoeventinterval <hours>  (1-48)")
        return
    hrs = max(1, min(48, int(args[1])))
    db.set_room_setting("auto_event_interval_hours", str(hrs))
    await _w(bot, user.id, f"✅ Auto-event interval set to {hrs}h.")
