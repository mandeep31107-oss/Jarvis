"""Controlled learning pipeline (spec section 9): nothing becomes trusted by arriving."""

from __future__ import annotations

from datetime import timedelta

from jarvis.core.confidence import Authority, Confidence, Source
from jarvis.core.knowledge import Candidate, Status, _contradicts
from jarvis.util.clock import now_iso, utcnow


def _source(authority=Authority.PRIMARY, days_old=30, url="https://example.gov/rule") -> Source:
    published = (utcnow() - timedelta(days=days_old)).isoformat(timespec="seconds")
    return Source(
        title="Primary source", url=url, authority=authority, published=published, retrieved=now_iso()
    )


def test_a_well_sourced_claim_is_accepted(knowledge):
    report = knowledge.ingest(
        Candidate(statement="Registration is required above the threshold.",
                  topic="tax", source=_source())
    )
    assert report.accepted
    assert [s.stage for s in report.stages] == [
        "source_validation", "duplicate_check", "authority_check", "date_check",
        "conflict_detection", "summarisation", "confidence_score", "test_simulation",
    ]
    assert report.item.status is Status.TRUSTED


def test_unsourced_information_is_rejected(knowledge):
    report = knowledge.ingest(Candidate(statement="Something someone said.", source=None))
    assert not report.accepted
    assert report.failed_stage == "source_validation"


def test_unverified_authority_is_rejected(knowledge):
    report = knowledge.ingest(
        Candidate(statement="A forum claim.", source=_source(authority=Authority.UNVERIFIED))
    )
    assert not report.accepted
    assert report.failed_stage == "authority_check"


def test_stale_source_is_rejected(knowledge):
    report = knowledge.ingest(
        Candidate(statement="An old rule.", source=_source(days_old=2000))
    )
    assert not report.accepted
    assert report.failed_stage == "date_check"


def test_source_published_after_retrieval_is_rejected(knowledge):
    """Impossible metadata means the citation cannot be trusted."""
    future = (utcnow() + timedelta(days=5)).isoformat(timespec="seconds")
    bad = Source(title="Odd", url="https://example.gov/x", authority=Authority.PRIMARY,
                 published=future, retrieved=now_iso())
    report = knowledge.ingest(Candidate(statement="A claim.", source=bad))
    assert report.failed_stage == "date_check"


def test_duplicate_is_not_stored_twice(knowledge):
    first = Candidate(statement="Identical statement.", topic="tax", source=_source())
    second = Candidate(statement="identical statement.", topic="tax", source=_source())
    assert knowledge.ingest(first).accepted
    report = knowledge.ingest(second)
    assert not report.accepted
    assert report.failed_stage == "duplicate_check"


def test_conflicting_claim_is_quarantined_not_accepted(knowledge):
    knowledge.ingest(
        Candidate(statement="Automation is allowed on this platform.", topic="terms",
                  key="terms.auto", source=_source())
    )
    report = knowledge.ingest(
        Candidate(statement="Automation is not allowed on this platform.", topic="terms",
                  key="terms.auto", source=_source())
    )
    assert not report.accepted
    assert report.failed_stage == "conflict_detection"
    assert report.item.status is Status.QUARANTINED
    # The original trusted item is untouched.
    trusted = knowledge.query("Automation", status=Status.TRUSTED)
    assert len(trusted) == 1
    assert "allowed" in trusted[0].statement


def test_a_failing_verifier_blocks_acceptance(knowledge):
    report = knowledge.ingest(
        Candidate(statement="A claim with a broken check.", source=_source(),
                  verifier=lambda item: False)
    )
    assert not report.accepted
    assert report.failed_stage == "test_simulation"


def test_a_verifier_that_raises_is_treated_as_failure(knowledge):
    def boom(item):
        raise RuntimeError("checker exploded")

    report = knowledge.ingest(
        Candidate(statement="A claim with a crashing check.", source=_source(), verifier=boom)
    )
    assert not report.accepted
    assert "checker exploded" in report.reason


def test_no_verifier_caps_confidence_at_medium(knowledge):
    report = knowledge.ingest(Candidate(statement="No check supplied.", source=_source()))
    assert report.accepted
    assert report.item.confidence is not Confidence.HIGH


def test_a_passing_verifier_allows_high_confidence(knowledge):
    report = knowledge.ingest(
        Candidate(statement="A verified claim.", source=_source(), verifier=lambda item: True)
    )
    assert report.accepted
    assert report.item.confidence in (Confidence.HIGH, Confidence.MEDIUM)


def test_rejections_are_recorded_so_the_same_input_is_not_reprocessed(knowledge):
    knowledge.ingest(Candidate(statement="Bad input.", source=None))
    assert knowledge.stats().get("rejected", 0) == 1


def test_needs_revalidation_flags_old_entries(knowledge):
    knowledge.ingest(
        Candidate(statement="An old but valid entry.", source=_source(days_old=400))
    )
    assert knowledge.needs_revalidation(days=365)


def test_query_filters_by_jurisdiction(knowledge):
    knowledge.ingest(
        Candidate(statement="India specific rule.", topic="tax", jurisdiction="IN", source=_source())
    )
    assert knowledge.query("India", jurisdiction="IN")
    assert knowledge.query("India", jurisdiction="JP") == []


def test_contradiction_detector_only_fires_on_negation():
    assert _contradicts("Automation is allowed here.", "Automation is not allowed here.")
    assert not _contradicts("Automation is allowed here.", "Registration costs money.")
    assert not _contradicts("", "anything")


def test_render_reports_an_empty_base(knowledge):
    assert "no trusted entries" in knowledge.render()


def test_report_is_serialisable(knowledge):
    report = knowledge.ingest(Candidate(statement="x", source=_source()))
    assert isinstance(report.as_dict()["stages"], list)
