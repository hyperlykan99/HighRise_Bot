"""
modules/collection.py — Collection book system (Update 3.1H).

Commands:
  !collection [rare]        — overall book progress / rare-finds log
  !rarelog                  — alias: rare-finds log
  !topcollectors [ore|fish] — discovery leaderboard
  !lastminesummary          — retrieve last auto-mine session summary
  !lastfishsummary          — retrieve last auto-fish session summary
"""
import database as db
from highrise import BaseBot, User


async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg)
    except Exception:
        pass


def _get_mining_total() -> int:
    try:
        tot = sum(db.get_mining_totals_by_rarity().values())
        return tot or 25
    except Exception:
        return 25


def _get_fishing_total() -> int:
    try:
        from modules.fishing import FISH_ITEMS
        return len(FISH_ITEMS)
    except Exception:
        return 50


def _pct(num: int, den: int) -> str:
    if den == 0:
        return "0%"
    return f"{int(num * 100 / den)}%"


async def handle_collection(bot: BaseBot, user: User, args: list) -> None:
    """!collection [rare] — overall collection progress or rare-finds log."""
    try:
        uid = user.id
        sub = args[1].lower() if len(args) > 1 else ""

        if sub in {"rare", "rarelog", "rares"}:
            await _send_rarelog(bot, uid)
            return

        counts = db.get_collection_counts(uid)
        mine_d = counts.get("mining",  0)
        fish_d = counts.get("fishing", 0)
        mine_t = _get_mining_total()
        fish_t = _get_fishing_total()
        total_d = mine_d + fish_d
        total_t = mine_t + fish_t

        if total_d == 0:
            await _w(bot, uid,
                     "📖 Collection Book\n"
                     "No discoveries yet.\n"
                     "Try !mine or !fish to start!")
            return

        await _w(bot, uid, (
            f"📖 Collection Book\n"
            f"⛏️ Mining: {mine_d}/{mine_t} ({_pct(mine_d, mine_t)})\n"
            f"🎣 Fishing: {fish_d}/{fish_t} ({_pct(fish_d, fish_t)})\n"
            f"Total: {total_d}/{total_t} ({_pct(total_d, total_t)})\n"
            f"!orebook  !fishbook  !rarelog"
        )[:249])
    except Exception as exc:
        import traceback; traceback.print_exc()
        await _w(bot, user.id, "⚠️ Could not load collection. Try again.")


async def _send_rarelog(bot: BaseBot, uid: str) -> None:
    """Shared rarelog display used by !collection rare and !rarelog."""
    items = db.get_rare_finds_collection(uid)
    if not items:
        await _w(bot, uid,
                 "✨ Rare Finds\n"
                 "No rare finds yet.\n"
                 "Keep mining & fishing!")
        return
    mine_r = [i for i in items if i["collection_type"] == "mining"]
    fish_r = [i for i in items if i["collection_type"] == "fishing"]
    lines  = ["✨ Rare Finds"]
    for it in mine_r[:4]:
        lines.append(f"⛏️ {it['item_name']} x{it['count']}")
    for it in fish_r[:4]:
        lines.append(f"🎣 {it['item_name']} x{it['count']}")
    if len(items) > 8:
        lines.append(f"+{len(items)-8} more — !orebook rarelog / !fishbook rarelog")
    await _w(bot, uid, "\n".join(lines)[:249])


async def handle_rarelog(bot: BaseBot, user: User, args: list) -> None:
    """!rarelog — combined rare-find log across ores and fish."""
    try:
        await _send_rarelog(bot, user.id)
    except Exception:
        import traceback; traceback.print_exc()
        await _w(bot, user.id, "⚠️ Could not load rare log. Try again.")


async def handle_topcollectors(bot: BaseBot, user: User, args: list) -> None:
    """!topcollectors [ore|fish] — leaderboard by unique discoveries."""
    try:
        sub = args[1].lower() if len(args) > 1 else ""

        if sub in {"ore", "ores", "mining"}:
            ctype  = "mining"
            t      = _get_mining_total()
            header = f"⛏️ Top Ore Collectors (/{t})"
        elif sub in {"fish", "fishing"}:
            ctype  = "fishing"
            t      = _get_fishing_total()
            header = f"🎣 Top Fish Collectors (/{t})"
        else:
            ctype  = None
            mine_t = _get_mining_total()
            fish_t = _get_fishing_total()
            t      = mine_t + fish_t
            header = f"🏆 Top Collectors (/{t})"

        rows = db.get_top_collectors(ctype=ctype, limit=5)
        if not rows:
            await _w(bot, user.id,
                     f"{header}\n"
                     "No data yet.\n"
                     "Mine & fish to start collecting!")
            return

        medals = ["🥇", "🥈", "🥉", "4.", "5."]
        lines  = [header]
        for i, r in enumerate(rows):
            lines.append(f"{medals[i]} @{r['username']} — {r['disc']}/{t}")
        lines.append("!topcollectors ore  !topcollectors fish")
        await _w(bot, user.id, "\n".join(lines)[:249])
    except Exception:
        import traceback; traceback.print_exc()
        await _w(bot, user.id, "⚠️ Could not load leaderboard. Try again.")


async def handle_lastminesummary(bot: BaseBot, user: User, args: list) -> None:
    """!lastminesummary — retrieve last auto-mine session summary."""
    try:
        uid  = user.id
        text = db.get_auto_session_summary(uid, "mining")
        if not text:
            await _w(bot, uid,
                     "⛏️ No saved mining summary yet.\n"
                     "Complete an !automine session first.")
            return
        await _w(bot, uid, text[:249])
    except Exception:
        import traceback; traceback.print_exc()
        await _w(bot, user.id, "⚠️ Could not load summary. Try again.")


async def handle_lastfishsummary(bot: BaseBot, user: User, args: list) -> None:
    """!lastfishsummary — retrieve last auto-fish session summary."""
    try:
        uid  = user.id
        text = db.get_auto_session_summary(uid, "fishing")
        if not text:
            await _w(bot, uid,
                     "🎣 No saved fishing summary yet.\n"
                     "Complete an !autofish session first.")
            return
        await _w(bot, uid, text[:249])
    except Exception:
        import traceback; traceback.print_exc()
        await _w(bot, user.id, "⚠️ Could not load summary. Try again.")
