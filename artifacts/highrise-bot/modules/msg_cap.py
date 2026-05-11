"""
modules/msg_cap.py
------------------
Bot message cap testing — owned by Host bot (EmceeBot).

Commands (manager+):
  /msgcap                     — show help + current safe limits
  /msgcap chat [length]       — send public test message
  /msgcap whisper [length]    — whisper test message to requester
  /msgcap split               — send 3-chunk split test
  /msgcap test <length>       — generic test (chat + whisper)
  /setmsgcap chat <length>    — set saved chat cap
  /setmsgcap whisper <length> — set saved whisper cap
"""
from __future__ import annotations

import database as db
from modules.permissions import is_owner, is_admin, is_manager

# Project default safe cap
_DEFAULT_CAP = 249
_MAX_TEST_LEN = 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _can_msgcap(username: str) -> bool:
    return is_owner(username) or is_admin(username) or is_manager(username)


async def _chat(bot, msg: str) -> None:
    try:
        await bot.highrise.chat(str(msg)[:_DEFAULT_CAP])
    except Exception:
        pass


async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:_DEFAULT_CAP])
    except Exception:
        pass


def _get_saved_cap(kind: str) -> int:
    try:
        val = db.get_room_setting(f"msgcap_{kind}", str(_DEFAULT_CAP))
        return max(1, min(_MAX_TEST_LEN, int(val)))
    except Exception:
        return _DEFAULT_CAP


def _build_test_msg(length: int, prefix: str, fill_char: str = "A") -> str:
    """Build a test message of exactly `length` chars ending with END."""
    end_marker = "END"
    if length <= len(end_marker):
        return end_marker[:length]
    fill_count = length - len(end_marker)
    return (fill_char * fill_count) + end_marker


# ---------------------------------------------------------------------------
# /msgcap  — main dispatcher
# ---------------------------------------------------------------------------

async def handle_msgcap(bot, user, args: list[str]) -> None:
    if not _can_msgcap(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
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
        await _w(
            bot, user.id,
            f"🧪 Message Cap Test\n"
            f"/msgcap chat\n"
            f"/msgcap whisper\n"
            f"/msgcap split\n"
            f"/msgcap test <length>\n"
            f"Safe limit — Chat: {chat_cap}  Whisper: {whisper_cap}",
        )


# ---------------------------------------------------------------------------
# /msgcap chat [length]
# ---------------------------------------------------------------------------

async def _msgcap_chat(bot, user, args: list[str]) -> None:
    cap = _get_saved_cap("chat")
    if args:
        try:
            cap = max(1, min(_MAX_TEST_LEN, int(args[0])))
        except ValueError:
            await _w(bot, user.id, "Length must be a whole number.")
            return

    warn = "  ⚠️ May be cut!" if cap > _DEFAULT_CAP else ""
    header = f"🧪 Public Chat Cap Test\nRequested: {cap}{warn}\n"
    fill_len = max(0, cap - len(header))
    body = _build_test_msg(fill_len, "")
    await bot.highrise.chat((header + body)[:cap])


# ---------------------------------------------------------------------------
# /msgcap whisper [length]
# ---------------------------------------------------------------------------

async def _msgcap_whisper(bot, user, args: list[str]) -> None:
    cap = _get_saved_cap("whisper")
    if args:
        try:
            cap = max(1, min(_MAX_TEST_LEN, int(args[0])))
        except ValueError:
            await _w(bot, user.id, "Length must be a whole number.")
            return

    warn = "  ⚠️ May be cut!" if cap > _DEFAULT_CAP else ""
    header = f"🧪 Whisper Cap Test\nRequested: {cap}{warn}\n"
    fill_len = max(0, cap - len(header))
    body = _build_test_msg(fill_len, "")
    try:
        await bot.highrise.send_whisper(user.id, (header + body)[:cap])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /msgcap test <length>  — chat + whisper
# ---------------------------------------------------------------------------

async def _msgcap_test(bot, user, args: list[str]) -> None:
    if not args:
        await _w(bot, user.id, "Usage: /msgcap test <length>  (1–1000)")
        return
    try:
        length = max(1, min(_MAX_TEST_LEN, int(args[0])))
    except ValueError:
        await _w(bot, user.id, "Length must be a whole number.")
        return

    warn = "  ⚠️ May be cut!" if length > _DEFAULT_CAP else ""
    c_header = f"🧪 Chat Cap Test  Len:{length}{warn}\n"
    w_header = f"🧪 Whisper Test   Len:{length}{warn}\n"

    c_fill = _build_test_msg(max(0, length - len(c_header)), "")
    w_fill = _build_test_msg(max(0, length - len(w_header)), "")

    try:
        await bot.highrise.chat((c_header + c_fill)[:length])
    except Exception:
        pass
    try:
        await bot.highrise.send_whisper(user.id, (w_header + w_fill)[:length])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /msgcap split  — send 3 timed chunks as separate messages
# ---------------------------------------------------------------------------

async def _msgcap_split(bot, user) -> None:
    cap = _get_saved_cap("chat")
    chunks = [
        ("1/3", "A", cap),
        ("2/3", "B", cap),
        ("3/3", "C", cap // 2),
    ]
    for label, char, length in chunks:
        header = f"🧪 Split Test {label}\nLen:{length}\n"
        fill   = _build_test_msg(max(0, length - len(header)), "", char)
        msg    = (header + fill)[:length]
        try:
            await bot.highrise.chat(msg)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# /setmsgcap chat|whisper <length>
# ---------------------------------------------------------------------------

async def handle_setmsgcap(bot, user, args: list[str]) -> None:
    if not _can_msgcap(user.username):
        await _w(bot, user.id, "Manager/admin/owner only.")
        return

    if len(args) < 3:
        await _w(bot, user.id,
                 "Usage: /setmsgcap chat|whisper <length>\n"
                 "Example: /setmsgcap chat 240")
        return

    kind = args[1].lower()
    if kind not in ("chat", "whisper"):
        await _w(bot, user.id, "Kind must be 'chat' or 'whisper'.")
        return

    try:
        length = max(1, min(_MAX_TEST_LEN, int(args[2])))
    except ValueError:
        await _w(bot, user.id, "Length must be a whole number.")
        return

    db.set_room_setting(f"msgcap_{kind}", str(length))
    warn = "  ⚠️ Above project default!" if length > _DEFAULT_CAP else ""
    await _w(
        bot, user.id,
        f"🧪 Message Cap Updated\n"
        f"{kind.capitalize()} cap: {length}{warn}",
    )
