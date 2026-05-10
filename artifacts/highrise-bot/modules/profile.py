"""
modules/profile.py
------------------
Public player profile system for the Highrise Mini Game Bot.

Public commands:
  /profile [username] [page]   -- view profile (self or target, pages 1-6)
  /me                          -- own profile (alias for /profile)
  /whois <username>            -- target profile (alias for /profile <user>)
  /pinfo <username>            -- target profile (alias for /profile <user>)
  /stats [username]            -- economy page (page 2)
  /badges [username]           -- inventory page (page 4)
  /titles [username]           -- inventory page (page 4)
  /privacy [field] [on/off]    -- manage your own privacy settings

Staff commands:
  /profileadmin <username> [page]   -- full profile bypassing privacy (admin+)
  /profileprivacy <username>        -- view target's privacy settings (mod+)
  /resetprofileprivacy <username>   -- reset privacy to defaults (admin+)

All messages <= 249 chars. Missing tables / data handled gracefully.
"""

from highrise import BaseBot, User

import database as db
from modules.permissions import is_admin, can_moderate
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


def _is_vip(user_id: str) -> bool:
    try:
        return db.owns_item(user_id, "vip")
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Whisper helper
# ---------------------------------------------------------------------------

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


# ---------------------------------------------------------------------------
# Privacy helpers
# ---------------------------------------------------------------------------

_PRIVACY_FIELDS: dict[str, str] = {
    "money":        "show_money",
    "casino":       "show_casino",
    "achievements": "show_achievements",
    "inventory":    "show_inventory",
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
    """Case-insensitive DB lookup. Returns (user_id, username, profile) or (None, name, None)."""
    target = db.get_user_by_username(clean_name)
    if not target:
        return None, clean_name, None
    t_id   = target["user_id"]
    t_name = target["username"]
    p      = db.get_profile(t_id)
    return t_id, t_name, p


def _setup(requester: User, raw_name: str | None):
    """
    Resolve target and build permission flags.
    Returns (t_id, t_name, p, is_self, is_staff, error_msg).
    error_msg is non-None only on failure.
    """
    if not raw_name:
        t_id   = requester.id
        t_name = requester.username
        p      = db.get_profile(t_id)
    else:
        clean          = raw_name.lstrip("@").strip()
        t_id, t_name, p = _resolve(clean)
        if t_id is None:
            return None, clean, None, False, False, "User not found. They may need to chat first."
    if not p:
        return None, t_name, None, False, False, "User not found in bot database."
    is_self  = (t_id == requester.id)
    is_staff = can_moderate(requester.username) or is_admin(requester.username)
    return t_id, t_name, p, is_self, is_staff, None


# ---------------------------------------------------------------------------
# Page builders
# ---------------------------------------------------------------------------

def _fmt_net(n: int) -> str:
    return f"+{n:,}" if n >= 0 else f"{n:,}"


def _build_page1(uid: str, uname: str, p: dict, privacy: dict,
                 is_self: bool, is_staff: bool) -> str:
    try:
        display = db.get_display_name(uid, uname)
    except Exception:
        display = f"@{uname}"
    role  = _staff_role(uname)
    vip   = "YES" if _is_vip(uid) else "NO"
    level = p.get("level", 1)
    xp    = p.get("xp", 0)
    try:
        xp_next = db.xp_for_level(level + 1)
    except Exception:
        xp_next = "?"
    lvrank = _level_rank(level)
    rep = 0
    try:
        rep_row = db.get_reputation(uid)
        rep = rep_row["rep_received"] if rep_row else 0
    except Exception:
        pass
    reprank = _rep_rank(rep)
    first = str(p.get("first_seen") or "?")[:10]
    lines = [
        f"👤 {display}"[:60],
        f"Role: {role} | VIP: {vip}",
        f"Lv {level} ({lvrank}) | {xp}/{xp_next} XP",
        f"Rep: {reprank} {rep}",
        f"Joined: {first}",
    ]
    if not is_self:
        lines.append(f"More: /profile @{uname} 2")
    return "\n".join(lines)[:249]


def _build_page2(uid: str, uname: str, p: dict, privacy: dict,
                 is_self: bool, is_staff: bool) -> str:
    see = _can_see("show_money", privacy, is_self, is_staff)
    if not see:
        return (
            f"💰 Economy — @{uname}\n"
            f"Coins: Hidden by player.\n"
            f"More: /profile @{uname} 3"
        )[:249]
    balance = p.get("balance", 0)
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
    race_wins = 0
    try:
        race_wins = db.get_race_wins_count(uid)
    except Exception:
        pass
    lines = [
        f"💰 Economy — @{uname}",
        f"Coins: {fmt_coins(balance)}",
        f"Earned: {fmt_coins(earned)} | Wins: {wins}",
        f"Sent: {fmt_coins(sent)} | Recv: {fmt_coins(recv)}",
        f"Race Wins: {race_wins} | Streak: {streak}d",
        f"More: /profile @{uname} 3",
    ]
    return "\n".join(lines)[:249]


def _build_page3(uid: str, uname: str, p: dict, privacy: dict,
                 is_self: bool, is_staff: bool) -> str:
    see = _can_see("show_casino", privacy, is_self, is_staff)
    if not see:
        return (
            f"🎰 Casino — @{uname}\n"
            f"Casino stats hidden.\n"
            f"More: /profile @{uname} 4"
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
        f"BJ: {bj_w}W/{bj_l}L Net {_fmt_net(bj_net)}c",
        f"RBJ: {rbj_w}W/{rbj_l}L Net {_fmt_net(rbj_net)}c",
        f"Poker: {pk_w}W/{pk_l}L Net {_fmt_net(pk_net)}c",
        f"Rank: {casino_rank}",
        f"More: /profile @{uname} 4",
    ]
    return "\n".join(lines)[:249]


def _build_page4(uid: str, uname: str, p: dict, privacy: dict,
                 is_self: bool, is_staff: bool) -> str:
    see = _can_see("show_inventory", privacy, is_self, is_staff)
    if not see:
        return (
            f"🛒 Items — @{uname}\n"
            f"Inventory hidden.\n"
            f"More: /profile @{uname} 5"
        )[:249]
    badge = p.get("equipped_badge") or "None"
    title = p.get("equipped_title") or "None"
    vip   = "YES" if _is_vip(uid) else "NO"
    n_badges = n_titles = 0
    try:
        counts   = db.get_owned_item_counts(uid)
        n_badges = counts.get("badges", 0)
        n_titles = counts.get("titles", 0)
    except Exception:
        pass
    lines = [
        f"🛒 Items — @{uname}",
        f"Badge: {badge} | Title: {title}",
        f"Badges: {n_badges} | Titles: {n_titles}",
        f"VIP: {vip}",
        f"More: /profile @{uname} 5",
    ]
    return "\n".join(lines)[:249]


def _build_page5(uid: str, uname: str, p: dict, privacy: dict,
                 is_self: bool, is_staff: bool) -> str:
    see = _can_see("show_achievements", privacy, is_self, is_staff)
    if not see:
        return (
            f"🏆 Progress — @{uname}\n"
            f"Achievements hidden.\n"
            f"More: /profile @{uname} 6"
        )[:249]
    n_ach  = 0
    latest = "None"
    try:
        achs = db.get_unlocked_achievements(uid)
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
    lines = [
        f"🏆 Progress — @{uname}",
        f"Achievements: {n_ach}",
        f"Latest: {latest}",
        f"Rep: {rep} | Rank: {reprank}",
        f"More: /profile @{uname} 6",
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
    vip      = "YES" if _is_vip(uid) else "NO"
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
        f"Sent/Recv: {sent_str}/{recv_str}c",
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
# /profile [username] [page]   /me   /whois   /pinfo
# ---------------------------------------------------------------------------

async def handle_profile_cmd(bot: BaseBot, user: User, args: list[str]) -> None:
    """Handle /profile, /me, /whois, /pinfo."""
    db.ensure_user(user.id, user.username)
    raw_name = None
    page     = 1
    for a in args[1:]:
        a_clean = a.lstrip("@").strip()
        if a_clean.isdigit() and 1 <= int(a_clean) <= 6:
            page = int(a_clean)
        elif a_clean:
            raw_name = a_clean
    t_id, t_name, p, is_self, is_staff, err = _setup(user, raw_name)
    if err:
        await _w(bot, user.id, err)
        return
    privacy = _get_privacy(t_name)
    msg     = _build_page(page, t_id, t_name, p, privacy, is_self, is_staff)
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /stats [username]
# ---------------------------------------------------------------------------

async def handle_stats_cmd(bot: BaseBot, user: User, args: list[str]) -> None:
    db.ensure_user(user.id, user.username)
    raw_name = args[1].lstrip("@").strip() if len(args) > 1 else None
    t_id, t_name, p, is_self, is_staff, err = _setup(user, raw_name)
    if err:
        await _w(bot, user.id, err)
        return
    privacy = _get_privacy(t_name)
    await _w(bot, user.id, _build_page2(t_id, t_name, p, privacy, is_self, is_staff))


# ---------------------------------------------------------------------------
# /badges [username]   /titles [username]
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
# /casino <username>   (casino profile page 3)
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
# /privacy [field] [on/off]
# ---------------------------------------------------------------------------

async def handle_privacy(bot: BaseBot, user: User, args: list[str]) -> None:
    db.ensure_user(user.id, user.username)
    privacy = _get_privacy(user.username)
    if len(args) < 2:
        def yn(v): return "ON" if v else "OFF"
        msg = (
            f"🔒 Privacy — @{user.username}\n"
            f"Money {yn(privacy['show_money'])} | Casino {yn(privacy['show_casino'])}\n"
            f"Achievements {yn(privacy['show_achievements'])} | Inventory {yn(privacy['show_inventory'])}"
        )
        await _w(bot, user.id, msg)
        return
    field_name = args[1].lower()
    if field_name not in _PRIVACY_FIELDS:
        await _w(bot, user.id, "Usage: /privacy money|casino|achievements|inventory on/off")
        return
    if len(args) < 3:
        await _w(bot, user.id, f"Usage: /privacy {field_name} on/off")
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
# /profileadmin <username> [page]   (admin+)
# ---------------------------------------------------------------------------

async def handle_profileadmin(bot: BaseBot, user: User, args: list[str]) -> None:
    if not is_admin(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /profileadmin <username> [page]")
        return
    raw_name = args[1].lstrip("@").strip()
    page     = 1
    if len(args) >= 3 and args[2].isdigit():
        page = max(1, min(6, int(args[2])))
    target = db.get_user_by_username(raw_name)
    if target is None:
        await _w(bot, user.id, f"@{raw_name} not found in database.")
        return
    t_id, t_name = target["user_id"], target["username"]
    p = db.get_profile(t_id)
    if not p:
        await _w(bot, user.id, "User not found in bot database.")
        return
    full_privacy = {
        "show_money": 1, "show_casino": 1,
        "show_achievements": 1, "show_inventory": 1,
    }
    msg = _build_page(page, t_id, t_name, p, full_privacy, is_self=False, is_staff=True)
    await _w(bot, user.id, f"[Admin] {msg}"[:249])


# ---------------------------------------------------------------------------
# /profileprivacy <username>   (mod+)
# ---------------------------------------------------------------------------

async def handle_profileprivacy(bot: BaseBot, user: User, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /profileprivacy <username>")
        return
    target_name = args[1].lstrip("@").strip()
    privacy     = _get_privacy(target_name)
    def yn(v): return "ON" if v else "OFF"
    msg = (
        f"🔒 Privacy — @{target_name}\n"
        f"Money {yn(privacy['show_money'])} | Casino {yn(privacy['show_casino'])}\n"
        f"Ach {yn(privacy['show_achievements'])} | Inv {yn(privacy['show_inventory'])}"
    )
    await _w(bot, user.id, msg)


# ---------------------------------------------------------------------------
# /resetprofileprivacy <username>   (admin+)
# ---------------------------------------------------------------------------

async def handle_resetprofileprivacy(bot: BaseBot, user: User, args: list[str]) -> None:
    if not is_admin(user.username):
        await _w(bot, user.id, "Admins and owners only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /resetprofileprivacy <username>")
        return
    target_name = args[1].lstrip("@").strip()
    db.reset_profile_privacy(target_name)
    await _w(bot, user.id, f"✅ Privacy reset for @{target_name}.")
