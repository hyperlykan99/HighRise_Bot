"""
modules/msg_utils.py
--------------------
Global safe message splitting and sending helpers for Highrise.

Highrise truncates chat/whisper messages beyond ~249 chars and color tags
(<#RRGGBB>) must never be cut in half.

Public API
----------
safe_split(text, max_chars=220) -> list[str]
    Split a string into chunks that are each at most max_chars long.
    Prefers splitting on \\n, then ' | ', then ' '.
    Never cuts a <#RRGGBB> tag.  Closes open color tags with <#FFFFFF>.

safe_send(bot, text, whisper_target=None, max_chars=220) -> coroutine
    Send text as one or more messages.  whisper_target=user_id → whisper;
    None → public room chat.
"""
from __future__ import annotations

import re

_TAG_RE = re.compile(r"<#[0-9A-Fa-f]{6}>")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_in_tag(text: str, pos: int) -> bool:
    """Return True if character position *pos* is inside a <#RRGGBB> tag."""
    look_back = text[max(0, pos - 8): pos]
    idx = look_back.rfind("<#")
    if idx == -1:
        return False
    return ">" not in look_back[idx:]


def _close_open_color(text: str) -> str:
    """Append <#FFFFFF> if *text* ends with an unclosed color tag."""
    tags = list(_TAG_RE.finditer(text))
    if not tags:
        return text
    last_non_white = None
    for m in tags:
        if m.group() != "<#FFFFFF>":
            last_non_white = m
    if last_non_white is None:
        return text
    # Check whether any <#FFFFFF> appears after last_non_white
    for m in tags:
        if m.start() > last_non_white.start() and m.group() == "<#FFFFFF>":
            return text
    return text + "<#FFFFFF>"


def _safe_end(text: str, max_chars: int) -> int:
    """Largest position ≤ max_chars that does not fall inside a color tag."""
    pos = min(max_chars, len(text))
    while pos > 0 and _is_in_tag(text, pos):
        pos -= 1
    return pos


# ---------------------------------------------------------------------------
# Public: split
# ---------------------------------------------------------------------------

def safe_split(text: str, max_chars: int = 220) -> list[str]:
    """
    Split *text* into chunks of at most *max_chars* characters.

    Split priority: \\n  >  ' | '  >  ' '.
    Never cuts a Highrise color tag (<#RRGGBB>).
    Closes open color tags with <#FFFFFF> at the end of each chunk.
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(_close_open_color(remaining))
            break

        end = _safe_end(remaining, max_chars)
        window = remaining[:end]

        # Try preferred split separators in order
        split_at = -1
        used_sep = " "
        for sep in ("\n", " | ", " "):
            idx = window.rfind(sep)
            # Only use if it leaves a meaningful left chunk (>= 1/3 of max)
            if idx >= max(end // 3, 1):
                split_at = idx
                used_sep = sep
                break

        if split_at == -1:
            # No good separator — hard cut at safe end
            chunk = remaining[:end]
            advance = end
        else:
            chunk = remaining[:split_at]
            advance = split_at + len(used_sep)

        chunk = _close_open_color(chunk.rstrip("\n "))
        if chunk:
            chunks.append(chunk)
        remaining = remaining[advance:].lstrip("\n")

    return chunks or [text[:max_chars]]


# ---------------------------------------------------------------------------
# Public: send
# ---------------------------------------------------------------------------

async def safe_send(
    bot,
    text: str,
    whisper_target: str | None = None,
    max_chars: int = 220,
) -> None:
    """
    Send *text* as one or more Highrise messages, split at *max_chars*.

    Parameters
    ----------
    bot            : Highrise BaseBot instance
    text           : The full message text (may contain color tags, newlines)
    whisper_target : user_id string → send_whisper; None → public chat
    max_chars      : Hard character limit per chunk (default 220)
    """
    for chunk in safe_split(text, max_chars):
        if not chunk:
            continue
        if whisper_target:
            await bot.highrise.send_whisper(whisper_target, chunk)
        else:
            await bot.highrise.chat(chunk)
