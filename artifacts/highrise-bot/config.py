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
# Cooldowns
# ---------------------------------------------------------------------------
TRIVIA_COOLDOWN:   int = 30   # seconds between /trivia starts (room-wide)
SCRAMBLE_COOLDOWN: int = 30   # seconds between /scramble starts (room-wide)
RIDDLE_COOLDOWN:   int = 30   # seconds between /riddle starts (room-wide)
COINFLIP_COOLDOWN: int = 10   # seconds between /coinflip uses (per user)
ANSWER_COOLDOWN:   int = 3    # seconds between /answer attempts (per user)

# ---------------------------------------------------------------------------
# Shared database file path
# All bot modes (GameBot, DJBot, BlackjackBot, HostBot, etc.) must use this
# same path so player coins, stats, and daily rewards are shared across bots.
# ---------------------------------------------------------------------------
DB_PATH: str = "highrise_hangout.db"
