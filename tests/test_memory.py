"""Long-term memory (spec section 8): controls, metadata, and the hard privacy rules."""

from __future__ import annotations

from datetime import timedelta

import pytest

from jarvis.core.confidence import Confidence
from jarvis.core.memory import MemoryCategory, MemoryStore
from jarvis.errors import PolicyViolation
from jarvis.util.clock import utcnow


def test_remember_and_recall(memory):
    record = memory.remember(
        MemoryCategory.USER_PROFILE,
        "Prefers terse answers",
        "Asked for no sugar-coating and direct feedback.",
        tags=["preference", "communication"],
        source="conversation",
        importance=7,
    )
    assert record.id
    hits = memory.search("sugar-coating")
    assert len(hits) == 1
    assert hits[0].title == record.title
    assert hits[0].importance == 7


def test_every_required_metadata_field_is_present(memory):
    record = memory.remember(MemoryCategory.PROJECT, "Jarvis core", "Phase 1 runtime.")
    assert record.created_at and record.updated_at
    assert record.confidence is Confidence.MEDIUM
    assert 0 <= record.importance <= 10
    assert record.category in MemoryCategory.ALL


def test_unknown_category_is_refused(memory):
    with pytest.raises(PolicyViolation):
        memory.remember("horoscope", "title", "content")


def test_secrets_are_refused(memory):
    with pytest.raises(PolicyViolation) as excinfo:
        memory.remember(
            MemoryCategory.KNOWLEDGE,
            "AWS key",
            "The key is AKIAIOSFODNN7EXAMPLE for the prod account",
        )
    assert "secret" in str(excinfo.value).lower()


def test_secrets_are_refused_on_update_too(memory):
    record = memory.remember(MemoryCategory.KNOWLEDGE, "note", "harmless text")
    with pytest.raises(PolicyViolation):
        memory.update(record.id, content="password = 'hunter2secret'")


def test_financial_memory_requires_explicit_authorisation(memory):
    with pytest.raises(PolicyViolation):
        memory.remember(MemoryCategory.FINANCIAL, "Revenue", "Made 4000 USD in June.")
    record = memory.remember(
        MemoryCategory.FINANCIAL, "Revenue", "Made 4000 USD in June.", authorised=True
    )
    assert record.authorisation == "user"


def test_update_changes_content_and_timestamp(memory):
    record = memory.remember(MemoryCategory.PREFERENCE, "Editor", "Uses vim.")
    updated = memory.update(record.id, content="Uses neovim now.", importance=9)
    assert "neovim" in updated.content
    assert updated.importance == 9
    assert updated.updated_at >= record.updated_at


def test_forget_hides_the_record_from_every_read_path(memory):
    record = memory.remember(MemoryCategory.CONVERSATION, "Chit chat", "Talked about cricket.")
    assert memory.search("cricket")
    assert memory.forget(record.id) is True
    assert memory.search("cricket") == []
    assert memory.get(record.id) is None


def test_forget_matching_removes_several(memory):
    memory.remember(MemoryCategory.KNOWLEDGE, "alpha note", "about alpha")
    memory.remember(MemoryCategory.KNOWLEDGE, "alpha other", "also alpha")
    memory.remember(MemoryCategory.KNOWLEDGE, "beta note", "about beta")
    assert memory.forget_matching("alpha") == 2
    assert memory.counts()[MemoryCategory.KNOWLEDGE] == 1


def test_purge_deleted_hard_removes_rows(memory):
    record = memory.remember(MemoryCategory.KNOWLEDGE, "temp", "temporary")
    memory.forget(record.id)
    assert memory.purge_deleted() == 1


def test_disabled_memory_refuses_reads_and_writes(memory):
    memory.remember(MemoryCategory.KNOWLEDGE, "before", "stored while enabled")
    memory.disable()
    with pytest.raises(PolicyViolation):
        memory.remember(MemoryCategory.KNOWLEDGE, "after", "should not be stored")
    with pytest.raises(PolicyViolation):
        memory.search("before")
    memory.enable()
    assert memory.search("before"), "disabling must not delete anything"


def test_export_writes_readable_json(memory, tmp_path):
    memory.remember(MemoryCategory.PROJECT, "Jarvis", "Autonomous agent.")
    destination = tmp_path / "export" / "memory.json"
    path = memory.export(destination)
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["record_count"] == 1
    assert payload["records"][0]["title"] == "Jarvis"


def test_expired_records_are_hidden_by_default(memory):
    past = (utcnow() - timedelta(days=1)).isoformat(timespec="seconds")
    memory.remember(MemoryCategory.TASK, "Old task", "Should be expired.", expires_at=past)
    assert memory.search("expired") == []
    assert memory.search("expired", include_expired=True)
    assert memory.stale()


def test_review_due_records_are_surfaced(memory):
    past = (utcnow() - timedelta(days=1)).isoformat(timespec="seconds")
    memory.remember(MemoryCategory.KNOWLEDGE, "Legal note", "Old rule.", review_at=past)
    assert any(r.title == "Legal note" for r in memory.stale())


def test_search_ranks_title_matches_above_body_matches(memory):
    memory.remember(MemoryCategory.KNOWLEDGE, "Unrelated", "mentions deploy in passing")
    memory.remember(MemoryCategory.KNOWLEDGE, "Deploy process", "about something else")
    hits = memory.search("deploy")
    assert hits[0].title == "Deploy process"


def test_counts_cover_every_category(memory):
    counts = memory.counts()
    assert set(counts) == set(MemoryCategory.ALL) | {"total"}
    assert counts["total"] == 0


def test_render_reports_an_empty_store(memory):
    assert "empty" in memory.render()


def test_store_reopens_with_data_intact(settings):
    settings.ensure_dirs()
    first = MemoryStore(settings.memory_db)
    first.remember(MemoryCategory.PROJECT, "Persisted", "survives a restart")
    first.close()
    second = MemoryStore(settings.memory_db)
    assert second.search("survives")
    second.close()
