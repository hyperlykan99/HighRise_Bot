"""
modules/ai_response_style.py — Response style / formatting engine (3.3B).

Applies length and tone rules per response mode:
  short_answer          — 1 sentence, plain
  friendly_explanation  — 2-3 sentences, emojis
  step_by_step          — numbered list
  staff_summary         — header + bullets
  owner_summary         — header + bullets + risk
  refusal               — 1 sentence, firm but kind
  confirmation_preview  — structured block
  clarification_question — 1 question
  bug_report_saved      — short confirmation
  public_chat_brief     — very short, no private data (≤120 chars)
  whispered_private_summary — up to 249 chars, richer detail
"""
from __future__ import annotations

_MAX_PUBLIC  = 120
_MAX_WHISPER = 249


def short_answer(text: str) -> str:
    return text.strip()[:_MAX_WHISPER]


def friendly_explanation(text: str, public: bool = False) -> str:
    limit = _MAX_PUBLIC if public else _MAX_WHISPER
    return text.strip()[:limit]


def public_brief(text: str) -> str:
    """Trim to public-safe length."""
    return text.strip()[:_MAX_PUBLIC]


def step_by_step(steps: list[str]) -> str:
    lines = [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    return "\n".join(lines)[:_MAX_WHISPER]


def staff_summary(title: str, bullets: list[str]) -> str:
    body = "\n".join(f"• {b}" for b in bullets)
    return f"📋 {title}:\n{body}"[:_MAX_WHISPER]


def owner_summary(title: str, bullets: list[str], risk: str = "") -> str:
    body  = "\n".join(f"• {b}" for b in bullets)
    extra = f"\n⚠️ Risk: {risk}" if risk else ""
    return f"🔧 {title}:\n{body}{extra}"[:_MAX_WHISPER]


def refusal(text: str) -> str:
    return text.strip()[:_MAX_WHISPER]


def confirmation_preview(label: str, current: str, new_val: str,
                          risk: str, phrase: str) -> str:
    return (
        f"⚙️ Prepared change:\n"
        f"Setting: {label}\n"
        f"Current: {current}\n"
        f"New: {new_val}\n"
        f"Risk: {risk}\n"
        f"Reply '{phrase}' to apply, or CANCEL."
    )[:_MAX_WHISPER]


def clarification_question(text: str) -> str:
    return f"❓ {text.strip()}"[:_MAX_WHISPER]


def bug_report_saved(extra: str = "") -> str:
    msg = "🐛 Bug report saved — staff will review it."
    if extra:
        msg += f" {extra}"
    return msg[:_MAX_WHISPER]


def whispered_private_summary(text: str) -> str:
    return f"🔒 {text.strip()}"[:_MAX_WHISPER]
