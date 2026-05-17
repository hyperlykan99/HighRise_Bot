"""
modules/dm_queue.py
-------------------
Host-only DM queue. Any bot inserts rows; only ChillTopiaBot (host / all mode)
processes and sends them.

DB table : host_dm_queue  (created in database._migrate_db)
Log tags  : [DM ROUTE] [DM HOST SEND] [DM BLOCKED]
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import database as db
import config as _cfg

if TYPE_CHECKING:
    from main import BaseBot

_IS_HOST: bool = _cfg.BOT_MODE in ("host", "all")


# ---------------------------------------------------------------------------
# Enqueue (any bot can call this)
# ---------------------------------------------------------------------------

def queue_host_dm(
    user_id: str,
    username: str,
    dm_type: str,
    category: str,
    message: str,
    conversation_id: str = "",
) -> None:
    """Insert a pending DM row to be sent by the host bot."""
    print(f"[DM ROUTE] queued_for_host user=@{username} type={dm_type}")
    try:
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO host_dm_queue
                   (user_id, username, conversation_id,
                    dm_type, category, message, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', datetime('now'))""",
            (user_id, username, conversation_id,
             dm_type, category, message[:249]),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[DM ROUTE] queue_host_dm error: {exc!r}")


# ---------------------------------------------------------------------------
# Send (host bot only)
# ---------------------------------------------------------------------------

async def send_host_dm(
    bot: "BaseBot",
    conv_id: str,
    message: str,
    dm_type: str = "direct",
) -> bool:
    """Send a single DM from the host bot. Returns True on success."""
    try:
        await bot.highrise.send_message(conv_id, message[:249], "text")
        print(f"[DM HOST SEND] type={dm_type} status=sent")
        return True
    except Exception as exc:
        print(f"[DM HOST SEND] type={dm_type} status=failed err={exc!r}")
        return False


# ---------------------------------------------------------------------------
# Process queue (host bot heartbeat / on_start)
# ---------------------------------------------------------------------------

async def process_host_dm_queue(bot: "BaseBot", limit: int = 20) -> None:
    """Flush pending rows from host_dm_queue. Host / all bot only."""
    if not _IS_HOST:
        print("[DM BLOCKED] bot=non-host reason=host_only_dm_queue")
        return

    try:
        conn = db.get_connection()
        rows = conn.execute(
            """SELECT id, user_id, username, conversation_id,
                      dm_type, category, message
               FROM host_dm_queue
               WHERE status='pending'
               ORDER BY id ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        print(f"[DM HOST SEND] queue fetch error: {exc!r}")
        return

    for row in rows:
        row_id  = row["id"]
        uid     = row["user_id"]
        uname   = row["username"]
        conv_id = row["conversation_id"]
        dm_type = row["dm_type"]
        msg     = row["message"]

        # Resolve conversation_id from notify_users if not stored
        if not conv_id:
            try:
                nr = db.get_notify_user(uid)
                if nr:
                    conv_id = nr.get("conversation_id", "")
            except Exception:
                pass

        if not conv_id:
            _mark_queue_row(row_id, "skipped", "no_conversation_id")
            print(
                f"[DM ROUTE] queued_for_host user=@{uname}"
                f" type={dm_type} skipped=no_conv"
            )
            continue

        ok     = await send_host_dm(bot, conv_id, msg, dm_type)
        status = "sent" if ok else "failed"
        _mark_queue_row(row_id, status)
        print(f"[DM HOST SEND] user=@{uname} type={dm_type} status={status}")
        await asyncio.sleep(0.4)


async def startup_host_dm_queue_loop(bot: "BaseBot") -> None:
    """
    Host bot only — drain host_dm_queue every 10 s.
    Started once in on_start under should_this_bot_run_module("host").
    """
    if not _IS_HOST:
        print("[DM BLOCKED] startup_host_dm_queue_loop skipped — not host bot")
        return
    print("[DM HOST QUEUE] Loop started — draining every 10s")
    await asyncio.sleep(8)
    while True:
        try:
            await process_host_dm_queue(bot)
        except Exception as exc:
            print(f"[DM HOST QUEUE] loop error: {exc!r}")
        await asyncio.sleep(10)


def _mark_queue_row(row_id: int, status: str, error: str = "") -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            """UPDATE host_dm_queue
               SET status=?, sent_at=datetime('now'), error=?
               WHERE id=?""",
            (status, error, row_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
