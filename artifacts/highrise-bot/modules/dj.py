"""
modules/dj.py
-------------
DJ Request System module.

User commands:
  /dj                - explain the DJ system
  /request  <song>  - add a song to the queue (20 tokens)
  /priority <song>  - jump to #2 in queue     (50 tokens)
  /queue            - show next 5 songs
  /now              - show current song
  /skipvote         - vote to skip the current song

Admin commands:
  /skip             - force skip current song
  /remove <pos>     - remove song by queue position (1-indexed)
  /clearqueue       - wipe the entire queue
"""

from highrise import BaseBot, User
import database as db
import config
from modules.cooldowns import check_cooldown, set_cooldown

# Cooldown duration (in seconds) applied to /request and /priority on success
REQUEST_COOLDOWN = 30

# ---------------------------------------------------------------------------
# Skip-vote state (in-memory; resets when the bot restarts)
# ---------------------------------------------------------------------------

# Set of user IDs who have voted to skip the current song
_skip_votes: set[str] = set()

# DB row ID of the song the current votes are targeting.
# Used to detect when the song at the front has changed.
_skip_vote_song_id: int | None = None


# ---------------------------------------------------------------------------
# Public routing functions (called from bot.py)
# ---------------------------------------------------------------------------

async def handle_dj_command(bot: BaseBot, user: User, args: list[str]):
    """
    Route a DJ user command to the correct handler.
    args[0] is the command name, args[1:] are its arguments.
    """
    if not args:
        return

    cmd = args[0].lower()

    if cmd == "dj":
        await _cmd_dj_info(bot, user)
    elif cmd == "request":
        song = " ".join(args[1:]).strip()
        await _cmd_request(bot, user, song, priority=False)
    elif cmd == "priority":
        song = " ".join(args[1:]).strip()
        await _cmd_request(bot, user, song, priority=True)
    elif cmd == "queue":
        await _cmd_queue(bot, user)
    elif cmd == "now":
        await _cmd_now(bot, user)
    elif cmd == "skipvote":
        await _cmd_skipvote(bot, user)


async def handle_dj_admin_command(bot: BaseBot, user: User, args: list[str]):
    """Route an admin-only DJ command."""
    if not args:
        return

    cmd = args[0].lower()

    if cmd == "skip":
        await _cmd_admin_skip(bot, user)
    elif cmd == "remove":
        await _cmd_admin_remove(bot, user, args[1:])
    elif cmd == "clearqueue":
        await _cmd_admin_clearqueue(bot, user)


# ---------------------------------------------------------------------------
# Content validation helpers
# ---------------------------------------------------------------------------

def _contains_banned_word(text: str) -> str | None:
    """
    Check if the text contains any banned word (case-insensitive).
    Returns the matched banned word if found, otherwise None.
    """
    lower = text.lower()
    for word in config.BANNED_WORDS:
        if word.lower() in lower:
            return word
    return None


def _validate_request(song: str) -> str | None:
    """
    Run all pre-add checks on a song title.
    Returns an error message string if the request should be rejected,
    or None if everything is fine.
    """
    if not song:
        return "Please include a song name or link."

    # Banned word check
    bad_word = _contains_banned_word(song)
    if bad_word:
        return "That song title contains a banned word and cannot be requested."

    # Duplicate check
    if db.is_song_in_queue(song):
        return "That song is already in the queue!"

    # Queue full check
    if db.get_queue_length() >= config.QUEUE_MAX_SIZE:
        return f"The queue is full ({config.QUEUE_MAX_SIZE} songs max). Try again soon!"

    return None  # all good


# ---------------------------------------------------------------------------
# User commands
# ---------------------------------------------------------------------------

async def _cmd_dj_info(bot: BaseBot, user: User):
    """Whisper an explanation of the DJ system to the requesting user."""
    msg = (
        "DJ Request System\n"
        f"/request <song> - {config.SONG_REQUEST_COST} tokens, adds to queue\n"
        f"/priority <song> - {config.PRIORITY_REQUEST_COST} tokens, jumps to #2\n"
        f"Queue limit: {config.QUEUE_MAX_SIZE} songs. No duplicates.\n"
        "Use /queue, /now, /skipvote, /balance, /daily"
    )
    await bot.highrise.send_whisper(user.id, msg)


async def _cmd_request(bot: BaseBot, user: User, song: str, priority: bool):
    """
    Shared handler for both /request (normal) and /priority.

    Validates the song, checks the user's balance, deducts tokens,
    and adds the song to the queue. Priority songs sort to position #2
    (right after whatever is currently playing).

    Parameters
    ----------
    priority : True  → /priority command (costs PRIORITY_REQUEST_COST)
               False → /request command  (costs SONG_REQUEST_COST)
    """
    global _skip_votes, _skip_vote_song_id

    db.ensure_user(user.id, user.username)

    # Cooldown check — applied to both /request and /priority
    # Uses a shared "request" key so both commands share the same 30-second window
    remaining = check_cooldown("request", user.id, REQUEST_COOLDOWN)
    if remaining:
        await bot.highrise.send_whisper(
            user.id, f"Please wait {remaining}s before requesting another song."
        )
        return

    # Run all content / queue-state checks first
    error = _validate_request(song)
    if error:
        await bot.highrise.send_whisper(user.id, error)
        return

    cost = config.PRIORITY_REQUEST_COST if priority else config.SONG_REQUEST_COST
    balance = db.get_balance(user.id)

    if balance < cost:
        await bot.highrise.send_whisper(
            user.id,
            f"Not enough tokens! You have {balance} but need {cost}. "
            "Use /daily for free tokens."
        )
        return

    # All checks passed — deduct and add to queue
    db.adjust_balance(user.id, -cost)
    position    = db.add_to_queue(user.id, user.username, song, priority=priority)
    new_balance = db.get_balance(user.id)

    # Record cooldown only after a successful request (failed attempts don't penalise)
    set_cooldown("request", user.id)

    label = "PRIORITY" if priority else "added"
    await bot.highrise.chat(
        f"[{label}] @{user.username}: {song} (position #{position})"
    )
    await bot.highrise.send_whisper(
        user.id,
        f"Queued at #{position}. Spent {cost} tokens. Balance: {new_balance}"
    )

    # If this is the first song ever added, initialise skip-vote tracking
    if position == 1:
        _skip_votes       = set()
        current           = db.get_current_song()
        _skip_vote_song_id = current["id"] if current else None


async def _cmd_queue(bot: BaseBot, user: User):
    """Whisper the next N songs in the queue to the requesting user."""
    songs = db.get_queue(config.QUEUE_DISPLAY_SIZE)

    if not songs:
        await bot.highrise.send_whisper(
            user.id, "The queue is empty. Use /request to add a song!"
        )
        return

    lines = [f"Queue ({db.get_queue_length()}/{config.QUEUE_MAX_SIZE}):"]
    for i, s in enumerate(songs, start=1):
        tag = " [P]" if s["priority"] and i > 1 else ""
        lines.append(f"  {i}. {s['song']}{tag}  (@{s['username']})")

    total = db.get_queue_length()
    if total > config.QUEUE_DISPLAY_SIZE:
        lines.append(f"  ...and {total - config.QUEUE_DISPLAY_SIZE} more.")

    await bot.highrise.send_whisper(user.id, "\n".join(lines))


async def _cmd_now(bot: BaseBot, user: User):
    """Whisper which song is currently at the front of the queue."""
    song = db.get_current_song()

    if not song:
        await bot.highrise.send_whisper(
            user.id, "Nothing is playing. Use /request to add a song!"
        )
        return

    await bot.highrise.send_whisper(
        user.id,
        f"Now playing: {song['song']}  (by @{song['username']})"
    )


async def _cmd_skipvote(bot: BaseBot, user: User):
    """
    Cast a skip vote for the current song.
    When SKIP_VOTE_THRESHOLD votes accumulate the song is auto-skipped.
    """
    global _skip_votes, _skip_vote_song_id

    db.ensure_user(user.id, user.username)
    current = db.get_current_song()

    if not current:
        await bot.highrise.send_whisper(user.id, "Nothing is playing to skip!")
        return

    # Reset votes if the song at the front changed since the last vote
    if _skip_vote_song_id != current["id"]:
        _skip_votes        = set()
        _skip_vote_song_id = current["id"]

    if user.id in _skip_votes:
        await bot.highrise.send_whisper(user.id, "You already voted to skip this song.")
        return

    _skip_votes.add(user.id)
    votes_so_far = len(_skip_votes)
    needed       = config.SKIP_VOTE_THRESHOLD

    if votes_so_far >= needed:
        skipped            = db.skip_current_song()
        _skip_votes        = set()
        next_song          = db.get_current_song()
        _skip_vote_song_id = next_song["id"] if next_song else None

        await bot.highrise.chat(f"'{skipped['song']}' was voted to skip!")
        if next_song:
            await bot.highrise.chat(
                f"Up next: {next_song['song']}  (@{next_song['username']})"
            )
        else:
            await bot.highrise.chat("Queue is empty. Use /request to add a song!")
    else:
        remaining = needed - votes_so_far
        await bot.highrise.chat(
            f"@{user.username} voted to skip. Need {remaining} more vote(s)."
        )


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

async def _cmd_admin_skip(bot: BaseBot, user: User):
    """Force-skip the current song (admin only)."""
    global _skip_votes, _skip_vote_song_id

    skipped = db.skip_current_song()

    if not skipped:
        await bot.highrise.send_whisper(user.id, "The queue is already empty.")
        return

    _skip_votes        = set()
    next_song          = db.get_current_song()
    _skip_vote_song_id = next_song["id"] if next_song else None

    await bot.highrise.chat(f"[Admin] Skipped: {skipped['song']}")
    if next_song:
        await bot.highrise.chat(
            f"Up next: {next_song['song']}  (@{next_song['username']})"
        )
    else:
        await bot.highrise.chat("Queue is now empty.")


async def _cmd_admin_remove(bot: BaseBot, user: User, args: list[str]):
    """Remove a song by its visible queue position (admin only). Usage: /remove <#>"""
    if not args or not args[0].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: /remove <queue number>")
        return

    position = int(args[0])
    removed  = db.remove_from_queue(position)

    if not removed:
        await bot.highrise.send_whisper(
            user.id, f"No song at position #{position}. Use /queue to check."
        )
        return

    await bot.highrise.chat(
        f"[Admin] @{user.username} removed #{position}: {removed['song']}"
    )


async def _cmd_admin_clearqueue(bot: BaseBot, user: User):
    """Wipe every song from the queue (admin only)."""
    global _skip_votes, _skip_vote_song_id

    count = db.clear_queue()
    _skip_votes        = set()
    _skip_vote_song_id = None

    if count == 0:
        await bot.highrise.send_whisper(user.id, "The queue was already empty.")
    else:
        await bot.highrise.chat(
            f"[Admin] @{user.username} cleared the queue ({count} song(s) removed)."
        )
