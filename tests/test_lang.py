"""Language detection and reply-language policy (spec section 14).

The three examples in the spec are tested literally, because they are the
behaviour the user actually asked for.
"""

from __future__ import annotations

import pytest

from jarvis.interaction.intents import parse_intent
from jarvis.interaction.lang import detect, is_multilingual, respond_in, supported_languages


# ----------------------------------------------------- the spec's own examples
def test_spec_example_english_request():
    assert detect("Open my project.").language == "en"
    assert respond_in("Open my project.") == "en"


def test_spec_example_hinglish_request():
    result = detect("Ab mera project kholo.")
    assert result.language == "hinglish"
    assert respond_in("Ab mera project kholo.") == "hi"


def test_spec_example_switching_back_to_english():
    assert detect("Now explain what you're doing.").language == "en"


def test_the_response_language_follows_the_latest_message():
    """Not a sticky setting - each message decides."""
    assert respond_in("Open my project.") == "en"
    assert respond_in("Ab mera project kholo.") == "hi"
    assert respond_in("Now explain what you're doing.") == "en"


def test_devanagari_is_detected_as_hindi():
    result = detect("\u092E\u0947\u0930\u093E \u092A\u094D\u0930\u094B\u091C\u0947\u0915\u094D\u091F \u0916\u094B\u0932\u094B")
    assert result.language == "hi"
    assert result.script == "Devanagari"
    assert result.confidence > 0.7


# ------------------------------------------------- regression: English words
@pytest.mark.parametrize(
    "text",
    [
        "Close the browser",
        "The quick brown fox jumps over the lazy dog",
        "Please do not open the file",
        "Can you help me with this?",
        "What is the capital of France?",
    ],
)
def test_ordinary_english_is_not_mistaken_for_hinglish(text):
    """An earlier version listed 'the', 'do', 'me' and 'help' as Hinglish words,
    which made plain English sentences detect as Hindi."""
    assert detect(text).language == "en"


# ------------------------------------------------------------- other languages
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Bonjour, comment allez-vous aujourd'hui, merci beaucoup", "fr"),
        ("Hola, gracias por favor necesito ayuda ahora mismo", "es"),
        ("Hallo danke bitte ich brauche Hilfe jetzt", "de"),
        ("Ola obrigado voce esta bem agora preciso", "pt"),
    ],
)
def test_latin_script_languages(text, expected):
    assert detect(text).language == expected


@pytest.mark.parametrize(
    "text,expected,script",
    [
        ("\u041F\u0440\u0438\u0432\u0435\u0442 \u043a\u0430\u043a \u0434\u0435\u043b\u0430", "ru", "Cyrillic"),
        ("\u3053\u3093\u306b\u3061\u306f\u3053\u308c\u306f\u30c6\u30b9\u30c8", "ja", "Hiragana"),
        ("\uc548\ub155\ud558\uc138\uc694 \ud14c\uc2a4\ud2b8", "ko", "Hangul"),
        ("\u4f60\u597d\u8fd9\u662f\u4e00\u4e2a\u6d4b\u8bd5", "zh", "CJK"),
        ("\u0645\u0631\u062d\u0628\u0627 \u0643\u064a\u0641 \u062d\u0627\u0644\u0643", "ar", "Arabic"),
    ],
)
def test_script_based_detection(text, expected, script):
    result = detect(text)
    assert result.language == expected
    assert result.script == script


# --------------------------------------------------------------------- edges
def test_empty_and_numeric_input_is_unknown_not_guessed():
    assert detect("").language == "unknown"
    assert detect("   ").language == "unknown"
    assert detect("12345 67890").language == "unknown"
    assert detect("xyzzy").confidence == 0.0


def test_unknown_input_falls_back_to_the_default_language():
    assert respond_in("xyzzy") == "en"


def test_a_fixed_policy_overrides_detection():
    assert respond_in("Ab mera project kholo.", policy="en") == "en"
    assert respond_in("Open my project.", policy="hi") == "hi"


def test_low_confidence_detection_falls_back():
    assert respond_in("banana") == "en"


def test_is_multilingual_detects_mixed_scripts():
    assert is_multilingual("Mera \u092A\u094D\u0930\u094B\u091C\u0947\u0915\u094D\u091F kholo")
    assert not is_multilingual("plain english only")


def test_supported_languages_includes_the_required_set():
    languages = set(supported_languages())
    assert {"en", "hi", "hinglish"} <= languages


# ------------------------------------------------- intent parsing per language
def test_hinglish_open_command_routes_to_computer_use():
    request = parse_intent("Ab mera project kholo")
    assert request.intent == "computer"
    assert request.params["intent"] == "open"
    assert request.language == "hi"


def test_english_open_command_routes_to_computer_use():
    request = parse_intent("Open my project")
    assert request.intent == "computer"
    assert request.params["intent"] == "open"


def test_hinglish_reminder_routes_to_productivity():
    request = parse_intent("yaad dilana ki kal meeting hai")
    assert request.intent == "productivity"
    assert request.params["intent"] == "add"


def test_hinglish_money_request_routes_to_revenue():
    assert parse_intent("mujhe paisa kamana hai").intent == "revenue"


def test_a_slash_command_beats_keyword_guessing():
    request = parse_intent("/revenue something that looks like code")
    assert request.intent == "revenue"


def test_document_intent_picks_the_format_from_the_words():
    assert parse_intent("make me an excel sheet").params["format"] == "xlsx"
    assert parse_intent("export this as a pdf").params["format"] == "pdf"
    assert parse_intent("write a markdown readme").params["format"] == "md"


def test_build_intent_picks_the_site_category():
    assert parse_intent("build me an e-commerce website").params["category"] == "ecommerce"
    assert parse_intent("build a portfolio site").params["category"] == "portfolio"


def test_detected_language_is_attached_to_every_request():
    request = parse_intent("Ab mera project kholo")
    assert request.params["detected_language"]["language"] == "hinglish"


def test_unmatched_input_defaults_to_research():
    assert parse_intent("asdf qwerty zxcv").intent == "research"
