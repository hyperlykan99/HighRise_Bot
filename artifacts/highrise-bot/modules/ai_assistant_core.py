"""
modules/ai_assistant_core.py — ChillTopiaMC AI Assistant (3.3A clean rebuild).

Primary trigger: message starts with "ai" (or "ai," "ai:" "ai?" "ai!").
Optional triggers: @ChillTopiaMC, ChillTopiaMC, ChillTopia, assistant, bot.

Trigger rules:
- "ai" must be the FIRST word — "said", "paid", "rain" do NOT trigger.
- "bot" / "assistant" / "chilltopia" trigger only at message start.
- @ChillTopiaMC / ChillTopiaMC trigger anywhere it appears at start.

Special cases:
- "ai" alone → welcome message
- "ai help"  → usage examples

All replies via send_whisper — never public chat spam.
Does NOT intercept !/slash commands.
Falls back gracefully — bot keeps running on any error.
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
    INTENT_LUXE, INTENT_CHILLCOINS, INTENT_MINING, INTENT_FISHING,
    INTENT_CASINO, INTENT_EVENT, INTENT_VIP,
    INTENT_BUG, INTENT_FEEDBACK, INTENT_SUMMARIZE_BUGS,
    INTENT_MOD_HELP, INTENT_STAFF_INFO, INTENT_ADMIN_INFO, INTENT_OWNER_INFO,
    INTENT_PREPARE_SETTING, INTENT_CONFIRM_SETTING, INTENT_CANCEL_SETTING,
    INTENT_PRIVATE_PLAYER_INFO, INTENT_CMD_EXPLAIN, INTENT_GENERAL, INTENT_UNKNOWN,
    RW_INTENTS,
    INTENT_RW_GLOBAL_TIME, INTENT_RW_GLOBAL_HOLIDAY,
    INTENT_RW_DATETIME, INTENT_RW_HOLIDAY,
    INTENT_RW_CURRENT_INFO, INTENT_RW_SENSITIVE,
    INTENT_RW_TRANSLATION, INTENT_RW_MATH,
    INTENT_RW_GENERAL, INTENT_RW_GLOBAL, INTENT_RW_UNKNOWN,
)
from modules.ai_global_time import get_global_time_reply, clarify_location
from modules.ai_global_holidays import get_global_holiday_reply
from modules.ai_global_knowledge import handle_global_question
from modules.ai_knowledge_access import get_knowledge_answer, check_access
from modules.ai_public_knowledge import get_public_answer, get_welcome
from modules.ai_time_holidays import get_date_reply, get_time_reply, get_next_holiday_reply
from modules.ai_safety import is_blocked, blocked_response
from modules.ai_confirmation_manager import (
    set_pending, get_pending, clear_pending, preview_message,
)
from modules.ai_action_executor import execute_action, BLOCKED_MSG
from modules.ai_logs import log_event


# ---------------------------------------------------------------------------
# Trigger detection — exactly per spec
# ---------------------------------------------------------------------------

def is_ai_trigger(message: str) -> bool:
    """
    Return True when a message is directed at the ChillTopiaMC AI.

    Triggers (first word only for short names to avoid false positives):
      ai / ai, / ai: / ai? / ai!
      @chilltopiamc / chilltopiamc / chilltopia
      assistant / bot (at start only)

    Does NOT trigger on "ai" inside another word (said, paid, rain, main).
    """
    normalized = message.strip().lower()
    return (
        normalized == "ai"
        or normalized.startswith("ai ")
        or normalized.startswith("ai,")
        or normalized.startswith("ai:")
        or normalized.startswith("ai?")
        or normalized.startswith("ai!")
        or normalized.startswith("@chilltopiamc")
        or normalized.startswith("chilltopiamc")
        or normalized.startswith("chilltopia ")
        or normalized.startswith("assistant ")
        or normalized.startswith("bot ")
    )


def strip_ai_trigger(message: str) -> str:
    """
    Remove the trigger prefix and return the actual user request.

    Examples:
      "ai what should I do next" → "what should I do next"
      "ai, explain Luxe Tickets" → "explain Luxe Tickets"
      "ai: start Mining Rush"   → "start Mining Rush"
      "ChillTopiaMC, help"      → "help"
      "ai"                      → ""
    """
    s = message.strip()
    low = s.lower()

    prefixes_ordered = [
        "@chilltopiamc", "chilltopiamc", "chilltopia",
        "assistant", "bot",
        "ai",
    ]
    for prefix in prefixes_ordered:
        if low.startswith(prefix):
            rest = s[len(prefix):]
            rest = re.sub(r"^[\s,!:?]+", "", rest)
            return rest.strip()
    return s


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


# ---------------------------------------------------------------------------
# Intent handlers (direct — not routed through knowledge_access)
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
    answer = get_public_answer("player_guidance") or (
        "🎯 Try: !daily, !missions, !mine, !fish, !events, !luxeshop"
    )
    await _w(bot, user.id, answer[:249])


async def _handle_bug(bot: "BaseBot", user: "User", text: str) -> None:
    saved = _save_report(user.id, user.username, text, "bug")
    if saved:
        await _w(bot, user.id,
                 "🐛 Bug report saved — thank you!\n"
                 "Staff will review it. Use !bug for more detail.")
    else:
        await _w(bot, user.id,
                 "🐛 Noted! Use !bug to file your report so it's tracked properly.")


async def _handle_feedback(bot: "BaseBot", user: "User", text: str) -> None:
    saved = _save_report(user.id, user.username, text, "feedback")
    if saved:
        await _w(bot, user.id,
                 "💬 Feedback saved — thank you for helping improve ChillTopia!")
    else:
        await _w(bot, user.id,
                 "💬 Thanks! Use !feedback to make sure it's recorded properly.")


async def _handle_mod_help(
    bot: "BaseBot", user: "User", text: str, perm: str,
) -> None:
    if not requires_staff(perm):
        await _w(bot, user.id, "🔒 Moderation help is staff only.")
        return
    low = text.lower()
    if "spam" in low or "spammer" in low:
        suggestion = "warn first, then !mute if it continues"
    elif "harass" in low or "bully" in low:
        suggestion = "warn, then !kick or escalate to owner"
    elif "ban" in low:
        suggestion = "use !ban [username] after documenting the reason"
    elif "kick" in low:
        suggestion = "consider a warning first, then !kick [username]"
    else:
        suggestion = "warn first, monitor, then escalate to owner if needed"
    await _w(bot, user.id,
             f"🛡️ Suggested: {suggestion}.\n"
             "I won't act automatically — use staff commands directly.")


async def _handle_prepare_setting(
    bot: "BaseBot", user: "User", text: str, perm: str,
) -> None:
    if not requires_admin(perm):
        await _w(bot, user.id, "🔒 Admin or owner only for setting changes.")
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
    m2 = re.search(r"start\s+(\w+(?:\s+\w+)?)\s+(?:for\s+)?(?:(\d+)\s*(hour|min|minute))?", text, re.I)
    if m2:
        ev_name = m2.group(1).strip().lower().replace(" ", "_")
        duration_str = ""
        if m2.group(2):
            unit = m2.group(3).lower()
            mins = int(m2.group(2)) * 60 if "hour" in unit else int(m2.group(2))
            duration_str = f" for {mins} minutes"
        set_pending(
            user_id        = user.id,
            action_key     = "start_event",
            label          = f"Start Event: {ev_name}{duration_str}",
            confirm_phrase = f"CONFIRM START {ev_name.upper()}",
            current_value  = "No active event",
            new_value      = ev_name,
            risk           = "Room-wide impact",
        )
        p = get_pending(user.id)
        if p:
            await _w(bot, user.id, preview_message(p))
        return

    # Event duration
    m3 = re.search(r"event\s+duration\s+to\s+([\d]+)\s*(min|minute|hour)?", text, re.I)
    if m3:
        val = m3.group(1)
        unit = (m3.group(2) or "min").lower()
        mins = int(val) * 60 if "hour" in unit else int(val)
        try:
            current = db.get_room_setting("default_event_duration", "60")
        except Exception:
            current = "60"
        set_pending(
            user_id        = user.id,
            action_key     = "set_event_duration",
            label          = "Default Event Duration",
            confirm_phrase = f"CONFIRM EVENT DURATION {mins}",
            current_value  = f"{current} min",
            new_value      = f"{mins} minutes",
            risk           = "Room setting change",
        )
        p = get_pending(user.id)
        if p:
            await _w(bot, user.id, preview_message(p))
        return

    await _w(bot, user.id,
             "⚙️ Recognized a setting change but couldn't parse it.\n"
             "Try: 'ai set VIP price to 600 tickets'\n"
             "or 'ai start mining_rush for 1 hour'")


async def _handle_confirm(
    bot: "BaseBot", user: "User", text: str, perm: str,
) -> None:
    p = get_pending(user.id)
    if not p:
        await _w(bot, user.id,
                 "⚠️ No pending change to confirm.\n"
                 "(It may have expired after 60 seconds.)")
        return
    phrase = text.upper().strip()
    if p["confirm_phrase"] not in phrase:
        await _w(bot, user.id,
                 f"⚠️ Wrong phrase. Reply exactly:\n{p['confirm_phrase']}")
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


async def _handle_real_world(
    bot: "BaseBot", user: "User", text: str, intent: str,
) -> None:
    """Route real-world questions to the appropriate global module."""
    if intent == INTENT_RW_GLOBAL_TIME or intent == INTENT_RW_DATETIME:
        await _w(bot, user.id, get_global_time_reply(text))

    elif intent == INTENT_RW_GLOBAL_HOLIDAY or intent == INTENT_RW_HOLIDAY:
        await _w(bot, user.id, get_global_holiday_reply(text))

    else:
        # CURRENT_INFO, SENSITIVE, TRANSLATION, MATH, GENERAL, GLOBAL, UNKNOWN
        await _w(bot, user.id, handle_global_question(text, intent))


async def _handle_unknown(bot: "BaseBot", user: "User", text: str = "") -> None:
    # Last-resort: try global knowledge before showing generic message
    if text:
        answer = handle_global_question(text, INTENT_RW_UNKNOWN)
        generic_patterns = ("💬 I'm not sure", "🤔 I don't have", "I can answer")
        if answer and not any(p in answer for p in generic_patterns):
            await _w(bot, user.id, answer[:249])
            return
    await _w(bot, user.id,
             "🤖 I'm ChillTopiaMC AI! I can help with:\n"
             "• Room questions, mining, fishing, casino, events\n"
             "• Science, geography, fun facts, translations, math\n"
             "• Date/time anywhere in the world\n"
             "Say 'ai help' for examples.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def handle_acesinatra(
    bot: "BaseBot", user: "User", message: str,
) -> bool:
    """
    ChillTopiaMC AI assistant handler (internal name kept for registry compatibility).

    Called from on_chat. Returns True if message was claimed.
    Only responds when is_ai_trigger(message) returns True.
    Never intercepts !/slash commands.
    """
    try:
        if not is_ai_trigger(message):
            return False

        # Never intercept slash or bot commands
        if message.strip().startswith("!") or message.strip().startswith("/"):
            return False

        perm  = get_perm_level(user.username)
        clean = strip_ai_trigger(message)

        # ── "ai" alone → welcome ─────────────────────────────────────────────
        if not clean:
            await _w(bot, user.id, get_welcome())
            log_event(user.username, perm, "welcome", "")
            return True

        # ── "ai help" → usage examples ───────────────────────────────────────
        low_clean = clean.lower().strip()
        if low_clean == "help":
            answer = get_public_answer("ai_help") or (
                "Say 'ai [question]'. Examples:\n"
                "ai what should I do next?\n"
                "ai explain Luxe Tickets\n"
                "ai what date is today?\n"
                "ai report bug: fishing broken"
            )
            await _w(bot, user.id, answer[:249])
            log_event(user.username, perm, "help", clean)
            return True

        # ── Hard safety check ────────────────────────────────────────────────
        if is_blocked(clean):
            log_event(user.username, perm, "blocked", clean, outcome="blocked")
            await _w(bot, user.id, blocked_response())
            return True

        # ── Detect intent ────────────────────────────────────────────────────
        intent = detect_intent(clean)

        # ── Permission / knowledge access check ──────────────────────────────
        denial = check_access(intent, perm, clean)
        if denial:
            log_event(user.username, perm, intent, clean, outcome="denied")
            await _w(bot, user.id, denial)
            return True

        log_event(user.username, perm, intent, clean)

        # ── Route intent ─────────────────────────────────────────────────────

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

        elif intent == INTENT_BUG:
            await _handle_bug(bot, user, clean)

        elif intent == INTENT_FEEDBACK:
            await _handle_feedback(bot, user, clean)

        elif intent == INTENT_MOD_HELP:
            await _handle_mod_help(bot, user, clean, perm)

        elif intent == INTENT_PREPARE_SETTING:
            await _handle_prepare_setting(bot, user, clean, perm)

        elif intent in RW_INTENTS:
            await _handle_real_world(bot, user, clean, intent)

        else:
            # Try knowledge access layer for all other intents
            answer = get_knowledge_answer(user, perm, intent, clean)
            if answer:
                await _w(bot, user.id, answer[:249])
            else:
                await _handle_unknown(bot, user, clean)

        return True

    except Exception as err:
        print(f"[AI_ASSISTANT] Error from {user.username}: {err!r}")
        return False
