"""
modules/ai_global_knowledge.py — Global real-world knowledge for ChillTopiaMC AI (3.3A).

Covers:
  - Geography (capitals, countries)
  - Science basics
  - Famous people
  - Basic economics
  - Fun facts & study tips
  - Safe math evaluation
  - Limited translations
  - Unsafe / live-data / sensitive detection

Does NOT pretend to have live internet. Cannot answer: weather, prices,
news, sports scores, exchange rates, current leaders, or live schedules.
"""
from __future__ import annotations

import ast
import operator
import random
import re

from modules.ai_intent_router import (
    INTENT_RW_CURRENT_INFO, INTENT_RW_SENSITIVE,
    INTENT_RW_TRANSLATION, INTENT_RW_MATH, INTENT_RW_GENERAL,
    INTENT_RW_GLOBAL, INTENT_RW_UNKNOWN,
)

# ── Unsafe patterns (global) ────────────────────────────────────────────────
_UNSAFE = re.compile(
    r"\b(hack|hacking|steal\s+(account|password)|bypass\s+(ban|mod|security|auth)"
    r"|exploit\s+(bot|game|cheat)|how\s+to\s+make\s+(bomb|weapon|explosive)"
    r"|self.?harm|hurt\s+myself|hurt\s+someone|doxx|doxing"
    r"|fraud|scam\s+(someone|people)|phishing"
    r"|illegal\s+(drug|weapon)|drug\s+deal)\b",
    re.I,
)
_UNSAFE_REDIRECT: dict[str, str] = {
    "hack":          "🚫 I can't help with hacking or account theft.",
    "weapon":        "🚫 I can't help with weapons or harmful content.",
    "self.harm":     "💙 If you're struggling, please reach out to a trusted person or crisis line.",
    "fraud":         "🚫 I can't assist with fraud or scams.",
    "doxx":          "🚫 I can't help with doxxing.",
    "illegal":       "🚫 I can't assist with illegal activity.",
}


def _get_unsafe_reply(text: str) -> str | None:
    m = _UNSAFE.search(text)
    if not m:
        return None
    word = m.group(0).lower()
    for key, reply in _UNSAFE_REDIRECT.items():
        if key in word:
            return reply
    return "🚫 I can't help with that."


# ── Live data patterns ───────────────────────────────────────────────────────
_LIVE_DATA = re.compile(
    r"\b(weather|forecast|temperature\s+in"
    r"|latest\s+news|breaking\s+news|news\s+in"
    r"|who\s+won\s+the|current\s+score|live\s+score"
    r"|usd\s+(to|vs)|php\s+exchange|exchange\s+rate|forex"
    r"|bitcoin\s+price|crypto\s+price|stock\s+price"
    r"|promo\s+code|latest\s+update|new\s+update"
    r"|current\s+president|prime\s+minister\s+of"
    r"|flight\s+price|hotel\s+price|bus\s+schedule"
    r"|train\s+schedule|cinema\s+schedule"
    r"|lotto\s+(result|winning)|pcso|powerball|mega\s+millions)\b",
    re.I,
)

_LIVE_REPLY = (
    "🌐 That needs live internet access which I don't have right now.\n"
    "I can answer general info, but live updates (prices, news, scores, "
    "weather, rates) need an online source."
)

# ── Sensitive patterns ───────────────────────────────────────────────────────
_SENSITIVE = re.compile(
    r"\b(chest\s+pain|heart\s+attack|can.?t\s+breathe|difficulty\s+breathing"
    r"|suicide|suicidal|overdose|poison(ing)?|bleeding\s+out"
    r"|medical\s+advice|diagnos|what\s+medicine\s+(to|should)"
    r"|legal\s+advice|is\s+it\s+legal\s+to|can\s+i\s+be\s+(arrested|charged)"
    r"|immigration\s+(advice|status)|visa\s+(denial|rejection)"
    r"|tax\s+(advice|evasion|cheat)|mental\s+health\s+crisis"
    r"|am\s+i\s+depressed|clinical\s+depression\s+symptoms"
    r"|emergency|call\s+(911|112|995|999)|ambulance)\b",
    re.I,
)

_SENSITIVE_REPLIES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(chest\s+pain|heart\s+attack|can.?t\s+breathe|difficulty\s+breathing|bleeding\s+out)\b", re.I),
     "🚨 Seek emergency medical help immediately — chest pain and breathing difficulty can be life-threatening. Call emergency services now!"),
    (re.compile(r"\b(suicide|suicidal|overdose)\b", re.I),
     "💙 If you or someone is in crisis, please reach out to a trusted person or a mental health helpline. You're not alone."),
    (re.compile(r"\b(medical\s+advice|diagnos|what\s+medicine)\b", re.I),
     "⚕️ I can share general health info, but I'm not a doctor. Please consult a licensed medical professional for advice."),
    (re.compile(r"\b(legal\s+advice|is\s+it\s+legal|arrested|charged)\b", re.I),
     "⚖️ I can share general info, but I'm not a lawyer. For specific legal situations, please consult a qualified attorney."),
    (re.compile(r"\b(mental\s+health|depressed|depression|anxiety|crisis)\b", re.I),
     "💙 Mental health matters. I can share general info, but please talk to a counselor or mental health professional for proper support."),
    (re.compile(r"\b(emergency|call\s+(911|112|995|999)|ambulance)\b", re.I),
     "🚨 If this is an emergency, please call your local emergency number (911 / 112 / 995) immediately!"),
    (re.compile(r"\b(immigration|visa|tax)\b", re.I),
     "ℹ️ Immigration, visa, and tax questions depend on your specific situation. Please consult a professional or official government source."),
]


def _get_sensitive_reply(text: str) -> str | None:
    if not _SENSITIVE.search(text):
        return None
    for pattern, reply in _SENSITIVE_REPLIES:
        if pattern.search(text):
            return reply[:249]
    return "ℹ️ That's a sensitive topic. Please consult a qualified professional for advice."


# ── Geography — capitals ──────────────────────────────────────────────────
_CAPITALS: dict[str, str] = {
    "philippines": "Manila",
    "japan": "Tokyo",
    "south korea": "Seoul",
    "north korea": "Pyongyang",
    "china": "Beijing",
    "india": "New Delhi",
    "indonesia": "Jakarta",
    "thailand": "Bangkok",
    "vietnam": "Hanoi",
    "malaysia": "Kuala Lumpur",
    "singapore": "Singapore City",
    "australia": "Canberra",
    "new zealand": "Wellington",
    "united states": "Washington D.C.",
    "usa": "Washington D.C.",
    "canada": "Ottawa",
    "mexico": "Mexico City",
    "brazil": "Brasília",
    "argentina": "Buenos Aires",
    "colombia": "Bogotá",
    "peru": "Lima",
    "chile": "Santiago",
    "uk": "London",
    "united kingdom": "London",
    "england": "London",
    "france": "Paris",
    "germany": "Berlin",
    "italy": "Rome",
    "spain": "Madrid",
    "portugal": "Lisbon",
    "netherlands": "Amsterdam",
    "belgium": "Brussels",
    "switzerland": "Bern",
    "austria": "Vienna",
    "sweden": "Stockholm",
    "norway": "Oslo",
    "denmark": "Copenhagen",
    "finland": "Helsinki",
    "poland": "Warsaw",
    "russia": "Moscow",
    "ukraine": "Kyiv",
    "turkey": "Ankara",
    "greece": "Athens",
    "egypt": "Cairo",
    "nigeria": "Abuja",
    "south africa": "Pretoria (administrative)",
    "kenya": "Nairobi",
    "ethiopia": "Addis Ababa",
    "ghana": "Accra",
    "morocco": "Rabat",
    "iran": "Tehran",
    "iraq": "Baghdad",
    "saudi arabia": "Riyadh",
    "uae": "Abu Dhabi",
    "israel": "Jerusalem",
    "pakistan": "Islamabad",
    "bangladesh": "Dhaka",
    "afghanistan": "Kabul",
}


def _get_capital_reply(text: str) -> str | None:
    low = text.lower()
    m = re.search(r"capital\s+of\s+(.+?)(?:\?|$)", low)
    if m:
        query = m.group(1).strip().rstrip("?. ")
        for country, capital in _CAPITALS.items():
            if country in query or query in country:
                return f"🗺️ The capital of {country.title()} is {capital}."
    # Also catch "what is [country]'s capital"
    m2 = re.search(r"(.+?)'?s?\s+capital\b", low)
    if m2:
        query = m2.group(1).strip()
        for country, capital in _CAPITALS.items():
            if country in query or query in country:
                return f"🗺️ {country.title()}'s capital is {capital}."
    return None


# ── Science, History, Famous People, Economics KB ─────────────────────────
_KB: list[tuple[re.Pattern, str]] = [

    # Science
    (re.compile(r"\bgravity\b", re.I),
     "🌍 Gravity is the force that attracts objects with mass toward each other. On Earth it accelerates objects at ~9.8 m/s²."),
    (re.compile(r"\bphotosynthesis\b", re.I),
     "🌱 Photosynthesis: plants use sunlight + water + CO₂ to make glucose (food) and release oxygen. It's how plants get energy."),
    (re.compile(r"\bevolution\b", re.I),
     "🧬 Evolution is how species change over generations through natural selection — traits that help survival get passed down more often."),
    (re.compile(r"\b(dna|deoxy)", re.I),
     "🧬 DNA (deoxyribonucleic acid) is the molecule that carries genetic instructions in living organisms — like a biological blueprint."),
    (re.compile(r"\bspeed\s+of\s+light\b", re.I),
     "✨ The speed of light is ~299,792 km/s (about 186,000 miles/s) in a vacuum — the fastest speed possible."),
    (re.compile(r"\bblack\s+hole\b", re.I),
     "🌑 A black hole is a region of space where gravity is so strong that not even light can escape. They form when massive stars collapse."),
    (re.compile(r"\batoms?\b", re.I),
     "⚛️ Atoms are the basic building blocks of matter. They consist of protons, neutrons (nucleus), and electrons (outer shell)."),
    (re.compile(r"\bclimate\s+change\b", re.I),
     "🌡️ Climate change refers to long-term shifts in global temperatures, mainly driven by human greenhouse gas emissions (CO₂, methane)."),
    (re.compile(r"\bvaccine\b", re.I),
     "💉 Vaccines train the immune system to recognize and fight specific viruses or bacteria, helping prevent serious illness."),
    (re.compile(r"\binternet\b.*\bwork\b|\bhow\s+does.*internet\b", re.I),
     "🌐 The internet is a global network of computers that communicate using protocols (TCP/IP). Data travels through cables and wireless signals."),
    (re.compile(r"\bartificial\s+intelligence\b|what\s+is\s+ai\b", re.I),
     "🤖 AI (Artificial Intelligence) is the simulation of human intelligence by computer systems — learning, reasoning, and problem-solving."),
    (re.compile(r"\bwater\b.*\b(boil|freeze)\b|\bboil.*\bwater\b", re.I),
     "💧 Water boils at 100°C (212°F) and freezes at 0°C (32°F) at standard sea-level pressure."),

    # Geography
    (re.compile(r"\blargest\s+country\b|\bbiggest\s+country\b", re.I),
     "🗺️ Russia is the largest country in the world by area (~17.1 million km²). Canada is 2nd, USA is 3rd."),
    (re.compile(r"\bsmallest\s+country\b", re.I),
     "🗺️ Vatican City is the smallest country in the world (~0.44 km²), located within Rome, Italy."),
    (re.compile(r"\bmost\s+populous\b|biggest\s+population\b", re.I),
     "🌍 India and China are the most populous countries, each with over 1.4 billion people."),
    (re.compile(r"\blongest\s+river\b", re.I),
     "🌊 The Nile (Africa) and Amazon (South America) are the two longest rivers. The Nile is traditionally listed as #1 at ~6,650 km."),
    (re.compile(r"\bhighest\s+mountain\b|tallest\s+mountain\b", re.I),
     "🏔️ Mount Everest (8,849 m / 29,032 ft) is Earth's highest mountain above sea level, in the Himalayas (Nepal/Tibet)."),
    (re.compile(r"\bhow\s+many\s+planets\b|\bplanets?\s+in.*solar\s+system\b", re.I),
     "🪐 There are 8 planets: Mercury, Venus, Earth, Mars, Jupiter, Saturn, Uranus, Neptune. Pluto was reclassified as a dwarf planet."),
    (re.compile(r"\bearth.*circumference\b|\bcircumference.*earth\b", re.I),
     "🌍 Earth's circumference is ~40,075 km (24,901 miles) at the equator."),
    (re.compile(r"\b(ocean|sea)\b.*\blargest\b|\blargest\s+(ocean|sea)\b", re.I),
     "🌊 The Pacific Ocean is the largest, covering about 165 million km² — larger than all landmasses combined."),

    # Famous people
    (re.compile(r"\balbert\s+einstein\b|\beinstein\b", re.I),
     "👨‍🔬 Albert Einstein (1879–1955) was a German-born physicist who developed the theory of relativity and the famous equation E=mc²."),
    (re.compile(r"\bisaac\s+newton\b|\bnewton\b", re.I),
     "👨‍🔬 Isaac Newton (1643–1727) was an English mathematician who formulated the laws of motion, gravity, and calculus."),
    (re.compile(r"\bnikola\s+tesla\b|\btesla\b.*\bscientist\b|\bscientist.*tesla\b", re.I),
     "⚡ Nikola Tesla (1856–1943) was a Serbian-American inventor known for developing AC electricity and the Tesla coil."),
    (re.compile(r"\bcharles\s+darwin\b|\bdarwin\b", re.I),
     "🐢 Charles Darwin (1809–1882) was an English naturalist who developed the theory of evolution by natural selection."),
    (re.compile(r"\bmarie\s+curie\b|\bcurie\b", re.I),
     "🧪 Marie Curie (1867–1934) was a Polish-French physicist who discovered polonium and radium — the first woman to win a Nobel Prize."),
    (re.compile(r"\bstephen\s+hawking\b|\bhawking\b", re.I),
     "🌌 Stephen Hawking (1942–2018) was a British physicist known for work on black holes, Hawking radiation, and A Brief History of Time."),
    (re.compile(r"\bwilliam\s+shakespeare\b|\bshakespeare\b", re.I),
     "📜 William Shakespeare (1564–1616) was an English playwright and poet, widely regarded as the greatest writer in the English language."),
    (re.compile(r"\babraham\s+lincoln\b|\blincoln\b", re.I),
     "🎩 Abraham Lincoln (1809–1865) was the 16th US President who led the country through the Civil War and abolished slavery."),
    (re.compile(r"\bmahatma\s+gandhi\b|\bgandhi\b", re.I),
     "☮️ Mahatma Gandhi (1869–1948) was an Indian leader who championed non-violent civil disobedience to free India from British rule."),

    # Economics basics
    (re.compile(r"\binflation\b", re.I),
     "📈 Inflation is the rate at which the general price level of goods and services rises over time, reducing purchasing power."),
    (re.compile(r"\bgdp\b|gross\s+domestic\s+product\b", re.I),
     "💹 GDP (Gross Domestic Product) is the total monetary value of all goods and services produced in a country within a period."),
    (re.compile(r"\binterest\s+rate\b", re.I),
     "💰 Interest rate is the cost of borrowing money (or the return on savings), expressed as a percentage of the principal."),
    (re.compile(r"\brecession\b", re.I),
     "📉 A recession is a period of economic decline — usually defined as two consecutive quarters of negative GDP growth."),
    (re.compile(r"\bstocks?\b.*\bwork\b|\bhow\s+do\s+stocks\b", re.I),
     "📊 Stocks are shares of ownership in a company. Their price rises and falls based on the company's performance and market demand."),
    (re.compile(r"\bcryptocurrency\b|\bcrypto\b.*\bwhat\b|\bwhat\s+is\s+crypto\b", re.I),
     "🪙 Cryptocurrency is digital/virtual money secured by cryptography. Bitcoin was the first, created in 2009. Not backed by governments."),

    # Life and advice
    (re.compile(r"\bstudy\s+tip|how\s+to\s+study\b|\bbetter\s+at\s+studying\b", re.I),
     "📚 Study tips: use the Pomodoro method (25 min focus, 5 min break), review notes same day, test yourself, and get enough sleep!"),
    (re.compile(r"\bproductiv\b|\bhow\s+to\s+be\s+productive\b", re.I),
     "⏰ Productivity tips: set clear goals, eliminate distractions, use time blocks, take breaks, and celebrate small wins!"),
    (re.compile(r"\bsleep\b.*\b(tip|better|good)\b|\bhow\s+(much|long)\s+to\s+sleep\b", re.I),
     "😴 Adults need 7–9 hours of sleep. Stick to a schedule, avoid screens before bed, and keep your room cool and dark."),

    # Languages
    (re.compile(r"\bmost\s+spoken\s+language\b|\bmost\s+common\s+language\b", re.I),
     "🗣️ English is the most widely spoken language globally (by total speakers). Mandarin Chinese has the most native speakers."),
    (re.compile(r"\bhow\s+many\s+languages\b", re.I),
     "🌍 There are approximately 7,000 languages spoken worldwide. About half the world's population speaks one of the top 23 languages."),
]


# ── Math evaluator ────────────────────────────────────────────────────────
_MATH_DETECT = re.compile(
    r"\b(\d[\d\s\+\-\*\/\^%\.]*[\d])\b"
    r"|what\s+is\s+\d"
    r"|calculate\b|compute\b|evaluate\b|solve\b.{0,15}\d",
    re.I,
)
_MATH_EXPR   = re.compile(r"([\d\s\+\-\*\/\^\(\)\.%]+)", re.I)

_OPS = {
    ast.Add:  operator.add,
    ast.Sub:  operator.sub,
    ast.Mult: operator.mul,
    ast.Div:  operator.truediv,
    ast.Pow:  operator.pow,
    ast.Mod:  operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(expr: str) -> str | None:
    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        elif isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
        elif isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"Unsupported: {type(node)}")

    # Clean: allow only digits and operators
    clean = re.sub(r"[^0-9\+\-\*\/\^\(\)\.\s%]", "", expr).strip()
    clean = clean.replace("^", "**")
    if not clean:
        return None
    try:
        tree = ast.parse(clean, mode="eval")
        result = _eval(tree.body)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        # Protect against huge numbers
        result_str = str(result)
        if len(result_str) > 30:
            return f"{result:.6g}"
        return result_str
    except Exception:
        return None


def _get_math_reply(text: str) -> str | None:
    if not _MATH_DETECT.search(text):
        return None
    m = _MATH_EXPR.search(text)
    if m:
        result = _safe_eval(m.group(1))
        if result:
            expr = m.group(1).strip().replace("**", "^")[:20]
            return f"🔢 {expr} = {result}"
    return None


# ── Translation mini-dict ──────────────────────────────────────────────────
_TRANSLATIONS: dict[tuple[str, str], str] = {
    # (phrase, language): response
    ("hello",      "japanese"):   "Hello in Japanese: こんにちは (Konnichiwa) 🇯🇵",
    ("hello",      "korean"):     "Hello in Korean: 안녕하세요 (Annyeonghaseyo) 🇰🇷",
    ("hello",      "spanish"):    "Hello in Spanish: Hola 🇪🇸",
    ("hello",      "french"):     "Hello in French: Bonjour 🇫🇷",
    ("hello",      "chinese"):    "Hello in Chinese: 你好 (Nǐ hǎo) 🇨🇳",
    ("hello",      "german"):     "Hello in German: Hallo 🇩🇪",
    ("hello",      "arabic"):     "Hello in Arabic: مرحبا (Marhaba) 🇸🇦",
    ("hello",      "tagalog"):    "Hello in Tagalog: Kamusta! 🇵🇭",
    ("thank you",  "japanese"):   "Thank you in Japanese: ありがとう (Arigatou) 🇯🇵",
    ("thank you",  "korean"):     "Thank you in Korean: 감사합니다 (Gamsahamnida) 🇰🇷",
    ("thank you",  "spanish"):    "Thank you in Spanish: Gracias 🇪🇸",
    ("thank you",  "french"):     "Thank you in French: Merci 🇫🇷",
    ("thank you",  "chinese"):    "Thank you in Chinese: 谢谢 (Xièxiè) 🇨🇳",
    ("thank you",  "arabic"):     "Thank you in Arabic: شكرا (Shukran) 🇸🇦",
    ("thank you",  "tagalog"):    "Thank you in Tagalog: Salamat! 🇵🇭",
    ("goodbye",    "japanese"):   "Goodbye in Japanese: さようなら (Sayōnara) 🇯🇵",
    ("goodbye",    "korean"):     "Goodbye in Korean: 잘 가 (Jal ga) / 잘 있어요 🇰🇷",
    ("goodbye",    "spanish"):    "Goodbye in Spanish: Adiós 🇪🇸",
    ("goodbye",    "french"):     "Goodbye in French: Au revoir 🇫🇷",
    ("goodbye",    "chinese"):    "Goodbye in Chinese: 再见 (Zàijiàn) 🇨🇳",
    ("goodbye",    "tagalog"):    "Goodbye in Tagalog: Paalam! 🇵🇭",
    ("i love you", "japanese"):   "I love you in Japanese: 愛してる (Aishiteru) 🇯🇵",
    ("i love you", "korean"):     "I love you in Korean: 사랑해요 (Saranghaeyo) 🇰🇷",
    ("i love you", "spanish"):    "I love you in Spanish: Te amo 🇪🇸",
    ("i love you", "french"):     "I love you in French: Je t'aime 🇫🇷",
    ("i love you", "chinese"):    "I love you in Chinese: 我爱你 (Wǒ ài nǐ) 🇨🇳",
    ("i love you", "tagalog"):    "I love you in Tagalog: Mahal kita 🇵🇭",
    ("yes",        "japanese"):   "Yes in Japanese: はい (Hai) 🇯🇵",
    ("yes",        "korean"):     "Yes in Korean: 네 (Ne) 🇰🇷",
    ("yes",        "spanish"):    "Yes in Spanish: Sí 🇪🇸",
    ("yes",        "french"):     "Yes in French: Oui 🇫🇷",
    ("yes",        "chinese"):    "Yes in Chinese: 是 (Shì) / 对 (Duì) 🇨🇳",
    ("no",         "japanese"):   "No in Japanese: いいえ (Iie) 🇯🇵",
    ("no",         "korean"):     "No in Korean: 아니요 (Aniyo) 🇰🇷",
    ("no",         "spanish"):    "No in Spanish: No 🇪🇸",
    ("no",         "french"):     "No in French: Non 🇫🇷",
    ("no",         "chinese"):    "No in Chinese: 不 (Bù) 🇨🇳",
    ("sorry",      "japanese"):   "Sorry in Japanese: ごめんなさい (Gomen nasai) 🇯🇵",
    ("sorry",      "korean"):     "Sorry in Korean: 죄송합니다 (Joesonghamnida) 🇰🇷",
    ("sorry",      "spanish"):    "Sorry in Spanish: Lo siento 🇪🇸",
    ("sorry",      "french"):     "Sorry in French: Désolé(e) 🇫🇷",
    ("beautiful",  "japanese"):   "Beautiful in Japanese: 美しい (Utsukushii) 🇯🇵",
    ("beautiful",  "korean"):     "Beautiful in Korean: 아름다워요 (Areumdawoyo) 🇰🇷",
    ("beautiful",  "spanish"):    "Beautiful in Spanish: Hermoso/a 🇪🇸",
    ("food",       "japanese"):   "Food in Japanese: 食べ物 (Tabemono) 🇯🇵",
    ("food",       "korean"):     "Food in Korean: 음식 (Eumsik) 🇰🇷",
    ("food",       "spanish"):    "Food in Spanish: Comida 🇪🇸",
    ("water",      "japanese"):   "Water in Japanese: 水 (Mizu) 🇯🇵",
    ("water",      "korean"):     "Water in Korean: 물 (Mul) 🇰🇷",
    ("water",      "spanish"):    "Water in Spanish: Agua 🇪🇸",
    ("cat",        "japanese"):   "Cat in Japanese: 猫 (Neko) 🇯🇵",
    ("cat",        "korean"):     "Cat in Korean: 고양이 (Goyangi) 🇰🇷",
    ("dog",        "japanese"):   "Dog in Japanese: 犬 (Inu) 🇯🇵",
    ("dog",        "korean"):     "Dog in Korean: 개 (Gae) 🇰🇷",
}

_LANGUAGE_ALIASES: dict[str, str] = {
    "japanese": "japanese", "jp": "japanese", "japan": "japanese",
    "korean":   "korean",   "kr": "korean",   "korea": "korean",
    "spanish":  "spanish",  "es": "spanish",  "spain": "spanish",
    "french":   "french",   "fr": "french",   "france": "french",
    "german":   "german",   "de": "german",   "germany": "german",
    "chinese":  "chinese",  "zh": "chinese",  "china": "chinese", "mandarin": "chinese",
    "arabic":   "arabic",   "ar": "arabic",
    "tagalog":  "tagalog",  "filipino": "tagalog", "ph": "tagalog",
}


def _get_translation_reply(text: str) -> str | None:
    low = text.lower()
    # Match "translate [phrase] to [language]"
    m = re.search(r"translate\s+(.+?)\s+to\s+(\w+)", low)
    if not m:
        # Match "how do you say [phrase] in [language]"
        m = re.search(r"how\s+(?:do\s+you\s+say|to\s+say)\s+[\"']?(.+?)[\"']?\s+in\s+(\w+)", low)
    if not m:
        # Match "[phrase] in [language]"
        m = re.search(r"[\"']?(.+?)[\"']?\s+in\s+(\w+)(?:\?|$)", low)
    if not m:
        return None

    phrase = m.group(1).strip().strip("'\"")
    lang_raw = m.group(2).strip().lower()
    lang = _LANGUAGE_ALIASES.get(lang_raw)
    if not lang:
        return f"🌐 I have basic translations for Japanese, Korean, Spanish, French, Chinese, Arabic, and Tagalog."

    result = _TRANSLATIONS.get((phrase, lang))
    if result:
        return result[:249]

    # Partial match
    for (p, l), r in _TRANSLATIONS.items():
        if l == lang and phrase in p:
            return r[:249]

    return f"📖 I don't have '{phrase}' in {lang_raw.title()} yet. Try a common word or phrase!"


# ── Fun facts ────────────────────────────────────────────────────────────────
_FUN_FACTS = [
    "🐙 Octopuses have three hearts and blue blood!",
    "🍯 Honey never spoils — archaeologists have found 3,000-year-old honey in Egyptian tombs!",
    "🌙 The Moon is moving away from Earth at ~3.8 cm per year.",
    "🐧 Penguins only live in the Southern Hemisphere (except in zoos)!",
    "💡 A bolt of lightning is about 5 times hotter than the Sun's surface.",
    "🐋 Blue whales are the largest animals to have ever lived on Earth.",
    "🦋 Butterflies taste with their feet!",
    "🌊 The ocean covers ~71% of Earth's surface, but 95% of it is still unexplored.",
    "🕷️ Most spiders have 8 eyes but many can barely see well.",
    "🌍 There are more stars in the universe than grains of sand on all of Earth's beaches.",
    "🐘 Elephants are the only animals that can't jump.",
    "🍌 Bananas are technically berries, but strawberries are not!",
    "🧠 Your brain uses about 20% of your body's total energy.",
    "🦈 Sharks are older than trees — sharks existed ~450 million years ago!",
    "🌴 Coconuts kill about 150 people each year — more than sharks do.",
    "🐢 A group of flamingos is called a flamboyance.",
    "🔬 There are more bacteria in your gut than cells in your entire body.",
    "🪐 Saturn is so light it would float on water (if you had a big enough ocean)!",
    "🦜 Parrots can live over 80 years — longer than most humans.",
    "🌺 The Philippines has the world's longest Christmas season (starting as early as September)!",
]


def _get_fun_fact() -> str:
    return random.choice(_FUN_FACTS)


# ── Main dispatcher ──────────────────────────────────────────────────────────

def handle_global_question(text: str, intent: str) -> str:
    """
    Main entry point for real-world question handling.
    Always returns a string (never None).
    """
    # 1. Unsafe check
    unsafe = _get_unsafe_reply(text)
    if unsafe:
        return unsafe

    # 2. Live data check
    if _LIVE_DATA.search(text) or intent == INTENT_RW_CURRENT_INFO:
        return _LIVE_REPLY[:249]

    # 3. Sensitive check
    sensitive = _get_sensitive_reply(text)
    if sensitive:
        return sensitive

    # 4. Translation
    if intent == INTENT_RW_TRANSLATION:
        result = _get_translation_reply(text)
        return (result or "📖 I can help with basic translations. Try: 'ai translate hello to Japanese'")[:249]

    # 5. Math
    if intent == INTENT_RW_MATH:
        math_r = _get_math_reply(text)
        return (math_r or "🔢 I can evaluate basic math. Try: 'ai what is 25 * 4'")[:249]

    # 6. Fun fact
    low = text.lower()
    if re.search(r"\bfun\s+fact\b|\btell\s+me\s+(a\s+)?fact\b|\brandom\s+fact\b", low):
        return _get_fun_fact()[:249]

    # 7. Capital lookup
    capital_r = _get_capital_reply(text)
    if capital_r:
        return capital_r[:249]

    # 8. Math (secondary check without intent — catches "what is 5 + 5")
    math_r = _get_math_reply(text)
    if math_r:
        return math_r

    # 9. Static KB search
    for pattern, answer in _KB:
        if pattern.search(text):
            return answer[:249]

    # 10. Specific topic fallbacks
    if re.search(r"\bwho\s+(is|was)\b", low):
        return "🤔 I don't have info on that person in my knowledge base. Try a well-known scientist, leader, or historical figure!"

    if re.search(r"\bwhat\s+is\b|\bwhat\s+are\b|\bexplain\b", low):
        return (
            "🤔 I don't have specific info on that. I can answer general science, geography, history, and more!\n"
            "Try: 'ai what is gravity' or 'ai capital of France'."
        )[:249]

    # 11. Generic fallback
    return (
        "💬 I'm not sure about that specific topic!\n"
        "I can answer: science, geography, capitals, famous people, fun facts, study tips, translations, and basic math."
    )[:249]
