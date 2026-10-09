"""Multilingual input handling (spec section 14).

Detects what the user is speaking in and decides what to answer in. The rules
the spec cares about are implemented literally:

* "Open my project." -> English, answer in English.
* "Ab mera project kholo." -> Hinglish, answer in Hindi/Hinglish.
* "Now explain what you're doing." -> back to English.

So the response language follows the *latest* user message, not a sticky setting.
Detection is script-first (Devanagari, Cyrillic, CJK...) and lexicon-second for
romanised text, which keeps it fast, offline and explainable.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

#: Romanised Hindi/Urdu function words. These are the words that make a Latin
#: sentence Hinglish rather than English.
#:
#: Deliberately excludes words that are ALSO common English ("the", "do", "me",
#: "help"): including them made "Close the browser" detect as Hindi. A missed
#: Hinglish word costs a wrong reply language; a false positive costs the same,
#: but silently and on ordinary English input.
HINGLISH = {
    "kya", "kyu", "kyun", "kyon", "hai", "hain", "tha", "thi", "ho", "hota", "hoti",
    "karo", "karna", "karta", "karti", "kiya", "kiye", "diya", "dena", "de", "lena",
    "liya", "mujhe", "mujhko", "mera", "meri", "tere", "tumhara", "tum", "aap", "aapka",
    "hum", "hamara", "tumhe", "isko", "usko", "iska", "uska", "abhi", "ab", "phir", "fir",
    "kab", "kahan", "kaisa", "kaisi", "kaise", "kitna", "kitni", "bahut", "thoda", "thodi",
    "acha", "achha", "acchi", "thik", "nahi", "nahin", "haan", "ji", "bhai", "yaar",
    "bolo", "batao", "bata", "samjha", "samjho", "dikhao", "dekho", "suno", "chalo", "chalu",
    "band", "kholo", "khol", "bandh", "padhai", "padhna", "kaam", "paisa", "paise", "ghar",
    "paani", "khana", "khao", "sona", "utho", "baithe", "chalao", "ruk", "ruko", "jaldi",
    "dheere", "zyada", "kam", "sab", "kuch", " kuch", "wala", "wali", "vale", "liye", "ke",
    "ki", "ka", "ko", "se", "mein", "par", "tak", "aur", "lekin", "magar", "isliye",
    "matlab", "samajh", "pata", "chahiye", "zarurat", "zaroori", "theek", "sahi", "galat",
    "naya", "nayi", "purana", "bada", "chota", "bura", "madad", "kripya",
}

#: Common English function words, used as the counter-signal.
ENGLISH = {
    "the", "and", "for", "that", "this", "with", "have", "has", "was", "were", "will",
    "my", "your", "me", "him", "her", "us", "them", "it", "its", "is", "are", "be",
    "been", "being", "do", "does", "did", "open", "close", "run", "make", "show",
    "tell", "find", "get", "let", "thanks", "today", "tomorrow", "yesterday",
    "would", "could", "should", "from", "they", "their", "there", "here", "what", "when",
    "where", "which", "who", "why", "how", "about", "into", "over", "after", "before",
    "because", "while", "please", "can", "you", "not", "but", "just",
    "now", "then", "also", "some", "any", "all", "more", "most", "very", "really",
}

#: Other supported languages by distinctive lexicon (used for Latin-script text).
LEXICONS = {
    "es": {"hola", "gracias", "por", "favor", "quiero", "necesito", "estoy", "porque",
           "cuando", "donde", "como", "esto", "para", "muy", "tambien", "ahora", "puedes"},
    "fr": {"bonjour", "merci", "s'il", "vous", "je", "suis", "pourquoi", "quand", "ou",
           "comment", "cela", "tres", "aussi", "maintenant", "peux", "besoin"},
    "de": {"hallo", "danke", "bitte", "ich", "bin", "warum", "wann", "wie", "das", "sehr",
           "auch", "jetzt", "kann", "brauche", "nicht", "und", "mit"},
    "pt": {"ola", "obrigado", "obrigada", "voce", "estou", "porque", "quando", "onde",
           "como", "muito", "tambem", "agora", "pode", "preciso", "nao"},
    "it": {"ciao", "grazie", "prego", "sono", "perche", "quando", "dove", "come", "molto",
           "anche", "adesso", "puoi", "bisogno", "non"},
}

#: Script ranges -> language, for the unambiguous cases.
SCRIPTS: list[tuple[str, str, tuple[int, int]]] = [
    ("hi", "Devanagari", (0x0900, 0x097F)),
    ("ar", "Arabic", (0x0600, 0x06FF)),
    ("he", "Hebrew", (0x0590, 0x05FF)),
    ("ru", "Cyrillic", (0x0400, 0x04FF)),
    ("el", "Greek", (0x0370, 0x03FF)),
    ("th", "Thai", (0x0E00, 0x0E7F)),
    ("ko", "Hangul", (0xAC00, 0xD7AF)),
    ("ja", "Hiragana", (0x3040, 0x309F)),
    ("ja", "Katakana", (0x30A0, 0x30FF)),
    ("zh", "CJK", (0x4E00, 0x9FFF)),
]

LANG_NAMES = {
    "en": "English", "hi": "Hindi", "hinglish": "Hinglish (romanised Hindi)",
    "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "it": "Italian",
    "ar": "Arabic", "he": "Hebrew", "ru": "Russian", "el": "Greek", "th": "Thai",
    "ko": "Korean", "ja": "Japanese", "zh": "Chinese", "unknown": "unknown",
}


@dataclass
class LanguageResult:
    language: str
    confidence: float
    script: str = "Latin"
    evidence: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return LANG_NAMES.get(self.language, self.language)

    @property
    def family(self) -> str:
        """The coarse bucket used to decide the reply language."""
        return "hi" if self.language in {"hi", "hinglish"} else self.language

    def as_dict(self) -> dict[str, object]:
        return {
            "language": self.language,
            "name": self.name,
            "confidence": round(self.confidence, 3),
            "script": self.script,
            "evidence": list(self.evidence),
        }

    def __str__(self) -> str:
        return f"{self.language} ({self.confidence:.0%}, {self.script})"


def detect(text: str) -> LanguageResult:
    """Detect the language of ``text``.

    Returns ``unknown`` with zero confidence rather than guessing when there is
    not enough signal -- the anti-hallucination rule applies to language too.
    """
    if not text or not text.strip():
        return LanguageResult("unknown", 0.0, evidence=["empty input"])

    script, script_lang, script_count = _dominant_script(text)
    total_letters = sum(1 for ch in text if ch.isalpha())
    if total_letters == 0:
        return LanguageResult("unknown", 0.0, script, ["no letters to analyse"])

    if script_lang:
        share = script_count / total_letters
        # Hindi in Devanagari is unambiguous; CJK needs the extra kana/hangul check.
        return LanguageResult(
            script_lang,
            min(0.99, 0.55 + 0.44 * share),
            script,
            [f"{script_count}/{total_letters} characters in {script}"],
        )

    words = _words(text)
    if not words:
        return LanguageResult("unknown", 0.0, script, ["no word tokens"])

    hinglish_hits = [w for w in words if w in HINGLISH]
    english_hits = [w for w in words if w in ENGLISH]
    other_scores = {
        lang: sum(1 for w in words if w in lexicon) for lang, lexicon in LEXICONS.items()
    }
    best_other = max(other_scores.items(), key=lambda kv: kv[1])

    evidence = [
        f"{len(hinglish_hits)} Hinglish word(s): {', '.join(hinglish_hits[:6]) or '-'}",
        f"{len(english_hits)} English function word(s)",
    ]

    # A single strong romanised-Hindi marker in an otherwise Latin sentence is
    # enough to switch: "ab mera project kholo" has almost no English words.
    if hinglish_hits and len(hinglish_hits) >= max(1, int(0.12 * len(words))):
        ratio = len(hinglish_hits) / max(1, len(hinglish_hits) + len(english_hits))
        confidence = min(0.97, 0.5 + 0.45 * ratio + 0.05 * min(len(hinglish_hits), 5))
        return LanguageResult("hinglish", confidence, script, evidence)

    if best_other[1] >= 2 and best_other[1] > len(english_hits):
        confidence = min(0.9, 0.45 + 0.1 * best_other[1])
        return LanguageResult(
            best_other[0], confidence, script,
            evidence + [f"{best_other[1]} {LANG_NAMES.get(best_other[0], '')} word(s)"],
        )

    if english_hits:
        confidence = min(0.95, 0.5 + 0.08 * len(english_hits))
        return LanguageResult("en", confidence, script, evidence)

    return LanguageResult(
        "unknown", 0.0, script, evidence + ["no recognised function words"]
    )


def respond_in(text: str, policy: str = "match", *, default: str = "en") -> str:
    """The language Jarvis should reply in for this message.

    ``match`` follows the user's latest language (spec section 14); the fixed
    settings force one language regardless.
    """
    if policy in {"en", "hi", "hinglish"}:
        return policy
    result = detect(text)
    if result.language == "unknown" or result.confidence < 0.4:
        return default
    return result.family


def greeting(language: str) -> str:
    return {
        "hi": "Namaste! Main Jarvis hoon. Bataiye kya karna hai?",
        "hinglish": "Namaste! Main Jarvis hoon. Bataiye, aaj kya karna hai?",
        "es": "Hola, soy Jarvis. En que puedo ayudar?",
        "fr": "Bonjour, je suis Jarvis. Comment puis-je aider ?",
        "de": "Hallo, ich bin Jarvis. Womit kann ich helfen?",
        "pt": "Ola, sou o Jarvis. Em que posso ajudar?",
        "it": "Ciao, sono Jarvis. Come posso aiutare?",
        "ru": "Privet, ya Jarvis. Chem pomoch?",
    }.get(language, "Hello, I'm Jarvis. What can I help with?")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z\u00C0-\u024F']+", text.lower())


def _dominant_script(text: str) -> tuple[str, str | None, int]:
    """The dominant non-Latin script, and the specific block it came from.

    Japanese spans two blocks (hiragana and katakana); the reported block name is
    the one that actually dominates, not whichever was seen last.
    """
    per_block: dict[tuple[str, str], int] = {}
    for ch in text:
        if not ch.isalpha():
            continue
        code = ord(ch)
        for lang, name, (low, high) in SCRIPTS:
            if low <= code <= high:
                per_block[(lang, name)] = per_block.get((lang, name), 0) + 1
                break
    if not per_block:
        return "Latin", None, 0
    per_language: dict[str, int] = {}
    for (lang, _name), count in per_block.items():
        per_language[lang] = per_language.get(lang, 0) + count
    lang = max(per_language.items(), key=lambda kv: kv[1])[0]
    block_name = max(
        (name for (block_lang, name) in per_block if block_lang == lang),
        key=lambda name: per_block[(lang, name)],
    )
    return block_name, lang, per_language[lang]


def is_multilingual(text: str) -> bool:
    """True when a single message mixes scripts (common in real Hinglish input)."""
    _, _, non_latin = _dominant_script(text)
    latin = sum(1 for ch in text if ch.isalpha() and ord(ch) < 0x0250)
    return bool(non_latin and latin and non_latin > 0 and latin > 0)


def script_name(text: str) -> str:
    try:
        for ch in text:
            if ch.isalpha():
                return unicodedata.name(ch).split()[0].title()
    except ValueError:
        pass
    return "Latin"


def supported_languages() -> Sequence[str]:
    return tuple(sorted(LANG_NAMES))
