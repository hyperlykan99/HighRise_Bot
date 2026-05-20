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
# Section D + E — TRUE Sync (follower mirrors target's live emote state)
# ===========================================================================
# Follower-uid -> target-uid they're copying
_sync_target:    dict[str, str]            = {}
# Target-uid -> set of follower-uids (reverse index, multiple followers allowed)
_sync_followers: dict[str, set]            = {}
# Follower-uid -> watcher task
_sync_tasks:     dict[str, asyncio.Task]   = {}

_SYNC_POLL_SECS = 0.3


async def _sync_watcher(bot: "BaseBot", follower_uid: str,
                         target_uid: str) -> None:
    """Continuously mirror target's current looping emote on follower.

    - Polls `_player_emotes[target_uid]` every _SYNC_POLL_SECS.
    - When target's emote changes, sends new emote to follower.
    - When target stops, follower stops too.
    - Re-sends current emote on follower at its registry-timed interval to
      keep the loop running. Direct send (not _start_player_loop) so we
      don't spam whispers or pollute `_player_emotes[follower_uid]`.
    """
    from modules.emote_system import _player_emotes, _send_player, get_emote_time
    import time as _time

    cur_eid: str | None = None
    next_resend = 0.0
    try:
        while True:
            # Bail if this follower's sync was cleared elsewhere
            if _sync_target.get(follower_uid) != target_uid:
                return

            target_eid = _player_emotes.get(target_uid)
            now = _time.time()

            if target_eid != cur_eid:
                # Target changed (or stopped)
                cur_eid = target_eid
                if cur_eid:
                    print(f"[SYNC_COPY] follower={follower_uid} "
                          f"target={target_uid} emote={cur_eid}")
                    try:
                        await _send_player(bot, cur_eid, follower_uid)
                    except Exception as exc:
                        print(f"[SYNC] send err {follower_uid}: {exc!r}")
                    next_resend = now + max(0.5, get_emote_time(cur_eid))
                else:
                    next_resend = 0.0
            elif cur_eid and now >= next_resend:
                # Same emote still playing — re-send to keep follower looping
                try:
                    await _send_player(bot, cur_eid, follower_uid)
                except Exception as exc:
                    print(f"[SYNC] resend err {follower_uid}: {exc!r}")
                next_resend = now + max(0.5, get_emote_time(cur_eid))

            await asyncio.sleep(_SYNC_POLL_SECS)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[SYNC] watcher err follower={follower_uid} "
              f"target={target_uid}: {exc!r}")


def _clear_sync_follower(follower_uid: str) -> str | None:
    """Cancel follower's watcher and drop indices. Returns prior target uid."""
    target = _sync_target.pop(follower_uid, None)
    if target:
        followers = _sync_followers.get(target)
        if followers:
            followers.discard(follower_uid)
            if not followers:
                _sync_followers.pop(target, None)
    task = _sync_tasks.pop(follower_uid, None)
    if task and not task.done():
        task.cancel()
    return target


async def handle_sync(bot: "BaseBot", user: "User", args: list) -> None:
    """!sync @user — start mirroring target's live emote state."""
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
    target_user, _ = pair

    # Replace any prior sync this follower had
    _clear_sync_follower(user.id)

    # Cancel any solo player loop / dancefloor loop the follower has so the
    # mirror is exclusive (otherwise both systems would fight to send emotes).
    try:
        from modules.emote_system import _cancel_player_loop
        _cancel_player_loop(user.id)
    except Exception:
        pass
    # Remove from dancefloor tracking so dancefloor doesn't restart them.
    _df_inside.discard(user.id)
    _df_user_emote.pop(user.id, None)

    _sync_target[user.id] = target_user.id
    _sync_followers.setdefault(target_user.id, set()).add(user.id)
    _sync_tasks[user.id] = asyncio.create_task(
        _sync_watcher(bot, user.id, target_user.id))

    await _w(bot, user.id,
             f"🔄 Syncing to @{target_user.username}. "
             f"Type 'Stop' or !syncstop to end.")


async def try_sync_shortcut(bot: "BaseBot", user: "User",
                             message: str) -> bool:
    """Bare 'Stop' (any case) from a syncing user ends their sync. Returns True if handled."""
    if not message:
        return False
    if message.strip().lower() == "stop" and is_in_sync(user.id):
        await handle_syncstop(bot, user, [])
        return True
    return False


async def handle_syncstop(bot: "BaseBot", user: "User",
                           _args: list | None = None) -> None:
    """!syncstop — end this user's sync."""
    prior = _clear_sync_follower(user.id)
    if not prior:
        await _w(bot, user.id, "You have no active sync.")
        return
    # Cancel any residual loop on follower (mirrored emote)
    try:
        from modules.emote_system import _cancel_player_loop
        _cancel_player_loop(user.id)
    except Exception:
        pass
    print(f"[SYNC_STOP] follower={user.id}")
    await _w(bot, user.id, "⏹ Sync stopped.")


def is_in_sync(user_id: str) -> bool:
    return user_id in _sync_target


async def sync_push_emote_event(bot: "BaseBot", uid: str,
                                emote_id: str) -> None:
    """Called from main.on_emote — update _player_emotes so the watcher poll
    stays accurate, then immediately push the new emote to any followers.

    This makes sync event-driven (no poll-lag) for both bot-command emotes
    and in-game emote-wheel emotes.
    """
    # Keep the shared dict accurate so the watcher's timing logic is correct.
    try:
        from modules.emote_system import _player_emotes
        _player_emotes[uid] = emote_id
    except Exception:
        pass
    followers = list(_sync_followers.get(uid, set()))
    if not followers:
        return
    print(f"[SYNC_TARGET_EVENT] target={uid} emote={emote_id} "
          f"followers={len(followers)}")
    try:
        from modules.emote_system import _send_player
    except Exception:
        return
    # Filter to valid followers and log before gather
    valid_fids = [f for f in followers if _sync_target.get(f) == uid]
    for f_uid in valid_fids:
        print(f"[SYNC_COPY] follower={f_uid} target={uid} emote={emote_id}")
    if not valid_fids:
        return
    # Send to all followers simultaneously — closest server-side sync possible
    results = await asyncio.gather(
        *[_send_player(bot, emote_id, f) for f in valid_fids],
        return_exceptions=True,
    )
    for f_uid, r in zip(valid_fids, results):
        if isinstance(r, Exception):
            print(f"[SYNC_COPY] send err follower={f_uid}: {r!r}")


def clear_sync_on_leave(user_id: str) -> None:
    """Called from main.on_user_leave — clean up sync state for a leaving user.

    - If they were a follower: cancel their watcher + drop indices.
    - If they were a target: cancel every follower's watcher and drop indices.
    """
    # As follower
    _clear_sync_follower(user_id)
    # As target — copy follower set since _clear_sync_follower mutates it
    followers = list(_sync_followers.get(user_id, set()))
    for f_uid in followers:
        _clear_sync_follower(f_uid)
    _sync_followers.pop(user_id, None)


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
_df_task: dict[str, asyncio.Task] = {}
_df_inside: set[str] = set()                   # user_ids currently inside
_df_user_emote: dict[str, str] = {}            # uid -> current emote alias
_df_player_tasks: dict[str, asyncio.Task] = {} # uid -> per-player cycle task


async def _df_player_cycle(bot: "BaseBot", uid: str) -> None:
    """Cycle through the full dancefloor emote pool for one player.

    Each lap:
      - Re-reads the saved pool (picks up live config changes).
      - Shuffles into random order; avoids starting on the same emote as the
        previous lap's last emote.
      - Sends each emote in sequence; waits for its registry-timed duration.
    """
    from modules.emote_system import _send_player, get_emote_time
    import random as _rnd

    prev_alias: str | None = None
    try:
        while True:
            emotes = _df_get_emotes()
            valid_pairs: list[tuple[str, str]] = []
            for alias in emotes:
                ent = _reg_get(alias)
                if ent and ent.get("id") and ent.get("player"):
                    valid_pairs.append((alias, ent["id"]))

            if not valid_pairs:
                await asyncio.sleep(2.0)
                continue

            # Shuffle; move prev_alias to end to avoid immediate repeat
            _rnd.shuffle(valid_pairs)
            if len(valid_pairs) > 1 and valid_pairs[0][0] == prev_alias:
                valid_pairs.append(valid_pairs.pop(0))

            for alias, eid in valid_pairs:
                # Check task is still wanted (player may have left mid-lap)
                if uid not in _df_inside:
                    return
                duration = max(0.5, get_emote_time(eid))
                print(f"[DANCEFLOOR_CYCLE] alias={alias} eid={eid} "
                      f"time={duration:.1f} players={len(_df_inside)}")
                try:
                    await _send_player(bot, eid, uid)
                except Exception as exc:
                    print(f"[DANCEFLOOR_CYCLE] send err uid={uid}: {exc!r}")
                prev_alias = alias
                await asyncio.sleep(duration)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[DANCEFLOOR_CYCLE] err uid={uid}: {exc!r}")


def _df_cancel_player(uid: str) -> None:
    """Cancel this player's dancefloor cycle task."""
    t = _df_player_tasks.pop(uid, None)
    if t and not t.done():
        t.cancel()
    _df_user_emote.pop(uid, None)


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
    """Poll positions every _DF_POLL_SECS; start/stop per-player cycle tasks."""
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
                current_inside.add(u.id)
                if u.id not in _df_inside:
                    # New entrant — skip if they are currently syncing
                    if is_in_sync(u.id):
                        print(f"[DANCEFLOOR_SKIP_SYNC] user={u.id}")
                        continue
                    # Entered — launch cycle task (picks emotes itself each lap)
                    print(f"[DANCEFLOOR_ENTER] user={u.id}")
                    t = _df_player_tasks.get(u.id)
                    if not t or t.done():
                        _df_player_tasks[u.id] = asyncio.create_task(
                            _df_player_cycle(bot, u.id))

            print(f"[DANCEFLOOR_TICK] users={len(users)} "
                  f"inside={len(current_inside)} active=true")

            # Stop cycle tasks for those who left
            for left_uid in (_df_inside - current_inside):
                print(f"[DANCEFLOOR_EXIT] user={left_uid}")
                _df_cancel_player(left_uid)

            _df_inside.clear()
            _df_inside.update(current_inside)
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
    t = _df_task.pop("_", None)
    if t and not t.done():
        t.cancel()
    # Cancel all per-player cycle tasks
    for uid in list(_df_inside):
        _df_cancel_player(uid)
    _df_inside.clear()
    _df_user_emote.clear()
    _df_player_tasks.clear()


async def handle_dancefloor(bot: "BaseBot", user: "User", args: list) -> None:
    """!dancefloor <subcommand>"""
    uid   = user.id
    uname = user.username
    if not _is_staff(uname):
        await _w(bot, uid, "Staff only.")
        return
    if len(args) < 2:
        await _w(bot, uid,
                 "Usage: !dancefloor setpoint 1|2 | save | "
                 "emotes <a,b,c|random N> | start | stop | status | clear")
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

    # ----- emotes / emote (alias) ---------------------------------------
    if sub in ("emotes", "emote"):
        rest = args[2:]
        if not rest:
            cur = _df_get_emotes()
            await _w(bot, uid, f"🎵 Dancefloor emotes: {', '.join(cur) if cur else '(none)'}")
            return
        if rest[0].lower() == "random" and len(rest) >= 2:
            try:
                n = max(1, min(50, int(rest[1])))
            except Exception:
                await _w(bot, uid, "Usage: !dancefloor emotes random <N>")
                return
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
            # Dedup case-insensitively + drop socials
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
            picks = random.sample(pool, min(n, len(pool)))
            db.set_room_setting("dancefloor_emotes", ",".join(picks))
            await _w(bot, uid, f"🎲 Random {len(picks)} emotes saved.")
            return
        # Explicit comma list (rejoin args, then split on commas)
        joined = " ".join(rest)
        aliases = [s.strip() for s in joined.split(",") if s.strip()]
        valid: list[str] = []
        bad:   list[str] = []
        for a in aliases:
            ent = _reg_get(a)
            if ent and ent.get("player"):
                valid.append(ent.get("name") or a)
            else:
                bad.append(a)
        if not valid:
            await _w(bot, uid, f"No valid player emotes. Rejected: {', '.join(bad)[:200]}")
            return
        db.set_room_setting("dancefloor_emotes", ",".join(valid))
        msg = f"🎵 Saved {len(valid)} emotes."
        if bad:
            msg += f" Rejected: {', '.join(bad)[:150]}"
        await _w(bot, uid, msg)
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

    # ----- debug --------------------------------------------------------
    if sub == "debug":
        box = _df_get_box()
        emotes = _df_get_emotes()
        active = _df_is_active()
        task = _df_task.get("_")
        task_running = bool(task and not task.done())
        inside_list = list(_df_inside)
        box_str = (f"x[{box[0]:.1f}..{box[2]:.1f}] z[{box[1]:.1f}..{box[3]:.1f}]"
                   if box else "unset")
        await _w(bot, uid,
                 f"🔍 DF: active={active} task={task_running} box={box_str} "
                 f"emotes={len(emotes)} inside={len(inside_list)}")
        if inside_list:
            await _w(bot, uid, f"Inside IDs: {', '.join(inside_list[:5])}"[:249])
        return

    await _w(bot, uid, f"Unknown dancefloor subcommand: {sub}")


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
    from modules.emote_system import _player_emotes
    syncing_to = _sync_target.get(tid, "(none)")
    followers  = list(_sync_followers.get(tid, set()))
    tracked    = _player_emotes.get(tid, "(none)")
    t_task     = _sync_tasks.get(tid)
    watcher    = bool(t_task and not t_task.done())
    await _w(bot, user.id,
             f"🔍 Sync: @{target_user.username} uid={tid}")
    await _w(bot, user.id,
             f"syncing_to={syncing_to} followers={len(followers)}"[:249])
    await _w(bot, user.id,
             f"tracked_emote={tracked} watcher={watcher}"[:249])
    if followers:
        await _w(bot, user.id,
                 f"follower IDs: {', '.join(followers[:4])}"[:249])


async def startup_dancefloor_recovery(bot: "BaseBot") -> None:
    """Called from main.on_start — if dancefloor_active=true, resume polling."""
    try:
        if _df_is_active() and _df_get_box() and _df_get_emotes():
            await asyncio.sleep(3)   # let positions settle
            _ensure_dancefloor_task(bot)
            print("[DANCEFLOOR] resumed after restart")
        else:
            print("[DANCEFLOOR] no active session to resume")
    except Exception as exc:
        print(f"[DANCEFLOOR] recovery err: {exc!r}")


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
