"""
modules/msg_cap.py
------------------
Bot message cap testing — owned by Host bot (EmceeBot).

Commands (manager+):
  !msgcap                     — show help + current safe limits
  !msgcap chat [length]       — send public test message (splits if >240)
  !msgcap whisper [length]    — whisper test message to requester
  !msgcap split               — send 3-chunk split test
  !msgcap test <length>       — generic test (chat + whisper)
  !setmsgcap chat <length>    — set saved chat cap
  !setmsgcap whisper <length> — set saved whisper cap
"""
from __future__ import annotations

import asyncio

import database as db
from modules.permissions import is_owner, is_admin, is_manager

# Project default safe cap — never send a raw message above this
_DEFAULT_CAP = 249
_SAFE_SEND   = 240   # max chars per single API call (leaves headroom)
_MAX_TEST_LEN = 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _can_msgcap(username: str) -> bool:
    return is_owner(username) or is_admin(username) or is_manager(username)


def _get_saved_cap(kind: str) -> int:
    try:
        val = db.get_room_setting(f"msgcap_{kind}", str(_DEFAULT_CAP))
        return max(1, min(_MAX_TEST_LEN, int(val)))
    except Exception:
        return _DEFAULT_CAP


def _build_test_msg(length: int, fill_char: str = "A") -> str:
    """Build a test message of exactly `length` chars ending with END."""
    end_marker = "END"
    if length <= len(end_marker):
        return end_marker[:length]
    fill_count = length - len(end_marker)
    return (fill_char * fill_count) + end_marker


def _split_into_chunks(text: str, max_len: int = _SAFE_SEND) -> list[str]:
    """Split text into chunks of at most max_len chars."""
    if not text:
        return [""]
    chunks = []
    for i in range(0, len(text), max_len):
        chunks.append(text[i:i + max_len])
    return chunks


async def _safe_chat(bot, msg: str) -> None:
    """Send a chat message — split into safe chunks if >_SAFE_SEND chars."""
    chunks = _split_into_chunks(str(msg), _SAFE_SEND)
    total  = len(chunks)
    for idx, chunk in enumerate(chunks, 1):
        label = f"[{idx}/{total}] " if total > 1 else ""
        line  = (label + chunk)[:_DEFAULT_CAP]
        try:
            await bot.highrise.chat(line)
        except Exception:
            pass
        if total > 1:
            await asyncio.sleep(0.3)


async def _safe_whisper(bot, uid: str, msg: str) -> None:
    """Whisper — split into safe chunks if needed."""
    chunks = _split_into_chunks(str(msg), _SAFE_SEND)
    total  = len(chunks)
    for idx, chunk in enumerate(chunks, 1):
        label = f"[{idx}/{total}] " if total > 1 else ""
        line  = (label + chunk)[:_DEFAULT_CAP]
        try:
            await bot.highrise.send_whisper(uid, line)
        except Exception:
            pass
        if total > 1:
            await asyncio.sleep(0.15)


# Legacy thin wrappers (used by other modules that import from here)
async def _chat(bot, msg: str) -> None:
    await _safe_chat(bot, msg)


async def _w(bot, uid: str, msg: str) -> None:
    await _safe_whisper(bot, uid, msg)


# ---------------------------------------------------------------------------
# !msgcap  — main dispatcher
# ---------------------------------------------------------------------------

async def handle_msgcap(bot, user, args: list[str]) -> None:
    if not _can_msgcap(user.username):
        await _safe_whisper(bot, user.id, "Manager/admin/owner only.")
        return

    sub = args[1].lower() if len(args) > 1 else ""

    if sub == "chat":
        await _msgcap_chat(bot, user, args[2:])
    elif sub == "whisper":
        await _msgcap_whisper(bot, user, args[2:])
    elif sub == "split":
        await _msgcap_split(bot, user)
    elif sub == "test":
        await _msgcap_test(bot, user, args[2:])
    else:
        chat_cap    = _get_saved_cap("chat")
        whisper_cap = _get_saved_cap("whisper")
        await _safe_whisper(
            bot, user.id,
            f"🧪 Message Cap Test\n"
            f"Safe limit — Chat: {chat_cap}  Whisper: {whisper_cap}\n"
            f"!msgcap chat [len]\n"
            f"!msgcap whisper [len]\n"
            f"!msgcap split\n"
            f"!msgcap test [len]",
        )


# ---------------------------------------------------------------------------
# !msgcap chat [length]
# ---------------------------------------------------------------------------

async def _msgcap_chat(bot, user, args: list[str]) -> None:
    cap = _get_saved_cap("chat")
    if args:
        try:
            cap = max(1, min(_MAX_TEST_LEN, int(args[0])))
        except ValueError:
            await _safe_whisper(bot, user.id, "Length must be a whole number.")
            return

    chunks_needed = max(1, (cap + _SAFE_SEND - 1) // _SAFE_SEND)
    warn = f"  ⚠️ Will send {chunks_needed} chunk(s)" if cap > _SAFE_SEND else ""
    header = f"🧪 Public Chat Cap Test\nRequested: {cap}{warn}\n"

    body    = _build_test_msg(max(0, cap - len(header)))
    full    = header + body
    chunks  = _split_into_chunks(full, _SAFE_SEND)
    total   = len(chunks)
    for idx, chunk in enumerate(chunks, 1):
        label = f"[{idx}/{total}] " if total > 1 else ""
        try:
            await bot.highrise.chat((label + chunk)[:_DEFAULT_CAP])
        except Exception:
            pass
        if total > 1:
            await asyncio.sleep(0.3)


# ---------------------------------------------------------------------------
# !msgcap whisper [length]
# ---------------------------------------------------------------------------

async def _msgcap_whisper(bot, user, args: list[str]) -> None:
    cap = _get_saved_cap("whisper")
    if args:
        try:
            cap = max(1, min(_MAX_TEST_LEN, int(args[0])))
        except ValueError:
            await _safe_whisper(bot, user.id, "Length must be a whole number.")
            return

    chunks_needed = max(1, (cap + _SAFE_SEND - 1) // _SAFE_SEND)
    warn   = f"  ⚠️ {chunks_needed} chunk(s)" if cap > _SAFE_SEND else ""
    header = f"🧪 Whisper Cap Test\nRequested: {cap}{warn}\n"

    body   = _build_test_msg(max(0, cap - len(header)))
    full   = header + body
    chunks = _split_into_chunks(full, _SAFE_SEND)
    total  = len(chunks)
    for idx, chunk in enumerate(chunks, 1):
        label = f"[{idx}/{total}] " if total > 1 else ""
        try:
            await bot.highrise.send_whisper(user.id, (label + chunk)[:_DEFAULT_CAP])
        except Exception:
            pass
        if total > 1:
            await asyncio.sleep(0.15)


# ---------------------------------------------------------------------------
# !msgcap test <length>  — chat + whisper
# ---------------------------------------------------------------------------

async def _msgcap_test(bot, user, args: list[str]) -> None:
    if not args:
        await _safe_whisper(bot, user.id, "Usage: !msgcap test <length>  (1–1000)")
        return
    try:
        length = max(1, min(_MAX_TEST_LEN, int(args[0])))
    except ValueError:
        await _safe_whisper(bot, user.id, "Length must be a whole number.")
        return

    chunks_needed = max(1, (length + _SAFE_SEND - 1) // _SAFE_SEND)
    warn     = f"  ⚠️ {chunks_needed} chunk(s)" if length > _SAFE_SEND else ""
    c_header = f"🧪 Chat Cap Test  Len:{length}{warn}\n"
    w_header = f"🧪 Whisper Test   Len:{length}{warn}\n"

    c_fill = _build_test_msg(max(0, length - len(c_header)))
    w_fill = _build_test_msg(max(0, length - len(w_header)))

    c_chunks = _split_into_chunks(c_header + c_fill, _SAFE_SEND)
    w_chunks = _split_into_chunks(w_header + w_fill, _SAFE_SEND)

    for idx, chunk in enumerate(c_chunks, 1):
        label = f"[{idx}/{len(c_chunks)}] " if len(c_chunks) > 1 else ""
        try:
            await bot.highrise.chat((label + chunk)[:_DEFAULT_CAP])
        except Exception:
            pass
        if len(c_chunks) > 1:
            await asyncio.sleep(0.3)

    for idx, chunk in enumerate(w_chunks, 1):
        label = f"[{idx}/{len(w_chunks)}] " if len(w_chunks) > 1 else ""
        try:
            await bot.highrise.send_whisper(user.id, (label + chunk)[:_DEFAULT_CAP])
        except Exception:
            pass
        if len(w_chunks) > 1:
            await asyncio.sleep(0.15)


# ---------------------------------------------------------------------------
# !msgcap split  — send 3 timed chunks as separate messages
# ---------------------------------------------------------------------------

async def _msgcap_split(bot, user) -> None:
    cap = _get_saved_cap("chat")
    test_sets = [
        ("1/3", "A", cap),
        ("2/3", "B", cap),
        ("3/3", "C", cap // 2),
    ]
    for label, char, length in test_sets:
        header = f"🧪 Split Test {label}\nLen:{length}\n"
        fill   = _build_test_msg(max(0, length - len(header)), char)
        full   = header + fill
        chunks = _split_into_chunks(full, _SAFE_SEND)
        total  = len(chunks)
        for idx, chunk in enumerate(chunks, 1):
            clabel = f"[{idx}/{total}] " if total > 1 else ""
            try:
                await bot.highrise.chat((clabel + chunk)[:_DEFAULT_CAP])
            except Exception:
                pass
            if total > 1:
                await asyncio.sleep(0.3)
        await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# !setmsgcap chat|whisper <length>
# ---------------------------------------------------------------------------

async def handle_setmsgcap(bot, user, args: list[str]) -> None:
    if not _can_msgcap(user.username):
        await _safe_whisper(bot, user.id, "Manager/admin/owner only.")
        return

    if len(args) < 3:
        await _safe_whisper(bot, user.id,
                 "Usage: !setmsgcap chat|whisper <length>\n"
                 "Example: !setmsgcap chat 240")
        return

    kind = args[1].lower()
    if kind not in ("chat", "whisper"):
        await _safe_whisper(bot, user.id, "Kind must be 'chat' or 'whisper'.")
        return

    try:
        length = max(1, min(_MAX_TEST_LEN, int(args[2])))
    except ValueError:
        await _safe_whisper(bot, user.id, "Length must be a whole number.")
        return

    db.set_room_setting(f"msgcap_{kind}", str(length))
    warn = "  ⚠️ Above safe cap!" if length > _SAFE_SEND else ""
    await _safe_whisper(
        bot, user.id,
        f"🧪 Message Cap Updated\n"
        f"{kind.capitalize()} cap: {length}{warn}",
    )
