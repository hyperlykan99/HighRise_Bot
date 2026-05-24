"""modules/custom_emotes.py
===========================
Custom emote sequence loops and saved personal packs — with full persistence
and restart recovery.

Sections:
  J1)  !customemote  <e1> <e2> ...             — instant simple loop
  J2)  !customtimed  <e1> <s1> <e2> <s2> ...  — instant timed loop
  J3)  !stopcustom                              — stop custom loop (permanent)
  J4)  !savecustom   <name> <e1> <e2> ...      — save simple pack
  J5)  !savecustomtimed <name> <e1> <s1> ...   — save timed pack
  J6)  !playcustom   <name>                    — play saved pack
  J7)  !custompacks                             — list saved packs
  J8)  !custominfo   <name>                    — show pack details
  J9)  !renamecustom <old> <new>               — rename pack
  J10) !deletecustom <name>                    — delete pack
  J11) !customdebug  [@user]                   — show loop state

Lifecycle:
  - Start        → save session to DB (is_active=1)
  - Each step    → update current_step in DB
  - User leaves  → cancel task only; keep DB active (non-permanent)
  - User rejoins → resume from saved step automatically
  - Bot restart  → startup_custom_loop_recovery() reloads all active sessions
  - Stop*        → cancel task + set is_active=0 (permanent)

Permanent stops:
  stop_custom_permanent(uid, reason) must be called for:
    - bare "Stop" in chat
    - !stopcustom command
    - !sync @leader (sync takes ownership)
    - dancefloor entry (floor takes ownership)

Engine rules:
  - Bare "Stop" / !stopcustom → permanent stop
  - Movement / position updates → NO effect on custom loop
  - !syncstop → does NOT stop custom loop
  - Sync followers mirror every step automatically
"""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import database as db
from modules.emote_targeting import UPGRADED_ROOM_EMOTE_NOTICE, log_emote_command_received

if TYPE_CHECKING:
    from highrise import BaseBot
    from highrise.models import User


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MAX_PACKS  = 20
_MAX_STEPS  = 20


# ---------------------------------------------------------------------------
# DB bootstrap — lazy CREATE TABLE IF NOT EXISTS (both tables at once)
# ---------------------------------------------------------------------------
_DB_READY = False


def _ensure_custom_tables() -> None:
    global _DB_READY
    if _DB_READY:
        return
    try:
        conn = db.get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_emote_packs (
                user_id       TEXT NOT NULL,
                pack_name     TEXT NOT NULL,
                mode          TEXT NOT NULL DEFAULT 'simple',
                sequence_json TEXT NOT NULL DEFAULT '[]',
                created_at    TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (user_id, pack_name)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_loop_sessions (
                user_id       TEXT PRIMARY KEY,
                username      TEXT NOT NULL DEFAULT '',
                mode          TEXT NOT NULL DEFAULT 'simple',
                sequence_json TEXT NOT NULL DEFAULT '[]',
                current_step  INTEGER NOT NULL DEFAULT 0,
                started_at    TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
                is_active     INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.commit()
        conn.close()
        _DB_READY = True
    except Exception as exc:
        print(f"[CUSTOM_EMOTE] DB init err: {exc!r}")


# ---------------------------------------------------------------------------
# Whisper helper
# ---------------------------------------------------------------------------
async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Active loop state  (in-memory)
# ---------------------------------------------------------------------------
_custom_loops: dict[str, asyncio.Task] = {}


def is_in_custom_loop(uid: str) -> bool:
    """True if uid has an active custom emote loop task running in memory."""
    t = _custom_loops.get(uid)
    return t is not None and not t.done()


def cancel_custom_loop(uid: str) -> bool:
    """Cancel the in-memory task only (non-permanent — DB stays active).
    Use stop_custom_permanent() for user-initiated stops."""
    task = _custom_loops.pop(uid, None)
    if task and not task.done():
        task.cancel()
        return True
    _custom_loops.pop(uid, None)
    return False


def stop_custom_permanent(uid: str, reason: str = "user") -> bool:
    """Cancel task AND mark session inactive in DB.
    Call this for Stop / !stopcustom / !sync / dancefloor-takeover."""
    stopped = cancel_custom_loop(uid)
    _deactivate_session(uid)
    print(f"[CUSTOM_LOOP_CANCEL] user={uid} reason={reason}")
    return stopped


# ---------------------------------------------------------------------------
# Session DB helpers
# ---------------------------------------------------------------------------
def _save_session(uid: str, username: str, mode: str,
                  steps: list[tuple[str, str, float]],
                  step_offset: int = 0) -> None:
    _ensure_custom_tables()
    seq = [{"alias": a, "eid": e, "seconds": s} for a, e, s in steps]
    try:
        conn = db.get_connection()
        conn.execute("""
            INSERT INTO custom_loop_sessions
                (user_id, username, mode, sequence_json,
                 current_step, started_at, updated_at, is_active)
            VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'), 1)
            ON CONFLICT(user_id) DO UPDATE SET
                username      = excluded.username,
                mode          = excluded.mode,
                sequence_json = excluded.sequence_json,
                current_step  = excluded.current_step,
                started_at    = datetime('now'),
                updated_at    = datetime('now'),
                is_active     = 1
        """, (uid, username, mode, json.dumps(seq), step_offset))
        conn.commit()
        conn.close()
        print(f"[CUSTOM_LOOP_SAVE] user={uid} mode={mode} step={step_offset}")
    except Exception as exc:
        print(f"[CUSTOM_LOOP_SAVE] err: {exc!r}")


def _update_session_step(uid: str, step: int) -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            "UPDATE custom_loop_sessions "
            "SET current_step=?, updated_at=datetime('now') "
            "WHERE user_id=? AND is_active=1",
            (step, uid)
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[CUSTOM_LOOP_STEP_DB] err: {exc!r}")


def _deactivate_session(uid: str) -> None:
    _ensure_custom_tables()
    try:
        conn = db.get_connection()
        conn.execute(
            "UPDATE custom_loop_sessions SET is_active=0, "
            "updated_at=datetime('now') WHERE user_id=?",
            (uid,)
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[CUSTOM_LOOP_DEACTIVATE] err: {exc!r}")


def _get_active_session(uid: str) -> dict | None:
    _ensure_custom_tables()
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT username, mode, sequence_json, current_step "
            "FROM custom_loop_sessions "
            "WHERE user_id=? AND is_active=1",
            (uid,)
        ).fetchone()
        conn.close()
        if not row:
            return None
        return {
            "username": row[0], "mode": row[1],
            "sequence": json.loads(row[2]), "current_step": row[3],
        }
    except Exception as exc:
        print(f"[CUSTOM_LOOP_GET] err: {exc!r}")
        return None


def _get_all_active_sessions() -> list[dict]:
    _ensure_custom_tables()
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT user_id, username, mode, sequence_json, current_step "
            "FROM custom_loop_sessions WHERE is_active=1"
        ).fetchall()
        conn.close()
        return [
            {"user_id": r[0], "username": r[1], "mode": r[2],
             "sequence": json.loads(r[3]), "current_step": r[4]}
            for r in rows
        ]
    except Exception as exc:
        print(f"[CUSTOM_LOOP_GET_ALL] err: {exc!r}")
        return []


# ---------------------------------------------------------------------------
# Core loop runner
# ---------------------------------------------------------------------------
async def _run_custom_loop(
    bot: "BaseBot",
    uid: str,
    steps: list[tuple[str, str, float]],
    step_offset: int = 0,
) -> None:
    """Loop steps forever, starting at step_offset, until cancelled.

    - Updates DB current_step each iteration.
    - Fans out to sync followers on every send.
    - Survives individual send errors without dying.
    - Logs [CUSTOM_LOOP_ALIVE] once per full cycle.
    """
    from modules.emote_system import _send_player
    from modules.emote_extras import _sync_followers, _sync_leader_of

    n = len(steps)
    cycle = 0
    idx = step_offset % n if n else 0

    try:
        while True:
            alias, eid, duration = steps[idx]

            # Collect valid sync followers
            followers = [
                f for f in _sync_followers.get(uid, set())
                if _sync_leader_of.get(f) == uid
            ]
            targets = [uid] + followers

            print(f"[CUSTOM_EMOTE_STEP] user={uid} alias={alias} "
                  f"eid={eid} time={duration:.1f} followers={len(followers)}")

            try:
                await asyncio.gather(
                    *[_send_player(bot, eid, t) for t in targets],
                    return_exceptions=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[CUSTOM_EMOTE_STEP] send err: {exc!r}")

            # Advance step; log alive once per full cycle
            idx = (idx + 1) % n
            if idx == 0:
                cycle += 1
                print(f"[CUSTOM_LOOP_ALIVE] user={uid} cycle={cycle} "
                      f"step={idx}")

            # Persist current step (fire-and-forget — don't let DB errors kill loop)
            try:
                _update_session_step(uid, idx)
            except Exception:
                pass

            try:
                await asyncio.sleep(max(0.5, duration))
            except asyncio.CancelledError:
                raise

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Log but don't die — task should only stop when explicitly cancelled
        print(f"[CUSTOM_LOOP_ERR] user={uid} {exc!r}")


async def _start_custom_loop(
    bot: "BaseBot",
    uid: str,
    steps: list[tuple[str, str, float]],
    mode: str,
    username: str = "",
    step_offset: int = 0,
) -> None:
    """Save to DB, cancel prior loops, and start the custom emote task."""
    from modules.emote_system import _cancel_player_loop
    _cancel_player_loop(uid)
    cancel_custom_loop(uid)
    _save_session(uid, username, mode, steps, step_offset)
    print(f"[CUSTOM_EMOTE_START] user={uid} mode={mode} count={len(steps)} "
          f"from_step={step_offset}")
    task = asyncio.create_task(
        _run_custom_loop(bot, uid, steps, step_offset))
    _custom_loops[uid] = task


# ---------------------------------------------------------------------------
# Lifecycle hooks — called from main.py
# ---------------------------------------------------------------------------
def on_custom_user_leave(uid: str) -> None:
    """User left room — pause in-memory task but keep DB active for resume."""
    if cancel_custom_loop(uid):
        print(f"[CUSTOM_LOOP_PAUSE_OFFLINE] user={uid}")


async def on_custom_user_join(bot: "BaseBot", user: "User") -> None:
    """User rejoined — clear legacy custom loops that implied avatar control."""
    if is_in_custom_loop(user.id):
        return  # already running (same session)
    sess = _get_active_session(user.id)
    if not sess:
        return
    stop_custom_permanent(user.id, "upgraded_room_no_force_emotes")
    await _w(bot, user.id, UPGRADED_ROOM_EMOTE_NOTICE)
    print(f"[CUSTOM_LOOP_RESUME_JOIN] user={user.id} disabled=upgraded_room")


async def startup_custom_loop_recovery(bot: "BaseBot") -> None:
    """On bot restart: deactivate legacy custom loops that implied avatar control."""
    await asyncio.sleep(6)  # let room stabilise after startup
    sessions = _get_all_active_sessions()
    if not sessions:
        return
    for sess in sessions:
        uid = sess["user_id"]
        if is_in_custom_loop(uid):
            continue  # already running
        stop_custom_permanent(uid, "upgraded_room_startup_no_force_emotes")
        print(f"[CUSTOM_LOOP_RECOVER] user={uid} disabled=upgraded_room")


# ---------------------------------------------------------------------------
# Emote resolution helpers
# ---------------------------------------------------------------------------
def _resolve_emote(alias: str) -> tuple[str, str] | None:
    """Return (alias, eid) or None if not a valid player emote."""
    try:
        from data import emote_registry as _r
        ent = _r.get_emote(alias)
    except Exception:
        return None
    if ent and ent.get("id") and ent.get("player"):
        return (alias, ent["id"])
    return None


def _steps_from_seq(
    mode: str,
    sequence: list[dict],
) -> tuple[list[tuple[str, str, float]], list[str]]:
    """Convert a stored sequence list into (alias, eid, seconds) steps."""
    from modules.emote_system import get_emote_time
    steps: list[tuple[str, str, float]] = []
    bad: list[str] = []
    for entry in sequence:
        alias = entry.get("alias", "")
        r = _resolve_emote(alias)
        if r is None:
            bad.append(alias)
            continue
        secs = float(entry.get("seconds", 5.0)) if mode == "timed" \
            else get_emote_time(r[1])
        steps.append((r[0], r[1], secs))
    return steps, bad


def _parse_simple_args(
    emote_args: list[str],
) -> tuple[list[tuple[str, str, float]], str]:
    """Parse [emote1, emote2, ...] → steps. Returns (steps, error_msg)."""
    from modules.emote_system import get_emote_time
    steps: list[tuple[str, str, float]] = []
    errors: list[str] = []
    for a in emote_args:
        r = _resolve_emote(a)
        if r is None:
            errors.append(a)
        else:
            steps.append((r[0], r[1], get_emote_time(r[1])))
    if errors:
        return [], f"Unknown emote(s): {', '.join(errors)}"
    return steps, ""


def _parse_timed_args(
    timed_args: list[str],
) -> tuple[list[tuple[str, str, float]], str]:
    """Parse [emote1, sec1, emote2, sec2, ...] → steps. Returns (steps, error_msg)."""
    if len(timed_args) % 2 != 0:
        return [], "Need pairs: emote1 seconds1 emote2 seconds2 ..."
    steps: list[tuple[str, str, float]] = []
    for i in range(0, len(timed_args), 2):
        alias, sec_str = timed_args[i], timed_args[i + 1]
        r = _resolve_emote(alias)
        if r is None:
            return [], f"Unknown emote: {alias}"
        try:
            secs = float(sec_str)
        except ValueError:
            return [], f"Invalid seconds: {sec_str!r}"
        if secs <= 0:
            return [], f"Seconds must be > 0 (got {secs})"
        steps.append((r[0], r[1], secs))
    return steps, ""


# ---------------------------------------------------------------------------
# Pack DB helpers
# ---------------------------------------------------------------------------
def _save_pack_db(
    uid: str,
    name: str,
    mode: str,
    steps: list[tuple[str, str, float]],
) -> None:
    _ensure_custom_tables()
    seq = [{"alias": a, "eid": e, "seconds": s} for a, e, s in steps]
    conn = db.get_connection()
    conn.execute("""
        INSERT INTO custom_emote_packs
            (user_id, pack_name, mode, sequence_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(user_id, pack_name) DO UPDATE SET
            mode          = excluded.mode,
            sequence_json = excluded.sequence_json,
            updated_at    = datetime('now')
    """, (uid, name, mode, json.dumps(seq)))
    conn.commit()
    conn.close()
    print(f"[CUSTOM_PACK_SAVE] user={uid} pack={name} mode={mode}")


def _load_pack_db(uid: str, name: str) -> dict | None:
    _ensure_custom_tables()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT mode, sequence_json FROM custom_emote_packs "
        "WHERE user_id=? AND pack_name=?",
        (uid, name)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"mode": row[0], "sequence": json.loads(row[1])}


def _list_packs_db(uid: str) -> list[str]:
    _ensure_custom_tables()
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT pack_name FROM custom_emote_packs "
        "WHERE user_id=? ORDER BY pack_name",
        (uid,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _count_packs_db(uid: str) -> int:
    _ensure_custom_tables()
    conn = db.get_connection()
    n = conn.execute(
        "SELECT COUNT(*) FROM custom_emote_packs WHERE user_id=?", (uid,)
    ).fetchone()[0]
    conn.close()
    return n


def _rename_pack_db(uid: str, old: str, new: str) -> str:
    """Rename pack. Returns 'ok', 'not_found', or 'conflict'."""
    _ensure_custom_tables()
    conn = db.get_connection()
    exists = conn.execute(
        "SELECT 1 FROM custom_emote_packs WHERE user_id=? AND pack_name=?",
        (uid, old)
    ).fetchone()
    if not exists:
        conn.close()
        return "not_found"
    conflict = conn.execute(
        "SELECT 1 FROM custom_emote_packs WHERE user_id=? AND pack_name=?",
        (uid, new)
    ).fetchone()
    if conflict:
        conn.close()
        return "conflict"
    conn.execute(
        "UPDATE custom_emote_packs SET pack_name=?, updated_at=datetime('now') "
        "WHERE user_id=? AND pack_name=?",
        (new, uid, old)
    )
    conn.commit()
    conn.close()
    return "ok"


def _delete_pack_db(uid: str, name: str) -> bool:
    _ensure_custom_tables()
    conn = db.get_connection()
    cur = conn.execute(
        "DELETE FROM custom_emote_packs WHERE user_id=? AND pack_name=?",
        (uid, name)
    )
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def _steps_from_pack(pack: dict) -> tuple[list[tuple[str, str, float]], list[str]]:
    """Convert loaded pack dict to steps list. Returns (steps, invalid_aliases)."""
    return _steps_from_seq(pack["mode"], pack["sequence"])


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def handle_customemote(bot: "BaseBot", user: "User", args: list) -> None:
    """!customemote <e1> <e2> ... — start an instant simple loop."""
    log_emote_command_received(
        " ".join(str(a) for a in args), user, "custom_emotes.handle_customemote")
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !customemote <emote1> <emote2> ...")
        return
    emote_args = args[1:]
    if len(emote_args) > _MAX_STEPS:
        await _w(bot, user.id, f"Max {_MAX_STEPS} emotes per sequence.")
        return
    steps, err = _parse_simple_args(emote_args)
    if err:
        await _w(bot, user.id, f"❌ {err}")
        return
    from modules.emote_system import _send_player
    alias, eid, _duration = steps[0]
    await _send_player(bot, eid, user.id, command=f"customemote:{alias}",
                       sender_id=user.id, sender_username=user.username,
                       sender_obj=user)
    await _w(bot, user.id, UPGRADED_ROOM_EMOTE_NOTICE)


async def handle_customtimed(bot: "BaseBot", user: "User", args: list) -> None:
    """!customtimed <e1> <s1> <e2> <s2> ... — start an instant timed loop."""
    log_emote_command_received(
        " ".join(str(a) for a in args), user, "custom_emotes.handle_customtimed")
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: !customtimed <emote1> <secs1> <emote2> <secs2> ...")
        return
    timed_args = args[1:]
    if len(timed_args) > _MAX_STEPS * 2:
        await _w(bot, user.id, f"Max {_MAX_STEPS} emote/second pairs.")
        return
    steps, err = _parse_timed_args(timed_args)
    if err:
        await _w(bot, user.id, f"❌ {err}")
        return
    from modules.emote_system import _send_player
    alias, eid, _duration = steps[0]
    await _send_player(bot, eid, user.id, command=f"customtimed:{alias}",
                       sender_id=user.id, sender_username=user.username,
                       sender_obj=user)
    await _w(bot, user.id, UPGRADED_ROOM_EMOTE_NOTICE)


async def handle_stopcustom(bot: "BaseBot", user: "User",
                             _args: list | None = None) -> None:
    """!stopcustom — permanently stop the user's custom emote loop."""
    had_session = _get_active_session(user.id) is not None
    stopped = stop_custom_permanent(user.id, reason="stopcustom_cmd")
    if stopped or had_session:
        await _w(bot, user.id, "⏹ Custom loop stopped.")
    else:
        await _w(bot, user.id, "No active custom loop.")


async def handle_savecustom(bot: "BaseBot", user: "User", args: list) -> None:
    """!savecustom <name> <e1> <e2> ... — save a simple emote pack."""
    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: !savecustom <packname> <emote1> <emote2> ...")
        return
    pack_name = args[1].lower()
    emote_args = args[2:]
    if len(emote_args) > _MAX_STEPS:
        await _w(bot, user.id, f"Max {_MAX_STEPS} emotes per pack.")
        return
    if _load_pack_db(user.id, pack_name) is None and \
            _count_packs_db(user.id) >= _MAX_PACKS:
        await _w(bot, user.id, f"Max {_MAX_PACKS} packs. !deletecustom one first.")
        return
    steps, err = _parse_simple_args(emote_args)
    if err:
        await _w(bot, user.id, f"❌ {err}")
        return
    _save_pack_db(user.id, pack_name, "simple", steps)
    await _w(bot, user.id, f"✅ Saved pack '{pack_name}' ({len(steps)} emotes).")


async def handle_savecustomtimed(bot: "BaseBot", user: "User",
                                  args: list) -> None:
    """!savecustomtimed <name> <e1> <s1> ... — save a timed emote pack."""
    if len(args) < 4:
        await _w(bot, user.id,
                 "Usage: !savecustomtimed <name> <e1> <s1> <e2> <s2> ...")
        return
    pack_name = args[1].lower()
    timed_args = args[2:]
    if len(timed_args) > _MAX_STEPS * 2:
        await _w(bot, user.id, f"Max {_MAX_STEPS} emote/second pairs.")
        return
    if _load_pack_db(user.id, pack_name) is None and \
            _count_packs_db(user.id) >= _MAX_PACKS:
        await _w(bot, user.id, f"Max {_MAX_PACKS} packs. !deletecustom one first.")
        return
    steps, err = _parse_timed_args(timed_args)
    if err:
        await _w(bot, user.id, f"❌ {err}")
        return
    _save_pack_db(user.id, pack_name, "timed", steps)
    await _w(bot, user.id, f"✅ Saved timed pack '{pack_name}' ({len(steps)} steps).")


async def handle_playcustom(bot: "BaseBot", user: "User", args: list) -> None:
    """!playcustom <name> — loop a saved pack (re-resolves emotes fresh)."""
    log_emote_command_received(
        " ".join(str(a) for a in args), user, "custom_emotes.handle_playcustom")
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !playcustom <packname>")
        return
    pack_name = args[1].lower()
    pack = _load_pack_db(user.id, pack_name)
    if pack is None:
        await _w(bot, user.id, f"Pack '{pack_name}' not found. Use !custompacks")
        return
    steps, bad = _steps_from_pack(pack)
    if bad:
        await _w(bot, user.id,
                 f"⚠️ Emotes no longer valid: {', '.join(bad)}")
        return
    if not steps:
        await _w(bot, user.id, "Pack is empty.")
        return
    print(f"[CUSTOM_PACK_PLAY] user={user.id} pack={pack_name} upgraded_single_send=true")
    from modules.emote_system import _send_player
    alias, eid, _duration = steps[0]
    await _send_player(bot, eid, user.id, command=f"playcustom:{pack_name}:{alias}",
                       sender_id=user.id, sender_username=user.username,
                       sender_obj=user)
    await _w(bot, user.id, UPGRADED_ROOM_EMOTE_NOTICE)


async def handle_custompacks(bot: "BaseBot", user: "User",
                              _args: list | None = None) -> None:
    """!custompacks — list all saved packs alphabetically."""
    packs = _list_packs_db(user.id)
    if not packs:
        await _w(bot, user.id,
                 "No saved packs yet. Use !savecustom to create one.")
        return
    header = f"📦 Packs ({len(packs)}/{_MAX_PACKS}):"
    lines = [f"  • {p}" for p in packs]
    buf = header
    for ln in lines:
        candidate = buf + "\n" + ln
        if len(candidate) > 245:
            await _w(bot, user.id, buf)
            await asyncio.sleep(0.3)
            buf = ln
        else:
            buf = candidate
    await _w(bot, user.id, buf)


async def handle_custominfo(bot: "BaseBot", user: "User", args: list) -> None:
    """!custominfo <name> — show pack name, mode, emotes, and timings."""
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !custominfo <packname>")
        return
    pack_name = args[1].lower()
    pack = _load_pack_db(user.id, pack_name)
    if pack is None:
        await _w(bot, user.id, f"Pack '{pack_name}' not found.")
        return
    mode = pack["mode"]
    seq = pack["sequence"]
    if mode == "timed":
        detail = " → ".join(f"{e['alias']}({e['seconds']}s)" for e in seq)
    else:
        detail = " → ".join(e["alias"] for e in seq)
    msg = (f"📦 {pack_name} | {mode} | {len(seq)} steps\n"
           f"{detail}")
    await _w(bot, user.id, msg[:249])


async def handle_renamecustom(bot: "BaseBot", user: "User", args: list) -> None:
    """!renamecustom <oldname> <newname> — rename a saved pack."""
    if len(args) < 3:
        await _w(bot, user.id, "Usage: !renamecustom <oldname> <newname>")
        return
    old, new = args[1].lower(), args[2].lower()
    if old == new:
        await _w(bot, user.id, "Names are the same.")
        return
    result = _rename_pack_db(user.id, old, new)
    if result == "ok":
        await _w(bot, user.id, f"✅ Renamed '{old}' → '{new}'.")
    elif result == "conflict":
        await _w(bot, user.id, f"Pack '{new}' already exists.")
    else:
        await _w(bot, user.id, f"Pack '{old}' not found.")


async def handle_deletecustom(bot: "BaseBot", user: "User", args: list) -> None:
    """!deletecustom <name> — permanently delete a saved pack."""
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !deletecustom <packname>")
        return
    pack_name = args[1].lower()
    if _delete_pack_db(user.id, pack_name):
        await _w(bot, user.id, f"🗑 Pack '{pack_name}' deleted.")
    else:
        await _w(bot, user.id, f"Pack '{pack_name}' not found.")


async def handle_customdebug(bot: "BaseBot", user: "User",
                              args: list) -> None:
    """!customdebug [@user] — show custom loop state for self or a target (staff)."""
    from modules.permissions import is_admin, is_manager
    target_uid  = user.id
    target_name = user.username

    if len(args) >= 2 and args[1].startswith("@"):
        if not (is_admin(user.username) or is_manager(user.username)):
            await _w(bot, user.id, "Staff only for @user lookup.")
            return
        target_name = args[1].lstrip("@")
        try:
            from modules.room_utils import get_user_id_by_name
            target_uid = get_user_id_by_name(target_name) or target_uid
        except Exception:
            pass

    task_alive = is_in_custom_loop(target_uid)
    sess = _get_active_session(target_uid)

    if not sess and not task_alive:
        await _w(bot, user.id,
                 f"@{target_name}: no active custom loop.")
        return

    db_active  = bool(sess)
    mode       = sess["mode"] if sess else "—"
    step       = sess["current_step"] if sess else "—"
    seq_count  = len(sess["sequence"]) if sess else "—"

    msg = (f"🔁 @{target_name} custom loop\n"
           f"db_active={db_active} task={task_alive}\n"
           f"mode={mode} step={step}/{seq_count}")
    await _w(bot, user.id, msg[:249])
