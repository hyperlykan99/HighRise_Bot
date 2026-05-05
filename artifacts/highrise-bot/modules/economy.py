"""
modules/economy.py — DEPRECATED
--------------------------------
Economy functions have moved to economy.py at the project root so they can
be shared by all future bot modes (GameBot, DJBot, BlackjackBot, etc.).

This file is kept as a redirect so any code that still imports from here
continues to work during the transition.
"""

# Re-export everything from the new location
from economy import handle_balance, handle_daily, handle_leaderboard  # noqa: F401
