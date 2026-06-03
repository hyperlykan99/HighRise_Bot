"""
Isolated Radio V2 package for DJ_DUDU.

Enabled only when RADIO_SYSTEM_VERSION=v2.  V1 modules remain available and
unchanged for the default path.
"""
from __future__ import annotations

import os


def enabled() -> bool:
    return (os.getenv("RADIO_SYSTEM_VERSION", "v1") or "v1").strip().lower() == "v2"

