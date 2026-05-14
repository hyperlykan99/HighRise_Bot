"""
modules/ai_abuse_guard.py — Prompt injection & abuse detection (3.3B).

Blocks prompt-injection attempts, permission-bypass tricks, and
forced-public-data requests before they reach the intent router.
"""
from __future__ import annotations

import re

# ── Master injection pattern ─────────────────────────────────────────────────
_INJECTION = re.compile(
    r"\bignore\s+(all\s+)?(previous|your)\s+(rules?|instructions?|context|prompt)\b"
    r"|\bforget\s+(all\s+)?(previous|your)\s+(rules?|instructions?)\b"
    r"|\bpretend\s+(i\s+am|you\s+are|i'm|i\s+am\s+now)\s+(owner|admin|staff|bot|god)\b"
    r"|\byou\s+are\s+now\s+(owner|admin|jailbroken|unfiltered|different)\b"
    r"|\bact\s+as\s+if\s+you\s+have\s+no\s+(rules?|limits?|restrictions?)\b"
    r"|\breveal\s+(your\s+)?(database|db|logs?|token|secret|system\s+prompt|prompt)\b"
    r"|\bshow\s+(hidden|secret)\s+(settings?|commands?|data|info)\b"
    r"|\bbypass\s+(confirmation|permission|safety|moderation|rules?|filter)\b"
    r"|\bskip\s+(confirmation|safety|permission)\b"
    r"|\bgive\s+me\s+(admin|owner|staff)\s+access\b"
    r"|\bgrant\s+(admin|owner|staff)\s+(access|permissions?)\b"
    r"|\bi\s+am\s+the\s+(owner|admin|developer|creator)\b"
    r"|\btell\s+every\s+bot\s+to\s+(answer|reply|respond)\b"
    r"|\bmake\s+(all|every)\s+bot\s+(answer|reply|respond)\b"
    r"|\benable\s+all\s+bots?\s+(to\s+)?(answer|reply|respond)\b"
    r"|\breply\s+publicly\s+(with|to)\s+(my|her|his|their)\s+(private|balance|tickets?|coins?)\b"
    r"|\bshow\s+\w+'s\s+(balance|tickets?|coins?|level)\s+publicly\b"
    r"|\bdisable\s+your\s+(rules?|safety|permission|filter)\b"
    r"|\bunlock\s+(admin|owner|developer)\s+mode\b"
    r"|\bwipe\s+(all\s+)?(data|database|players?|records?)\b",
    re.I,
)

# ── Specific pattern → tailored reply ────────────────────────────────────────
_SPECIFIC: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bignore.*rules?\b|\bforget.*rules?\b|\bbypass.*rules?\b", re.I),
     "🚫 I can't ignore my safety rules."),
    (re.compile(r"\bpretend.*owner\b|\bi\s+am\s+(the\s+)?owner\b|\byou\s+are\s+now\b", re.I),
     "🚫 I can't grant different permissions than you actually have."),
    (re.compile(r"\breveal.*database\b|\bshow.*secret\b|\bshow.*hidden\b|\bsystem\s+prompt\b", re.I),
     "🚫 I can't reveal internal data, secrets, or hidden settings."),
    (re.compile(r"\bbypass.*confirm\b|\bskip.*confirm\b", re.I),
     "🚫 Confirmation steps can't be bypassed."),
    (re.compile(r"\btell.*bot.*answer\b|\bmake.*bot.*respond\b|\benable.*all.*bot\b", re.I),
     "🚫 Only ChillTopiaMC answers AI messages to prevent duplicate spam."),
    (re.compile(r"\breply.*publicly.*private\b|\bshow.*publicly.*balance\b", re.I),
     "🔒 Private data stays private — I can't show it publicly."),
    (re.compile(r"\bgive.*admin.*access\b|\bgrant.*owner\b|\bunlock.*mode\b", re.I),
     "🚫 I can't grant admin or owner access through the AI."),
    (re.compile(r"\bwipe.*data\b|\bwipe.*database\b", re.I),
     "🚫 I can't wipe player data through AI. That action is blocked for safety."),
]

_FALLBACK = "🚫 That looks like a permission bypass attempt. I can't help with that."


def check_abuse(text: str) -> str | None:
    """
    Check for prompt injection or abuse patterns.

    Returns:
        None  → text is clean, proceed normally
        str   → refusal message to send to the user
    """
    if not _INJECTION.search(text):
        return None
    for pattern, reply in _SPECIFIC:
        if pattern.search(text):
            return reply
    return _FALLBACK
