"""
modules/suggestions.py
-----------------------
Player suggestions, bug reports, and event voting.

/suggest <message>       — submit a suggestion (everyone)
/suggestions             — view recent suggestions (manager+)
/bugreport <message>     — submit a bug report (everyone)
/bugreports              — view recent bug reports (manager+)
/eventvote               — show current event vote counts (everyone)
/voteevent <choice>      — cast or change your vote (everyone)
"""
from __future__ import annotations

import database as db
from modules.permissions import can_manage_economy

_VOTE_CHOICES: frozenset[str] = frozenset({
    "fishing", "mining", "jackpot", "chill", "hype",
})


async def _w(bot, uid: str, msg: str) -> None:
    try:
        await bot.highrise.send_whisper(uid, str(msg)[:249])
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /suggest <message>
# ---------------------------------------------------------------------------

async def handle_suggest(bot, user, args: list[str]) -> None:
    """/suggest <message> — submit a suggestion."""
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /suggest <your suggestion>")
        return
    message = " ".join(args[1:])[:400]
    db.add_suggestion(user.id, user.username, message)
    await _w(bot, user.id,
             "💡 Suggestion received!\n"
             "Thank you — staff will review it soon.")


# ---------------------------------------------------------------------------
# /suggestions  (manager+)
# ---------------------------------------------------------------------------

async def handle_suggestions(bot, user) -> None:
    """/suggestions — view recent suggestions (manager+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/owner only.")
        return
    rows = db.get_suggestions(limit=8)
    if not rows:
        await _w(bot, user.id, "💡 No suggestions submitted yet.")
        return
    lines = [f"💡 Suggestions ({len(rows)})"]
    for r in rows:
        uname = r.get("username", "?")
        msg   = r.get("message", "")[:55]
        lines.append(f"#{r['id']} @{uname}: {msg}")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /bugreport <message>
# ---------------------------------------------------------------------------

async def handle_bugreport(bot, user, args: list[str]) -> None:
    """/bugreport <message> — report a bug."""
    if len(args) < 2:
        await _w(bot, user.id, "Usage: /bugreport <describe the bug>")
        return
    message = " ".join(args[1:])[:400]
    db.add_bug_report(user.id, user.username, message)
    await _w(bot, user.id,
             "🐛 Bug report received!\n"
             "Thank you — staff will look into it.")


# ---------------------------------------------------------------------------
# /bugreports  (manager+)
# ---------------------------------------------------------------------------

async def handle_bugreports(bot, user) -> None:
    """/bugreports — view recent bug reports (manager+)."""
    if not can_manage_economy(user.username):
        await _w(bot, user.id, "Manager/owner only.")
        return
    rows = db.get_bug_reports(limit=8)
    if not rows:
        await _w(bot, user.id, "🐛 No bug reports submitted yet.")
        return
    lines = [f"🐛 Bug Reports ({len(rows)})"]
    for r in rows:
        uname = r.get("username", "?")
        msg   = r.get("message", "")[:55]
        lines.append(f"#{r['id']} @{uname}: {msg}")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /eventvote  — show vote tallies
# ---------------------------------------------------------------------------

async def handle_eventvote(bot, user) -> None:
    """/eventvote — view current event vote counts."""
    counts = db.get_event_vote_counts()
    if not counts:
        await _w(bot, user.id,
                 "🎉 Event Vote\n"
                 "No votes yet.\n"
                 "Cast yours: /voteevent <fishing|mining|jackpot|chill|hype>")
        return
    lines = ["🎉 Event Vote Counts"]
    for choice, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        bar = "▓" * min(cnt, 10)
        lines.append(f"{choice.title()}: {cnt} {bar}")
    lines.append("Vote: /voteevent <choice>")
    await _w(bot, user.id, "\n".join(lines)[:249])


# ---------------------------------------------------------------------------
# /voteevent <choice>
# ---------------------------------------------------------------------------

async def handle_voteevent(bot, user, args: list[str]) -> None:
    """/voteevent <choice> — cast or change your event vote."""
    if len(args) < 2:
        opts = "/".join(sorted(_VOTE_CHOICES))
        await _w(bot, user.id, f"Usage: /voteevent <{opts}>")
        return
    choice = args[1].lower()
    if choice not in _VOTE_CHOICES:
        opts = ", ".join(sorted(_VOTE_CHOICES))
        await _w(bot, user.id, f"Invalid choice.\nOptions: {opts}")
        return
    is_new = db.cast_event_vote(user.id, user.username, choice)
    if is_new:
        await _w(bot, user.id,
                 f"🎉 Vote cast: {choice.title()}!\n"
                 f"See totals: /eventvote")
    else:
        await _w(bot, user.id,
                 f"🎉 Vote updated: {choice.title()}!\n"
                 f"See totals: /eventvote")
