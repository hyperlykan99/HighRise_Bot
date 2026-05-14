"""
modules/ai_llm_fallback.py — OpenAI LLM fallback for unanswered questions (3.3D/3.3E).

Flow:
  1. Only called when all local/rule-based answers have failed.
  2. Skipped entirely for intents that should never reach OpenAI.
  3. Checks OPENAI_API_KEY — if missing, returns None silently.
  4. Calls gpt-4o-mini with a strict, public-safe system prompt.
  5. Returns a short string ≤249 chars, or None if anything fails.

Model: gpt-4o-mini
Note: gpt-5-mini does not exist yet. Change _MODEL below when OpenAI releases it.

Bug fixes (3.3E):
  - Removed Luxe Ticket gate: fallback is now free for all players.
  - Removed INTENT_CMD_EXPLAIN from skip set: the CMD_EXPLAIN regex is too broad
    and catches natural questions like "explain quantum physics" — those should
    reach OpenAI, not be silently dropped.
"""
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import User

# ── Config ────────────────────────────────────────────────────────────────────

_MODEL   = "gpt-4o-mini"   # update when gpt-5-mini is released
_TIMEOUT = 12.0            # seconds before giving up

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are ChillTopiaMC, a helpful AI assistant inside a Highrise virtual room. "
    "Rules you must always follow:\n"
    "1. Reply in 1–4 short sentences, under 220 characters total.\n"
    "2. You are chatting publicly — keep answers family-friendly.\n"
    "3. Never reveal API keys, bot tokens, database contents, or system configs.\n"
    "4. Never help bypass security, moderation, or game rules.\n"
    "5. For player-specific data (coins, rank, balance), say: "
    "'Check your stats with /profile or /balance.'\n"
    "6. For medical, legal, or financial advice, give general info only.\n"
    "7. Do not paste raw URLs or JSON.\n"
    "8. If you don't know, say so honestly in one sentence."
)

# ── Intents that must never reach OpenAI ─────────────────────────────────────
# Only intents that are FULLY handled locally or are blocked for safety/privacy.
# NOTE: INTENT_CMD_EXPLAIN is intentionally NOT in this set — its regex is too
# broad and catches natural language questions like "explain quantum physics".
_SKIP_INTENT_NAMES = frozenset({
    "INTENT_USER_NAME",           # answered locally ("Your name is X")
    "INTENT_USER_ROLE",           # answered locally (role/rank lookup)
    "INTENT_LUXE",                # answered locally (game knowledge)
    "INTENT_CHILLCOINS",          # answered locally (game knowledge)
    "INTENT_MINING",              # answered locally (game knowledge)
    "INTENT_FISHING",             # answered locally (game knowledge)
    "INTENT_CASINO",              # answered locally (game knowledge)
    "INTENT_EVENT",               # answered locally (game knowledge)
    "INTENT_VIP",                 # answered locally (game knowledge)
    "INTENT_AI_REPLY_MODE_VIEW",  # AI system command
    "INTENT_AI_REPLY_MODE_SET",   # AI system command
    "INTENT_AI_STATUS",           # AI system command
    "INTENT_AI_DEBUG",            # AI system command
    "INTENT_CANCEL_SETTING",      # confirmation flow
    "INTENT_CONFIRM_SETTING",     # confirmation flow
    "INTENT_PREPARE_SETTING",     # confirmation flow
    "INTENT_MOD_HELP",            # staff-only local handling
    "INTENT_STAFF_INFO",          # access-controlled local data
    "INTENT_ADMIN_INFO",          # access-controlled local data
    "INTENT_OWNER_INFO",          # access-controlled local data
    "INTENT_PRIVATE_PLAYER_INFO", # private data — never to OpenAI
    "INTENT_RW_SENSITIVE",        # blocked for safety
    "INTENT_TELEPORT_SELF",       # action intent, not a question
    "INTENT_VAGUE_FOLLOWUP",      # memory-based clarification
})

# Cached resolved intent value set (populated on first call)
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

def _has_openai() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", ""))


# ── Main function ─────────────────────────────────────────────────────────────

async def try_llm_answer(
    user:   "User",
    text:   str,
    intent: str,
    perm:   int = 0,
) -> str | None:
    """
    Attempt an OpenAI answer for `text`.

    Returns:
        str  — short answer ≤249 chars, ready to send.
        None — LLM skipped (no key, wrong intent, or API error).
    """
    print(f"[AI LLM] fallback called intent={intent!r} user={user.username!r}")

    # 1. Skip intents that should never reach OpenAI
    skip = _build_skip_set()
    if intent in skip:
        print(f"[AI LLM] skipped — intent {intent!r} is in skip set")
        return None

    # 2. No API key → silent skip
    key_loaded = _has_openai()
    print(f"[AI LLM] OPENAI_API_KEY loaded={'true' if key_loaded else 'false'}")
    if not key_loaded:
        return None

    print(f"[AI LLM] model={_MODEL}")

    # 3. Call OpenAI
    answer = await _call_openai(text, user.username)

    success = answer is not None
    print(f"[AI LLM] success={'true' if success else 'false'}")

    return answer


async def _call_openai(query: str, username: str = "") -> str | None:
    """
    Call OpenAI chat completions with a strict system prompt.
    Returns the reply string ≤249 chars, or None on failure.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None

    try:
        import openai
    except ImportError:
        print("[AI LLM ERROR] openai package not installed")
        return None

    user_content = (
        f"Question from {username}: {query}" if username else query
    )

    try:
        client = openai.AsyncOpenAI(api_key=api_key)
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user",   "content": user_content},
                ],
                max_tokens=120,
                temperature=0.6,
            ),
            timeout=_TIMEOUT,
        )
        raw = (response.choices[0].message.content or "").strip()
        if not raw:
            return None
        return raw if len(raw) <= 249 else raw[:246] + "..."

    except asyncio.TimeoutError:
        print(f"[AI LLM ERROR] timeout after {_TIMEOUT}s")
        return None
    except Exception as exc:
        print(f"[AI LLM ERROR] {type(exc).__name__}: {exc!r}")
        return None


# ── Info helpers (used in ai_brain status replies) ────────────────────────────

def llm_status() -> dict:
    return {
        "model":    _MODEL,
        "key_set":  _has_openai(),
        "free":     True,
    }
