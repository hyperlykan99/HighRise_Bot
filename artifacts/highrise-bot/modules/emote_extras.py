"""modules/emote_extras.py
=================================
Compact emote lists + social target emotes (VIP+) + sync emotes + staff
room-wide emote + per-user favorite emotes + dancefloor + test checklist.

Sections:
  A) Compact emote lists           — handle_emote_list_compact / handle_botemotes_compact
  B) Socials menu                  — handle_emotes_socials
  C) VIP+ social target emotes     — handle_kick_social / handle_kiss_social / handle_slap_social
  D) Sync emotes                   — handle_sync / try_sync_shortcut
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
    """!emotes socials — list VIP+ social target commands."""
    msg = ("💞 Social Emotes (VIP+ for targeted)\n"
           "!kick @user, !kiss @user, !punch @user, !slap @user, !swordfight @user\n"
           "Plain: kick, kiss, slap, punch, swordfight")
    await _w(bot, user.id, msg)


# ===========================================================================
# Section C — VIP+ social target emotes
# ===========================================================================
# (attacker emote alias, target reaction emote alias, broadcast template)
_SOCIAL_TARGETS: dict[str, tuple[str, str, str]] = {
    "kick":  ("kick",  "embarrassed",  "🦵 {a} kicked {b}!"),
    "kiss":  ("kiss",  "cute",         "😘 {a} kissed {b}!"),
    "slap":  ("slap",  "disappointed", "👋 {a} slapped {b}!"),
}

_TARGET_SOCIAL_CD: dict[str, float] = {}
_TARGET_SOCIAL_CD_SECS = 8


async def _do_target_social(bot: "BaseBot", user: "User",
                             args: list, kind: str) -> None:
    """Common path for !kick @u / !kiss @u / !slap @u — VIP+ gated."""
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

    attacker_alias, target_alias, template = _SOCIAL_TARGETS[kind]
    a_entry = _reg_get(attacker_alias)
    t_entry = _reg_get(target_alias)
    a_eid = (a_entry or {}).get("id")
    t_eid = (t_entry or {}).get("id")

    if not a_eid:
        await _w(bot, uid, f"Emote '{attacker_alias}' not in registry.")
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


async def handle_kick_social(bot, user, args):
    await _do_target_social(bot, user, args, "kick")


async def handle_kiss_social(bot, user, args):
    await _do_target_social(bot, user, args, "kiss")


async def handle_slap_social(bot, user, args):
    await _do_target_social(bot, user, args, "slap")


# ===========================================================================
# Section D + E — Sync emotes / Sync stop
# ===========================================================================
# user_id -> partner_user_id  (mirrored: both directions stored)
_sync_partners: dict[str, str] = {}
# Frozenset({uid_a, uid_b}) -> asyncio.Task (one task drives the pair)
_sync_tasks:    dict[frozenset, asyncio.Task] = {}


def _pair_key(a: str, b: str) -> frozenset:
    return frozenset({a, b})


async def _sync_loop(bot: "BaseBot", uid_a: str, uid_b: str, eid: str) -> None:
    """Loop the same emote on two users simultaneously, sleeping registry-timed."""
    from modules.emote_system import get_emote_time
    while True:
        try:
            await asyncio.gather(
                bot.highrise.send_emote(eid, uid_a),
                bot.highrise.send_emote(eid, uid_b),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[SYNC] send err: {exc!r}")
        await asyncio.sleep(get_emote_time(eid))


def _clear_sync_for(uid: str) -> str | None:
    """Cancel sync task involving uid; return partner uid (if any)."""
    partner = _sync_partners.pop(uid, None)
    if not partner:
        return None
    _sync_partners.pop(partner, None)
    task = _sync_tasks.pop(_pair_key(uid, partner), None)
    if task and not task.done():
        task.cancel()
    return partner


async def _start_sync(bot: "BaseBot", sender: "User", target_user: "User",
                      emote_alias: str) -> bool:
    """Resolve emote, validate player-usable, start shared sync loop."""
    entry = _reg_get(emote_alias)
    if not entry:
        await _w(bot, sender.id, f"Unknown emote '{emote_alias}'.")
        return False
    if not entry.get("player"):
        await _w(bot, sender.id, f"'{emote_alias}' is not a player emote.")
        return False
    eid = entry["id"]

    # Cancel any existing sync on either side
    _clear_sync_for(sender.id)
    _clear_sync_for(target_user.id)

    # Also cancel any solo player loop they have running
    try:
        from modules.emote_system import _cancel_player_loop
        _cancel_player_loop(sender.id)
        _cancel_player_loop(target_user.id)
    except Exception:
        pass

    _sync_partners[sender.id]      = target_user.id
    _sync_partners[target_user.id] = sender.id

    task = asyncio.create_task(_sync_loop(bot, sender.id, target_user.id, eid))
    _sync_tasks[_pair_key(sender.id, target_user.id)] = task

    await _w(bot, sender.id,
             f"🔄 Synced with @{target_user.username} on '{emote_alias}'. "
             f"!syncstop to end.")
    try:
        await bot.highrise.send_whisper(
            target_user.id,
            f"🔄 @{sender.username} synced you to '{emote_alias}'. "
            f"!syncstop or 'Stop' to end."[:249])
    except Exception:
        pass
    return True


async def handle_sync(bot: "BaseBot", user: "User", args: list) -> None:
    """!sync @user <emote>"""
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !sync @user <emote>")
        return
    target_name = args[1].lstrip("@")
    emote_alias = args[2]
    if target_name.lower() == user.username.lower():
        await _w(bot, user.id, "You can't sync with yourself.")
        return
    from modules.room_utils import _resolve_user_in_room
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        await _w(bot, user.id, f"@{target_name} is not in the room.")
        return
    target_user, _ = pair
    await _start_sync(bot, user, target_user, emote_alias)


async def try_sync_shortcut(bot: "BaseBot", user: "User",
                             message: str) -> bool:
    """Handles two chat shortcuts. Returns True if handled.
       1) bare `Stop` (any case) from a user in an active sync → !syncstop
       2) `<emote> @user`                                       → !sync @user <emote>"""
    if not message:
        return False
    stripped = message.strip()

    # 1) Bare "Stop" — only acts when this user is actually in a sync,
    #    so we don't intercept casual chat from non-synced users.
    if stripped.lower() == "stop" and is_in_sync(user.id):
        await handle_syncstop(bot, user, [])
        return True

    # 2) <emote> @user
    if "@" not in stripped:
        return False
    parts = stripped.split()
    if len(parts) != 2:
        return False
    word, mention = parts[0], parts[1]
    if not mention.startswith("@"):
        return False
    entry = _reg_get(word)
    if not entry or not entry.get("player"):
        return False
    target_name = mention.lstrip("@")
    if target_name.lower() == user.username.lower():
        return False
    from modules.room_utils import _resolve_user_in_room
    pair = await _resolve_user_in_room(bot, target_name)
    if not pair:
        return False
    target_user, _ = pair
    await _start_sync(bot, user, target_user, word)
    return True


async def handle_syncstop(bot: "BaseBot", user: "User",
                           _args: list | None = None) -> None:
    """!syncstop — stop sender's sync AND linked target's sync."""
    partner = _clear_sync_for(user.id)
    if not partner:
        await _w(bot, user.id, "You have no active sync.")
        return
    # Also stop solo player loops on both sides (in case they leaked)
    try:
        from modules.emote_system import _cancel_player_loop
        _cancel_player_loop(user.id)
        _cancel_player_loop(partner)
    except Exception:
        pass
    await _w(bot, user.id, "⏹ Sync stopped for both users.")
    try:
        await bot.highrise.send_whisper(partner, "⏹ Sync stopped.")
    except Exception:
        pass


def is_in_sync(user_id: str) -> bool:
    return user_id in _sync_partners


# ===========================================================================
# Section F — Staff room-wide emote
# ===========================================================================
_room_all_task: dict[str, asyncio.Task] = {}   # single-entry: {"_": Task}
_room_all_eid:  dict[str, str] = {}            # {"_": eid}


async def _room_all_loop(bot: "BaseBot", eid: str) -> None:
    """Continuously loop `eid` on every non-bot user in the room."""
    from modules.emote_system import get_emote_time
    from modules.room_utils import _user_positions
    from modules.live_bot_registry import live_bot_keys
    while True:
        try:
            try:
                resp = await bot.highrise.get_room_users()
                users = list(resp.content) if hasattr(resp, "content") else []
            except Exception:
                users = []
            bot_names = {str(n).lower() for n in (live_bot_keys() or [])}
            for u, _pos in users:
                if u.username.lower() in bot_names:
                    continue
                try:
                    await bot.highrise.send_emote(eid, u.id)
                except Exception:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[ROOM_ALL] iter err: {exc!r}")
        await asyncio.sleep(get_emote_time(eid))


async def handle_emote_all(bot: "BaseBot", user: "User", args: list) -> None:
    """!emote all <emote>   |   !emote all stop  (staff only)"""
    if not _is_staff(user.username):
        await _w(bot, user.id, "Staff only.")
        return
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !emote all <emote>  |  !emote all stop")
        return
    sub = args[2].lower()
    if sub == "stop":
        task = _room_all_task.pop("_", None)
        if task and not task.done():
            task.cancel()
        _room_all_eid.pop("_", None)
        await _w(bot, user.id, "⏹ Room-wide emote stopped.")
        return
    entry = _reg_get(sub)
    if not entry:
        await _w(bot, user.id, f"Unknown emote '{sub}'.")
        return
    eid = entry["id"]
    # Cancel any existing
    old = _room_all_task.pop("_", None)
    if old and not old.done():
        old.cancel()
    _room_all_task["_"] = asyncio.create_task(_room_all_loop(bot, eid))
    _room_all_eid["_"]  = eid
    await _w(bot, user.id, f"🎭 Looping '{sub}' on the whole room. !emote all stop")


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

_DF_POLL_SECS = 1.5
_df_task: dict[str, asyncio.Task] = {}
_df_inside: set[str] = set()                   # user_ids currently inside
_df_user_emote: dict[str, str] = {}            # uid -> current emote alias


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
    """Poll positions every _DF_POLL_SECS; toggle player loops on box entry/exit."""
    from modules.emote_system import _start_player_loop, _cancel_player_loop
    from modules.live_bot_registry import live_bot_keys
    print("[DANCEFLOOR] loop started")
    while True:
        try:
            if not _df_is_active():
                await asyncio.sleep(_DF_POLL_SECS)
                continue
            box = _df_get_box()
            emotes = _df_get_emotes()
            if not box or not emotes:
                await asyncio.sleep(_DF_POLL_SECS)
                continue

            # Resolve emote IDs once per tick
            valid_pairs: list[tuple[str, str]] = []   # (alias, eid)
            for alias in emotes:
                ent = _reg_get(alias)
                if ent and ent.get("id") and ent.get("player"):
                    valid_pairs.append((alias, ent["id"]))
            if not valid_pairs:
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
                    # Entered — pick an emote and start loop
                    alias, eid = random.choice(valid_pairs)
                    _df_user_emote[u.id] = alias
                    try:
                        await _start_player_loop(
                            bot, u.id, eid, alias,
                            username=u.username, log_event="dancefloor_enter",
                        )
                    except Exception as exc:
                        print(f"[DANCEFLOOR] start err {u.id}: {exc!r}")

            # Stop loops for those who left
            for left_uid in (_df_inside - current_inside):
                try:
                    _cancel_player_loop(left_uid)
                except Exception:
                    pass
                _df_user_emote.pop(left_uid, None)

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
    # Also stop any per-user dancefloor loops still running
    from modules.emote_system import _cancel_player_loop
    for uid in list(_df_inside):
        try:
            _cancel_player_loop(uid)
        except Exception:
            pass
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

    # ----- emotes -------------------------------------------------------
    if sub == "emotes":
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
            pool = _reg_player_aliases()
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

    await _w(bot, uid, f"Unknown dancefloor subcommand: {sub}")


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
    "[ ] !emotes compact list",
    "[ ] !botemotes compact list",
    "[ ] !emotes socials",
    "[ ] VIP can !slap @user",
    "[ ] non-VIP blocked from !slap @user",
    "[ ] !kiss @user",
    "[ ] !kick @user",
    "[ ] !sync @user sweetjammer",
    "[ ] sweetjammer @user shortcut",
    "[ ] !syncstop stops both users",
    "[ ] !emote all sweetjammer",
    "[ ] !emote all stop",
    "[ ] !favemote add sweetjammer",
    "[ ] !favemotes persists",
    "[ ] dancefloor points save",
    "[ ] dancefloor random 10 works",
    "[ ] dancefloor starts/stops",
    "[ ] dancefloor persists after restart",
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
