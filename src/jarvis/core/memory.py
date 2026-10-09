"""Long-term memory (spec section 8).

A SQLite-backed store with the exact metadata the spec asks for (created,
updated, source, confidence, importance, expiry/review) and the exact user
controls: view, search, edit, delete, disable, export.

Two rules are enforced in code rather than in documentation:

* **No secrets.** Writing something that looks like a credential is refused.
  Memory is not a secrets manager.
* **Financial/business memory is opt-in.** Records in the ``financial``
  category are only stored when the caller passes ``authorised=True``, which the
  runtime only does after the user has said so.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.core.confidence import Confidence
from jarvis.errors import PolicyViolation
from jarvis.util import redaction
from jarvis.util.clock import now_iso, parse_iso
from jarvis.util.ids import new_id
from jarvis.util.store import connect_sqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    id            TEXT PRIMARY KEY,
    category      TEXT NOT NULL,
    title         TEXT NOT NULL,
    content       TEXT NOT NULL,
    tags          TEXT NOT NULL DEFAULT '[]',
    source        TEXT NOT NULL DEFAULT '',
    confidence    TEXT NOT NULL DEFAULT 'medium',
    importance    INTEGER NOT NULL DEFAULT 3,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    expires_at    TEXT,
    review_at     TEXT,
    authorisation TEXT NOT NULL DEFAULT '',
    deleted       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memory_category ON memory(category, deleted);
CREATE INDEX IF NOT EXISTS idx_memory_updated  ON memory(updated_at);
"""


class MemoryCategory:
    USER_PROFILE = "user_profile"
    PROJECT = "project"
    CONVERSATION = "conversation"
    DECISION = "decision"
    TASK = "task"
    PREFERENCE = "preference"
    KNOWLEDGE = "knowledge"
    LESSON = "lesson"
    FINANCIAL = "financial"

    ALL = (
        USER_PROFILE,
        PROJECT,
        CONVERSATION,
        DECISION,
        TASK,
        PREFERENCE,
        KNOWLEDGE,
        LESSON,
        FINANCIAL,
    )

    #: Categories that may only be written with the user's explicit say-so.
    SENSITIVE = (FINANCIAL,)


@dataclass
class MemoryRecord:
    id: str
    category: str
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    source: str = ""
    confidence: Confidence = Confidence.MEDIUM
    importance: int = 3
    created_at: str = now_iso()
    updated_at: str = now_iso()
    expires_at: str | None = None
    review_at: str | None = None
    authorisation: str = ""
    deleted: bool = False

    @property
    def expired(self) -> bool:
        dt = parse_iso(self.expires_at)
        if dt is None:
            return False
        from jarvis.util.clock import utcnow

        return utcnow() > dt

    @property
    def due_for_review(self) -> bool:
        dt = parse_iso(self.review_at)
        if dt is None:
            return False
        from jarvis.util.clock import utcnow

        return utcnow() > dt

    def as_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "category": self.category,
            "title": self.title,
            "tags": list(self.tags),
            "source": self.source,
            "confidence": self.confidence.value,
            "importance": self.importance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "review_at": self.review_at,
            "authorisation": self.authorisation,
        }
        if include_content:
            data["content"] = self.content
        return data


def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        id=row["id"],
        category=row["category"],
        title=row["title"],
        content=row["content"],
        tags=json.loads(row["tags"] or "[]"),
        source=row["source"],
        confidence=Confidence(row["confidence"]),
        importance=row["importance"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        review_at=row["review_at"],
        authorisation=row["authorisation"],
        deleted=bool(row["deleted"]),
    )


class MemoryStore:
    """Persistent memory with hard privacy guarantees."""

    def __init__(self, path: str | Path, *, enabled: bool = True) -> None:
        self.path = Path(path)
        self.enabled = enabled
        self._lock = threading.RLock()
        self._conn = connect_sqlite(self.path)
        self._conn.executescript(_SCHEMA)

    # --- enable / disable ----------------------------------------------------
    def disable(self) -> None:
        """Turning memory off stops reads *and* writes; nothing is deleted."""
        self.enabled = False

    def enable(self) -> None:
        self.enabled = True

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _require_enabled(self, operation: str) -> None:
        if not self.enabled:
            raise PolicyViolation(
                f"Memory is disabled, refusing {operation}.", rule="memory.disabled", risk="low"
            )

    # --- write ---------------------------------------------------------------
    def remember(
        self,
        category: str,
        title: str,
        content: str,
        *,
        tags: Sequence[str] = (),
        source: str = "",
        confidence: Confidence | str = Confidence.MEDIUM,
        importance: int = 3,
        expires_at: str | None = None,
        review_at: str | None = None,
        authorised: bool = False,
    ) -> MemoryRecord:
        self._require_enabled("to store a memory")

        if category not in MemoryCategory.ALL:
            raise PolicyViolation(
                f"Unknown memory category {category!r}.", rule="memory.category", risk="low"
            )

        findings = redaction.scan(f"{title}\n{content}")
        if findings:
            labels = ", ".join(sorted({f.label for f in findings}))
            raise PolicyViolation(
                "Refusing to store what looks like a secret "
                f"({labels}). Memory is not a secrets manager - put credentials in "
                "your environment or a dedicated secret store.",
                rule="memory.no_secrets",
                risk="high",
            )

        if category in MemoryCategory.SENSITIVE and not authorised:
            raise PolicyViolation(
                f"Category '{category}' requires the user's explicit authorisation. "
                "Re-run with authorised=True only after the user has agreed.",
                rule="memory.sensitive_consent",
                risk="high",
            )

        conf = Confidence(confidence) if isinstance(confidence, str) else confidence
        stamp = now_iso()
        record = MemoryRecord(
            id=new_id("mem"),
            category=category,
            title=title.strip(),
            content=content.strip(),
            tags=sorted({t.strip() for t in tags if t.strip()}),
            source=source,
            confidence=conf,
            importance=max(0, min(10, int(importance))),
            created_at=stamp,
            updated_at=stamp,
            expires_at=expires_at,
            review_at=review_at,
            authorisation=("user" if authorised else ""),
        )
        with self._lock:
            self._conn.execute(
                """INSERT INTO memory (id, category, title, content, tags, source, confidence,
                                       importance, created_at, updated_at, expires_at, review_at,
                                       authorisation, deleted)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                (
                    record.id,
                    record.category,
                    record.title,
                    record.content,
                    json.dumps(record.tags),
                    record.source,
                    record.confidence.value,
                    record.importance,
                    record.created_at,
                    record.updated_at,
                    record.expires_at,
                    record.review_at,
                    record.authorisation,
                ),
            )
        return record

    def update(self, record_id: str, *, title: str | None = None, content: str | None = None,
               tags: Sequence[str] | None = None, importance: int | None = None,
               confidence: Confidence | str | None = None,
               review_at: str | None = None, expires_at: str | None = None) -> MemoryRecord:
        self._require_enabled("to update a memory")
        record = self.get(record_id)
        if record is None:
            raise KeyError(f"no such memory: {record_id}")
        if content is not None and redaction.has_secret(content):
            raise PolicyViolation(
                "Refusing to store what looks like a secret.", rule="memory.no_secrets", risk="high"
            )
        new_title = title if title is not None else record.title
        new_content = content if content is not None else record.content
        new_tags = json.dumps(sorted({t for t in tags if t})) if tags is not None else json.dumps(record.tags)
        new_importance = record.importance if importance is None else max(0, min(10, importance))
        new_conf = (
            record.confidence.value
            if confidence is None
            else (Confidence(confidence) if isinstance(confidence, str) else confidence).value
        )
        new_review = record.review_at if review_at is None else review_at
        new_expires = record.expires_at if expires_at is None else expires_at
        with self._lock:
            self._conn.execute(
                """UPDATE memory SET title=?, content=?, tags=?, importance=?, confidence=?,
                                     review_at=?, expires_at=?, updated_at=? WHERE id=? AND deleted=0""",
                (new_title, new_content, new_tags, new_importance, new_conf,
                 new_review, new_expires, now_iso(), record_id),
            )
        return self.get(record_id)  # type: ignore[return-value]

    # --- read ----------------------------------------------------------------
    def get(self, record_id: str) -> MemoryRecord | None:
        self._require_enabled("to read memory")
        row = self._conn.execute(
            "SELECT * FROM memory WHERE id=? AND deleted=0", (record_id,)
        ).fetchone()
        return _row_to_record(row) if row else None

    def search(
        self,
        query: str = "",
        *,
        category: str | None = None,
        tag: str | None = None,
        limit: int = 25,
        include_expired: bool = False,
    ) -> list[MemoryRecord]:
        """Keyword search ranked by a simple relevance/importance/recency blend.

        Deliberately not full-text-indexed: memory volumes on a personal machine
        are small, and a LIKE scan keeps the store portable and inspectable.
        """
        self._require_enabled("to search memory")
        sql = "SELECT * FROM memory WHERE deleted=0"
        args: list[Any] = []
        if category:
            sql += " AND category=?"
            args.append(category)
        if tag:
            sql += " AND tags LIKE ?"
            args.append(f'%"{tag}"%')
        if query:
            like = f"%{query}%"
            sql += " AND (title LIKE ? OR content LIKE ? OR tags LIKE ?)"
            args += [like, like, like]
        rows = self._conn.execute(sql, args).fetchall()
        records = [_row_to_record(r) for r in rows]
        if not include_expired:
            records = [r for r in records if not r.expired]

        needle = query.lower().strip()

        def score(r: MemoryRecord) -> tuple[int, int, str]:
            relevance = 0
            if needle:
                if needle in r.title.lower():
                    relevance += 3
                if needle in r.content.lower():
                    relevance += 1
                if any(needle in t.lower() for t in r.tags):
                    relevance += 2
            return (relevance, r.importance, r.updated_at)

        records.sort(key=score, reverse=True)
        return records[:limit]

    def all_records(self, *, category: str | None = None, limit: int = 500) -> list[MemoryRecord]:
        return self.search("", category=category, limit=limit)

    def stale(self, *, days: int = 180) -> list[MemoryRecord]:
        """Records whose review date has passed - surfaced so they can be revalidated."""
        self._require_enabled("to inspect memory")
        rows = self._conn.execute("SELECT * FROM memory WHERE deleted=0").fetchall()
        return [r for r in (_row_to_record(row) for row in rows) if r.due_for_review or r.expired]

    def counts(self) -> dict[str, int]:
        self._require_enabled("to count memory")
        rows = self._conn.execute(
            "SELECT category, COUNT(*) AS n FROM memory WHERE deleted=0 GROUP BY category"
        ).fetchall()
        out = {c: 0 for c in MemoryCategory.ALL}
        for row in rows:
            out[row["category"]] = row["n"]
        out["total"] = sum(out.values())
        return out

    # --- delete / export -----------------------------------------------------
    def forget(self, record_id: str) -> bool:
        """Soft delete: the row stays flagged so an audit of memory itself is possible,
        but it is invisible to every read path and excluded from exports."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memory SET deleted=1, updated_at=? WHERE id=? AND deleted=0",
                (now_iso(), record_id),
            )
        return cur.rowcount > 0

    def forget_matching(self, query: str) -> int:
        hits = self.search(query, limit=1000)
        return sum(1 for r in hits if self.forget(r.id))

    def purge_deleted(self) -> int:
        """Hard delete of already-forgotten rows (user's right to erasure)."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM memory WHERE deleted=1")
        return cur.rowcount

    def export(self, destination: str | Path, *, category: str | None = None) -> Path:
        self._require_enabled("to export memory")
        records = self.all_records(category=category, limit=100000)
        payload = {
            "exported_at": now_iso(),
            "record_count": len(records),
            "records": [r.as_dict() for r in records],
        }
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return dest

    def render(self, records: Iterable[MemoryRecord] | None = None, limit: int = 20) -> str:
        rows = list(records) if records is not None else self.all_records(limit=limit)
        if not rows:
            return "(memory is empty)"
        lines = []
        for r in rows:
            flags = []
            if r.expired:
                flags.append("EXPIRED")
            if r.due_for_review:
                flags.append("REVIEW")
            suffix = f"  [{' '.join(flags)}]" if flags else ""
            lines.append(
                f"- [{r.category}] {r.title}  (imp {r.importance}, {r.confidence.value}){suffix}\n"
                f"  {r.content[:160]}\n  id={r.id} src={r.source or '-'}"
            )
        return "\n".join(lines)
