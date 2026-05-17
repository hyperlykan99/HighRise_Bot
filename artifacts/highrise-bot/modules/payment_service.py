"""
modules/payment_service.py
--------------------------
Coin charge and refund operations for the radio request system.

Policy (enforced here, not in callers):
  • Owner and admin: always free (0 coins).
  • Everyone else (including VIP): pays the configured request_price().

Uses database.get_balance / database.adjust_balance directly.
Never raises — all errors are logged and returned as (False, message).
"""
from __future__ import annotations
import database as db
from modules.permissions import is_admin, is_owner
from modules.config_store import request_price

_LOG = "[RADIO_PAY]"


def get_balance(user_id: str) -> int:
    try:
        return int(db.get_balance(user_id) or 0)
    except Exception:
        return 0


def charge(user_id: str, amount: int) -> "tuple[bool, str]":
    """
    Deduct `amount` coins from user.
    Returns (True, "") on success, (False, reason) on failure.
    amount == 0 is always success (free request).
    """
    if amount <= 0:
        return True, ""
    try:
        bal = db.get_balance(user_id)
        if bal < amount:
            return False, f"Not enough coins. You have {bal:,}, need {amount:,}."
        db.adjust_balance(user_id, -amount)
        print(f"{_LOG} Charged {amount} coins from {user_id}")
        return True, ""
    except Exception as exc:
        print(f"{_LOG} charge error for {user_id}: {exc}")
        return False, "Payment system error. Please try again."


def refund(user_id: str, amount: int, reason: str = "") -> None:
    """
    Refund coins to user.  Non-fatal — logs error but never raises.
    Safe to call with amount=0 or empty user_id (no-op).
    """
    if amount <= 0 or not user_id:
        return
    try:
        db.adjust_balance(user_id, amount)
        tag = f" ({reason})" if reason else ""
        print(f"{_LOG} Refunded {amount} coins to {user_id}{tag}")
    except Exception as exc:
        print(f"{_LOG} refund error for {user_id}: {exc}")


def request_cost_for(username: str) -> int:
    """
    Return the coin cost for this user's request.
    Owner / admin → free.  Everyone else → configured price.
    """
    if is_owner(username) or is_admin(username):
        return 0
    return request_price()
