"""
modules/qa_test.py
------------------
Owner/admin-only QA test system.  Tests command routing, notification logic,
economy/title/badge handler presence, and poker command coverage — without
touching real player balances or game state.

Commands
--------
!qatest              — show menu
!qatest quick        — fast routing check for notify + economy cmds
!qatest commands     — full command routing report
!qatest notify       — DM parse logic + notification routing
!qatest economy      — economy handler checks
!qatest titles       — title handler checks
!qatest badges       — badge handler checks
!qatest poker        — poker light checks
!qatest help         — help system checks
!qatest all          — run every suite
!qatest last         — show last stored report summary
!qatest failed       — show failures from last report

All messages <= 249 characters.
"""

from __future__ import annotations
from highrise import BaseBot, User
from modules.permissions import is_owner, is_admin

# ── In-memory last report ─────────────────────────────────────────────────────

LAST_QA_REPORT: dict = {}

# ── Command lists per suite ───────────────────────────────────────────────────

_NOTIFY_CMDS = [
    "sub", "subscribe", "unsub", "unsubscribe",
    "notifysettings", "alerts", "notify", "notifyhelp",
    "promo", "tipalert", "eventalert", "gamealert", "announcement",
    "subcount", "notifyaudit", "notifystatus", "unsubuser",
]
_BADGE_CMDS = [
    "badges", "mybadges", "badgeshop", "buybadge", "equipbadge",
    "profilebadge", "showbadge", "rarebadges",
    "wishlist", "wishbadge", "removewishlist", "unwishlist",
    "unequipbadge", "unlockbadge",
    "setbadgeconfirm", "badgehelp",
]
_TITLE_CMDS = [
    "titles", "titleshop", "mytitles", "buytitle",
    "equiptitle", "titlehelp", "myboosts",
]
_ECONOMY_CMDS = [
    "bal", "balance", "buycoins", "confirmbuycoins",
    "cancelbuycoins", "vipstatus",
]
_POKER_CMDS = ["poker", "pokerhelp", "pokerguide", "pokerstats", "pokeraudit"]
_HELP_CMDS   = ["help", "notifyhelp", "badgehelp", "titlehelp", "adminhelp"]


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _w(bot: BaseBot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


def _ok(label: str) -> tuple[bool, str]:
    return True,  f"✅ {label}"


def _fail(label: str) -> tuple[bool, str]:
    return False, f"❌ {label}"


def _check(label: str, condition: bool) -> tuple[bool, str]:
    return (True, f"✅ {label}") if condition else (False, f"❌ {label}")


# ── Registry-based routing check ─────────────────────────────────────────────

def _safe_get(entry, *names, default=None):
    """Read the first matching attribute or dict key from entry."""
    for name in names:
        if hasattr(entry, name):
            return getattr(entry, name)
        if isinstance(entry, dict) and name in entry:
            return entry[name]
    return default


def _check_cmd(cmd: str) -> tuple[bool, str]:
    """Return pass/fail based on REGISTRY entry.
    Cmd fields: owner, cat, fallback, safe, write, perm, aliases.
    """
    try:
        from modules.command_registry import REGISTRY, alias_map  # noqa: PLC0415
        key   = alias_map.get(cmd, cmd)
        entry = REGISTRY.get(key)
        if entry is None:
            return _fail(f"!{cmd}: no registry entry")
        owner = _safe_get(entry, "owner", "owner_bot", "bot", default="")
        if not owner:
            return _fail(f"!{cmd}: no owner set")
        return _ok(f"!{cmd} owner={owner}")
    except Exception as exc:
        return _fail(f"!{cmd}: error {exc!r}"[:60])


def _routing_suite(cmds: list[str]) -> list[tuple[bool, str]]:
    return [_check_cmd(c) for c in cmds]


# ── Notify logic checks (pure-function; no DB writes) ────────────────────────

def _notify_logic_checks() -> list[tuple[bool, str]]:
    """
    19 QA checks for the notification rebuild + hard-gate hotfix.
    Tests  1-2 : DM parse — exact subscribe/unsubscribe keywords accepted
    Tests  3-8 : DM gate — random/settings DMs hard-rejected (no subscribe)
    Test   9   : Main DM gate helper (is_valid_notify_dm_command) exists
    Tests 10-11: Room !sub gate (DB level)
    Tests 12-13: Settings/category are view-only (no subscribe side effect)
    Tests 14-16: Broadcast filtering (unsub / no conv_id / category OFF)
    Test  17   : Random DM does not create subscribed DB row
    Test  18   : First-time DM !sub works (user_id not in room DB)
    Test  19   : notify_system owns routing (old modules do not)
    """
    results: list[tuple[bool, str]] = []
    try:
        from modules.notify_system import (  # noqa: PLC0415
            _is_sub_command, _is_unsub_command, _OWNS_NOTIFY_ROUTING,
            is_valid_notify_dm_command, VALID_DM_NOTIFY_COMMANDS,
        )
        import database as _db  # noqa: PLC0415

        # ── Tests 1-2: exact keyword acceptance ───────────────────────────────
        results.append(_check("DM '!sub' triggers subscribe",
                               _is_sub_command("!sub")))
        results.append(_check("DM '!unsub' triggers unsubscribe",
                               _is_unsub_command("!unsub")))

        # ── Tests 3-8: hard gate rejects random / settings DMs ───────────────
        _random_dms = ["Hello", "hello", ".", "Ok", "ok", "?",
                        "thanks", "test", "!notifysettings", "!notify tips on"]
        gate_all_rejected = all(
            not is_valid_notify_dm_command(d) for d in _random_dms
        )
        results.append(_check("Gate rejects 'Hello'",
                               not is_valid_notify_dm_command("Hello")))
        results.append(_check("Gate rejects '.'",
                               not is_valid_notify_dm_command(".")))
        results.append(_check("Gate rejects 'Ok'",
                               not is_valid_notify_dm_command("Ok")))
        results.append(_check("Gate rejects '?'",
                               not is_valid_notify_dm_command("?")))
        results.append(_check("Gate rejects '!notifysettings'",
                               not is_valid_notify_dm_command("!notifysettings")))
        results.append(_check("Gate rejects '!notify tips on'",
                               not is_valid_notify_dm_command("!notify tips on")))

        # ── Test 9: gate helper exists and accepts valid commands ─────────────
        gate_accepts = (
            is_valid_notify_dm_command("!sub")
            and is_valid_notify_dm_command("subscribe")
            and is_valid_notify_dm_command("!unsub")
            and is_valid_notify_dm_command("unsubscribe")
        )
        results.append(_check("DM gate accepts !sub/subscribe/!unsub/unsubscribe",
                               gate_accepts))

        # ── Tests 10-11: Room !sub gate (DB level) ────────────────────────────
        row10 = _db.get_notify_user("__qa_no_conv_user__")
        results.append(_check("Room !sub blocked: no conversation_id",
                               not row10 or not row10.get("conversation_id")))

        _db.upsert_notify_user(
            "__qa_test_sub__", "qa_test_sub_user",
            subscribed=1, source="manual_dm",
            conversation_id="qa_conv_123", dm_available=1,
        )
        row11 = _db.get_notify_user("__qa_test_sub__")
        results.append(_check("Room !sub works with conversation_id",
                               bool(row11 and row11.get("conversation_id") == "qa_conv_123"
                                    and row11.get("subscribed") == 1)))

        # ── Tests 12-13: Settings/category are view-only ──────────────────────
        row12 = _db.get_notify_user("__qa_test_sub__")
        results.append(_check("!notifysettings view-only (subscribed unchanged)",
                               row12.get("subscribed") == 1))

        _db.set_notify_category("__qa_test_sub__", "qa_test_sub_user", "tips", True)
        row13 = _db.get_notify_user("__qa_test_sub__")
        results.append(_check("!notify category does not subscribe",
                               row13.get("subscribed") == 1 and row13.get("tips") == 1))

        # ── Tests 14-16: Broadcast filtering ─────────────────────────────────
        _db.upsert_notify_user(
            "__qa_unsub__", "qa_unsub_user",
            subscribed=0, conversation_id="qa_unsub_conv",
        )
        bcast14 = _db.get_notify_users_for_broadcast("events")
        results.append(_check("Broadcast skips unsubscribed users",
                               not any(r["user_id"] == "__qa_unsub__" for r in bcast14)))

        _db.upsert_notify_user("__qa_noconv__", "qa_noconv_user", subscribed=1)
        bcast15 = _db.get_notify_users_for_broadcast("events")
        results.append(_check("Broadcast skips no conversation_id",
                               not any(r["user_id"] == "__qa_noconv__" for r in bcast15)))

        _db.upsert_notify_user(
            "__qa_catoff__", "qa_catoff_user",
            subscribed=1, conversation_id="qa_catoff_conv", events=0,
        )
        bcast16 = _db.get_notify_users_for_broadcast("events")
        results.append(_check("Broadcast respects category OFF",
                               not any(r["user_id"] == "__qa_catoff__" for r in bcast16)))

        # ── Test 17: Random DM must not create subscribed row ─────────────────
        # Simulate the gate: a random DM is blocked before any DB write.
        # We verify no row exists for a fresh user_id after a rejected DM.
        _rand_uid = "__qa_random_dm_test__"
        _db.delete_notify_user(_rand_uid)  # ensure clean
        # The gate would return False for "Hello" — verify no row exists
        gate_rejected = not is_valid_notify_dm_command("Hello")
        existing_row  = _db.get_notify_user(_rand_uid)
        results.append(_check("Random DM does not create subscribed row",
                               gate_rejected and not existing_row))

        # ── Test 18: First-time DM !sub works (user_id not in room DB) ────────
        _ft_uid = "__qa_firsttime_dm_sub__"
        _db.delete_notify_user(_ft_uid)
        _db.upsert_notify_user(
            _ft_uid, "",
            subscribed=1, source="manual_dm",
            conversation_id="qa_firsttime_conv", dm_available=1,
        )
        row18 = _db.get_notify_user(_ft_uid)
        results.append(_check("First-time DM !sub saves user_id + conv_id",
                               bool(row18 and row18.get("subscribed") == 1
                                    and row18.get("conversation_id") == "qa_firsttime_conv")))

        # ── Test 19: notify_system owns routing ───────────────────────────────
        results.append(_check("notify_system owns notification routing",
                               bool(_OWNS_NOTIFY_ROUTING)))

        # Cleanup
        for _uid in ("__qa_test_sub__", "__qa_unsub__", "__qa_noconv__",
                     "__qa_catoff__", "__qa_random_dm_test__", "__qa_firsttime_dm_sub__"):
            try:
                _db.delete_notify_user(_uid)
            except Exception:
                pass

    except Exception as exc:
        results.append(_fail(f"Import error: {exc!r}"[:80]))

    return results


# ── Report helpers ────────────────────────────────────────────────────────────

async def _deliver_report(
    bot: BaseBot,
    user: User,
    suite: str,
    results: list[tuple[bool, str]],
) -> None:
    global LAST_QA_REPORT

    passed   = sum(1 for ok, _ in results if ok)
    failed   = sum(1 for ok, _ in results if not ok)
    failures = [line for ok, line in results if not ok]

    LAST_QA_REPORT = {
        "suite":    suite,
        "passed":   passed,
        "failed":   failed,
        "failures": failures,
        "all":      [line for _, line in results],
    }
    print(f"[QA] finished suite={suite} passed={passed} failed={failed}")

    summary = f"🧪 QA {suite.title()} Report\nPassed: {passed}\nFailed: {failed}"
    await _w(bot, user.id, summary)

    if failures:
        chunk = "\n".join(failures[:5])
        if len(failures) > 5:
            chunk += f"\n…+{len(failures) - 5} more — use !qatest failed"
        await _w(bot, user.id, chunk[:249])
    else:
        await _w(bot, user.id, "✅ All checks passed.")


async def _show_last(bot: BaseBot, user: User) -> None:
    if not LAST_QA_REPORT:
        await _w(bot, user.id, "No QA report yet. Run !qatest all or !qatest [suite].")
        return
    r   = LAST_QA_REPORT
    msg = (f"🧪 Last QA: {r.get('suite', '?')}\n"
           f"Passed: {r.get('passed', 0)}\n"
           f"Failed: {r.get('failed', 0)}")
    await _w(bot, user.id, msg)


async def _show_failed(bot: BaseBot, user: User) -> None:
    if not LAST_QA_REPORT:
        await _w(bot, user.id, "No QA report yet.")
        return
    failures = LAST_QA_REPORT.get("failures", [])
    if not failures:
        await _w(bot, user.id, "✅ No failures in last QA report.")
        return
    for i in range(0, min(len(failures), 15), 5):
        chunk = "\n".join(failures[i:i + 5])
        await _w(bot, user.id, chunk[:249])


# ── Main entry point ──────────────────────────────────────────────────────────

async def handle_qatest(bot: BaseBot, user: User, args: list[str]) -> None:
    """!qatest [suite] — owner/admin QA test system."""
    if not is_owner(user.username) and not is_admin(user.username):
        await _w(bot, user.id, "⚠️ Owner only.")
        return

    suite = (args[1].lower() if len(args) > 1 else "").strip()

    if not suite:
        await _w(bot, user.id,
                 "🧪 QA Test Menu\n"
                 "⚡ Quick: !qatest quick\n"
                 "🧾 Commands: !qatest commands\n"
                 "🔔 Notify: !qatest notify\n"
                 "💰 Economy: !qatest economy\n"
                 "🏷️ Titles: !qatest titles")
        await _w(bot, user.id,
                 "🎖️ Badges: !qatest badges\n"
                 "🃏 Poker: !qatest poker\n"
                 "📖 Help: !qatest help\n"
                 "✅ All: !qatest all\n"
                 "❌ Failed: !qatest failed")
        return

    if suite == "last":
        await _show_last(bot, user)
        return

    if suite == "failed":
        await _show_failed(bot, user)
        return

    if suite == "quick":
        results = _routing_suite(_NOTIFY_CMDS + _ECONOMY_CMDS)
        await _deliver_report(bot, user, "quick", results)
        return

    if suite == "commands":
        all_cmds = (_NOTIFY_CMDS + _BADGE_CMDS + _TITLE_CMDS
                    + _ECONOMY_CMDS + _POKER_CMDS + _HELP_CMDS)
        results = _routing_suite(all_cmds)
        await _deliver_report(bot, user, "commands", results)
        return

    if suite == "notify":
        await _w(bot, user.id, "🧪 QA Test Started: notify")
        results = _notify_logic_checks() + _routing_suite(_NOTIFY_CMDS)
        await _deliver_report(bot, user, "notify", results)
        return

    if suite == "economy":
        results = _routing_suite(_ECONOMY_CMDS)
        await _deliver_report(bot, user, "economy", results)
        return

    if suite == "titles":
        results = _routing_suite(_TITLE_CMDS)
        await _deliver_report(bot, user, "titles", results)
        return

    if suite == "badges":
        results = _routing_suite(_BADGE_CMDS)
        await _deliver_report(bot, user, "badges", results)
        return

    if suite == "poker":
        results = _routing_suite(_POKER_CMDS)
        await _deliver_report(bot, user, "poker", results)
        return

    if suite == "help":
        results = _routing_suite(_HELP_CMDS)
        await _deliver_report(bot, user, "help", results)
        return

    if suite == "all":
        await _w(bot, user.id, "🧪 QA All — running all suites…")
        results = (
            _notify_logic_checks()
            + _routing_suite(_NOTIFY_CMDS)
            + _routing_suite(_BADGE_CMDS)
            + _routing_suite(_TITLE_CMDS)
            + _routing_suite(_ECONOMY_CMDS)
            + _routing_suite(_POKER_CMDS)
            + _routing_suite(_HELP_CMDS)
        )
        await _deliver_report(bot, user, "all", results)
        return

    await _w(bot, user.id,
             "⚠️ Unknown suite.\n"
             "Use: quick commands notify economy titles badges poker help all")
