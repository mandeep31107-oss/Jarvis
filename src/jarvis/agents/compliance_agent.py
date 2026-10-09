"""Compliance agent (spec section 24, backed by sections 4 and 5)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from jarvis.agents.base import Agent, AgentRequest, AgentResult
from jarvis.compliance.jurisdiction import DISCLAIMER, JurisdictionKnowledge, related_jurisdictions
from jarvis.compliance.terms import TermsRegistry
from jarvis.core.confidence import Claim, Confidence


class ComplianceAgent(Agent):
    name = "compliance"
    description = "Checks laws, regulations and platform terms; stops plans that conflict."
    capabilities = ("compliance", "legal_research", "terms_check", "jurisdiction")

    def __init__(
        self,
        runtime: Any = None,
        *,
        jurisdictions: JurisdictionKnowledge | None = None,
        terms: TermsRegistry | None = None,
        default_jurisdiction: str = "IN",
    ) -> None:
        super().__init__(runtime)
        self.jurisdictions = jurisdictions or JurisdictionKnowledge()
        self.terms = terms or TermsRegistry()
        self.default_jurisdiction = default_jurisdiction

    def run(self, request: AgentRequest) -> AgentResult:
        markets = request.param("markets") or [self.default_jurisdiction]
        jurisdictions = related_jurisdictions(markets)
        topics = request.param("topics") or ["data_privacy", "tax", "consumer_protection"]
        platforms: Sequence[str] = request.param("platforms") or []
        activities: Sequence[str] = request.param("activities") or ["api_automation"]
        activity = request.param("activity") or request.text or "the described activity"

        result = AgentResult(agent=self.name, summary=f"Compliance check: {activity}")
        result.say(f"looking up {len(jurisdictions)} jurisdiction(s)")
        brief = self.jurisdictions.brief(activity, jurisdictions, topics)
        result.data["brief"] = brief.as_dict()

        result.add_claim(
            Claim(
                statement=(
                    f"{len(brief.entries)} regulation entries apply to '{activity}' in "
                    f"{', '.join(jurisdictions)}."
                ),
                confidence=Confidence.MEDIUM if brief.entries else Confidence.LOW,
                caveats=(
                    [f"{len(brief.unverified)} entries unverified"] if brief.unverified else []
                )
                + ([f"no entries for: {', '.join(brief.missing_coverage)}"] if brief.missing_coverage else []),
            )
        )

        blocked: list[str] = []
        for platform in platforms:
            verdict = self.terms.evaluate_plan(platform, activities)
            result.say(f"checking platform policy for {platform}")
            result.data[f"terms:{platform}"] = verdict.as_dict()
            if verdict.must_stop and verdict.blocking:
                blocked.append(
                    f"{platform}/{verdict.blocking.activity} -> {verdict.blocking.verdict.upper()}: "
                    f"{verdict.blocking.reason}"
                )

        if blocked:
            result.ok = False
            result.summary = "STOP. A required step conflicts with a platform policy."
            result.add_claim(
                Claim(
                    statement="; ".join(blocked),
                    confidence=Confidence.HIGH,
                    caveats=["policy entries are unverified - re-read the current terms"],
                )
            )
            for platform in platforms:
                p = self.terms.get(platform)
                if p and p.sanctioned_channel:
                    result.follow_ups.append(f"{platform}: use {p.sanctioned_channel} instead.")
            result.follow_ups.append("Or perform that step manually.")
        else:
            result.summary = (
                f"{len(brief.entries)} regulation entries reviewed, no platform conflict found."
            )
            result.follow_ups.append("Close the unverified entries before acting on them.")

        result.follow_ups.append(DISCLAIMER)
        result.data["disclaimer"] = DISCLAIMER
        return result

    # ------------------------------------------------------------------ helpers
    def brief_text(self, activity: str, markets: Sequence[str] | None = None) -> str:
        return self.jurisdictions.brief(
            activity, related_jurisdictions(markets or [self.default_jurisdiction])
        ).render()

    def terms_text(self, platform: str, activities: Sequence[str]) -> str:
        return self.terms.evaluate_plan(platform, list(activities)).render()

    def verification_queue(self) -> dict[str, Any]:
        return {
            "regulations": [r.id for r in self.jurisdictions.verification_queue()],
            "platforms": [p.id for p in self.terms.verification_queue()],
            "note": "Entries Jarvis has never confirmed against their primary source.",
        }
