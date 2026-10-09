"""Compliance layer (spec sections 4 and 5)."""

from __future__ import annotations

from jarvis.compliance.jurisdiction import (
    DISCLAIMER,
    JurisdictionKnowledge,
    Regulation,
    related_jurisdictions,
)
from jarvis.compliance.terms import (
    ALLOWED,
    CONDITIONAL,
    DENIED,
    TermsRegistry,
    normalize_verdict,
)
from jarvis.core.confidence import Authority


# ------------------------------------------------------------- jurisdiction
def test_seed_data_loads_for_every_listed_market():
    kb = JurisdictionKnowledge()
    covered = {r.jurisdiction for r in kb.entries}
    assert {"IN", "US", "GB", "EU", "AU", "SG", "AE", "JP", "KR", "CA"} <= covered


def test_every_entry_has_source_jurisdiction_dates_and_confidence():
    for reg in JurisdictionKnowledge().entries:
        assert reg.jurisdiction, reg.id
        assert reg.source_title and reg.source_url, reg.id
        assert reg.published, reg.id
        assert reg.confidence.value in {"high", "medium", "low", "unknown"}, reg.id
        assert reg.authority is not Authority.UNVERIFIED, reg.id
        assert reg.applies_to, reg.id


def test_unverified_entries_are_flagged_rather_than_trusted():
    kb = JurisdictionKnowledge()
    assert kb.verification_queue(), "seed data should be flagged for verification"
    for reg in kb.verification_queue():
        assert "UNVERIFIED" in reg.status_label


def test_marking_an_entry_verified_clears_the_flag():
    kb = JurisdictionKnowledge()
    entry = kb.entries[0]
    assert entry.needs_verification
    kb.mark_verified(entry.id)
    assert not kb.get(entry.id).needs_verification


def test_state_law_requests_also_return_federal_entries():
    kb = JurisdictionKnowledge()
    found = kb.lookup(["US-CA"], ["data_privacy", "consumer_protection"])
    jurisdictions = {r.jurisdiction for r in found}
    assert "US-CA" in jurisdictions
    assert "US" in jurisdictions, "state law sits on top of federal law"


def test_primary_sources_sort_first():
    kb = JurisdictionKnowledge()
    found = kb.lookup(["US"], ["consumer_protection", "communication"])
    weights = [r.authority.weight for r in found]
    assert weights == sorted(weights, reverse=True)


def test_brief_always_carries_the_disclaimer():
    brief = JurisdictionKnowledge().brief("selling online", ["IN", "US"])
    assert brief.disclaimer == DISCLAIMER
    assert DISCLAIMER in brief.render()


def test_brief_for_an_unknown_jurisdiction_says_it_does_not_know():
    text = JurisdictionKnowledge().brief("anything", ["ZZ"]).render()
    assert "not mean it is unregulated" in text
    assert "will not guess" in text


def test_missing_topics_are_reported_as_gaps():
    brief = JurisdictionKnowledge().brief("x", ["IN"], ["employment"])
    assert "employment" in brief.missing_coverage


def test_related_jurisdictions_expands_regions():
    assert related_jurisdictions(["US-CA", "IN"]) == ["US-CA", "US", "IN"]
    assert related_jurisdictions([]) == []


def test_regulation_can_be_added_at_runtime():
    kb = JurisdictionKnowledge()
    before = len(kb.entries)
    kb.add(
        Regulation(
            id="XX-TEST", jurisdiction="XX", topic="tax", title="Test rule",
            summary="A rule added at runtime.", authority=Authority.PRIMARY,
            source_title="Test", source_url="https://example.gov/test",
        )
    )
    assert len(kb.entries) == before + 1
    assert kb.get("XX-TEST").needs_verification


# ------------------------------------------------------------------- terms
def test_seed_data_covers_the_platforms_named_in_the_spec():
    registry = TermsRegistry()
    ids = {p.id for p in registry.platforms}
    assert {"whatsapp", "youtube", "instagram", "telegram", "google", "microsoft",
            "stripe", "paypal", "upwork", "amazon", "linkedin"} <= ids


def test_verdict_normalisation_handles_booleans_and_words():
    assert normalize_verdict(True) == ALLOWED
    assert normalize_verdict(False) == DENIED
    assert normalize_verdict("yes") == ALLOWED
    assert normalize_verdict("no") == DENIED
    assert normalize_verdict("conditional") == CONDITIONAL
    assert normalize_verdict("prohibited") == DENIED
    assert normalize_verdict("nonsense") == "unknown"
    assert normalize_verdict(None) == "unknown"


def test_denied_rules_survive_loading():
    """A regression guard: JSON booleans used to load as the string 'false'
    and every prohibition silently became 'unknown'."""
    stats = TermsRegistry().stats()
    assert stats["denied_rules"] >= 10


def test_whatsapp_ui_automation_is_denied_with_a_safer_route():
    check = TermsRegistry().check("whatsapp", "ui_automation")
    assert check.verdict == DENIED
    assert check.must_stop
    assert check.sanctioned_channel
    assert check.safer_alternatives


def test_telegram_bot_api_is_allowed():
    assert TermsRegistry().check("telegram", "api_automation").verdict == ALLOWED


def test_an_unknown_platform_is_unknown_not_allowed():
    check = TermsRegistry().check("made-up-platform", "scrape_public")
    assert check.verdict == "unknown"
    assert check.must_stop


def test_an_unlisted_activity_is_unknown_not_allowed():
    check = TermsRegistry().check("whatsapp", "launch_a_rocket")
    assert check.verdict == "unknown"
    assert check.must_stop


def test_evaluate_plan_stops_at_the_first_conflict():
    verdict = TermsRegistry().evaluate_plan(
        "instagram", ["api_automation", "ui_automation", "scrape_public"]
    )
    assert verdict.must_stop
    assert verdict.blocking.activity == "ui_automation"
    # Later steps are never evaluated, because the plan is already stopped.
    assert [c.activity for c in verdict.checks] == ["api_automation", "ui_automation"]


def test_a_clean_plan_reports_conditionals_without_stopping():
    verdict = TermsRegistry().evaluate_plan("telegram", ["api_automation", "send_message"])
    assert not verdict.must_stop
    assert "No conflict found" in verdict.render()


def test_stop_rendering_explains_the_rule_and_the_alternative():
    text = TermsRegistry().check("linkedin", "ui_automation").render()
    assert "DENIED" in text
    assert "safer alternative" in text


def test_platform_entries_require_verification():
    registry = TermsRegistry()
    assert len(registry.verification_queue()) == len(registry.platforms)
    registry.mark_verified("telegram")
    assert registry.get("telegram").needs_verification is False


def test_new_platform_can_be_tracked():
    from jarvis.compliance.terms import ActivityRule, Platform

    registry = TermsRegistry()
    registry.track(
        Platform(
            id="example", name="Example", automation_allowed=DENIED,
            activities={"api_automation": ActivityRule("api_automation", DENIED, "not allowed")},
        )
    )
    assert registry.check("example", "api_automation").verdict == DENIED


def test_render_lists_activities_and_risk_notes():
    text = TermsRegistry().render("whatsapp")
    assert "ui_automation" in text
    assert "source:" in text
