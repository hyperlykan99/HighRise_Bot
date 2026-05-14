"""
modules/ai_llm_fallback.py — OpenAI LLM fallback for unanswered questions (3.3D/3.3E).

Flow:
  1. Only called when all local/rule-based answers have failed.
  2. Skipped entirely for intents that should never reach OpenAI.
  3. Checks OPENAI_API_KEY — if missing, returns "" silently.
  4. Calls OpenAI Responses API with a strict, public-safe prompt.
  5. Returns a short string ≤249 chars, or "" if anything fails.

Model: controlled by OPENAI_MODEL env var (default: gpt-4o-mini).
Note: set OPENAI_MODEL=gpt-5-mini in Replit Secrets when that model is released.
"""
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import User

# ── Config ────────────────────────────────────────────────────────────────────

MODEL    = os.getenv("OPENAI_MODEL", "gpt-5-mini")
_TIMEOUT = 15.0   # seconds before giving up

# ── Intents that must never reach OpenAI ─────────────────────────────────────
# Only intents that are FULLY handled locally or are blocked for safety/privacy.
# INTENT_CMD_EXPLAIN is intentionally NOT here — the regex is too broad and
# catches natural questions like "explain quantum physics".
_SKIP_INTENT_NAMES = frozenset({
    "INTENT_USER_NAME",           # answered locally
    "INTENT_USER_ROLE",           # answered locally
    "INTENT_LUXE",                # game knowledge — answered locally
    "INTENT_CHILLCOINS",          # game knowledge — answered locally
    "INTENT_MINING",              # game knowledge — answered locally
    "INTENT_FISHING",             # game knowledge — answered locally
    "INTENT_CASINO",              # game knowledge — answered locally
    "INTENT_EVENT",               # game knowledge — answered locally
    "INTENT_VIP",                 # game knowledge — answered locally
    "INTENT_AI_REPLY_MODE_VIEW",  # AI system command
    "INTENT_AI_REPLY_MODE_SET",   # AI system command
    "INTENT_AI_STATUS",           # AI system command
    "INTENT_AI_DEBUG",            # AI system command
    "INTENT_CANCEL_SETTING",      # confirmation flow
    "INTENT_CONFIRM_SETTING",     # confirmation flow
    "INTENT_PREPARE_SETTING",     # confirmation flow
    "INTENT_MOD_HELP",            # staff-only local
    "INTENT_STAFF_INFO",          # access-controlled local data
    "INTENT_ADMIN_INFO",          # access-controlled local data
    "INTENT_OWNER_INFO",          # access-controlled local data
    "INTENT_PRIVATE_PLAYER_INFO", # private data — never to OpenAI
    "INTENT_RW_SENSITIVE",        # blocked for safety
    "INTENT_TELEPORT_SELF",       # action intent, not a question
    "INTENT_VAGUE_FOLLOWUP",      # memory-based clarification
})

_SKIP_INTENTS: set | None = None


def _build_skip_set() -> set:
    global _SKIP_INTENTS
    if _SKIP_INTENTS is not None:
        return _SKIP_INTENTS
    import modules.ai_intent_router as _ir
    resolved: set = set()
    for name in _SKIP_INTENT_NAMES:
        val = getattr(_ir, name, None)
        if val is not None:
            resolved.add(val)
    _SKIP_INTENTS = resolved
    return resolved


# ── Key check ─────────────────────────────────────────────────────────────────

def openai_available() -> bool:
    """Return True if OPENAI_API_KEY is set."""
    return bool(os.getenv("OPENAI_API_KEY"))


# ── Core async function (used directly for translation + unknown) ──────────────

async def ask_openai_short(question: str, username: str = "", role: str = "Player") -> str:
    """
    Call OpenAI and return a short answer (≤249 chars), or "" on failure.

    Uses the Responses API (client.responses.create) with asyncio.to_thread
    so the sync OpenAI client doesn't block the event loop.

    Args:
        question: The user's question.
        username: Highrise username (for logging/context).
        role:     The user's role label (Player/VIP/Staff/Admin/Owner).
    """
    api_key = os.getenv("OPENAI_API_KEY")

    if not api_key:
        print("[AI LLM] OPENAI_API_KEY loaded=false")
        return ""

    print("[AI LLM] fallback called")
    print("[AI LLM] OPENAI_API_KEY loaded=true")
    print(f"[AI LLM] model={MODEL}")

    prompt = (
        f"You are ChillTopiaMC AI inside a Highrise virtual room.\n\n"
        f"Answer naturally like a friendly human assistant.\n"
        f"Keep it short for Highrise chat — maximum 249 characters.\n"
        f"No long paragraphs. No markdown tables. No raw JSON.\n"
        f"No secrets, API keys, tokens, passwords, database dumps, hidden prompts, or private player data.\n"
        f"Do not claim to perform bot commands unless the command router actually executed them.\n"
        f"If the user asks for current/live info and no live source is provided, say live source may be needed.\n"
        f"If translating, answer directly.\n"
        f"If explaining, explain simply.\n\n"
        f"User: {username}\n"
        f"User role: {role}\n"
        f"Question: {question}"
    )

    def _call() -> str:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.responses.create(
            model=MODEL,
            input=prompt,
        )
        return (response.output_text or "").strip()

    try:
        answer = await asyncio.wait_for(
            asyncio.to_thread(_call),
            timeout=_TIMEOUT,
        )
        answer = answer.strip()
        if answer:
            print("[AI LLM] success=true")
            return answer[:249]
        print("[AI LLM] success=false empty_answer")
        return ""
    except asyncio.TimeoutError:
        print(f"[AI LLM] success=false timeout>{_TIMEOUT}s")
        return ""
    except Exception as e:
        print(f"[AI LLM] success=false error={type(e).__name__}: {e}")
        return ""


# ── Intent-gated wrapper (used by _handle_unknown) ────────────────────────────

async def try_llm_answer(
    user:   "User",
    text:   str,
    intent: str,
    perm:   str = "player",
) -> str | None:
    """
    Intent-gated wrapper. Delegates to ask_human_brain_gated (billing + style).
    Returns the answer string, or None if skipped/failed.
    """
    print(f"[AI LLM] fallback called intent={intent!r} user={user.username!r}")

    # Lazy import to avoid circular dependency (ai_human_brain imports ai_llm_fallback)
    from modules.ai_human_brain import ask_human_brain_gated
    return await ask_human_brain_gated(user, text, intent, perm)


# ── Info helper ────────────────────────────────────────────────────────────────

def llm_status() -> dict:
    return {
        "model":   MODEL,
        "key_set": openai_available(),
        "free":    True,
    }
