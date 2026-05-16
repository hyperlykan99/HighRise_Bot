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
    "removewishlist", "setbadgeconfirm", "badgehelp",
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
    results: list[tuple[bool, str]] = []
    try:
        from modules.subscribers import (  # noqa: PLC0415
            _normalize_dm, _is_subscribe_request, _is_unsubscribe_request,
        )

        # --- normalisation
        results.append(_check("normalize '!sub' → 'sub'",
                               _normalize_dm("!sub") == "sub"))
        results.append(_check("normalize '!unsub' → 'unsub'",
                               _normalize_dm("!unsub") == "unsub"))
        results.append(_check("normalize '.' → '.'",
                               _normalize_dm(".") == "."))

        # --- !sub must map to subscribe, NOT unsubscribe
        results.append(_check("'sub' triggers subscribe",
                               _is_subscribe_request("sub")))
        results.append(_check("'sub' does NOT trigger unsubscribe",
                               not _is_unsubscribe_request("sub")))

        # --- !unsub must map to unsubscribe, NOT subscribe
        results.append(_check("'unsub' triggers unsubscribe",
                               _is_unsubscribe_request("unsub")))
        results.append(_check("'unsub' does NOT trigger subscribe",
                               not _is_subscribe_request("unsub")))

        # --- full words
        results.append(_check("'subscribe' triggers subscribe",
                               _is_subscribe_request("subscribe")))
        results.append(_check("'unsubscribe' triggers unsubscribe",
                               _is_unsubscribe_request("unsubscribe")))
        results.append(_check("'subscribe' NOT unsubscribe",
                               not _is_unsubscribe_request("subscribe")))
        results.append(_check("'unsubscribe' NOT subscribe",
                               not _is_subscribe_request("unsubscribe")))

        # --- random DMs must be ignored
        for noise in (".", "hello", "test", "yo", "ok", "thanks", "?"):
            results.append(_check(f"'{noise}' ignored (not sub/unsub)",
                                   not _is_subscribe_request(noise)
                                   and not _is_unsubscribe_request(noise)))

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
                 "!qatest quick\n"
                 "!qatest commands\n"
                 "!qatest notify\n"
                 "!qatest economy\n"
                 "!qatest titles\n"
                 "!qatest badges\n"
                 "!qatest all")
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
