"""
modules/radio_diagnostics.py
----------------------------
Lightweight runtime visibility for the AzuraCast radio pipeline.

This module is intentionally read-only. It reports ownership, queue health,
registry integrity, AzuraCast reachability, and small structured lifecycle logs
without changing request, playback, cleanup, or refund behavior.
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import time
from collections import Counter

import database as db
import modules.config_store as cs
from modules.radio_status import ACTIVE_QUEUE_STATUSES

_LOG = "[RADIO_HEALTH]"
_EVENT_LOG = "[RADIO_EVENT]"

CLEANUP_OWNER = "modules.playback_engine"
PLAYBACK_OWNER = "modules.playback_engine"
PASSIVE_CLEANUP_COMPAT = "modules.yt_request.startup_yt_cleanup_task"


def log_radio_event(event: str, **fields: object) -> None:
    """Print one compact structured radio event line."""
    payload = {"event": event, **fields}
    parts = []
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, (dict, list, tuple)):
            value = json.dumps(value, sort_keys=True, default=str)
        parts.append(f"{key}={str(value)!r}")
    print(f"{_EVENT_LOG} " + " ".join(parts))


def _active_queue_counts() -> dict[str, int]:
    counts = {status: 0 for status in ACTIVE_QUEUE_STATUSES}
    try:
        placeholders = ",".join("?" * len(ACTIVE_QUEUE_STATUSES))
        with db.db_conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM yt_request_jobs "
                f"WHERE status IN ({placeholders}) AND played_at IS NULL "
                "GROUP BY status",
                ACTIVE_QUEUE_STATUSES,
            ).fetchall()
        counts.update({str(status): int(count or 0) for status, count in rows})
    except Exception as exc:
        if "no such table" not in str(exc).lower():
            counts["error"] = -1
            print(f"{_LOG} queue_count_error={exc!r}")
    return counts


def _active_playback_state() -> dict[str, object]:
    try:
        import modules.playback_engine as engine

        live: dict[str, object] = {}
        try:
            with db.db_conn() as conn:
                row = conn.execute(
                    "SELECT id, title FROM yt_request_jobs "
                    "WHERE status='playing' AND played_at IS NULL "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
            if row:
                live = {"id": row[0], "title": row[1] or ""}
        except Exception as exc:
            if "no such table" not in str(exc).lower():
                live = {"error": repr(exc)}
        return {
            "mode": engine.get_playlist_mode(),
            "request_id": (live or {}).get("job_id") or (live or {}).get("id") or 0,
            "title": (live or {}).get("title", ""),
            "elapsed": engine.get_cur_elapsed(),
            "duration": engine.get_cur_duration(),
        }
    except Exception as exc:
        return {"error": repr(exc)}


def _bot_owner_state() -> dict[str, object]:
    try:
        import config
        from modules.multi_bot import should_this_bot_run_module

        mode = getattr(config, "BOT_MODE", "")
        return {
            "bot_mode": mode,
            "yt_request_owner": bool(should_this_bot_run_module("yt_request")),
            "active_for_radio": bool(mode == "dj" and should_this_bot_run_module("yt_request")),
        }
    except Exception as exc:
        return {"error": repr(exc)}


def _registry_state() -> dict[str, object]:
    try:
        from modules import radio_command_registry as rcr

        entries = rcr.entries()
        duplicate_aliases = rcr.find_duplicate_commands()
        modules = sorted({entry.module for entry in entries})
        return {
            "loaded": True,
            "entries": len(entries),
            "commands": len(rcr.registry()),
            "duplicates": duplicate_aliases,
            "modules": modules,
        }
    except Exception as exc:
        return {"loaded": False, "error": repr(exc)}


def _lifecycle_api_state() -> dict[str, object]:
    required = (
        "create_request",
        "mark_ready",
        "mark_playing",
        "mark_played",
        "mark_failed",
        "mark_cleaned",
        "mark_cancelled",
    )
    try:
        import modules.request_queue as rq

        missing = [name for name in required if not callable(getattr(rq, name, None))]
        return {"loaded": not missing, "missing": missing}
    except Exception as exc:
        return {"loaded": False, "error": repr(exc)}


def _task_name_counts() -> Counter:
    try:
        return Counter(
            task.get_name()
            for task in asyncio.all_tasks()
            if not task.done()
        )
    except RuntimeError:
        return Counter()


def _cleanup_loop_state() -> dict[str, object]:
    names = _task_name_counts()
    watched = {
        "radio_cleanup_poll": names.get("radio_cleanup_poll", 0),
        "radio_playback_poll": names.get("radio_playback_poll", 0),
        "radio_prepare_worker": names.get("radio_prepare_worker", 0),
        "radio_playback_startup": names.get("radio_playback_startup", 0),
    }
    duplicates = {name: count for name, count in watched.items() if count > 1}
    return {
        "cleanup_owner": CLEANUP_OWNER,
        "playback_owner": PLAYBACK_OWNER,
        "passive_cleanup_compat": PASSIVE_CLEANUP_COMPAT,
        "tasks": watched,
        "duplicates": duplicates,
    }


def _orphan_temp_file_count() -> int:
    count = 0
    try:
        staging_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "request_staging",
        )
        STAGING_DIR = os.path.abspath(staging_dir)
        count += len(glob.glob(os.path.join(STAGING_DIR, "tmp_replay_*.mp3")))
    except Exception:
        pass
    try:
        with db.db_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM local_replay_jobs "
                "WHERE temp_filename LIKE 'tmp_replay_%' "
                "AND COALESCE(status, '') NOT IN ('cleanup_complete', 'cleaned')"
            ).fetchone()
        count += int(row[0] if row else 0)
    except Exception:
        pass
    return count


async def azuracast_status(timeout_s: float = 3.0) -> dict[str, object]:
    if not cs.azura_api_ready():
        return {"configured": False, "state": "unconfigured"}
    try:
        import modules.azuracast_controller as azura

        loop = asyncio.get_running_loop()
        np = await asyncio.wait_for(
            loop.run_in_executor(None, azura.fetch_nowplaying),
            timeout=timeout_s,
        )
        if not np:
            return {"configured": True, "state": "unreachable"}
        song = ((np.get("now_playing") or {}).get("song") or {})
        return {
            "configured": True,
            "state": "ok",
            "title": (song.get("title") or "")[:80],
        }
    except asyncio.TimeoutError:
        return {"configured": True, "state": "timeout"}
    except Exception as exc:
        return {"configured": True, "state": "error", "error": repr(exc)[:120]}


async def snapshot(timeout_s: float = 3.0) -> dict[str, object]:
    return {
        "ts": int(time.time()),
        "queue_counts": _active_queue_counts(),
        "playback": _active_playback_state(),
        "cleanup": _cleanup_loop_state(),
        "bot_owner": _bot_owner_state(),
        "azuracast": await azuracast_status(timeout_s=timeout_s),
        "registry": _registry_state(),
        "queue_lifecycle_api": _lifecycle_api_state(),
        "orphan_temp_files": _orphan_temp_file_count(),
    }


def _flat_status(value: object) -> str:
    if isinstance(value, dict):
        return ",".join(f"{k}:{v}" for k, v in value.items()) or "none"
    return str(value)


async def log_startup_health(bot: object | None = None) -> None:
    snap = await snapshot(timeout_s=2.5)
    cleanup = snap["cleanup"] if isinstance(snap["cleanup"], dict) else {}
    registry = snap["registry"] if isinstance(snap["registry"], dict) else {}
    owner = snap["bot_owner"] if isinstance(snap["bot_owner"], dict) else {}
    azura = snap["azuracast"] if isinstance(snap["azuracast"], dict) else {}
    lifecycle = snap["queue_lifecycle_api"] if isinstance(snap["queue_lifecycle_api"], dict) else {}
    print(
        f"{_LOG} stage=startup"
        f" cleanup_owner={cleanup.get('cleanup_owner')!r}"
        f" playback_owner={cleanup.get('playback_owner')!r}"
        f" registry_loaded={registry.get('loaded')!r}"
        f" registry_duplicates={registry.get('duplicates')!r}"
        f" lifecycle_api_loaded={lifecycle.get('loaded')!r}"
        f" azuracast_state={azura.get('state')!r}"
        f" bot_owner={owner.get('bot_mode')!r}"
        f" active_for_radio={owner.get('active_for_radio')!r}"
        f" duplicate_cleanup_loops={cleanup.get('duplicates')!r}"
        f" orphan_temp_files={snap.get('orphan_temp_files')!r}"
    )


def format_status_messages(snap: dict[str, object]) -> list[str]:
    queue_counts = snap.get("queue_counts", {})
    playback = snap.get("playback", {})
    cleanup = snap.get("cleanup", {})
    owner = snap.get("bot_owner", {})
    azura = snap.get("azuracast", {})
    registry = snap.get("registry", {})

    active_total = sum(
        int(v)
        for v in (queue_counts or {}).values()
        if isinstance(v, int) and v > 0
    )
    lines = [
        "RADIO STATUS:",
        f"Queue active: {active_total} ({_flat_status(queue_counts)})",
        f"Playback: req={_flat_status((playback or {}).get('request_id', 0))} "
        f"mode={(playback or {}).get('mode', '?')}",
        f"Cleanup owner: {(cleanup or {}).get('cleanup_owner', '?')}",
        f"Playback owner: {(cleanup or {}).get('playback_owner', '?')}",
        f"Bot owner: {(owner or {}).get('bot_mode', '?')} "
        f"active={(owner or {}).get('active_for_radio', False)}",
        f"AzuraCast: {(azura or {}).get('state', '?')}",
        f"Registry: loaded={(registry or {}).get('loaded', False)} "
        f"dupes={len((registry or {}).get('duplicates') or {})}",
        f"Orphan tmp files: {snap.get('orphan_temp_files', 0)}",
    ]

    messages: list[str] = []
    current = ""
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > 240:
            messages.append(current)
            current = line
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages
