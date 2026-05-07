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
# Owner usernames — highest privilege, inherits all admin rights
# ---------------------------------------------------------------------------
OWNER_USERS: list[str] = [
    "4ktreymarion",
]

# ---------------------------------------------------------------------------
# Admin usernames (case-insensitive Highrise usernames)
# Can manage economy, casino, and games. Can add/remove managers.
# ---------------------------------------------------------------------------
ADMIN_USERS: list[str] = [
    "4ktreymarion",
]

# ---------------------------------------------------------------------------
# XP rewards
# ---------------------------------------------------------------------------
XP_TRIVIA:   int = 10  # XP for a correct trivia answer
XP_SCRAMBLE: int = 10  # XP for a correct scramble answer
XP_RIDDLE:   int = 10  # XP for solving a riddle
XP_COINFLIP: int = 5   # XP for winning a coinflip
XP_DAILY:    int = 5   # XP for claiming the daily reward

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
DB_PATH: str = os.environ.get("SHARED_DB_PATH", "highrise_hangout.db")

# ---------------------------------------------------------------------------
# Multi-bot identity — set these env vars when running separate bots.
# Default "all" means one bot handles every command (backwards-compatible).
# ---------------------------------------------------------------------------
BOT_ID:       str = os.environ.get("BOT_ID",       "main")
BOT_MODE:     str = os.environ.get("BOT_MODE",     "all")
BOT_USERNAME: str = os.environ.get("BOT_USERNAME", "")

# Extra modes this process covers because it merged a duplicate-token bot.
# Populated automatically by bot.py when two bots share the same Highrise account.
BOT_EXTRA_MODES: frozenset[str] = frozenset(
    m.strip()
    for m in os.environ.get("BOT_EXTRA_MODES", "").split(",")
    if m.strip()
)
