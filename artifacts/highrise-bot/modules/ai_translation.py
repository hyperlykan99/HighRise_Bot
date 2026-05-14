"""
modules/ai_translation.py — Built-in translation dictionary (3.3B).

Handles these patterns without any internet access:
  • "what is the spanish of hello"
  • "what is hello in spanish"
  • "translate hello to spanish"
  • "how do you say hello in spanish"
  • "what is salamat in english"   (reverse lookup)

Languages: Spanish, Tagalog, Japanese, French, Korean, English (reverse).
"""
from __future__ import annotations

import re

# ── Language display names ────────────────────────────────────────────────────
_LANGS: dict[str, str] = {
    "spanish":  "Spanish",
    "tagalog":  "Tagalog",
    "japanese": "Japanese",
    "french":   "French",
    "korean":   "Korean",
    "english":  "English",
    "german":   "German",
    "chinese":  "Chinese",
    "arabic":   "Arabic",
}

_LANG_PAT = (
    "(?P<lang>spanish|tagalog|japanese|french|korean|english|german|chinese|arabic)"
)

# ── Built-in dictionary (english phrase → target language → translation) ──────
# Keys: (english_phrase, target_lang_lower)
_DICT: dict[tuple[str, str], str] = {
    # Spanish
    ("hello",         "spanish"): "Hola",
    ("thank you",     "spanish"): "Gracias",
    ("good morning",  "spanish"): "Buenos días",
    ("good night",    "spanish"): "Buenas noches",
    ("goodbye",       "spanish"): "Adiós",
    ("love",          "spanish"): "Amor",
    ("friend",        "spanish"): "Amigo / Amiga",
    ("yes",           "spanish"): "Sí",
    ("no",            "spanish"): "No",
    ("please",        "spanish"): "Por favor",
    ("sorry",         "spanish"): "Lo siento",
    ("beautiful",     "spanish"): "Hermoso / Hermosa",
    ("family",        "spanish"): "Familia",
    ("water",         "spanish"): "Agua",
    ("food",          "spanish"): "Comida",
    # Tagalog
    ("hello",         "tagalog"): "Kumusta",
    ("thank you",     "tagalog"): "Salamat",
    ("good morning",  "tagalog"): "Magandang umaga",
    ("good night",    "tagalog"): "Magandang gabi",
    ("goodbye",       "tagalog"): "Paalam",
    ("love",          "tagalog"): "Pagmamahal / Mahal",
    ("friend",        "tagalog"): "Kaibigan",
    ("yes",           "tagalog"): "Oo",
    ("no",            "tagalog"): "Hindi",
    ("please",        "tagalog"): "Pakiusap",
    ("sorry",         "tagalog"): "Patawad",
    ("beautiful",     "tagalog"): "Maganda",
    ("family",        "tagalog"): "Pamilya",
    ("water",         "tagalog"): "Tubig",
    ("food",          "tagalog"): "Pagkain",
    # Japanese
    ("hello",         "japanese"): "Konnichiwa",
    ("thank you",     "japanese"): "Arigatou",
    ("good morning",  "japanese"): "Ohayou gozaimasu",
    ("good night",    "japanese"): "Oyasumi",
    ("goodbye",       "japanese"): "Sayonara",
    ("love",          "japanese"): "Ai",
    ("friend",        "japanese"): "Tomodachi",
    ("yes",           "japanese"): "Hai",
    ("no",            "japanese"): "Iie",
    ("please",        "japanese"): "Onegaishimasu",
    ("sorry",         "japanese"): "Gomen nasai",
    ("beautiful",     "japanese"): "Utsukushii",
    ("family",        "japanese"): "Kazoku",
    ("water",         "japanese"): "Mizu",
    ("food",          "japanese"): "Tabemono",
    # French
    ("hello",         "french"): "Bonjour",
    ("thank you",     "french"): "Merci",
    ("good morning",  "french"): "Bonjour",
    ("good night",    "french"): "Bonne nuit",
    ("goodbye",       "french"): "Au revoir",
    ("love",          "french"): "Amour",
    ("friend",        "french"): "Ami / Amie",
    ("yes",           "french"): "Oui",
    ("no",            "french"): "Non",
    ("please",        "french"): "S'il vous plaît",
    ("sorry",         "french"): "Désolé",
    ("beautiful",     "french"): "Beau / Belle",
    ("family",        "french"): "Famille",
    ("water",         "french"): "Eau",
    ("food",          "french"): "Nourriture",
    # Korean
    ("hello",         "korean"): "Annyeonghaseyo",
    ("thank you",     "korean"): "Gamsahamnida",
    ("good morning",  "korean"): "Joeun achim",
    ("good night",    "korean"): "Jal jayo",
    ("goodbye",       "korean"): "Annyeong",
    ("love",          "korean"): "Sarang",
    ("friend",        "korean"): "Chingu",
    ("yes",           "korean"): "Ne",
    ("no",            "korean"): "Aniyo",
    ("please",        "korean"): "Juseyo",
    ("sorry",         "korean"): "Mianhamnida",
    ("beautiful",     "korean"): "Areumdawo",
    ("family",        "korean"): "Gajok",
    ("water",         "korean"): "Mul",
    ("food",          "korean"): "Eumsik",
}

# ── Reverse lookup: known translations → English meaning ─────────────────────
_REVERSE: dict[tuple[str, str], str] = {}
for (_en_phrase, _lang_key), _translation in _DICT.items():
    for _variant in _translation.split(" / "):
        _t = _variant.strip().lower()
        if _t and len(_t) > 1:
            _REVERSE[(_t, "english")] = _en_phrase.title()

# ── Patterns (order matters — most specific first) ────────────────────────────
_PATTERNS: list[re.Pattern] = [
    # "what is the spanish of hello"
    re.compile(
        rf"what\s+is\s+the\s+{_LANG_PAT}\s+(?:word\s+for\s+|of\s+)(?P<text>.+)",
        re.I,
    ),
    # "what is hello in spanish"
    re.compile(
        rf"what\s+is\s+(?P<text>.+?)\s+in\s+{_LANG_PAT}",
        re.I,
    ),
    # "translate hello to spanish"
    re.compile(
        rf"translate\s+(?P<text>.+?)\s+(?:in\s+)?to\s+{_LANG_PAT}",
        re.I,
    ),
    # "how do you say hello in spanish"
    re.compile(
        rf"how\s+(?:do\s+you\s+say|to\s+say)\s+(?P<text>.+?)\s+in\s+{_LANG_PAT}",
        re.I,
    ),
    # fallback: "[text] in [language]"
    re.compile(
        rf"(?P<text>.+?)\s+in\s+{_LANG_PAT}$",
        re.I,
    ),
]


def get_translation(raw_text: str) -> str:
    """
    Parse a translation request and return a formatted reply string.
    Returns an empty string if no translation pattern was matched.
    """
    raw = raw_text.strip()

    for pattern in _PATTERNS:
        m = pattern.search(raw)
        if not m:
            continue

        text = m.group("text").strip().lower().rstrip("?.,! ")
        lang = m.group("lang").strip().lower()
        lang_label = _LANGS.get(lang, lang.title())

        # ── Reverse lookup (non-English word → English meaning) ───────────────
        if lang == "english":
            result = _REVERSE.get((text, "english"))
            if result:
                return f'"{text.title()}" in English means "{result}".'

        # ── Direct lookup ─────────────────────────────────────────────────────
        result = _DICT.get((text, lang))
        if result:
            print(f"[AI DEBUG] translation found text={text!r} lang={lang!r} result={result!r}")
            return f'"{text.title()}" in {lang_label} is "{result}".'

        # ── Partial / fuzzy match ─────────────────────────────────────────────
        words = text.split()
        for (phrase_key, lang_key), translation in _DICT.items():
            if lang_key != lang:
                continue
            phrase_words = phrase_key.split()
            if any(w in phrase_words for w in words if len(w) > 3):
                short = (
                    f'"{phrase_key.title()}" in {lang_label} is "{translation}". '
                    f'(Closest match for "{text}")'
                )
                return short[:249]

        # ── Not found in dictionary ───────────────────────────────────────────
        print(f"[AI DEBUG] translation not found text={text!r} lang={lang!r}")
        supported = "hello, thank you, good morning, good night, goodbye, love, friend, yes, no, please"
        return (
            f'I don\'t have "{text}" in {lang_label} yet. '
            f"Try common words like: {supported}."
        )[:249]

    return ""
