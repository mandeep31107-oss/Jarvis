"""Append-only audit trail (spec section 28).

Every consequential action leaves a record of: what was asked, how Jarvis
interpreted it, the plan, the tools and data sources, the policy/risk result,
whether a human approved it, what actually happened, and how it recovered if it
failed. Records are redacted on the way in and the log is append-only from the
agent's point of view -- there is no ``delete`` method here by design.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from jarvis.util import redaction
from jarvis.util.clock import now_iso
from jarvis.util.ids import new_id
from jarvis.util.store import append_jsonl, read_jsonl


@dataclass
class AuditRecord:
    """One row of the audit log."""

    action: str
    user_request: str = ""
    interpretation: str = ""
    plan: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    policy_checks: list[str] = field(default_factory=list)
    risk: str = ""
    approval: str = ""
    result: str = ""
    error: str = ""
    recovery: str = ""
    agent: str = ""
    task_id: str = ""
    id: str = field(default_factory=lambda: new_id("aud"))
    at: str = field(default_factory=now_iso)
    #: Free-form structured extras (redacted before they are written).
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return redaction.redact_mapping(data)


class AuditLog:
    """JSONL audit log with redaction and query support."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    # --- write ---------------------------------------------------------------
    def record(self, entry: AuditRecord) -> AuditRecord:
        with self._lock:
            append_jsonl(self.path, entry.as_dict())
        return entry

    def log(
        self,
        action: str,
        *,
        user_request: str = "",
        interpretation: str = "",
        plan: Sequence[str] = (),
        tools: Sequence[str] = (),
        sources: Sequence[str] = (),
        policy_checks: Sequence[str] = (),
        risk: str = "",
        approval: str = "",
        result: str = "",
        error: str = "",
        recovery: str = "",
        agent: str = "",
        task_id: str = "",
        meta: dict[str, Any] | None = None,
        **extra: Any,
    ) -> AuditRecord:
        return self.record(
            AuditRecord(
                action=action,
                user_request=user_request,
                interpretation=interpretation,
                plan=list(plan),
                tools=list(tools),
                sources=list(sources),
                policy_checks=list(policy_checks),
                risk=risk,
                approval=approval,
                result=result,
                error=error,
                recovery=recovery,
                agent=agent,
                task_id=task_id,
                meta={**(meta or {}), **extra},
            )
        )

    # --- read ----------------------------------------------------------------
    def entries(self, limit: int | None = None) -> list[dict[str, Any]]:
        rows = list(read_jsonl(self.path))
        return rows[-limit:] if limit else rows

    def query(
        self,
        *,
        agent: str | None = None,
        risk: str | None = None,
        task_id: str | None = None,
        contains: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        rows = self.entries()
        if agent:
            rows = [r for r in rows if r.get("agent") == agent]
        if risk:
            rows = [r for r in rows if r.get("risk") == risk]
        if task_id:
            rows = [r for r in rows if r.get("task_id") == task_id]
        if contains:
            needle = contains.lower()
            rows = [r for r in rows if needle in redaction.redact_mapping(r).__str__().lower()]
        return rows[-limit:]

    def stats(self) -> dict[str, Any]:
        rows = self.entries()
        by_risk: dict[str, int] = {}
        by_agent: dict[str, int] = {}
        errors = 0
        for r in rows:
            by_risk[r.get("risk", "?")] = by_risk.get(r.get("risk", "?"), 0) + 1
            agent = r.get("agent") or "-"
            by_agent[agent] = by_agent.get(agent, 0) + 1
            if r.get("error"):
                errors += 1
        return {
            "total": len(rows),
            "by_risk": by_risk,
            "by_agent": by_agent,
            "with_errors": errors,
            "path": str(self.path),
        }

    def export(self, destination: str | Path) -> Path:
        """Copy the log somewhere the user can read or hand to an auditor."""
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            "".join(f"{redaction.redact_mapping(r)}\n" for r in self.entries()),
            encoding="utf-8",
        )
        return dest


def summarize(records: Iterable[dict[str, Any]]) -> str:
    """One-line-per-entry rendering used by the CLI ``/audit`` command."""
    lines = []
    for r in records:
        stamp = str(r.get("at", ""))[:19].replace("T", " ")
        risk = (r.get("risk") or "-").upper()[:4]
        agent = r.get("agent") or "-"
        action = r.get("action") or "-"
        result = r.get("result") or r.get("error") or ""
        lines.append(f"{stamp}  {risk:<5} {agent:<14} {action:<26} {result}")
    return "\n".join(lines) or "(audit log is empty)"
