"""Intent parsing: turns words into a normalised :class:`AgentRequest`.

Deliberately rule-based and offline. An LLM can be plugged in later by replacing
:func:`parse_intent`; nothing downstream depends on how the intent was derived,
which is the point of having the request be a plain data structure.
"""

from __future__ import annotations

import re
from typing import Any

from jarvis.agents.base import AgentRequest
from jarvis.interaction.lang import detect, respond_in

#: Keyword -> intent. Order matters: the first match wins, so specific phrases
#: come before generic ones.
INTENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("status", ("/status", "your status", "system status", "how are you doing")),
    ("security", ("/security", "scan for secret", "leaked key", "any credentials")),
    ("revenue", ("/revenue", "make money", "earn money", "business idea", "side income",
                 "revenue", "monetise", "monetize", "start a business", "freelance idea",
                 "passive income", "sell online", "profitable",
                 "paisa kamana", "paisa kamane", "kamai", "vyapar", "rojgar")),
    ("compliance", ("/compliance", "is it legal", "is this legal", "legal requirement",
                    "gdpr", "terms of service", "platform policy", "is automation allowed",
                    "tax", "registration", "privacy law", "allowed by")),
    ("build", ("/build", "build me a", "build a website", "create a website", "make a website",
               "e-commerce site", "ecommerce site", "landing page", "portfolio site",
               "saas", "web app", "web application")),
    # NOTE: bare nouns like "invoice" must NOT be here. "Remind me to file the
    # invoice" is a reminder, not a spreadsheet - a greedy noun matched it to
    # this rule first and silently produced the wrong document. Document
    # keywords are format names, or a noun plus a document verb. A format name
    # like "pdf" is safe; a bare noun like "invoice" is not.
    ("document", ("/doc", "/document", "excel", "spreadsheet", "xlsx", "csv file",
                  "powerpoint", "presentation", "word document", "pdf file", "pdf report",
                  "pdf", "make a pdf", "create a pdf", "expense sheet", "report file",
                  "invoice template", "invoice document", "create an invoice",
                  "generate an invoice", "generate invoice", "markdown", "readme")),
    ("coding", ("/code", "debug", "fix this code", "syntax error", "review this code",
                "refactor", "run the tests", "unit test", "traceback", "stack trace",
                "write a function", "python script")),
    ("vision", ("/vision", "/camera", "what am i wearing", "what do you see",
                "read this text", "what's in front", "whats in front")),
    ("computer", ("/computer", "/open", "/close", "/lock", "lock everything", "privacy mode",
                  "camera on", "camera off", "microphone off", "pause the agent",
                  "emergency stop", "stop everything",
                  "open my", "open the", "close my", "close the", "launch the",
                  # Hindi / Hinglish (spec section 14: match the user's language)
                  "kholo", "khol do", "khul jaye", "band karo", "band kar do",
                  "lock kar do", "sab lock", "camera chalu", "camera band")),
    ("memory", ("/remember", "/recall", "/memory", "remember that", "don't forget",
                "what do you know about", "forget that",
                "yaad rakhna", "yaad rakho", "yaad hai", "bhool jao", "bhool ja")),
    ("productivity", ("/task", "/plan", "remind me", "add a task", "plan my day",
                      "what's on my list", "whats on my list", "daily plan", "todo",
                      "yaad dilana", "aaj ka plan", "kaam ki list", "kaam yaad")),
    ("research", ("/research", "find out", "research", "look up", "what is", "who is",
                  "how does", "explain", "tell me about", "verify",
                  "batao", "kya hai", "kaun hai", "kaise", "samjhao", "samjha do")),
]

#: Website categories recognised in natural language.
SITE_CATEGORIES = {
    # Keys must match jarvis.agents.webbuilder.CATEGORIES exactly - a mismatch
    # here means the builder rejects a category the parser just produced.
    "ecommerce": ("e-commerce", "ecommerce", "online store", "shop"),
    "portfolio": ("portfolio",),
    "saas": ("saas",),
    "business": ("business site", "company website", "business website"),
    "blog": ("blog",),
    "landing": ("landing page", "landing"),
    "dashboard": ("dashboard", "admin panel"),
    "education": ("course", "education", "learning platform", "school"),
    "booking": ("booking", "appointment", "reservation"),
    "ai_app": ("ai app", "chatbot", "ai application"),
}

DOC_FORMATS = {
    "xlsx": ("excel", "spreadsheet", "xlsx", "workbook"),
    "csv": ("csv",),
    "docx": ("word", "docx", "document file"),
    "pdf": ("pdf",),
    "md": ("markdown", "readme"),
}


def parse_intent(
    text: str,
    *,
    intent: str | None = None,
    language: str | None = None,
    policy: str = "match",
    **params: Any,
) -> AgentRequest:
    """Parse ``text`` into an :class:`AgentRequest`.

    ``intent`` and ``params`` override the parsed values, so the CLI's slash
    commands and structured callers take precedence over keyword guessing.
    """
    raw = (text or "").strip()
    lowered = raw.lower()
    detected = detect(raw)
    reply_language = language or respond_in(raw, policy)

    explicit, remainder = _split_command(raw)
    resolved = intent or explicit or _match_intent(lowered)
    scan_target = (remainder or raw).lower()

    request = AgentRequest(
        intent=resolved,
        text=(remainder or raw).strip(),
        user_request=raw,
        language=reply_language,
        params=dict(params),
    )
    request.params.setdefault("detected_language", detected.as_dict())

    if resolved == "build":
        request.params.setdefault("category", _match_category(scan_target))
        request.params.setdefault("name", _slug(remainder or raw) or "jarvis-site")
    if resolved == "document":
        request.params.setdefault("format", _match_format(scan_target))
        request.params.setdefault("title", (remainder or raw).strip()[:80] or "Document")
    if resolved == "computer":
        request.params.setdefault("intent", _computer_action(lowered))
    if resolved == "productivity":
        request.params.setdefault("intent", _productivity_action(lowered))
    if resolved == "memory":
        request.params.setdefault("intent", _memory_action(lowered))
    if resolved == "coding":
        request.params.setdefault("mode", "run" if _wants_execution(lowered) else "review")
    if resolved == "research":
        request.params.setdefault("query", (remainder or raw).strip())
    return request


# --------------------------------------------------------------------------- #
def _split_command(text: str) -> tuple[str | None, str]:
    """Pull a leading ``/command`` off the input."""
    match = re.match(r"^/([a-z]+)\s*(.*)$", text.strip(), flags=re.S | re.I)
    if not match:
        return None, text
    return match.group(1).lower(), match.group(2).strip()


def _match_intent(lowered: str) -> str:
    for intent, keywords in INTENT_RULES:
        if any(keyword in lowered for keyword in keywords):
            return intent
    return "research"


def _match_category(scan: str) -> str:
    for category, keywords in SITE_CATEGORIES.items():
        if any(keyword in scan for keyword in keywords):
            return category
    return "business"


def _match_format(scan: str) -> str:
    for fmt, keywords in DOC_FORMATS.items():
        if any(keyword in scan for keyword in keywords):
            return fmt
    return "xlsx"


def _computer_action(lowered: str) -> str:
    table = [
        ("lock everything", "lock"), ("lock device", "lock"), ("/lock", "lock"),
        ("privacy mode", "privacy_on"), ("privacy off", "privacy_off"),
        ("camera on", "camera_on"), ("camera off", "camera_off"),
        ("microphone on", "mic_on"), ("mic on", "mic_on"),
        ("microphone off", "mic_off"), ("mic off", "mic_off"),
        ("emergency stop", "status"), ("stop everything", "status"),
        ("pause the agent", "status"),
    ]
    for needle, action in table:
        if needle in lowered:
            return action
    hindi_open = ("kholo", "khol do", "khol de", "khul jaye", "chalu karo", "chalao")
    hindi_close = ("band karo", "band kar do", "band kar de", "band ho")
    if lowered.startswith("/open") or " open " in f" {lowered} " or any(w in lowered for w in hindi_open):
        return "open"
    if lowered.startswith("/close") or " close " in f" {lowered} " or any(w in lowered for w in hindi_close):
        return "close"
    return "status"


def _productivity_action(lowered: str) -> str:
    if any(w in lowered for w in ("remind me", "add a task", "add task", "new task",
                                  "/task add", "yaad dilana", "yaad rakhna", "kaam add")):
        return "add"
    if any(w in lowered for w in ("plan my day", "daily plan", "what's on my list", "whats on my list")):
        return "plan"
    return "list"


def _memory_action(lowered: str) -> str:
    if any(w in lowered for w in ("remember that", "remember this", "/remember")):
        return "remember"
    if any(w in lowered for w in ("forget that", "forget this", "delete that memory")):
        return "forget"
    if "export" in lowered:
        return "export"
    return "search"


def _wants_execution(lowered: str) -> bool:
    return any(w in lowered for w in ("run it", "run this", "execute", "run the tests", "pytest"))


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:40]


def describe_intents() -> dict[str, list[str]]:
    """The routing table, for the CLI's help output."""
    return {intent: list(keywords) for intent, keywords in INTENT_RULES}
