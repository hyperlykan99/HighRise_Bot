"""
modules/first_find.py
---------------------
First-to-Find Race Event system for mining and fishing.

Concept:
  Staff configures a race target and winner count, then starts a timed event.
  Players race during the active window to mine/catch the target rarity or item.
  First X players to match the target win a gold reward.
  The race ends when all slots are filled or time expires.

Flow:
  1. Staff: /setfirstfind mining prismatic 1 5  → creates draft race
  2. Staff: /startfirstfind 30                  → activates for 30 minutes, EmceeBot announces
  3. Player mines prismatic ore during race      → check_race_win() fires
  4. Winner recorded → EmceeBot announces publicly, BankerBot pays via tip_user()
  5. Race completes (all slots filled) or expires (time up) → EmceeBot announces

Role separation:
  EmceeBot  — public announcements (start, winner, end/expiry)
  BankerBot — payout via process_gold_tip_reward() + /firstfindpending retry

SDK note:
  bot.highrise.tip_user() IS supported. BankerBot must hold sufficient gold.
  If tip fails (player left, insufficient gold) → claim stays pending_manual_gold.
  Use !firstfindpending to see pending, /paypendingfirstfind to retry.
"""
from __future__ import annotations
import asyncio
import time
from difflib import get_close_matches

import database as db
from modules.permissions import can_manage_economy, is_owner

_RARITY_ORDER = [
    "common", "uncommon", "rare", "epic",
    "legendary", "mythic", "ultra_rare", "prismatic", "exotic",
]

_RANK_LABELS: dict[int, str] = {1: "1st", 2: "2nd", 3: "3rd"}

_SELF_CMDS = frozenset({
    "setfirstfind", "setfirstfinditem", "setfirstfindreward",
    "startfirstfind", "stopfirstfind", "resetfirstfind",
    "firstfindstatus", "firstfindcheck", "firstfindwinners",
    "firstfindpending", "firstfindpay", "paypendingfirstfind", "retryfirstfind",
    "firstfindrewards", "firstfindlist", "firstfindreward",
})

_SYSTEM_USER = "_SYSTEM_"


async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


def _strip_cmd(args: list[str]) -> list[str]:
    if args and args[0].lower() in _SELF_CMDS:
        return args[1:]
    return list(args)


def _rank(n: int) -> str:
    return _RANK_LABELS.get(n, f"#{n}")


def _target_label(target_type: str, target_value: str) -> str:
    if target_type == "item":
        return target_value
    return f"[{target_value.replace('_', ' ').upper()}]"


def _time_left(ends_at_str: str) -> str:
    """Return human-readable time remaining from ISO ends_at string."""
    if not ends_at_str:
        return "?"
    import datetime
    try:
        ends_at = datetime.datetime.fromisoformat(ends_at_str.replace("Z", "+00:00"))
        # SQLite returns naive UTC strings — treat as UTC
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = ends_at - now
        secs = max(0, int(delta.total_seconds()))
        if secs == 0:
            return "expired"
        mins, s = divmod(secs, 60)
        hrs, m = divmod(mins, 60)
        if hrs:
            return f"{hrs}h {m}m"
        return f"{m}m {s}s"
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# Core win-check — called from mining.py and fishing.py after every drop
# ---------------------------------------------------------------------------

async def check_race_win(bot, user_id: str, username: str,
                         category: str, rarity: str, item_name: str = "") -> None:
    """
    Check if a mining/fishing result wins an active First Find Race.
    Call this after every drop — it returns immediately if no race is active
    or if the drop doesn't match the race target.
    """
    try:
        race = db.get_active_first_find_race()
        if not race:
            return
        if race["category"] != category:
            return

        # Target match
        if race["target_type"] == "rarity":
            if rarity != race["target_value"]:
                return
        elif race["target_type"] == "item":
            if item_name.lower() != race["target_value"].lower():
                return

        # Duplicate winner guard
        if db.has_first_find_race_winner(race["id"], user_id):
            return

        # Slot availability
        winner_count = db.count_first_find_race_winners(race["id"])
        if winner_count >= race["winners_count"]:
            return

        rank = winner_count + 1
        gold_amount = int(race["gold_amount"])
        t_label = _target_label(race["target_type"], race["target_value"])
        verb = "catch" if category == "fishing" else "mine"
        find_word = "First Catch Race" if category == "fishing" else "First Find Race"

        # Record winner
        winner_id = db.add_first_find_race_winner(
            race["id"], user_id, username, rank, category,
            race["target_type"], race["target_value"],
            item_name, rarity, gold_amount,
        )
        if not winner_id:
            return  # race condition — duplicate blocked by unique index

        # Whisper winner immediately from detecting bot
        w_msg = (
            f"🏆 You are {_rank(rank)} in the {find_word}! "
            f"{gold_amount}g reward — BankerBot is processing..."
        )
        await _w(bot, user_id, w_msg)

        # Queue cross-bot: EmceeBot announces, BankerBot pays
        emcee_msg = (
            f"🏆 {find_word} Winner!\n"
            f"@{username} is {_rank(rank)} to {verb} {t_label}!\n"
            f"Reward: {gold_amount}g"
        )
        banker_msg = f"Pay {gold_amount}g to @{username} (race winner #{rank})"
        db.add_first_find_pending(
            race["id"], category, race["target_value"],
            username, user_id, rank, gold_amount, emcee_msg, banker_msg,
        )

        # Auto-complete race if all slots filled
        new_count = winner_count + 1
        if new_count >= race["winners_count"]:
            db.stop_first_find_race(race["id"], "completed")
            completion_msg = (
                f"🏁 {find_word} Complete!\n"
                f"All winners have been found."
            )
            db.add_first_find_pending(
                race["id"], category, "", _SYSTEM_USER, _SYSTEM_USER,
                0, 0, completion_msg, "",
            )
            print(f"[RACE] Race {race['id']} completed — all {race['winners_count']} slots filled.")

    except Exception as exc:
        print(f"[RACE] check_race_win error: {exc}")


# ---------------------------------------------------------------------------
# Backward-compat alias — mining/fishing may still import this name
# ---------------------------------------------------------------------------
async def check_first_find(bot, user_id: str, username: str,
                           category: str, rarity: str) -> None:
    """Deprecated wrapper — calls check_race_win with empty item_name."""
    await check_race_win(bot, user_id, username, category, rarity, "")


# ---------------------------------------------------------------------------
# EmceeBot background poller — announcements + race expiration timer
# ---------------------------------------------------------------------------

async def startup_firstfind_announcer(bot) -> None:
    """EmceeBot: poll pending announcements and handle race expiration."""
    await asyncio.sleep(12)
    last_expire_check = 0.0
    while True:
        try:
            # Drain pending announcements
            rows = db.get_pending_firstfind_for_emcee(limit=3)
            for row in rows:
                try:
                    await bot.highrise.chat(row["emcee_msg"][:249])
                except Exception:
                    pass
                try:
                    db.mark_firstfind_emcee_done(row["id"])
                except Exception:
                    pass
                await asyncio.sleep(2)

            # Expiration check every 60 s
            now = time.time()
            if now - last_expire_check >= 60:
                last_expire_check = now
                expired_races = db.expire_first_find_races()
                for race in expired_races:
                    winners = db.count_first_find_race_winners(race["id"])
                    total   = race["winners_count"]
                    exp_msg = (
                        f"⏰ First Find Race Ended\n"
                        f"Time is up!\n"
                        f"Winners: {winners}/{total}"
                    )
                    try:
                        await bot.highrise.chat(exp_msg[:249])
                    except Exception:
                        pass
                    print(f"[RACE] Race {race['id']} expired — {winners}/{total} winners.")

        except Exception as exc:
            print(f"[FIRSTFIND] announcer error: {exc}")
        await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# BankerBot background poller — pays race winners via tip_user
# ---------------------------------------------------------------------------

async def startup_firstfind_banker(bot) -> None:
    """BankerBot: pay race winners. Reuses process_gold_tip_reward from gold.py."""
    from modules.gold import process_gold_tip_reward  # lazy import avoids circular dep
    await asyncio.sleep(15)
    while True:
        try:
            rows = db.get_pending_firstfind_for_banker(limit=3)
            for row in rows:
                user_id     = row["user_id"]
                username    = row["username"]
                gold_amount = int(row["gold_amount"])
                race_id     = row["reward_id"]  # reward_id stores race_id

                # Skip system-only rows (completion messages, etc.)
                if user_id == _SYSTEM_USER:
                    try:
                        db.mark_firstfind_banker_done(row["id"])
                    except Exception:
                        pass
                    continue

                if gold_amount > 0:
                    status, err = await process_gold_tip_reward(
                        bot, user_id, username, gold_amount,
                        reason="First Find Race reward",
                        source="first_find_race",
                        created_by="BankerBot",
                    )
                else:
                    status, err = "acknowledged", ""

                # Update race winner payout record
                try:
                    winner = db.get_race_winner_by_race_user(race_id, user_id)
                    if winner:
                        db.update_race_winner_payout(winner["id"], status, err)
                    else:
                        # Fallback: try old claims table (legacy rows)
                        db.update_first_find_claim_payout_status(race_id, user_id, status)
                except Exception:
                    pass

                # Whisper player with result
                if status == "paid_gold":
                    msg = f"💰 Race Reward Sent\n@{username} received {gold_amount}g."
                elif status == "acknowledged":
                    msg = f"🏆 Race win noted!"
                else:
                    msg = (
                        f"💰 Race Reward Logged\n"
                        f"@{username} earned {gold_amount}g.\n"
                        f"Status: pending manual tip."
                    )
                    if err:
                        msg = (msg + f"\nReason: {err[:60]}")[:249]

                await _w(bot, user_id, msg)

                try:
                    db.mark_firstfind_banker_done(row["id"])
                except Exception:
                    pass
                await asyncio.sleep(2)

        except Exception as exc:
            print(f"[FIRSTFIND] banker poller error: {exc}")
        await asyncio.sleep(7)


# ---------------------------------------------------------------------------
# /setfirstfind <mining|fishing> <rarity> <winners_count> <gold_amount>
# ---------------------------------------------------------------------------

async def handle_setfirstfind(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    a = _strip_cmd(args)
    usage = (
        "🏁 First Find Race Setup\n"
        "Usage: !setfirstfind <mining|fishing> <rarity> <winners> <gold>\n"
        "Example: /setfirstfind mining prismatic 1 5\n"
        "Then: /startfirstfind <minutes>"
    )
    if len(a) < 4:
        await _w(bot, user.id, usage[:249])
        return

    category = a[0].lower()
    if category not in ("mining", "fishing"):
        await _w(bot, user.id, "Category must be: mining or fishing")
        return

    rarity = a[1].lower()
    if rarity not in _RARITY_ORDER:
        m = get_close_matches(rarity, _RARITY_ORDER, n=1, cutoff=0.6)
        if m:
            await _w(bot, user.id, f"Did you mean: {m[0]}?")
        else:
            await _w(bot, user.id,
                     f"Valid rarities: {', '.join(_RARITY_ORDER)}"[:249])
        return

    if not a[2].isdigit() or int(a[2]) < 1:
        await _w(bot, user.id, "Winners count must be 1 or more.")
        return
    winners_count = int(a[2])

    try:
        gold_amount = float(a[3])
        if gold_amount <= 0:
            raise ValueError
    except ValueError:
        await _w(bot, user.id, "Gold amount must be a positive number.")
        return

    db.create_first_find_race(
        category, "rarity", rarity, winners_count, gold_amount, user.username
    )

    rar_label = rarity.replace("_", " ").title()
    reply = (
        f"🏁 First Find Race Set\n"
        f"Type: {category.title()}\n"
        f"Target: Any {rar_label}\n"
        f"Winners: {winners_count}\n"
        f"Reward: {gold_amount:g} gold\n"
        f"Use !startfirstfind <minutes> to begin."
    )
    await _w(bot, user.id, reply[:249])


# ---------------------------------------------------------------------------
# /setfirstfinditem <mining|fishing> <item name...> <winners_count> <gold_amount>
# ---------------------------------------------------------------------------

async def handle_setfirstfinditem(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    a = _strip_cmd(args)
    usage = (
        "🏁 First Find Race Setup (specific item)\n"
        "Usage: !setfirstfinditem <mining|fishing> <item name> <winners> <gold>\n"
        "Example: /setfirstfinditem mining Prismatic Pearl 1 5"
    )
    if len(a) < 4:
        await _w(bot, user.id, usage[:249])
        return

    category = a[0].lower()
    if category not in ("mining", "fishing"):
        await _w(bot, user.id, "Category must be: mining or fishing")
        return

    # Last two args are count and gold; everything in between is item name
    try:
        gold_amount   = float(a[-1])
        winners_count = int(a[-2])
        if gold_amount <= 0 or winners_count < 1:
            raise ValueError
    except (ValueError, IndexError):
        await _w(bot, user.id, usage[:249])
        return

    item_name = " ".join(a[1:-2]).strip()
    if not item_name:
        await _w(bot, user.id, "Item name cannot be empty.")
        return

    db.create_first_find_race(
        category, "item", item_name, winners_count, gold_amount, user.username
    )

    reply = (
        f"🏁 First Find Race Set\n"
        f"Type: {category.title()}\n"
        f"Target: {item_name}\n"
        f"Winners: {winners_count}\n"
        f"Reward: {gold_amount:g} gold\n"
        f"Use !startfirstfind <minutes> to begin."
    )
    await _w(bot, user.id, reply[:249])


# ---------------------------------------------------------------------------
# /startfirstfind <minutes>
# ---------------------------------------------------------------------------

async def handle_startfirstfind(bot, user, args: list[str]) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    a = _strip_cmd(args)
    if not a or not a[0].isdigit() or int(a[0]) < 1:
        await _w(bot, user.id, "Usage: !startfirstfind <minutes>  (e.g. /startfirstfind 30)")
        return

    minutes = int(a[0])

    race = db.get_draft_first_find_race()
    if not race:
        await _w(bot, user.id,
                 "No race configured. Use !setfirstfind or /setfirstfinditem first.")
        return

    db.start_first_find_race(race["id"], minutes)

    t_label    = _target_label(race["target_type"], race["target_value"])
    cat_label  = race["category"].title()
    verb       = "catch any" if race["category"] == "fishing" else "mine any"
    if race["target_type"] == "item":
        verb = "catch" if race["category"] == "fishing" else "mine"

    winner_txt = f"First {race['winners_count']} player{'s' if race['winners_count'] > 1 else ''}"

    # Announce via EmceeBot (queue cross-bot pending)
    emcee_msg = (
        f"🏁 First Find Race Started!\n"
        f"Target: {verb.title()} {t_label}\n"
        f"Reward: {race['gold_amount']:g} gold\n"
        f"Winners: {winner_txt}\n"
        f"Time: {minutes} minutes — Start now!"
    )
    db.add_first_find_pending(
        race["id"], race["category"], race["target_value"],
        _SYSTEM_USER, _SYSTEM_USER, 0, 0, emcee_msg, "",
    )

    reply = (
        f"🏁 Race started! {emcee_msg[:120]}\n"
        f"EmceeBot will announce."
    )
    await _w(bot, user.id, reply[:249])
    print(f"[RACE] Race {race['id']} started — {cat_label} {t_label} x{race['winners_count']} {minutes}min")


# ---------------------------------------------------------------------------
# /stopfirstfind
# ---------------------------------------------------------------------------

async def handle_stopfirstfind(bot, user) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    race = db.get_active_first_find_race()
    if not race:
        await _w(bot, user.id, "No active race to stop.")
        return

    winners = db.count_first_find_race_winners(race["id"])
    db.stop_first_find_race(race["id"], "stopped")

    stop_msg = f"🏁 First Find Race Ended!\nStopped by staff. Winners: {winners}/{race['winners_count']}"
    db.add_first_find_pending(
        race["id"], race["category"], race["target_value"],
        _SYSTEM_USER, _SYSTEM_USER, 0, 0, stop_msg, "",
    )
    await _w(bot, user.id, f"Race stopped. {winners}/{race['winners_count']} winners found.")
    print(f"[RACE] Race {race['id']} stopped by {user.username}")


# ---------------------------------------------------------------------------
# /firstfindstatus
# ---------------------------------------------------------------------------

async def handle_firstfindstatus(bot, user, args: list[str] = None) -> None:
    race = db.get_latest_first_find_race()
    if not race:
        await _w(bot, user.id, "🏁 No First Find Race configured yet.")
        return

    status    = race["status"].upper()
    cat       = race["category"].title()
    t_label   = _target_label(race["target_type"], race["target_value"])
    winners   = db.count_first_find_race_winners(race["id"])
    total     = race["winners_count"]
    gold_txt  = f"{race['gold_amount']:g}g"
    time_left = _time_left(race["ends_at"]) if race["status"] == "active" else race["status"]

    lines = [
        f"🏁 First Find Race",
        f"Status: {status}",
        f"Type: {cat}",
        f"Target: {t_label}",
        f"Reward: {gold_txt}",
        f"Winners: {winners}/{total}",
    ]
    if race["status"] == "active":
        lines.append(f"Time Left: {time_left}")

    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /firstfindwinners
# ---------------------------------------------------------------------------

async def handle_firstfindwinners(bot, user) -> None:
    race = db.get_latest_first_find_race()
    if not race:
        await _w(bot, user.id, "🏆 No First Find Race found.")
        return

    winners = db.get_first_find_race_winners(race["id"])
    t_label = _target_label(race["target_type"], race["target_value"])

    if not winners:
        await _w(bot, user.id,
                 f"🏆 First Find Race Winners\n"
                 f"Target: {t_label}\nNo winners yet.")
        return

    lines = [f"🏆 First Find Race Winners — {t_label}"]
    for w in winners:
        lines.append(
            f"{w['rank']}. @{w['username']} — {w['gold_amount']:g}g — {w['payout_status']}"
        )
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /resetfirstfind — cancel current race and clear state
# ---------------------------------------------------------------------------

async def handle_resetfirstfind(bot, user, args: list[str] = None) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return
    db.reset_first_find_race()
    await _w(bot, user.id, "♻️ First Find Race reset. All draft/active races cleared.")


# ---------------------------------------------------------------------------
# /firstfindpending — show race winners with pending_manual_gold payout
# ---------------------------------------------------------------------------

async def handle_firstfindpending(bot, user) -> None:
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return
    rows = db.get_pending_race_winners_for_banker(limit=10)
    if not rows:
        rows_old = db.get_first_find_pending_manual()
        if not rows_old:
            await _w(bot, user.id, "💰 No pending first-find gold rewards.")
            return
        lines = ["💰 Pending First Find Tips"]
        for r in rows_old:
            rar_label = r["rarity"].replace("_", " ").title()
            lines.append(
                f"@{r['username']} — {r['gold_amount']:g}g "
                f"— {r['category'].title()} {rar_label}"
            )
        await _w(bot, user.id, "\n".join(lines)[:249])
        return

    lines = ["💰 Pending Race Rewards"]
    for r in rows:
        t_label = _target_label(r["target_type"], r["target_value"])
        lines.append(f"@{r['username']} — {r['gold_amount']:g}g — {t_label}")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /paypendingfirstfind [username] — retry payout for pending race winners
# ---------------------------------------------------------------------------

async def handle_paypendingfirstfind(bot, user, args: list[str]) -> None:
    from modules.gold import process_gold_tip_reward  # lazy import

    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Admin/owner only.")
        return

    a = _strip_cmd(args)
    target_filter = a[0].lower().lstrip("@") if a else None

    rows = db.get_pending_race_winners_for_banker(limit=20)
    if target_filter:
        rows = [r for r in rows if r["username"].lower() == target_filter]

    if not rows:
        label = f" for @{target_filter}" if target_filter else ""
        await _w(bot, user.id, f"No pending race rewards{label}.")
        return

    paid = failed = 0
    for r in rows:
        gold_amount = int(r["gold_amount"])
        if gold_amount < 1:
            continue
        status, err = await process_gold_tip_reward(
            bot, r["user_id"], r["username"], gold_amount,
            reason="First Find Race reward (manual retry)",
            source="first_find_manual",
            created_by=user.username,
        )
        try:
            db.update_race_winner_payout(r["id"], status, err)
        except Exception:
            pass
        if status == "paid_gold":
            paid += 1
            await _w(bot, r["user_id"],
                     f"💰 Race Reward Sent\n@{r['username']} received {gold_amount}g.")
        else:
            failed += 1

    await _w(bot, user.id, f"💰 Race Pay: {paid} sent, {failed} still pending.")


# ---------------------------------------------------------------------------
# Compatibility alias — /firstfindrewards redirects to /firstfindstatus
# ---------------------------------------------------------------------------

async def handle_firstfindrewards(bot, user) -> None:
    await handle_firstfindstatus(bot, user)
