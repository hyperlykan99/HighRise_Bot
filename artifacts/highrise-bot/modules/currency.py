"""modules/currency.py — Shared currency formatters.

Usage:
    from modules.currency import fmt_coins, fmt_luxe, fmt_gold, fmt_currency

    fmt_coins(1000)           → "1,000 🪙"
    fmt_luxe(100)             → "100 🎫"
    fmt_gold(50)              → "50 Gold"
    fmt_currency(500, "luxe") → "500 🎫"
"""

__all__ = ["fmt_coins", "fmt_luxe", "fmt_gold", "fmt_currency"]


def fmt_coins(amount: int | float) -> str:
    return f"{int(amount):,} 🪙"


def fmt_luxe(amount: int | float) -> str:
    return f"{int(amount):,} 🎫"


def fmt_gold(amount: int | float) -> str:
    return f"{int(amount):,} Gold"


def fmt_currency(amount: int | float, currency: str) -> str:
    c = currency.lower()
    if c in ("chillcoins", "coins", "chillcoin", "coin"):
        return fmt_coins(amount)
    if c in ("luxe_tickets", "luxe", "tickets", "ticket"):
        return fmt_luxe(amount)
    if c in ("gold", "highrise_gold", "hr_gold"):
        return fmt_gold(amount)
    return f"{int(amount):,}"
