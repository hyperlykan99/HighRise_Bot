"""
modules/music_credits.py
------------------------
Music request credit system for DJ_DUDU.

Credits control who can use !play / !request / !playmine.
Staff (admin/owner) are ALWAYS exempt — they never consume credits.
Priority requests (paid with Luxe Tickets) are also exempt.

Credit types — consumed in this order:
  free        5 granted automatically on first request
  vip         20 granted when VIP pass is activated
  purchased   bought via !buyrequests

Shop price tables (coins / luxe per pack):
  SHOP_COINS = {5: 500, 10: 900, 25: 2000}
  SHOP_LUXE  = {5: 10,  10: 18,  25: 40}
"""
from __future__ import annotations
import database as db

_LOG = "[MUSIC_CREDITS]"

# ─── Shop prices ──────────────────────────────────────────────────────────────
SHOP_COINS: "dict[int, int]" = {1: 500,  5: 2400,  10: 4500,  25: 10000}
SHOP_LUXE:  "dict[int, int]" = {1: 20,  5: 95,    10: 180,   25: 400}
PRIORITY_COST_LUXE = 100


# ─── DB helpers ───────────────────────────────────────────────────────────────

def _ensure_row(user_id: str, username: str) -> None:
    """INSERT OR IGNORE a row for this user, granting 5 free credits on first visit."""
    with db.db_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO user_music_credits
               (user_id, username, free_requests, purchased_requests,
                vip_requests, total_used)
               VALUES (?, ?, 5, 0, 0, 0)""",
            (user_id, (username or "").lower()),
        )


def get_credits(user_id: str, username: str = "") -> dict:
    """
    Return credit counts for a user.
    Creates the row (with 5 free credits) if it does not exist yet.
    Returns: {free, vip, purchased, total, total_used}
    """
    if username:
        _ensure_row(user_id, username)
    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT free_requests, purchased_requests, vip_requests, total_used "
            "FROM user_music_credits WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return {"free": 0, "vip": 0, "purchased": 0, "total": 0, "total_used": 0}
    free, purchased, vip, used = row
    return {
        "free":       free,
        "vip":        vip,
        "purchased":  purchased,
        "total":      free + vip + purchased,
        "total_used": used,
    }


def has_credits(user_id: str, username: str) -> bool:
    """Return True if the user has at least 1 credit of any type."""
    return get_credits(user_id, username)["total"] > 0


def consume_credit(user_id: str, username: str) -> bool:
    """
    Deduct 1 credit in order: free → vip → purchased.
    Returns True on success, False if no credits are available.
    Logs stage=music_credit_consume.
    """
    _ensure_row(user_id, username)
    with db.db_conn() as conn:
        row = conn.execute(
            "SELECT free_requests, vip_requests, purchased_requests "
            "FROM user_music_credits WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return False
        free, vip, purchased = row

        if free > 0:
            conn.execute(
                "UPDATE user_music_credits "
                "SET free_requests = free_requests - 1, "
                "    total_used    = total_used + 1, "
                "    updated_at    = datetime('now') "
                "WHERE user_id = ?",
                (user_id,),
            )
            print(
                f"{_LOG} stage=music_credit_consume"
                f" user_id={user_id!r} username={username!r}"
                f" type=free remaining={free - 1}"
            )
            return True

        if vip > 0:
            conn.execute(
                "UPDATE user_music_credits "
                "SET vip_requests = vip_requests - 1, "
                "    total_used   = total_used + 1, "
                "    updated_at   = datetime('now') "
                "WHERE user_id = ?",
                (user_id,),
            )
            print(
                f"{_LOG} stage=music_credit_consume"
                f" user_id={user_id!r} username={username!r}"
                f" type=vip remaining={vip - 1}"
            )
            return True

        if purchased > 0:
            conn.execute(
                "UPDATE user_music_credits "
                "SET purchased_requests = purchased_requests - 1, "
                "    total_used         = total_used + 1, "
                "    updated_at         = datetime('now') "
                "WHERE user_id = ?",
                (user_id,),
            )
            print(
                f"{_LOG} stage=music_credit_consume"
                f" user_id={user_id!r} username={username!r}"
                f" type=purchased remaining={purchased - 1}"
            )
            return True

        return False


def refund_credit(user_id: str, username: str) -> None:
    """
    Return 1 consumed credit to the user (added to purchased bucket).
    Logs stage=music_credit_refund.
    """
    try:
        with db.db_conn() as conn:
            conn.execute(
                "UPDATE user_music_credits "
                "SET purchased_requests = purchased_requests + 1, "
                "    total_used         = MAX(0, total_used - 1), "
                "    updated_at         = datetime('now') "
                "WHERE user_id = ?",
                (user_id,),
            )
        print(
            f"{_LOG} stage=music_credit_refund"
            f" user_id={user_id!r} username={username!r}"
        )
    except Exception as exc:
        print(f"{_LOG} stage=music_credit_refund error={exc!r} user_id={user_id!r}")


def add_credits(
    user_id: str,
    username: str,
    amount: int,
    credit_type: str = "purchased",
) -> None:
    """
    Add `amount` credits of `credit_type` ('free', 'vip', 'purchased').
    Creates the row first if needed.
    Logs stage=music_credit_grant.
    """
    _ensure_row(user_id, username)
    col_map = {
        "free":      "free_requests",
        "vip":       "vip_requests",
        "purchased": "purchased_requests",
    }
    col = col_map.get(credit_type, "purchased_requests")
    try:
        with db.db_conn() as conn:
            conn.execute(
                f"UPDATE user_music_credits "
                f"SET {col} = {col} + ?, updated_at = datetime('now') "
                f"WHERE user_id = ?",
                (amount, user_id),
            )
        print(
            f"{_LOG} stage=music_credit_grant"
            f" user_id={user_id!r} username={username!r}"
            f" type={credit_type!r} amount={amount}"
        )
    except Exception as exc:
        print(f"{_LOG} add_credits error={exc!r} user_id={user_id!r}")


def grant_vip_requests(user_id: str, username: str, amount: int = 20) -> None:
    """
    Add VIP request credits (called when a VIP pass is activated).
    Logs stage=vip_request_grant.
    """
    add_credits(user_id, username, amount, "vip")
    print(
        f"{_LOG} stage=vip_request_grant"
        f" user_id={user_id!r} username={username!r} amount={amount}"
    )
