"""Supervisor agent (spec sections 23, 24, 26).

The supervisor is the only component that performs actions. Agents propose; the
supervisor routes, checks, gates, executes, verifies, reports and audits:

    PLAN -> CHECK -> EXECUTE -> VERIFY -> REPORT

Section 26 is explicit that if confidence is low, the agent must not guess. That
is implemented as a hard rule in :meth:`Supervisor.verify`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.agents.base import Agent, AgentRequest, AgentResult
from jarvis.core.audit import AuditLog
from jarvis.core.confidence import Claim, Confidence
from jarvis.core.events import EventBus
from jarvis.core.options import Dilemma, Option
from jarvis.core.policy import PolicyEngine
from jarvis.core.risk import ActionRequest, RiskLevel
from jarvis.errors import HardDenial
from jarvis.util.ids import new_id

log = logging.getLogger("jarvis.supervisor")


@dataclass
class Execution:
    """One end-to-end run through the supervisor."""

    request: AgentRequest
    agent: str = ""
    plan: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    result: AgentResult | None = None
    decision: Any = None
    verified: bool = False
    verification_notes: list[str] = field(default_factory=list)
    audit_id: str = ""
    id: str = field(default_factory=lambda: new_id("exec"))
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.result and self.result.ok and not self.error)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "request": {
                "intent": self.request.intent,
                "text": self.request.text,
                "language": self.request.language,
            },
            "agent": self.agent,
            "plan": list(self.plan),
            "checks": list(self.checks),
            "decision": self.decision.as_dict() if self.decision else None,
            "verified": self.verified,
            "verification_notes": list(self.verification_notes),
            "result": self.result.as_dict() if self.result else None,
            "error": self.error,
            "audit_id": self.audit_id,
        }


class AgentRegistry:
    """Name -> agent, with capability lookup for routing."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> Agent:
        if agent.name in self._agents:
            raise ValueError(f"an agent named '{agent.name}' is already registered")
        self._agents[agent.name] = agent
        return agent

    def get(self, name: str) -> Agent | None:
        return self._agents.get(name)

    @property
    def names(self) -> list[str]:
        return sorted(self._agents)

    def all(self) -> list[Agent]:
        return [self._agents[n] for n in self.names]

    def for_capability(self, capability: str) -> list[Agent]:
        return [a for a in self.all() if capability in a.capabilities]

    def describe(self) -> dict[str, Any]:
        out = {}
        for name in self.names:
            agent = self._agents[name]
            ok, reason = agent.available()
            out[name] = {
                "description": agent.description,
                "capabilities": list(agent.capabilities),
                "available": ok,
                "reason": reason,
            }
        return out


class Supervisor:
    """Coordinates the specialized agents and enforces the safety path."""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        policy: PolicyEngine,
        audit: AuditLog,
        events: EventBus | None = None,
        router: Callable[[AgentRequest], str] | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.audit = audit
        self.events = events
        self._router = router or self._default_router

    # ------------------------------------------------------------------ routing
    #: Intent -> agent name. Anything unmapped falls back to research.
    #: intent name -> agent name.
    #:
    #: Every intent the parser can emit MUST appear here. A missing key falls
    #: through to "research" silently, which is how the coding and productivity
    #: agents became unreachable from natural language: the parser emitted
    #: "coding"/"productivity", this table only had the aliases "code" and
    #: "task", and every such request was quietly answered by research instead.
    #: tests/test_supervisor.py asserts the two sets stay in step.
    ROUTES: dict[str, str] = {
        "research": "research",
        "coding": "coding",
        "productivity": "productivity",
        "revenue": "revenue",
        "compliance": "compliance",
        "code": "coding",
        "review": "coding",
        "test": "coding",
        "build": "webbuilder",
        "website": "webbuilder",
        "document": "documents",
        "spreadsheet": "documents",
        "report": "documents",
        "computer": "computer",
        "vision": "vision",
        "camera": "vision",
        "memory": "memory",
        "remember": "memory",
        "recall": "memory",
        "task": "productivity",
        "plan": "productivity",
        "reminder": "productivity",
        "security": "security",
        "status": "supervisor",
    }

    def _default_router(self, request: AgentRequest) -> str:
        agent = self.ROUTES.get(request.intent)
        if agent and self.registry.get(agent) is not None:
            return agent
        if request.intent in {"status", "help"}:
            return "supervisor"
        return "research"

    def route(self, request: AgentRequest) -> str:
        return self._router(request)

    # ------------------------------------------------------------------ execute
    def handle(self, request: AgentRequest) -> Execution:
        """PLAN -> CHECK -> EXECUTE -> VERIFY -> REPORT, with an audit record."""
        execution = Execution(request=request)
        request.request_id = request.request_id or execution.id
        agent_name = self.route(request)
        execution.agent = agent_name
        self._activity("supervisor", f"routing '{request.intent}' to {agent_name}")

        # --- PLAN ---------------------------------------------------------
        execution.plan = [
            f"interpret the request as intent '{request.intent}'",
            f"route to the {agent_name} agent",
            "run policy, risk and secret checks before any action",
            "execute, then verify the output before reporting",
        ]

        # --- CHECK --------------------------------------------------------
        execution.checks.append(self._check_secret(request))
        if agent_name == "supervisor":
            execution.result = self._status_result(request)
        else:
            agent = self.registry.get(agent_name)
            if agent is None:
                execution.error = f"no agent registered for '{agent_name}'"
                execution.checks.append(f"FAILED: {execution.error}")
                self._audit(execution, error=execution.error)
                return execution
            ok, reason = agent.available()
            execution.checks.append(
                f"agent availability: {'ok' if ok else 'degraded - ' + reason}"
            )
            if not ok:
                execution.result = AgentResult(
                    agent=agent_name,
                    ok=False,
                    summary=f"The {agent_name} agent is not usable right now: {reason}",
                )
                self._audit(execution)
                return execution

            # --- EXECUTE --------------------------------------------------
            self._activity(agent_name, f"working on: {request.text[:80] or request.intent}")
            try:
                execution.result = agent.run(request)
            except HardDenial as exc:
                execution.error = f"refused: {exc}"
                execution.checks.append(f"HARD DENIAL ({exc.rule}): {exc}")
                self._audit(execution, error=str(exc), risk=RiskLevel.CRITICAL.value)
                return execution
            except Exception as exc:  # noqa: BLE001 - the audit log must record it
                execution.error = f"{type(exc).__name__}: {exc}"
                log.exception("agent %s failed", agent_name)
                self._audit(execution, error=execution.error)
                return execution

        # --- VERIFY -------------------------------------------------------
        execution.verified, execution.verification_notes = self.verify(execution.result)

        # --- REPORT -------------------------------------------------------
        self._audit(execution)
        if execution.result is not None and execution.result.dilemmas:
            for dilemma in execution.result.dilemmas:
                self._activity("supervisor", "waiting for the user to choose an option")
                self.audit.log(
                    "dilemma.presented",
                    agent=agent_name,
                    user_request=request.user_request or request.text,
                    risk=RiskLevel.LOW.value,
                    result=dilemma.recommendation,
                    meta={"options": [o.label for o in dilemma.options]},
                )
        return execution

    def verify(self, result: AgentResult | None) -> tuple[bool, list[str]]:
        """Section 26 self-verification. Low confidence is not silently promoted."""
        notes: list[str] = []
        if result is None:
            return False, ["no result to verify"]
        if not result.claims:
            notes.append("the agent produced no verifiable claims")
            return False, notes
        weakest = result.confidence
        notes.append(f"weakest claim confidence: {weakest.value}")
        if weakest.rank < Confidence.MEDIUM.rank:
            notes.append(
                "confidence is below MEDIUM - the answer is reported as unverified, not as fact"
            )
            return False, notes
        low = [c for c in result.claims if c.confidence is Confidence.LOW]
        if low:
            notes.append(f"{len(low)} claim(s) at LOW confidence are flagged in the output")
        unsourced = [c for c in result.claims if not c.deterministic and not c.sources]
        if unsourced:
            notes.append(f"{len(unsourced)} claim(s) have no cited source")
        return True, notes

    # ------------------------------------------------------------------ actions
    def propose(self, action: ActionRequest, *, preapproved: Any = None) -> dict[str, Any]:
        """Run an action through the risk/policy gate. This is the only path in."""
        try:
            decision = self.policy.gate(action, preapproved=preapproved)
        except HardDenial as exc:
            # A refusal is the single most important thing to audit; it must be
            # recorded *before* the exception propagates, never swallowed by it.
            self.audit.log(
                f"action.refused.{action.verb}",
                user_request=action.request_id,
                interpretation=action.describe(),
                risk=RiskLevel.CRITICAL.value,
                policy_checks=[f"HARD DENIAL ({exc.rule}): {exc}"],
                result="refused",
                error=str(exc),
                agent="supervisor",
                meta={"permanent": True, "rule": exc.rule},
            )
            raise
        record = self.audit.log(
            f"action.{action.verb}",
            user_request=action.request_id,
            interpretation=action.describe(),
            risk=decision.assessment.level.value,
            policy_checks=[decision.reason],
            approval=("granted" if decision.allowed else "not required" if decision.decision == "allow" else "denied"),
            result="executed" if decision.allowed else "blocked",
            agent="supervisor",
            meta={"category": decision.assessment.category.value},
        )
        return {"decision": decision.as_dict(), "audit_id": record.id}

    def refuse(self, action: ActionRequest) -> dict[str, Any]:
        """Explicit refusal path - returns the reason and the safer alternatives."""
        decision = self.policy.decide(action)
        self.audit.log(
            f"action.refused.{action.verb}",
            interpretation=action.describe(),
            risk=decision.assessment.level.value,
            policy_checks=[decision.reason],
            result="refused",
            agent="supervisor",
        )
        return decision.as_dict()

    # ------------------------------------------------------------------ status
    def _status_result(self, request: AgentRequest) -> AgentResult:
        result = AgentResult(agent="supervisor", summary="Jarvis status")
        result.data["agents"] = self.registry.describe()
        result.data["policy"] = self.policy.describe()
        result.add_claim(Claim.certain(f"{len(self.registry.names)} agent(s) registered."))
        return result

    # ------------------------------------------------------------------ helpers
    def _check_secret(self, request: AgentRequest) -> str:
        from jarvis.util import redaction

        blob = f"{request.text} {request.params}"
        if redaction.has_secret(blob):
            return "secret scan: FOUND - the request contains credential-shaped text"
        return "secret scan: clean"

    def _activity(self, agent: str, message: str) -> None:
        if self.events is not None:
            self.events.publish("activity", agent=agent, message=message)

    def _audit(
        self, execution: Execution, *, error: str = "", risk: str = RiskLevel.LOW.value
    ) -> None:
        result = execution.result
        record = self.audit.log(
            f"request.{execution.request.intent}",
            user_request=execution.request.user_request or execution.request.text,
            interpretation=f"intent={execution.request.intent} agent={execution.agent}",
            plan=execution.plan,
            policy_checks=execution.checks,
            risk=risk,
            result=(result.summary if result else ""),
            error=error,
            agent=execution.agent,
            task_id=execution.id,
            meta={
                "confidence": result.confidence.value if result else "unknown",
                "verified": execution.verified,
                "verification": execution.verification_notes,
                "language": execution.request.language,
            },
        )
        execution.audit_id = record.id


def option_from_decision(decision: Any) -> Option | None:
    """Turn a policy decision into a user-facing option, for the CLI."""
    if decision is None:
        return None
    return Option(
        label="",
        title=decision.action.describe(),
        risk=decision.assessment.level,
        expected_result=decision.reason,
        compliance="blocked" if not decision.allowed else "compliant",
    )


def build_dilemma_from_denial(action: ActionRequest, decision: Any) -> Dilemma:
    """The user-facing shape of a hard refusal (spec sections 5 and 7)."""
    dilemma = Dilemma(
        situation=f"You asked Jarvis to: {action.describe()}",
        problem=decision.reason,
        risk="Performing this could be unlawful, breach a platform's terms, or harm someone.",
    )
    for i, alternative in enumerate(decision.safer_alternatives or [], start=1):
        dilemma.add(
            Option(
                label=chr(ord("A") + i - 1),
                title=alternative,
                kind="safe",
                expected_result="Achieves the underlying goal within the rules.",
                risk=RiskLevel.LOW,
                cost="varies",
                time="varies",
                compliance="compliant",
            )
        )
    return dilemma.cancel_option("Do nothing.")
