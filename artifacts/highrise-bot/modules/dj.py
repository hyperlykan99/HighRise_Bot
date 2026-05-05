"""
modules/dj.py
-------------
DJ Request System module.

Handles all DJ-related commands:
  /dj       - explain the DJ system
  /request  - add a song to the queue (costs tokens)
  /queue    - show next 5 songs
  /now      - show current song
  /skipvote - vote to skip the current song

Admin commands:
  /skip           - force skip current song
  /remove <pos>   - remove a song by queue position (1-indexed)
"""

from highrise import BaseBot, User
import database as db
import config

# Tracks which users have voted to skip the current song this round.
# Cleared whenever the song at the front of the queue changes.
_skip_votes: set[str] = set()

# The database row ID of the song the current skip votes are aimed at.
# Used to detect when the current song has changed between vote calls.
_skip_vote_song_id: int | None = None


async def handle_dj_command(bot: BaseBot, user: User, args: list[str]):
    """
    Route a DJ user command to the correct handler.

    Parameters
    ----------
    bot  : running bot instance (used to send messages via bot.highrise)
    user : the Highrise user who typed the command
    args : words after the '/', e.g. ["request", "Blinding", "Lights"]
    """
    if not args:
        return

    cmd = args[0].lower()

    if cmd == "dj":
        await _cmd_dj_info(bot, user)
    elif cmd == "request":
        song = " ".join(args[1:]).strip()
        await _cmd_request(bot, user, song)
    elif cmd == "queue":
        await _cmd_queue(bot, user)
    elif cmd == "now":
        await _cmd_now(bot, user)
    elif cmd == "skipvote":
        await _cmd_skipvote(bot, user)


async def handle_dj_admin_command(bot: BaseBot, user: User, args: list[str]):
    """
    Route an admin-only DJ command.

    Parameters
    ----------
    bot  : running bot instance
    user : the admin user who typed the command
    args : words after the '/', e.g. ["skip"] or ["remove", "2"]
    """
    if not args:
        return

    cmd = args[0].lower()

    if cmd == "skip":
        await _cmd_admin_skip(bot, user)
    elif cmd == "remove":
        await _cmd_admin_remove(bot, user, args[1:])


# ---------------------------------------------------------------------------
# User commands
# ---------------------------------------------------------------------------

async def _cmd_dj_info(bot: BaseBot, user: User):
    """Whisper an explanation of the DJ system to the requesting user."""
    msg = (
        "DJ Request System\n"
        f"Use /request <song name or link> to add a song to the queue.\n"
        f"Each request costs {config.SONG_REQUEST_COST} tokens.\n"
        "Use /queue to see upcoming songs, /now to see what's playing.\n"
        "Earn free tokens daily with /daily!"
    )
    await bot.highrise.send_whisper(user.id, msg)


async def _cmd_request(bot: BaseBot, user: User, song: str):
    """
    Add a song to the queue if the user has enough tokens.
    Deducts the cost from their balance and saves the request to the DB.
    Announces the new addition in the public room chat.
    """
    global _skip_votes, _skip_vote_song_id

    db.ensure_user(user.id, user.username)

    if not song:
        await bot.highrise.send_whisper(user.id, "Usage: /request <song name or link>")
        return

    balance = db.get_balance(user.id)

    if balance < config.SONG_REQUEST_COST:
        await bot.highrise.send_whisper(
            user.id,
            f"Not enough tokens! You have {balance} but need {config.SONG_REQUEST_COST}. "
            "Use /daily for free tokens."
        )
        return

    # Deduct tokens first, then add to queue
    db.adjust_balance(user.id, -config.SONG_REQUEST_COST)
    position   = db.add_to_queue(user.id, user.username, song)
    new_balance = db.get_balance(user.id)

    # Public announcement so the room sees the request
    await bot.highrise.chat(
        f"@{user.username} added: {song} (queue position #{position})"
    )
    # Private confirmation with balance info
    await bot.highrise.send_whisper(
        user.id,
        f"Added to queue at position #{position}. "
        f"Spent {config.SONG_REQUEST_COST} tokens. Balance: {new_balance}"
    )

    # If this is the only song, initialise skip-vote tracking for it
    if position == 1:
        _skip_votes = set()
        current = db.get_current_song()
        _skip_vote_song_id = current["id"] if current else None


async def _cmd_queue(bot: BaseBot, user: User):
    """Whisper the next N songs in the queue to the requesting user."""
    songs = db.get_queue(config.QUEUE_DISPLAY_SIZE)

    if not songs:
        await bot.highrise.send_whisper(
            user.id, "The queue is empty. Be the first to /request a song!"
        )
        return

    lines = ["Upcoming songs:"]
    for i, song in enumerate(songs, start=1):
        lines.append(f"  {i}. {song['song']}  (by @{song['username']})")

    total = db.get_queue_length()
    if total > config.QUEUE_DISPLAY_SIZE:
        lines.append(f"  ...and {total - config.QUEUE_DISPLAY_SIZE} more in the queue.")

    await bot.highrise.send_whisper(user.id, "\n".join(lines))


async def _cmd_now(bot: BaseBot, user: User):
    """Whisper which song is currently at the front of the queue."""
    song = db.get_current_song()

    if not song:
        await bot.highrise.send_whisper(
            user.id, "Nothing is playing right now. Use /request to add a song!"
        )
        return

    await bot.highrise.send_whisper(
        user.id,
        f"Now playing: {song['song']}  (requested by @{song['username']})"
    )


async def _cmd_skipvote(bot: BaseBot, user: User):
    """
    Cast a skip vote for the current song.
    When the threshold is reached the song is removed from the queue
    and the next one is announced publicly.
    """
    global _skip_votes, _skip_vote_song_id

    db.ensure_user(user.id, user.username)
    current = db.get_current_song()

    if not current:
        await bot.highrise.send_whisper(user.id, "There is nothing playing to skip!")
        return

    # If the song changed since the last vote round, reset the vote set
    if _skip_vote_song_id != current["id"]:
        _skip_votes       = set()
        _skip_vote_song_id = current["id"]

    if user.id in _skip_votes:
        await bot.highrise.send_whisper(user.id, "You already voted to skip this song.")
        return

    _skip_votes.add(user.id)
    votes_so_far = len(_skip_votes)
    needed       = config.SKIP_VOTE_THRESHOLD

    if votes_so_far >= needed:
        # Threshold reached — skip automatically
        skipped   = db.skip_current_song()
        _skip_votes       = set()
        next_song = db.get_current_song()
        _skip_vote_song_id = next_song["id"] if next_song else None

        await bot.highrise.chat(
            f"'{skipped['song']}' was voted to skip by the room!"
        )
        if next_song:
            await bot.highrise.chat(
                f"Up next: {next_song['song']}  (by @{next_song['username']})"
            )
        else:
            await bot.highrise.chat(
                "The queue is now empty. Use /request to add a song!"
            )
    else:
        remaining = needed - votes_so_far
        await bot.highrise.chat(
            f"@{user.username} voted to skip. Need {remaining} more vote(s)."
        )


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

async def _cmd_admin_skip(bot: BaseBot, user: User):
    """Force-skip the current song without needing votes (admin only)."""
    global _skip_votes, _skip_vote_song_id

    skipped = db.skip_current_song()

    if not skipped:
        await bot.highrise.send_whisper(user.id, "The queue is already empty.")
        return

    _skip_votes       = set()
    next_song = db.get_current_song()
    _skip_vote_song_id = next_song["id"] if next_song else None

    await bot.highrise.chat(
        f"[Admin] @{user.username} skipped: {skipped['song']}"
    )
    if next_song:
        await bot.highrise.chat(
            f"Up next: {next_song['song']}  (by @{next_song['username']})"
        )
    else:
        await bot.highrise.chat("The queue is now empty.")


async def _cmd_admin_remove(bot: BaseBot, user: User, args: list[str]):
    """
    Remove a specific song from the queue by its 1-indexed position (admin only).
    Usage: /remove <queue number>
    """
    if not args or not args[0].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: /remove <queue number>")
        return

    position = int(args[0])
    removed  = db.remove_from_queue(position)

    if not removed:
        await bot.highrise.send_whisper(
            user.id,
            f"No song at position #{position}. Use /queue to see current positions."
        )
        return

    await bot.highrise.chat(
        f"[Admin] @{user.username} removed #{position}: {removed['song']}"
    )
