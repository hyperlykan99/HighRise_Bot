"""
modules/ai_assistant_core.py — AceSinatra AI Assistant entry point (3.3A rebuild).

Trigger words (case-insensitive):
  - "AceSinatra" / "@AceSinatra" — anywhere in the message
  - "ace"        / "@ace"        — at start of message or @mention
  - "assistant"                  — at start of message
  - "bot"                        — at start of message

Does NOT respond to every chat message — only clearly triggered ones.
Does NOT intercept !/slash commands.
All replies are sent via send_whisper (never public chat spam).
No external API calls — keyword/pattern-based only.
Falls back gracefully on any error — bot keeps running.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

import database as db

if TYPE_CHECKING:
    from highrise import BaseBot, User

from modules.ai_permissions import (
    get_perm_level,
    requires_admin,
    requires_staff,
    PERM_STAFF, PERM_ADMIN, PERM_OWNER,
)
from modules.ai_intent_router import (
    detect_intent,
    INTENT_DATE_TIME, INTENT_HOLIDAY, INTENT_PLAYER_GUIDANCE,
    INTENT_LUXE, INTENT_MINING, INTENT_FISHING,
    INTENT_CASINO, INTENT_EVENT,
    INTENT_BUG, INTENT_FEEDBACK, INTENT_SUMMARIZE_BUGS,
    INTENT_MOD_HELP, INTENT_PREPARE_SETTING,
    INTENT_CONFIRM_SETTING, INTENT_CANCEL_SETTING,
    INTENT_CMD_EXPLAIN, INTENT_GENERAL, INTENT_UNKNOWN,
)
from modules.ai_knowledge_base import get_answer
from modules.ai_time_holidays import get_date_reply, get_time_reply, get_next_holiday_reply
from modules.ai_safety import is_blocked, blocked_response
from modules.ai_confirmation_manager import (
    set_pending, get_pending, clear_pending, preview_message,
)
from modules.ai_action_executor import execute_action, BLOCKED_MSG
from modules.ai_logs import log_event


# ---------------------------------------------------------------------------
# Trigger detection
# ---------------------------------------------------------------------------

_TRIGGER_FULL_RE = re.compile(
    r"@?acesinatra\b",
    re.I,
)
_TRIGGER_START_RE = re.compile(
    r"^@?ace(?=[\s,!:?]|$)"
    r"|^@?assistant(?=[\s,!:?]|$)"
    r"|^@?bot(?=[\s,!:?]|$)",
    re.I,
)
_TRIGGER_MENTION_RE = re.compile(
    r"@ace\b|@bot\b|@acesinatra\b",
    re.I,
)


def is_ace_trigger(message: str) -> bool:
    """Return True when a message is clearly directed at AceSinatra."""
    s = message.strip()
    if _TRIGGER_FULL_RE.search(s):
        return True
    if _TRIGGER_START_RE.match(s):
        return True
    if _TRIGGER_MENTION_RE.search(s):
        return True
    return False


def strip_trigger(message: str) -> str:
    """Remove the trigger prefix and leading punctuation."""
    s = message.strip()
    s = _TRIGGER_FULL_RE.sub("", s, count=1)
    s = _TRIGGER_START_RE.sub("", s.strip(), count=1)
    s = re.sub(r"^[\s,!:?]+", "", s)
    return s.strip() or message.strip()


# ---------------------------------------------------------------------------
# Whisper helper
# ---------------------------------------------------------------------------

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Bug/feedback report helpers
# ---------------------------------------------------------------------------

def _save_report(user_id: str, username: str, text: str, category: str) -> bool:
    try:
        db.create_report(
            reporter_id       = user_id,
            reporter_username = username,
            target_username   = "",
            report_type       = "bug_report" if category == "bug" else "feedback",
            reason            = text[:500],
        )
        return True
    except Exception:
        return False


def _count_open_bugs() -> int:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='bug_report' AND status='open'"
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _count_critical_bugs() -> int:
    try:
        conn = db.get_connection()
        row = conn.execute(
            "SELECT COUNT(*) FROM reports "
            "WHERE report_type='bug_report' AND status='open' AND priority='critical'"
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Intent handlers
# ---------------------------------------------------------------------------

async def _handle_date_time(bot: "BaseBot", user: "User", text: str) -> None:
    low = text.lower()
    if "holiday" in low:
        await _w(bot, user.id, get_next_holiday_reply())
    elif "time" in low and "date" not in low and "day" not in low:
        await _w(bot, user.id, get_time_reply())
    else:
        await _w(bot, user.id, get_date_reply())


async def _handle_holiday(bot: "BaseBot", user: "User") -> None:
    await _w(bot, user.id, get_next_holiday_reply())


async def _handle_player_guidance(bot: "BaseBot", user: "User") -> None:
    answer = get_answer("player_guidance") or (
        "Try: !missions, !mine, !fish, !daily, !events, !luxeshop"
    )
    await _w(bot, user.id, answer[:249])


async def _handle_bug(
    bot: "BaseBot", user: "User", text: str,
) -> None:
    saved = _save_report(user.id, user.username, text, "bug")
    if saved:
        await _w(bot, user.id,
                 "🐛 Bug report saved — thank you!\n"
                 "Staff will review it. You can also use !bug for more detail.")
    else:
        await _w(bot, user.id,
                 "🐛 Noted! Please use !bug to file your report "
                 "so it gets tracked properly.")


async def _handle_feedback(
    bot: "BaseBot", user: "User", text: str,
) -> None:
    saved = _save_report(user.id, user.username, text, "feedback")
    if saved:
        await _w(bot, user.id,
                 "💬 Feedback saved — thank you for helping improve ChillTopia!")
    else:
        await _w(bot, user.id,
                 "💬 Thanks! Use !feedback to make sure it's recorded properly.")


async def _handle_summarize_bugs(
    bot: "BaseBot", user: "User", perm: str,
) -> None:
    if not requires_staff(perm):
        await _w(bot, user.id, "🔒 Staff only.")
        return
    total    = _count_open_bugs()
    critical = _count_critical_bugs()
    await _w(bot, user.id,
             f"🐛 Open Bug Reports: {total}\n"
             f"Critical: {critical}\n"
             "Use !bugs open for details.\n"
             "Use !launchblockers for blockers.")


async def _handle_mod_help(
    bot: "BaseBot", user: "User", text: str, perm: str,
) -> None:
    if not requires_staff(perm):
        await _w(bot, user.id, "🔒 Staff only.")
        return
    low = text.lower()
    if "spam" in low or "spammer" in low:
        suggestion = "warn first, then !mute if it continues"
    elif "harass" in low or "bully" in low:
        suggestion = "warn, then !kick or escalate to owner"
    elif "ban" in low:
        suggestion = "use !ban [username] after documenting the reason"
    elif "kick" in low:
        suggestion = "use !kick [username] — consider a warning first"
    else:
        suggestion = "warn first, monitor behavior, escalate to owner if needed"
    await _w(bot, user.id,
             f"🛡️ Suggested: {suggestion}.\n"
             "I won't act automatically — use staff commands directly.")


async def _handle_prepare_setting(
    bot: "BaseBot", user: "User", text: str, perm: str,
) -> None:
    if not requires_admin(perm):
        await _w(bot, user.id,
                 "🔒 Admin or owner only for setting changes.")
        return

    # VIP price
    m = re.search(r"vip\s+price\s+to\s+([\d,]+)", text, re.I)
    if m:
        val = m.group(1).replace(",", "")
        try:
            current = db.get_room_setting("vip_price", "unknown")
        except Exception:
            current = "unknown"
        set_pending(
            user_id        = user.id,
            action_key     = "set_vip_price",
            label          = "VIP Price",
            confirm_phrase = "CONFIRM VIP PRICE",
            current_value  = f"{current} 🎫",
            new_value      = f"{val} 🎫 Luxe Tickets",
            risk           = "Economy-impacting",
        )
        p = get_pending(user.id)
        if p:
            await _w(bot, user.id, preview_message(p))
        return

    # Start event
    m2 = re.search(r"start\s+event\s+(\w+)", text, re.I)
    if m2:
        ev = m2.group(1).lower()
        set_pending(
            user_id        = user.id,
            action_key     = "start_event",
            label          = f"Start Event: {ev}",
            confirm_phrase = f"CONFIRM START {ev.upper()}",
            current_value  = "No active event",
            new_value      = ev,
            risk           = "Room-wide impact",
        )
        p = get_pending(user.id)
        if p:
            await _w(bot, user.id, preview_message(p))
        return

    # Event duration
    m3 = re.search(r"event\s+duration\s+to\s+([\d]+)\s*(min|minute)?", text, re.I)
    if m3:
        mins = m3.group(1)
        set_pending(
            user_id        = user.id,
            action_key     = "set_event_duration",
            label          = "Default Event Duration",
            confirm_phrase = f"CONFIRM EVENT DURATION {mins}",
            current_value  = db.get_room_setting("default_event_duration", "60"),
            new_value      = f"{mins} minutes",
            risk           = "Room setting change",
        )
        p = get_pending(user.id)
        if p:
            await _w(bot, user.id, preview_message(p))
        return

    await _w(bot, user.id,
             "⚙️ I recognized a setting change request but couldn't parse it.\n"
             "Try: 'set VIP price to 600' or 'start event double_coins'.")


async def _handle_confirm(
    bot: "BaseBot", user: "User", text: str, perm: str,
) -> None:
    p = get_pending(user.id)
    if not p:
        await _w(bot, user.id,
                 "⚠️ No pending change to confirm. "
                 "(It may have expired after 60 seconds.)")
        return
    phrase = text.upper().strip()
    if p["confirm_phrase"] not in phrase:
        await _w(bot, user.id,
                 f"⚠️ Wrong confirmation phrase.\n"
                 f"Reply exactly: {p['confirm_phrase']}")
        return
    if not requires_admin(perm):
        await _w(bot, user.id, "🔒 Admin or owner only.")
        clear_pending(user.id)
        return
    result = await execute_action(
        bot, user.id, p["action_key"], p["new_value"], user.username,
    )
    clear_pending(user.id)
    log_event(user.username, perm, "confirmed_action", text,
              action=p["action_key"], outcome="executed")
    await _w(bot, user.id, result)


async def _handle_cancel(bot: "BaseBot", user: "User") -> None:
    p = get_pending(user.id)
    if p:
        clear_pending(user.id)
        await _w(bot, user.id, "❌ Pending change cancelled.")
    else:
        await _w(bot, user.id, "⚠️ Nothing to cancel.")


async def _handle_topic(bot: "BaseBot", user: "User", intent: str) -> None:
    topic_map = {
        INTENT_LUXE:    "luxe_tickets",
        INTENT_MINING:  "mining",
        INTENT_FISHING: "fishing",
        INTENT_CASINO:  "casino",
        INTENT_EVENT:   "events",
    }
    topic  = topic_map.get(intent)
    answer = get_answer(topic) if topic else None
    if answer:
        await _w(bot, user.id, answer[:249])
    else:
        await _w(bot, user.id,
                 "I'm not sure about that. Try !help or ask a staff member.")


async def _handle_cmd_explain(
    bot: "BaseBot", user: "User", text: str,
) -> None:
    m = re.search(r"[!/]?(\w+)\s*$", text.strip())
    cmd_name = m.group(1).lower() if m else ""
    topic_map = {
        "mine": "mining", "automine": "mining", "mineinv": "mining",
        "fish": "fishing", "autofish": "fishing", "fishinv": "fishing",
        "luxeshop": "luxe_tickets", "tickets": "luxe_tickets",
        "casino": "casino", "bet": "casino", "poker": "casino",
        "events": "events", "event": "events",
        "missions": "missions", "daily": "daily",
        "profile": "profile", "vip": "vip",
    }
    topic  = topic_map.get(cmd_name)
    answer = get_answer(topic) if topic else None
    if answer:
        await _w(bot, user.id, answer[:249])
    else:
        await _w(bot, user.id,
                 f"ℹ️ Try !{cmd_name} or !help for usage info."
                 if cmd_name else
                 "ℹ️ Try !help for command info.")


async def _handle_general(bot: "BaseBot", user: "User", text: str) -> None:
    await _w(bot, user.id, get_answer("room_overview") or
             "🏠 Ask me about mining, fishing, events, or what to do next!")


async def _handle_unknown(bot: "BaseBot", user: "User") -> None:
    await _w(bot, user.id,
             "🤖 I'm AceSinatra! I can help with:\n"
             "• What to do next — just ask!\n"
             "• Mining, fishing, casino, events, Luxe Tickets\n"
             "• Date/time • Bug reports\n"
             "Example: 'AceSinatra, explain mining'")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def handle_acesinatra(
    bot: "BaseBot", user: "User", message: str,
) -> bool:
    """
    AceSinatra natural-language assistant handler.

    Called from on_chat (and on_whisper if desired).
    Returns True if the message was claimed (prevents further processing).

    Only responds when is_ace_trigger(message) is True.
    Never intercepts !/slash commands.
    Catches all exceptions so the bot keeps running on errors.
    """
    try:
        if not is_ace_trigger(message):
            return False

        # Never intercept slash or bot commands
        if message.strip().startswith("!") or message.strip().startswith("/"):
            return False

        perm  = get_perm_level(user.username)
        clean = strip_trigger(message)

        # Hard safety check
        if is_blocked(clean):
            log_event(user.username, perm, "blocked", clean, outcome="blocked")
            await _w(bot, user.id, blocked_response())
            return True

        # Detect intent
        intent = detect_intent(clean)
        log_event(user.username, perm, intent, clean)

        # Route by intent
        if intent == INTENT_CANCEL_SETTING:
            await _handle_cancel(bot, user)

        elif intent == INTENT_CONFIRM_SETTING:
            await _handle_confirm(bot, user, clean, perm)

        elif intent == INTENT_HOLIDAY:
            await _handle_holiday(bot, user)

        elif intent == INTENT_DATE_TIME:
            await _handle_date_time(bot, user, clean)

        elif intent == INTENT_PLAYER_GUIDANCE:
            await _handle_player_guidance(bot, user)

        elif intent == INTENT_SUMMARIZE_BUGS:
            await _handle_summarize_bugs(bot, user, perm)

        elif intent == INTENT_BUG:
            await _handle_bug(bot, user, clean)

        elif intent == INTENT_FEEDBACK:
            await _handle_feedback(bot, user, clean)

        elif intent == INTENT_MOD_HELP:
            await _handle_mod_help(bot, user, clean, perm)

        elif intent == INTENT_PREPARE_SETTING:
            await _handle_prepare_setting(bot, user, clean, perm)

        elif intent in (
            INTENT_LUXE, INTENT_MINING, INTENT_FISHING,
            INTENT_CASINO, INTENT_EVENT,
        ):
            await _handle_topic(bot, user, intent)

        elif intent == INTENT_CMD_EXPLAIN:
            await _handle_cmd_explain(bot, user, clean)

        elif intent == INTENT_GENERAL:
            await _handle_general(bot, user, clean)

        else:
            await _handle_unknown(bot, user)

        return True

    except Exception as err:
        print(f"[ACESINATRA] Error handling message from {user.username}: {err!r}")
        return False
