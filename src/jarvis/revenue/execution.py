"""Executing an approved revenue plan, and monitoring it afterwards.

The analysis pipeline could already recommend an option. What it could not do is
carry an approved plan through to action and then watch the result. This is that
missing half, and the design constraint is the one that matters most: **nothing
here runs without a recorded approval, and no approval is ever inferred.**

Every step is gated through the same :class:`~jarvis.core.policy.PolicyEngine` as
the rest of the system. A CRITICAL step stops regardless of what was approved
earlier, because an approval for a plan is not an approval for every consequence
of it.

What "execute" honestly means here: run the steps that are actually automatable -
generating documents, creating a project skeleton, recording a decision, writing
a monitoring rule - and stop in front of the ones that are not. Moving money,
creating accounts and accepting terms of service need a human, and a step of that
kind is reported as *blocked on you* rather than silently skipped or faked.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from jarvis.core.audit import AuditLog
from jarvis.core.policy import Approval, PolicyEngine
from jarvis.core.risk import ActionRequest, RiskLevel
from jarvis.util.clock import now_iso
from jarvis.util.ids import new_id

__all__ = [
    "ExecutionPlan",
    "ExecutionReport",
    "Executor",
    "Monitor",
    "MonitorRule",
    "Step",
    "StepOutcome",
    "StepState",
]


class StepState(str, Enum):
    """Where a step is in its life.

    ``BLOCKED_ON_USER`` is distinct from ``FAILED``: one means a person must act,
    the other means Jarvis tried and could not. Collapsing them hides the single
    most useful piece of information in the report.
    """

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    #: Jarvis cannot do this one; a human has to.
    BLOCKED_ON_USER = "blocked_on_user"
    SKIPPED = "skipped"

    @property
    def terminal(self) -> bool:
        return self in {
            StepState.DONE,
            StepState.FAILED,
            StepState.BLOCKED_ON_USER,
            StepState.SKIPPED,
        }


#: Steps that need a human being, by canonical verb. Anything here is reported as
#: blocked rather than attempted, because a partial or faked attempt is worse
#: than an honest stop - and because several of these are legally significant.
HUMAN_ONLY = frozenset(
    {
        "payment.transfer",
        "payment.collect",
        "account.create",
        "account.link",
        "identity.verify",
        "terms.accept",
        "contract.sign",
        "tax.file",
        "bank.connect",
    }
)


@dataclass(slots=True)
class Step:
    """One unit of work inside a plan.

    Attributes
    ----------
    verb:
        Canonical action verb, matched against the policy rules.
    irreversible:
        Whether it can be undone. Irreversible steps are never retried
        automatically, because a retry can double the effect.
    handler:
        Callable that does the work. Not serialised; a plan reloaded from disk
        has handlers reattached by the caller.
    """

    verb: str
    title: str
    target: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    risk: RiskLevel = RiskLevel.LOW
    irreversible: bool = False
    state: StepState = StepState.PENDING
    message: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    approval: dict[str, Any] = field(default_factory=dict)
    at: str = ""
    handler: Callable[[Step], dict[str, Any]] | None = None

    @property
    def needs_a_human(self) -> bool:
        return self.verb in HUMAN_ONLY

    def as_dict(self) -> dict[str, Any]:
        return {
            "verb": self.verb,
            "title": self.title,
            "target": self.target,
            "detail": self.detail,
            "risk": self.risk.value,
            "irreversible": self.irreversible,
            "state": self.state.value,
            "message": self.message,
            "result": self.result,
            "attempts": self.attempts,
            "approval": self.approval,
            "at": self.at,
            "needs_a_human": self.needs_a_human,
        }


@dataclass(slots=True)
class StepOutcome:
    """What happened when one step was attempted."""

    step: Step
    ok: bool
    message: str
    gated_by_policy: bool = False
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ExecutionPlan:
    """An ordered list of steps with a durable record of what happened."""

    plan_id: str = field(default_factory=lambda: new_id("plan"))
    title: str = ""
    opportunity: str = ""
    approved_by: str = ""
    created_at: str = field(default_factory=now_iso)
    steps: list[Step] = field(default_factory=list)
    #: Set once the user has approved this specific plan.
    approval: dict[str, Any] = field(default_factory=dict)

    @property
    def approved(self) -> bool:
        return bool(self.approval.get("granted"))

    def add(self, step: Step) -> Step:
        self.steps.append(step)
        return step

    def counts(self) -> dict[str, int]:
        out = {state.value: 0 for state in StepState}
        for step in self.steps:
            out[step.state.value] += 1
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "title": self.title,
            "opportunity": self.opportunity,
            "approved_by": self.approved_by,
            "created_at": self.created_at,
            "approval": self.approval,
            "approved": self.approved,
            "counts": self.counts(),
            "steps": [s.as_dict() for s in self.steps],
        }


@dataclass(slots=True)
class ExecutionReport:
    """The result of a run, shaped to be shown to a person."""

    plan_id: str
    ran: bool
    reason: str
    counts: dict[str, int]
    steps: list[dict[str, Any]]
    next_action: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "ran": self.ran,
            "reason": self.reason,
            "counts": self.counts,
            "steps": self.steps,
            "next_action": self.next_action,
        }


class Executor:
    """Runs approved plan steps, one policy gate per step.

    The engine holds no authority of its own. It asks the policy engine about
    every step, individually, at the moment it is about to run it - not once for
    the whole plan. Approval of a plan is not approval of each consequence.
    """

    def __init__(
        self,
        policy: PolicyEngine,
        audit: AuditLog | None = None,
        *,
        ledger: Path | None = None,
        actor: str = "jarvis",
    ) -> None:
        self.policy = policy
        self.audit = audit
        self.ledger = ledger
        self.actor = actor

    # --- approval ------------------------------------------------------------
    def approve(self, plan: ExecutionPlan, *, approver: str = "user", note: str = "") -> ExecutionPlan:
        """Record that a person approved this specific plan.

        Approvals are per plan, never blanket. The record is written into the
        plan so a later run can prove the authority it is acting on.
        """
        plan.approval = {
            "granted": True,
            "approver": approver,
            "note": note,
            "plan_id": plan.plan_id,
            "at": now_iso(),
        }
        plan.approved_by = approver
        if self.audit is not None:
            self.audit.log(
                "revenue.plan_approved",
                result="approved",
                agent="revenue",
                meta={"plan_id": plan.plan_id, "approver": approver, "steps": len(plan.steps)},
            )
        self._persist(plan)
        return plan

    # --- running -------------------------------------------------------------
    def run(
        self,
        plan: ExecutionPlan,
        *,
        approver: Callable[[ActionRequest, Any], Approval] | None = None,
    ) -> ExecutionReport:
        """Execute the plan's steps in order.

        Stops at the first step the policy engine refuses. Continuing past a
        refusal would mean the gate was decorative.
        """
        if not plan.approved:
            reason = (
                "This plan has not been approved, so nothing was executed. "
                "Approving is a deliberate act - Jarvis never infers one."
            )
            if self.audit is not None:
                self.audit.log(
                    "revenue.plan_blocked", result="blocked", agent="revenue",
                    meta={"plan_id": plan.plan_id, "reason": "unapproved"},
                )
            return ExecutionReport(
                plan_id=plan.plan_id, ran=False, reason=reason,
                counts=plan.counts(), steps=[s.as_dict() for s in plan.steps],
                next_action="Review the plan and approve it to proceed.",
            )

        blocked: list[str] = []
        for step in plan.steps:
            if step.state is not StepState.PENDING:
                continue
            outcome = self._run_step(step, approver=approver)
            if not outcome.ok and outcome.gated_by_policy:
                #: A policy refusal ends the run. Later steps were approved as
                #: part of a sequence, not as independent acts.
                blocked.append(step.title)
                break
            if not outcome.ok:
                blocked.append(step.title)

        report = ExecutionReport(
            plan_id=plan.plan_id,
            ran=True,
            reason="executed" if not blocked else f"stopped at: {', '.join(blocked[:3])}",
            counts=plan.counts(),
            steps=[s.as_dict() for s in plan.steps],
            next_action=self._next_action(plan),
        )
        if self.audit is not None:
            self.audit.log(
                "revenue.plan_executed",
                result="ok" if not blocked else "partial",
                agent="revenue",
                meta={"plan_id": plan.plan_id, **report.counts},
            )
        self._persist(plan)
        return report

    def _run_step(
        self, step: Step, *, approver: Callable[[ActionRequest, Any], Approval] | None
    ) -> StepOutcome:
        step.attempts += 1
        step.at = now_iso()

        #: Human-only steps are reported, never attempted. Faking one would put a
        #: false record into the ledger; attempting one would either fail or, on
        #: the platforms that allow automation, breach their terms.
        if step.needs_a_human:
            step.state = StepState.BLOCKED_ON_USER
            step.message = (
                "This step needs you: it involves money, an account, identity or "
                "accepting terms. Jarvis will not do these on your behalf."
            )
            return StepOutcome(step, False, step.message)

        action = ActionRequest(
            verb=step.verb,
            target=step.target,
            context={**step.detail, "plan_id": step.title, "risk": step.risk.value},
            actor=self.actor,
            irreversible=step.irreversible,
        )
        preapproved = None
        if approver is not None:
            preapproved = approver(action, step)

        try:
            decision = self.policy.gate(action, preapproved=preapproved)
        except Exception as exc:  # a hard denial or an engine fault
            step.state = StepState.FAILED
            step.message = f"the policy engine refused this step: {exc}"
            if self.audit is not None:
                self.audit.log(
                    "revenue.step_refused", result="refused", agent="revenue",
                    meta={"verb": step.verb, "reason": str(exc)},
                )
            return StepOutcome(step, False, step.message, gated_by_policy=True)

        if not decision.allowed:
            step.state = StepState.BLOCKED_ON_USER if decision.requires_approval else StepState.FAILED
            step.message = f"policy: {decision.reason}"
            step.result = {"decision": decision.decision, "risk": decision.assessment.level.value}
            if self.audit is not None:
                self.audit.log(
                    "revenue.step_blocked", result="blocked", agent="revenue",
                    meta={"verb": step.verb, "decision": decision.decision, "reason": decision.reason},
                )
            return StepOutcome(step, False, step.message, gated_by_policy=True)

        #: Record the approval that unlocked this step, so the ledger shows the
        #: authority each action ran under rather than asserting it.
        if decision.requires_approval:
            step.approval = {
                "required": True,
                "risk": decision.assessment.level.value,
                "decision": decision.decision,
            }

        if step.handler is None:
            step.state = StepState.BLOCKED_ON_USER
            step.message = (
                "Jarvis has no way to carry out this step itself. It is recorded "
                "here so nothing is quietly forgotten."
            )
            return StepOutcome(step, False, step.message)

        step.state = StepState.RUNNING
        try:
            result = step.handler(step) or {}
        except Exception as exc:
            step.state = StepState.FAILED
            step.message = f"{type(exc).__name__}: {exc}"
            if self.audit is not None:
                self.audit.log(
                    "revenue.step_failed", result="failed", agent="revenue",
                    meta={"verb": step.verb, "error": str(exc)},
                )
            return StepOutcome(step, False, step.message)

        step.state = StepState.DONE
        step.result = result if isinstance(result, dict) else {"value": result}
        step.message = str(step.result.get("message", "done"))
        if self.audit is not None:
            self.audit.log(
                "revenue.step_done", result="ok", agent="revenue",
                meta={"verb": step.verb, "irreversible": step.irreversible},
            )
        return StepOutcome(step, True, step.message, data=step.result)

    @staticmethod
    def _next_action(plan: ExecutionPlan) -> str:
        counts = plan.counts()
        if counts[StepState.BLOCKED_ON_USER.value]:
            return (
                f"{counts[StepState.BLOCKED_ON_USER.value]} step(s) need you. "
                "They involve money, accounts, identity or terms of service."
            )
        if counts[StepState.FAILED.value]:
            return f"{counts[StepState.FAILED.value]} step(s) failed; see the report before retrying."
        if counts[StepState.PENDING.value]:
            return f"{counts[StepState.PENDING.value]} step(s) still pending."
        return "Every step completed."

    # --- persistence ---------------------------------------------------------
    def _persist(self, plan: ExecutionPlan) -> None:
        """Write the ledger so a run survives a restart.

        A plan that only exists in memory cannot be audited afterwards, and an
        agent that forgets what it did cannot be held to account for it.
        """
        if self.ledger is None:
            return
        try:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            existing: list[dict[str, Any]] = []
            if self.ledger.exists():
                try:
                    loaded = json.loads(self.ledger.read_text() or "[]")
                    existing = [p for p in loaded if isinstance(p, dict)] if isinstance(loaded, list) else []
                except json.JSONDecodeError:
                    #: A corrupt ledger must not stop execution, and must not be
                    #: silently overwritten - that would erase the audit trail.
                    existing = []
                    self.ledger.with_suffix(".corrupt").write_text(
                        self.ledger.read_text(errors="replace")
                    )
            #: Replace any earlier copy of this plan rather than appending a
            #: duplicate, so the ledger shows the latest state of each plan.
            existing = [p for p in existing if p.get("plan_id") != plan.plan_id]
            existing.append(plan.as_dict())
            self.ledger.write_text(json.dumps(existing, indent=2))
        except OSError:
            #: Persistence is best-effort; failing to write must not lose the run.
            return

    def load(self) -> list[dict[str, Any]]:
        """Previously recorded plans, newest last."""
        if self.ledger is None or not self.ledger.exists():
            return []
        try:
            loaded = json.loads(self.ledger.read_text() or "[]")
        except (OSError, json.JSONDecodeError):
            return []
        return [p for p in loaded if isinstance(p, dict)] if isinstance(loaded, list) else []


# ------------------------------------------------------------------ monitoring


@dataclass(slots=True)
class MonitorRule:
    """A threshold to watch, and what to say when it is crossed.

    Attributes
    ----------
    direction:
        ``above`` or ``below``.
    """

    name: str
    metric: str
    threshold: float
    direction: str = "above"
    severity: str = "warning"
    advice: str = ""
    last_value: float | None = None
    last_state: str = "unknown"
    triggered_at: str = ""

    def __post_init__(self) -> None:
        if self.direction not in ("above", "below"):
            raise ValueError(f"direction must be 'above' or 'below', not {self.direction!r}")

    def evaluate(self, value: float) -> bool:
        """Whether the value crosses the threshold. Updates the recorded state."""
        self.last_value = value
        crossed = value > self.threshold if self.direction == "above" else value < self.threshold
        self.last_state = "breached" if crossed else "ok"
        if crossed:
            self.triggered_at = now_iso()
        return crossed

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "metric": self.metric,
            "threshold": self.threshold,
            "direction": self.direction,
            "severity": self.severity,
            "advice": self.advice,
            "last_value": self.last_value,
            "last_state": self.last_state,
            "triggered_at": self.triggered_at,
        }


class Monitor:
    """Watches the numbers a plan depends on and reports breaches.

    Monitoring is the part that turns a one-off analysis into an ongoing
    decision. Without it a projection is a guess made once and never revisited.
    """

    def __init__(self, rules: list[MonitorRule] | None = None, audit: AuditLog | None = None) -> None:
        self.rules = list(rules or [])
        self.audit = audit

    def add(self, rule: MonitorRule) -> MonitorRule:
        self.rules.append(rule)
        return rule

    def check(self, metrics: dict[str, float]) -> list[dict[str, Any]]:
        """Evaluate every rule against a set of readings.

        Returns only the breaches, since a report of everything-is-fine is noise
        at the moment it matters.
        """
        breaches: list[dict[str, Any]] = []
        for rule in self.rules:
            if rule.metric not in metrics:
                continue
            value = metrics[rule.metric]
            if rule.evaluate(value):
                entry = {
                    "rule": rule.name,
                    "metric": rule.metric,
                    "value": value,
                    "threshold": rule.threshold,
                    "direction": rule.direction,
                    "severity": rule.severity,
                    "advice": rule.advice,
                    "at": rule.triggered_at,
                }
                breaches.append(entry)
                if self.audit is not None:
                    self.audit.log(
                        "revenue.monitor_breach",
                        result="warning",
                        agent="revenue",
                        meta={"rule": rule.name, "metric": rule.metric, "value": value},
                    )
        return breaches

    def status(self) -> dict[str, Any]:
        return {
            "rules": len(self.rules),
            "watching": sorted({r.metric for r in self.rules}),
            "breached": [r.as_dict() for r in self.rules if r.last_state == "breached"],
            "rules_detail": [r.as_dict() for r in self.rules],
        }
