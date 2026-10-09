"""Anti-hallucination primitives (spec section 27).

Every important claim Jarvis makes is wrapped in a :class:`Claim` that carries
a confidence level, its sources, when they were verified, and an explicit
statement of what is *not* known. Nothing downstream is allowed to upgrade a
``LOW``/``UNKNOWN`` claim into a confident-sounding sentence -- the formatting
helpers refuse to.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from jarvis.util.clock import age_days, now_iso


class Confidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        return {"unknown": 0, "low": 1, "medium": 2, "high": 3}[self.value]

    def __lt__(self, other: Confidence) -> bool:  # enables min()/sorted()
        if not isinstance(other, Confidence):
            return NotImplemented
        return self.rank < other.rank

    @classmethod
    def from_score(cls, score: float) -> Confidence:
        if score >= 0.8:
            return cls.HIGH
        if score >= 0.55:
            return cls.MEDIUM
        if score > 0.0:
            return cls.LOW
        return cls.UNKNOWN


class Authority(str, enum.Enum):
    """How much weight a source deserves, independent of how it reads."""

    PRIMARY = "primary"  # statute, regulation, official register, first-party docs
    OFFICIAL_SECONDARY = "official_secondary"  # government guidance, platform help centre
    REPUTABLE = "reputable"  # established press, vendor engineering blog
    COMMUNITY = "community"  # forums, Q&A sites, personal blogs
    UNVERIFIED = "unverified"  # no attributable origin

    @property
    def weight(self) -> float:
        return {
            "primary": 1.0,
            "official_secondary": 0.8,
            "reputable": 0.6,
            "community": 0.35,
            "unverified": 0.1,
        }[self.value]


#: Phrases the agent is forbidden from using about an uncertain claim.
BANNED_ABSOLUTES = (
    "100% guaranteed",
    "guaranteed to work",
    "absolutely certain",
    "no risk at all",
    "definitely legal",
    "completely risk free",
)


@dataclass
class Source:
    """A citation with enough metadata to be re-verified later."""

    title: str
    url: str = ""
    authority: Authority = Authority.UNVERIFIED
    jurisdiction: str = ""
    published: str | None = None
    retrieved: str = now_iso()
    publisher: str = ""

    @property
    def staleness_days(self) -> float | None:
        return age_days(self.published or self.retrieved)

    def is_stale(self, max_days: float = 365.0) -> bool:
        age = self.staleness_days
        return age is not None and age > max_days

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "authority": self.authority.value,
            "jurisdiction": self.jurisdiction,
            "published": self.published,
            "retrieved": self.retrieved,
            "publisher": self.publisher,
        }


@dataclass
class Claim:
    """One assertion plus everything needed to judge it.

    ``deterministic`` is the *only* way to say something is guaranteed: it means
    the statement follows from code or arithmetic, not from research.
    """

    statement: str
    confidence: Confidence = Confidence.UNKNOWN
    sources: list[Source] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    deterministic: bool = False
    time_sensitive: bool = False
    last_verified: str | None = now_iso()
    reasoning: str = ""

    # --- construction helpers ------------------------------------------------
    @classmethod
    def certain(cls, statement: str, *, reasoning: str = "") -> Claim:
        """A claim derived from computation, not from external research."""
        return cls(
            statement=statement,
            confidence=Confidence.HIGH,
            deterministic=True,
            reasoning=reasoning or "derived by computation",
        )

    @classmethod
    def unknown(cls, statement: str, *, why: str = "") -> Claim:
        return cls(
            statement=statement,
            confidence=Confidence.UNKNOWN,
            caveats=[why or "no verified source available"],
        )

    # --- scoring -------------------------------------------------------------
    def score(self) -> float:
        """Blend source authority, freshness and agreement into 0..1."""
        if self.deterministic:
            return 1.0
        if not self.sources:
            return 0.0
        authority = max(s.authority.weight for s in self.sources)
        freshness = 1.0
        if self.time_sensitive:
            oldest = max((s.staleness_days or 0.0) for s in self.sources)
            freshness = max(0.0, 1.0 - oldest / 365.0)
        corroboration = min(1.0, 0.6 + 0.2 * (len(self.sources) - 1))
        penalty = 0.15 * len(self.caveats)
        return max(0.0, min(1.0, authority * (0.5 + 0.3 * freshness) * corroboration * 1.15 - penalty))

    def refresh_confidence(self) -> Confidence:
        """Recompute confidence from the current evidence and update the claim."""
        self.confidence = (
            Confidence.HIGH if self.deterministic else Confidence.from_score(self.score())
        )
        return self.confidence

    def add_source(self, source: Source) -> Claim:
        self.sources.append(source)
        self.refresh_confidence()
        return self

    # --- presentation --------------------------------------------------------
    def render(self) -> str:
        """A user-safe sentence that never overstates the evidence."""
        banned = [b for b in BANNED_ABSOLUTES if b in self.statement.lower()]
        if banned and not self.deterministic:
            return (
                f"{self.statement} (confidence: {self.confidence.value} - "
                f"absolute wording removed: {', '.join(banned)})"
            )
        if self.deterministic:
            return f"{self.statement} (verified by computation)"
        suffix = f" (confidence: {self.confidence.value}"
        if self.time_sensitive:
            suffix += ", time-sensitive"
        suffix += ")"
        if self.sources:
            names = ", ".join(s.title for s in self.sources[:3])
            suffix = suffix[:-1] + f"; sources: {names})"
        if self.caveats:
            suffix += " Caveats: " + " ".join(self.caveats)
        return f"{self.statement}{suffix}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "confidence": self.confidence.value,
            "deterministic": self.deterministic,
            "time_sensitive": self.time_sensitive,
            "score": round(self.score(), 3),
            "sources": [s.as_dict() for s in self.sources],
            "caveats": list(self.caveats),
            "last_verified": self.last_verified,
            "reasoning": self.reasoning,
        }


def weakest(claims: Iterable[Claim]) -> Confidence:
    """Confidence of a composite statement = weakest link."""
    levels = [c.confidence for c in claims]
    if not levels:
        return Confidence.UNKNOWN
    return min(levels, key=lambda c: c.rank)


def needs_verification(claims: Sequence[Claim], max_stale_days: float = 365.0) -> list[Claim]:
    """Claims that must be re-checked against a live source before being relied on."""
    out: list[Claim] = []
    for c in claims:
        if c.deterministic:
            continue
        stale = any(s.is_stale(max_stale_days) for s in c.sources)
        if c.confidence.rank < Confidence.MEDIUM.rank or (c.time_sensitive and stale):
            out.append(c)
    return out
