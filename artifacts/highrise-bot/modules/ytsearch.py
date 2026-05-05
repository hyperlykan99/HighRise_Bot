"""
modules/ytsearch.py
-------------------
YouTube search module.

Commands:
  /ytsearch <song name>  - search YouTube, whisper top results to the user
  /pick <1|2|3>          - queue the chosen result from the last /ytsearch

Flow:
  1. User types /ytsearch blinding lights
  2. Bot whispers up to 3 results (title, channel, link)
  3. User types /pick 2
  4. Bot queues that YouTube link using the full /request flow

Safety:
  - Results expire after 5 minutes so /pick can't use stale data
  - All errors are caught and whispered to the user — the bot never crashes
  - The API key is never printed to logs
"""

import html
import time
import aiohttp
from highrise import BaseBot, User

import config
from modules.dj import handle_dj_command
from modules.cooldowns import check_cooldown, set_cooldown

# Cooldown durations for YouTube commands
YTSEARCH_COOLDOWN = 30   # seconds between /ytsearch uses
PICK_COOLDOWN     = 10   # seconds between /pick uses

# ---------------------------------------------------------------------------
# Per-user result cache (in-memory, with expiry timestamps)
# ---------------------------------------------------------------------------
# Structure:
#   _user_results[user_id] = {
#       "results":    [{"title": ..., "channel": ..., "url": ...}, ...],
#       "expires_at": float  (Unix timestamp — time.time() + RESULT_TTL_SECONDS)
#   }

_user_results: dict[str, dict] = {}

# How long (in seconds) search results are valid before /pick rejects them
RESULT_TTL_SECONDS = 300  # 5 minutes


def _get_cached_results(user_id: str) -> list[dict] | None:
    """
    Return the cached results for a user if they exist and haven't expired.
    Returns None if there are no results or they have expired.
    Automatically removes expired entries.
    """
    entry = _user_results.get(user_id)
    if entry is None:
        return None
    if time.time() > entry["expires_at"]:
        # Results expired — clean up and tell the caller nothing is available
        del _user_results[user_id]
        return None
    return entry["results"]


def _store_results(user_id: str, results: list[dict]):
    """Cache results for a user with a fresh expiry timestamp."""
    _user_results[user_id] = {
        "results":    results,
        "expires_at": time.time() + RESULT_TTL_SECONDS,
    }


def _clear_results(user_id: str):
    """Remove any cached results for a user."""
    _user_results.pop(user_id, None)


# ---------------------------------------------------------------------------
# Public command handlers (called from bot.py)
# ---------------------------------------------------------------------------

async def handle_ytsearch_command(bot: BaseBot, user: User, args: list[str]):
    """
    /ytsearch <song name or artist>

    Searches YouTube and whispers the top results to the user privately.
    Caches results so the user can follow up with /pick within 5 minutes.
    """
    try:
        if len(args) < 2:
            await bot.highrise.send_whisper(user.id, "Usage: /ytsearch <song name or artist>")
            return

        if not config.YOUTUBE_API_KEY:
            await bot.highrise.send_whisper(
                user.id,
                "YouTube search is not configured. Ask the room owner to add the API key."
            )
            return

        # Cooldown check — set immediately to prevent API spam even on slow connections
        remaining = check_cooldown("ytsearch", user.id, YTSEARCH_COOLDOWN)
        if remaining:
            await bot.highrise.send_whisper(
                user.id, f"Please wait {remaining}s before searching again."
            )
            return
        set_cooldown("ytsearch", user.id)

        query = " ".join(args[1:])
        await bot.highrise.send_whisper(user.id, f'Searching for "{query}"...')

        results = await _search_youtube(query)

        if not results:
            _clear_results(user.id)
            await bot.highrise.send_whisper(
                user.id, "No results found. Try a different search term."
            )
            return

        # Cache with expiry so /pick works for the next 5 minutes
        _store_results(user.id, results)
        count = len(results)

        # Header tells the user exactly how many results came back
        await bot.highrise.send_whisper(
            user.id,
            f'Found {count} result(s) for "{query}" — valid for 5 minutes:'
        )

        # One whisper per result to stay within Highrise's character limit
        for i, result in enumerate(results, start=1):
            await bot.highrise.send_whisper(
                user.id,
                f"{i}. {result['title']}\n"
                f"   {result['channel']}\n"
                f"   {result['url']}"
            )

        if count == 1:
            await bot.highrise.send_whisper(user.id, "Type /pick 1 to queue this song.")
        else:
            options = "/".join(f"/pick {n}" for n in range(1, count + 1))
            await bot.highrise.send_whisper(user.id, f"Type {options} to queue a song.")

    except Exception as e:
        print(f"[YTSearch] Unexpected error in handle_ytsearch_command: {type(e).__name__}: {e}")
        await bot.highrise.send_whisper(
            user.id, "Something went wrong with the search. Please try again."
        )


async def handle_pick_command(bot: BaseBot, user: User, args: list[str]):
    """
    /pick <1|2|3>

    Queues the user's chosen result from their last /ytsearch.
    Results expire after 5 minutes. All edge cases whisper a helpful message
    instead of raising an exception.
    """
    try:
        # ── Validate the argument ────────────────────────────────────────────
        if len(args) < 2 or not args[1].isdigit():
            await bot.highrise.send_whisper(user.id, "Usage: /pick 1, /pick 2, or /pick 3")
            return

        # ── Cooldown check ───────────────────────────────────────────────────
        remaining = check_cooldown("pick", user.id, PICK_COOLDOWN)
        if remaining:
            await bot.highrise.send_whisper(
                user.id, f"Please wait {remaining}s before picking again."
            )
            return

        number = int(args[1])

        # ── Look up cached results (also checks expiry) ──────────────────────
        results = _get_cached_results(user.id)

        if results is None:
            await bot.highrise.send_whisper(
                user.id,
                "No search results found. Use /ytsearch <song> first."
            )
            return

        # ── Check the picked number is within what was actually returned ──────
        if number < 1 or number > len(results):
            if len(results) == 1:
                await bot.highrise.send_whisper(
                    user.id,
                    "Invalid pick. Only 1 result was found — use /pick 1."
                )
            else:
                await bot.highrise.send_whisper(
                    user.id,
                    f"Invalid pick. Choose one of the shown results (1–{len(results)})."
                )
            return

        picked = results[number - 1]

        # Confirm the choice to the user before deducting tokens
        await bot.highrise.send_whisper(
            user.id,
            f"Queueing pick #{number}: {picked['title']}"
        )

        # ── Delegate to the standard /request handler ─────────────────────────
        # This applies all the usual rules: token cost, banned words,
        # duplicate detection, and queue size limit.
        await handle_dj_command(bot, user, ["request", picked["url"]])

        # Record the pick cooldown and clear the cache after a successful pick.
        # Note: handle_dj_command also sets the "request" 30s cooldown on success,
        # so both cooldowns are active after a successful /pick.
        set_cooldown("pick", user.id)
        _clear_results(user.id)

    except Exception as e:
        # Log without exposing the API key or any sensitive data
        print(f"[YTPick] Unexpected error for user {user.username}: {type(e).__name__}: {e}")
        await bot.highrise.send_whisper(
            user.id,
            "Something went wrong with your pick. Please try /ytsearch again."
        )


# ---------------------------------------------------------------------------
# YouTube Data API v3 helper
# ---------------------------------------------------------------------------

async def _search_youtube(query: str) -> list[dict]:
    """
    Call the YouTube Data API v3 search endpoint.

    Returns a list of up to 3 dicts, each with:
      - title   : video title (HTML entities decoded)
      - channel : channel display name
      - url     : https://youtube.com/watch?v=<videoId>

    Returns an empty list on any error so callers can handle it gracefully.
    The API key is passed as a query parameter and is NEVER logged.
    """
    endpoint = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part":       "snippet",
        "q":          query,
        "type":       "video",
        "maxResults": 3,
        "key":        config.YOUTUBE_API_KEY,  # secret — never log this
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                endpoint,
                params=params,
                timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                # Log status code only — not the URL, which contains the API key
                if resp.status != 200:
                    print(f"[YTSearch] API returned HTTP {resp.status}")
                    return []

                data = await resp.json()

    except aiohttp.ClientError as e:
        print(f"[YTSearch] Network error: {type(e).__name__}")
        return []
    except Exception as e:
        print(f"[YTSearch] Unexpected error during request: {type(e).__name__}")
        return []

    results = []
    for item in data.get("items", []):
        try:
            video_id = item["id"]["videoId"]
            snippet  = item["snippet"]
            results.append({
                "title":   _decode(snippet.get("title",        "Unknown title")),
                "channel": _decode(snippet.get("channelTitle", "Unknown channel")),
                "url":     f"https://youtube.com/watch?v={video_id}",
            })
        except (KeyError, TypeError):
            # Skip any malformed items without crashing
            continue

    return results


def _decode(text: str) -> str:
    """Decode HTML entities the YouTube API sometimes returns (e.g. &#39; → ')."""
    return html.unescape(text)
