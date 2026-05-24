"""
Small helpers for bot startup task orchestration.
"""
from __future__ import annotations

import asyncio
import traceback
from collections.abc import Awaitable
from typing import Any


def create_guarded_startup_task(coro: Awaitable[Any], label: str) -> asyncio.Task[Any]:
    """Run a startup coroutine in a named task and log failures without crashing."""
    async def _guarded() -> None:
        try:
            await coro
        except Exception as exc:
            print(f"[TASK ERROR] {label} failed: {exc!r}")
            traceback.print_exc()

    return asyncio.create_task(_guarded(), name=f"startup:{label}")
