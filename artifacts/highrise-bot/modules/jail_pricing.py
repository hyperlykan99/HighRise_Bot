"""modules/jail_pricing.py — Ticket cost helpers for the Luxe Jail system (3.4A)."""
from modules.jail_config import cost_per_minute, bail_multiplier


def cost_for_jail(minutes: int) -> int:
    """Total Luxe Ticket cost to jail someone for `minutes` minutes."""
    return max(1, minutes) * cost_per_minute()


def bail_cost_for(original_cost: int) -> int:
    """Bail cost given the original jail purchase cost."""
    return max(1, original_cost) * bail_multiplier()
