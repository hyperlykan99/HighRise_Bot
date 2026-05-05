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

# ── Admin user IDs ───────────────────────────────────────────────────────────
# Add the Highrise user IDs of anyone who should have admin commands.
# You can find your user ID by checking the Highrise app or API.
ADMIN_IDS: list[str] = [
    "66e6d8aa5f46e3ac67e12392",  # 4ktreyMarion
]
