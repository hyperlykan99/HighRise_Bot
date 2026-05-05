"""
config.py
---------
Central configuration for the Mini Game Bot.
Edit values here to tune the bot without touching any other file.
"""

import os

# ---------------------------------------------------------------------------
# Highrise credentials — loaded from environment secrets
# ---------------------------------------------------------------------------
BOT_TOKEN: str = os.environ["BOT_TOKEN"]
ROOM_ID:   str = os.environ["ROOM_ID"]

# ---------------------------------------------------------------------------
# Coin economy
# ---------------------------------------------------------------------------
STARTING_BALANCE: int = 100   # coins every new player starts with
DAILY_REWARD:     int = 50    # coins from /daily (claimable once per 24 hours)

# ---------------------------------------------------------------------------
# Mini-game rewards
# ---------------------------------------------------------------------------
TRIVIA_REWARD:   int = 25     # coins for winning /trivia
SCRAMBLE_REWARD: int = 25     # coins for winning /scramble
RIDDLE_REWARD:   int = 25     # coins for winning /riddle

# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------
LEADERBOARD_SIZE: int = 10    # how many players /leaderboard shows

# ---------------------------------------------------------------------------
# Admin usernames (case-insensitive Highrise usernames)
# Add anyone who should have access to /addcoins, /removecoins, /resetgame, /announce
# ---------------------------------------------------------------------------
ADMIN_USERS: list[str] = [
    "4ktreymarion",
]

# ---------------------------------------------------------------------------
# Shared database file path
# All bot modes (GameBot, DJBot, BlackjackBot, HostBot, etc.) must use this
# same path so player coins, stats, and daily rewards are shared across bots.
# ---------------------------------------------------------------------------
DB_PATH: str = "highrise_hangout.db"
