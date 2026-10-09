"""Secret detection and redaction (spec sections 8, 12, 28, 32).

Anything that flows into memory, the audit log, task checkpoints or a generated
report passes through here first. The goal is not to be a perfect DLP product --
it is to make accidental leakage of a credential into a persisted artifact very
unlikely, and to make the failure mode obvious instead of silent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from re import error
from typing import Any

REDACTION = "[REDACTED]"

# Ordered list of (label, compiled pattern). Patterns are deliberately anchored
# on entropy-bearing shapes plus a keyword, to keep false positives low.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END")),
    ("aws-access-key-id", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe-secret", re.compile(r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{10,}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("telegram-bot-token", re.compile(r"\b\d{8,10}:AA[0-9A-Za-z_\-]{33,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    # password / secret / token / apikey assignments in any common syntax.
    # Only spaces and tabs are allowed around the separator: using \s there let
    # the pattern run across a line break and flag the *next* line as the value,
    # which produced false positives on empty placeholder lines such as
    # "DATABASE_PASSWORD=" in a .env.example.
    (
        "assignment-secret",
        re.compile(
            r"""(?ix)
            (?P<key>(?<![A-Za-z0-9])(?:pass(?:word|wd)?|secret|token|api[_-]?key|
                        access[_-]?key|private[_-]?key|client[_-]?secret|auth)\b)
            [ \t]*[:=][ \t]*
            (?P<quote>['"]?)
            (?P<value>[^\s'",;}{)]{6,})
            """,
        ),
    ),
    (
        "url-credentials",
        re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:(?P<pw>[^/\s@]{3,})@"),
    ),
    ("private-ip-range", re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}(?:\.\d{1,3})?\b")),
]

#: Text that looks secret-ish but is safe and shows up in docs/examples.
_SAFE_VALUES = {
    "your-key-here",
    "changeme",
    "example",
    "placeholder",
    "redacted",
    "xxxxxxxx",
    "your_key_here",
    "none",
    "null",
    "true",
    "false",
    "***",
}


@dataclass(frozen=True)
class Finding:
    """One detected secret-ish substring."""

    label: str
    start: int
    end: int
    preview: str

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.label}@{self.start}:{self.preview}"


def _preview(value: str, limit: int = 4) -> str:
    value = value.strip()
    if len(value) <= limit:
        return "*" * len(value)
    return value[:2] + "*" * min(len(value) - 2, 6)


def _secret_text(match: re.Match[str]) -> str:
    """The sensitive part of a match, whatever the pattern called its group.

    Not every pattern has a named group; using ``match.group("value")`` blindly
    raised IndexError on the patterns that do not.
    """
    for name in ("value", "pw"):
        try:
            captured = match.group(name)
        except (IndexError, error):
            captured = None
        if captured:
            return captured
    return match.group(0)


def scan(text: str) -> list[Finding]:
    """Return every secret-shaped region found in ``text``."""
    if not text:
        return []
    found: list[Finding] = []
    for label, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            value = _secret_text(match)
            if value.strip().lower() in _SAFE_VALUES:
                continue
            start, end = match.span()
            # Skip regions already covered by a higher-priority pattern.
            if any(f.start <= start and end <= f.end for f in found):
                continue
            found.append(Finding(label, start, end, _preview(value)))
    found.sort(key=lambda f: f.start)
    return found


def redact(text: str, replacement: str = REDACTION) -> str:
    """Return ``text`` with every secret-shaped region replaced."""
    if not text:
        return text
    findings = scan(text)
    if not findings:
        return text
    out: list[str] = []
    cursor = 0
    for f in findings:
        if f.start < cursor:
            continue
        out.append(text[cursor : f.start])
        out.append(f"{replacement}:{f.label}")
        cursor = f.end
    out.append(text[cursor:])
    return "".join(out)


def has_secret(text: str) -> bool:
    return bool(scan(text))


def scan_mapping(data: Mapping[str, Any], _path: str = "") -> list[Finding]:
    """Recursively scan a mapping / sequence for secrets."""
    out: list[Finding] = []
    stack: Iterable[Any] = [(data, _path)]
    for value, path in list(stack):
        if isinstance(value, Mapping):
            for k, v in value.items():
                out.extend(scan_mapping(v, f"{path}.{k}" if path else str(k)))
        elif isinstance(value, (list, tuple, set)):
            for i, v in enumerate(value):
                out.extend(scan_mapping(v, f"{path}[{i}]"))
        elif isinstance(value, str):
            for f in scan(value):
                out.append(f)
    return out


def redact_mapping(data: Any) -> Any:
    """Return a deep copy of ``data`` with secret-shaped strings redacted."""
    if isinstance(data, Mapping):
        return {k: redact_mapping(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [redact_mapping(v) for v in data]
    if isinstance(data, set):
        return [redact_mapping(v) for v in sorted(data, key=repr)]
    if isinstance(data, str):
        return redact(data)
    return data
