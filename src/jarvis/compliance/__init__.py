"""Compliance subsystem: jurisdiction knowledge + platform terms intelligence."""

from jarvis.compliance.jurisdiction import (
    DISCLAIMER,
    ComplianceBrief,
    JurisdictionKnowledge,
    Regulation,
    related_jurisdictions,
)
from jarvis.compliance.terms import (
    ActivityRule,
    PlanVerdict,
    Platform,
    TermsCheck,
    TermsRegistry,
)

__all__ = [
    "DISCLAIMER",
    "ActivityRule",
    "ComplianceBrief",
    "JurisdictionKnowledge",
    "PlanVerdict",
    "Platform",
    "Regulation",
    "TermsCheck",
    "TermsRegistry",
    "related_jurisdictions",
]
