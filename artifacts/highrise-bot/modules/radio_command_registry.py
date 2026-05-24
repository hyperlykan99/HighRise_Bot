"""
Registry-driven dispatch for radio commands.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from highrise import BaseBot, User

Handler = Callable[["BaseBot", "User", list[str]], Awaitable[None]]


@dataclass(frozen=True)
class RadioCommandEntry:
    command: str
    aliases: tuple[str, ...]
    owner: str
    permission: str
    handler: Handler
    module: str


def _entries() -> tuple[RadioCommandEntry, ...]:
    from modules import radio_commands as rc

    return (
        RadioCommandEntry(
            command="play",
            aliases=("request", "sr", "req", "song", "requesy"),
            owner="dj",
            permission="public",
            handler=rc.handle_request,
            module="modules.radio_commands",
        ),
        RadioCommandEntry(
            command="playfav",
            aliases=(),
            owner="dj",
            permission="public",
            handler=rc.handle_playfav,
            module="modules.radio_commands",
        ),
        RadioCommandEntry(
            command="queue",
            aliases=("q", "djqueue"),
            owner="dj",
            permission="public",
            handler=rc.handle_queue,
            module="modules.radio_commands",
        ),
        RadioCommandEntry(
            command="skip",
            aliases=("djskip",),
            owner="dj",
            permission="staff",
            handler=rc.handle_skip,
            module="modules.radio_commands",
        ),
        RadioCommandEntry(
            command="remove",
            aliases=("djremove", "radioremove"),
            owner="dj",
            permission="staff",
            handler=rc.handle_remove,
            module="modules.radio_commands",
        ),
        RadioCommandEntry(
            command="cancel",
            aliases=(),
            owner="dj",
            permission="public",
            handler=rc.handle_cancel,
            module="modules.radio_commands",
        ),
        RadioCommandEntry(
            command="radiohelp",
            aliases=(),
            owner="dj",
            permission="public",
            handler=rc.handle_radiohelp,
            module="modules.radio_commands",
        ),
        RadioCommandEntry(
            command="radiostatus",
            aliases=("radiohealth",),
            owner="dj",
            permission="public",
            handler=rc.handle_radiostatus,
            module="modules.radio_commands",
        ),
        RadioCommandEntry(
            command="queuelimit",
            aliases=("setqueuelimit",),
            owner="dj",
            permission="staff",
            handler=rc.handle_queuelimit,
            module="modules.radio_commands",
        ),
    )


def entries() -> tuple[RadioCommandEntry, ...]:
    """Return canonical registry entries without alias expansion."""
    return _entries()


def find_duplicate_commands() -> dict[str, list[str]]:
    """Return duplicate registry command/alias mappings for diagnostics."""
    seen: dict[str, str] = {}
    duplicates: dict[str, list[str]] = {}
    for entry in _entries():
        for command in (entry.command, *entry.aliases):
            key = command.lower().strip()
            if key in seen:
                duplicates.setdefault(key, [seen[key]]).append(entry.command)
            else:
                seen[key] = entry.command
    return duplicates


def registry() -> dict[str, RadioCommandEntry]:
    commands: dict[str, RadioCommandEntry] = {}
    for entry in _entries():
        commands[entry.command] = entry
        for alias in entry.aliases:
            commands[alias] = entry
    return commands


def command_names() -> frozenset[str]:
    """Return every radio command and alias handled by this registry."""
    return frozenset(registry().keys())


def lookup(command: str) -> "RadioCommandEntry | None":
    return registry().get((command or "").lower().strip())


async def dispatch_radio_command(
    bot: "BaseBot",
    user: "User",
    args: list[str],
    command: str,
) -> bool:
    entry = lookup(command)
    if not entry:
        return False
    print(
        f"[RADIO_REGISTRY] command={command!r}"
        f" entry={entry.command!r}"
        f" owner={entry.owner!r}"
        f" permission={entry.permission!r}"
        f" handler={entry.handler.__name__!r}"
    )
    await entry.handler(bot, user, args)
    return True
