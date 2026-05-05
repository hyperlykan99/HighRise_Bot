"""
modules/ytsearch.py
-------------------
YouTube search module.

Commands:
  /ytsearch <song name>  - search YouTube and whisper top 3 results to the user
  /pick <1|2|3>          - queue the chosen result from the last /ytsearch

Flow:
  1. User types /ytsearch blinding lights
  2. Bot whispers 3 results (title, channel, link)
  3. User types /pick 2
  4. Bot queues that YouTube link exactly like /request <link>

No audio is downloaded, ripped, or converted at any point.
Only official YouTube metadata and video links are used.
The API key is read from config and never printed to logs.
"""

import html
import aiohttp
from highrise import BaseBot, User

import config

# Import the DJ request handler so /pick can reuse all the token/duplicate/
# queue-limit logic without duplicating it.
from modules.dj import handle_dj_command

# ---------------------------------------------------------------------------
# Per-user result cache (in-memory)
# ---------------------------------------------------------------------------
# Stores the last /ytsearch results for each user so /pick can reference them.
# Key   : Highrise user ID
# Value : list of result dicts (up to 3)
#
# This is intentionally in-memory only — results expire when the bot restarts,
# which is fine since stale search results aren't useful anyway.
_user_results: dict[str, list[dict]] = {}


# ---------------------------------------------------------------------------
# Public command handlers (called from bot.py)
# ---------------------------------------------------------------------------

async def handle_ytsearch_command(bot: BaseBot, user: User, args: list[str]):
    """
    /ytsearch <song name or artist>

    Searches YouTube and whispers the top 3 video results to the user.
    Stores the results so the user can immediately follow up with /pick.
    """
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: /ytsearch <song name or artist>")
        return

    if not config.YOUTUBE_API_KEY:
        await bot.highrise.send_whisper(
            user.id,
            "YouTube search is not configured. Ask the room owner to add the API key."
        )
        return

    query = " ".join(args[1:])
    await bot.highrise.send_whisper(user.id, f'Searching for "{query}"...')

    results = await _search_youtube(query)

    if not results:
        # Clear any stale cached results so /pick won't use old data
        _user_results.pop(user.id, None)
        await bot.highrise.send_whisper(
            user.id, "No results found. Try a different search term."
        )
        return

    # Cache results for this user so /pick can reference them
    _user_results[user.id] = results

    # Send one whisper per result to stay safely inside Highrise's size limit
    await bot.highrise.send_whisper(user.id, f'Top results for "{query}":')

    for i, result in enumerate(results, start=1):
        await bot.highrise.send_whisper(
            user.id,
            f"{i}. {result['title']}\n"
            f"   {result['channel']}\n"
            f"   {result['url']}"
        )

    await bot.highrise.send_whisper(user.id, "Type /pick 1, /pick 2, or /pick 3 to queue a song.")


async def handle_pick_command(bot: BaseBot, user: User, args: list[str]):
    """
    /pick <1|2|3>

    Queues the chosen result from the user's most recent /ytsearch.
    Reuses the full /request flow (token cost, duplicate check, queue limit, etc.).
    """
    # Validate the argument
    if len(args) < 2 or not args[1].isdigit():
        await bot.highrise.send_whisper(user.id, "Usage: /pick 1, /pick 2, or /pick 3")
        return

    number = int(args[1])

    if number < 1 or number > 3:
        await bot.highrise.send_whisper(user.id, "Please pick 1, 2, or 3.")
        return

    # Look up the cached results for this user
    results = _user_results.get(user.id)

    if not results:
        await bot.highrise.send_whisper(
            user.id, "No search results to pick from. Use /ytsearch first."
        )
        return

    if number > len(results):
        await bot.highrise.send_whisper(
            user.id, f"Only {len(results)} result(s) available. Pick a lower number."
        )
        return

    picked = results[number - 1]

    # Confirm what the user picked before queuing
    await bot.highrise.send_whisper(
        user.id,
        f"Queueing: {picked['title']}\n{picked['url']}"
    )

    # Delegate to the standard /request handler with the YouTube URL.
    # This applies all the usual rules: token cost, banned words,
    # duplicate detection, and queue size limit.
    await handle_dj_command(bot, user, ["request", picked["url"]])

    # Clear the cache for this user after a successful pick so stale
    # results can't be accidentally re-picked later.
    _user_results.pop(user.id, None)


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

    Returns an empty list on any error so the caller handles it gracefully.
    The API key is passed as a query parameter and is never logged.
    """
    endpoint = "https://www.googleapis.com/youtube/v3/search"

    params = {
        "part":       "snippet",   # only need basic metadata
        "q":          query,       # the search term
        "type":       "video",     # exclude channels and playlists
        "maxResults": 3,           # top 3 results only
        "key":        config.YOUTUBE_API_KEY,  # never print this value
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                endpoint,
                params=params,
                timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:

                if resp.status != 200:
                    # Log the status code only — not the URL (which contains the key)
                    print(f"[YTSearch] API returned status {resp.status}")
                    return []

                data = await resp.json()

    except Exception as e:
        # Log the exception type only — not the full repr which could include params
        print(f"[YTSearch] Request failed: {type(e).__name__}: {e}")
        return []

    results = []
    for item in data.get("items", []):
        video_id = item.get("id", {}).get("videoId")
        if not video_id:
            continue  # skip malformed items

        snippet = item.get("snippet", {})
        results.append({
            "title":   _decode(snippet.get("title",        "Unknown title")),
            "channel": _decode(snippet.get("channelTitle", "Unknown channel")),
            "url":     f"https://youtube.com/watch?v={video_id}",
        })

    return results


def _decode(text: str) -> str:
    """Decode HTML entities the YouTube API sometimes returns (e.g. &#39; → ')."""
    return html.unescape(text)
