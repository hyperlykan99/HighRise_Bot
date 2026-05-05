"""
config.py
---------
Central configuration for the bot.
Edit these values to tune bot behaviour without touching other files.
"""

import os

# ── Highrise credentials (loaded from environment secrets) ──────────────────
BOT_TOKEN: str = os.environ["BOT_TOKEN"]
ROOM_ID:   str = os.environ["ROOM_ID"]

# ── Token economy ────────────────────────────────────────────────────────────
SONG_REQUEST_COST: int = 20    # tokens deducted per song request
DAILY_REWARD:      int = 10    # free tokens from /daily

# ── DJ system ───────────────────────────────────────────────────────────────
QUEUE_DISPLAY_SIZE: int = 5    # how many songs /queue shows
SKIP_VOTE_THRESHOLD: int = 3   # number of votes needed to auto-skip

# ── Admin usernames ──────────────────────────────────────────────────────────
# Add Highrise usernames (case-insensitive) for anyone who should have
# access to admin commands: /skip /remove /addtokens /refund
ADMIN_USERS: list[str] = [
    "4ktreymarion",
]
