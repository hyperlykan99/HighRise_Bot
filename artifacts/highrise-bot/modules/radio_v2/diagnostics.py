"""Structured Radio V2 logging."""
from __future__ import annotations


def log(event: str, **fields: object) -> None:
    extras = "".join(f" {k}={v!r}" for k, v in fields.items())
    print(f"[RADIO_V2] event={event}{extras}")

