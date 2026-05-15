"""modules/tip_audit.py
--------------------
Owner/admin commands for reviewing tip and conversion audit logs.

Commands:
  !tipaudit [@user | last | failed | event <hash>] [N]
  !tipauditdetails <log_id>
  !conversionlogs [@user | last] [N]

Permissions: admin / owner only.
All replies <= 249 chars.
"""
from __future__ import annotations
import traceback
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot, User

import database as db
from modules.permissions import is_owner, is_admin

try:
    from config import BOT_MODE as _BOT_MODE
except Exception:
    _BOT_MODE = ""


async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    await bot.highrise.send_whisper(uid, msg[:249])


def _can_audit(username: str) -> bool:
    return is_admin(username) or is_owner(username)


def _status_icon(status: str) -> str:
    if "success" in status:
        return "✅"
    if "duplicate" in status:
        return "⚠️"
    return "❌"


def _fmt_ts(ts: str) -> str:
    try:
        return ts[5:16].replace("T", " ")
    except Exception:
        return ts or ""


# ---------------------------------------------------------------------------
# !tipaudit [@user | last | failed | event <hash>] [N]
# ---------------------------------------------------------------------------

async def handle_tipaudit(bot: "BaseBot", user: "User", args: list[str]) -> None:
    print(f"[TIP AUDIT CMD] handler=handle_tipaudit bot={_BOT_MODE} "
          f"user={user.username} args={args}")
    if not _can_audit(user.username):
        await _w(bot, user.id, "🔒 Admin+ only.")
        return

    sub = args[1].lstrip("@").lower() if len(args) > 1 else ""

    try:
        # ── !tipaudit failed ──────────────────────────────────────────────
        if sub == "failed":
            rows = _query_failed_tips(8)
            print(f"[TIP AUDIT CMD] mode=failed rows_found={len(rows)}")
            if not rows:
                await _w(bot, user.id, "✅ No failed tip rewards found.")
                return
            lines = ["⚠️ Failed Tip Rewards"]
            for r in rows:
                line = f"@{r['sender_username']} {r['gold_amount']}g → {r['status']}"
                if r.get("failure_reason"):
                    line += f": {r['failure_reason'][:30]}"
                lines.append(line)
            msg = "\n".join(lines)[:249]
            await _w(bot, user.id, msg)
            print(f"[TIP AUDIT CMD] reply_sent=true")
            return

        # ── !tipaudit event <hash> ────────────────────────────────────────
        if sub == "event" and len(args) > 2:
            row = _query_tip_by_hash(args[2])
            print(f"[TIP AUDIT CMD] mode=event hash={args[2][:20]} found={row is not None}")
            if not row:
                await _w(bot, user.id, f"⚠️ No audit log for hash: {args[2][:20]}")
                return
            await _w(bot, user.id,
                     f"🧾 Tip Audit #{row['id']}\n"
                     f"From: @{row['sender_username']}\n"
                     f"Bot: {row['bot_mode']}\n"
                     f"Gold: {row['gold_amount']}g | {row['status']}")
            print(f"[TIP AUDIT CMD] reply_sent=true")
            return

        # ── !tipaudit last ────────────────────────────────────────────────
        if sub == "last":
            rows = _query_recent_tips(None, 5)
            print(f"[TIP AUDIT CMD] mode=last rows_found={len(rows)}")
            if not rows:
                await _w(bot, user.id, "🧾 No tip audit logs found.")
                return
            lines = ["🧾 Recent Tip Audits"]
            for r in rows:
                lines.append(
                    f"{_status_icon(r['status'])} @{r['sender_username']} "
                    f"{r['gold_amount']}g → {r['bot_mode']} #{r['id']}"
                )
            await _w(bot, user.id, "\n".join(lines)[:249])
            print(f"[TIP AUDIT CMD] reply_sent=true")
            return

        # ── !tipaudit (no args) ───────────────────────────────────────────
        if not sub:
            await _w(bot, user.id,
                     "Usage: !tipaudit @user [N]\n"
                     "       !tipaudit last\n"
                     "       !tipaudit failed\n"
                     "       !tipaudit event <hash>")
            return

        # ── !tipaudit @user [N] ───────────────────────────────────────────
        limit = 5
        if len(args) > 2:
            try:
                limit = max(1, min(int(args[2]), 10))
            except ValueError:
                pass

        print(f"[TIP AUDIT CMD] querying user={sub} limit={limit}")
        rows = _query_recent_tips(sub, limit)
        print(f"[TIP AUDIT CMD] rows_found={len(rows)}")

        if not rows:
            await _w(bot, user.id, f"🧾 No tip audit logs found for @{sub}.")
            print(f"[TIP AUDIT CMD] reply_sent=true (empty)")
            return

        lines = [f"🧾 Tip Audit @{sub}"]
        for i, r in enumerate(rows, 1):
            icon = _status_icon(r["status"])
            dup  = " (dup)" if r.get("duplicate_detected") else ""
            lines.append(
                f"{i}) {r['gold_amount']}g → +{r['luxe_awarded']}🎫 "
                f"to {r['bot_mode']} {icon}{dup}"
            )
        await _w(bot, user.id, "\n".join(lines)[:249])
        print(f"[TIP AUDIT CMD] reply_sent=true rows={len(rows)}")

    except Exception as _e:
        traceback.print_exc()
        print(f"[TIP AUDIT CMD ERROR] {_e!r}")
        try:
            await _w(bot, user.id, "⚠️ Tip audit failed. Check console logs.")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# !tipauditdetails <log_id>
# ---------------------------------------------------------------------------

async def handle_tipauditdetails(bot: "BaseBot", user: "User",
                                  args: list[str]) -> None:
    print(f"[TIP AUDIT CMD] handler=tipauditdetails bot={_BOT_MODE} "
          f"user={user.username} args={args}")
    if not _can_audit(user.username):
        await _w(bot, user.id, "🔒 Admin+ only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !tipauditdetails <log_id>")
        return
    try:
        log_id = int(args[1])
    except ValueError:
        await _w(bot, user.id, "⚠️ log_id must be a number.")
        return

    try:
        row = _query_tip_by_id(log_id)
        print(f"[TIP AUDIT CMD] querying id={log_id} found={row is not None}")
        if not row:
            await _w(bot, user.id, f"⚠️ No tip audit log #{log_id}.")
            return

        await _w(bot, user.id,
                 f"🧾 Tip Audit #{row['id']}\n"
                 f"From: @{row['sender_username']}\n"
                 f"Bot: {row['bot_mode']}\n"
                 f"Gold: {row['gold_amount']}g\n"
                 f"Expected: +{row['luxe_expected']}🎫\n"
                 f"Awarded: +{row['luxe_awarded']}🎫\n"
                 f"Status: {row['status']}")
        await _w(bot, user.id,
                 f"Before: {row['luxe_balance_before']:,}🎫\n"
                 f"After: {row['luxe_balance_after']:,}🎫\n"
                 f"Hash: {(row.get('event_hash') or '')[:24]}\n"
                 f"Time: {_fmt_ts(row.get('created_at', ''))}")
        print(f"[TIP AUDIT CMD] reply_sent=true id={log_id}")

    except Exception as _e:
        traceback.print_exc()
        print(f"[TIP AUDIT CMD ERROR] details {_e!r}")
        try:
            await _w(bot, user.id, "⚠️ Tip audit failed. Check console logs.")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# !conversionlogs [@user | last] [N]
# ---------------------------------------------------------------------------

async def handle_conversionlogs(bot: "BaseBot", user: "User",
                                  args: list[str]) -> None:
    print(f"[TIP AUDIT CMD] handler=conversionlogs bot={_BOT_MODE} "
          f"user={user.username} args={args}")
    if not _can_audit(user.username):
        await _w(bot, user.id, "🔒 Admin+ only.")
        return

    sub   = args[1].lstrip("@").lower() if len(args) > 1 else "last"
    limit = 5
    if len(args) > 2:
        try:
            limit = max(1, min(int(args[2]), 10))
        except ValueError:
            pass

    try:
        if sub == "last":
            rows  = _query_conversions(None, limit)
            label = "Recent"
        else:
            rows  = _query_conversions(sub, limit)
            label = f"@{sub}"

        print(f"[TIP AUDIT CMD] querying conversions label={label} rows_found={len(rows)}")

        if not rows:
            await _w(bot, user.id, f"🪙 No conversion logs found for {label}.")
            print(f"[TIP AUDIT CMD] reply_sent=true (empty)")
            return

        lines = [f"🪙 Conversion Logs {label}"]
        for i, r in enumerate(rows, 1):
            icon = "✅" if r["status"] == "success" else "❌"
            lines.append(
                f"{i}) {r['item_key']}: -{r['tickets_spent']:,}🎫 "
                f"+{r['coins_awarded']:,}🪙 {icon}"
            )
        await _w(bot, user.id, "\n".join(lines)[:249])
        print(f"[TIP AUDIT CMD] reply_sent=true rows={len(rows)}")

    except Exception as _e:
        traceback.print_exc()
        print(f"[TIP AUDIT CMD ERROR] conversionlogs {_e!r}")
        try:
            await _w(bot, user.id, "⚠️ Tip audit failed. Check console logs.")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# DB query helpers (read-only, all swallow exceptions)
# ---------------------------------------------------------------------------

def _query_recent_tips(username: str | None, limit: int) -> list[dict]:
    try:
        conn = db.get_connection()
        if username:
            rows = conn.execute(
                "SELECT * FROM tip_audit_logs "
                "WHERE sender_username = ? "
                "ORDER BY id DESC LIMIT ?",
                (username.lower(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tip_audit_logs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[TIP AUDIT DB] _query_recent_tips error: {e!r}")
        return []


def _query_failed_tips(limit: int) -> list[dict]:
    try:
        conn = db.get_connection()
        rows = conn.execute(
            "SELECT * FROM tip_audit_logs "
            "WHERE status IN ('failed_luxe', 'duplicate_ignored') "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[TIP AUDIT DB] _query_failed_tips error: {e!r}")
        return []


def _query_tip_by_id(log_id: int) -> dict | None:
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT * FROM tip_audit_logs WHERE id = ?", (log_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"[TIP AUDIT DB] _query_tip_by_id error: {e!r}")
        return None


def _query_tip_by_hash(event_hash: str) -> dict | None:
    try:
        conn = db.get_connection()
        row  = conn.execute(
            "SELECT * FROM tip_audit_logs WHERE event_hash = ? LIMIT 1",
            (event_hash,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"[TIP AUDIT DB] _query_tip_by_hash error: {e!r}")
        return None


def _query_conversions(username: str | None, limit: int) -> list[dict]:
    try:
        conn = db.get_connection()
        if username:
            rows = conn.execute(
                "SELECT * FROM luxe_conversion_logs "
                "WHERE username = ? "
                "ORDER BY id DESC LIMIT ?",
                (username.lower(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM luxe_conversion_logs "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[TIP AUDIT DB] _query_conversions error: {e!r}")
        return []
