"""
modules/ai_openai_brain.py — OpenAI-First Brain orchestrator.

Main entry point for the OpenAI-first AI pipeline (OpenAI-First spec).
Called from ai_brain.handle_ai_message after all local fast-paths pass.

Pipeline:
  1. Local safety guard (pre-OpenAI, instant)
  2. Classify intent via OpenAI → structured JSON
  3. Dispatch by type:
       "command" → validate → permission → confirm if risky → execute
       "answer"  → apply Luxe billing → send reply
       "clarify" → ask clarifying question (free)
       "refuse"  → send refusal (free)
  4. Log everything

Never evals OpenAI output. Never runs commands based on OpenAI text alone.
All command execution goes through ai_command_executor.execute_command.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from modules.ai_safety_guard      import safety_check
from modules.ai_openai_intent     import classify_intent
from modules.ai_command_validator  import validate_command
from modules.ai_command_executor   import execute_command
from modules.ai_command_mapper     import get_command_config
from modules.ai_command_confirmation import (
    prepare_command, build_prompt, get_pending, clear_pending,
)
from modules.ai_permissions        import perm_label, PERM_ADMIN, PERM_OWNER
from modules.ai_luxe_billing       import (
    is_billing_enabled, estimate_cost, check_can_afford,
    charge_luxe, insufficient_funds_msg, TIER_FREE,
)
from modules.ai_usage_logs         import log_billing, log_llm_call

if TYPE_CHECKING:
    from highrise import BaseBot, User


# ── Internal whisper helper ───────────────────────────────────────────────────

async def _w(bot: "BaseBot", uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, msg[:249])
    except Exception as e:
        print(f"[AI OPENAI BRAIN] whisper_error={e}")


# ── Fallback reply when OpenAI is unavailable ─────────────────────────────────

_FALLBACK = (
    "I couldn't process that right now. "
    "Try a direct command like !balance, !mine, or !help."
)


# ── Main entry point ──────────────────────────────────────────────────────────

async def handle_openai_brain(
    bot:  "BaseBot",
    user: "User",
    text: str,
    perm: str,
) -> None:
    """
    OpenAI-first pipeline for all natural language AI requests.
    Safe: never evals, never runs unknown commands, never trusts OpenAI output blindly.
    """

    # ── 1. Local safety guard (fast, no API call) ─────────────────────────────
    blocked, refusal = safety_check(text)
    if blocked:
        print(f"[AI OPENAI BRAIN] safety_blocked user={user.username!r}")
        await _w(bot, user.id, refusal)
        return

    # ── 2. Resolve role label ─────────────────────────────────────────────────
    role = perm_label(perm)

    # ── 3. Call OpenAI for intent classification ──────────────────────────────
    result = await classify_intent(text, user.username, role)

    if result is None:
        # OpenAI unavailable / bad response — graceful fallback
        print(f"[AI OPENAI BRAIN] openai_failed user={user.username!r} — sending fallback")
        await _w(bot, user.id, _FALLBACK)
        return

    rtype   = result["type"]
    reply   = result.get("reply", "")
    risk    = result.get("risk", "low")
    intent  = result.get("intent", "")

    print(
        f"[AI OPENAI BRAIN] user={user.username!r} perm={perm!r} "
        f"type={rtype!r} intent={intent!r} command={result.get('command')!r} risk={risk!r}"
    )

    # ── 4a. "refuse" — OpenAI itself refused ─────────────────────────────────
    if rtype == "refuse":
        log_billing(user.username, 0, False, "refused")
        await _w(bot, user.id, reply or "I can't help with that.")
        return

    # ── 4b. "clarify" — ask for more info ────────────────────────────────────
    if rtype == "clarify":
        await _w(bot, user.id, reply or "Could you be more specific?")
        return

    # ── 4c. "command" — validate, confirm if risky, then execute ─────────────
    if rtype == "command":
        cmd_key = (result.get("command") or "").lower().strip()
        args    = [str(a) for a in (result.get("args") or [])]
        needs_confirm = bool(result.get("needs_confirmation", False))

        if not cmd_key:
            await _w(bot, user.id, "I detected a command but couldn't identify which one. Try the direct command.")
            return

        # Validate against local whitelist + permission
        valid, error_msg = validate_command(cmd_key, args, perm, user.username)
        if not valid:
            print(f"[AI COMMAND] cmd={cmd_key!r} validation_failed reason={error_msg!r}")
            await _w(bot, user.id, error_msg[:249])
            return

        # Override needs_confirmation from config if stricter
        cfg = get_command_config(cmd_key)
        if cfg and cfg.get("requires_confirmation"):
            needs_confirm = True

        # Check economy lock for confirmation display note
        economy_locked = False
        try:
            import database as db
            economy_locked = db.get_room_setting("economy_lock", "off") == "on"
        except Exception:
            pass

        if needs_confirm or risk in ("medium", "high"):
            # Store pending command and show confirmation prompt
            prepare_command(
                user_id    = user.id,
                command    = cmd_key,
                args       = args,
                risk       = risk.capitalize(),
                perm_label = role,
                economy    = economy_locked,
            )
            prompt = build_prompt(cmd_key, args, risk.capitalize(), role, economy_locked)
            print(f"[AI COMMAND] cmd={cmd_key!r} confirmation_required=true")
            await _w(bot, user.id, prompt)
            return

        # Direct-execute (low-risk, no confirmation needed)
        print(
            f"[AI COMMAND] mapped={cmd_key!r} permission_ok=true "
            f"confirmation_required=false executed=true"
        )
        executed = await execute_command(bot, user, cmd_key, args)
        if not executed:
            await _w(bot, user.id, f"I mapped to !{cmd_key} but couldn't run it. Try the direct command.")
        elif reply:
            # Send OpenAI's short acknowledgement (e.g. "Sure, mining now!")
            await _w(bot, user.id, reply[:249])
        return

    # ── 4d. "answer" — general question, apply billing then send reply ────────
    if rtype == "answer":
        if not reply:
            await _w(bot, user.id, "I don't have an answer for that. Try being more specific.")
            return

        billing_on = is_billing_enabled()
        is_live    = "live" in intent.lower() or risk == "high"
        cost       = estimate_cost(text, intent, is_live) if billing_on else TIER_FREE

        if billing_on and cost > TIER_FREE:
            can_afford, balance = check_can_afford(user.id, cost)
            if not can_afford:
                log_billing(user.username, cost, False, "insufficient_funds")
                await _w(bot, user.id, insufficient_funds_msg(cost, balance))
                return

            charge_luxe(user.id, user.username, cost)
            log_billing(user.username, cost, True)
        else:
            log_billing(user.username, 0, True, "free")

        log_llm_call(user.username, intent, True)
        await _w(bot, user.id, reply[:249])
        return

    # ── Unknown type (shouldn't happen after validation in classify_intent) ───
    print(f"[AI OPENAI BRAIN] unknown_type={rtype!r} — sending fallback")
    await _w(bot, user.id, _FALLBACK)
