"""Terms & Policy Intelligence (spec section 5).

The load-bearing rule here is from the spec, and it is implemented as control
flow rather than as advice:

    Do not assume that automation is permitted merely because it is technically
    possible. If a proposed action conflicts with a platform policy: STOP.

So :meth:`TermsRegistry.check` returns a verdict, and
:meth:`TermsRegistry.evaluate_plan` returns ``must_stop=True`` the moment any
step in a plan is prohibited -- together with the rule at issue, why it is risky
and what the sanctioned alternative is.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from importlib import resources
from typing import Any

from jarvis.core.confidence import Confidence
from jarvis.util.clock import age_days, now_iso

DATA_FILE = "platforms.json"
MAX_VERIFIED_AGE_DAYS = 180.0  # platform policies move faster than statutes

ALLOWED = "allowed"
CONDITIONAL = "conditional"
DENIED = "denied"
UNKNOWN = "unknown"

#: Seed data may express a verdict as a JSON boolean, "yes"/"no", or the verdict
#: word itself. Everything is normalised here so a typo cannot silently turn a
#: prohibition into an "unknown".
_VERDICT_ALIASES = {
    "true": ALLOWED,
    "yes": ALLOWED,
    "permitted": ALLOWED,
    "allow": ALLOWED,
    ALLOWED: ALLOWED,
    "false": DENIED,
    "no": DENIED,
    "prohibited": DENIED,
    "forbidden": DENIED,
    DENIED: DENIED,
    CONDITIONAL: CONDITIONAL,
    "with conditions": CONDITIONAL,
    "conditional with conditions": CONDITIONAL,
    "": UNKNOWN,
    UNKNOWN: UNKNOWN,
}


def normalize_verdict(value: Any) -> str:
    """Map any seed-data spelling of a verdict onto the canonical four."""
    if isinstance(value, bool):
        return ALLOWED if value else DENIED
    if value is None:
        return UNKNOWN
    text = str(value).strip().lower()
    return _VERDICT_ALIASES.get(text, UNKNOWN)


@dataclass
class ActivityRule:
    activity: str
    allowed: str  # allowed | conditional | denied | unknown
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"activity": self.activity, "allowed": self.allowed, "reason": self.reason}


@dataclass
class Platform:
    id: str
    name: str
    category: str = ""
    automation_allowed: str = UNKNOWN
    sanctioned_channel: str = ""
    source_title: str = ""
    source_url: str = ""
    confidence: Confidence = Confidence.UNKNOWN
    last_verified: str | None = None
    activities: dict[str, ActivityRule] = field(default_factory=dict)
    risk_notes: list[str] = field(default_factory=list)

    @property
    def needs_verification(self) -> bool:
        if not self.last_verified:
            return True
        age = age_days(self.last_verified)
        return age is None or age > MAX_VERIFIED_AGE_DAYS

    def rule(self, activity: str) -> ActivityRule | None:
        return self.activities.get(activity)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "automation_allowed": self.automation_allowed,
            "sanctioned_channel": self.sanctioned_channel,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "confidence": self.confidence.value,
            "last_verified": self.last_verified,
            "needs_verification": self.needs_verification,
            "activities": {k: v.as_dict() for k, v in self.activities.items()},
            "risk_notes": list(self.risk_notes),
        }


@dataclass
class TermsCheck:
    """The verdict for one (platform, activity) pair."""

    platform: str
    activity: str
    verdict: str  # allowed | conditional | denied | unknown
    reason: str
    sanctioned_channel: str = ""
    source: str = ""
    source_url: str = ""
    verified: bool = False
    safer_alternatives: list[str] = field(default_factory=list)

    @property
    def must_stop(self) -> bool:
        return self.verdict in (DENIED, UNKNOWN)

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "activity": self.activity,
            "verdict": self.verdict,
            "reason": self.reason,
            "sanctioned_channel": self.sanctioned_channel,
            "source": self.source,
            "source_url": self.source_url,
            "verified": self.verified,
            "safer_alternatives": list(self.safer_alternatives),
            "must_stop": self.must_stop,
        }

    def render(self) -> str:
        head = f"{self.platform}/{self.activity}: {self.verdict.upper()}"
        lines = [head, f"    why: {self.reason}"]
        if self.sanctioned_channel:
            lines.append(f"    sanctioned channel: {self.sanctioned_channel}")
        if self.source:
            lines.append(f"    source: {self.source} <{self.source_url}>")
        if not self.verified:
            lines.append("    NOTE: policy entry is unverified - re-read the current terms.")
        for alt in self.safer_alternatives:
            lines.append(f"    safer alternative: {alt}")
        return "\n".join(lines)


@dataclass
class PlanVerdict:
    """A whole plan checked step by step. Stops at the first prohibited step."""

    platform: str
    steps: list[str]
    checks: list[TermsCheck] = field(default_factory=list)
    must_stop: bool = False
    blocking: TermsCheck | None = None

    @property
    def conditionals(self) -> list[TermsCheck]:
        return [c for c in self.checks if c.verdict == CONDITIONAL]

    def as_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "steps": list(self.steps),
            "checks": [c.as_dict() for c in self.checks],
            "must_stop": self.must_stop,
            "blocking": self.blocking.as_dict() if self.blocking else None,
        }

    def render(self) -> str:
        lines = [f"Platform policy check: {self.platform} -> {', '.join(self.steps)}"]
        lines.extend("  " + c.render().replace("\n", "\n  ") for c in self.checks)
        if self.must_stop and self.blocking:
            lines.append("")
            lines.append("STOP. " + self.blocking.reason)
            if self.blocking.safer_alternatives:
                lines.append("Safer alternatives:")
                lines.extend(f"  - {a}" for a in self.blocking.safer_alternatives)
        elif self.conditionals:
            lines.append("")
            lines.append(
                "Proceed only within the sanctioned channel and after the user confirms "
                "the conditions are met."
            )
        else:
            lines.append("")
            lines.append("No conflict found with the entries on file - still read the current terms.")
        return "\n".join(lines)


class TermsRegistry:
    """Tracks platform policies and refuses to route around them."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        raw = data if data is not None else _load_data()
        self.activity_descriptions: dict[str, str] = dict(raw.get("activities", {}))
        self._platforms: dict[str, Platform] = {}
        for item in raw.get("platforms", []):
            activities = {
                name: ActivityRule(
                    activity=name,
                    allowed=normalize_verdict(spec.get("allowed", UNKNOWN)),
                    reason=spec.get("reason", ""),
                )
                for name, spec in (item.get("activities") or {}).items()
            }
            platform = Platform(
                id=item["id"],
                name=item.get("name", item["id"]),
                category=item.get("category", ""),
                automation_allowed=normalize_verdict(item.get("automation_allowed", UNKNOWN)),
                sanctioned_channel=item.get("sanctioned_channel", ""),
                source_title=item.get("source_title", ""),
                source_url=item.get("source_url", ""),
                confidence=Confidence(item.get("confidence", "unknown")),
                last_verified=item.get("last_verified"),
                activities=activities,
                risk_notes=list(item.get("risk_notes", [])),
            )
            self._platforms[platform.id] = platform

    # --- registry ------------------------------------------------------------
    @property
    def platforms(self) -> list[Platform]:
        return list(self._platforms.values())

    def get(self, platform_id: str) -> Platform | None:
        return self._platforms.get(platform_id.strip().lower())

    def known(self, platform_id: str) -> bool:
        return self.get(platform_id) is not None

    def track(self, platform: Platform) -> None:
        """Add or update a tracked platform (the 'policy monitoring' surface)."""
        self._platforms[platform.id] = platform

    def mark_verified(self, platform_id: str, when: str | None = None) -> Platform | None:
        p = self.get(platform_id)
        if p is None:
            return None
        p.last_verified = when or now_iso()
        return p

    def verification_queue(self) -> list[Platform]:
        return [p for p in self.platforms if p.needs_verification]

    # --- checks --------------------------------------------------------------
    def check(self, platform_id: str, activity: str) -> TermsCheck:
        platform = self.get(platform_id)
        if platform is None:
            return TermsCheck(
                platform=platform_id,
                activity=activity,
                verdict=UNKNOWN,
                reason=(
                    f"Jarvis has no policy record for '{platform_id}'. Unknown is treated as "
                    "'do not automate' until the current terms are read."
                ),
                safer_alternatives=[
                    f"Read the current terms of {platform_id} and add an entry to the registry.",
                    "Perform the step manually.",
                ],
            )

        rule = platform.rule(activity)
        verified = not platform.needs_verification
        if rule is None:
            return TermsCheck(
                platform=platform.id,
                activity=activity,
                verdict=UNKNOWN,
                reason=(
                    f"No entry for '{activity}' on {platform.name}. Absence of a rule is not "
                    "permission - the general terms still apply."
                ),
                sanctioned_channel=platform.sanctioned_channel,
                source=platform.source_title,
                source_url=platform.source_url,
                verified=verified,
                safer_alternatives=[
                    f"Use the sanctioned channel: {platform.sanctioned_channel}"
                    if platform.sanctioned_channel
                    else "Do it manually.",
                ],
            )

        alternatives: list[str] = []
        if platform.sanctioned_channel and rule.allowed != ALLOWED:
            alternatives.append(f"Use the sanctioned channel: {platform.sanctioned_channel}")
        alternatives.append("Perform the step manually, yourself.")

        return TermsCheck(
            platform=platform.id,
            activity=activity,
            verdict=rule.allowed,
            reason=rule.reason,
            sanctioned_channel=platform.sanctioned_channel,
            source=platform.source_title,
            source_url=platform.source_url,
            verified=verified,
            safer_alternatives=alternatives,
        )

    def evaluate_plan(self, platform_id: str, activities: Sequence[str]) -> PlanVerdict:
        """Check every step. Stops at the first prohibited or unknown one."""
        verdict = PlanVerdict(platform=platform_id, steps=list(activities))
        for activity in activities:
            result = self.check(platform_id, activity)
            verdict.checks.append(result)
            if result.must_stop:
                verdict.must_stop = True
                verdict.blocking = result
                break
        return verdict

    def audit_multiple(self, plans: Iterable[tuple[str, Sequence[str]]]) -> dict[str, PlanVerdict]:
        return {platform: self.evaluate_plan(platform, acts) for platform, acts in plans}

    # --- reporting -----------------------------------------------------------
    def render(self, platform_id: str | None = None) -> str:
        targets = [self.get(platform_id)] if platform_id else self.platforms
        lines = []
        for p in targets:
            if p is None:
                return f"(no platform '{platform_id}' in the registry)"
            lines.append(
                f"{p.id} - {p.name}  [automation: {p.automation_allowed}"
                f"{' - UNVERIFIED' if p.needs_verification else ''}]"
            )
            for name, rule in sorted(p.activities.items()):
                lines.append(f"    {name:<16} {rule.allowed:<12} {rule.reason}")
            for note in p.risk_notes:
                lines.append(f"    ! {note}")
            lines.append(f"    source: {p.source_title} <{p.source_url}>")
            lines.append("")
        return "\n".join(lines).rstrip()

    def stats(self) -> dict[str, Any]:
        total_rules = sum(len(p.activities) for p in self.platforms)
        denied = sum(1 for p in self.platforms for r in p.activities.values() if r.allowed == DENIED)
        return {
            "platforms": len(self.platforms),
            "activity_rules": total_rules,
            "denied_rules": denied,
            "needing_verification": len(self.verification_queue()),
        }


def _load_data() -> dict[str, Any]:
    ref = resources.files("jarvis.compliance.data").joinpath(DATA_FILE)
    return json.loads(ref.read_text(encoding="utf-8"))
