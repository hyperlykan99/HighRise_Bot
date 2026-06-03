"""Radio V3 payment helpers: normal requests use Song Plays only."""
from __future__ import annotations

import modules.music_credits as mc
from modules.permissions import is_admin, is_manager, is_owner


def is_staff(username: str) -> bool:
    return bool(is_owner(username) or is_admin(username) or is_manager(username))


def charge_request(user_id: str, username: str, *, priority: int = 0) -> tuple[bool, str, int | None]:
    if is_staff(username):
        return True, "staff_free", None
    if priority:
        return True, "priority_luxe", None
    if not mc.has_credits(user_id, username):
        return False, "no_song_plays", None
    if not mc.consume_credit(user_id, username):
        return False, "no_song_plays", None
    remaining = mc.get_credits(user_id, username).get("total", 0)
    return True, "music_credit", int(remaining)


def refund_if_needed(row: dict) -> bool:
    if not row or int(row.get("song_play_refunded") or 0):
        return False
    if (row.get("payment_type") or "").strip().lower() not in ("music_credit", "song_play", "song_play_credit"):
        return False
    uid = row.get("user_id") or ""
    uname = row.get("username") or ""
    if not uid:
        return False
    mc.refund_credit(uid, uname)
    return True

