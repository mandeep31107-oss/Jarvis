"""Controlled learning pipeline (spec section 9).

New information does **not** become trusted knowledge by arriving. It has to
pass, in order:

    NEW DATA -> SOURCE VALIDATION -> DUPLICATE CHECK -> AUTHORITY CHECK ->
    DATE CHECK -> CONFLICT DETECTION -> SUMMARIZATION -> CONFIDENCE SCORE ->
    TEST/SIMULATION -> KNOWLEDGE BASE UPDATE

Anything that fails a gate is recorded as ``rejected`` (or ``quarantined`` when
it conflicts with something already trusted) with the reason, so the agent can
learn quickly without being poisoned.
"""

from __future__ import annotations

import enum
import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.core.confidence import Authority, Confidence, Source
from jarvis.util.clock import age_days, now_iso
from jarvis.util.ids import new_id
from jarvis.util.store import connect_sqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge (
    id            TEXT PRIMARY KEY,
    topic         TEXT NOT NULL,
    key           TEXT NOT NULL,
    statement     TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    jurisdiction  TEXT NOT NULL DEFAULT '',
    authority     TEXT NOT NULL DEFAULT 'unverified',
    confidence    TEXT NOT NULL DEFAULT 'unknown',
    status        TEXT NOT NULL DEFAULT 'candidate',
    source_title  TEXT NOT NULL DEFAULT '',
    source_url    TEXT NOT NULL DEFAULT '',
    published     TEXT,
    retrieved     TEXT NOT NULL,
    effective     TEXT,
    version       TEXT NOT NULL DEFAULT '',
    rejection     TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    verified_at   TEXT,
    review_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_key    ON knowledge(key, status);
CREATE INDEX IF NOT EXISTS idx_knowledge_topic  ON knowledge(topic);
"""


class Status(str, enum.Enum):
    CANDIDATE = "candidate"
    TRUSTED = "trusted"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"  # conflicts with something already trusted
    STALE = "stale"


@dataclass
class Candidate:
    """A piece of new information waiting to be judged."""

    statement: str
    topic: str = ""
    key: str = ""
    jurisdiction: str = ""
    source: Source | None = None
    effective: str | None = None
    version: str = ""
    #: Optional executable check that must pass before the item is trusted.
    verifier: Callable[[KnowledgeItem], bool] | None = None
    id: str = field(default_factory=lambda: new_id("kb"))

    @property
    def url(self) -> str:
        return self.source.url if self.source else ""

    def fingerprint(self) -> str:
        basis = f"{self.topic.lower()}|{self.statement.lower().strip()}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


@dataclass
class KnowledgeItem:
    id: str
    topic: str
    key: str
    statement: str
    summary: str = ""
    jurisdiction: str = ""
    authority: Authority = Authority.UNVERIFIED
    confidence: Confidence = Confidence.UNKNOWN
    status: Status = Status.CANDIDATE
    source: Source | None = None
    published: str | None = None
    effective: str | None = None
    version: str = ""
    rejection: str = ""
    created_at: str = now_iso()
    verified_at: str | None = None
    review_at: str | None = None

    @property
    def age_days(self) -> float | None:
        return age_days(self.published or self.created_at)

    def is_stale(self, max_days: float = 730.0) -> bool:
        age = self.age_days
        return age is not None and age > max_days

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "topic": self.topic,
            "key": self.key,
            "statement": self.statement,
            "summary": self.summary,
            "jurisdiction": self.jurisdiction,
            "authority": self.authority.value,
            "confidence": self.confidence.value,
            "status": self.status.value,
            "source": self.source.as_dict() if self.source else None,
            "published": self.published,
            "effective": self.effective,
            "version": self.version,
            "rejection": self.rejection,
            "created_at": self.created_at,
            "verified_at": self.verified_at,
            "review_at": self.review_at,
            "age_days": self.age_days,
        }


@dataclass
class StageResult:
    stage: str
    passed: bool
    reason: str = ""


@dataclass
class IngestReport:
    candidate: Candidate
    item: KnowledgeItem | None
    stages: list[StageResult] = field(default_factory=list)
    accepted: bool = False
    reason: str = ""

    @property
    def failed_stage(self) -> str | None:
        for s in self.stages:
            if not s.passed:
                return s.stage
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "statement": self.candidate.statement,
            "accepted": self.accepted,
            "reason": self.reason,
            "failed_stage": self.failed_stage,
            "stages": [{"stage": s.stage, "passed": s.passed, "reason": s.reason} for s in self.stages],
            "item": self.item.as_dict() if self.item else None,
        }


#: Schemes accepted as a source locator. ``file:`` is included because a
#: document the user hands over is a legitimate source and the user can check
#: it. ``data:`` and ``javascript:`` are not: neither is retrievable later, so
#: neither can be verified, and neither should be recorded as a source.
_SOURCE_SCHEMES = ("http://", "https://", "file:///")


def _looks_like_url(value: str) -> bool:
    """True when ``value`` is an absolute, checkable locator.

    A bare path such as ``/tmp/page.html`` is rejected: it is not a URI, it says
    nothing about which machine it refers to, and it cannot be re-checked.
    """
    text = (value or "").strip()
    if "\n" in text or "\r" in text:
        return False
    return any(text.startswith(scheme) and len(text) > len(scheme) for scheme in _SOURCE_SCHEMES)


class KnowledgeBase:
    """The pipeline plus the store it writes into."""

    #: How old a time-sensitive item may be before the DATE CHECK rejects it.
    MAX_AGE_DAYS = 730.0
    #: Below this source authority an item can be stored but never marked trusted.
    MIN_TRUST_AUTHORITY = Authority.OFFICIAL_SECONDARY

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._conn = connect_sqlite(self.path)
        self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------ stages
    def _stage_source_validation(self, c: Candidate) -> StageResult:
        if not c.statement or not c.statement.strip():
            return StageResult("source_validation", False, "empty statement")
        if c.source is None:
            return StageResult("source_validation", False, "no source attributed")
        if not c.source.title.strip():
            return StageResult("source_validation", False, "source has no title")
        if c.source.url and not _looks_like_url(c.source.url):
            return StageResult("source_validation", False, f"malformed source URL: {c.source.url!r}")
        if not c.source.url:
            return StageResult(
                "source_validation",
                c.source.authority is Authority.PRIMARY,
                "no URL given; only primary sources may be unlinked",
            )
        return StageResult("source_validation", True, "source attributable and reachable-looking")

    def _stage_duplicate(self, c: Candidate) -> StageResult:
        fp = c.fingerprint()
        row = self._conn.execute(
            "SELECT id, status FROM knowledge WHERE key=? AND status IN ('trusted','candidate')",
            (fp,),
        ).fetchone()
        if row:
            return StageResult("duplicate_check", False, f"duplicate of {row['id']} ({row['status']})")
        return StageResult("duplicate_check", True, "no existing entry")

    def _stage_authority(self, c: Candidate) -> StageResult:
        assert c.source is not None
        if c.source.authority is Authority.UNVERIFIED:
            return StageResult(
                "authority_check", False, "source is unverified - cannot enter the knowledge base"
            )
        return StageResult("authority_check", True, f"authority={c.source.authority.value}")

    def _stage_date(self, c: Candidate) -> StageResult:
        assert c.source is not None
        reference = c.source.published or c.source.retrieved
        age = age_days(reference)
        if age is None:
            return StageResult("date_check", False, "no publication or retrieval date")
        if age > self.MAX_AGE_DAYS:
            return StageResult(
                "date_check", False, f"source is {age:.0f} days old (limit {self.MAX_AGE_DAYS:.0f})"
            )
        if c.source.published and age_days(c.source.retrieved) is not None:
            # Published after we supposedly retrieved it: the metadata is wrong.
            from jarvis.util.clock import parse_iso

            pub, ret = parse_iso(c.source.published), parse_iso(c.source.retrieved)
            if pub and ret and pub > ret:
                return StageResult("date_check", False, "published date is after retrieval date")
        return StageResult("date_check", True, f"source is {age:.0f} days old")

    def _stage_conflict(self, c: Candidate, item: KnowledgeItem) -> StageResult:
        """A candidate that contradicts a trusted item on the same key is quarantined,
        not silently accepted and not silently dropped."""
        rows = self._conn.execute(
            "SELECT id, statement FROM knowledge WHERE key=? AND status='trusted'", (item.key,)
        ).fetchall()
        for row in rows:
            if _contradicts(row["statement"], c.statement):
                item.status = Status.QUARANTINED
                item.rejection = f"conflicts with trusted item {row['id']}"
                return StageResult(
                    "conflict_detection", False, f"conflicts with trusted item {row['id']}"
                )
        return StageResult("conflict_detection", True, "no contradiction with trusted items")

    def _stage_summary(self, c: Candidate, item: KnowledgeItem) -> StageResult:
        item.summary = _summarise(c.statement)
        return StageResult("summarisation", bool(item.summary), "summary generated")

    def _stage_confidence(self, c: Candidate, item: KnowledgeItem) -> StageResult:
        assert c.source is not None
        base = c.source.authority.weight
        freshness = max(0.0, 1.0 - (item.age_days or 0.0) / self.MAX_AGE_DAYS)
        score = base * (0.6 + 0.4 * freshness)
        if c.jurisdiction:
            score *= 1.0  # jurisdiction-scoped statements are not discounted
        else:
            score *= 0.85  # unscoped legal/market claims are less useful
        item.confidence = Confidence.from_score(score)
        return StageResult("confidence_score", True, f"confidence={item.confidence.value}")

    def _stage_verification(self, c: Candidate, item: KnowledgeItem) -> StageResult:
        if c.verifier is None:
            # No test available: cap the item at MEDIUM so it cannot masquerade
            # as verified fact.
            if item.confidence is Confidence.HIGH:
                item.confidence = Confidence.MEDIUM
            return StageResult("test_simulation", True, "no verifier supplied; confidence capped at medium")
        try:
            ok = bool(c.verifier(item))
        except Exception as exc:  # noqa: BLE001 - a broken check must not be trusted
            return StageResult("test_simulation", False, f"verifier raised: {exc}")
        return StageResult("test_simulation", ok, "verifier passed" if ok else "verifier failed")

    # ------------------------------------------------------------------ ingest
    def ingest(self, candidate: Candidate) -> IngestReport:
        report = IngestReport(candidate=candidate, item=None)

        report.stages.append(self._stage_source_validation(candidate))
        if not report.stages[-1].passed:
            report.reason = report.stages[-1].reason
            self._persist_rejected(candidate, report)
            return report

        report.stages.append(self._stage_duplicate(candidate))
        if not report.stages[-1].passed:
            report.reason = report.stages[-1].reason
            return report

        report.stages.append(self._stage_authority(candidate))
        if not report.stages[-1].passed:
            report.reason = report.stages[-1].reason
            self._persist_rejected(candidate, report)
            return report

        report.stages.append(self._stage_date(candidate))
        if not report.stages[-1].passed:
            report.reason = report.stages[-1].reason
            self._persist_rejected(candidate, report)
            return report

        assert candidate.source is not None
        item = KnowledgeItem(
            id=candidate.id,
            topic=candidate.topic or _topic_from(candidate.statement),
            key=candidate.key or candidate.fingerprint(),
            statement=candidate.statement.strip(),
            jurisdiction=candidate.jurisdiction,
            authority=candidate.source.authority,
            source=candidate.source,
            published=candidate.source.published,
            effective=candidate.effective,
            version=candidate.version,
        )
        report.item = item

        conflict = self._stage_conflict(candidate, item)
        report.stages.append(conflict)
        if not conflict.passed:
            report.reason = conflict.reason
            self._persist(item)  # kept, but quarantined - visible for review
            return report

        report.stages.append(self._stage_summary(candidate, item))
        report.stages.append(self._stage_confidence(candidate, item))
        verification = self._stage_verification(candidate, item)
        report.stages.append(verification)
        if not verification.passed:
            item.rejection = verification.reason
            report.reason = verification.reason
            self._persist_rejected(candidate, report, item=item)
            return report

        item.status = Status.TRUSTED
        item.verified_at = now_iso()
        item.review_at = _review_date(item)
        self._persist(item)
        report.accepted = True
        report.reason = f"accepted as {item.confidence.value} confidence"
        return report

    # ------------------------------------------------------------------ store
    def _persist(self, item: KnowledgeItem) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO knowledge (id, topic, key, statement, summary,
                       jurisdiction, authority, confidence, status, source_title, source_url,
                       published, retrieved, effective, version, rejection, created_at,
                       verified_at, review_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    item.id, item.topic, item.key, item.statement, item.summary,
                    item.jurisdiction, item.authority.value, item.confidence.value,
                    item.status.value,
                    item.source.title if item.source else "",
                    item.source.url if item.source else "",
                    item.published,
                    item.source.retrieved if item.source else now_iso(),
                    item.effective, item.version, item.rejection, item.created_at,
                    item.verified_at, item.review_at,
                ),
            )

    def _persist_rejected(
        self, candidate: Candidate, report: IngestReport, *, item: KnowledgeItem | None = None
    ) -> None:
        """Rejections are stored too, so the same bad input is not re-processed
        forever and so the user can see what the filter blocked."""
        record = item or KnowledgeItem(
            id=candidate.id,
            topic=candidate.topic or _topic_from(candidate.statement),
            key=candidate.key or candidate.fingerprint(),
            statement=candidate.statement.strip(),
            jurisdiction=candidate.jurisdiction,
            authority=candidate.source.authority if candidate.source else Authority.UNVERIFIED,
            source=candidate.source,
            published=candidate.source.published if candidate.source else None,
            version=candidate.version,
        )
        record.status = Status.REJECTED
        record.rejection = f"{report.failed_stage}: {report.reason}"
        self._persist(record)
        report.item = record

    # ------------------------------------------------------------------ query
    def _row(self, row: sqlite3.Row) -> KnowledgeItem:
        source = None
        if row["source_title"] or row["source_url"]:
            source = Source(
                title=row["source_title"],
                url=row["source_url"],
                authority=Authority(row["authority"]),
                jurisdiction=row["jurisdiction"],
                published=row["published"],
                retrieved=row["retrieved"],
            )
        return KnowledgeItem(
            id=row["id"], topic=row["topic"], key=row["key"], statement=row["statement"],
            summary=row["summary"], jurisdiction=row["jurisdiction"],
            authority=Authority(row["authority"]), confidence=Confidence(row["confidence"]),
            status=Status(row["status"]), source=source, published=row["published"],
            effective=row["effective"], version=row["version"], rejection=row["rejection"],
            created_at=row["created_at"], verified_at=row["verified_at"], review_at=row["review_at"],
        )

    def query(
        self,
        topic: str = "",
        *,
        jurisdiction: str = "",
        status: Status | str = Status.TRUSTED,
        limit: int = 25,
    ) -> list[KnowledgeItem]:
        sql = "SELECT * FROM knowledge WHERE 1=1"
        args: list[Any] = []
        if topic:
            sql += " AND (topic LIKE ? OR statement LIKE ?)"
            args += [f"%{topic}%", f"%{topic}%"]
        if jurisdiction:
            sql += " AND (jurisdiction=? OR jurisdiction='')"
            args.append(jurisdiction)
        status_value = status.value if isinstance(status, Status) else status
        if status_value:
            sql += " AND status=?"
            args.append(status_value)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        return [self._row(r) for r in self._conn.execute(sql, args).fetchall()]

    def needs_revalidation(self, *, days: int = 180) -> list[KnowledgeItem]:
        rows = self._conn.execute(
            "SELECT * FROM knowledge WHERE status='trusted'"
        ).fetchall()
        out = []
        for r in rows:
            item = self._row(r)
            if (item.age_days or 0) > days:
                item.status = Status.STALE
                out.append(item)
        return out

    def mark_stale(self, item_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE knowledge SET status='stale' WHERE id=?", (item_id,))

    def stats(self) -> dict[str, Any]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM knowledge GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    def render(self, items: Sequence[KnowledgeItem] | None = None, limit: int = 15) -> str:
        rows = list(items) if items is not None else self.query(limit=limit)
        if not rows:
            return "(knowledge base has no trusted entries)"
        return "\n".join(
            f"- [{i.status.value}/{i.confidence.value}] {i.statement}"
            + (f"\n    src: {i.source.title if i.source else '-'} ({i.jurisdiction or 'global'})"
               if i.source else "")
            + (f"\n    review: {i.review_at}" if i.review_at else "")
            for i in rows
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_NEGATIONS = ("not ", "no ", "never", "cannot", "can't", "does not", "doesn't", "isn't", "won't")


def _contradicts(a: str, b: str) -> bool:
    """A deliberately conservative contradiction check.

    It only fires when the two statements share most of their content words and
    differ by a negation - i.e. the obvious "X is allowed" vs "X is not allowed"
    case. Ambiguous cases are left to a human, which is the safe direction.
    """
    wa, wb = _content_words(a), _content_words(b)
    if not wa or not wb:
        return False
    overlap = len(wa & wb) / max(1, min(len(wa), len(wb)))
    if overlap < 0.6:
        return False
    na, nb = _negated(a), _negated(b)
    return na != nb


def _content_words(text: str) -> set[str]:
    stop = {
        "the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "in", "on", "for",
        "and", "or", "that", "this", "it", "as", "with", "by", "from", "at", "not",
    }
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in stop and len(w) > 2}


def _negated(text: str) -> bool:
    low = text.lower()
    return any(neg in low for neg in _NEGATIONS)


def _summarise(statement: str, limit: int = 200) -> str:
    text = re.sub(r"\s+", " ", statement).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "..."


def _topic_from(statement: str) -> str:
    words = re.findall(r"[A-Za-z][a-z]+", statement)[:3]
    return "-".join(w.lower() for w in words) or "general"


def _review_date(item: KnowledgeItem) -> str:
    """Time-sensitive items get reviewed sooner."""
    from datetime import timedelta

    from jarvis.util.clock import utcnow

    days = 90 if item.authority is Authority.PRIMARY else 180
    return (utcnow() + timedelta(days=days)).isoformat(timespec="seconds")


def dumps(items: Sequence[KnowledgeItem]) -> str:
    return json.dumps([i.as_dict() for i in items], indent=2)
