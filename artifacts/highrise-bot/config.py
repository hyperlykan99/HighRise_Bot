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

# ── YouTube Data API v3 ──────────────────────────────────────────────────────
YOUTUBE_API_KEY: str = os.environ.get("YOUTUBE_API_KEY", "")

# ── Token economy ────────────────────────────────────────────────────────────
SONG_REQUEST_COST:    int = 20   # tokens for a normal /request
PRIORITY_REQUEST_COST: int = 50  # tokens for a /priority request (jumps to #2 in queue)
DAILY_REWARD:         int = 10   # free tokens from /daily

# ── DJ system ────────────────────────────────────────────────────────────────
QUEUE_DISPLAY_SIZE:  int = 5    # how many songs /queue shows
QUEUE_MAX_SIZE:      int = 20   # maximum songs allowed in the queue at once
SKIP_VOTE_THRESHOLD: int = 3    # votes needed to auto-skip

# ── Content filter ───────────────────────────────────────────────────────────
# Song titles containing any of these words (case-insensitive) will be rejected.
# Add or remove words here — no other file needs to change.
BANNED_WORDS: list[str] = [
    "nigga", "nigger", "faggot", "fag", "retard", "cunt",
]

# ── Admin usernames ──────────────────────────────────────────────────────────
# Add Highrise usernames (case-insensitive) for anyone who should have
# access to admin commands: /skip /remove /addtokens /refund /clearqueue
ADMIN_USERS: list[str] = [
    "4ktreymarion",
]
