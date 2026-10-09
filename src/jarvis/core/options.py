"""Structured option generation (spec section 7).

When Jarvis hits uncertainty it must not silently pick a path. It builds a
:class:`Dilemma`: what happened, why it cannot safely continue, what could go
wrong, and a set of comparable options -- each with expected result, risk, cost,
time and compliance status -- then asks the user to choose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jarvis.core.risk import RiskLevel
from jarvis.util.clock import now_iso
from jarvis.util.ids import new_id

#: The standard shapes of option, per the spec. Extra options are allowed as long
#: as one safe and one "cancel" option are always present.
OPTION_KINDS = ("safe", "fast", "profitable", "conservative", "cancel")


@dataclass
class Option:
    label: str  # "A", "B", ...
    title: str
    kind: str = "safe"
    expected_result: str = ""
    risk: RiskLevel = RiskLevel.LOW
    cost: str = "none"
    time: str = ""
    compliance: str = "compliant"
    notes: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id("opt"))

    @property
    def blocked(self) -> bool:
        return self.compliance.lower() not in {"compliant", "compliant with conditions"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "title": self.title,
            "kind": self.kind,
            "expected_result": self.expected_result,
            "risk": self.risk.value,
            "cost": self.cost,
            "time": self.time,
            "compliance": self.compliance,
            "notes": list(self.notes),
        }

    def render(self) -> str:
        lines = [
            f"{self.label}. {self.title}",
            f"   expected : {self.expected_result}",
            f"   risk     : {self.risk.value}    cost: {self.cost}    time: {self.time}",
            f"   compliance: {self.compliance}",
        ]
        lines.extend(f"   - {n}" for n in self.notes)
        return "\n".join(lines)


@dataclass
class Dilemma:
    """A decision the agent is handing back to the user."""

    situation: str
    problem: str
    risk: str
    options: list[Option] = field(default_factory=list)
    recommendation: str = ""
    id: str = field(default_factory=lambda: new_id("dil"))
    at: str = field(default_factory=now_iso)

    def add(self, option: Option) -> Dilemma:
        if not option.label:
            option.label = chr(ord("A") + len(self.options))
        self.options.append(option)
        return self

    def cancel_option(self, note: str = "Do nothing; leave the current state untouched.") -> Dilemma:
        """Every dilemma must offer a way out that changes nothing."""
        if not any(o.kind == "cancel" for o in self.options):
            self.add(
                Option(
                    label="",
                    title="Cancel",
                    kind="cancel",
                    expected_result="Nothing changes.",
                    risk=RiskLevel.LOW,
                    cost="none",
                    time="immediate",
                    compliance="compliant",
                    notes=[note],
                )
            )
        return self

    def validate(self) -> list[str]:
        """Guards against a malformed dilemma reaching the user."""
        problems = []
        if not self.situation.strip():
            problems.append("missing situation")
        if not self.problem.strip():
            problems.append("missing problem")
        if not self.risk.strip():
            problems.append("missing risk")
        if len(self.options) < 2:
            problems.append("fewer than two options")
        if not any(o.kind == "cancel" for o in self.options):
            problems.append("no cancel option")
        if not any(o.risk.rank <= RiskLevel.LOW.rank for o in self.options):
            problems.append("no low-risk option")
        labels = [o.label for o in self.options]
        if len(set(labels)) != len(labels):
            problems.append("duplicate option labels")
        return problems

    def recommended(self) -> Option | None:
        if not self.recommendation:
            return None
        for o in self.options:
            if o.label.lower() == self.recommendation.lower():
                return o
        return None

    def render(self) -> str:
        parts = [
            "SITUATION",
            f"  {self.situation}",
            "",
            "PROBLEM",
            f"  {self.problem}",
            "",
            "RISK",
            f"  {self.risk}",
            "",
            "OPTIONS",
        ]
        parts.extend("  " + o.render().replace("\n", "\n  ") for o in self.options)
        if self.recommendation:
            parts.append("")
            parts.append(f"RECOMMENDED: {self.recommendation}")
        return "\n".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "at": self.at,
            "situation": self.situation,
            "problem": self.problem,
            "risk": self.risk,
            "options": [o.as_dict() for o in self.options],
            "recommendation": self.recommendation,
            "valid": not self.validate(),
        }


def failure_dilemma(
    *,
    task: str,
    cause: str,
    safe: Option | None = None,
    alternatives: list[Option] | None = None,
) -> Dilemma:
    """The section-34 failure template: tell the user what broke and what's next.

    Example from the spec -- a payment integration failing for missing
    credentials yields (A) configure securely, (B) sandbox mode, (C) skip.
    """
    dilemma = Dilemma(
        situation=f"Task '{task}' stopped.",
        problem=f"Cause: {cause}",
        risk="Continuing blindly could produce an incomplete or incorrect result.",
    )
    if safe:
        dilemma.add(safe)
    for opt in alternatives or []:
        dilemma.add(opt)
    return dilemma.cancel_option()
