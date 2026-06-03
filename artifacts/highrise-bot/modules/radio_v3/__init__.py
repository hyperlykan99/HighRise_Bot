"""Isolated Radio V3 package.

Radio V3 is enabled only with RADIO_SYSTEM_VERSION=v3. V1/V2 remain available
for rollback, but V3 owns request playback while active.
"""
from __future__ import annotations

import os


def enabled() -> bool:
    return (os.getenv("RADIO_SYSTEM_VERSION", "v1") or "v1").strip().lower() == "v3"

