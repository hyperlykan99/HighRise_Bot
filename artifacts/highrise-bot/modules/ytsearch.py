"""
modules/ytsearch.py
-------------------
YouTube search module.

User command:
  /ytsearch <song name>  - search YouTube and return the top 3 results

Each result shows:
  - title
  - channel name
  - direct YouTube link

The user can then copy the link and queue it with /request <link>.

No audio is downloaded or streamed — only official metadata is used.
"""

import aiohttp
from highrise import BaseBot, User
import config


async def handle_ytsearch_command(bot: BaseBot, user: User, args: list[str]):
    """
    Entry point for /ytsearch.

    Parameters
    ----------
    bot  : running bot instance
    user : the Highrise user who typed the command
    args : words after '/', e.g. ["ytsearch", "Blinding", "Lights"]
    """
    # Need at least one search term after the command name
    if len(args) < 2:
        await bot.highrise.send_whisper(user.id, "Usage: /ytsearch <song name or artist>")
        return

    # Check that the API key is configured
    if not config.YOUTUBE_API_KEY:
        await bot.highrise.send_whisper(
            user.id, "YouTube search is not set up yet. Ask the room owner to add the API key."
        )
        return

    query = " ".join(args[1:])
    await bot.highrise.send_whisper(user.id, f'Searching YouTube for "{query}"...')

    # Fetch results from the YouTube Data API v3
    results = await _search_youtube(query)

    if not results:
        await bot.highrise.send_whisper(
            user.id,
            "No results found. Check your search term or try again in a moment."
        )
        return

    # Send a header then one whisper per result to stay inside Highrise's
    # per-message character limit
    await bot.highrise.send_whisper(user.id, f'Top results for "{query}":')

    for i, result in enumerate(results, start=1):
        await bot.highrise.send_whisper(
            user.id,
            f"{i}. {result['title']}\n"
            f"   {result['channel']}\n"
            f"   {result['url']}"
        )

    # Remind the user how to actually queue one of the links
    await bot.highrise.send_whisper(
        user.id, "Copy a link above and use /request <link> to add it to the queue."
    )


async def _search_youtube(query: str) -> list[dict]:
    """
    Call the YouTube Data API v3 search endpoint and return up to 3 results.

    Each result dict has:
      - title   : video title (HTML entities decoded)
      - channel : channel display name
      - url     : full https://youtube.com/watch?v=... link

    Returns an empty list on any error (network failure, bad API key, etc.)
    so the caller can handle it gracefully.
    """
    endpoint = "https://www.googleapis.com/youtube/v3/search"

    params = {
        "part":       "snippet",    # we only need basic metadata
        "q":          query,        # the search term
        "type":       "video",      # only return videos, not channels/playlists
        "maxResults": 3,            # top 3 results
        "key":        config.YOUTUBE_API_KEY,
    }

    try:
        # aiohttp is already installed as a dependency of highrise-bot-sdk
        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, params=params, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    # Log the error server-side without crashing the bot
                    print(f"[YTSearch] API error {resp.status}: {await resp.text()}")
                    return []

                data = await resp.json()

    except Exception as e:
        print(f"[YTSearch] Request failed: {e}")
        return []

    results = []
    for item in data.get("items", []):
        video_id = item.get("id", {}).get("videoId")
        if not video_id:
            continue  # safety check — skip malformed items

        snippet = item.get("snippet", {})
        title   = _decode_html(snippet.get("title", "Unknown title"))
        channel = _decode_html(snippet.get("channelTitle", "Unknown channel"))

        results.append({
            "title":   title,
            "channel": channel,
            "url":     f"https://youtube.com/watch?v={video_id}",
        })

    return results


def _decode_html(text: str) -> str:
    """
    Decode common HTML entities that the YouTube API sometimes returns
    in titles and channel names (e.g. &#39; → ', &amp; → &).
    Uses the standard library so no extra dependencies are needed.
    """
    import html
    return html.unescape(text)
