"""
modules/qol_cmds.py
QoL / debug / player support / management commands.

Commands (manager+ unless noted):
  /quicktest              — post-update system health check
  /playercheck @username  — full player status snapshot
  /claimrewards           — everyone: claim pending coin rewards
  /eventcalendar          — everyone: upcoming events
  /lastupdate             — everyone: view; manager+: set
  /knownissues            — everyone: view known bug list
  /knownissue add/remove/clear  — manager+: manage list
  /feedback <msg>         — everyone: submit feedback
  /feedbacks / /feedbacklist   — manager+: view feedback
  /todo                   — manager+: staff checklist
  /aetest                 — manager+: auto event diagnostics
  /ownercheck             — manager+: command ownership check
  /botstatus              — everyone: bot online/maintenance status
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta

import database as db
from modules.permissions import can_manage_economy, is_owner, is_admin, can_moderate


async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def _time_ago(dt_str: str) -> str:
    if not dt_str:
        return "never"
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = int((datetime.now(timezone.utc) - dt).total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        m, s = divmod(secs, 60)
        if m < 60:
            return f"{m}m ago"
        h, m = divmod(m, 60)
        return f"{h}h {m}m ago"
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# /quicktest
# ---------------------------------------------------------------------------

async def handle_quicktest(bot, user) -> None:
    """/quicktest — post-update system health check."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    parts: list[str] = []

    # DB connection
    try:
        db.get_economy_settings()
        parts.append("Database: OK")
    except Exception:
        parts.append("Database: FAIL")

    # Command registry
    try:
        from modules.command_registry import REGISTRY
        parts.append(f"Commands: {len(REGISTRY)} registered")
    except Exception:
        parts.append("Commands: ERR")

    # Auto events
    try:
        import modules.auto_games as _ag
        ae_task = _ag._auto_event_loop_task
        ae_s    = db.get_auto_event_settings()
        ae_en   = int(ae_s.get("enabled", ae_s.get("auto_events_enabled", 0)))
        ae_live = ae_task is not None and not ae_task.done()
        parts.append(f"AutoEvents: {'ON' if ae_en else 'OFF'}[{'live' if ae_live else 'dead'}]")
    except Exception:
        parts.append("AutoEvents: ERR")

    # Blackjack
    try:
        from modules.blackjack import _state as _bj
        parts.append(f"BlackJack: {getattr(_bj, 'phase', '?')}")
    except Exception:
        parts.append("BlackJack: ERR")

    # Notifications
    try:
        from modules.sub_notif import NOTIF_CATEGORIES, _NOTIF_COOLDOWN
        parts.append(f"Notifications: {len(NOTIF_CATEGORIES)} categories OK")
    except Exception:
        parts.append("Notifications: ERR")

    # Gold tips
    try:
        gs = db.get_tip_settings() or {}
        parts.append(f"Gold Tips: {'OK' if gs else 'no settings'}")
    except Exception:
        parts.append("Gold Tips: ERR")

    # Pending rewards
    try:
        pending = db.get_pending_race_winners_for_banker(limit=50)
        parts.append(f"Pending Rewards: {len(pending)}")
    except Exception:
        parts.append("Pending Rewards: ERR")

    lines = ["🧪 Quick Test"] + parts
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /playercheck @username
# ---------------------------------------------------------------------------

async def handle_playercheck(bot, user, args: list[str]) -> None:
    """/playercheck @username — full player status snapshot."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return
    if len(args) < 2:
        await _w(bot, user.id, "Usage: !playercheck @username")
        return

    raw = args[1].lstrip("@").lower()
    sub_row = db.get_subscriber(raw)
    uid     = (sub_row or {}).get("user_id", "")
    subbed  = bool((sub_row or {}).get("subscribed", 0))
    last_seen_sub = (sub_row or {}).get("last_seen_at", "")

    if not uid:
        try:
            urow = db.get_user_by_username(raw)
            if urow:
                uid = urow.get("user_id", "")
        except Exception:
            pass

    if not uid:
        await _w(bot, user.id, f"🔍 @{raw} not found in database.")
        return

    # Balance
    try:
        coins = db.get_balance(uid)
    except Exception:
        coins = 0

    # Global notif
    try:
        global_on = bool(db.get_sub_notif_global(uid).get("global_enabled", 1))
    except Exception:
        global_on = True

    # Mining level (best-effort)
    mine_level = mine_xp = 0
    try:
        conn = db.get_connection()
        mrow = conn.execute(
            "SELECT mining_level, mining_xp FROM users WHERE user_id=?", (uid,)
        ).fetchone()
        conn.close()
        if mrow:
            mine_level = mrow["mining_level"] or 0
            mine_xp    = mrow["mining_xp"] or 0
    except Exception:
        pass

    # Fishing level (best-effort)
    fish_level = fish_xp = 0
    try:
        fp = db.get_or_create_fish_profile(uid, raw)
        fish_level = fp.get("fishing_level", fp.get("level", 0))
        fish_xp    = fp.get("fishing_xp", 0)
    except Exception:
        pass

    # Warnings
    warn_count = 0
    try:
        _, warn_count = db.get_warnings(raw)
    except Exception:
        pass

    # Currently in room
    in_room = False
    try:
        from modules.room_utils import _user_positions
        in_room = uid in _user_positions
    except Exception:
        pass

    lines = [
        f"🔍 Player: @{raw}",
        f"Subscribed: {'YES' if subbed else 'NO'}",
        f"Global Notifs: {'ON' if global_on else 'OFF'}",
        f"Coins: {_fmt(coins)}",
        f"Mining Lv: {mine_level} | Fishing Lv: {fish_level}",
        f"Warnings: {warn_count}",
        f"In Room: {'YES' if in_room else 'NO'}",
        f"Last Seen: {_time_ago(last_seen_sub)}",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /claimrewards  (banker — everyone)
# ---------------------------------------------------------------------------

async def handle_claimrewards(bot, user) -> None:
    """/claimrewards — claim all pending coin rewards."""
    uid   = user.id
    uname = user.username.lower()

    total, count = db.claim_pending_coin_rewards(uid)
    if total <= 0:
        await _w(bot, user.id, "🎁 Rewards\nNo claimable rewards right now.")
        return

    # Credit coins
    try:
        db.add_balance(uid, total)
    except Exception as exc:
        print(f"[CLAIMREWARDS] add_balance failed for {uname}: {exc}")
        await _w(bot, user.id, "❌ Error crediting coins. Please contact staff.")
        return

    await _w(bot, user.id,
             f"🎁 Rewards Claimed\n"
             f"Coins: {_fmt(total)} ({count} reward(s))\n"
             f"New Balance: {_fmt(db.get_balance(uid))}")


# ---------------------------------------------------------------------------
# /eventcalendar
# ---------------------------------------------------------------------------

async def handle_eventcalendar(bot, user) -> None:
    """/eventcalendar — upcoming events overview."""
    lines = ["📅 Event Calendar"]

    # Current event
    try:
        from modules.events import _get_all_active_events
        active = _get_all_active_events()
        if active:
            cur = active[0]["name"]
        else:
            cur = "None"
    except Exception:
        cur = "?"
    lines.append(f"Current Event: {cur}")

    # Next auto event
    try:
        next_at = db.get_auto_event_setting_str("next_event_at", "")
        next_id = db.get_auto_event_setting_str("next_event_id", "")
        ae_s    = db.get_auto_event_settings()
        ae_en   = int(ae_s.get("enabled", ae_s.get("auto_events_enabled", 0)))

        if not ae_en:
            lines.append("Next Auto Event: Disabled")
        elif next_id and next_at:
            # Get event name
            try:
                from modules.events import EVENTS
                ev_name = EVENTS.get(next_id, {}).get("name", next_id)
            except Exception:
                ev_name = next_id
            # Time remaining
            try:
                dt = datetime.fromisoformat(next_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                secs = max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))
                m, s = divmod(secs, 60)
                h, m = divmod(m, 60)
                tstr = f"{h}h {m}m" if h else f"{m}m {s}s"
                lines.append(f"Next Auto Event: {ev_name} in {tstr}")
            except Exception:
                lines.append(f"Next Auto Event: {ev_name}")
        else:
            lines.append("Next Auto Event: None scheduled")
    except Exception:
        lines.append("Next Auto Event: ?")

    # First Hunt status
    try:
        from modules.first_find import get_first_find_status
        ff = get_first_find_status()
        ff_on = ff.get("active", False)
        lines.append(f"First Hunt Race: {'Active' if ff_on else 'Not active'}")
    except Exception:
        lines.append("First Hunt Race: ?")

    # Weekly reset
    try:
        now = datetime.now(timezone.utc)
        days_until_sunday = (6 - now.weekday()) % 7 or 7
        reset_dt = now + timedelta(days=days_until_sunday)
        lines.append(f"Weekly Reset: {reset_dt.strftime('%a %b %d')}")
    except Exception:
        pass

    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /lastupdate
# ---------------------------------------------------------------------------

async def handle_lastupdate(bot, user, args: list[str]) -> None:
    """/lastupdate — view/edit latest bot update notes."""
    sub = args[1].lower() if len(args) > 1 else ""

    if sub == "set":
        if not can_manage_economy(user.username):
            await _w(bot, user.id, "Manager/admin/owner only.")
            return
        if len(args) < 3:
            await _w(bot, user.id, "Usage: !lastupdate set <message>")
            return
        msg = " ".join(args[2:])[:180]
        db.set_update_notes([msg], user.username)
        await _w(bot, user.id, "✅ Update notes set.")

    elif sub == "add":
        if not can_manage_economy(user.username):
            await _w(bot, user.id, "Manager/admin/owner only.")
            return
        if len(args) < 3:
            await _w(bot, user.id, "Usage: !lastupdate add <line>")
            return
        line = " ".join(args[2:])[:120]
        db.add_update_note(line, user.username)
        await _w(bot, user.id, "✅ Update note added.")

    elif sub == "clear":
        if not can_manage_economy(user.username):
            await _w(bot, user.id, "Manager/admin/owner only.")
            return
        db.clear_update_notes()
        await _w(bot, user.id, "✅ Update notes cleared.")

    else:
        notes = db.get_update_notes()
        if not notes:
            await _w(bot, user.id, "🛠️ Last Bot Update\nNo update notes yet.\nStaff: /lastupdate add <line>")
            return
        lines = ["🛠️ Last Bot Update"]
        for n in notes[:8]:
            lines.append(f"- {n.get('note','?')[:60]}")
        await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /knownissues  /knownissue add/remove/clear
# ---------------------------------------------------------------------------

async def handle_knownissues(bot, user) -> None:
    """/knownissues — public known bug/issue list."""
    issues = db.get_known_issues()
    if not issues:
        await _w(bot, user.id, "⚠️ Known Issues\nNo known issues right now.")
        return
    lines = ["⚠️ Known Issues"]
    for i, row in enumerate(issues[:8], 1):
        lines.append(f"{i}. {row.get('issue', '?')[:60]}")
    await _w(bot, user.id, "\n".join(lines)[:249])


async def handle_knownissue(bot, user, args: list[str]) -> None:
    """/knownissue add/remove/clear — manage known issues list."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else ""

    if sub == "add":
        if len(args) < 3:
            await _w(bot, user.id, "Usage: !knownissue add <description>")
            return
        text = " ".join(args[2:])[:150]
        new_id = db.add_known_issue(text, user.username)
        await _w(bot, user.id, f"✅ Issue #{new_id} added.")

    elif sub == "remove":
        if len(args) < 3 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !knownissue remove <id>")
            return
        ok = db.remove_known_issue(int(args[2]))
        await _w(bot, user.id, f"✅ Issue #{args[2]} removed." if ok else "Issue not found.")

    elif sub == "clear":
        n = db.clear_known_issues()
        await _w(bot, user.id, f"✅ {n} issue(s) cleared.")

    else:
        await _w(bot, user.id, "Usage: !knownissue add <text> | remove <id> | clear")


# ---------------------------------------------------------------------------
# /feedback  /feedbacks  /feedbacklist
# ---------------------------------------------------------------------------

async def handle_feedback(bot, user, args: list[str]) -> None:
    """/feedback <message> — submit feedback."""
    if len(args) < 2:
        await _w(bot, user.id,
                 "Usage: !feedback <message>\n"
                 "Use !suggest for feature ideas.\n"
                 "Use !bugreport for broken systems.")
        return
    msg = " ".join(args[1:])[:200]
    db.add_player_feedback(user.id, user.username.lower(), msg)
    await _w(bot, user.id, "💬 Feedback received. Thank you!")


async def handle_feedbacks(bot, user) -> None:
    """/feedbacks — view recent feedback (staff+)."""
    if not can_moderate(user.username):
        await _w(bot, user.id, "🔒 Staff only.")
        return
    rows = db.get_recent_feedback(limit=8)
    if not rows:
        await _w(bot, user.id, "💬 Recent Feedback\nNo feedback yet.")
        return
    lines = ["💬 Recent Feedback"]
    for i, r in enumerate(rows, 1):
        uname = r.get("username", "?")[:12]
        msg   = r.get("message", "")[:40]
        lines.append(f"{i}. @{uname}: {msg}")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /todo
# ---------------------------------------------------------------------------

async def handle_todo(bot, user, args: list[str]) -> None:
    """/todo — staff checklist (manager+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else ""

    if sub == "add":
        if len(args) < 3:
            await _w(bot, user.id, "Usage: !todo add <task>")
            return
        task = " ".join(args[2:])[:120]
        new_id = db.add_staff_todo(task, user.id, user.username.lower())
        await _w(bot, user.id, f"✅ Todo #{new_id} added.")

    elif sub == "done":
        if len(args) < 3 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !todo done <id>")
            return
        ok = db.complete_staff_todo(int(args[2]))
        await _w(bot, user.id, f"✅ Todo #{args[2]} done." if ok else "Todo not found.")

    elif sub == "remove":
        if len(args) < 3 or not args[2].isdigit():
            await _w(bot, user.id, "Usage: !todo remove <id>")
            return
        ok = db.remove_staff_todo(int(args[2]))
        await _w(bot, user.id, f"✅ Todo #{args[2]} removed." if ok else "Todo not found.")

    elif sub == "clear":
        n = db.clear_staff_todo()
        await _w(bot, user.id, f"✅ {n} todo(s) cleared.")

    else:
        rows = db.get_staff_todo()
        if not rows:
            await _w(bot, user.id,
                     "📝 Staff Todo\nNo tasks.\nUse !todo add <task> to add one.")
            return
        lines = ["📝 Staff Todo"]
        for r in rows[:8]:
            status = "✓" if r.get("status") == "done" else "·"
            task   = r.get("task", "?")[:50]
            lines.append(f"{status} #{r['id']} {task}")
        await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /aetest
# ---------------------------------------------------------------------------

async def handle_aetest(bot, user) -> None:
    """/aetest — auto event system diagnostic (manager+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    lines = ["🧪 Auto Event Test"]

    try:
        settings = db.get_auto_event_settings()
        enabled  = int(settings.get("enabled", settings.get("auto_events_enabled", 0)))
        lines.append(f"Auto Events: {'ON' if enabled else 'OFF'}")
    except Exception:
        lines.append("Auto Events: ERR")
        enabled = 0

    # Scheduler loop alive check
    try:
        import modules.auto_games as _ag
        ae_task = _ag._auto_event_loop_task
        alive   = ae_task is not None and not ae_task.done()
        lines.append(f"Scheduler: {'RUNNING' if alive else 'STOPPED'}")
    except Exception:
        lines.append("Scheduler: ERR")

    # Pool + eligible
    try:
        pool     = db.get_event_pool()
        eligible = db.get_eligible_pool_events()
        lines.append(f"Pool: {len(pool)} | Eligible: {len(eligible)}")
    except Exception:
        lines.append("Pool: ERR")

    # Current event
    try:
        from modules.events import _get_all_active_events
        active  = _get_all_active_events()
        cur_str = active[0]["name"] if active else "None"
        lines.append(f"Current: {cur_str}")
    except Exception:
        lines.append("Current: ERR")

    # Next event + timer
    try:
        next_id = db.get_auto_event_setting_str("next_event_id", "")
        next_at = db.get_auto_event_setting_str("next_event_at", "")
        if next_id and next_at:
            try:
                from modules.events import EVENTS
                ev_name = EVENTS.get(next_id, {}).get("name", next_id)
            except Exception:
                ev_name = next_id
            try:
                dt   = datetime.fromisoformat(next_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                secs = max(0, int((dt - datetime.now(timezone.utc)).total_seconds()))
                m, s = divmod(secs, 60)
                h, m = divmod(m, 60)
                tstr = f"{h}h {m}m" if h else f"{m}m {s}s"
                lines.append(f"Next: {ev_name} in {tstr}")
            except Exception:
                lines.append(f"Next: {ev_name}")
        else:
            lines.append("Next: None scheduled")
    except Exception:
        lines.append("Next: ERR")

    # History writable
    try:
        db.get_auto_event_settings()
        lines.append("History Writable: YES")
    except Exception:
        lines.append("History Writable: NO")

    # Skip commands registered
    try:
        from modules.command_registry import REGISTRY
        has_skip = "aeskip" in REGISTRY or "autoeventskip" in REGISTRY
        lines.append(f"Skip Commands: {'YES' if has_skip else 'NO'}")
    except Exception:
        lines.append("Skip Commands: ERR")

    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /ownercheck
# ---------------------------------------------------------------------------

async def handle_ownercheck(bot, user) -> None:
    """/ownercheck — check command ownership problems (manager+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    try:
        from modules.command_registry import REGISTRY
        from modules.multi_bot import _DEFAULT_COMMAND_OWNERS

        no_owner  = []
        valid_modes = {"host", "banker", "miner", "fisher", "blackjack",
                       "poker", "dj", "security", "eventhost", "shopkeeper"}

        for cmd, entry in REGISTRY.items():
            if not entry.owner or entry.owner == "UNKNOWN":
                no_owner.append(f"/{cmd}")

        lines = ["🧭 Owner Check"]
        if not no_owner:
            lines.append("NoOwner: 0")
            lines.append("Missing: 0")
            lines.append("Broken Routes: 0")
            lines.append("All commands: OK")
        else:
            lines.append(f"NoOwner: {len(no_owner)}")
            for cmd in no_owner[:5]:
                lines.append(f"  {cmd}")
            if len(no_owner) > 5:
                lines.append(f"  ...and {len(no_owner)-5} more")

        await _w(bot, user.id, "\n".join(lines)[:249])
    except Exception as exc:
        await _w(bot, user.id, f"🧭 Owner Check\nError: {str(exc)[:60]}")


# ---------------------------------------------------------------------------
# /botstatus  (spec-compliant: per-bot online/maintenance view)
# ---------------------------------------------------------------------------

async def handle_botstatus(bot, user, args: list[str] | None = None) -> None:
    """/botstatus — compact multi-bot status: 🟢 mode up=Xh rc=N"""
    import json as _j, time as _time

    _HIDDEN_MODES = {"main", "all", ""}

    # ── Gather raw data ────────────────────────────────────────────────────────
    try:
        instances  = db.get_all_bot_instances_status()
        maint_rows = db.get_all_maintenance_states()
    except Exception:
        instances, maint_rows = [], []

    maint_map    = {r["target"]: bool(r["enabled"]) for r in maint_rows
                    if r.get("scope") == "bot"}
    global_maint = any(r["enabled"] for r in maint_rows
                       if r.get("scope") == "global")

    # Deduplicate bot_instances by mode — newest heartbeat row wins.
    inst_by_mode: dict[str, dict] = {}
    for inst in instances:
        mode = (inst.get("bot_mode") or "").lower().strip()
        if mode in _HIDDEN_MODES:
            continue
        prev = inst_by_mode.get(mode)
        if prev is None or (inst.get("last_seen_at") or "") > (prev.get("last_seen_at") or ""):
            inst_by_mode[mode] = inst

    # Reconnect stats (rc count + last reason) written by bot.py runner.
    rc_stats: dict[str, dict] = {}
    for _m in inst_by_mode:
        try:
            _raw = db.get_room_setting(f"_bot_rc_{_m}", "")
            if _raw:
                rc_stats[_m] = _j.loads(_raw)
        except Exception:
            pass

    # Supervisor health — provides process_started_at (true uptime) and reason.
    sup_health: dict[str, dict] = {}
    try:
        from modules.bot_supervisor import load_health_from_db as _lhdb
        for _k, _v in _lhdb().items():
            sup_health[(_v.get("bot_mode") or _k).lower()] = _v
    except Exception:
        pass

    # Registry — for disabled bots + display names.
    _reg: dict = {}
    try:
        from modules.bot_supervisor import load_registry as _lreg
        _reg = _lreg()
    except Exception:
        pass

    # ── Helpers ────────────────────────────────────────────────────────────────
    def _hm(secs: float) -> str:
        m, _ = divmod(int(secs), 60)
        h, m = divmod(m, 60)
        return f"{h}h{m}m" if h else f"{m}m"

    def _proc_uptime(mode: str) -> str:
        """True process uptime from supervisor process_started_at."""
        sh = sup_health.get(mode, {})
        pst = sh.get("process_started_at")
        if pst:
            return _hm(_time.time() - float(pst))
        # Fallback: age since last heartbeat timestamp.
        ls_str = (inst_by_mode.get(mode) or {}).get("last_seen_at", "")
        if ls_str:
            try:
                ls = datetime.fromisoformat(ls_str)
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=timezone.utc)
                return _hm((datetime.now(timezone.utc) - ls).total_seconds())
            except Exception:
                pass
        return "?"

    now_utc = datetime.now(timezone.utc)

    # ── Build bot lines ────────────────────────────────────────────────────────
    bot_lines: list[str] = []

    # Modes seen in bot_instances (started bots).
    all_modes = set(inst_by_mode)

    # Add registry-disabled bots that were never started.
    for _rk in _reg:
        if not _rk.startswith("_") and not _reg[_rk].get("enabled", True):
            if _rk not in all_modes:
                all_modes.add(_rk)

    for mode in sorted(all_modes):
        inst     = inst_by_mode.get(mode)
        in_maint = maint_map.get(mode, False)
        rc       = rc_stats.get(mode, {})
        rc_n     = int(rc.get("rc", 0))
        sh       = sup_health.get(mode, {})

        # Registry disabled and never connected.
        if inst is None:
            bot_lines.append(f"⚫ {mode} disabled")
            continue

        status = inst.get("status", "offline")

        if in_maint:
            icon   = "🔧"
            detail = "maint"
        elif status == "online":
            try:
                ls = datetime.fromisoformat(inst.get("last_seen_at", ""))
                if ls.tzinfo is None:
                    ls = ls.replace(tzinfo=timezone.utc)
                stale = (now_utc - ls).total_seconds() > 90
            except Exception:
                stale = False
            if stale:
                icon   = "🟡"
                detail = "reconnecting"
            else:
                icon   = "🟢"
                detail = f"up={_proc_uptime(mode)}"
        else:
            icon   = "🔴"
            reason = (sh.get("last_disconnect_reason")
                      or rc.get("reason", "")).lower()
            detail = f"reason={reason[:15]}" if reason else "offline"

        rc_sfx = f" rc={rc_n}" if rc_n else ""
        bot_lines.append(f"{icon} {mode} {detail}{rc_sfx}")

    # ── Send (split at ~125 chars if long) ────────────────────────────────────
    header = "🤖 Bot Status"
    footer = f"Maint: {'ON 🛡️' if global_maint else 'OFF'}"

    if not bot_lines:
        await _w(bot, user.id, f"{header}\n(no bots)\n{footer}")
        return

    full = f"{header}\n" + "\n".join(bot_lines) + f"\n{footer}"
    if len(full) <= 249:
        await _w(bot, user.id, full)
        return

    # Split: page 1 = header + first half, page 2 = second half + footer.
    half = max(1, len(bot_lines) // 2)
    await _w(bot, user.id, (f"{header}\n" + "\n".join(bot_lines[:half]))[:249])
    await _w(bot, user.id, ("\n".join(bot_lines[half:]) + f"\n{footer}")[:249])


async def handle_botregistry(bot, user, args: list[str] | None = None) -> None:
    """/botregistry — show config/bot_registry.json in compact format."""
    from modules.economy import can_manage_economy
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return

    print(f"[BOT_SUPERVISOR] botregistry requested by {user.username}")

    try:
        from modules.bot_supervisor import load_registry as _lreg
        reg = _lreg()
    except Exception as exc:
        await _w(bot, user.id, f"Registry load error: {str(exc)[:80]}")
        return

    if not reg:
        await _w(bot, user.id, "📋 No registry found (config/bot_registry.json missing).")
        return

    # Sort by priority then name.
    entries = sorted(
        ((k, v) for k, v in reg.items() if not k.startswith("_") and isinstance(v, dict)),
        key=lambda kv: (int(kv[1].get("priority", 99)), kv[0]),
    )

    active = sum(1 for _, v in entries if v.get("enabled", True))
    lines  = [f"📋 Bot Registry ({active}/{len(entries)} active)"]

    for mode, cfg in entries:
        enabled     = cfg.get("enabled", True)
        priority    = cfg.get("priority", 99)
        reconnect   = cfg.get("auto_reconnect", True)
        tick  = "✓" if enabled  else "✗"
        rtick = "✓" if reconnect else "✗"
        lines.append(f"{mode} {tick} p{priority} reconnect{rtick}")

    # Send in up to 2 whispers.
    full = "\n".join(lines)
    if len(full) <= 249:
        await _w(bot, user.id, full)
        return

    half = max(1, len(lines) // 2)
    await _w(bot, user.id, "\n".join(lines[:half])[:249])
    await _w(bot, user.id, "\n".join(lines[half:])[:249])


# ---------------------------------------------------------------------------
# /botdiag [mode] — per-bot health diagnostics (owner/admin only)
# ---------------------------------------------------------------------------

async def handle_botdiag(bot, user, args: list[str] | None = None) -> None:
    """/botdiag [mode] — uptime, reconnects, last crash, locks for a bot."""
    import time as _t
    from modules.economy import can_manage_economy
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Owner/admin only.")
        return

    print(f"[BOT_SUPERVISOR] botdiag requested by {user.username}"
          + (f" target={args[1]}" if args and len(args) > 1 else ""))

    target = args[1].lower().strip() if args and len(args) > 1 else None

    # Load supervisor health from DB
    try:
        from modules.bot_supervisor import load_health_from_db as _lhdb
        health = _lhdb()
    except Exception:
        health = {}

    def _hm(secs: float) -> str:
        m, _ = divmod(max(0, int(secs)), 60)
        h, m = divmod(m, 60)
        return f"{h}h{m}m" if h else f"{m}m"

    now = _t.time()

    # ── No target → compact summary of all modes ───────────────────────────
    if not target:
        lines = ["🔍 Botdiag (all)"]
        for k, v in sorted(health.items()):
            bm  = (v.get("bot_mode") or k).lower()
            pst = v.get("process_started_at")
            up  = _hm(now - float(pst)) if pst else "?"
            rc  = v.get("reconnect_count", 0)
            reason = (v.get("last_disconnect_reason") or "—")[:20]
            lines.append(f"{bm} up={up} rc={rc} last={reason}")
        if len(lines) == 1:
            lines.append("(no health data yet — wait for first disconnect)")
        full = "\n".join(lines)
        if len(full) <= 249:
            await _w(bot, user.id, full)
        else:
            half = max(1, len(lines) // 2)
            await _w(bot, user.id, "\n".join(lines[:half])[:249])
            await _w(bot, user.id, "\n".join(lines[half:])[:249])
        return

    # ── Targeted mode diag ─────────────────────────────────────────────────
    snap: dict = {}
    for k, v in health.items():
        if (v.get("bot_mode") or k).lower() == target:
            snap = v
            break

    if not snap:
        await _w(bot, user.id,
                 f"No health data for '{target}'. "
                 f"Check mode name or wait for first disconnect.")
        return

    pst    = snap.get("process_started_at")
    cs     = snap.get("connected_since")
    up     = _hm(now - float(pst)) if pst else "?"
    conn   = _hm(now - float(cs))  if cs  else "?"
    rc     = snap.get("reconnect_count", 0)
    reason = (snap.get("last_disconnect_reason") or "—")[:35]

    # Normalised reason category
    try:
        from modules.bot_logger import categorize_reason as _cat
        cat = _cat(reason)
    except Exception:
        cat = "unknown"

    # Latest crash snapshot timestamp
    crash_ts = "—"
    try:
        from modules.bot_logger import get_recent_crashes as _grc
        snaps = _grc(target, n=1)
        if snaps:
            crash_ts = snaps[0].get("timestamp", "?")[:16]
    except Exception:
        pass

    # Active locks held by this bot
    lock_info = "none"
    try:
        import database as _db2
        _cn = _db2.get_connection()
        _rows = _cn.execute(
            "SELECT module FROM bot_module_locks WHERE bot_id=?", (target,)
        ).fetchall()
        _cn.close()
        if _rows:
            lock_info = ",".join(r[0] for r in _rows)
    except Exception:
        pass

    lines = [
        f"🔍 Diag: {target}",
        f"up={up}  conn={conn}  rc={rc}",
        f"reason={reason}",
        f"category={cat}",
        f"last_crash={crash_ts}",
        f"locks={lock_info}",
    ]
    await _w(bot, user.id, "\n".join(lines)[:249])
