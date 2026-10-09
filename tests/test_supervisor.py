"""Supervisor, end-to-end pipeline, audit trail and self-verification (sections 23-28)."""

from __future__ import annotations

import pytest

from jarvis.agents.base import Agent, AgentRequest, AgentResult
from jarvis.core.confidence import Claim, Confidence
from jarvis.core.options import Dilemma
from jarvis.core.policy import CallbackApprover
from jarvis.core.risk import ActionRequest
from jarvis.core.supervisor import Supervisor, build_dilemma_from_denial
from jarvis.errors import HardDenial
from jarvis.interaction.intents import parse_intent
from jarvis.runtime import build_runtime, quick_status


class EchoAgent(Agent):
    name = "echo"
    description = "test agent"
    capabilities = ("echo",)

    def __init__(self, confidence: Confidence = Confidence.HIGH) -> None:
        super().__init__()
        self.confidence = confidence

    def run(self, request: AgentRequest) -> AgentResult:
        result = AgentResult(agent=self.name, summary=f"echo: {request.text}")
        result.add_claim(Claim.certain(f"echoed {request.text}"))
        return result


class ShakyAgent(Agent):
    name = "shaky"
    description = "returns low-confidence claims"
    capabilities = ("shaky",)

    def run(self, request: AgentRequest) -> AgentResult:
        result = AgentResult(agent=self.name, summary="not sure")
        result.add_claim(Claim(statement="probably true", confidence=Confidence.LOW))
        return result


class ExplodingAgent(Agent):
    name = "exploding"
    description = "always raises"
    capabilities = ("exploding",)

    def run(self, request: AgentRequest) -> AgentResult:
        raise RuntimeError("agent exploded")


class MissingDepAgent(Agent):
    name = "missingdep"
    description = "needs an extra that is not installed"
    capabilities = ("missingdep",)
    requires = ("a_module_that_does_not_exist",)

    def run(self, request: AgentRequest) -> AgentResult:  # pragma: no cover
        return AgentResult(agent=self.name, summary="should not run")


@pytest.fixture()
def wired(supervisor, registry) -> Supervisor:
    registry.register(EchoAgent())
    registry.register(ShakyAgent())
    registry.register(ExplodingAgent())
    registry.register(MissingDepAgent())
    supervisor.ROUTES = {**Supervisor.ROUTES, "echo": "echo", "shaky": "shaky",
                         "exploding": "exploding", "missingdep": "missingdep"}
    return supervisor


# -------------------------------------------------------------------- routing
def test_routes_to_the_named_agent(wired):
    execution = wired.handle(AgentRequest(intent="echo", text="hello"))
    assert execution.agent == "echo"
    assert execution.ok
    assert execution.result.summary == "echo: hello"


def test_unknown_intent_falls_back_to_research(wired, registry):
    from jarvis.agents.research import ResearchAgent

    registry.register(ResearchAgent())
    execution = wired.handle(AgentRequest(intent="nonexistent", text="x"))
    assert execution.agent == "research"


def test_a_route_with_no_registered_agent_is_reported(wired):
    wired.ROUTES["ghost"] = "ghost"
    execution = wired.handle(AgentRequest(intent="ghost"))
    assert not execution.ok
    assert "no agent registered" in execution.error


def test_an_agent_with_a_missing_dependency_degrades_instead_of_crashing(wired):
    execution = wired.handle(AgentRequest(intent="missingdep"))
    assert not execution.ok
    assert "not usable right now" in execution.result.summary


# ---------------------------------------------------------- self-verification
def test_a_low_confidence_result_is_not_reported_as_verified(wired):
    execution = wired.handle(AgentRequest(intent="shaky", text="x"))
    assert execution.verified is False
    assert any("below MEDIUM" in n for n in execution.verification_notes)


def test_a_high_confidence_result_is_verified(wired):
    execution = wired.handle(AgentRequest(intent="echo", text="x"))
    assert execution.verified is True


def test_a_result_with_no_claims_is_not_verified(wired, registry):
    class SilentAgent(Agent):
        name = "silent"
        capabilities = ()

        def run(self, request):
            return AgentResult(agent=self.name, summary="nothing to say")

    registry.register(SilentAgent())
    wired.ROUTES["silent"] = "silent"
    execution = wired.handle(AgentRequest(intent="silent"))
    assert execution.verified is False
    assert any("no verifiable claims" in n for n in execution.verification_notes)


def test_an_agent_exception_is_caught_and_audited(wired, audit):
    execution = wired.handle(AgentRequest(intent="exploding"))
    assert not execution.ok
    assert "agent exploded" in execution.error
    assert any(r.get("error") for r in audit.entries())


# ------------------------------------------------------------------ auditing
def test_every_request_leaves_an_audit_record(wired, audit):
    before = len(audit.entries())
    wired.handle(AgentRequest(intent="echo", text="audited"))
    entries = audit.entries()
    assert len(entries) == before + 1
    record = entries[-1]
    assert record["action"] == "request.echo"
    assert record["agent"] == "echo"
    assert record["plan"], "the plan must be recorded"
    assert record["meta"]["verified"] is True


def test_secrets_in_a_request_are_never_written_to_the_audit_log(wired, audit):
    wired.handle(AgentRequest(intent="echo", text="my key is AKIAIOSFODNN7EXAMPLE"))
    raw = audit.path.read_text(encoding="utf-8")
    assert "AKIAIOSFODNN7EXAMPLE" not in raw
    assert "REDACTED" in raw


def test_secret_scan_is_reported_in_the_checks(wired):
    execution = wired.handle(AgentRequest(intent="echo", text="token = 'abcdef1234567890'"))
    assert any("secret scan: FOUND" in c for c in execution.checks)


def test_audit_query_filters_by_agent(wired, audit):
    wired.handle(AgentRequest(intent="echo", text="a"))
    wired.handle(AgentRequest(intent="shaky", text="b"))
    assert {r["agent"] for r in audit.query(agent="echo")} == {"echo"}


def test_audit_stats_and_render(wired, audit):
    from jarvis.core.audit import summarize

    wired.handle(AgentRequest(intent="echo", text="a"))
    assert audit.stats()["total"] >= 1
    assert "echo" in summarize(audit.entries())


def test_audit_export_produces_a_readable_copy(audit, tmp_path):
    audit.log("test.action", agent="echo", result="ok", risk="low")
    path = audit.export(tmp_path / "audit.txt")
    assert path.is_file() and path.stat().st_size > 0


# ------------------------------------------------------------- action gating
def test_a_low_risk_action_is_executed_and_audited(wired, audit):
    outcome = wired.propose(ActionRequest(verb="file.read", target="notes.md"))
    assert outcome["decision"]["decision"] == "allow"
    assert outcome["audit_id"]


def test_a_high_risk_action_is_blocked_without_approval(wired):
    outcome = wired.propose(ActionRequest(verb="message.send", target="someone"))
    assert outcome["decision"]["decision"] == "deny"


def test_a_forbidden_action_raises_and_is_audited(wired, audit):
    """A refusal is the most important thing to audit; it must be recorded even
    though the call raises."""
    with pytest.raises(HardDenial):
        wired.propose(ActionRequest(verb="captcha.solve"))
    refusals = [r for r in audit.entries() if r.get("action") == "action.refused.captcha.solve"]
    assert refusals, "a hard refusal left no audit record"
    assert refusals[0]["result"] == "refused"
    assert refusals[0]["meta"]["permanent"] is True


def test_refuse_returns_the_reason_and_alternatives(wired):
    outcome = wired.refuse(ActionRequest(verb="captcha.solve"))
    assert outcome["decision"] == "hard_deny"
    assert outcome["safer_alternatives"]


def test_build_dilemma_from_a_denial_offers_a_way_out(wired):
    action = ActionRequest(verb="spam.send")
    decision = wired.policy.decide(action)
    dilemma = build_dilemma_from_denial(action, decision)
    assert isinstance(dilemma, Dilemma)
    assert dilemma.validate() == []
    assert any(o.kind == "cancel" for o in dilemma.options)


# --------------------------------------------------------------- full runtime
def test_runtime_handles_a_natural_language_request_end_to_end(runtime):
    execution = runtime.handle("What is the capital of France?")
    assert execution.request.intent == "research"
    assert execution.result is not None
    # With no search provider, the honest answer is "I don't know".
    assert not execution.result.ok
    assert "will not guess" in execution.result.summary


def test_runtime_status_reports_every_subsystem(runtime):
    status = runtime.status()
    for key in ("version", "agents", "policy", "models", "host", "memory",
                "knowledge", "audit", "compliance"):
        assert key in status, key


def test_runtime_act_is_the_only_path_to_a_side_effect(runtime):
    outcome = runtime.act("payment.transfer", "vendor", amount=500, currency="USD")
    assert outcome["decision"]["decision"] == "deny"


def test_runtime_pause_and_resume(runtime):
    assert "PAUSED" in runtime.pause()
    assert runtime.paused is True
    assert "RESUMED" in runtime.resume()


def test_runtime_emergency_stop_always_works(runtime):
    message = runtime.emergency_stop()
    assert "EMERGENCY STOP" in message
    assert runtime.host.camera_active is False


def test_runtime_settings_never_leak_secrets(runtime, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-abcdefghijklmnopqrstuvwx")
    view = runtime.settings.redacted_view()
    assert "sk-proj-abcdefghijklmnopqrstuvwx" not in str(view)
    assert "OPENAI" in view["configured_providers"]


def test_quick_status_renders(runtime):

    text = quick_status(runtime)
    assert "Jarvis" in text
    assert "autonomy" in text


def test_memory_update_happens_for_decisions(runtime):
    from jarvis.agents.revenue import Opportunity

    runtime.handle(
        "websites for clinics",
        intent="revenue",
        opportunity=Opportunity(title="Clinic websites", description="Build sites for clinics."),
    )
    assert runtime.memory.counts()["decision"] >= 1


def test_event_bus_records_activity(runtime):
    runtime.handle("hello there")
    topics = {e.topic for e in runtime.events.recent(50)}
    assert "activity" in topics


def test_agent_registry_rejects_duplicate_names(registry):
    registry.register(EchoAgent())
    with pytest.raises(ValueError):
        registry.register(EchoAgent())


def test_agent_registry_describes_availability(registry):
    registry.register(MissingDepAgent())
    described = registry.describe()["missingdep"]
    assert described["available"] is False
    assert "missing optional dependency" in described["reason"]


# --------------------------------------------------------------------------
# The emergency stop has to work, and the user has to be able to *see* that it
# worked. A stop that is invisible is not a stop.
# --------------------------------------------------------------------------


def test_emergency_stop_is_visible_in_status(runtime):
    assert runtime.run_state == "running"
    assert runtime.status()["run_state"] == "running"

    runtime.emergency_stop()
    assert runtime.run_state == "emergency_stopped"
    assert runtime.status()["run_state"] == "emergency_stopped"
    assert "EMERGENCY_STOP" in quick_status(runtime).upper()


def test_emergency_stop_refuses_new_work(runtime):
    runtime.emergency_stop()
    execution = runtime.handle("summarise something")
    assert not execution.ok
    assert "EMERGENCY STOP is active" in execution.error
    assert runtime.run_state == "emergency_stopped", "a refused request must not clear the stop"


def test_emergency_stop_is_audited(runtime, audit):
    runtime.emergency_stop()
    stops = [r for r in audit.entries() if r.get("action") == "runtime.emergency_stop"]
    assert stops, "the emergency stop left no audit record"
    assert stops[-1]["result"] == "stopped"


def test_resume_clears_the_stop_and_says_so(runtime):
    runtime.emergency_stop()
    message = runtime.resume()
    assert "RESUMED" in message
    assert "Emergency stop cleared" in message
    assert runtime.run_state == "running"
    # work is accepted again
    assert runtime.handle("hello there").result is not None


def test_pause_is_reported_separately_from_emergency_stop(runtime):
    runtime.pause()
    assert runtime.run_state == "paused"
    runtime.resume()
    assert runtime.run_state == "running"


# --------------------------------------------------------------------------
# Structural check: the intent vocabulary and the route table are maintained in
# two different modules, so they drift. This is the test that catches it.
# --------------------------------------------------------------------------


def test_every_intent_the_parser_can_emit_has_a_route():
    from jarvis.interaction.intents import INTENT_RULES

    emitted = {intent for intent, _keywords in INTENT_RULES} | {"research"}
    routed = set(Supervisor.ROUTES)
    missing = sorted(emitted - routed)
    assert not missing, (
        f"intents with no route fall through to research silently: {missing}"
    )


def test_every_routed_agent_actually_exists_in_the_registry(runtime):
    """A route pointing at an agent nobody registered is a silent dead end."""
    for intent, agent in Supervisor.ROUTES.items():
        if agent == "supervisor":
            continue
        assert runtime.registry.get(agent) is not None, (
            f"intent {intent!r} routes to agent {agent!r}, which is not registered"
        )


@pytest.mark.parametrize(
    "text,expected_agent",
    [
        ("review this code", "coding"),
        ("fix this code please", "coding"),
        ("run the tests", "coding"),
        ("remind me about the meeting", "productivity"),
        ("add a task to call the bank", "productivity"),
        ("make me an excel sheet", "documents"),
        ("build me an ecommerce website", "webbuilder"),
    ],
)
def test_natural_language_reaches_the_right_agent(runtime, text, expected_agent):
    """Regression: 'review this code' used to be answered by the research agent."""
    request = parse_intent(text)
    assert request.intent != "research", f"{text!r} was not parsed as a specific intent"
    assert runtime.supervisor.route(request) == expected_agent


# --------------------------------------------------------------------------
# The lock has to survive a restart. A stop that a new process forgets means
# `jarvis lock` in one shell is undone by simply opening another.
# --------------------------------------------------------------------------


def test_the_emergency_stop_is_written_to_disk(runtime, settings):
    runtime.emergency_stop()
    state_file = settings.home / "state.json"
    assert state_file.is_file(), "the lock was not persisted"
    import json

    assert json.loads(state_file.read_text(encoding="utf-8"))["emergency_stopped"] is True


def test_a_new_runtime_inherits_the_lock(settings, audit, runtime):
    """The regression this guards: the flag used to be in-memory only, so a new
    process started in the running state and happily did the work."""
    runtime.emergency_stop()

    fresh = build_runtime(settings, approver=CallbackApprover(lambda _d: False))
    try:
        assert fresh.run_state == "emergency_stopped"
        assert not fresh.handle("do something").ok
        fresh.resume()
    finally:
        fresh.shutdown()

    # ...and clearing it is persisted too
    third = build_runtime(settings, approver=CallbackApprover(lambda _d: False))
    try:
        assert third.run_state == "running"
    finally:
        third.shutdown()


def test_an_unreadable_state_file_fails_closed(settings, tmp_path):
    """If the state file cannot be read, assume stopped - not running.

    Guessing "running" after a crash is the dangerous guess.
    """
    settings.ensure_dirs()
    (settings.home / "state.json").write_text("{ this is not json", encoding="utf-8")

    rt = build_runtime(settings, approver=CallbackApprover(lambda _d: False))
    try:
        assert rt.run_state == "emergency_stopped", "an unreadable state file must fail closed"
        assert not rt.handle("do something").ok
    finally:
        rt.shutdown()
