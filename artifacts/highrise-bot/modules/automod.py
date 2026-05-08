"""
modules/automod.py
In-memory spam detection and auto-moderation for the Highrise Mini Game Bot.

Called from on_chat after the mute gate.
Tracks per-user: command rate, duplicate messages, report spam.
Actions: warn → 5-min mute → 30-min mute (escalating per automod offense count).
Never fires for staff users.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

import database as db
from modules.permissions import can_moderate

# ── Per-user in-memory state ──────────────────────────────────────────────────

class _UserTracker:
    __slots__ = ("cmd_times", "msg_history", "report_times")

    def __init__(self) -> None:
        self.cmd_times:    deque[float] = deque()   # epoch timestamps of any command
        self.msg_history:  deque[str]   = deque(maxlen=5)  # last 5 raw messages
        self.report_times: deque[float] = deque()   # timestamps of /report commands


_trackers: dict[str, _UserTracker] = defaultdict(_UserTracker)

# ── Commands that should not trigger automod (mute-exempt + help) ─────────────
_AUTOMOD_SKIP = {
    "help", "casinohelp", "gamehelp", "coinhelp", "profilehelp",
    "shophelp", "progresshelp", "bankhelp", "staffhelp", "modhelp",
    "managerhelp", "adminhelp", "ownerhelp", "questhelp",
    "profile", "level", "balance", "myitems", "rules",
    "myreports", "bug", "botstatus", "warnings",
    # ── Blackjack / RBJ gameplay ─────────────────────────────────────────────
    # Players send these rapidly during a hand; flagging them as spam would
    # mute active game participants.  All BJ/RBJ action commands are exempt.
    "bj", "rbj",
    "bjoin", "bt", "bh", "bs", "bd", "bsp",
    "blimits", "bstats", "bhand",
    "bjh", "bjs", "bjd", "bjsp", "bjhand",
    "rjoin", "rt", "rh", "rs", "rd", "rsp", "rshoe",
    "rlimits", "rstats", "rhand",
    "rbjh", "rbjs", "rbjd", "rbjsp", "rbjhand",
    "blackjack", "bjbet", "bet", "hit", "stand", "double", "split",
    "insurance", "surrender", "shoe", "bjshoe",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _purge_old(dq: deque[float], window: float) -> None:
    """Remove timestamps older than `window` seconds from the left."""
    cutoff = time.monotonic() - window
    while dq and dq[0] < cutoff:
        dq.popleft()


def _get_settings() -> dict[str, int | str]:
    """Read automod settings from DB (cached per call — fast sqlite query)."""
    try:
        conn = db.get_connection()
        rows = conn.execute("SELECT key, value FROM moderation_settings").fetchall()
        conn.close()
        return {r["key"]: r["value"] for r in rows}
    except Exception:
        return {}


def _automod_offense_count(username: str) -> int:
    """Count how many automod warnings this user has received (ever)."""
    try:
        conn = db.get_connection()
        cnt = conn.execute(
            "SELECT COUNT(*) FROM warnings WHERE LOWER(username) = ? AND warned_by = '__automod__'",
            (username.lower(),),
        ).fetchone()[0]
        conn.close()
        return cnt
    except Exception:
        return 0


async def _take_action(bot, user, reason: str) -> str:
    """
    Escalate: warn → 5-min mute → 30-min mute.
    Returns "warned" | "muted5" | "muted30".
    """
    uname  = user.username.lower()
    uid    = user.id
    _w     = lambda msg: bot.highrise.send_whisper(uid, msg[:249])

    offense = _automod_offense_count(uname)

    if offense == 0:
        db.add_warning(uid, user.username, "__automod__", reason)
        await _w(f"⚠️ AutoMod: {reason}. Further violations will mute you.")
        return "warned"
    elif offense == 1:
        db.add_warning(uid, user.username, "__automod__", reason)
        db.mute_user(uid, user.username, "__automod__", 5)
        await _w(f"🔇 AutoMod muted you 5m. Reason: {reason}")
        return "muted5"
    else:
        db.add_warning(uid, user.username, "__automod__", reason)
        db.mute_user(uid, user.username, "__automod__", 30)
        await _w(f"🔇 AutoMod muted you 30m. Reason: {reason}")
        return "muted30"


# ── Public entry point ────────────────────────────────────────────────────────

async def automod_check(bot, user, cmd: str, message: str) -> bool:
    """
    Run spam/abuse checks for a player command.
    Returns True if the action should be blocked (player was auto-muted).
    Returns False if the command should proceed (ok, or only warned).
    Never fires for staff users.
    """
    try:
        # Skip staff entirely
        if can_moderate(user.username):
            return False

        # Skip exempt commands
        if cmd in _AUTOMOD_SKIP:
            return False

        settings = _get_settings()
        if settings.get("automod_enabled", "1") != "1":
            return False

        max_cmds    = int(settings.get("max_commands", "8"))
        max_same    = int(settings.get("max_same_message", "3"))
        max_reports = int(settings.get("max_reports", "3"))

        tracker = _trackers[user.id]
        now     = time.monotonic()

        # ── Check 1: command rate (max_commands within 30s) ───────────────────
        _purge_old(tracker.cmd_times, 30)
        tracker.cmd_times.append(now)
        if len(tracker.cmd_times) > max_cmds:
            result = await _take_action(bot, user, "Command spam detected")
            return result.startswith("muted")

        # ── Check 2: same message spam (max_same_message within 30s) ─────────
        # We compare by storing (message_lower, timestamp) pairs
        msg_lower = message.lower().strip()
        # Count how many of the last 5 messages are identical
        recent_same = sum(1 for m in tracker.msg_history if m == msg_lower)
        tracker.msg_history.append(msg_lower)
        if recent_same >= max_same - 1:   # -1 because we already appended
            result = await _take_action(bot, user, "Repeated message spam")
            return result.startswith("muted")

        # ── Check 3: report spam (max_reports within 10 min) ─────────────────
        if cmd == "report":
            _purge_old(tracker.report_times, 600)
            tracker.report_times.append(now)
            if len(tracker.report_times) > max_reports:
                result = await _take_action(bot, user, "Report spam detected")
                return result.startswith("muted")

        return False

    except Exception as exc:
        print(f"[AUTOMOD] check error for @{user.username}: {exc!r}")
        return False


# ── Tracker management (called by /unmute and /mutestatus) ───────────────────

def reset_tracker(user_id: str) -> None:
    """Clear all in-memory automod state for a user (e.g. on /unmute)."""
    _trackers.pop(user_id, None)


def get_tracker_status(user_id: str) -> dict:
    """
    Return current in-memory tracker state for a user.
    Used by /mutestatus to show whether the user has recent command activity.
    """
    if user_id not in _trackers:
        return {"cmd_count": 0, "active": False}
    t = _trackers[user_id]
    _purge_old(t.cmd_times, 30)
    count = len(t.cmd_times)
    return {"cmd_count": count, "active": count > 0}


def automod_offense_count(username: str) -> int:
    """Public wrapper for _automod_offense_count (used by /mutestatus)."""
    return _automod_offense_count(username)
