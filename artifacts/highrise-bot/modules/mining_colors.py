"""
modules/mining_colors.py
------------------------
Rarity color formatting helpers for the Mining system.

Color format: <#RRGGBB>text<#FFFFFF>  (Highrise rich-text tags).
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Rarity → display name + hex color (None = rainbow / prismatic treatment)
# ---------------------------------------------------------------------------

_RARITY_COLORS: dict[str, str | None] = {
    "common":     "#AAAAAA",
    "uncommon":   "#66BBAA",
    "rare":       "#3399FF",
    "epic":       "#B266FF",
    "legendary":  "#FFD700",
    "mythic":     "#FF66CC",
    "ultra_rare": None,       # Prismatic → rainbow
    "prismatic":  None,       # Prismatic → rainbow
    "exotic":     "#FF0000",
}

_RARITY_DISPLAY_NAMES: dict[str, str] = {
    "common":     "Common",
    "uncommon":   "Uncommon",
    "rare":       "Rare",
    "epic":       "Epic",
    "legendary":  "Legendary",
    "mythic":     "Mythic",
    "ultra_rare": "Prismatic",
    "prismatic":  "Prismatic",
    "exotic":     "Exotic",
}

# Default weight ranges per rarity (kg): (min, max)
RARITY_WEIGHT_RANGES: dict[str, tuple[float, float]] = {
    "common":     (0.50, 2.00),
    "uncommon":   (0.50, 2.00),
    "rare":       (0.75, 3.00),
    "epic":       (1.00, 4.00),
    "legendary":  (1.50, 6.00),
    "mythic":     (2.00, 8.00),
    "ultra_rare": (2.50, 10.00),
    "prismatic":  (2.50, 10.00),
    "exotic":     (3.00, 12.00),
}

# Lowest → highest sort order
RARITY_ORDER: list[str] = [
    "common", "uncommon", "rare", "epic",
    "legendary", "mythic", "ultra_rare", "prismatic", "exotic",
]

# ---------------------------------------------------------------------------
# Rainbow / Prismatic
# ---------------------------------------------------------------------------

_RAINBOW_COLORS: list[str] = [
    "#FF0000", "#FF9900", "#FFFF00",
    "#00FF00", "#00CCFF", "#3366FF",
    "#9933FF", "#FF66CC",
]

# Exact Prismatic rarity label as specified
_PRISMATIC_LABEL = (
    "<#FF0000>P<#FF9900>r<#FFFF00>i<#00FF00>s"
    "<#00CCFF>m<#3366FF>a<#9933FF>t<#FF66CC>i<#FFFFFF>c"
)


def rainbow_text(text: str) -> str:
    """Color text with cycling rainbow colors, one color per letter."""
    if not text:
        return text
    parts: list[str] = []
    idx = 0
    for ch in text:
        if ch == " ":
            parts.append(" ")
        else:
            parts.append(f"<{_RAINBOW_COLORS[idx % len(_RAINBOW_COLORS)]}>{ch}")
            idx += 1
    parts.append("<#FFFFFF>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Public formatters
# ---------------------------------------------------------------------------

def format_mining_rarity(rarity: str) -> str:
    """Return a colored rarity label string."""
    key  = rarity.lower()
    name = _RARITY_DISPLAY_NAMES.get(key, key.replace("_", " ").title())
    if key in ("ultra_rare", "prismatic"):
        return _PRISMATIC_LABEL
    color = _RARITY_COLORS.get(key)
    if color:
        return f"<{color}>{name}<#FFFFFF>"
    return name


def format_ore_name(ore_name: str, rarity: str) -> str:
    """Return ore_name colored by its rarity."""
    key = rarity.lower()
    if key in ("ultra_rare", "prismatic"):
        return rainbow_text(ore_name)
    color = _RARITY_COLORS.get(key)
    if color:
        return f"<{color}>{ore_name}<#FFFFFF>"
    return ore_name


def format_mining_label(label: str) -> str:
    """Return a named mining label string."""
    return _LABELS.get(label.lower(), label)


def get_rarity_display_name(rarity: str) -> str:
    """Return the display name for a rarity key."""
    key = rarity.lower()
    return _RARITY_DISPLAY_NAMES.get(key, key.replace("_", " ").title())


def rarity_sort_key(rarity: str) -> int:
    """Numeric sort key: 0 = lowest rarity."""
    try:
        return RARITY_ORDER.index(rarity.lower())
    except ValueError:
        return 99


def get_default_weight_range(rarity: str) -> tuple[float, float]:
    """Return (min_kg, max_kg) defaults for a rarity."""
    return RARITY_WEIGHT_RANGES.get(rarity.lower(), (0.50, 2.00))


# ---------------------------------------------------------------------------
# Standard mining labels
# ---------------------------------------------------------------------------

_LABELS: dict[str, str] = {
    "mining":       "<#66CCFF>⛏️ Mining<#FFFFFF>",
    "ore":          "<#CC66FF>💎 Ore<#FFFFFF>",
    "weight":       "<#CCCCCC>⚖️ Weight<#FFFFFF>",
    "value":        "<#FFD700>💰 Value<#FFFFFF>",
    "mxp":          "<#00FFAA>⭐ MXP<#FFFFFF>",
    "cooldown":     "<#FFCC00>⏳ Cooldown<#FFFFFF>",
    "warning":      "<#FF3333>⚠️ Warning<#FFFFFF>",
    "success":      "<#00FF66>✅ Success<#FFFFFF>",
    "announcement": "<#FFD700>📣 Big Find<#FFFFFF>",
}

LBL_MINING   = _LABELS["mining"]
LBL_ORE      = _LABELS["ore"]
LBL_WEIGHT   = _LABELS["weight"]
LBL_VALUE    = _LABELS["value"]
LBL_MXP      = _LABELS["mxp"]
LBL_COOLDOWN = _LABELS["cooldown"]
LBL_WARN     = _LABELS["warning"]
LBL_SUCCESS  = _LABELS["success"]
LBL_ANN      = _LABELS["announcement"]
