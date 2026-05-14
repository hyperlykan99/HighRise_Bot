"""
modules/ai_llm_fallback.py — OpenAI LLM fallback for unanswered questions (3.3D).

Flow:
  1. Only called when all local/rule-based answers have failed.
  2. Skipped entirely for intents that should never reach OpenAI.
  3. Checks OPENAI_API_KEY — if missing, returns None silently.
  4. Checks player's 🎫 Luxe Ticket balance (cost = LLM_COST tickets).
  5. Calls gpt-4o-mini with a strict, public-safe system prompt.
  6. Deducts tickets ONLY after a successful answer.
  7. Logs the transaction in premium_transactions.
  8. Returns a short string ≤249 chars, or None if anything fails.

Model: gpt-4o-mini (note: gpt-5-mini not yet available; update _MODEL when
       OpenAI releases it — just change the constant below).
"""
from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import User

# luxe functions are imported lazily inside try_llm_answer to avoid pulling in
# database → config → BOT_TOKEN at module-load time.

# ── Config ────────────────────────────────────────────────────────────────────

_MODEL    = "gpt-4o-mini"       # swap to "gpt-5-mini" when released
_LLM_COST = 1                   # 🎫 Luxe Tickets per successful answer
_TIMEOUT  = 12.0                # seconds before giving up

# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are ChillTopiaMC, a helpful AI assistant inside a Highrise virtual room. "
    "Rules you must always follow:\n"
    "1. Reply in 1–4 short sentences, ≤220 characters total.\n"
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
# (imported lazily inside the function to avoid circular import at module load)
_SKIP_INTENT_NAMES = frozenset({
    "INTENT_AI_HELP",
    "INTENT_USER_NAME",
    "INTENT_USER_ROLE",
    "INTENT_CMD_EXPLAIN",
    "INTENT_LUXE",
    "INTENT_CHILLCOINS",
    "INTENT_MINING",
    "INTENT_FISHING",
    "INTENT_AI_REPLY_MODE_VIEW",
    "INTENT_AI_REPLY_MODE_SET",
    "INTENT_AI_STATUS",
    "INTENT_AI_DEBUG",
    "INTENT_CANCEL_SETTING",
    "INTENT_CONFIRM_SETTING",
    "INTENT_PREPARE_SETTING",
    "INTENT_MOD_HELP",
    "INTENT_STAFF_INFO",
    "INTENT_ADMIN_INFO",
    "INTENT_OWNER_INFO",
    "INTENT_PRIVATE_PLAYER_INFO",
    "INTENT_RW_SENSITIVE",
})

# Cache resolved intent objects after first call
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


# ── No-key / no-openai guard ──────────────────────────────────────────────────

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
        None — LLM skipped (no key, no tickets, wrong intent, or API error).
    """
    # 1. Skip intents that should never reach OpenAI
    skip = _build_skip_set()
    if intent in skip:
        return None

    # 2. No API key → silent skip
    if not _has_openai():
        return None

    # Lazy import to avoid BOT_TOKEN requirement at module-load time
    from modules.luxe import get_luxe_balance, deduct_luxe_balance, log_luxe_transaction

    # 3. Check balance
    balance = get_luxe_balance(user.id)
    if balance < _LLM_COST:
        needed = _LLM_COST - balance
        return (
            f"🎫 You need {_LLM_COST} Luxe Ticket to ask ChillTopia AI an open question. "
            f"You have {balance} (need {needed} more). "
            f"Earn Luxe Tickets by tipping gold or buying in the shop."
        )[:249]

    # 4. Call OpenAI
    print(f"[AI LLM] calling {_MODEL!r} for user={user.username!r} query={text[:60]!r}")
    answer = await _call_openai(text)

    if answer is None:
        return None

    # 5. Deduct tickets ONLY on success
    deducted = deduct_luxe_balance(user.id, user.username, _LLM_COST)
    if deducted:
        log_luxe_transaction(
            user.id, user.username,
            tx_type="ai_llm_use",
            amount=_LLM_COST,
            currency="luxe_tickets",
            details=f"AI LLM fallback: {text[:80]}",
        )
        print(f"[AI LLM] deducted {_LLM_COST} ticket from {user.username!r}, bal now {balance - _LLM_COST}")
    else:
        # Race condition edge case: balance changed between check and deduct
        print(f"[AI LLM] deduct failed for {user.username!r} — balance dropped? returning answer anyway")

    return answer[:249]


async def _call_openai(query: str) -> str | None:
    """
    Call OpenAI chat completions with a strict system prompt.
    Returns the reply string, or None on failure.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None

    try:
        import openai
    except ImportError:
        print("[AI LLM ERROR] openai package not installed")
        return None

    try:
        client = openai.AsyncOpenAI(api_key=api_key)
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user",   "content": query},
                ],
                max_tokens=120,
                temperature=0.6,
            ),
            timeout=_TIMEOUT,
        )
        raw = response.choices[0].message.content or ""
        raw = raw.strip()
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
        "model":      _MODEL,
        "cost":       _LLM_COST,
        "key_set":    _has_openai(),
    }
