"""
modules/bot_guard.py
--------------------
Single-session protection via per-account lock files.

Prevents multilogin conflicts when the workflow is restarted while a
previous subprocess is still alive, or when two bot.py runs start at
the same time for the same Highrise account.

Lock directory:  runtime_locks/   (beside bot.py)
Lock file:       runtime_locks/<bot_username>.lock
Lock content:    JSON — pid, bot_mode, bot_username, started_at, heartbeat_at

Stale-lock rules (EITHER condition → stale):
  • PID is no longer alive  (checked via os.kill(pid, 0))
  • heartbeat_at is older than STALE_TIMEOUT seconds

Usage in main.py:
    from modules import bot_guard
    if not bot_guard.acquire(bot_username, bot_mode):
        sys.exit(0)          # duplicate — clean exit, not an error
    import atexit
    atexit.register(bot_guard.release, bot_username)
    # inside async context:
    asyncio.create_task(bot_guard.heartbeat_loop(bot_username))
"""
from __future__ import annotations

import asyncio
import datetime
import json
import os
from pathlib import Path

_LOG          = "[BOT_GUARD]"
_HERE         = Path(__file__).parent.parent   # artifacts/highrise-bot/
_LOCK_DIR     = _HERE / "runtime_locks"
_STALE_SECS   = 120   # heartbeat older than this → stale
_HB_INTERVAL  = 30    # write heartbeat every N seconds


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _lock_path(bot_username: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in bot_username)
    return _LOCK_DIR / f"{safe}.lock"


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    """True if the process exists (doesn't check zombie state)."""
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _read_lock(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _write_lock(path: Path, data: dict) -> None:
    _LOCK_DIR.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)   # atomic rename


def _is_stale(lock_data: dict) -> bool:
    """Return True if the lock is no longer valid."""
    pid = lock_data.get("pid")
    if pid and not _pid_alive(int(pid)):
        return True
    try:
        hb_str = lock_data.get("heartbeat_at") or ""
        hb = datetime.datetime.fromisoformat(hb_str)
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=datetime.timezone.utc)
        age = (datetime.datetime.now(datetime.timezone.utc) - hb).total_seconds()
        return age > _STALE_SECS
    except Exception:
        return True   # unparseable → treat as stale


# ─── Public API ───────────────────────────────────────────────────────────────

def acquire(bot_username: str, bot_mode: str) -> bool:
    """
    Attempt to acquire the session lock for this bot account.

    Returns True  → lock acquired, bot may start.
    Returns False → another live process holds the lock; caller should exit.
    Always prints [BOT_GUARD] messages.
    """
    print(
        f"{_LOG} starting BOT_USERNAME={bot_username!r}"
        f" BOT_MODE={bot_mode!r} PID={os.getpid()}"
    )
    _LOCK_DIR.mkdir(exist_ok=True)
    path = _lock_path(bot_username)

    if path.exists():
        existing = _read_lock(path)
        if existing is not None:
            if not _is_stale(existing):
                print(
                    f"{_LOG} duplicate session detected — "
                    f"{bot_username!r} already running as"
                    f" pid={existing.get('pid','?')}"
                    f" mode={existing.get('bot_mode','?')}."
                    " Exiting safely."
                )
                return False
            print(
                f"{_LOG} stale lock for {bot_username!r}"
                f" (pid={existing.get('pid','?')}"
                f" hb={existing.get('heartbeat_at','?')}) — reclaiming."
            )

    _write_lock(path, {
        "bot_username": bot_username,
        "bot_mode":     bot_mode,
        "pid":          os.getpid(),
        "started_at":   _utc_now(),
        "heartbeat_at": _utc_now(),
    })
    print(f"{_LOG} lock acquired for {bot_username!r} mode={bot_mode!r}")
    return True


def release(bot_username: str) -> None:
    """Release the lock on clean shutdown (called via atexit)."""
    path = _lock_path(bot_username)
    try:
        data = _read_lock(path)
        if data and data.get("pid") == os.getpid():
            path.unlink(missing_ok=True)
            print(f"{_LOG} lock released for {bot_username!r}", flush=True)
        # else: another process claimed it — leave it alone
    except Exception as exc:
        print(f"{_LOG} release error: {exc!r}", flush=True)


def update_heartbeat(bot_username: str) -> None:
    """Refresh the heartbeat timestamp — called by heartbeat_loop."""
    path = _lock_path(bot_username)
    try:
        data = _read_lock(path) or {}
        if data.get("pid") == os.getpid():
            data["heartbeat_at"] = _utc_now()
            _write_lock(path, data)
    except Exception:
        pass


async def heartbeat_loop(bot_username: str) -> None:
    """Async task: update lock heartbeat every 30 s so it never goes stale."""
    while True:
        await asyncio.sleep(_HB_INTERVAL)
        update_heartbeat(bot_username)


def list_locks() -> list[dict]:
    """
    Return all current lock file payloads, each augmented with '_stale' bool.
    Used by !botstatus to show session state.
    """
    _LOCK_DIR.mkdir(exist_ok=True)
    result = []
    for f in sorted(_LOCK_DIR.glob("*.lock")):
        data = _read_lock(f) or {}
        data["_stale"] = _is_stale(data)
        data["_file"]  = f.name
        result.append(data)
    return result
