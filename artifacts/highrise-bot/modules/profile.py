"""
modules/profile.py — 3.1L Player Profile + Social Identity Polish

Public commands:
  !profile [page]          — own public card (pages 1-6 for legacy detail)
  !profile @user [page]   — other player's public card
  !profile private         — own detailed private whisper (same as !stats)
  !profile settings [...]  — profile privacy settings
  !profile help            — profile help
  !me / !myprofile         — alias for !profile
  !stats                   — private detailed profile
  !flex [@user]            — social flex card (public, 30s cooldown)
  !showoff / !card         — alias for !flex

Staff commands:
  !profileadmin <sub> [@user] — admin profile tools
  !profileprivacy @user       — view target's privacy (mod+)
  !resetprofileprivacy @user  — reset privacy to defaults (admin+)

All messages <= 249 chars. Missing data handled gracefully.
Currencies: 🪙 ChillCoins  🎫 Luxe Tickets
"""

import time
import asyncio
from datetime import datetime, timezone

from highrise import BaseBot, User

import database as db
from modules.permissions import is_admin, is_owner, can_moderate
from economy import fmt_coins


# ---------------------------------------------------------------------------
# Rank helpers
# ---------------------------------------------------------------------------

_REP_RANKS: list[tuple[int, str]] = [
    (500, "Celebrity"),
    (250, "Icon"),
    (100, "Loved"),
    (50,  "Popular"),
    (25,  "Known"),
    (10,  "Friendly"),
    (0,   "New Face"),
]

_LEVEL_RANKS: list[tuple[int, str]] = [
    (50, "Legend"),
    (25, "Veteran"),
    (10, "Grinder"),
    (3,  "Regular"),
    (0,  "Newbie"),
]

_CASINO_RANKS: list[tuple[int, str]] = [
    (100_000, "Whale"),
    (10_000,  "High Roller"),
    (1_000,   "Lucky"),
    (0,       "New Gambler"),
]

# Level-based default titles (3.1L)
_LEVEL_TITLES: list[tuple[int, str]] = [
    (100, "Mythic Collector"),
    (50,  "ChillTopia Legend"),
    (25,  "Treasure Hunter"),
    (15,  "Skilled Angler"),
    (10,  "Lucky Miner"),
    (5,   "Rookie Explorer"),
]


def _rep_rank(rep: int) -> str:
    for threshold, name in _REP_RANKS:
        if rep >= threshold:
            return name
    return "New Face"


def _level_rank(level: int) -> str:
    for threshold, name in _LEVEL_RANKS:
        if level >= threshold:
            return name
    return "Newbie"


def _casino_rank(net: int) -> str:
    if net <= -10_000:
        return "Bankrupt"
    for threshold, name in _CASINO_RANKS:
        if net >= threshold:
            return name
    return "New Gambler"


def _staff_role(username: str) -> str:
    from modules.permissions import (
        is_owner as _io, is_admin as _ia,
        is_manager as _im, is_moderator as _imod,
    )
    if _io(username):   return "Owner"
    if _ia(username):   return "Admin"
    if _im(username):   return "Manager"
    if _imod(username): return "Moderator"
    return "Player"


def _default_title(level: int) -> str | None:
    """Return the highest level-unlocked default title, or None."""
    for threshold, title in _LEVEL_TITLES:
        if level >= threshold:
            return title
    return None


def _is_vip(user_id: str) -> bool:
    try:
        return db.owns_item(user_id, "vip")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Whisper helper
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 3.1L Data helpers
# ---------------------------------------------------------------------------

def _vip_status_str(user_id: str) -> str:
    """Return 'Lifetime', 'Active Xd Yh', or 'Inactive'."""
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT vip_expires_at FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        if row and row["vip_expires_at"]:
            exp_str = row["vip_expires_at"]
            exp = datetime.fromisoformat(exp_str)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            now  = datetime.now(timezone.utc)
            if exp > now:
                secs = int((exp - now).total_seconds())
                days, rem = divmod(secs, 86400)
                hours = rem // 3600
                if days >= 365 * 10:
                    return "Lifetime"
                if days > 0:
                    return f"Active {days}d {hours}h"
                return f"Active {hours}h"
    except Exception:
        pass
    if _is_vip(user_id):
        return "Active"
    return "Inactive"


def _luxe_tickets(user_id: str) -> int:
    """Return Luxe Ticket balance from premium_balances."""
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT luxe_tickets FROM premium_balances WHERE user_id=?",
            (user_id,),
        ).fetchone()
        conn.close()
        return int(row["luxe_tickets"]) if row else 0
    except Exception:
        return 0


def _collection_info(user_id: str) -> tuple[int, int, int, int]:
    """Return (mine_found, mine_total, fish_found, fish_total)."""
    try:
        counts  = db.get_collection_counts(user_id)
        mine_d  = counts.get("mining", 0)
        fish_d  = counts.get("fishing", 0)
    except Exception:
        mine_d = fish_d = 0
    mine_t = fish_t = 0
    try:
        conn   = db.get_connection()
        mine_t = conn.execute("SELECT COUNT(*) AS c FROM mining_items").fetchone()["c"] or 40
        fish_t = conn.execute("SELECT COUNT(*) AS c FROM fishing_items").fetchone()["c"] or 35
        conn.close()
    except Exception:
        mine_t, fish_t = 40, 35
    return mine_d, mine_t, fish_d, fish_t


def _collection_pct(user_id: str) -> int:
    mine_d, mine_t, fish_d, fish_t = _collection_info(user_id)
    total_d = mine_d + fish_d
    total_t = mine_t + fish_t
    if not total_t:
        return 0
    return int(100 * total_d / total_t)


def _mission_progress(user_id: str) -> tuple[int, int, int, int]:
    """Return (daily_done, daily_total, weekly_done, weekly_total)."""
    try:
        from modules.missions import (
            DAILY_MISSIONS, WEEKLY_MISSIONS,
            _daily_period, _weekly_period,
        )
        dk = _daily_period()
        wk = _weekly_period()
        d_done = sum(
            1 for m in DAILY_MISSIONS
            if db.get_mission_progress(user_id, m["key"], dk) >= m["target"]
        )
        w_done = 0
        for m in WEEKLY_MISSIONS:
            if m["key"] == "weekly_streak7":
                try:
                    stats = db.get_daily_stats(user_id)
                    prog  = stats.get("streak", 0)
                except Exception:
                    prog = 0
            else:
                prog = db.get_mission_progress(user_id, m["key"], wk)
            if prog >= m["target"]:
                w_done += 1
        return d_done, len(DAILY_MISSIONS), w_done, len(WEEKLY_MISSIONS)
    except Exception:
        return 0, 5, 0, 5


def _season_rank_str(user_id: str, username: str) -> str:
    """Return 'Mining #4' or 'Unranked'."""
    try:
        from modules.missions import _season_key
        sk   = _season_key()
        cats = ["mining", "fishing", "collection", "trivia", "casino", "tipper"]
        best: list[str] = []
        for cat in cats:
            rows = db.get_season_leaderboard(sk, cat, limit=10)
            for i, r in enumerate(rows):
                if r["username"].lower() == username.lower():
                    best.append(f"{cat.title()} #{i + 1}")
                    break
        return ", ".join(best[:2]) if best else "Unranked"
    except Exception:
        return "Unranked"


def _equipped_badge_display(user_id: str) -> str:
    """Return equipped badge emoji or 'None'."""
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT equipped_badge FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        badge = (row["equipped_badge"] or "").strip() if row else ""
        return badge if badge else "None"
    except Exception:
        return "None"


def _equipped_title_display(user_id: str, level: int = 1) -> str:
    """Return equipped title, or default level-based title, or 'None'."""
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT equipped_title FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        conn.close()
        title = (row["equipped_title"] or "").strip() if row else ""
        if title:
            return title
    except Exception:
        pass
    default = _default_title(level)
    return default if default else "None"


def _get_profile_setting(user_id: str, field: str) -> str:
    """Get a profile setting value from player_profile_settings."""
    _defaults = {
        "balance_visibility":    "private",
        "collection_visibility": "public",
        "badge_visibility":      "public",
        "vip_visibility":        "public",
        "season_visibility":     "public",
        "level_visibility":      "public",
    }
    try:
        conn = db.get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO player_profile_settings (user_id) VALUES (?)",
            (user_id,),
        )
        conn.commit()
        row = conn.execute(
            f"SELECT {field} FROM player_profile_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
        conn.close()
        if row and row[field]:
            return row[field]
    except Exception:
        pass
    return _defaults.get(field, "public")


def _set_profile_setting(user_id: str, username: str, field: str, value: str) -> None:
    _allowed = {
        "balance_visibility", "collection_visibility", "badge_visibility",
        "vip_visibility", "season_visibility", "level_visibility",
    }
    if field not in _allowed:
        return
    try:
        conn = db.get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO player_profile_settings (user_id, username) VALUES (?, ?)",
            (user_id, username.lower()),
        )
        conn.execute(
            f"UPDATE player_profile_settings SET {field}=?, updated_at=datetime('now') WHERE user_id=?",
            (value, user_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Privacy helpers (legacy + new)
# ---------------------------------------------------------------------------

_PRIVACY_FIELDS: dict[str, str] = {
    "money":        "show_money",
    "casino":       "show_casino",
    "achievements": "show_achievements",
    "inventory":    "show_inventory",
}

_PROFILE_SETTING_FIELDS: dict[str, str] = {
    "balance":    "balance_visibility",
    "collection": "collection_visibility",
    "collections": "collection_visibility",
    "badge":      "badge_visibility",
    "badges":     "badge_visibility",
    "vip":        "vip_visibility",
    "season":     "season_visibility",
    "level":      "level_visibility",
}


def _get_privacy(username: str) -> dict:
    return db.get_profile_privacy(username)


def _can_see(field: str, privacy: dict, is_self: bool, is_staff: bool) -> bool:
    if is_self or is_staff:
        return True
    return bool(privacy.get(field, 1))


# ---------------------------------------------------------------------------
# Username resolution
# ---------------------------------------------------------------------------

def _resolve(clean_name: str):
    target = db.get_user_by_username(clean_name)
    if not target:
        return None, clean_name, None
    t_id   = target["user_id"]
    t_name = target["username"]
    p      = db.get_profile(t_id)
    return t_id, t_name, p


def _setup(requester: User, raw_name: str | None):
    if not raw_name:
        t_id   = requester.id
        t_name = requester.username
        p      = db.get_profile(t_id)
    else:
        clean          = raw_name.lstrip("@").strip()
        t_id, t_name, p = _resolve(clean)
        if t_id is None:
            return None, clean, None, False, False, (
                "⚠️ Player not found.\nThey may need to join first."
            )
    if not p:
        return None, t_name if raw_name else requester.username, None, False, False, (
            "⚠️ Player not found.\nThey may need to join first."
        )
    is_self  = (t_id == requester.id)
    is_staff = can_moderate(requester.username) or is_admin(requester.username)
    return t_id, t_name, p, is_self, is_staff, None


# ---------------------------------------------------------------------------
# 3.1L — Polished public card builder
# ---------------------------------------------------------------------------

async def _send_public_card(
    bot: BaseBot, requester_id: str,
    uid: str, uname: str, p: dict,
    is_self: bool, is_staff: bool,
) -> None:
    """Send the 3.1L polished public card (1-2 messages ≤ 220 chars each)."""
    level = p.get("level", 1) or 1
    xp    = p.get("xp", 0) or 0
    try:
        xp_next = db.xp_for_level(level + 1)
    except Exception:
        xp_next = "?"

    title  = _equipped_title_display(uid, level)
    badge  = _equipped_badge_display(uid)
    col_pct = _collection_pct(uid)

    # Visibility rules for PUBLIC card:
    # - is_staff can bypass any setting (admins see everything)
    # - is_self does NOT bypass — public card respects privacy even for owner
    # - Only !stats / !profile private always shows own full data
    vip_pub    = _get_profile_setting(uid, "vip_visibility")    == "public" or is_staff
    badge_pub  = _get_profile_setting(uid, "badge_visibility")  == "public" or is_staff
    level_pub  = _get_profile_setting(uid, "level_visibility")  == "public" or is_staff
    col_pub    = _get_profile_setting(uid, "collection_visibility") == "public" or is_staff
    bal_pub    = _get_profile_setting(uid, "balance_visibility") == "public" or is_staff
    season_pub = _get_profile_setting(uid, "season_visibility") == "public" or is_staff

    vip_str = _vip_status_str(uid) if vip_pub else None

    lines1 = [f"👤 @{uname}"]
    lines1.append(f"Title: {title}")
    if level_pub:
        lines1.append(f"Level: {level} | XP: {xp:,}/{xp_next}")
    if vip_str:
        lines1.append(f"VIP: {vip_str}")
    if badge_pub:
        lines1.append(f"Badges: {badge}")
    if col_pub and col_pct > 0:
        lines1.append(f"Collection: {col_pct}%")
    if not is_self:
        lines1.append(f"!profile @{uname} 2 for more")

    await _w(bot, requester_id, "\n".join(lines1)[:220])

    # Message 2 — progress + optional balance (own profile or staff only)
    # Balance only shows when balance_visibility = public; is_self never overrides.
    if is_self or is_staff:
        d_done, d_total, w_done, w_total = _mission_progress(uid)
        s_rank = _season_rank_str(uid, uname)

        lines2 = ["📊 Progress"]
        lines2.append(f"Daily: {d_done}/{d_total} | Weekly: {w_done}/{w_total}")
        if season_pub:
            lines2.append(f"Season Rank: {s_rank}")
        if bal_pub:
            coins   = db.get_balance(uid)
            tickets = _luxe_tickets(uid)
            lines2.append(f"Balance: {coins:,} 🪙 | {tickets:,} 🎫")

        await _w(bot, requester_id, "\n".join(lines2)[:220])


# ---------------------------------------------------------------------------
# 3.1L — Private detailed card (whisper, up to 3 messages)
# ---------------------------------------------------------------------------

async def _send_private_card(
    bot: BaseBot, uid: str, uname: str, p: dict,
) -> None:
    """Send up to 3 private whisper messages with full profile detail."""
    level = p.get("level", 1) or 1
    xp    = p.get("xp", 0) or 0
    try:
        xp_next = db.xp_for_level(level + 1)
    except Exception:
        xp_next = "?"

    coins   = db.get_balance(uid)
    tickets = _luxe_tickets(uid)
    vip_str = _vip_status_str(uid)

    # Message 1 — identity
    streak = 0
    try:
        ds     = db.get_daily_stats(uid)
        streak = ds.get("streak", 0)
    except Exception:
        pass

    msg1 = (
        f"👤 Private Profile\n"
        f"Level: {level} | XP: {xp:,}/{xp_next}\n"
        f"🪙 {coins:,} | 🎫 {tickets:,}\n"
        f"VIP: {vip_str}"
    )
    await _w(bot, uid, msg1[:220])

    # Message 2 — collection
    mine_d, mine_t, fish_d, fish_t = _collection_info(uid)
    col_pct = 0
    if mine_t + fish_t:
        col_pct = int(100 * (mine_d + fish_d) / (mine_t + fish_t))
    milestones = 0
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT COUNT(*) AS c FROM collection_milestone_claims WHERE user_id=?",
            (uid,),
        ).fetchone()
        conn.close()
        milestones = row["c"] if row else 0
    except Exception:
        pass

    msg2 = (
        f"📖 Collection ({col_pct}%)\n"
        f"Ore: {mine_d}/{mine_t}\n"
        f"Fish: {fish_d}/{fish_t}\n"
        f"Milestones: {milestones} claimed"
    )
    await _w(bot, uid, msg2[:220])

    # Message 3 — progress
    d_done, d_total, w_done, w_total = _mission_progress(uid)
    s_rank = _season_rank_str(uid, uname)

    msg3 = (
        f"📋 Progress\n"
        f"Daily: {d_done}/{d_total}\n"
        f"Weekly: {w_done}/{w_total}\n"
        f"Season: {s_rank}\n"
        f"Streak: Day {streak}"
    )
    await _w(bot, uid, msg3[:220])


# ---------------------------------------------------------------------------
# Flex cooldown
# ---------------------------------------------------------------------------

_flex_cooldowns: dict[str, float] = {}
_FLEX_COOLDOWN = 30.0


# ---------------------------------------------------------------------------
# 3.1L — Flex card
# ---------------------------------------------------------------------------

async def _send_flex_card(
    bot: BaseBot, requester_id: str,
    uid: str, uname: str, p: dict,
) -> None:
    """Send a PUBLIC room flex card — respects privacy, never shows private balance."""
    level = p.get("level", 1) or 1
    title = _equipped_title_display(uid, level)

    # Flex only shows fields that are public (no is_self bypass — this is a public room message)
    badge_pub  = _get_profile_setting(uid, "badge_visibility")      == "public"
    col_pub    = _get_profile_setting(uid, "collection_visibility")  == "public"
    season_pub = _get_profile_setting(uid, "season_visibility")      == "public"

    lines = [f"✨ @{uname} Flex", f"Level {level} {title}"]

    if badge_pub:
        badge = _equipped_badge_display(uid)
        lines.append(f"Badges: {badge}")

    if col_pub:
        col_pct = _collection_pct(uid)
        if col_pct > 0:
            lines.append(f"Collection: {col_pct}%")

    if season_pub:
        s_rank = _season_rank_str(uid, uname)
        lines.append(f"Season: {s_rank}")

    msg = "\n".join(lines)[:220]
    try:
        await bot.highrise.chat(msg)
    except Exception:
        await _w(bot, requester_id, msg)


# ---------------------------------------------------------------------------
# Legacy page builders (kept for backward compat & profileadmin)
# ---------------------------------------------------------------------------

def _fmt_net(n: int) -> str:
    return f"+{n:,}" if n >= 0 else f"{n:,}"


def _build_page1(uid: str, uname: str, p: dict, privacy: dict,
                 is_self: bool, is_staff: bool) -> str:
    try:
        display = db.get_display_name(uid, uname)
    except Exception:
        display = f"@{uname}"
    role   = _staff_role(uname)
    level  = p.get("level", 1) or 1
    xp     = p.get("xp", 0) or 0
    try:
        xp_next = db.xp_for_level(level + 1)
    except Exception:
        xp_next = "?"
    lvrank = _level_rank(level)
    vip    = _vip_status_str(uid)
    rep    = 0
    try:
        rep_row = db.get_reputation(uid)
        rep = rep_row["rep_received"] if rep_row else 0
    except Exception:
        pass
    reprank = _rep_rank(rep)
    first   = str(p.get("first_seen") or "?")[:10]
    title   = _equipped_title_display(uid, level)
    badge   = _equipped_badge_display(uid)
    lines = [
        f"👤 {display}"[:60],
        f"Title: {title} | Badge: {badge}",
        f"Role: {role} | VIP: {vip}",
        f"Lv {level} ({lvrank}) | {xp:,}/{xp_next} XP",
        f"Rep: {reprank} {rep} | Joined: {first}",
    ]
    if not is_self:
        lines.append(f"More: !profile @{uname} 2")
    return "\n".join(lines)[:249]


def _build_page2(uid: str, uname: str, p: dict, privacy: dict,
                 is_self: bool, is_staff: bool) -> str:
    see = _can_see("show_money", privacy, is_self, is_staff)
    if not see:
        return (
            f"💰 Economy — @{uname}\n"
            f"Balance: Hidden\n"
            f"More: !profile @{uname} 3"
        )[:249]
    coins   = db.get_balance(uid)
    tickets = _luxe_tickets(uid)
    earned  = p.get("total_coins_earned", 0)
    wins    = p.get("total_games_won", 0)
    sent = recv = 0
    try:
        bank = db.get_bank_user_stats(uid)
        sent = bank.get("total_sent", 0)
        recv = bank.get("total_received", 0)
    except Exception:
        pass
    streak = 0
    try:
        ds     = db.get_daily_stats(uid)
        streak = ds.get("streak", 0)
    except Exception:
        pass
    lines = [
        f"💰 Economy — @{uname}",
        f"🪙 {coins:,} | 🎫 {tickets:,}",
        f"Earned: {earned:,} 🪙 | Wins: {wins}",
        f"Sent: {sent:,} | Recv: {recv:,} 🪙",
        f"Streak: {streak}d",
        f"More: !profile @{uname} 3",
    ]
    return "\n".join(lines)[:249]


def _build_page3(uid: str, uname: str, p: dict, privacy: dict,
                 is_self: bool, is_staff: bool) -> str:
    see = _can_see("show_casino", privacy, is_self, is_staff)
    if not see:
        return (
            f"🎰 Casino — @{uname}\n"
            f"Casino stats hidden.\n"
            f"More: !profile @{uname} 4"
        )[:249]
    bj_w = bj_l = bj_net = 0
    try:
        bj    = db.get_bj_stats(uid)
        bj_w  = bj.get("bj_wins", 0)
        bj_l  = bj.get("bj_losses", 0)
        bj_net = bj.get("bj_total_won", 0) - bj.get("bj_total_bet", 0)
    except Exception:
        pass
    rbj_w = rbj_l = rbj_net = 0
    try:
        rbj     = db.get_rbj_stats(uid)
        rbj_w   = rbj.get("rbj_wins", 0)
        rbj_l   = rbj.get("rbj_losses", 0)
        rbj_net = rbj.get("rbj_total_won", 0) - rbj.get("rbj_total_bet", 0)
    except Exception:
        pass
    pk_w = pk_l = pk_net = 0
    try:
        pk    = db.get_poker_stats(uid)
        pk_w  = pk.get("wins", 0)
        pk_l  = pk.get("losses", 0)
        pk_net = pk.get("net_profit", pk.get("total_won", 0) - pk.get("total_lost", 0))
    except Exception:
        pass
    total_net   = bj_net + rbj_net + pk_net
    casino_rank = _casino_rank(total_net)
    lines = [
        f"🎰 Casino — @{uname}",
        f"BJ: {bj_w}W/{bj_l}L {_fmt_net(bj_net)} 🪙",
        f"RBJ: {rbj_w}W/{rbj_l}L {_fmt_net(rbj_net)} 🪙",
        f"Poker: {pk_w}W/{pk_l}L {_fmt_net(pk_net)} 🪙",
        f"Rank: {casino_rank}",
        f"More: !profile @{uname} 4",
    ]
    return "\n".join(lines)[:249]


def _build_page4(uid: str, uname: str, p: dict, privacy: dict,
                 is_self: bool, is_staff: bool) -> str:
    see = _can_see("show_inventory", privacy, is_self, is_staff)
    if not see:
        return (
            f"🛒 Items — @{uname}\n"
            f"Inventory hidden.\n"
            f"More: !profile @{uname} 5"
        )[:249]
    level  = p.get("level", 1) or 1
    badge  = _equipped_badge_display(uid)
    title  = _equipped_title_display(uid, level)
    vip    = _vip_status_str(uid)
    n_badges = n_titles = 0
    try:
        counts   = db.get_owned_item_counts(uid)
        n_badges = counts.get("badges", 0)
        n_titles = counts.get("titles", 0)
    except Exception:
        pass
    col_pct = _collection_pct(uid)
    lines = [
        f"🛒 Items — @{uname}",
        f"Badge: {badge} | Title: {title}",
        f"Badges: {n_badges} | Titles: {n_titles}",
        f"VIP: {vip}",
        f"Collection: {col_pct}%",
        f"More: !profile @{uname} 5",
    ]
    return "\n".join(lines)[:249]


def _build_page5(uid: str, uname: str, p: dict, privacy: dict,
                 is_self: bool, is_staff: bool) -> str:
    see = _can_see("show_achievements", privacy, is_self, is_staff)
    if not see:
        return (
            f"🏆 Progress — @{uname}\n"
            f"Achievements hidden.\n"
            f"More: !profile @{uname} 6"
        )[:249]
    n_ach  = 0
    latest = "None"
    try:
        achs  = db.get_unlocked_achievements(uid)
        n_ach = len(achs)
        if achs:
            latest = achs[-1].replace("_", " ").title()[:20]
    except Exception:
        pass
    rep = 0
    try:
        rep_row = db.get_reputation(uid)
        rep = rep_row["rep_received"] if rep_row else 0
    except Exception:
        pass
    reprank = _rep_rank(rep)
    d_done, d_total, w_done, w_total = _mission_progress(uid)
    s_rank = _season_rank_str(uid, uname) if (is_self or is_staff) else ""
    lines = [
        f"🏆 Progress — @{uname}",
        f"Daily: {d_done}/{d_total} | Weekly: {w_done}/{w_total}",
        f"Season: {s_rank}" if s_rank else f"Rep: {rep} | Rank: {reprank}",
        f"Achievements: {n_ach} | Latest: {latest}",
        f"More: !profile @{uname} 6",
    ]
    return "\n".join(lines)[:249]


def _build_page6(uid: str, uname: str, p: dict, privacy: dict,
                 is_self: bool, is_staff: bool) -> str:
    see_money = _can_see("show_money", privacy, is_self, is_staff)
    rep = 0
    try:
        rep_row = db.get_reputation(uid)
        rep = rep_row["rep_received"] if rep_row else 0
    except Exception:
        pass
    vip      = _vip_status_str(uid)
    sent_str = recv_str = "Hidden"
    if see_money:
        try:
            bank     = db.get_bank_user_stats(uid)
            sent_str = f"{bank.get('total_sent', 0):,}"
            recv_str = f"{bank.get('total_received', 0):,}"
        except Exception:
            pass
    lines = [
        f"🏦 Social — @{uname}",
        f"Sent: {sent_str} 🪙 | Recv: {recv_str} 🪙",
        f"Rep: {rep} | VIP: {vip}",
    ]
    if is_staff:
        blocked = "?"
        n_rep = n_warn = 0
        try:
            bank    = db.get_bank_user_stats(uid)
            blocked = "YES" if bank.get("bank_blocked") else "NO"
        except Exception:
            pass
        try:
            conn   = db.get_connection()
            n_rep  = conn.execute(
                "SELECT COUNT(*) as c FROM reports WHERE target_username=? COLLATE NOCASE",
                (uname,)
            ).fetchone()["c"]
            n_warn = conn.execute(
                "SELECT COUNT(*) as c FROM warnings WHERE LOWER(username)=LOWER(?)",
                (uname,)
            ).fetchone()["c"]
            conn.close()
        except Exception:
            pass
        lines.append(f"Blocked:{blocked} Rep:{n_rep} Warns:{n_warn}")
    return "\n".join(lines)[:249]


_PAGE_BUILDERS = {
    1: _build_page1,
    2: _build_page2,
    3: _build_page3,
    4: _build_page4,
    5: _build_page5,
    6: _build_page6,
}


def _build_page(page: int, uid: str, uname: str, p: dict,
                privacy: dict, is_self: bool, is_staff: bool) -> str:
    fn = _PAGE_BUILDERS.get(page, _build_page1)
    return fn(uid, uname, p, privacy, is_self, is_staff)


# ---------------------------------------------------------------------------
# !profile — main handler (routes all subcommands)
# ---------------------------------------------------------------------------

async def handle_profile_cmd(bot: BaseBot, user: User, args: list[str]) -> None:
    """!profile [private|settings|help|@user] [page] — polished profile system."""
    db.ensure_user(user.id, user.username)
    sub = args[1].lower().strip() if len(args) > 1 else ""

    # --- subcommand: private ---
    if sub == "private":
        p = db.get_profile(user.id)
        if not p:
            await _w(bot, user.id, "⚠️ Profile not found. Try chatting first.")
            return
        await _send_private_card(bot, user.id, user.username, p)
        return

    # --- subcommand: settings ---
    if sub == "settings":
        await handle_profile_settings(bot, user, args)
        return

    # --- subcommand: help ---
    if sub == "help":
        await handle_profile_help(bot, user)
        return

    # --- subcommand: page number (legacy) ---
    raw_name = None
    page     = 0   # 0 = use polished card
    for a in args[1:]:
        a_clean = a.lstrip("@").strip()
        if a_clean.isdigit() and 1 <= int(a_clean) <= 6:
            page = int(a_clean)
        elif a_clean and not a_clean.isdigit():
            raw_name = a_clean

    t_id, t_name, p, is_self, is_staff, err = _setup(user, raw_name)
    if err:
        await _w(bot, user.id, err)
        return

    if page:
        # Legacy paged view
        privacy = _get_privacy(t_name)
        msg     = _build_page(page, t_id, t_name, p, privacy, is_self, is_staff)
        await _w(bot, user.id, msg)
    else:
        # 3.1L polished card
        await _send_public_card(bot, user.id, t_id, t_name, p, is_self, is_staff)


# ---------------------------------------------------------------------------
# !stats — private detailed profile
# ---------------------------------------------------------------------------

async def handle_stats_cmd(bot: BaseBot, user: User, args: list[str]) -> None:
    """!stats — private detailed profile (alias for !profile private)."""
    db.ensure_user(user.id, user.username)
    raw_name = args[1].lstrip("@").strip() if len(args) > 1 else None
    if raw_name:
        # Viewing other player's stats page
        t_id, t_name, p, is_self, is_staff, err = _setup(user, raw_name)
        if err:
            await _w(bot, user.id, err)
            return
        privacy = _get_privacy(t_name)
        await _w(bot, user.id, _build_page2(t_id, t_name, p, privacy, is_self, is_staff))
    else:
        # Self — full private card
        p = db.get_profile(user.id)
        if not p:
            await _w(bot, user.id, "⚠️ Profile not found. Try chatting first.")
            return
        await _send_private_card(bot, user.id, user.username, p)


# ---------------------------------------------------------------------------
# !profile settings
# ---------------------------------------------------------------------------

async def handle_profile_settings(bot: BaseBot, user: User, args: list[str]) -> None:
    """!profile settings [field] [public|private]"""
    db.ensure_user(user.id, user.username)
    uid    = user.id
    uname  = user.username

    # Show current settings
    if len(args) < 3 or (len(args) == 2 and args[1].lower() == "settings"):
        bal  = _get_profile_setting(uid, "balance_visibility")
        col  = _get_profile_setting(uid, "collection_visibility")
        bdg  = _get_profile_setting(uid, "badge_visibility")
        vip  = _get_profile_setting(uid, "vip_visibility")
        ssn  = _get_profile_setting(uid, "season_visibility")
        lvl  = _get_profile_setting(uid, "level_visibility")
        await _w(bot, uid,
                 f"⚙️ Profile Settings\n"
                 f"Balance: {bal}\n"
                 f"Collections: {col}\n"
                 f"Badges: {bdg}\n"
                 f"VIP: {vip} | Season: {ssn} | Level: {lvl}\n"
                 f"Use: !profile settings <field> public/private")
        return

    # !profile settings <field> <value>
    field_raw = args[2].lower() if len(args) > 2 else ""
    value_raw = args[3].lower() if len(args) > 3 else ""
    db_field  = _PROFILE_SETTING_FIELDS.get(field_raw)

    if not db_field:
        valid = " | ".join(_PROFILE_SETTING_FIELDS.keys())
        await _w(bot, uid, f"⚠️ Valid fields: {valid}")
        return
    if value_raw not in ("public", "private"):
        await _w(bot, uid, "⚠️ Value must be 'public' or 'private'.")
        return

    _set_profile_setting(uid, uname, db_field, value_raw)
    await _w(bot, uid, f"✅ {field_raw.capitalize()} visibility set to {value_raw}.")


# ---------------------------------------------------------------------------
# !profile help
# ---------------------------------------------------------------------------

async def handle_profile_help(bot: BaseBot, user: User) -> None:
    """!profile help — show profile commands."""
    await _w(bot, user.id,
             "👤 Profile Help\n"
             "!profile — public card\n"
             "!profile @user\n"
             "!stats — private details\n"
             "!profile settings")
    await asyncio.sleep(0.3)
    await _w(bot, user.id,
             "Identity:\n"
             "!setbadge [badge]\n"
             "!settitle [title]\n"
             "!flex — public flex\n"
             "!privacy — old privacy toggle")


# ---------------------------------------------------------------------------
# !flex / !showoff / !card
# ---------------------------------------------------------------------------

async def handle_flex(bot: BaseBot, user: User, args: list[str]) -> None:
    """!flex [@user] — public social flex card (30s cooldown)."""
    db.ensure_user(user.id, user.username)
    now = time.monotonic()
    last = _flex_cooldowns.get(user.id, 0.0)
    if now - last < _FLEX_COOLDOWN:
        remaining = int(_FLEX_COOLDOWN - (now - last))
        await _w(bot, user.id, f"⏳ Flex cooldown: {remaining}s remaining.")
        return

    raw_name = args[1].lstrip("@").strip() if len(args) > 1 else None
    t_id, t_name, p, is_self, is_staff, err = _setup(user, raw_name)
    if err:
        await _w(bot, user.id, err)
        return

    _flex_cooldowns[user.id] = now
    await _send_flex_card(bot, user.id, t_id, t_name, p)


# ---------------------------------------------------------------------------
# !badges [username]  (legacy — kept for routing)
# ---------------------------------------------------------------------------

async def handle_badges_cmd(bot: BaseBot, user: User, args: list[str]) -> None:
    db.ensure_user(user.id, user.username)
    raw_name = args[1].lstrip("@").strip() if len(args) > 1 else None
    t_id, t_name, p, is_self, is_staff, err = _setup(user, raw_name)
    if err:
        await _w(bot, user.id, err)
        return
    privacy = _get_privacy(t_name)
    await _w(bot, user.id, _build_page4(t_id, t_name, p, privacy, is_self, is_staff))


# ---------------------------------------------------------------------------
# !casino profile (legacy)
# ---------------------------------------------------------------------------

async def handle_casino_profile(bot: BaseBot, user: User, target_name: str) -> None:
    db.ensure_user(user.id, user.username)
    t_id, t_name, p, is_self, is_staff, err = _setup(user, target_name)
    if err:
        await _w(bot, user.id, err)
        return
    privacy = _get_privacy(t_name)
    await _w(bot, user.id, _build_page3(t_id, t_name, p, privacy, is_self, is_staff))


# ---------------------------------------------------------------------------
# !privacy [field] [on/off]  (legacy toggles — kept)
# ---------------------------------------------------------------------------

async def handle_privacy(bot: BaseBot, user: User, args: list[str]) -> None:
    db.ensure_user(user.id, user.username)
    privacy = _get_privacy(user.username)
    if len(args) < 2:
        def yn(v): return "ON" if v else "OFF"
        await _w(bot, user.id,
                 f"🔒 Privacy — @{user.username}\n"
                 f"Money {yn(privacy['show_money'])} | Casino {yn(privacy['show_casino'])}\n"
                 f"Achievements {yn(privacy['show_achievements'])} | Inventory {yn(privacy['show_inventory'])}\n"
                 f"Use !profile settings for 3.1L controls.")
        return
    field_name = args[1].lower()
    if field_name not in _PRIVACY_FIELDS:
        await _w(bot, user.id, "Usage: !privacy money|casino|achievements|inventory on/off")
        return
    if len(args) < 3:
        await _w(bot, user.id, f"Usage: !privacy {field_name} on/off")
        return
    toggle = args[2].lower()
    if toggle not in ("on", "off"):
        await _w(bot, user.id, "Use 'on' or 'off'.")
        return
    value    = 1 if toggle == "on" else 0
    db_field = _PRIVACY_FIELDS[field_name]
    db.set_profile_privacy(user.username, db_field, value)
    if toggle == "on":
        msg = f"✅ {field_name.capitalize()} stats are now visible."
    else:
        msg = f"✅ {field_name.capitalize()} privacy OFF. Stats are hidden."
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# !profileadmin — admin tools
# ---------------------------------------------------------------------------

async def handle_profileadmin(bot: BaseBot, user: User, args: list[str]) -> None:
    """!profileadmin [view|resetprivacy|refresh] @user — admin profile tools."""
    if not is_admin(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return

    sub      = args[1].lower() if len(args) >= 2 else "help"
    raw_name = args[2].lstrip("@").strip() if len(args) >= 3 else None

    if sub == "help" or (sub not in ("view", "resetprivacy", "refresh", "rebuild") and not raw_name):
        await _w(bot, user.id,
                 "🛠️ Profile Admin\n"
                 "!profileadmin view @user\n"
                 "!profileadmin resetprivacy @user\n"
                 "!profileadmin refresh @user")
        return

    # --- view ---
    if sub == "view":
        if not raw_name:
            await _w(bot, user.id, "Usage: !profileadmin view @user")
            return
        target = db.get_user_by_username(raw_name)
        if target is None:
            await _w(bot, user.id, f"@{raw_name} not found.")
            return
        t_id, t_name = target["user_id"], target["username"]
        p = db.get_profile(t_id)
        if not p:
            await _w(bot, user.id, "No profile data.")
            return
        full_priv = {"show_money": 1, "show_casino": 1,
                     "show_achievements": 1, "show_inventory": 1}
        msg = _build_page(1, t_id, t_name, p, full_priv, False, True)
        await _w(bot, user.id, f"[Admin] {msg}"[:249])
        await asyncio.sleep(0.3)
        await _send_public_card(bot, user.id, t_id, t_name, p, False, True)
        return

    # --- resetprivacy ---
    if sub == "resetprivacy":
        if not raw_name:
            await _w(bot, user.id, "Usage: !profileadmin resetprivacy @user")
            return
        db.reset_profile_privacy(raw_name)
        # Also reset player_profile_settings
        try:
            target = db.get_user_by_username(raw_name)
            if target:
                conn = db.get_connection()
                conn.execute(
                    "DELETE FROM player_profile_settings WHERE user_id=?",
                    (target["user_id"],),
                )
                conn.commit()
                conn.close()
        except Exception:
            pass
        await _w(bot, user.id, f"✅ Privacy reset for @{raw_name}.")
        return

    # --- refresh ---
    if sub == "refresh":
        if not raw_name:
            await _w(bot, user.id, "Usage: !profileadmin refresh @user")
            return
        target = db.get_user_by_username(raw_name)
        if not target:
            await _w(bot, user.id, f"@{raw_name} not found.")
            return
        t_id, t_name = target["user_id"], target["username"]
        db.ensure_user(t_id, t_name)
        await _w(bot, user.id, f"✅ Profile refreshed for @{t_name}.")
        return

    # --- rebuild (no-op since data is live) ---
    if sub == "rebuild":
        await _w(bot, user.id, "✅ Profiles are live — no rebuild needed.")
        return

    # Fallback: treat sub as username for old-style !profileadmin <user>
    try:
        page = int(sub) if sub.isdigit() else 1
        page = max(1, min(6, page))
        target_name = sub if not sub.isdigit() else (raw_name or "")
        if not target_name:
            await _w(bot, user.id, "Usage: !profileadmin view @user")
            return
        target = db.get_user_by_username(target_name)
        if not target:
            await _w(bot, user.id, f"@{target_name} not found.")
            return
        t_id, t_name = target["user_id"], target["username"]
        p = db.get_profile(t_id)
        if not p:
            await _w(bot, user.id, "No profile data.")
            return
        full_priv = {"show_money": 1, "show_casino": 1,
                     "show_achievements": 1, "show_inventory": 1}
        msg = _build_page(page, t_id, t_name, p, full_priv, False, True)
        await _w(bot, user.id, f"[Admin] {msg}"[:249])
    except Exception as exc:
        await _w(bot, user.id, f"Error: {exc}")


# ---------------------------------------------------------------------------
# !profileprivacy @user  (mod+)
# ---------------------------------------------------------------------------

async def handle_profileprivacy(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !profileprivacy @username")
        return
    target_name = args[1].lstrip("@").strip()
    privacy     = _get_privacy(target_name)
    def yn(v): return "ON" if v else "OFF"
    await _w(bot, user.id,
             f"🔒 Privacy — @{target_name}\n"
             f"Money {yn(privacy['show_money'])} | Casino {yn(privacy['show_casino'])}\n"
             f"Ach {yn(privacy['show_achievements'])} | Inv {yn(privacy['show_inventory'])}")


# ---------------------------------------------------------------------------
# !resetprofileprivacy @user  (admin+)
# ---------------------------------------------------------------------------

async def handle_resetprofileprivacy(bot: BaseBot, user: User, args: list[str]) -> None:
    if not is_admin(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !resetprofileprivacy @username")
        return
    target_name = args[1].lstrip("@").strip()
    db.reset_profile_privacy(target_name)
    await _w(bot, user.id, f"✅ Privacy reset for @{target_name}.")
