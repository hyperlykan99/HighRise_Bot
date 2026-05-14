"""
modules/ai_brain.py — ChillTopiaMC AI Brain (3.3B).

Central orchestrator for all AI requests.  Called from handle_acesinatra().

Pipeline per request:
  1. Host lock  — only ChillTopiaMC answers AI messages
  2. Rate limit — per-user sliding-window + duplicate suppression
  3. Abuse guard — prompt-injection / permission-bypass detection
  4. Permission level resolution
  5. Trigger stripping + context resolution from short-term memory
  6. Intent detection
  7. Permission / access check
  8. Reply channel selection (public / whisper via reply-mode)
  9. Handler dispatch
 10. Short-term memory update
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

import database as db

if TYPE_CHECKING:
    from highrise import BaseBot, User

# ── New 3.3B modules ─────────────────────────────────────────────────────────
from modules.ai_host_lock       import is_ai_host_bot
from modules.ai_rate_limiter    import check_rate_limit
from modules.ai_abuse_guard     import check_abuse
from modules.ai_memory_short_term import (
    get_context_hint, clear_memory,
)
from modules.ai_context_manager import resolve_context, record_interaction
from modules.ai_reply_mode      import (
    get_reply_mode, set_reply_mode, choose_reply_channel,
)
from modules.ai_personalized_guidance import (
    get_personalized_guidance, summarize_progress,
)
from modules.ai_reasoning_templates import (
    AI_STATUS, AI_HELP, AI_WELCOME,
    REPLY_MODE_VIEW, REPLY_MODE_OWNER_ONLY, REPLY_MODE_SAME, REPLY_MODE_DONE,
    DEBUG_TEMPLATE, UNKNOWN_FALLBACK,
)
from modules.ai_rate_limiter    import get_status as _rl_status
from modules.ai_context_manager import active_memory_count

# ── Existing AI modules ───────────────────────────────────────────────────────
from modules.ai_permissions import (
    get_perm_level, requires_admin, requires_staff,
    perm_label,
    PERM_STAFF, PERM_ADMIN, PERM_OWNER,
)
from modules.ai_intent_router import (
    detect_intent, RW_INTENTS,
    INTENT_DATE_TIME, INTENT_HOLIDAY,
    INTENT_PLAYER_GUIDANCE, INTENT_PERSONALIZED_GUIDANCE,
    INTENT_CMD_EXPLAIN,
    INTENT_LUXE, INTENT_CHILLCOINS, INTENT_MINING, INTENT_FISHING,
    INTENT_CASINO, INTENT_EVENT, INTENT_VIP,
    INTENT_BUG, INTENT_FEEDBACK, INTENT_SUMMARIZE_BUGS,
    INTENT_MOD_HELP, INTENT_STAFF_INFO, INTENT_ADMIN_INFO, INTENT_OWNER_INFO,
    INTENT_PREPARE_SETTING, INTENT_CONFIRM_SETTING, INTENT_CANCEL_SETTING,
    INTENT_PRIVATE_PLAYER_INFO, INTENT_GENERAL, INTENT_UNKNOWN,
    INTENT_AI_STATUS, INTENT_AI_DEBUG,
    INTENT_AI_REPLY_MODE_VIEW, INTENT_AI_REPLY_MODE_SET,
    INTENT_RW_GLOBAL_TIME, INTENT_RW_GLOBAL_HOLIDAY,
    INTENT_RW_DATETIME, INTENT_RW_HOLIDAY,
    INTENT_RW_SENSITIVE, INTENT_RW_CURRENT_INFO,
    INTENT_RW_TRANSLATION, INTENT_RW_MATH,
    INTENT_RW_GENERAL, INTENT_RW_GLOBAL, INTENT_RW_UNKNOWN,
    # 3.3B identity + translation intents
    INTENT_USER_NAME, INTENT_USER_ROLE, INTENT_TRANSLATION,
    # 3.3E natural-language action intents
    INTENT_TELEPORT_SELF, INTENT_VAGUE_FOLLOWUP,
    # 3.3F AI Command Control Layer
    INTENT_AI_COMMAND, INTENT_AI_CMD_HELP,
)
from modules.ai_translation  import get_translation
from modules.ai_live_router  import handle_live_question, is_live_question, detect_live_type
from modules.ai_llm_fallback import try_llm_answer, ask_openai_short, llm_status
from modules.ai_global_time      import get_global_time_reply
from modules.ai_global_holidays  import get_global_holiday_reply
from modules.ai_global_knowledge import handle_global_question
from modules.ai_knowledge_access import get_knowledge_answer, check_access
from modules.ai_public_knowledge import get_public_answer, get_welcome
from modules.ai_time_holidays    import get_date_reply, get_time_reply, get_next_holiday_reply
from modules.ai_safety           import is_blocked, blocked_response
from modules.ai_confirmation_manager import (
    set_pending, get_pending, clear_pending, preview_message,
    is_simple_confirm, is_simple_cancel,
)
from modules.ai_action_executor  import execute_action
from modules.ai_logs             import log_event
from modules.ai_command_router   import (
    handle_ai_command, handle_ai_cmd_help, is_confirm_or_cancel,
)
from modules.ai_send             import ai_send as _ai_send_impl, ai_whisper as _ai_whisper_impl
from modules.ai_cost_preview     import (
    get_live_pending, clear_live_pending, cost_info_message,
    is_cost_preview_required, set_cost_preview_required,
    cost_preview_status_msg, get_basic_pending, clear_basic_pending,
)
from modules.ai_human_brain      import ask_human_brain
from modules.ai_openai_brain     import handle_openai_brain


# ── Trigger helpers (kept here to avoid circular imports) ────────────────────

def is_ai_trigger(message: str) -> bool:
    n = message.strip().lower()
    return (
        n == "ai"
        or n.startswith("ai ")
        or n.startswith("ai,")
        or n.startswith("ai:")
        or n.startswith("ai?")
        or n.startswith("ai!")
        or n.startswith("@chilltopiamc")
        or n.startswith("chilltopiamc")
        or n.startswith("chilltopia ")
        or n.startswith("assistant ")
        or n.startswith("bot ")
    )


def strip_ai_trigger(message: str) -> str:
    s = message.strip()
    low = s.lower()
    for prefix in ("@chilltopiamc", "chilltopiamc", "chilltopia",
                   "assistant", "bot", "ai"):
        if low.startswith(prefix):
            rest = s[len(prefix):]
            rest = re.sub(r"^[\s,!:?]+", "", rest)
            return rest.strip()
    return s


# ── 3.3E Alias normalization ──────────────────────────────────────────────────
# Applied BEFORE intent detection so natural shorthand phrases reach the right
# handler. Substitutions are word-boundary safe so they don't mangle unrelated
# words (e.g. "typical" is not changed by the "tp" rule).
_ALIAS_SUBS: list[tuple[re.Pattern, str]] = [
    # Teleport shorthands
    (re.compile(r"\btele\b", re.I),         "teleport"),
    (re.compile(r"\btp\b(?=\s|$)", re.I),   "teleport"),
    # Currency common-names → ISO codes (helps exchange-rate extraction)
    (re.compile(r"\bpesos?\b", re.I),       "PHP"),
    (re.compile(r"\bdollars?\b", re.I),     "USD"),
    (re.compile(r"\beuros?\b", re.I),       "EUR"),
    (re.compile(r"\byens?\b", re.I),        "JPY"),
    (re.compile(r"\bpounds?\b", re.I),      "GBP"),
    # In-game currency shorthands
    (re.compile(r"\btix\b", re.I),                    "Luxe Tickets"),
    (re.compile(r"\bluxe\s+coins?\b", re.I),          "Luxe Tickets"),
    (re.compile(r"\blux\s+tickets?\b", re.I),         "Luxe Tickets"),
]


def _normalize_aliases(text: str) -> str:
    """Replace common shorthand/typo aliases with canonical forms (3.3E)."""
    for pat, repl in _ALIAS_SUBS:
        text = pat.sub(repl, text)
    return text


# ── Send helpers ─────────────────────────────────────────────────────────────

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    """Always-whisper helper for private / sensitive data."""
    await _ai_whisper_impl(bot, uid, msg)


async def _send(
    bot:              "BaseBot",
    user:             "User",
    message:          str,
    response_type:    str  = "general",
    knowledge_level:  str  = "public",
    contains_private: bool = False,
) -> None:
    """
    Route reply through public chat or whisper via ai_send.py.
    Reply mode, safety overrides, and debug logging are handled there.
    """
    await _ai_send_impl(bot, user, message, response_type, knowledge_level, contains_private)


# ── Report helper ─────────────────────────────────────────────────────────────

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


# ── Intent handlers ───────────────────────────────────────────────────────────

async def _handle_date_time(bot, user, text):
    low = text.lower()
    if "holiday" in low:
        reply = get_next_holiday_reply()
    elif "time" in low and "date" not in low and "day" not in low:
        reply = get_time_reply()
    else:
        reply = get_date_reply()
    await _send(bot, user, reply, "general")


async def _handle_holiday(bot, user):
    await _send(bot, user, get_next_holiday_reply(), "general")


async def _handle_player_guidance(bot, user, text):
    """Generic guidance — uses personalized context for richer advice."""
    low = text.lower()
    if any(k in low for k in ("summarize", "summary", "progress", "how am i doing")):
        msg = summarize_progress(user.id)
    else:
        msg = get_personalized_guidance(user.id, user.username)
    await _w(bot, user.id, msg)


async def _handle_personalized_guidance(bot, user, text):
    """Explicit personalized guidance for 'what can I afford?', 'what should I grind?'."""
    low = text.lower()
    if any(k in low for k in ("summarize", "summary", "progress", "how am i doing")):
        msg = summarize_progress(user.id)
    else:
        msg = get_personalized_guidance(user.id, user.username)
    await _w(bot, user.id, msg)


async def _handle_bug(bot, user, text):
    saved = _save_report(user.id, user.username, text, "bug")
    if saved:
        await _w(bot, user.id,
                 "🐛 Bug report saved — thank you!\n"
                 "Staff will review it. Use !bug to add more detail.")
    else:
        await _w(bot, user.id,
                 "🐛 Noted! Use !bug to file it properly so it's tracked.")


async def _handle_feedback(bot, user, text):
    saved = _save_report(user.id, user.username, text, "feedback")
    if saved:
        await _w(bot, user.id,
                 "💬 Feedback saved — thank you for helping improve ChillTopia!")
    else:
        await _w(bot, user.id,
                 "💬 Thanks! Use !feedback to make sure it's recorded.")


async def _handle_mod_help(bot, user, text, perm):
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


async def _handle_prepare_setting(bot, user, text, perm):
    if not requires_admin(perm):
        await _w(bot, user.id, "🔒 Admin or owner only for setting changes.")
        return

    m = re.search(r"vip\s+price\s+to\s+([\d,]+)", text, re.I)
    if m:
        val = m.group(1).replace(",", "")
        try:
            current = db.get_room_setting("vip_price", "unknown")
        except Exception:
            current = "unknown"
        set_pending(user_id=user.id, action_key="set_vip_price",
                    label="VIP Price", confirm_phrase="CONFIRM VIP PRICE",
                    current_value=f"{current} 🎫", new_value=f"{val} 🎫 Luxe Tickets",
                    risk="Economy-impacting")
        p = get_pending(user.id)
        if p:
            await _w(bot, user.id, preview_message(p))
        return

    m2 = re.search(
        r"start\s+(\w+(?:\s+\w+)?)\s+(?:for\s+)?(?:(\d+)\s*(hour|min|minute))?",
        text, re.I,
    )
    if m2:
        ev_name = m2.group(1).strip().lower().replace(" ", "_")
        duration_str = ""
        if m2.group(2):
            unit = m2.group(3).lower()
            mins = int(m2.group(2)) * 60 if "hour" in unit else int(m2.group(2))
            duration_str = f" for {mins} minutes"
        set_pending(user_id=user.id, action_key="start_event",
                    label=f"Start Event: {ev_name}{duration_str}",
                    confirm_phrase=f"CONFIRM START {ev_name.upper()}",
                    current_value="No active event", new_value=ev_name,
                    risk="Room-wide impact")
        p = get_pending(user.id)
        if p:
            await _w(bot, user.id, preview_message(p))
        return

    m3 = re.search(r"event\s+duration\s+to\s+([\d]+)\s*(min|minute|hour)?", text, re.I)
    if m3:
        val  = m3.group(1)
        unit = (m3.group(2) or "min").lower()
        mins = int(val) * 60 if "hour" in unit else int(val)
        try:
            current = db.get_room_setting("default_event_duration", "60")
        except Exception:
            current = "60"
        set_pending(user_id=user.id, action_key="set_event_duration",
                    label="Default Event Duration",
                    confirm_phrase=f"CONFIRM EVENT DURATION {mins}",
                    current_value=f"{current} min", new_value=f"{mins} minutes",
                    risk="Room setting change")
        p = get_pending(user.id)
        if p:
            await _w(bot, user.id, preview_message(p))
        return

    await _w(bot, user.id,
             "⚙️ Recognized a setting change but couldn't parse it.\n"
             "Try: 'ai set VIP price to 600 tickets'\n"
             "or 'ai start mining_rush for 1 hour'")


async def _handle_confirm(bot, user, text, perm):
    p = get_pending(user.id)
    if not p:
        await _w(bot, user.id,
                 "⚠️ You don't have a pending AI action to confirm."
                 " (It may have expired after 60 seconds.)")
        return
    phrase  = text.upper().strip()
    simple  = is_simple_confirm(text)
    if not simple and p["confirm_phrase"] not in phrase:
        await _w(bot, user.id,
                 f"⚠️ Wrong phrase. Reply confirm, or: {p['confirm_phrase']}")
        return
    if not requires_admin(perm):
        await _w(bot, user.id, "🔒 Admin or owner only.")
        clear_pending(user.id)
        return

    # ── Special: AI reply mode change ────────────────────────────────────────
    if p["action_key"] == "set_ai_reply_mode":
        success = set_reply_mode(p["new_value"])
        clear_pending(user.id)
        if success:
            await _w(bot, user.id, REPLY_MODE_DONE.format(mode=p["new_value"]))
        else:
            await _w(bot, user.id, "❌ Failed to update AI reply mode.")
        return

    # ── Special: AI cost preview setting (owner only) ────────────────────────
    if p["action_key"] == "set_ai_cost_preview":
        if perm != PERM_OWNER:
            await _w(bot, user.id, "\U0001f512 AI cost preview setting is owner only.")
            clear_pending(user.id)
            return
        val = p["new_value"]
        set_cost_preview_required(val == "on")
        clear_pending(user.id)
        print(f"[AI COST PREVIEW] saved={val}")
        await _w(bot, user.id, f"\u2705 AI cost preview is now {val.upper()}.")
        return

    # ── Default action execution ──────────────────────────────────────────────
    result = await execute_action(bot, user.id, p["action_key"], p["new_value"], user.username)
    clear_pending(user.id)
    log_event(user.username, perm, "confirmed_action", text,
              action=p["action_key"], outcome="executed")
    await _w(bot, user.id, result)


async def _handle_cancel(bot, user):
    p = get_pending(user.id)
    if p:
        clear_pending(user.id)
        await _w(bot, user.id, "\u274c Pending change cancelled.")
    else:
        await _w(bot, user.id, "\u26a0\ufe0f Nothing to cancel.")


async def _handle_live_confirm(bot, user, live_p: dict, perm: str) -> None:
    """Execute a stored 5\U0001f3ab live AI query after the user confirmed."""
    from modules.ai_cost_preview import clear_live_pending
    from modules.ai_luxe_billing import (
        is_billing_enabled, check_can_afford, charge_luxe, insufficient_funds_msg,
    )
    from modules.ai_usage_logs import log_billing
    clear_live_pending(user.id)
    query = live_p["query"]
    cost  = live_p["cost"]
    if is_billing_enabled() and cost > 0:
        can_afford, balance = check_can_afford(user.id, cost)
        if not can_afford:
            await _w(bot, user.id, insufficient_funds_msg(cost, balance))
            return
        charge_luxe(user.id, user.username, cost)
        log_billing(user.username, cost, True)
        print(f"[AI BILLING] live_confirmed charged=true cost={cost} user={user.username!r}")
    await handle_openai_brain(bot, user, query, perm, skip_live_check=True)


async def _handle_basic_confirm(bot, user, basic_p: dict, perm: str) -> None:
    """Execute a stored 1–3\U0001f3ab query after the user confirmed (cost preview ON)."""
    from modules.ai_cost_preview import clear_basic_pending
    from modules.ai_luxe_billing import (
        is_billing_enabled, check_can_afford, charge_luxe, insufficient_funds_msg,
    )
    from modules.ai_usage_logs import log_billing
    clear_basic_pending(user.id)
    query = basic_p["query"]
    cost  = basic_p["cost"]
    print(f"[AI COST PREVIEW] basic_confirmed cost={cost} user={user.username!r}")
    if is_billing_enabled() and cost > 0:
        can_afford, balance = check_can_afford(user.id, cost)
        if not can_afford:
            await _w(bot, user.id, insufficient_funds_msg(cost, balance))
            return
        charge_luxe(user.id, user.username, cost)
        log_billing(user.username, cost, True)
        print(f"[AI BILLING] basic_confirmed charged=true cost={cost} user={user.username!r}")
    await handle_openai_brain(bot, user, query, perm, skip_live_check=True)


async def _handle_real_world(bot, user, text, intent, perm=0):
    """Route real-world intents — sensitive always whispers, others use reply mode."""
    # Live/current info: route through the live router (3.3D)
    if intent == INTENT_RW_CURRENT_INFO:
        print(f"[AI LIVE] routing live question: {text!r}")
        reply = await handle_live_question(user, text, perm)
        await _send(bot, user, reply[:249], "general")
        return

    if intent == INTENT_RW_SENSITIVE:
        reply = handle_global_question(text, intent)
        await _w(bot, user.id, reply[:249])
        return

    if intent in (INTENT_RW_GLOBAL_TIME, INTENT_RW_DATETIME):
        reply = get_global_time_reply(text)
    elif intent in (INTENT_RW_GLOBAL_HOLIDAY, INTENT_RW_HOLIDAY):
        reply = get_global_holiday_reply(text)
    else:
        reply = handle_global_question(text, intent)

    await _send(bot, user, reply[:249], "general")


async def _handle_ai_status(bot, user):
    await _send(bot, user, AI_STATUS, "general")


async def _handle_ai_debug(bot, user, perm):
    if perm != PERM_OWNER:
        await _w(bot, user.id, "🔒 AI debug summary is owner only.")
        return
    from modules.ai_host_lock import AI_HOST_BOT_NAME
    rl  = _rl_status()
    mem = active_memory_count()
    mode = get_reply_mode()
    msg = DEBUG_TEMPLATE.format(
        host        = AI_HOST_BOT_NAME,
        reply_mode  = mode,
        rate_users  = rl["tracked_users"],
        memory      = mem,
        pending     = 0,
    )
    await _w(bot, user.id, msg[:249])


async def _handle_ai_reply_mode_view(bot, user, perm):
    mode = get_reply_mode()
    msg  = REPLY_MODE_VIEW.format(mode=mode)
    await _send(bot, user, msg, "general")


async def _handle_ai_reply_mode_set(bot, user, text, perm):
    if perm != PERM_OWNER:
        await _w(bot, user.id, REPLY_MODE_OWNER_ONLY)
        return

    low  = text.lower()
    mode = None
    if "public" in low:
        mode = "public"
    elif "whisper" in low:
        mode = "whisper"
    elif "smart" in low:
        mode = "smart"

    if not mode:
        await _w(bot, user.id, "Which AI reply mode? Choose: public, whisper, or smart.")
        return

    current = get_reply_mode()
    if current == mode:
        await _w(bot, user.id, REPLY_MODE_SAME.format(mode=mode))
        return

    set_pending(
        user_id       = user.id,
        action_key    = "set_ai_reply_mode",
        label         = "AI Reply Mode",
        confirm_phrase= "CONFIRM AI REPLY MODE",
        current_value = current,
        new_value     = mode,
        risk          = "Medium — changes how AI replies appear in room",
    )
    p = get_pending(user.id)
    if p:
        await _w(bot, user.id, preview_message(p))


async def _handle_ai_cost_preview_setting(bot, user, text: str, perm: str) -> None:
    """Handle 'ai cost preview ...' locally — owner to change, anyone to view status."""
    low = text.lower()

    # ── Status query — no owner check needed ─────────────────────────────────
    is_change = any(kw in low for kw in (" on", " off", "enable", "disable", "turn"))
    if not is_change or "status" in low:
        await _send(bot, user, cost_preview_status_msg(), "general")
        return

    # ── Change — owner only ───────────────────────────────────────────────────
    if perm != PERM_OWNER:
        await _w(bot, user.id, "\U0001f512 AI cost preview setting is owner only.")
        return

    new_val: str | None = None
    if any(kw in low for kw in ("enable", " on", ":on", "=on")):
        new_val = "on"
    elif any(kw in low for kw in ("disable", " off", ":off", "=off")):
        new_val = "off"

    if not new_val:
        await _w(bot, user.id, cost_preview_status_msg())
        return

    current = "on" if is_cost_preview_required() else "off"
    print(f"[AI COST PREVIEW] requested={new_val}")
    print(f"[AI COST PREVIEW] current={current}")

    if current == new_val:
        await _w(bot, user.id, f"AI cost preview is already {new_val.upper()}.")
        return

    effect = (
        "Paid AI answers will show cost before charging."
        if new_val == "on" else
        "1\u20133 \U0001f3ab answers can auto-charge after successful answer."
    )
    set_pending(
        user_id        = user.id,
        action_key     = "set_ai_cost_preview",
        label          = "AI Cost Preview",
        confirm_phrase = "CONFIRM AI COST PREVIEW",
        current_value  = current,
        new_value      = new_val,
        risk           = f"Medium \u2014 {effect}",
    )
    p = get_pending(user.id)
    if p:
        print(f"[AI COST PREVIEW] pending_confirmation=true")
        await _w(bot, user.id, preview_message(p))


async def _handle_role(bot, user):
    """Return the sender's role/permission level."""
    perm  = get_perm_level(user.username)
    label = perm_label(perm)
    print(f"[AI DEBUG] permission_level={perm!r} role_label={label!r}")
    msg = f"Your current role is {label}."
    await _send(bot, user, msg, "general")


async def _handle_translation(bot, user, text):
    """Answer translation questions; falls back to OpenAI for unknown words."""
    print(f"[AI DEBUG] detected_intent=translation_question source_text={text!r}")
    reply = get_translation(text)
    if reply:
        await _send(bot, user, reply[:249], "general")
        return
    # Local dictionary doesn't have this word — try OpenAI (with billing)
    perm = get_perm_level(user.username)
    llm = await ask_human_brain(text, user.username, user.id, role=perm_label(perm), intent="translation_question")
    if llm:
        await _send(bot, user, llm[:249], "general")
        return
    await _send(
        bot, user,
        "I can translate hello, thank you, good morning, goodbye, love, friend "
        "in Spanish, Tagalog, Japanese, French, Korean. Try: 'ai translate hello to spanish'.",
        "general",
    )


async def _handle_teleport_self(bot: "BaseBot", user: "User", text: str) -> None:
    """AI-triggered self-teleport to a named spawn (3.3E)."""
    from modules.room_utils import ai_teleport_to_spawn
    # Extract spawn name: last word(s) after "to/at/into"
    import re as _re
    m = _re.search(
        r"\b(?:to|at|into|toward)\s+(?:the\s+)?([a-z][\w\s]{0,25})\s*$",
        text, _re.I,
    )
    spawn = m.group(1).strip().lower() if m else ""
    # Clean trailing noise words
    for noise in ("room", "area", "spot", "zone", "place"):
        if spawn.endswith(f" {noise}"):
            spawn = spawn[: -(len(noise) + 1)].strip()
    print(f"[AI DEBUG] intent=teleport_self target_spawn={spawn!r}")
    if not spawn:
        await _w(bot, user.id,
                 "Which spot? Try: 'ai tele me to mod'. See all spots: !spawns")
        return
    await ai_teleport_to_spawn(bot, user, spawn)


async def _handle_vague_followup(bot: "BaseBot", user: "User") -> None:
    """Resolve vague action phrases using short-term memory (3.3E)."""
    from modules.ai_memory_short_term import get_memory
    mem = get_memory(user.id)
    last_intent = mem.last_intent or ""
    last_topic  = mem.last_topic  or ""
    if "reply_mode" in last_intent or "reply mode" in last_topic.lower():
        reply = "Switch AI reply to which mode: public, whisper, or smart?"
    elif "teleport" in last_intent or "teleport spots" in last_topic.lower():
        reply = "Which spot should I teleport you to? Use !spawns to see all."
    elif "event" in last_intent or "event" in last_topic.lower():
        reply = "Which event setting do you want to switch?"
    elif "mining" in last_intent or "mining" in last_topic.lower():
        reply = "What mining setting do you want to switch?"
    elif last_topic:
        reply = f"What do you want to switch about {last_topic}?"
    else:
        reply = "What would you like me to switch or change?"
    print(f"[AI DEBUG] intent=vague_followup last_intent={last_intent!r} last_topic={last_topic!r}")
    await _send(bot, user, reply, "general")


async def _handle_unknown(bot, user, text="", intent=None, perm=0):
    # 1. Try OpenAI first — free, fast, and handles most general questions
    if text and intent is not None:
        llm_reply = await try_llm_answer(user, text, intent, perm)
        if llm_reply:
            await _send(bot, user, llm_reply[:249], "general")
            return

    # 2. Fallback: rule-based global knowledge (no internet needed)
    if text:
        answer = handle_global_question(text, INTENT_RW_UNKNOWN)
        _generic = ("💬 I'm not sure", "🤔 I don't have", "I can answer")
        if answer and not any(s in answer for s in _generic):
            await _send(bot, user, answer[:249], "general")
            return

    # 3. Canned last-resort — nothing could answer
    await _send(
        bot, user,
        "AI fallback is unavailable right now. Try again later or type 'ai help'.",
        "general",
    )


# ── Dispatch ──────────────────────────────────────────────────────────────────

async def _dispatch(
    bot:     "BaseBot",
    user:    "User",
    text:    str,
    intent:  str,
    perm:    str,
) -> None:
    if intent == INTENT_CANCEL_SETTING:
        await _handle_cancel(bot, user)
    elif intent == INTENT_CONFIRM_SETTING:
        await _handle_confirm(bot, user, text, perm)
    elif intent == INTENT_AI_STATUS:
        await _handle_ai_status(bot, user)
    elif intent == INTENT_AI_DEBUG:
        await _handle_ai_debug(bot, user, perm)
    elif intent == INTENT_AI_REPLY_MODE_VIEW:
        await _handle_ai_reply_mode_view(bot, user, perm)
    elif intent == INTENT_AI_REPLY_MODE_SET:
        await _handle_ai_reply_mode_set(bot, user, text, perm)
    elif intent == INTENT_USER_NAME:
        name_reply = f"Your name is {user.username}."
        print(f"[AI DEBUG] detected_intent=user_name_question username={user.username!r}")
        await _send(bot, user, name_reply, "general")
    elif intent == INTENT_USER_ROLE:
        await _handle_role(bot, user)
    elif intent == INTENT_TRANSLATION:
        await _handle_translation(bot, user, text)
    elif intent == INTENT_HOLIDAY:
        await _handle_holiday(bot, user)
    elif intent == INTENT_DATE_TIME:
        await _handle_date_time(bot, user, text)
    elif intent in (INTENT_PLAYER_GUIDANCE, INTENT_PERSONALIZED_GUIDANCE):
        await _handle_player_guidance(bot, user, text)
    elif intent == INTENT_BUG:
        await _handle_bug(bot, user, text)
    elif intent == INTENT_FEEDBACK:
        await _handle_feedback(bot, user, text)
    elif intent == INTENT_MOD_HELP:
        await _handle_mod_help(bot, user, text, perm)
    elif intent == INTENT_PREPARE_SETTING:
        await _handle_prepare_setting(bot, user, text, perm)
    elif intent == INTENT_TELEPORT_SELF:
        await _handle_teleport_self(bot, user, text)
    elif intent == INTENT_VAGUE_FOLLOWUP:
        await _handle_vague_followup(bot, user)
    elif intent == INTENT_AI_COMMAND:
        await handle_ai_command(bot, user, text, perm)
    elif intent == INTENT_AI_CMD_HELP:
        await handle_ai_cmd_help(bot, user)
    elif intent in RW_INTENTS:
        await _handle_real_world(bot, user, text, intent, perm)
    else:
        # Knowledge access layer (staff/admin/owner info, topic explanations)
        answer = get_knowledge_answer(user, perm, intent, text)
        if answer:
            kl = "public"
            priv = False
            if intent in (INTENT_PRIVATE_PLAYER_INFO, INTENT_STAFF_INFO,
                          INTENT_ADMIN_INFO, INTENT_OWNER_INFO):
                kl, priv = "staff", True
            await _send(bot, user, answer[:249], "general", kl, priv)
        else:
            await _handle_unknown(bot, user, text, intent, perm)


# ── Main entry point ──────────────────────────────────────────────────────────

async def handle_ai_message(
    bot:     "BaseBot",
    user:    "User",
    message: str,
) -> bool:
    """
    Main AI orchestrator (3.3B).  Called from handle_acesinatra().
    Returns True when the message is consumed (even if silently).
    """
    raw_msg   = message.strip() if message else ""
    normalized = raw_msg.lower()

    # ── Debug: received ───────────────────────────────────────────────────────
    print(f"[AI DEBUG] received sender={user.username} raw={raw_msg!r} normalized={normalized!r}")

    try:
        # ── 1. Host lock ──────────────────────────────────────────────────────
        host = is_ai_host_bot(debug=True)
        print(f"[AI DEBUG] is_host={host}")

        if not host:
            print(f"[AI DEBUG] ignored — not host bot")
            return True  # Consume silently — other bots ignore AI triggers

        # ── 2. Rate limit ─────────────────────────────────────────────────────
        rl = check_rate_limit(user.id, raw_msg)
        if rl == "duplicate":
            print(f"[AI DEBUG] ignored — duplicate message")
            return True
        if rl:
            print(f"[AI DEBUG] rate-limited sender={user.username}")
            await _w(bot, user.id, rl)
            return True

        # ── 3. Abuse guard ────────────────────────────────────────────────────
        abuse = check_abuse(raw_msg)
        if abuse:
            print(f"[AI DEBUG] abuse blocked sender={user.username}")
            await _w(bot, user.id, abuse)
            return True

        # ── 4. Permission + clean text ────────────────────────────────────────
        perm  = get_perm_level(user.username)
        clean = strip_ai_trigger(raw_msg)

        # ── 4b. Alias normalization (tele→teleport, pesos→PHP, etc.) ─────────
        clean = _normalize_aliases(clean)

        # ── 5. Context resolution (resolves "more" → last topic) ──────────────
        resolved = resolve_context(user.id, clean)

        # ── 6. "ai" alone → welcome ───────────────────────────────────────────
        if not clean:
            reply_mode = get_reply_mode()
            print(f"[AI DEBUG] handler_called=True intent=welcome reply_mode={reply_mode}")
            await _send(bot, user, get_welcome(), "general")
            log_event(user.username, perm, "welcome", "")
            return True

        low = resolved.lower().strip()

        # ── 7. "ai help" ──────────────────────────────────────────────────────
        if low in ("help", "commands", "what can you do", "what do you do"):
            reply_mode = get_reply_mode()
            print(f"[AI DEBUG] handler_called=True intent=help reply_mode={reply_mode}")
            await _send(bot, user, AI_HELP, "general")
            log_event(user.username, perm, "help", clean)
            return True

        # ── 7b. Quick-path: "what's my name" / "what is my name" ─────────────
        if ("what" in low or "whats" in low) and "name" in low and (
            "my" in low or "am i" in low
        ):
            name_reply = f"Your name is {user.username}."
            print(f"[AI DEBUG] handler_called=True intent=name_query")
            await _send(bot, user, name_reply, "general")
            return True

        # ── 7c. Pending confirmation / cancellation (both systems) ───────────
        # System A: AI command executor pending (ai_command_confirmation)
        # is_confirm/is_cancel now also accept simple words (confirm/yes/y/cancel/no/n)
        if await is_confirm_or_cancel(bot, user, resolved):
            return True

        # System B: Settings change pending (ai_confirmation_manager)
        # Catches simple words AND exact long phrases like "CONFIRM VIP PRICE"
        _mgr_p = get_pending(user.id)
        if _mgr_p:
            _up = resolved.strip().upper()
            _phrase_match = _mgr_p["confirm_phrase"] in _up
            _cancel_match = is_simple_cancel(resolved) or _up == "CANCEL"
            if is_simple_confirm(resolved) or _phrase_match:
                await _handle_confirm(bot, user, resolved, perm)
                return True
            if _cancel_match:
                await _handle_cancel(bot, user)
                return True

        # System C: Live AI query pending (5🎫 live queries, ai_cost_preview)
        _live_p = get_live_pending(user.id)
        if _live_p:
            if is_simple_confirm(resolved):
                await _handle_live_confirm(bot, user, _live_p, perm)
                return True
            if is_simple_cancel(resolved):
                clear_live_pending(user.id)
                await _w(bot, user.id, "\u274c Live AI query cancelled.")
                return True

        # System D: Basic query pending (1–3🎫 when AI cost preview is ON)
        _basic_p = get_basic_pending(user.id)
        if _basic_p:
            if is_simple_confirm(resolved):
                await _handle_basic_confirm(bot, user, _basic_p, perm)
                return True
            if is_simple_cancel(resolved):
                clear_basic_pending(user.id)
                await _w(bot, user.id, "\u274c AI query cancelled.")
                return True

        # ── 7d. AI cost info fast-path ─────────────────────────────────────────
        _r_low = resolved.strip().lower()
        if _r_low in {
            "cost", "prices", "price",
            "ai cost", "ai prices",
            "how much", "how much does ai cost",
        }:
            await _send(bot, user, cost_info_message(), "general")
            return True

        # ── 7e. AI cost preview setting fast-path ──────────────────────────────
        if any(
            kw in _r_low
            for kw in ("cost preview", "ai cost preview", "ticket preview", "preview cost")
        ):
            await _handle_ai_cost_preview_setting(bot, user, resolved, perm)
            return True

        # ── 8. Hard safety check ──────────────────────────────────────────────
        if is_blocked(resolved):
            log_event(user.username, perm, "blocked", resolved, outcome="blocked")
            await _w(bot, user.id, blocked_response())
            return True

        # ── 9. Local fast-path intents (admin tools — no OpenAI call needed) ────
        #  detect_intent is still used for a narrow set of meta-commands that
        #  don't benefit from OpenAI and are handled entirely by local logic.
        intent = detect_intent(resolved)
        _LOCAL_FAST_PATH = {
            INTENT_AI_STATUS,
            INTENT_AI_DEBUG,
            INTENT_AI_REPLY_MODE_VIEW,
            INTENT_AI_REPLY_MODE_SET,
            INTENT_USER_ROLE,
            INTENT_BUG,
            INTENT_FEEDBACK,
        }
        if intent in _LOCAL_FAST_PATH:
            reply_mode = get_reply_mode()
            print(f"[AI DEBUG] local_fast_path intent={intent!r} permission_level={perm!r}")
            denial = check_access(intent, perm, resolved)
            if denial:
                log_event(user.username, perm, intent, resolved, outcome="denied")
                await _w(bot, user.id, denial)
                return True
            log_event(user.username, perm, intent, resolved)
            await _dispatch(bot, user, resolved, intent, perm)
            record_interaction(user.id, intent, clean)
            return True

        # ── 10. OpenAI-first pipeline for everything else ─────────────────────
        #  OpenAI classifies intent → JSON → local code validates + executes.
        #  Never evals. Never trusts OpenAI output blindly.
        print(f"[AI DEBUG] request={resolved!r}")
        print(f"[AI DEBUG] permission_level={perm!r}")
        log_event(user.username, perm, "openai_pipeline", resolved)

        await handle_openai_brain(bot, user, resolved, perm)

        # ── 11. Update short-term memory ──────────────────────────────────────
        record_interaction(user.id, "openai", clean)

        return True

    except Exception as err:
        print(f"[AI_BRAIN] Error from {user.username}: {err!r}")
        import traceback
        traceback.print_exc()
        # Safe fallback so the player knows we got their message
        try:
            await bot.highrise.send_whisper(
                user.id,
                "🤖 AI had a small hiccup. Try again in a moment!"
            )
        except Exception:
            pass
        return True
