"""Configuration and settings.

Design rules enforced here:

* No secret ever lives in source code (spec section 32). Values come from the
  process environment, optionally seeded from a local ``.env`` that is git
  ignored.
* Everything has a working default, so Jarvis boots with zero configuration.
* Settings are immutable once built; the runtime is constructed from them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Names of settings whose values must never be printed, logged or persisted.
SECRET_KEY_HINTS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE",
    "AUTH",
)

_VALID_AUTONOMY = ("auto", "ask", "review")
_VALID_LANG_POLICY = ("match", "en", "hi", "hinglish")


#: Names that are not credentials themselves but routinely carry one: a
#: ``DATABASE_URL`` is ``scheme://user:password@host/db`` more often than not.
SECRET_VALUE_HINTS = ("_URL", "_URI", "_DSN", "_CONN")


def is_secret_name(name: str) -> bool:
    """True when an environment variable name looks like it holds a secret."""
    upper = name.upper()
    return any(hint in upper for hint in SECRET_KEY_HINTS) or any(
        hint in upper for hint in SECRET_VALUE_HINTS
    )


def load_dotenv(path: str | Path) -> dict[str, str]:
    """Tiny dependency-free ``.env`` parser.

    Only ``KEY=value`` lines are honoured; existing environment variables win so
    that a shell export always overrides the file. Returns the parsed mapping
    (secrets included) but never prints it.
    """
    found: dict[str, str] = {}
    p = Path(path)
    if not p.is_file():
        return found
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if not key:
            continue
        found[key] = value
        os.environ.setdefault(key, value)
    return found


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration."""

    home: Path
    autonomy: str = "auto"
    jurisdiction: str = "IN"
    language_policy: str = "match"
    computer_use: bool = False
    capture_devices: tuple[str, ...] = ()
    always_active: bool = False
    #: Phase 2: may Jarvis retrieve URLs at all? Off by default - network access
    #: is a capability the operator grants, not a convenience that is assumed.
    fetch_enabled: bool = False
    database_url: str | None = None
    redis_url: str | None = None
    model_provider: str = "auto"
    extra: Mapping[str, str] = field(default_factory=dict)

    # --- derived paths -----------------------------------------------------
    @property
    def memory_db(self) -> Path:
        return self.home / "memory.sqlite3"

    @property
    def audit_path(self) -> Path:
        return self.home / "audit" / "actions.jsonl"

    @property
    def checkpoint_dir(self) -> Path:
        return self.home / "checkpoints"

    @property
    def documents_dir(self) -> Path:
        return self.home / "documents"

    @property
    def consent_path(self) -> Path:
        return self.home / "consent.json"

    def ensure_dirs(self) -> None:
        for d in (
            self.home,
            self.home / "audit",
            self.checkpoint_dir,
            self.documents_dir,
            self.home / "exports",
        ):
            d.mkdir(parents=True, exist_ok=True)

    # --- secrets -----------------------------------------------------------
    def secret(self, name: str, default: str | None = None) -> str | None:
        """Read a secret from the environment. Never cached, never logged."""
        return os.environ.get(name, default)

    def has_secret(self, name: str) -> bool:
        return bool(os.environ.get(name))

    def redacted_view(self) -> dict[str, Any]:
        """A representation that is safe to print or store."""
        data: dict[str, Any] = {
            "home": str(self.home),
            "autonomy": self.autonomy,
            "jurisdiction": self.jurisdiction,
            "language_policy": self.language_policy,
            "computer_use": self.computer_use,
            "capture_devices": list(self.capture_devices),
            "always_active": self.always_active,
            "fetch_enabled": self.fetch_enabled,
            "database_url": "***" if self.database_url else None,
            "redis_url": "***" if self.redis_url else None,
            "model_provider": self.model_provider,
        }
        from jarvis.util import redaction

        # Two independent filters, because a name-based blocklist will always miss
        # something: drop secret-named variables outright, and mask anything else
        # whose *value* looks like a credential.
        data["extra"] = {
            key: ("***" if redaction.has_secret(str(value)) else value)
            for key, value in sorted(self.extra.items())
            if not is_secret_name(key)
        }
        data["configured_providers"] = sorted(
            name
            for name in ("OPENAI", "ANTHROPIC", "GOOGLE", "GROQ", "OPENROUTER")
            if self.has_secret(f"{name}_API_KEY")
        )
        return data


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def settings_from_env(env: Mapping[str, str] | None = None, *, dotenv: str | Path | None = ".env") -> Settings:
    """Build :class:`Settings` from the environment.

    ``env`` is only used for tests / explicit injection; production reads
    ``os.environ`` (optionally seeded from ``.env``).
    """
    source: Mapping[str, str] = env if env is not None else os.environ
    if env is None and dotenv is not None:
        load_dotenv(dotenv)
        source = os.environ

    home = Path(source.get("JARVIS_HOME") or Path.home() / ".jarvis").expanduser()

    autonomy = (source.get("JARVIS_AUTONOMY") or "auto").strip().lower()
    if autonomy not in _VALID_AUTONOMY:
        autonomy = "auto"

    lang = (source.get("JARVIS_LANGUAGE_POLICY") or "match").strip().lower()
    if lang not in _VALID_LANG_POLICY:
        lang = "match"

    devices = tuple(
        d.strip().lower()
        for d in (source.get("JARVIS_CAPTURE_DEVICES") or "").split(",")
        if d.strip()
    )

    return Settings(
        home=home,
        autonomy=autonomy,
        jurisdiction=(source.get("JARVIS_JURISDICTION") or "IN").strip().upper(),
        language_policy=lang,
        computer_use=_as_bool(source.get("JARVIS_COMPUTER_USE")),
        capture_devices=devices,
        always_active=_as_bool(source.get("JARVIS_ALWAYS_ACTIVE")),
        fetch_enabled=_as_bool(source.get("JARVIS_FETCH")),
        database_url=source.get("JARVIS_DATABASE_URL") or None,
        redis_url=source.get("JARVIS_REDIS_URL") or None,
        model_provider=(source.get("JARVIS_MODEL_PROVIDER") or "auto").strip().lower(),
        extra={k: v for k, v in source.items() if k.startswith("JARVIS_")},
    )
