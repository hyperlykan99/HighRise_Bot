"""
modules/ai_memory_short_term.py — Per-user short-term conversation memory (3.3B).

Stores the last 5 AI exchanges per user with a 15-minute TTL.
Used to resolve ambiguous references like "how do I get more?" → last topic.
Never stores private player data beyond the current session.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

MEMORY_TTL: int   = 900   # 15 minutes
MAX_HISTORY: int  = 5


@dataclass
class UserMemory:
    last_topic:             str        = ""
    last_intent:            str        = ""
    last_question:          str        = ""
    pending_clarification:  str        = ""
    history:                list[tuple[str, str]] = field(default_factory=list)
    last_active:            float      = field(default_factory=time.monotonic)


_store: dict[str, UserMemory] = {}


def _evict_expired() -> None:
    now = time.monotonic()
    expired = [uid for uid, m in _store.items()
               if now - m.last_active > MEMORY_TTL]
    for uid in expired:
        del _store[uid]


def get_memory(user_id: str) -> UserMemory:
    _evict_expired()
    if user_id not in _store:
        _store[user_id] = UserMemory()
    return _store[user_id]


def update_memory(
    user_id:  str,
    intent:   str,
    question: str,
    topic:    str = "",
) -> None:
    """Record a new interaction in the user's short-term memory."""
    m = get_memory(user_id)
    m.last_intent   = intent
    m.last_question = question
    if topic:
        m.last_topic = topic
    m.history.append(("user", question[:200]))
    if len(m.history) > MAX_HISTORY:
        m.history = m.history[-MAX_HISTORY:]
    m.last_active = time.monotonic()


def get_context_hint(user_id: str) -> str:
    """Return the last stored topic for context resolution, or empty string."""
    m = _store.get(user_id)
    return m.last_topic if m else ""


def set_pending_clarification(user_id: str, question: str) -> None:
    m = get_memory(user_id)
    m.pending_clarification = question
    m.last_active = time.monotonic()


def get_pending_clarification(user_id: str) -> str:
    m = _store.get(user_id)
    return m.pending_clarification if m else ""


def clear_pending_clarification(user_id: str) -> None:
    m = _store.get(user_id)
    if m:
        m.pending_clarification = ""


def clear_memory(user_id: str) -> None:
    _store.pop(user_id, None)


def memory_count() -> int:
    _evict_expired()
    return len(_store)
