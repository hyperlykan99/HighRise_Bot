"""
modules/safety.py
-----------------
3.1N — Moderation anti-abuse + economy safety commands.

New commands:
  !softban @user [minutes] [reason]   — manager+  (economy-gate player)
  !unsoftban @user                    — manager+
  !safetyadmin [settings|set|enable|disable|cooldowns]  — admin/owner
  !economysafety [alerts|user|ledger|recent|tx]         — admin/owner
  !safetydash                          — admin/owner (summary dashboard)
  !mod                                 — alias for !modhelp (staff+)
  !modhelp                             — role-tiered moderation help (staff+)

All messages ≤ 249 chars.
"""
from __future__ import annotations

import time
import uuid
import datetime
from typing import Optional

import database as db
from modules.permissions import (
    can_moderate, can_manage_games, can_manage_economy,
    is_admin, is_owner,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _gc():
    return db.get_connection()


# ---------------------------------------------------------------------------
# Softban helpers (DB-backed)
# ---------------------------------------------------------------------------

def is_softbanned(user_id: str) -> Optional[dict]:
    """
    Return the active softban row dict (with 'mins_left') or None.
    Automatically expires stale entries.
    """
    try:
        conn = _gc()
        row = conn.execute(
            """SELECT *, CAST(
                    (julianday(expires_at) - julianday('now')) * 1440
               AS INTEGER) AS mins_left
               FROM softbans
               WHERE user_id=? AND active=1 AND datetime(expires_at) > datetime('now')
               ORDER BY id DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        # Clean up any expired rows
        conn.execute(
            "UPDATE softbans SET active=0 WHERE user_id=? AND datetime(expires_at)<=datetime('now')",
            (user_id,),
        )
        conn.commit()
        conn.close()
        return dict(row) if row else None
    except Exception as exc:
        print(f"[SAFETY] is_softbanned error: {exc!r}")
        return None


def _softban_user(user_id: str, username: str, banned_by: str,
                  duration_minutes: int, reason: str) -> None:
    try:
        conn = _gc()
        conn.execute(
            "UPDATE softbans SET active=0 WHERE user_id=? AND active=1",
            (user_id,),
        )
        conn.execute(
            """INSERT INTO softbans
                   (user_id, username, banned_by, reason, duration_minutes,
                    created_at, expires_at, active)
               VALUES (?, ?, ?, ?, ?,
                    datetime('now'),
                    datetime('now', ?),
                    1)""",
            (user_id, username.lower(), banned_by.lower(), reason,
             duration_minutes, f"+{duration_minutes} minutes"),
        )
        # Log to moderation_logs
        _log_mod_action(
            staff_id="", staff_name=banned_by,
            target_id=user_id, target_name=username,
            action="softban", reason=reason,
            duration_minutes=duration_minutes,
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[SAFETY] _softban_user error: {exc!r}")


def _unsoftban_user(user_id: str) -> bool:
    try:
        conn = _gc()
        cur = conn.execute(
            "UPDATE softbans SET active=0 WHERE user_id=? AND active=1",
            (user_id,),
        )
        conn.commit()
        conn.close()
        return cur.rowcount > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Moderation log helper
# ---------------------------------------------------------------------------

def _log_mod_action(staff_id: str, staff_name: str,
                    target_id: str, target_name: str,
                    action: str, reason: str,
                    duration_minutes: int = 0) -> None:
    try:
        action_id = str(uuid.uuid4())
        expires_at = ""
        if duration_minutes > 0:
            dt = datetime.datetime.utcnow() + datetime.timedelta(minutes=duration_minutes)
            expires_at = dt.strftime("%Y-%m-%d %H:%M:%S")
        conn = _gc()
        conn.execute(
            """INSERT OR IGNORE INTO moderation_logs
                   (action_id, staff_id, staff_name, target_id, target_name,
                    action, reason, duration_minutes, created_at, expires_at, active)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, 1)""",
            (action_id, staff_id, staff_name, target_id, target_name,
             action, reason, duration_minutes, expires_at),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[SAFETY] _log_mod_action error: {exc!r}")


# ---------------------------------------------------------------------------
# Safety alert helper
# ---------------------------------------------------------------------------

_ALERT_COOLDOWN: dict[str, float] = {}  # (user_id, source) -> last alert epoch


def log_safety_alert(user_id: str, username: str, alert_type: str,
                     source: str, details: str, severity: str = "medium",
                     blocked: bool = False) -> None:
    """Log a safety alert. Deduped to at most 1 per user/source per 60s."""
    try:
        key = f"{user_id}:{source}"
        now = time.time()
        if now - _ALERT_COOLDOWN.get(key, 0) < 60:
            return
        _ALERT_COOLDOWN[key] = now
        alert_id = str(uuid.uuid4())
        conn = _gc()
        conn.execute(
            """INSERT OR IGNORE INTO safety_alerts
                   (alert_id, user_id, username, alert_type, severity,
                    source, details, blocked, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (alert_id, user_id, username.lower(), alert_type, severity,
             source, details, 1 if blocked else 0),
        )
        conn.commit()
        conn.close()
        print(f"[SAFETY ALERT] type={alert_type} user=@{username} source={source} blocked={blocked}")
    except Exception as exc:
        print(f"[SAFETY] log_safety_alert error: {exc!r}")


# ---------------------------------------------------------------------------
# Processed event helpers (tip dedup)
# ---------------------------------------------------------------------------

def is_event_processed(event_id: str) -> bool:
    """Return True if this event_id has already been handled."""
    if not event_id:
        return False
    try:
        conn = _gc()
        row = conn.execute(
            "SELECT 1 FROM processed_events WHERE event_id=? LIMIT 1",
            (event_id,),
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def mark_event_processed(event_id: str, event_type: str,
                         user_id: str, username: str, source: str) -> bool:
    """
    Mark an event_id as processed. Returns True on success, False if already exists.
    """
    if not event_id:
        return True
    try:
        conn = _gc()
        conn.execute(
            """INSERT OR IGNORE INTO processed_events
                   (event_id, event_type, user_id, username, source, created_at)
               VALUES (?, ?, ?, ?, ?, datetime('now'))""",
            (event_id, event_type, user_id, username.lower(), source),
        )
        inserted = conn.total_changes > 0
        conn.commit()
        conn.close()
        return inserted
    except Exception as exc:
        print(f"[SAFETY] mark_event_processed error: {exc!r}")
        return True


# ---------------------------------------------------------------------------
# Economy transaction ledger helper
# ---------------------------------------------------------------------------

def log_economy_tx(user_id: str, username: str, currency: str,
                   amount: int, direction: str, source: str,
                   details: str = "", event_id: str = "") -> None:
    """Log a coin/ticket credit or debit to the economy_transactions ledger."""
    try:
        tx_id = str(uuid.uuid4())
        conn = _gc()
        conn.execute(
            """INSERT OR IGNORE INTO economy_transactions
                   (tx_id, user_id, username, currency, amount, direction,
                    source, details, event_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (tx_id, user_id, username.lower(), currency, abs(amount),
             direction, source, details, event_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[SAFETY] log_economy_tx error: {exc!r}")


# ---------------------------------------------------------------------------
# !softban @user [minutes] [reason]   (manager+)
# ---------------------------------------------------------------------------

async def handle_softban(bot, user, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !softban @user [minutes] [reason]")
        return

    target_name = args[1].lstrip("@").strip()
    minutes = 60
    reason = "economy access restricted"

    if len(args) >= 3 and args[2].isdigit():
        minutes = min(int(args[2]), 10080)
        reason = " ".join(args[3:]).strip() or reason
    elif len(args) >= 3:
        reason = " ".join(args[2:]).strip()

    if target_name.lower() == user.username.lower():
        await _w(bot, user.id, "You cannot softban yourself.")
        return

    target = db.get_user_by_username(target_name)
    if target is None:
        await _w(bot, user.id, f"@{target_name} not found.")
        return

    _softban_user(
        user_id=target["user_id"],
        username=target["username"],
        banned_by=user.username,
        duration_minutes=minutes,
        reason=reason[:80],
    )
    name = target["username"][:15]
    rsn  = reason[:60]
    await _w(bot, user.id,
             f"🚫 Economy Restricted\n@{name} for {minutes}m\nReason: {rsn}")

    # Security staff alert
    try:
        print(f"[SECURITY ALERT TRIGGER] action=softban target=@{target['username']} by=@{user.username}")
        from modules.staff_alerts import queue_staff_alert  # noqa: PLC0415
        _salert = (
            f"🚨 Security Alert\n"
            f"Action: Softban\n"
            f"User: @{target['username']}\n"
            f"Duration: {minutes}m\n"
            f"By: @{user.username}\n"
            f"Reason: {rsn}"
        )[:249]
        queue_staff_alert("security", _salert)
    except Exception:
        pass

    # Player notice — whisper + host DM
    try:
        await _w(bot, target["user_id"],
                 f"🚫 Softban Notice\n"
                 f"You were restricted in ChillTopia.\n"
                 f"Duration: {minutes}m\nReason: {rsn}\nBy: @{user.username}")
    except Exception:
        pass
    try:
        from modules.staff_alerts import send_player_mod_notice  # noqa: PLC0415
        await send_player_mod_notice(
            bot, target["user_id"], target["username"],
            "softban", rsn, user.username, duration=f"{minutes}m",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# !unsoftban @user   (manager+)
# ---------------------------------------------------------------------------

async def handle_unsoftban(bot, user, args: list[str]) -> None:
    if not can_manage_games(user.username):
        await _w(bot, user.id, "🔒 Manager only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !unsoftban @user")
        return

    target_name = args[1].lstrip("@").strip()
    target = db.get_user_by_username(target_name)
    if target is None:
        await _w(bot, user.id, f"@{target_name} not found.")
        return

    removed = _unsoftban_user(target["user_id"])
    name = target["username"][:15]
    if removed:
        await _w(bot, user.id,
                 f"✅ Economy Restriction Removed\n@{name} can earn/use economy again.")
        try:
            await _w(bot, target["user_id"],
                     "✅ Your economy access has been restored. You can earn and spend again.")
        except Exception:
            pass
    else:
        await _w(bot, user.id, f"@{name} has no active softban.")


# ---------------------------------------------------------------------------
# !safetyadmin [settings|set|enable|disable|cooldowns]   (admin+)
# ---------------------------------------------------------------------------

_SAFETY_SETTING_KEYS = {
    "spamwindow":   ("automod_spam_window",   "30",  "Spam window (seconds)"),
    "maxcommands":  ("max_commands",          "8",   "Max commands per window"),
    "repeatlimit":  ("max_same_message",      "3",   "Repeat same-cmd limit"),
}


def _get_safety_setting(key: str, default: str) -> str:
    try:
        conn = _gc()
        row = conn.execute(
            "SELECT value FROM moderation_settings WHERE key=? LIMIT 1", (key,),
        ).fetchone()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default


def _set_safety_setting(key: str, value: str) -> None:
    try:
        conn = _gc()
        conn.execute(
            "INSERT OR REPLACE INTO moderation_settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[SAFETY] _set_safety_setting error: {exc!r}")


async def handle_safetyadmin(bot, user, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    sub = args[1].lower().strip() if len(args) > 1 else "settings"

    if sub in ("settings", "status"):
        enabled = _get_safety_setting("automod_enabled", "1") == "1"
        win     = _get_safety_setting("automod_spam_window", "30")
        maxc    = _get_safety_setting("max_commands", "8")
        rep     = _get_safety_setting("max_same_message", "3")
        await _w(bot, user.id,
                 f"🛡️ Safety Settings\n"
                 f"Spam: {'ON' if enabled else 'OFF'}\n"
                 f"Window: {win}s | Max: {maxc} cmds\n"
                 f"Repeat limit: {rep}\n"
                 f"Action: warn + cooldown")

    elif sub == "enable":
        _set_safety_setting("automod_enabled", "1")
        await _w(bot, user.id, "🛡️ Anti-spam enabled.")

    elif sub == "disable":
        _set_safety_setting("automod_enabled", "0")
        await _w(bot, user.id, "🛡️ Anti-spam disabled.")

    elif sub == "cooldowns":
        await _w(bot, user.id,
                 "🛡️ Cooldown Groups\n"
                 "Low (3-5s): profile balance missions\n"
                 "Medium (10s): flex showoff shop orebook\n"
                 "Game cmds: use game timers\n"
                 "Admin: no cooldown")

    elif sub == "set" and len(args) >= 4:
        param  = args[2].lower()
        val    = args[3]
        if param not in _SAFETY_SETTING_KEYS:
            keys = ", ".join(_SAFETY_SETTING_KEYS)
            await _w(bot, user.id, f"Unknown param. Use: {keys}")
            return
        if not val.isdigit() or int(val) < 1:
            await _w(bot, user.id, "Value must be a positive integer.")
            return
        db_key, _, label = _SAFETY_SETTING_KEYS[param]
        _set_safety_setting(db_key, val)
        await _w(bot, user.id, f"✅ {label} → {val}")

    else:
        await _w(bot, user.id,
                 "🛡️ Safety Admin\n"
                 "!safetyadmin settings\n"
                 "!safetyadmin enable | disable\n"
                 "!safetyadmin set spamwindow [s]\n"
                 "!safetyadmin set maxcommands [n]\n"
                 "!safetyadmin set repeatlimit [n]\n"
                 "!safetyadmin cooldowns")


# ---------------------------------------------------------------------------
# !economysafety [alerts|user|ledger|recent|tx]   (admin+)
# ---------------------------------------------------------------------------

async def handle_economysafety(bot, user, args: list[str]) -> None:
    if not (is_admin(user.username) or is_owner(user.username)):
        await _w(bot, user.id, "🔒 Admin only.")
        return

    sub = args[1].lower().strip() if len(args) > 1 else ""

    if sub == "alerts":
        try:
            conn = _gc()
            today = datetime.date.today().isoformat()
            rows = conn.execute(
                """SELECT username, alert_type, severity, blocked, created_at
                   FROM safety_alerts
                   WHERE date(created_at)=?
                   ORDER BY id DESC LIMIT 8""",
                (today,),
            ).fetchall()
            conn.close()
            if not rows:
                await _w(bot, user.id, "🛡️ No safety alerts today.")
                return
            lines = [f"🛡️ Alerts today ({len(rows)}):"]
            for r in rows:
                b = "BLOCKED" if r["blocked"] else "logged"
                lines.append(f"@{r['username'][:12]} {r['alert_type']} [{b}]")
            await _w(bot, user.id, "\n".join(lines)[:249])
        except Exception as exc:
            await _w(bot, user.id, f"Error: {exc!r}"[:100])

    elif sub == "recent":
        try:
            conn = _gc()
            rows = conn.execute(
                """SELECT username, currency, amount, direction, source, created_at
                   FROM economy_transactions
                   ORDER BY id DESC LIMIT 8""",
            ).fetchall()
            conn.close()
            if not rows:
                await _w(bot, user.id, "📋 No recent transactions.")
                return
            lines = ["📋 Recent Transactions:"]
            for r in rows:
                sign = "+" if r["direction"] == "credit" else "-"
                lines.append(
                    f"{sign}{r['amount']:,} {r['currency'][:5]}"
                    f" @{r['username'][:10]} [{r['source'][:12]}]"
                )
            await _w(bot, user.id, "\n".join(lines)[:249])
        except Exception as exc:
            await _w(bot, user.id, f"Error: {exc!r}"[:100])

    elif sub in ("user", "ledger") and len(args) >= 3:
        target_name = args[2].lstrip("@").strip()
        try:
            conn = _gc()
            rows = conn.execute(
                """SELECT currency, amount, direction, source, created_at
                   FROM economy_transactions
                   WHERE LOWER(username)=?
                   ORDER BY id DESC LIMIT 6""",
                (target_name.lower(),),
            ).fetchall()
            alerts = conn.execute(
                "SELECT COUNT(*) FROM safety_alerts WHERE LOWER(username)=?",
                (target_name.lower(),),
            ).fetchone()[0]
            sb = conn.execute(
                "SELECT 1 FROM softbans WHERE LOWER(username)=? AND active=1 LIMIT 1",
                (target_name.lower(),),
            ).fetchone()
            conn.close()
            head = (
                f"📋 Economy: @{target_name[:15]}\n"
                f"Alerts: {alerts} | Softban: {'YES' if sb else 'no'}"
            )
            if not rows:
                await _w(bot, user.id, head + "\nNo ledger entries.")
                return
            lines = [head]
            for r in rows:
                sign = "+" if r["direction"] == "credit" else "-"
                lines.append(
                    f"{sign}{r['amount']:,} {r['currency'][:5]}"
                    f" [{r['source'][:14]}]"
                )
            await _w(bot, user.id, "\n".join(lines)[:249])
        except Exception as exc:
            await _w(bot, user.id, f"Error: {exc!r}"[:100])

    elif sub == "tx" and len(args) >= 3:
        tx_id = args[2].strip()
        try:
            conn = _gc()
            row = conn.execute(
                "SELECT * FROM economy_transactions WHERE tx_id=? LIMIT 1", (tx_id,),
            ).fetchone()
            conn.close()
            if not row:
                await _w(bot, user.id, f"TX not found: {tx_id[:36]}")
                return
            msg = (
                f"📋 TX {tx_id[:8]}…\n"
                f"@{row['username'][:15]} {row['direction']}"
                f" {row['amount']:,} {row['currency']}\n"
                f"Source: {row['source'][:30]}\n"
                f"At: {row['created_at'][:16]}"
            )
            await _w(bot, user.id, msg)
        except Exception as exc:
            await _w(bot, user.id, f"Error: {exc!r}"[:100])

    else:
        await _w(bot, user.id,
                 "🛡️ Economy Safety\n"
                 "!economysafety alerts\n"
                 "!economysafety recent\n"
                 "!economysafety user @user\n"
                 "!economysafety ledger @user\n"
                 "!economysafety tx [tx_id]")


# ---------------------------------------------------------------------------
# !safetydash   (admin+)
# ---------------------------------------------------------------------------

async def handle_safetydash(bot, user) -> None:
    is_adm    = is_admin(user.username) or is_owner(user.username)
    is_staff  = can_moderate(user.username) or can_manage_games(user.username)

    if not is_staff and not is_adm:
        await _w(bot, user.id, "🔒 Staff only.")
        return

    try:
        conn = _gc()
        today = datetime.date.today().isoformat()
        alerts_today = conn.execute(
            "SELECT COUNT(*) FROM safety_alerts WHERE date(created_at)=?",
            (today,),
        ).fetchone()[0]
        blocked_today = conn.execute(
            "SELECT COUNT(*) FROM safety_alerts WHERE date(created_at)=? AND blocked=1",
            (today,),
        ).fetchone()[0]
        active_mutes = conn.execute(
            "SELECT COUNT(*) FROM mutes WHERE datetime(expires_at) > datetime('now')",
        ).fetchone()[0]
        active_softbans = conn.execute(
            "SELECT COUNT(*) FROM softbans WHERE active=1 AND datetime(expires_at) > datetime('now')",
        ).fetchone()[0]
        conn.close()

        if is_adm:
            enabled = _get_safety_setting("automod_enabled", "1") == "1"
            risk = "High" if alerts_today >= 5 else ("Medium" if alerts_today >= 2 else "Low")
            await _w(bot, user.id,
                     f"🛡️ Safety Dashboard\n"
                     f"AutoMod: {'ON' if enabled else 'OFF'}\n"
                     f"Alerts: {alerts_today} | Blocks: {blocked_today}\n"
                     f"Muted: {active_mutes} | Softbanned: {active_softbans}\n"
                     f"Risk: {risk}\n"
                     f"Use !economysafety alerts")
        else:
            risk = "High" if alerts_today >= 5 else ("Medium" if alerts_today >= 2 else "Low")
            await _w(bot, user.id,
                     f"🛡️ Safety\n"
                     f"Alerts today: {alerts_today}\n"
                     f"Duplicate blocks: {blocked_today}\n"
                     f"Muted: {active_mutes}\n"
                     f"Risk: {risk}\n"
                     f"Use !bugs open for reports.")
    except Exception as exc:
        await _w(bot, user.id, f"Dashboard error: {str(exc)[:60]}")


# ---------------------------------------------------------------------------
# !modhelp   (role-tiered, staff+)
# !mod       (alias)
# ---------------------------------------------------------------------------

async def handle_modhelp(bot, user, args: list[str]) -> None:
    if not can_moderate(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return

    is_mgr = can_manage_games(user.username)
    is_adm = is_admin(user.username) or is_owner(user.username)

    await _w(bot, user.id,
             "🛡️ Staff Mod\n"
             "!warn @user [reason]\n"
             "!warnings @user\n"
             "!modlog @user\n"
             "!bugs open\n"
             "!feedbacks recent")

    if is_mgr:
        await _w(bot, user.id,
                 "🛠️ Manager Mod\n"
                 "!mute @user [min] [reason]\n"
                 "!unmute @user\n"
                 "!softban @user [min] [reason]\n"
                 "!unsoftban @user")

    if is_adm:
        await _w(bot, user.id,
                 "👑 Admin Mod\n"
                 "!clearwarnings @user\n"
                 "!safetyadmin\n"
                 "!economysafety\n"
                 "!audit")
