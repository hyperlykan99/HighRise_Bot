"""
bot_names.py — Central bot display-name configuration.

Edit BOT_DISPLAY_NAMES here when a bot is renamed in Highrise.
Internal mode keys (host, banker, miner, …) NEVER change — only display names do.
"""

# ---------------------------------------------------------------------------
# Display names  (mode key → public-facing bot name)
# ---------------------------------------------------------------------------

BOT_DISPLAY_NAMES: dict[str, str] = {
    "host":       "ChillTopiaMC",
    "eventhost":  "ChillTopiaMC",
    "banker":     "BankingBot",
    "shopkeeper": "BankingBot",
    "blackjack":  "AceSinatra",
    "poker":      "ChipSoprano",
    "dealer":     "Dealer",
    "miner":      "GreatestProspector",
    "fisher":     "MasterAngler",
    "security":   "KeanuShield",
    "dj":         "DJ_DUDU",
    "all":        "Main",
}

# ---------------------------------------------------------------------------
# Alias map  (old/external name → internal mode key)
# Use this so old DB records / log entries don't crash lookups.
# ---------------------------------------------------------------------------

BOT_NAME_ALIASES: dict[str, str] = {
    "emceebot":     "host",
    "chilltopia":   "host",
    "chilltopiamc": "host",
    "bankingbot":   "banker",
    "bankerbot":    "banker",
    "acesinatra":   "blackjack",
    "chipsoprano":  "poker",
    "greatestprospector": "miner",
    "masterangler": "fisher",
    "keanushield":  "security",
    "dj_dudu":      "dj",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_display_name(mode: str, fallback: str = "") -> str:
    """Return the public display name for an internal mode key.
    Falls back to `fallback` (or the mode key itself) if not found."""
    return BOT_DISPLAY_NAMES.get(mode, fallback or mode)


def resolve_mode(name: str) -> str:
    """Resolve a display name or alias back to its internal mode key.
    Returns the input unchanged if no alias is found."""
    return BOT_NAME_ALIASES.get(name.lower(), name.lower())


def get_host_display_name() -> str:
    """Convenience: display name for the host/emcee bot."""
    return BOT_DISPLAY_NAMES.get("host", "ChillTopiaMC")
