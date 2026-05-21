"""modules/emote_extras.py
=================================
Compact emote lists + social target emotes (VIP+) + sync emotes + staff
room-wide emote + per-user favorite emotes + dancefloor + test checklist.

Sections:
  A) Compact emote lists           — handle_emote_list_compact / handle_botemotes_compact
  B) Socials menu                  — handle_emotes_socials
  C) VIP+ social target emotes     — kiss / slap / superpunch / bonk / yeet /
                                     hypnotize / duel (alphabetical in menu)
  D) Sync emotes (TRUE mirror)     — handle_sync / try_sync_shortcut
  E) Sync stop                     — handle_syncstop
  F) Staff room-wide emote         — handle_emote_all (with stop sub)
  G) Favorite emotes               — handle_favemotes / handle_favemote
  H) Dancefloor system             — handle_dancefloor + startup_dancefloor_recovery
  I) Test checklist                — handle_emotetestchecklist
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING

import database as db
from modules.permissions import (
    is_owner, is_admin, is_manager, is_moderator, can_moderate,
)

if TYPE_CHECKING:
    from highrise import BaseBot
    from highrise.models import User, Position


# ---------------------------------------------------------------------------
# DB bootstrap — lazy CREATE TABLE IF NOT EXISTS
# ---------------------------------------------------------------------------
_DB_READY = False


def _ensure_tables() -> None:
    global _DB_READY
    if _DB_READY:
        return
    try:
        conn = db.get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fav_emotes (
                user_id TEXT NOT NULL,
                alias   TEXT NOT NULL,
                PRIMARY KEY (user_id, alias)
            )
        """)
        conn.commit()
        conn.close()
        _DB_READY = True
    except Exception as exc:
        print(f"[EMOTE_EXTRAS] table bootstrap failed: {exc!r}")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception as exc:
        print(f"[EMOTE_EXTRAS WHISPER] {exc!r}")


def _is_vip(user_id: str) -> bool:
    try:
        return bool(db.owns_item(user_id, "vip"))
    except Exception:
        return False


def _is_staff(username: str) -> bool:
    return bool(can_moderate(username) or is_admin(username) or is_owner(username))


def _is_admin(username: str) -> bool:
    return bool(is_admin(username) or is_owner(username))


# ---------------------------------------------------------------------------
# Compact whisper paging — ≤249 char hard cap, fills up to ~240 char body
# ---------------------------------------------------------------------------
_MAX_LINE = 249
_TARGET   = 240   # body target before header


async def _send_compact_pages(bot: "BaseBot", uid: str, header_label: str,
                              items: list[str]) -> None:
    """Whisper alphabetical items, comma-separated, auto-paged to ≤249 chars."""
    if not items:
        await _w(bot, uid, f"{header_label} 1/1\n(none)")
        return
    items = sorted({str(s).strip() for s in items if s and str(s).strip()})

    # First, partition into pages by accumulating "alias, alias, alias..."
    pages: list[list[str]] = []
    buf: list[str] = []
    used = 0
    # Reserve worst-case header "🤖 Bot Emotes 99/99\n" = ~22 chars
    avail = _TARGET - 22

    for it in items:
        add_len = len(it) + (2 if buf else 0)   # ", "
        if buf and used + add_len > avail:
            pages.append(buf)
            buf = []
            used = 0
            add_len = len(it)
        buf.append(it)
        used += add_len
    if buf:
        pages.append(buf)

    total = max(1, len(pages))
    for i, chunk in enumerate(pages, 1):
        body = ", ".join(chunk)
        msg  = f"{header_label} {i}/{total}\n{body}"
        await _w(bot, uid, msg[:_MAX_LINE])
        if i < total:
            await asyncio.sleep(0.4)


# ---------------------------------------------------------------------------
# Registry adapters
# ---------------------------------------------------------------------------
def _reg_get(alias_or_id: str) -> dict | None:
    try:
        from data import emote_registry as _r
        return _r.get_emote(alias_or_id)
    except Exception:
        return None


def _reg_player_aliases() -> list[str]:
    try:
        from data import emote_registry as _r
        return list(_r.player_aliases())
    except Exception:
        return []


def _reg_bot_aliases() -> list[str]:
    try:
        from data import emote_registry as _r
        return list(_r.bot_aliases())
    except Exception:
        return []


# ===========================================================================
# Section A — Compact emote lists
# ===========================================================================
async def handle_emote_list_compact(bot: "BaseBot", user: "User",
                                    _args: list | None = None) -> None:
    """!emote list / !emotes — alphabetical comma-separated, paged."""
    await _send_compact_pages(bot, user.id, "🎭 Emotes",
                              _reg_player_aliases())


async def handle_botemotes_compact(bot: "BaseBot", user: "User",
                                    _args: list | None = None) -> None:
    """!botemotes — alphabetical comma-separated, paged."""
    await _send_compact_pages(bot, user.id, "🤖 Bot Emotes",
                              _reg_bot_aliases())


# ===========================================================================
# Section B — Socials menu
# ===========================================================================
async def handle_emotes_socials(bot: "BaseBot", user: "User",
                                _args: list | None = None) -> None:
    """!emotes socials — alphabetical compact list of social target commands."""
    cmds = ["!bonk @user", "!duel @user", "!hypnotize @user",
            "!kiss @user", "!slap @user", "!superpunch @user", "!yeet @user"]
    await _send_compact_pages(bot, user.id, "💞 Socials (VIP+)", cmds)


# ===========================================================================
# Section C — VIP+ social target emotes
# ===========================================================================
# (attacker_emote_candidates, target_reaction_candidates, broadcast template)
# Resolver tries each candidate as a registry alias first, then as a raw
# emote-*/dance-*/idle-* ID. First hit wins.
_SOCIAL_TARGETS: dict[str, tuple[list[str], list[str], str]] = {
    # Emote ID resolution: registry alias → raw SDK ID (prefix or upper).
    # Use exact SDK IDs as first candidate so they always win.
    "slap":       (["SLAP", "slap", "emote-slap"],
                   ["emote-death", "death", "deathdrop", "emote-deathdrop"],
                   "👋 {a} slapped {b}!"),
    "kiss":       (["emote-kissing", "kissing", "kiss"],
                   ["emote-bloomify-pose2", "bloomify", "bloom", "emote-bloom"],
                   "😘 {a} kissed {b}!"),
    "superpunch": (["emote-superpunch", "superpunch", "emote-punch-strong"],
                   ["emote-death", "death", "deathdrop", "emote-deathdrop"],
                   "💥 {a} superpunched {b}!"),
    "bonk":       (["pointing", "tapdance", "emoji-pointing"],
                   ["confused", "dizzy", "emote-confused"],
                   "🔨 {a} bonked {b}!"),
    "yeet":       (["push", "shrink", "emote-pushit", "emote-shrink"],
                   ["deathdrop", "emote-deathdrop"],
                   "🚀 {a} yeeted {b}!"),
    "hypnotize":  (["witchcraft", "creepycute", "emote-witchcraft"],
                   ["confused", "dizzy", "float", "emote-confused"],
                   "🌀 {a} hypnotized {b}!"),
}

_TARGET_SOCIAL_CD: dict[str, float] = {}
_TARGET_SOCIAL_CD_SECS = 8

_RAW_ID_PREFIXES = ("emote-", "emoji-", "dance-", "idle-")


def _resolve_eid(candidates: list[str]) -> str | None:
    """Try each candidate as registry alias first, then as raw SDK emote ID.

    Accepts:
      - registry alias  → returns registry id
      - known prefix    → returns as-is  (emote-*, emoji-*, dance-*, idle-*)
      - all-uppercase   → returns as-is  (e.g. 'SLAP')
    """
    for c in candidates:
        ent = _reg_get(c)
        if ent and ent.get("id"):
            return ent["id"]
    for c in candidates:
        if any(c.startswith(p) for p in _RAW_ID_PREFIXES) or c.isupper():
            return c
    return None


async def _do_target_social(bot: "BaseBot", user: "User",
                             args: list, kind: str) -> None:
    """Common path for VIP+ target socials (sender + target reaction)."""
    uid   = user.id
    uname = user.username

    if not _is_vip(uid) and not _is_staff(uname):
        await _w(bot, uid, "✨ VIP+ required for social emotes.")
        return

    if len(args) < 2:
        await _w(bot, uid, f"Usage: !{kind} @user")
        return
    target_name = args[1].lstrip("@")
    if target_name.lower() == uname.lower():
        await _w(bot, uid, "You can't target yourself.")
        return

    # Cooldown
    last = _TARGET_SOCIAL_CD.get(uid, 0.0)
    rem  = _TARGET_SOCIAL_CD_SECS - (time.time() - last)
    if rem > 0:
        await _w(bot, uid, f"⏱ Wait {rem:.0f}s before another social.")
        return

    # Resolve target
    from modules.room_utils import _resolve_user_in_room
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, uid, f"@{target_name} is not in the room.")
        return
    target_user, _ = pair

    # Bot check — staff only
    from modules.live_bot_registry import live_bot_keys
    bot_names = {str(n).lower() for n in (live_bot_keys() or [])}
    if target_user.username.lower() in bot_names and not _is_staff(uname):
        await _w(bot, uid, "Can't target bots.")
        return

    a_cands, t_cands, template = _SOCIAL_TARGETS[kind]
    a_eid = _resolve_eid(a_cands)
    t_eid = _resolve_eid(t_cands)

    if not a_eid:
        await _w(bot, uid, f"Emote for !{kind} unavailable in registry.")
        return

    _TARGET_SOCIAL_CD[uid] = time.time()

    # Send attacker emote, then target reaction
    try:
        await bot.highrise.send_emote(a_eid, uid)
    except Exception as exc:
        print(f"[EMOTE_EXTRAS] {kind} attacker send err: {exc!r}")
    if t_eid:
        await asyncio.sleep(0.3)
        try:
            await bot.highrise.send_emote(t_eid, target_user.id)
        except Exception as exc:
            print(f"[EMOTE_EXTRAS] {kind} target send err: {exc!r}")

    # Public broadcast
    try:
        await bot.highrise.chat(template.format(a=uname, b=target_user.username)[:249])
    except Exception:
        pass


async def handle_kiss_social(bot, user, args):
    await _do_target_social(bot, user, args, "kiss")


async def handle_slap_social(bot, user, args):
    await _do_target_social(bot, user, args, "slap")


async def handle_superpunch(bot, user, args):
    await _do_target_social(bot, user, args, "superpunch")


async def handle_bonk(bot, user, args):
    await _do_target_social(bot, user, args, "bonk")


async def handle_yeet(bot, user, args):
    await _do_target_social(bot, user, args, "yeet")


async def handle_hypnotize(bot, user, args):
    await _do_target_social(bot, user, args, "hypnotize")


async def handle_duel(bot: "BaseBot", user: "User", args: list) -> None:
    """!duel @user — both players loop swordfight emote. VIP+ gated."""
    uid, uname = user.id, user.username
    if not _is_vip(uid) and not _is_staff(uname):
        await _w(bot, uid, "✨ VIP+ required for social emotes.")
        return
    if len(args) < 2:
        await _w(bot, uid, "Usage: !duel @user")
        return
    target_name = args[1].lstrip("@")
    if target_name.lower() == uname.lower():
        await _w(bot, uid, "You can't duel yourself.")
        return
    last = _TARGET_SOCIAL_CD.get(uid, 0.0)
    rem  = _TARGET_SOCIAL_CD_SECS - (time.time() - last)
    if rem > 0:
        await _w(bot, uid, f"⏱ Wait {rem:.0f}s before another social.")
        return
    from modules.room_utils import _resolve_user_in_room
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, uid, f"@{target_name} is not in the room.")
        return
    target_user, _ = pair
    from modules.live_bot_registry import live_bot_keys
    bot_names = {str(n).lower() for n in (live_bot_keys() or [])}
    if target_user.username.lower() in bot_names and not _is_staff(uname):
        await _w(bot, uid, "Can't target bots.")
        return
    eid = _resolve_eid(["swordfight", "emote-swordfight"])
    if not eid:
        await _w(bot, uid, "Swordfight emote unavailable.")
        return
    _TARGET_SOCIAL_CD[uid] = time.time()
    from modules.emote_system import _start_player_loop
    try:
        await _start_player_loop(bot, uid, eid, "swordfight",
                                  username=uname, log_event="duel")
        await _start_player_loop(bot, target_user.id, eid, "swordfight",
                                  username=target_user.username, log_event="duel")
    except Exception as exc:
        print(f"[EMOTE_EXTRAS] duel loop err: {exc!r}")
    try:
        await bot.highrise.chat(f"⚔ {uname} dueled {target_user.username}!"[:249])
    except Exception:
        pass


# ===========================================================================
# Section D + E — Group-Controller Sync
# ===========================================================================
# follower_uid -> leader_uid
_sync_leader_of:    dict[str, str]          = {}
# leader_uid -> set of follower_uids (multiple followers per leader)
_sync_followers:    dict[str, set[str]]     = {}
# leader_uid -> shared group loop asyncio.Task
_sync_group_tasks:  dict[str, asyncio.Task] = {}
# leader_uid -> emote_id the current group task is cycling (dedup guard)
_sync_group_emote:  dict[str, str]          = {}
# follower_uid -> leader_username  (for !syncstatus display)
_sync_leader_name:  dict[str, str]          = {}
# follower_uid -> follower_username (for leader's !syncstatus display)
_sync_follower_name: dict[str, str]         = {}

# ── Sync persistence DB helpers ───────────────────────────────────────────────
_SYNC_DB_READY = False


def _ensure_sync_tables() -> None:
    global _SYNC_DB_READY
    if _SYNC_DB_READY:
        return
    try:
        conn = db.get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_relations (
                follower_user_id  TEXT PRIMARY KEY,
                follower_username TEXT NOT NULL DEFAULT '',
                leader_user_id    TEXT NOT NULL,
                leader_username   TEXT NOT NULL DEFAULT '',
                is_active         INTEGER NOT NULL DEFAULT 1,
                persist_enabled   INTEGER NOT NULL DEFAULT 1,
                created_at        TEXT DEFAULT (datetime('now')),
                updated_at        TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sr_leader "
            "ON sync_relations(leader_user_id)"
        )
        conn.commit()
        conn.close()
        _SYNC_DB_READY = True
    except Exception as exc:
        print(f"[SYNC_DB] ensure_tables err: {exc!r}")


def _sync_db_save(follower_id: str, follower_name: str,
                  leader_id: str, leader_name: str) -> None:
    _ensure_sync_tables()
    try:
        conn = db.get_connection()
        conn.execute("""
            INSERT INTO sync_relations
              (follower_user_id, follower_username, leader_user_id, leader_username,
               is_active, persist_enabled, updated_at)
            VALUES (?, ?, ?, ?, 1, 1, datetime('now'))
            ON CONFLICT(follower_user_id) DO UPDATE SET
              follower_username=excluded.follower_username,
              leader_user_id=excluded.leader_user_id,
              leader_username=excluded.leader_username,
              is_active=1,
              updated_at=datetime('now')
        """, (follower_id, follower_name.lower(), leader_id, leader_name.lower()))
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[SYNC_DB] save err: {exc!r}")


def _sync_db_deactivate(follower_id: str) -> None:
    _ensure_sync_tables()
    try:
        conn = db.get_connection()
        conn.execute(
            "UPDATE sync_relations SET is_active=0, updated_at=datetime('now') "
            "WHERE follower_user_id=?", (follower_id,)
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[SYNC_DB] deactivate err: {exc!r}")


def _sync_db_set_persist(follower_id: str, enabled: bool) -> bool:
    _ensure_sync_tables()
    try:
        conn = db.get_connection()
        conn.execute(
            "UPDATE sync_relations SET persist_enabled=?, updated_at=datetime('now') "
            "WHERE follower_user_id=? AND is_active=1",
            (1 if enabled else 0, follower_id),
        )
        changed = conn.total_changes > 0
        conn.commit()
        conn.close()
        return changed
    except Exception as exc:
        print(f"[SYNC_DB] set_persist err: {exc!r}")
        return False


def _sync_db_followers_of_leader(leader_id: str) -> list[dict]:
    _ensure_sync_tables()
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT follower_user_id, follower_username, leader_user_id, leader_username "
            "FROM sync_relations "
            "WHERE leader_user_id=? AND is_active=1 AND persist_enabled=1",
            (leader_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        print(f"[SYNC_DB] followers_of_leader err: {exc!r}")
        return []


def _sync_db_active_sessions() -> list[dict]:
    _ensure_sync_tables()
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT follower_user_id, follower_username, leader_user_id, leader_username "
            "FROM sync_relations WHERE is_active=1 AND persist_enabled=1"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        print(f"[SYNC_DB] active_sessions err: {exc!r}")
        return []


def get_sync_group(leader_id: str) -> list[str]:
    """[leader_id] + all current follower_ids."""
    return [leader_id] + list(_sync_followers.get(leader_id, set()))


async def _sync_group_loop(bot: "BaseBot", leader_id: str,
                           emote_id: str, alias: str) -> None:
    """Shared keepalive loop for a sync group.

    Sleeps one emote-duration then re-sends the emote to all current
    followers.  Leader's own _run_player_loop keeps leader looping in
    parallel; both use get_emote_time() so they stay on the same cadence.
    Exits cleanly when no followers remain.
    """
    from modules.emote_system import _send_player, get_emote_time
    try:
        while True:
            duration = max(0.5, get_emote_time(emote_id))
            print(f"[SYNC_GROUP_LOOP] leader={leader_id} alias={alias} "
                  f"time={duration:.1f}")
            await asyncio.sleep(duration)
            followers = [f for f in _sync_followers.get(leader_id, set())
                         if _sync_leader_of.get(f) == leader_id]
            if not followers:
                _sync_group_tasks.pop(leader_id, None)
                _sync_group_emote.pop(leader_id, None)
                return
            await asyncio.gather(
                *[_send_player(bot, emote_id, f) for f in followers],
                return_exceptions=True,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[SYNC_GROUP_LOOP] err leader={leader_id}: {exc!r}")


async def start_sync_group_emote(bot: "BaseBot", leader_id: str,
                                  emote_id: str, alias: str) -> bool:
    """Called (via hook) when a bot-controlled player emote starts for leader_id.

    - If leader has no followers: returns False — _start_player_loop sends normally.
    - Otherwise: sends emote to leader + all followers simultaneously via
      asyncio.gather(), starts a shared group loop for follower keepalive,
      and returns True — caller skips its individual _send_player for the leader.
    Dedup guard: if the same emote is already running for this leader, skips.
    """
    followers = list(_sync_followers.get(leader_id, set()))
    if not followers:
        return False

    # Dedup: hook fires AND on_emote fires for bot-triggered emotes — skip second
    existing = _sync_group_tasks.get(leader_id)
    if (existing and not existing.done()
            and _sync_group_emote.get(leader_id) == emote_id):
        return True

    # Cancel old group task (leader changed emote)
    old = _sync_group_tasks.pop(leader_id, None)
    if old and not old.done():
        old.cancel()

    # Simultaneous send to leader + all valid followers
    group = get_sync_group(leader_id)
    print(f"[SYNC_GROUP_SEND] leader={leader_id} members={len(group)} emote={emote_id}")
    try:
        from modules.emote_system import _send_player
        await asyncio.gather(
            *[_send_player(bot, emote_id, uid) for uid in group],
            return_exceptions=True,
        )
    except Exception as exc:
        print(f"[SYNC_GROUP_SEND] gather err: {exc!r}")

    _sync_group_emote[leader_id] = emote_id
    # Group loop re-sends followers only; leader's _run_player_loop handles leader
    _sync_group_tasks[leader_id] = asyncio.create_task(
        _sync_group_loop(bot, leader_id, emote_id, alias))
    return True


def _on_cancel_player_loop(uid: str) -> None:
    """Hook called by _cancel_player_loop — cancel group loop for this leader.

    Subscriptions are kept intact: next emote from the leader re-engages
    the group.  Only clear() / leave / syncstop unsubscribes followers.
    """
    old = _sync_group_tasks.pop(uid, None)
    if old and not old.done():
        old.cancel()
    _sync_group_emote.pop(uid, None)


def _dissolve_leader(leader_id: str) -> None:
    """Cancel group task and remove all follower subscriptions."""
    old = _sync_group_tasks.pop(leader_id, None)
    if old and not old.done():
        old.cancel()
    _sync_group_emote.pop(leader_id, None)
    for f in list(_sync_followers.pop(leader_id, set())):
        _sync_leader_of.pop(f, None)
        _sync_leader_name.pop(f, None)
        _sync_follower_name.pop(f, None)
    print(f"[SYNC_DISSOLVE] leader={leader_id}")


def _unsubscribe_follower(follower_id: str) -> str | None:
    """Remove follower from their leader's group. Returns prior leader_id."""
    leader_id = _sync_leader_of.pop(follower_id, None)
    _sync_leader_name.pop(follower_id, None)
    _sync_follower_name.pop(follower_id, None)
    if leader_id is None:
        return None
    followers = _sync_followers.get(leader_id)
    if followers:
        followers.discard(follower_id)
        if not followers:
            _dissolve_leader(leader_id)
    print(f"[SYNC_UNSUBSCRIBE] follower={follower_id}")
    return leader_id


def get_current_controlled_emote(user_id: str) -> dict | None:
    """Return info about user_id's current bot-controlled emote, or None.

    Checks in priority order:
      1. Active player loop  (_player_emotes / _player_loops in emote_system)
      2. Dancefloor          (user is inside box and shared cycle is broadcasting)
      3. Sync group          (user is a leader whose group task is running)

    Returns a dict: {source, alias, eid, interval}
    Python resolves _df_inside / _df_current_eid at call-time so the forward
    reference to dancefloor state (defined later in this file) is safe.
    """
    from modules.emote_system import _player_emotes, _player_loops, get_emote_time

    # 1. Player loop (plain emote trigger or !emote command)
    eid = _player_emotes.get(user_id)
    if eid:
        task = _player_loops.get(user_id)
        if task and not task.done():
            return {"source": "player_loop", "alias": eid,
                    "eid": eid, "interval": get_emote_time(eid)}

    # 2. Dancefloor (user inside box; shared cycle currently active)
    if user_id in _df_inside:
        cur_eid = _df_current_eid[0]
        if cur_eid:
            return {"source": "dancefloor", "alias": cur_eid,
                    "eid": cur_eid, "interval": get_emote_time(cur_eid)}

    # 3. Sync group (user is a leader with a running group task)
    grp_eid = _sync_group_emote.get(user_id)
    if grp_eid:
        grp_task = _sync_group_tasks.get(user_id)
        if grp_task and not grp_task.done():
            return {"source": "sync_group", "alias": grp_eid,
                    "eid": grp_eid, "interval": get_emote_time(grp_eid)}

    return None


async def handle_sync(bot: "BaseBot", user: "User", args: list) -> None:
    """!sync @Leader — subscribe to Leader's emote group."""
    if len(args) < 2 or not args[1].startswith("@"):
        await _w(bot, user.id, "Usage: !sync @user")
        return
    target_name = args[1].lstrip("@")
    if target_name.lower() == user.username.lower():
        await _w(bot, user.id, "You can't sync with yourself.")
        return
    from modules.room_utils import _resolve_user_in_room
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, user.id, f"@{target_name} is not in the room.")
        return
    leader_user, _ = pair

    # Unsubscribe from any prior leader
    _unsubscribe_follower(user.id)

    # Cancel solo player loop, custom loop, and dancefloor slot
    try:
        from modules.emote_system import _cancel_player_loop
        _cancel_player_loop(user.id)
    except Exception:
        pass
    try:
        from modules.custom_emotes import stop_custom_permanent
        stop_custom_permanent(user.id, "sync_subscribe")
    except Exception:
        pass
    _df_inside.discard(user.id)
    _df_user_emote.pop(user.id, None)

    _sync_leader_of[user.id]    = leader_user.id
    _sync_leader_name[user.id]  = leader_user.username
    _sync_follower_name[user.id] = user.username
    _sync_followers.setdefault(leader_user.id, set()).add(user.id)
    print(f"[SYNC_SUBSCRIBE] follower={user.id} leader={leader_user.id}")
    _sync_db_save(user.id, user.username, leader_user.id, leader_user.username)

    # Catch-up: leader already in a bot-controlled emote → send it now
    cur = get_current_controlled_emote(leader_user.id)
    if cur:
        eid, alias = cur["eid"], cur["alias"]
        print(f"[SYNC_CATCHUP] follower={user.id} leader={leader_user.id} "
              f"source={cur['source']} alias={alias} eid={eid}")
        try:
            from modules.emote_system import _send_player
            await _send_player(bot, eid, user.id)
        except Exception as exc:
            print(f"[SYNC_CATCHUP] send err: {exc!r}")
        # Start group loop if not already running (covers first-follower case)
        existing_task = _sync_group_tasks.get(leader_user.id)
        if existing_task is None or existing_task.done():
            _sync_group_emote[leader_user.id] = eid
            _sync_group_tasks[leader_user.id] = asyncio.create_task(
                _sync_group_loop(bot, leader_user.id, eid, alias))
        await _w(bot, user.id,
                 f"🔄 Synced to @{leader_user.username} — "
                 f"now doing: {alias}"[:249])
    else:
        print(f"[SYNC_WAITING] follower={user.id} leader={leader_user.id}")
        await _w(bot, user.id,
                 f"🔄 Synced to @{leader_user.username}. "
                 f"Waiting for their next emote.")


async def try_sync_shortcut(bot: "BaseBot", user: "User",
                             message: str) -> bool:
    """Bare 'Stop' from a follower ends sync. Returns True if handled."""
    if not message:
        return False
    if message.strip().lower() == "stop" and is_in_sync(user.id):
        await handle_syncstop(bot, user, [])
        return True
    return False


async def handle_syncstop(bot: "BaseBot", user: "User",
                           _args: list | None = None) -> None:
    """!syncstop — leave sync group."""
    _sync_db_deactivate(user.id)   # always mark inactive in DB
    prior = _unsubscribe_follower(user.id)
    if prior is None:
        await _w(bot, user.id, "You have no active sync.")
        return
    try:
        from modules.emote_system import _cancel_player_loop
        _cancel_player_loop(user.id)
    except Exception:
        pass
    await _w(bot, user.id, "⏹ Sync stopped.")


def is_in_sync(user_id: str) -> bool:
    """True if user_id is subscribed to a sync group as a follower."""
    return user_id in _sync_leader_of


async def handle_syncstatus(bot: "BaseBot", user: "User",
                             _args: list | None = None) -> None:
    """!syncstatus — show your sync group status."""
    uid = user.id
    # As follower
    if uid in _sync_leader_of:
        leader_name = _sync_leader_name.get(uid, "?")
        await _w(bot, uid, f"🔄 Synced to: @{leader_name}")
        return
    # As leader
    followers = list(_sync_followers.get(uid, set()))
    if followers:
        names = ", ".join(
            f"@{_sync_follower_name.get(f, f[:6])}" for f in followers[:6])
        await _w(bot, uid,
                 f"👥 Followers ({len(followers)}): {names}"[:249])
        return
    await _w(bot, uid, "No active sync.")


def clear_sync_on_leave(user_id: str) -> None:
    """Called from main.on_user_leave — clean up sync state for a leaving user."""
    # As follower
    _unsubscribe_follower(user_id)
    # As leader — dissolve entire group
    if user_id in _sync_followers:
        _dissolve_leader(user_id)


# Register hooks with emote_system at import time.
# emote_system never imports emote_extras, so there is no circular import.
try:
    from modules.emote_system import (
        set_group_start_hook as _set_gs_hook,
        set_cancel_loop_hook as _set_cl_hook,
    )
    _set_gs_hook(start_sync_group_emote)
    _set_cl_hook(_on_cancel_player_loop)
except Exception as _hook_exc:
    print(f"[SYNC] hook registration failed: {_hook_exc!r}")


# ── Sync persistence: startup recovery + leader-join resume ──────────────────

async def startup_sync_recovery(bot: "BaseBot") -> None:
    """On DJ bot startup: recover active sync relationships from DB."""
    try:
        await asyncio.sleep(6)
        sessions = _sync_db_active_sessions()
        if not sessions:
            print("[SYNC_RECOVERY] no active sessions to recover")
            return
        try:
            resp = await bot.highrise.get_room_users()
            room_raw = list(resp.content) if hasattr(resp, "content") else []
        except Exception:
            room_raw = []
        room_by_id: dict[str, object] = {u.id: u for u, _ in room_raw}
        print(f"[SYNC_RECOVERY] {len(sessions)} sessions | {len(room_by_id)} in room")
        for row in sessions:
            fid   = row["follower_user_id"]
            lid   = row["leader_user_id"]
            lname = row["leader_username"]
            if fid not in room_by_id:
                continue
            follower_obj = room_by_id[fid]
            if lid in room_by_id:
                leader_obj = room_by_id[lid]
                _unsubscribe_follower(fid)
                _sync_leader_of[fid]     = lid
                _sync_leader_name[fid]   = leader_obj.username
                _sync_follower_name[fid] = follower_obj.username
                _sync_followers.setdefault(lid, set()).add(fid)
                print(f"[SYNC_RECOVERY] restored {fid} -> {lid}")
                await _w(bot, fid,
                         f"🔄 Sync with @{leader_obj.username} restored.")
            else:
                print(f"[SYNC_RECOVERY] leader={lid} offline, follower={fid} waiting")
                await _w(bot, fid,
                         f"🔄 Synced to @{lname}. Waiting for them to return.")
    except Exception as exc:
        print(f"[SYNC_RECOVERY] err: {exc!r}")


async def on_sync_leader_join(bot: "BaseBot", user: "User") -> None:
    """Called from on_user_join — if the joining user is a persisted leader,
    re-subscribe any in-room followers who are waiting for them."""
    try:
        rows = _sync_db_followers_of_leader(user.id)
        if not rows:
            return
        try:
            resp = await bot.highrise.get_room_users()
            room_raw = list(resp.content) if hasattr(resp, "content") else []
        except Exception:
            room_raw = []
        room_by_id: dict[str, object] = {u.id: u for u, _ in room_raw}
        for row in rows:
            fid = row["follower_user_id"]
            if fid not in room_by_id:
                continue
            if _sync_leader_of.get(fid) == user.id:
                continue   # already synced in memory
            follower_obj = room_by_id[fid]
            _unsubscribe_follower(fid)
            _sync_leader_of[fid]     = user.id
            _sync_leader_name[fid]   = user.username
            _sync_follower_name[fid] = follower_obj.username
            _sync_followers.setdefault(user.id, set()).add(fid)
            print(f"[SYNC_LEADER_JOIN] leader={user.id} resumed follower={fid}")
            await _w(bot, fid, f"🔄 @{user.username} is back — sync resumed!")
    except Exception as exc:
        print(f"[SYNC_LEADER_JOIN] err: {exc!r}")


async def handle_syncpersist(bot: "BaseBot", user: "User", args: list) -> None:
    """!syncpersist on|off — toggle sync recovery across bot restarts."""
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await _w(bot, user.id, "Usage: !syncpersist on|off")
        return
    enabled = args[1].lower() == "on"
    changed = _sync_db_set_persist(user.id, enabled)
    if not changed:
        await _w(bot, user.id, "No active sync to configure.")
        return
    state  = "ON" if enabled else "OFF"
    suffix = ("Sync will resume after restart."
              if enabled else "Will not resume after restart.")
    await _w(bot, user.id, f"🔄 Sync persistence {state}. {suffix}"[:249])


async def handle_synchelp(bot: "BaseBot", user: "User",
                           _args: list | None = None) -> None:
    """!synchelp — sync system reference."""
    await _w(bot, user.id,
             "🔄 Sync: !sync @user | !syncstop | !syncstatus | "
             "!syncpersist on|off | !syncdebug @u (staff) | Stop=quit"[:249])


# ===========================================================================
# Section F — Staff room-wide emote
# ===========================================================================
async def handle_emote_all(bot: "BaseBot", user: "User", args: list) -> None:
    """!emote all <emote>  — one-time room-wide emote (staff only).

    Sends <emote> once to every non-bot player currently in the room.
    Does NOT loop. Does NOT persist. No stop command needed.
    """
    if not _is_staff(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !emote all <emote>")
        return
    sub = args[2].lower()
    entry = _reg_get(sub)
    if not entry:
        await _w(bot, user.id, f"Unknown emote '{sub}'.")
        return
    eid = entry["id"]
    from modules.live_bot_registry import live_bot_keys
    try:
        resp = await bot.highrise.get_room_users()
        users = list(resp.content) if hasattr(resp, "content") else []
    except Exception:
        users = []
    bot_names = {str(n).lower() for n in (live_bot_keys() or [])}
    count = 0
    for u, _pos in users:
        if u.username.lower() in bot_names:
            continue
        try:
            await bot.highrise.send_emote(eid, u.id)
            count += 1
        except Exception:
            pass
    print(f"[ROOM_EMOTE_ONCE] eid={eid!r} count={count}")
    await _w(bot, user.id, f"🎭 '{sub}' sent to {count} player(s).")


# ===========================================================================
# Section G — Favorite emotes
# ===========================================================================
async def handle_favemotes(bot: "BaseBot", user: "User",
                            _args: list | None = None) -> None:
    """!favemotes — list this user's saved favorites, alphabetical, paged."""
    _ensure_tables()
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT alias FROM fav_emotes WHERE user_id=?", (user.id,)
        ).fetchall()
        conn.close()
    except Exception as exc:
        print(f"[FAVEMOTES] read err: {exc!r}")
        rows = []
    items = [r[0] for r in rows]
    await _send_compact_pages(bot, user.id, "⭐ Favorites", items)


async def handle_favemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!favemote add|remove|clear <emote>"""
    _ensure_tables()
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: !favemote add|remove|clear <emote>  |  !favemotes")
        return
    sub = args[1].lower()
    uid = user.id

    if sub == "clear":
        try:
            conn = db.get_connection()
            conn.execute("DELETE FROM fav_emotes WHERE user_id=?", (uid,))
            conn.commit()
            conn.close()
            await _w(bot, uid, "⭐ All favorites cleared.")
        except Exception as exc:
            await _w(bot, uid, f"DB error: {exc!r}")
        return

    if sub not in ("add", "remove") or len(args) < 3:
        await _w(bot, uid, "Usage: !favemote add|remove <emote>")
        return
    alias = args[2].strip()
    entry = _reg_get(alias)
    if not entry:
        await _w(bot, uid, f"Unknown emote '{alias}'.")
        return
    norm = (entry.get("name") or alias).strip()

    try:
        conn = db.get_connection()
        if sub == "add":
            conn.execute(
                "INSERT OR IGNORE INTO fav_emotes (user_id, alias) VALUES (?, ?)",
                (uid, norm),
            )
            msg = f"⭐ Added '{norm}' to favorites."
        else:
            conn.execute(
                "DELETE FROM fav_emotes WHERE user_id=? AND alias=?",
                (uid, norm),
            )
            msg = f"⭐ Removed '{norm}'."
        conn.commit()
        conn.close()
        await _w(bot, uid, msg)
    except Exception as exc:
        await _w(bot, uid, f"DB error: {exc!r}")


# ===========================================================================
# Section H — Dancefloor
# ===========================================================================
# Stored in room_settings (no new table needed):
#   dancefloor_p1 = "x,y,z"     (set by setpoint 1)
#   dancefloor_p2 = "x,y,z"     (set by setpoint 2)
#   dancefloor_box = "x1,z1,x2,z2"  (saved AABB on /save)
#   dancefloor_emotes = "alias,alias,alias"
#   dancefloor_active = "true" | "false"
#
# The polling task tracks who is currently inside and runs/stops their
# personal emote loop.

_DF_POLL_SECS = 2.0
_df_task:        dict[str, asyncio.Task] = {}  # {"_": position-polling loop task}
_df_cycle_task:  dict[str, asyncio.Task] = {}  # {"_": shared emote-cycle task}
_df_inside:      set[str]                = set()  # user_ids currently inside box
_df_user_emote:  dict[str, str]          = {}     # uid -> last emote alias (compat)
_df_current_eid: list[str]               = [""]   # [0] = eid currently being broadcast
_df_current_step: list[int]              = [0]    # [0] = step index in active sequence

# ── Dancefloor sequence & pack DB helpers ─────────────────────────────────────
import json as _json  # used by sequence helpers

_DF_DB_READY = False


def _ensure_df_tables() -> None:
    global _DF_DB_READY
    if _DF_DB_READY:
        return
    try:
        conn = db.get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dancefloor_packs (
                pack_name     TEXT PRIMARY KEY,
                mode          TEXT NOT NULL DEFAULT 'simple',
                sequence_json TEXT NOT NULL DEFAULT '[]',
                created_at    TEXT DEFAULT (datetime('now')),
                updated_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
        conn.close()
        _DF_DB_READY = True
    except Exception as exc:
        print(f"[DF_PACK_DB] ensure err: {exc!r}")


def _df_get_sequence() -> list[dict]:
    """Read active typed sequence from room_settings.
    Falls back to legacy dancefloor_emotes comma list."""
    raw = db.get_room_setting("dancefloor_sequence_json", "")
    if raw:
        try:
            return _json.loads(raw)
        except Exception:
            pass
    # Legacy fallback: convert plain alias list to typed steps
    seq = []
    for a in _df_get_emotes():
        ent = _reg_get(a)
        if ent and ent.get("id") and ent.get("player"):
            seq.append({"alias": a, "eid": ent["id"], "seconds": None})
    return seq


def _df_set_sequence(seq: list[dict], mode: str = "simple") -> None:
    db.set_room_setting("dancefloor_sequence_json", _json.dumps(seq))
    db.set_room_setting("dancefloor_mode", mode)


def _df_get_mode() -> str:
    return db.get_room_setting("dancefloor_mode", "simple")


async def _df_shared_cycle(bot: "BaseBot") -> None:
    """Shared emote cycle — broadcasts the same emote to ALL players inside.

    - simple/random modes: re-reads pool each cycle and shuffles.
    - timed mode: plays fixed sequence in order with per-step seconds.
    - If a step's seconds is None, falls back to registry timing.
    """
    from modules.emote_system import _send_player, get_emote_time
    import random as _rnd

    prev_alias: str | None = None
    try:
        while True:
            if not _df_inside:
                await asyncio.sleep(1.0)
                continue

            seq  = _df_get_sequence()
            mode = _df_get_mode()

            # Build valid (alias, eid, seconds|None) triples
            valid_steps: list[tuple[str, str, float | None]] = []
            for step in seq:
                alias   = step.get("alias", "")
                eid     = step.get("eid",   "")
                seconds = step.get("seconds")
                if not eid:
                    ent = _reg_get(alias)
                    if ent and ent.get("id") and ent.get("player"):
                        eid = ent["id"]
                    else:
                        continue
                valid_steps.append((alias, eid, seconds))

            if not valid_steps:
                await asyncio.sleep(2.0)
                continue

            # Shuffle for non-timed modes; avoid repeating last emote
            if mode in ("simple", "random"):
                _rnd.shuffle(valid_steps)
                if len(valid_steps) > 1 and valid_steps[0][0] == prev_alias:
                    valid_steps.append(valid_steps.pop(0))

            for idx, (alias, eid, seconds) in enumerate(valid_steps):
                players = list(_df_inside)
                if not players:
                    break
                _df_current_eid[0]  = eid
                _df_current_step[0] = idx
                # Fan out to sync followers of inside players not on the floor
                extra: list[str] = []
                for inside_uid in players:
                    fols = [
                        f for f in _sync_followers.get(inside_uid, set())
                        if _sync_leader_of.get(f) == inside_uid
                        and f not in _df_inside
                    ]
                    if fols:
                        print(f"[DANCEFLOOR_SYNC_FOLLOWERS] leader={inside_uid} "
                              f"followers={len(fols)} alias={alias}")
                        extra.extend(fols)
                all_targets = players + extra
                duration = (float(seconds) if seconds is not None
                            else max(0.5, get_emote_time(eid)))
                print(f"[DANCEFLOOR_CYCLE] mode={mode} alias={alias} "
                      f"t={duration:.1f}s players={len(players)}")
                await asyncio.gather(
                    *[_send_player(bot, eid, uid) for uid in all_targets],
                    return_exceptions=True,
                )
                prev_alias = alias
                await asyncio.sleep(duration)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[DANCEFLOOR_CYCLE] err: {exc!r}")


def _df_get_point(slot: int) -> tuple[float, float, float] | None:
    raw = db.get_room_setting(f"dancefloor_p{slot}", "")
    if not raw:
        return None
    try:
        x, y, z = [float(s) for s in raw.split(",")]
        return (x, y, z)
    except Exception:
        return None


def _df_get_box() -> tuple[float, float, float, float] | None:
    raw = db.get_room_setting("dancefloor_box", "")
    if not raw:
        return None
    try:
        x1, z1, x2, z2 = [float(s) for s in raw.split(",")]
        return (min(x1, x2), min(z1, z2), max(x1, x2), max(z1, z2))
    except Exception:
        return None


def _df_get_emotes() -> list[str]:
    raw = db.get_room_setting("dancefloor_emotes", "")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _df_is_active() -> bool:
    return db.get_room_setting("dancefloor_active", "false").lower() == "true"


def _in_box(pos, box) -> bool:
    if pos is None or box is None:
        return False
    x1, z1, x2, z2 = box
    try:
        px, pz = float(getattr(pos, "x", 0.0)), float(getattr(pos, "z", 0.0))
    except Exception:
        return False
    return (x1 <= px <= x2) and (z1 <= pz <= z2)


async def _dancefloor_loop(bot: "BaseBot") -> None:
    """Poll positions every _DF_POLL_SECS; update _df_inside; manage shared cycle."""
    from modules.live_bot_registry import live_bot_keys
    print("[DANCEFLOOR] loop started")
    while True:
        try:
            if not _df_is_active():
                await asyncio.sleep(_DF_POLL_SECS)
                continue
            box = _df_get_box()
            if not box or not _df_get_emotes():
                await asyncio.sleep(_DF_POLL_SECS)
                continue

            # Fetch live room users (authoritative — _user_positions can lag)
            try:
                resp = await bot.highrise.get_room_users()
                users = list(resp.content) if hasattr(resp, "content") else []
            except Exception:
                users = []
            bot_names = {str(n).lower() for n in (live_bot_keys() or [])}

            current_inside: set[str] = set()
            for u, pos in users:
                if u.username.lower() in bot_names:
                    continue
                if not _in_box(pos, box):
                    continue
                # Skip players that are currently in a sync group as followers
                if is_in_sync(u.id):
                    print(f"[DANCEFLOOR_SKIP_SYNC] user={u.id}")
                    continue
                current_inside.add(u.id)
                if u.id not in _df_inside:
                    print(f"[DANCEFLOOR_ENTER] user={u.id}")
                    try:
                        from modules.custom_emotes import stop_custom_permanent
                        stop_custom_permanent(u.id, "dancefloor_entry")
                    except Exception:
                        pass

            print(f"[DANCEFLOOR_TICK] users={len(users)} "
                  f"inside={len(current_inside)} active=true")

            for left_uid in (_df_inside - current_inside):
                print(f"[DANCEFLOOR_EXIT] user={left_uid}")

            _df_inside.clear()
            _df_inside.update(current_inside)

            # Ensure shared cycle is running iff players are inside
            cyc = _df_cycle_task.get("_")
            if _df_inside and (cyc is None or cyc.done()):
                _df_cycle_task["_"] = asyncio.create_task(_df_shared_cycle(bot))
                print("[DANCEFLOOR] shared cycle started")
            elif not _df_inside and cyc and not cyc.done():
                cyc.cancel()
                _df_cycle_task.pop("_", None)
                print("[DANCEFLOOR] shared cycle stopped (empty)")
        except asyncio.CancelledError:
            print("[DANCEFLOOR] loop cancelled")
            raise
        except Exception as exc:
            print(f"[DANCEFLOOR] tick err: {exc!r}")
        await asyncio.sleep(_DF_POLL_SECS)


def _ensure_dancefloor_task(bot: "BaseBot") -> None:
    t = _df_task.get("_")
    if t and not t.done():
        return
    _df_task["_"] = asyncio.create_task(_dancefloor_loop(bot))


def _stop_dancefloor_task() -> None:
    # Cancel position-polling loop
    t = _df_task.pop("_", None)
    if t and not t.done():
        t.cancel()
    # Cancel shared emote-cycle task
    cyc = _df_cycle_task.pop("_", None)
    if cyc and not cyc.done():
        cyc.cancel()
    _df_inside.clear()
    _df_user_emote.clear()


async def handle_dancefloor(bot: "BaseBot", user: "User", args: list) -> None:
    """!dancefloor <subcommand>"""
    uid   = user.id
    uname = user.username
    if not _is_staff(uname):
        await _w(bot, uid, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, uid,
                 "!dancefloor: setpoint 1|2 | save | emotes|random|timed | "
                 "savepack|loadpack|packs | start|stop|status|debug|clear"[:249])
        return
    sub = args[1].lower()

    # ----- setpoint -----------------------------------------------------
    if sub == "setpoint":
        if len(args) < 3 or args[2] not in ("1", "2"):
            await _w(bot, uid, "Usage: !dancefloor setpoint 1|2")
            return
        slot = args[2]
        from modules.room_utils import _user_positions
        pos = _user_positions.get(uid)
        if pos is None:
            await _w(bot, uid, "Move once so the bot can read your position, then retry.")
            return
        try:
            x, y, z = float(pos.x), float(pos.y), float(pos.z)
        except Exception:
            await _w(bot, uid, "Your position can't be read right now (try moving).")
            return
        db.set_room_setting(f"dancefloor_p{slot}", f"{x:.2f},{y:.2f},{z:.2f}")
        await _w(bot, uid, f"📍 Point {slot} set: ({x:.1f}, {y:.1f}, {z:.1f}). "
                          f"!dancefloor save when both are set.")
        return

    # ----- save (build AABB from p1 + p2) -------------------------------
    if sub == "save":
        p1 = _df_get_point(1)
        p2 = _df_get_point(2)
        if not p1 or not p2:
            await _w(bot, uid, "Set both points first: !dancefloor setpoint 1 / 2")
            return
        x1, _, z1 = p1
        x2, _, z2 = p2
        db.set_room_setting("dancefloor_box",
                            f"{min(x1, x2):.2f},{min(z1, z2):.2f},"
                            f"{max(x1, x2):.2f},{max(z1, z2):.2f}")
        await _w(bot, uid, f"💾 Dancefloor box saved: "
                          f"x[{min(x1,x2):.1f}..{max(x1,x2):.1f}], "
                          f"z[{min(z1,z2):.1f}..{max(z1,z2):.1f}].")
        return

    # ----- random (top-level shortcut: same as !dancefloor emotes random) ------
    if sub == "random":
        args = list(args)
        if len(args) > 2:
            args.insert(2, "random")
        else:
            args.append("random")
        args[1] = "emotes"
        sub = "emotes"

    # ----- emotes / emote -----------------------------------------------
    if sub in ("emotes", "emote"):
        rest = args[2:]
        if not rest:
            seq  = _df_get_sequence()
            mode = _df_get_mode()
            if seq:
                names  = ", ".join(s["alias"] for s in seq[:5])
                suffix = f"…+{len(seq)-5}" if len(seq) > 5 else ""
                await _w(bot, uid,
                         f"🎵 DF {mode}: {len(seq)} steps — {names}{suffix}"[:249])
            else:
                await _w(bot, uid, "🎵 Dancefloor: no sequence set yet.")
            return
        if rest[0].lower() == "random":
            # Build exclusion set of social-emote aliases (attacker + reaction)
            social_excl: set[str] = set()
            for a_cands, t_cands, _ in _SOCIAL_TARGETS.values():
                for c in (*a_cands, *t_cands):
                    social_excl.add(c.lower())
                    ent = _reg_get(c)
                    if ent:
                        nm = (ent.get("name") or "").lower()
                        if nm:
                            social_excl.add(nm)
            raw_pool = _reg_player_aliases()
            seen: set[str] = set()
            pool: list[str] = []
            for a in raw_pool:
                low = a.lower()
                if low in seen or low in social_excl:
                    continue
                seen.add(low)
                pool.append(a)
            if not pool:
                await _w(bot, uid, "No player emotes available.")
                return
            # N is optional — bare "random" uses all available
            n = len(pool)
            if len(rest) >= 2:
                try:
                    n = max(1, int(rest[1]))
                except Exception:
                    await _w(bot, uid, "Usage: !dancefloor random [N]")
                    return
            picks = random.sample(pool, min(n, len(pool)))
            seq = []
            for a in picks:
                ent = _reg_get(a)
                if ent and ent.get("id"):
                    seq.append({"alias": a, "eid": ent["id"], "seconds": None})
            _df_set_sequence(seq, "random")
            db.set_room_setting("dancefloor_emotes",
                                ",".join(p["alias"] for p in seq))
            await _w(bot, uid, f"🎲 Random {len(seq)} emotes saved.")
            return
        # Explicit list — support both space-separated and comma-separated
        joined = " ".join(rest)
        aliases = ([s.strip() for s in joined.split(",") if s.strip()]
                   if "," in joined
                   else [s.strip() for s in rest if s.strip()])
        valid_steps: list[dict] = []
        bad: list[str] = []
        for a in aliases:
            ent = _reg_get(a)
            if ent and ent.get("player"):
                valid_steps.append({"alias": ent.get("name") or a,
                                    "eid": ent["id"], "seconds": None})
            else:
                bad.append(a)
        if not valid_steps:
            await _w(bot, uid,
                     f"No valid player emotes. Rejected: {', '.join(bad)[:200]}")
            return
        _df_set_sequence(valid_steps, "simple")
        db.set_room_setting("dancefloor_emotes",
                            ",".join(s["alias"] for s in valid_steps))
        msg = f"🎵 Saved {len(valid_steps)} emotes."
        if bad:
            msg += f" Rejected: {', '.join(bad)[:120]}"
        await _w(bot, uid, msg[:249])
        return

    # ----- timed --------------------------------------------------------
    if sub == "timed":
        rest = args[2:]
        if len(rest) < 2 or len(rest) % 2 != 0:
            await _w(bot, uid,
                     "Usage: !dancefloor timed <emote> <secs> [<emote> <secs>...]")
            return
        seq_t: list[dict] = []
        bad_t: list[str] = []
        for i in range(0, len(rest), 2):
            a, s = rest[i], rest[i + 1]
            try:
                secs = max(0.5, float(s))
            except ValueError:
                bad_t.append(a)
                continue
            ent = _reg_get(a)
            if ent and ent.get("player"):
                seq_t.append({"alias": ent.get("name") or a,
                               "eid": ent["id"], "seconds": secs})
            else:
                bad_t.append(a)
        if not seq_t:
            await _w(bot, uid,
                     f"No valid emotes. Rejected: {', '.join(bad_t)[:200]}")
            return
        _df_set_sequence(seq_t, "timed")
        msg = f"⏱ Timed {len(seq_t)} steps saved."
        if bad_t:
            msg += f" Rejected: {', '.join(bad_t)[:100]}"
        await _w(bot, uid, msg[:249])
        return

    # ----- start --------------------------------------------------------
    if sub == "start":
        box = _df_get_box()
        emotes = _df_get_emotes()
        if not box:
            await _w(bot, uid, "Save points first: setpoint 1/2 → save.")
            return
        if not emotes:
            await _w(bot, uid, "Set emotes first: !dancefloor emotes ...")
            return
        db.set_room_setting("dancefloor_active", "true")
        _ensure_dancefloor_task(bot)
        await _w(bot, uid, "▶ Dancefloor active. Players inside box auto-loop.")
        return

    # ----- stop ---------------------------------------------------------
    if sub == "stop":
        db.set_room_setting("dancefloor_active", "false")
        _stop_dancefloor_task()
        await _w(bot, uid, "⏹ Dancefloor stopped.")
        return

    # ----- status -------------------------------------------------------
    if sub == "status":
        box = _df_get_box()
        emotes = _df_get_emotes()
        active = _df_is_active()
        inside = len(_df_inside)
        box_str = (f"x[{box[0]:.1f}..{box[2]:.1f}] z[{box[1]:.1f}..{box[3]:.1f}]"
                   if box else "unset")
        await _w(bot, uid,
                 f"💃 Dancefloor: active={active} | box={box_str} | "
                 f"emotes={len(emotes)} | inside={inside}")
        return

    # ----- clear --------------------------------------------------------
    if sub == "clear":
        db.set_room_setting("dancefloor_active", "false")
        db.set_room_setting("dancefloor_p1", "")
        db.set_room_setting("dancefloor_p2", "")
        db.set_room_setting("dancefloor_box", "")
        db.set_room_setting("dancefloor_emotes", "")
        _stop_dancefloor_task()
        await _w(bot, uid, "🗑 Dancefloor cleared.")
        return

    # ----- savepack -----------------------------------------------------
    if sub == "savepack":
        if len(args) < 3:
            await _w(bot, uid, "Usage: !dancefloor savepack <name>")
            return
        pack_name = " ".join(args[2:]).strip().lower()[:50]
        seq  = _df_get_sequence()
        mode = _df_get_mode()
        if not seq:
            await _w(bot, uid, "No sequence active. Set emotes/timed first.")
            return
        _ensure_df_tables()
        try:
            conn = db.get_connection()
            conn.execute("""
                INSERT INTO dancefloor_packs (pack_name, mode, sequence_json, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(pack_name) DO UPDATE SET
                  mode=excluded.mode,
                  sequence_json=excluded.sequence_json,
                  updated_at=datetime('now')
            """, (pack_name, mode, _json.dumps(seq)))
            conn.commit()
            conn.close()
            await _w(bot, uid,
                     f"💾 Pack '{pack_name}' saved ({mode}, {len(seq)} steps).")
        except Exception as exc:
            await _w(bot, uid, f"DB error: {exc!r}"[:200])
        return

    # ----- loadpack -----------------------------------------------------
    if sub == "loadpack":
        if len(args) < 3:
            await _w(bot, uid, "Usage: !dancefloor loadpack <name>")
            return
        pack_name = " ".join(args[2:]).strip().lower()[:50]
        _ensure_df_tables()
        try:
            conn = db.get_connection()
            row = conn.execute(
                "SELECT mode, sequence_json FROM dancefloor_packs WHERE pack_name=?",
                (pack_name,)
            ).fetchone()
            conn.close()
            if not row:
                await _w(bot, uid, f"Pack '{pack_name}' not found.")
                return
            pmode, pjson = row[0], row[1]
            pseq = _json.loads(pjson) if pjson else []
            _df_set_sequence(pseq, pmode)
            db.set_room_setting("dancefloor_emotes",
                                ",".join(s["alias"] for s in pseq))
            await _w(bot, uid,
                     f"📂 Loaded '{pack_name}' ({pmode}, {len(pseq)} steps).")
        except Exception as exc:
            await _w(bot, uid, f"DB error: {exc!r}"[:200])
        return

    # ----- packs --------------------------------------------------------
    if sub == "packs":
        _ensure_df_tables()
        try:
            conn = db.get_connection()
            rows = conn.execute(
                "SELECT pack_name, mode, sequence_json FROM dancefloor_packs "
                "ORDER BY pack_name"
            ).fetchall()
            conn.close()
            if not rows:
                await _w(bot, uid, "No dancefloor packs saved yet.")
                return
            lines = []
            for r in rows[:12]:
                try:
                    cnt = len(_json.loads(r[2])) if r[2] else 0
                except Exception:
                    cnt = "?"
                lines.append(f"{r[0]}({r[1]},{cnt})")
            header = f"📦 {len(rows)} pack(s):"
            await _w(bot, uid, (header + " " + "  ".join(lines[:6]))[:249])
            if len(lines) > 6:
                await _w(bot, uid, "  ".join(lines[6:12])[:249])
        except Exception as exc:
            await _w(bot, uid, f"DB error: {exc!r}"[:200])
        return

    # ----- packinfo -----------------------------------------------------
    if sub == "packinfo":
        if len(args) < 3:
            await _w(bot, uid, "Usage: !dancefloor packinfo <name>")
            return
        pack_name = " ".join(args[2:]).strip().lower()[:50]
        _ensure_df_tables()
        try:
            conn = db.get_connection()
            row = conn.execute(
                "SELECT pack_name, mode, sequence_json, updated_at "
                "FROM dancefloor_packs WHERE pack_name=?", (pack_name,)
            ).fetchone()
            conn.close()
            if not row:
                await _w(bot, uid, f"Pack '{pack_name}' not found.")
                return
            pseq = _json.loads(row[2]) if row[2] else []
            names  = ", ".join(s["alias"] for s in pseq[:5])
            suffix = f"…+{len(pseq)-5}" if len(pseq) > 5 else ""
            await _w(bot, uid,
                     f"📦 '{row[0]}' mode={row[1]} steps={len(pseq)} "
                     f"updated={row[3]}"[:249])
            if pseq:
                await _w(bot, uid, f"  {names}{suffix}"[:249])
        except Exception as exc:
            await _w(bot, uid, f"DB error: {exc!r}"[:200])
        return

    # ----- renamepack ---------------------------------------------------
    if sub == "renamepack":
        if len(args) < 4:
            await _w(bot, uid, "Usage: !dancefloor renamepack <old> <new>")
            return
        old_name = args[2].strip().lower()[:50]
        new_name = args[3].strip().lower()[:50]
        _ensure_df_tables()
        try:
            conn = db.get_connection()
            res = conn.execute(
                "UPDATE dancefloor_packs SET pack_name=?, "
                "updated_at=datetime('now') WHERE pack_name=?",
                (new_name, old_name)
            )
            changed = res.rowcount > 0
            conn.commit()
            conn.close()
            if changed:
                await _w(bot, uid, f"✏️ Renamed '{old_name}' → '{new_name}'.")
            else:
                await _w(bot, uid, f"Pack '{old_name}' not found.")
        except Exception as exc:
            await _w(bot, uid, f"DB error: {exc!r}"[:200])
        return

    # ----- deletepack ---------------------------------------------------
    if sub == "deletepack":
        if len(args) < 3:
            await _w(bot, uid, "Usage: !dancefloor deletepack <name>")
            return
        pack_name = " ".join(args[2:]).strip().lower()[:50]
        _ensure_df_tables()
        try:
            conn = db.get_connection()
            res = conn.execute(
                "DELETE FROM dancefloor_packs WHERE pack_name=?", (pack_name,)
            )
            changed = res.rowcount > 0
            conn.commit()
            conn.close()
            if changed:
                await _w(bot, uid, f"🗑 Pack '{pack_name}' deleted.")
            else:
                await _w(bot, uid, f"Pack '{pack_name}' not found.")
        except Exception as exc:
            await _w(bot, uid, f"DB error: {exc!r}"[:200])
        return

    # ----- debug --------------------------------------------------------
    if sub == "debug":
        box      = _df_get_box()
        seq      = _df_get_sequence()
        mode     = _df_get_mode()
        active   = _df_is_active()
        poll_run = bool(_df_task.get("_") and not _df_task["_"].done())
        cyc_run  = bool(_df_cycle_task.get("_") and not _df_cycle_task["_"].done())
        inside_list = list(_df_inside)
        box_str = (f"x[{box[0]:.1f}..{box[2]:.1f}] z[{box[1]:.1f}..{box[3]:.1f}]"
                   if box else "unset")
        cur_step  = _df_current_step[0]
        cur_alias = (seq[cur_step]["alias"]
                     if seq and 0 <= cur_step < len(seq) else "(none)")
        await _w(bot, uid,
                 f"🔍 DF: active={active} mode={mode} "
                 f"poll={poll_run} cycle={cyc_run} inside={len(inside_list)}"[:249])
        await _w(bot, uid,
                 f"box={box_str} seq={len(seq)} "
                 f"step={cur_step} emote={cur_alias}"[:249])
        if inside_list:
            await _w(bot, uid, f"Inside: {', '.join(inside_list[:5])}"[:249])
        return

    await _w(bot, uid, f"Unknown dancefloor subcommand: '{sub}'")


async def handle_syncdebug(bot: "BaseBot", user: "User", args: list) -> None:
    """!syncdebug @user — show sync state for a user (staff only)."""
    if not _is_staff(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 2 or not args[1].startswith("@"):
        await _w(bot, user.id, "Usage: !syncdebug @user")
        return
    target_name = args[1].lstrip("@")
    from modules.room_utils import _resolve_user_in_room
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, user.id, f"@{target_name} not in room.")
        return
    target_user, _ = pair
    tid = target_user.id
    leader    = _sync_leader_of.get(tid, "(none)")
    followers = list(_sync_followers.get(tid, set()))
    grp_task  = _sync_group_tasks.get(tid)
    grp_run   = bool(grp_task and not grp_task.done())
    cur_emote = _sync_group_emote.get(tid, "(none)")
    await _w(bot, user.id,
             f"🔍 Sync: @{target_user.username} uid={tid}")
    await _w(bot, user.id,
             f"syncing_to={leader} followers={len(followers)}"[:249])
    await _w(bot, user.id,
             f"group_emote={cur_emote} group_task={grp_run}"[:249])
    if followers:
        await _w(bot, user.id,
                 f"follower IDs: {', '.join(followers[:4])}"[:249])


async def startup_dancefloor_recovery(bot: "BaseBot") -> None:
    """Called from main.on_start — if dancefloor_active=true, resume polling."""
    try:
        has_seq = bool(_df_get_sequence() or _df_get_emotes())
        if _df_is_active() and _df_get_box() and has_seq:
            await asyncio.sleep(3)   # let positions settle
            _ensure_dancefloor_task(bot)
            print("[DANCEFLOOR] resumed after restart")
        else:
            print("[DANCEFLOOR] no active session to resume")
    except Exception as exc:
        print(f"[DANCEFLOOR] recovery err: {exc!r}")


async def handle_dancefloorhelp(bot: "BaseBot", user: "User",
                                 _args: list | None = None) -> None:
    """!dancefloorhelp — staff dancefloor reference."""
    await _w(bot, user.id,
             "💃 DF (staff): setpoint 1|2 → save | emotes <a b c> | "
             "random [N] | timed <a s b s> | savepack|loadpack|packs|"
             "packinfo|renamepack|deletepack | start|stop|status|debug"[:249])


# ===========================================================================
# Section I — Test checklist
# ===========================================================================
_CHECKLIST = [
    "[ ] !sync @user mirrors target",
    "[ ] target changing emote updates sync user",
    "[ ] !syncstop works",
    "[ ] Stop breaks sync",
    "[ ] !slap @user",
    "[ ] !kiss @user",
    "[ ] !superpunch @user",
    "[ ] !bonk @user",
    "[ ] !yeet @user",
    "[ ] !hypnotize @user",
    "[ ] !duel @user",
    "[ ] !kick still moderation kick",
    "[ ] !dancefloor emote random 20",
    "[ ] !dancefloor emotes random 20",
    "[ ] compact emote lists",
    "[ ] persistence after restart",
    "[ ] !customemote justvibing sit aerobics",
    "[ ] !customtimed justvibing 6 hipshake 10 laidback 3",
    "[ ] !stopcustom",
    "[ ] !savecustom chillpack justvibing sit aerobics",
    "[ ] !savecustomtimed vibeloop justvibing 6 hipshake 10 laidback 3",
    "[ ] !playcustom chillpack",
    "[ ] !custompacks",
    "[ ] !custominfo chillpack",
    "[ ] !renamecustom chillpack chillvibes",
    "[ ] !deletecustom chillvibes",
    "[ ] saved packs persist after restart",
]


async def handle_emotetestchecklist(bot: "BaseBot", user: "User",
                                     _args: list | None = None) -> None:
    """!emotetestchecklist — whisper the test checklist in compact pages."""
    # The lines are already short; pack them by sequential whispers.
    buf: list[str] = []
    used = 0
    pages: list[list[str]] = []
    for line in _CHECKLIST:
        add = len(line) + 1  # "\n"
        if buf and used + add > 240:
            pages.append(buf)
            buf = []
            used = 0
            add = len(line)
        buf.append(line)
        used += add
    if buf:
        pages.append(buf)
    total = max(1, len(pages))
    for i, chunk in enumerate(pages, 1):
        await _w(bot, user.id,
                 f"✅ Test {i}/{total}\n" + "\n".join(chunk))
        if i < total:
            await asyncio.sleep(0.4)
