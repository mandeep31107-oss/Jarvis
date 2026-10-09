"""Jurisdiction-aware compliance knowledge (spec section 4).

The engine keeps, for every entry: source, jurisdiction, effective date,
publication date, version, confidence, last verification date, the business
activity it is relevant to, and the restrictions it imposes.

The single most important behaviour is ``needs_verification``. Legal and tax
facts go stale, so an entry that has never been checked against its primary
source -- or was checked a long time ago -- is reported as *unverified* rather
than presented as settled fact.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from jarvis.core.confidence import Authority, Confidence

#: Always attached to any compliance output. Non-negotiable per spec section 4.
DISCLAIMER = (
    "This is information and risk analysis, not a substitute for qualified legal or tax advice."
)

DATA_FILE = "jurisdictions.json"

#: Entries older than this (in days) are treated as needing re-verification.
MAX_VERIFIED_AGE_DAYS = 365.0


@dataclass
class Regulation:
    """One law, regulation or official guidance entry."""

    id: str
    jurisdiction: str
    topic: str
    title: str
    summary: str
    authority: Authority = Authority.UNVERIFIED
    source_title: str = ""
    source_url: str = ""
    publisher: str = ""
    published: str | None = None
    effective: str | None = None
    version: str = ""
    confidence: Confidence = Confidence.UNKNOWN
    last_verified: str | None = None
    applies_to: list[str] = field(default_factory=list)
    restrictions: list[str] = field(default_factory=list)

    @property
    def needs_verification(self) -> bool:
        """True unless someone has recently checked this against its primary source."""
        from jarvis.util.clock import age_days

        if not self.last_verified:
            return True
        age = age_days(self.last_verified)
        return age is None or age > MAX_VERIFIED_AGE_DAYS

    @property
    def status_label(self) -> str:
        if self.needs_verification:
            return "UNVERIFIED - check the primary source before relying on this"
        return f"verified {self.last_verified}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "jurisdiction": self.jurisdiction,
            "topic": self.topic,
            "title": self.title,
            "summary": self.summary,
            "authority": self.authority.value,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "publisher": self.publisher,
            "published": self.published,
            "effective": self.effective,
            "version": self.version,
            "confidence": self.confidence.value,
            "last_verified": self.last_verified,
            "status": self.status_label,
            "applies_to": list(self.applies_to),
            "restrictions": list(self.restrictions),
        }

    def render(self) -> str:
        lines = [
            f"[{self.jurisdiction}/{self.topic}] {self.title}",
            f"    {self.summary}",
            f"    source : {self.source_title} <{self.source_url}> ({self.authority.value})",
            f"    dates  : published={self.published or '?'} effective={self.effective or 'not commenced / unknown'}"
            f"  version={self.version or '?'}",
            f"    status : {self.status_label}  confidence={self.confidence.value}",
        ]
        if self.applies_to:
            lines.append("    applies: " + "; ".join(self.applies_to))
        if self.restrictions:
            lines.extend(f"    - {r}" for r in self.restrictions)
        return "\n".join(lines)


@dataclass
class ComplianceBrief:
    """What the user actually needs: what applies, what to check, what to avoid."""

    activity: str
    jurisdictions: list[str]
    entries: list[Regulation] = field(default_factory=list)
    missing_coverage: list[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER

    @property
    def unverified(self) -> list[Regulation]:
        return [e for e in self.entries if e.needs_verification]

    @property
    def all_verified(self) -> bool:
        return bool(self.entries) and not self.unverified

    def restrictions(self) -> list[str]:
        out: list[str] = []
        for e in self.entries:
            out.extend(f"[{e.jurisdiction}] {r}" for r in e.restrictions)
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "activity": self.activity,
            "jurisdictions": list(self.jurisdictions),
            "entries": [e.as_dict() for e in self.entries],
            "unverified_count": len(self.unverified),
            "missing_coverage": list(self.missing_coverage),
            "disclaimer": self.disclaimer,
        }

    def render(self) -> str:
        if not self.entries:
            return (
                f"No jurisdiction data on file for '{self.activity}' in "
                f"{', '.join(self.jurisdictions) or 'the configured jurisdiction'}.\n"
                "This does not mean it is unregulated - it means Jarvis has no verified "
                "entry yet and will not guess.\n" + self.disclaimer
            )
        head = f"Compliance brief: {self.activity} ({', '.join(self.jurisdictions)})"
        body = "\n\n".join(e.render() for e in self.entries)
        tail = []
        if self.missing_coverage:
            tail.append("Gaps: " + "; ".join(self.missing_coverage))
        if self.unverified:
            tail.append(
                f"{len(self.unverified)} of {len(self.entries)} entries are UNVERIFIED - "
                "confirm against the linked primary source before acting."
            )
        tail.append(self.disclaimer)
        return "\n".join([head, "", body, "", *tail])


#: Topics the engine knows how to look up.
TOPICS = (
    "data_privacy",
    "tax",
    "consumer_protection",
    "ai_regulation",
    "communication",
    "employment",
    "financial_services",
)

#: Human-readable jurisdiction names.
JURISDICTIONS = {
    "IN": "India",
    "US": "United States (federal)",
    "US-CA": "United States - California",
    "CA": "Canada",
    "GB": "United Kingdom",
    "EU": "European Union",
    "AU": "Australia",
    "SG": "Singapore",
    "AE": "United Arab Emirates",
    "JP": "Japan",
    "KR": "South Korea",
}


class JurisdictionKnowledge:
    """Read-only registry of legal/tax entries with verification tracking."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        raw = data if data is not None else _load_data()
        self.generated = raw.get("generated")
        self.verify_every = bool(raw.get("verify_every", True))
        self._entries: dict[str, Regulation] = {}
        for item in raw.get("entries", []):
            reg = Regulation(
                id=item["id"],
                jurisdiction=item.get("jurisdiction", ""),
                topic=item.get("topic", ""),
                title=item.get("title", ""),
                summary=item.get("summary", ""),
                authority=Authority(item.get("authority", "unverified")),
                source_title=item.get("source_title", ""),
                source_url=item.get("source_url", ""),
                publisher=item.get("publisher", ""),
                published=item.get("published"),
                effective=item.get("effective"),
                version=item.get("version", ""),
                confidence=Confidence(item.get("confidence", "unknown")),
                last_verified=item.get("last_verified"),
                applies_to=list(item.get("applies_to", [])),
                restrictions=list(item.get("restrictions", [])),
            )
            self._entries[reg.id] = reg

    # --- registry ------------------------------------------------------------
    @property
    def entries(self) -> list[Regulation]:
        return list(self._entries.values())

    def get(self, entry_id: str) -> Regulation | None:
        return self._entries.get(entry_id)

    def add(self, reg: Regulation) -> None:
        """Entries can be added at runtime (e.g. after the user verifies one),
        which is how the 'last verification date' ever becomes real."""
        self._entries[reg.id] = reg

    def mark_verified(self, entry_id: str, when: str | None = None) -> Regulation | None:
        reg = self._entries.get(entry_id)
        if reg is None:
            return None
        from jarvis.util.clock import now_iso

        reg.last_verified = when or now_iso()
        return reg

    def coverage(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for reg in self.entries:
            out.setdefault(reg.jurisdiction, [])
            if reg.topic not in out[reg.jurisdiction]:
                out[reg.jurisdiction].append(reg.topic)
        return out

    # --- queries -------------------------------------------------------------
    def lookup(
        self,
        jurisdictions: Sequence[str],
        topics: Sequence[str] = (),
        *,
        include_parent: bool = True,
    ) -> list[Regulation]:
        """Find entries for the given jurisdictions and topics.

        ``include_parent`` means a request for ``US-CA`` also returns federal
        ``US`` entries, because state law sits on top of federal law.
        """
        wanted: set[str] = set()
        for j in jurisdictions:
            wanted.add(j)
            if include_parent and "-" in j:
                wanted.add(j.split("-", 1)[0])
        out = [
            r
            for r in self.entries
            if r.jurisdiction in wanted and (not topics or r.topic in topics)
        ]
        # Primary sources first, then most recent.
        out.sort(key=lambda r: (r.authority.weight, r.published or ""), reverse=True)
        return out

    def brief(
        self,
        activity: str,
        jurisdictions: Sequence[str],
        topics: Sequence[str] = ("data_privacy", "tax", "consumer_protection"),
    ) -> ComplianceBrief:
        entries = self.lookup(list(jurisdictions), list(topics))
        found_topics = {e.topic for e in entries}
        missing = [t for t in topics if t not in found_topics]
        return ComplianceBrief(
            activity=activity,
            jurisdictions=list(jurisdictions),
            entries=entries,
            missing_coverage=missing,
        )

    def verification_queue(self) -> list[Regulation]:
        """Everything that should be re-checked. Surfaced in the dashboard's
        Compliance page so stale knowledge never silently ages."""
        return [r for r in self.entries if r.needs_verification]

    def render_coverage(self) -> str:
        cov = self.coverage()
        if not cov:
            return "(no jurisdiction data loaded)"
        lines = []
        for j in sorted(cov):
            name = JURISDICTIONS.get(j, j)
            lines.append(f"- {j} ({name}): {', '.join(sorted(cov[j]))}")
        queue = self.verification_queue()
        lines.append("")
        lines.append(f"{len(queue)} of {len(self.entries)} entries need verification.")
        lines.append(self.generated and f"Data generated: {self.generated}" or "")
        lines.append(DISCLAIMER)
        return "\n".join(line for line in lines if line is not None)


def _load_data() -> dict[str, Any]:
    ref = resources.files("jarvis.compliance.data").joinpath(DATA_FILE)
    return json.loads(ref.read_text(encoding="utf-8"))


def related_jurisdictions(codes: Iterable[str]) -> list[str]:
    """Expand a list of target markets into every jurisdiction that matters."""
    out: list[str] = []
    for code in codes:
        code = code.strip().upper()
        if not code:
            continue
        if code not in out:
            out.append(code)
        if "-" in code:
            parent = code.split("-", 1)[0]
            if parent not in out:
                out.append(parent)
    return out
