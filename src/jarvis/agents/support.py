"""Productivity, memory and security agents (spec sections 24, 31, 32, 35)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from jarvis.agents.base import Agent, AgentRequest, AgentResult
from jarvis.core.confidence import Claim, Confidence
from jarvis.core.memory import MemoryCategory, MemoryStore
from jarvis.util.clock import now_iso, utcnow


# --------------------------------------------------------------------------- #
# Productivity
# --------------------------------------------------------------------------- #
@dataclass
class Todo:
    title: str
    done: bool = False
    due: str | None = None
    tags: list[str] = field(default_factory=list)


class ProductivityAgent(Agent):
    name = "productivity"
    description = "Daily planning, reminders and task tracking."
    capabilities = ("planning", "reminders", "todo", "daily")

    def __init__(self, runtime: Any = None, *, memory: MemoryStore | None = None) -> None:
        super().__init__(runtime)
        self.memory = memory

    def run(self, request: AgentRequest) -> AgentResult:
        intent = (request.param("intent") or "plan").lower()
        result = AgentResult(agent=self.name, summary="")

        if intent == "add":
            title = request.text or request.param("title") or ""
            if not title.strip():
                return AgentResult(agent=self.name, ok=False, summary="Nothing to add.")
            if self.memory is None:
                return AgentResult(agent=self.name, ok=False, summary="Memory is disabled; cannot store tasks.")
            record = self.memory.remember(
                MemoryCategory.TASK,
                title.strip()[:120],
                request.param("notes") or "",
                tags=request.param("tags") or [],
                source="user",
                importance=int(request.param("importance") or 5),
                review_at=request.param("due"),
            )
            result.summary = f"Added task: {record.title} (id {record.id})"
            result.add_claim(Claim.certain(result.summary))
            return result

        if intent == "list":
            if self.memory is None:
                return AgentResult(agent=self.name, ok=False, summary="Memory is disabled.")
            tasks = self.memory.search("", category=MemoryCategory.TASK, limit=50)
            result.summary = f"{len(tasks)} open task(s)."
            result.data["tasks"] = [t.as_dict() for t in tasks]
            result.add_claim(Claim.certain(f"{len(tasks)} task(s) stored in the task category."))
            return result

        if intent == "plan":
            return self.daily_plan(request)

        return AgentResult(agent=self.name, ok=False, summary=f"Unknown productivity intent '{intent}'.")

    def daily_plan(self, request: AgentRequest) -> AgentResult:
        """A blunt, honest day plan: what is due, what is stale, what to do first."""
        result = AgentResult(agent=self.name, summary="Daily plan")
        if self.memory is None:
            result.ok = False
            result.summary = "Memory is disabled, so there is nothing to plan from."
            return result

        tasks = self.memory.search("", category=MemoryCategory.TASK, limit=100)
        due_today = [t for t in tasks if t.due_for_review]
        high = sorted(tasks, key=lambda t: -t.importance)[:5]
        stale = self.memory.stale(days=30)

        lines = []
        if due_today:
            lines.append(f"Due today ({len(due_today)}): " + "; ".join(t.title for t in due_today[:6]))
        if high:
            lines.append("Highest importance: " + "; ".join(f"{t.title} ({t.importance})" for t in high))
        if stale:
            lines.append(f"{len(stale)} item(s) are past their review date - close or reschedule them.")
        if not lines:
            lines.append("Nothing scheduled. That is either a free day or an empty task list.")

        result.summary = " | ".join(lines)
        result.data = {
            "total_tasks": len(tasks),
            "due_today": [t.id for t in due_today],
            "top_priority": [t.id for t in high],
            "stale": len(stale),
        }
        result.add_claim(
            Claim.certain(
                f"{len(tasks)} task(s) on file, {len(due_today)} due today, {len(stale)} past review."
            )
        )
        return result


# --------------------------------------------------------------------------- #
# Memory agent
# --------------------------------------------------------------------------- #
class MemoryAgent(Agent):
    name = "memory"
    description = "Reads, writes, searches, exports and forgets long-term memory."
    capabilities = ("memory", "remember", "recall", "forget", "export")

    def __init__(self, runtime: Any = None, *, memory: MemoryStore | None = None) -> None:
        super().__init__(runtime)
        self.memory = memory

    def run(self, request: AgentRequest) -> AgentResult:
        intent = (request.param("intent") or "search").lower()
        if self.memory is None:
            return AgentResult(
                agent=self.name,
                ok=False,
                summary="Memory is disabled by configuration. Nothing is stored or read.",
            )

        if intent == "remember":
            category = request.param("category") or MemoryCategory.KNOWLEDGE
            title = request.param("title") or request.text[:120] or "note"
            record = self.memory.remember(
                category,
                title,
                request.param("content") or request.text,
                tags=request.param("tags") or [],
                source=request.param("source") or "user",
                importance=int(request.param("importance") or 5),
                authorised=bool(request.param("authorised")),
            )
            return AgentResult(
                agent=self.name,
                summary=f"Stored in {category}: {record.title} (id {record.id})",
                data={"id": record.id},
                claims=[Claim.certain(f"Memory record {record.id} created in {category}.")],
            )

        if intent == "forget":
            target = request.param("id")
            if target:
                ok = self.memory.forget(target)
                summary = f"Forgot {target}." if ok else f"No such memory: {target}"
            else:
                query = request.text or ""
                count = self.memory.forget_matching(query)
                summary = f"Forgot {count} record(s) matching '{query}'."
            return AgentResult(agent=self.name, summary=summary, claims=[Claim.certain(summary)])

        if intent == "export":
            destination = Path(request.param("path") or (Path.cwd() / "output" / "jarvis-memory.json"))
            path = self.memory.export(destination, category=request.param("category"))
            summary = f"Exported memory to {path}"
            return AgentResult(
                agent=self.name, summary=summary, data={"path": str(path)},
                claims=[Claim.certain(summary)],
            )

        hits = self.memory.search(
            request.text or request.param("query") or "",
            category=request.param("category"),
            limit=int(request.param("limit") or 10),
        )
        return AgentResult(
            agent=self.name,
            summary=f"{len(hits)} matching record(s).",
            data={"records": [r.as_dict() for r in hits], "counts": self.memory.counts()},
            claims=[Claim.certain(f"{len(hits)} record(s) matched.")],
        )


# --------------------------------------------------------------------------- #
# Security agent
# --------------------------------------------------------------------------- #
class SecurityAgent(Agent):
    name = "security"
    description = "Pre-flight checks: secrets, paths, permissions, scopes."
    capabilities = ("security", "preflight", "secrets", "scopes")

    def __init__(self, runtime: Any = None) -> None:
        super().__init__(runtime)

    def run(self, request: AgentRequest) -> AgentResult:
        from jarvis.util import redaction

        payload = request.param("payload") or request.text or ""
        if isinstance(payload, (dict, list)):
            findings = redaction.scan_mapping(payload)
        else:
            findings = redaction.scan(str(payload))

        result = AgentResult(
            agent=self.name,
            ok=not findings,
            summary=(
                f"{len(findings)} secret-shaped region(s) detected."
                if findings
                else "No secret-shaped content detected."
            ),
            data={"findings": [{"label": f.label, "preview": f.preview} for f in findings]},
        )
        result.add_claim(
            Claim.certain(
                f"{len(findings)} secret-shaped region(s) found by the redaction scanner."
            )
        )
        if findings:
            result.follow_ups.extend(
                [
                    "Move credentials to environment variables or a secret store.",
                    "Rotate anything that has already been shared in chat, a file or a repo.",
                ]
            )
        return result

    def preflight(self, *, text: str = "", paths: Iterable[str] = (), root: str | None = None) -> dict[str, Any]:
        """The checks the supervisor runs before executing anything."""
        from jarvis.util import redaction

        problems: list[str] = []
        if redaction.has_secret(text):
            problems.append("the request contains something that looks like a credential")
        if root:
            for path in paths:
                allowed, reason = _within_root(path, root)
                if not allowed:
                    problems.append(reason)
        return {"ok": not problems, "problems": problems}


def _within_root(path: str, root: str) -> tuple[bool, str]:
    try:
        target = Path(path).resolve()
        base = Path(root).resolve()
    except OSError as exc:
        return False, f"cannot resolve {path}: {exc}"
    if base not in target.parents and target != base:
        return False, f"{target} escapes the allowed root {base}"
    return True, ""


# --------------------------------------------------------------------------- #
# Lessons (spec section 35)
# --------------------------------------------------------------------------- #
@dataclass
class Lesson:
    task: str
    result: str
    what_worked: str
    what_failed: str
    lesson: str
    future_strategy: str
    confidence: Confidence = Confidence.LOW
    single_instance: bool = True


class LessonLog:
    """Structured post-task learning with an explicit guard against over-generalising.

    Spec section 35: a single failure must not silently become a universal rule.
    """

    def __init__(self, memory: MemoryStore | None = None) -> None:
        self.memory = memory

    def record(self, lesson: Lesson) -> dict[str, Any]:
        content = (
            f"task: {lesson.task}\nresult: {lesson.result}\nworked: {lesson.what_worked}\n"
            f"failed: {lesson.what_failed}\nlesson: {lesson.lesson}\nstrategy: {lesson.future_strategy}"
        )
        if lesson.single_instance:
            content += (
                "\n\nSCOPE: observed once. This is a hypothesis, not a rule - revalidate "
                "before applying it broadly."
            )
        capped = Confidence.LOW if lesson.single_instance else lesson.confidence
        if self.memory is None:
            return {"stored": False, "reason": "memory disabled", "confidence": capped.value}
        record = self.memory.remember(
            MemoryCategory.LESSON,
            lesson.lesson[:120],
            content,
            tags=["lesson"],
            source="self-evaluation",
            confidence=capped,
            importance=4,
            review_at=(utcnow() + timedelta(days=30)).isoformat(timespec="seconds"),
        )
        return {"stored": True, "id": record.id, "confidence": capped.value, "at": now_iso()}

    def recent(self, limit: int = 10) -> Sequence[Any]:
        if self.memory is None:
            return []
        return self.memory.search("", category=MemoryCategory.LESSON, limit=limit)
