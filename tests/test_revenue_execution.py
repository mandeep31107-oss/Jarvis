"""Tests for revenue plan execution and monitoring.

These use the real :class:`~jarvis.core.policy.PolicyEngine` rather than a stub,
because the whole point of the executor is that it does not have authority of its
own. Testing it against a permissive fake would prove nothing about the gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.core.audit import AuditLog
from jarvis.core.policy import Approval, CallbackApprover, PolicyEngine
from jarvis.core.risk import RiskEngine, RiskLevel
from jarvis.revenue.execution import (
    HUMAN_ONLY,
    ExecutionPlan,
    Executor,
    Monitor,
    MonitorRule,
    Step,
    StepState,
)

# --------------------------------------------------------------------- helpers


def engine(
    tmp_path: Path,
    *,
    autonomy: str = "review",
    grant: bool = True,
    ledger: bool = True,
) -> tuple[Executor, AuditLog]:
    audit = AuditLog(tmp_path / "audit.jsonl")
    policy = PolicyEngine(
        RiskEngine(),
        autonomy=autonomy,
        approver=CallbackApprover(lambda decision: grant),
    )
    return (
        Executor(policy, audit, ledger=tmp_path / "ledger.json" if ledger else None),
        audit,
    )


def approved_plan(*steps: Step) -> ExecutionPlan:
    plan = ExecutionPlan(title="Launch the template pack", opportunity="digital-product")
    for step in steps:
        plan.add(step)
    plan.approval = {"granted": True, "approver": "user", "plan_id": plan.plan_id}
    return plan


def low_step(**kwargs: object) -> Step:
    """A step that the policy engine will let through under 'review' autonomy."""
    defaults: dict[str, object] = {
        "verb": "app.install",
        "title": "Set up the tooling",
        "risk": RiskLevel.LOW,
        "handler": lambda step: {"message": "done"},
    }
    defaults.update(kwargs)
    return Step(**defaults)  # type: ignore[arg-type]


# -------------------------------------------------------------------- approval


def test_an_unapproved_plan_executes_nothing(tmp_path: Path) -> None:
    """The core invariant: no approval, no action."""
    executor, _audit = engine(tmp_path)
    ran = {"called": False}

    def handler(step: Step) -> dict[str, object]:
        ran["called"] = True
        return {}

    plan = ExecutionPlan(title="Unapproved")
    plan.add(low_step(handler=handler))

    report = executor.run(plan)

    assert report.ran is False
    assert ran["called"] is False
    assert plan.steps[0].state is StepState.PENDING
    assert "not been approved" in report.reason


def test_approval_is_recorded_with_who_and_when(tmp_path: Path) -> None:
    executor, audit = engine(tmp_path)
    plan = ExecutionPlan(title="Recorded")

    executor.approve(plan, approver="owner", note="go ahead")

    assert plan.approved is True
    assert plan.approval["approver"] == "owner"
    assert plan.approval["note"] == "go ahead"
    assert plan.approval["at"], "the approval must be timestamped"
    assert any(e["action"] == "revenue.plan_approved" for e in audit.entries())


def test_approval_is_written_to_the_ledger_immediately(tmp_path: Path) -> None:
    """An approval that only exists in memory cannot be audited later."""
    executor, _audit = engine(tmp_path)
    plan = ExecutionPlan(title="Durable")

    executor.approve(plan, approver="owner")

    on_disk = executor.load()
    assert len(on_disk) == 1
    assert on_disk[0]["approved"] is True
    assert on_disk[0]["approval"]["approver"] == "owner"


# -------------------------------------------------------------------- execution


def test_an_approved_plan_runs_its_steps(tmp_path: Path) -> None:
    executor, _audit = engine(tmp_path)
    done: list[str] = []

    plan = approved_plan(
        low_step(title="First", handler=lambda s: done.append(s.title) or {"message": "first"}),
        low_step(title="Second", handler=lambda s: done.append(s.title) or {"message": "second"}),
    )

    report = executor.run(plan)

    assert report.ran is True
    assert done == ["First", "Second"]
    assert report.counts["done"] == 2


def test_steps_run_in_the_order_they_were_added(tmp_path: Path) -> None:
    executor, _audit = engine(tmp_path)
    order: list[str] = []

    plan = approved_plan(
        *[low_step(title=f"step{i}", handler=lambda s, i=i: order.append(f"step{i}") or {})
          for i in range(4)]
    )

    executor.run(plan)

    assert order == ["step0", "step1", "step2", "step3"]


def test_a_step_that_raises_is_recorded_as_failed_with_the_reason(tmp_path: Path) -> None:
    executor, audit = engine(tmp_path)

    def boom(step: Step) -> dict[str, object]:
        raise ValueError("the template directory is read-only")

    plan = approved_plan(low_step(title="Broken", handler=boom))

    report = executor.run(plan)

    assert plan.steps[0].state is StepState.FAILED
    assert "read-only" in plan.steps[0].message
    assert report.counts["failed"] == 1
    assert any(e["action"] == "revenue.step_failed" for e in audit.entries())


def test_a_step_with_no_handler_is_blocked_not_silently_dropped(tmp_path: Path) -> None:
    """Silently skipping would leave the user believing it happened."""
    executor, _audit = engine(tmp_path)
    plan = approved_plan(Step(verb="app.install", title="No handler", risk=RiskLevel.LOW))

    executor.run(plan)

    assert plan.steps[0].state is StepState.BLOCKED_ON_USER
    assert "no way to carry out" in plan.steps[0].message


def test_an_already_finished_step_is_not_run_again(tmp_path: Path) -> None:
    """Retrying a completed irreversible step could double its effect."""
    executor, _audit = engine(tmp_path)
    calls: list[str] = []
    step = low_step(title="Once", handler=lambda s: calls.append("ran") or {})
    step.state = StepState.DONE

    executor.run(approved_plan(step))

    assert calls == []


def test_completed_steps_are_reported_with_their_results(tmp_path: Path) -> None:
    executor, _audit = engine(tmp_path)
    plan = approved_plan(low_step(handler=lambda s: {"message": "wrote 6 templates", "files": 6}))

    report = executor.run(plan)

    assert report.steps[0]["result"]["files"] == 6
    assert "wrote 6 templates" in report.steps[0]["message"]


# ---------------------------------------------------------------- policy gating


def test_a_step_the_policy_refuses_stops_the_whole_run(tmp_path: Path) -> None:
    """Continuing past a refusal would make the gate decorative."""
    executor, _audit = engine(tmp_path, grant=False)
    reached: list[str] = []

    plan = approved_plan(
        low_step(title="Gate", handler=lambda s: reached.append("gate") or {}),
        low_step(title="After", handler=lambda s: reached.append("after") or {}),
    )

    report = executor.run(plan)

    assert reached == []
    assert report.counts["done"] == 0
    assert "stopped at" in report.reason


def test_a_denied_high_risk_step_is_left_for_the_user(tmp_path: Path) -> None:
    executor, _audit = engine(tmp_path, grant=False)
    plan = approved_plan(Step(verb="app.install", title="Needs approval", risk=RiskLevel.HIGH))

    executor.run(plan)

    assert plan.steps[0].state is StepState.BLOCKED_ON_USER
    assert "policy" in plan.steps[0].message


def test_a_forbidden_action_is_refused_even_inside_an_approved_plan(tmp_path: Path) -> None:
    """Approving a plan is not approving every consequence of it."""
    executor, audit = engine(tmp_path, grant=True)
    plan = approved_plan(Step(verb="auth.bypass", title="Get past the login", risk=RiskLevel.CRITICAL))

    executor.run(plan)

    assert plan.steps[0].state is StepState.FAILED
    assert plan.steps[0].attempts == 1
    assert any(e["action"] == "revenue.step_refused" for e in audit.entries())


def test_the_engine_asks_about_every_step_individually(tmp_path: Path) -> None:
    """One approval for the plan must not cover each step silently."""
    asked: list[str] = []
    audit = AuditLog(tmp_path / "audit.jsonl")

    def approver(step: object) -> Approval:
        asked.append(step.title)  # type: ignore[attr-defined]
        return Approval(granted=True, approver="user", note="ok")

    policy = PolicyEngine(RiskEngine(), autonomy="ask", approver=CallbackApprover(lambda d: True))
    executor = Executor(policy, audit, ledger=tmp_path / "ledger.json")
    plan = approved_plan(
        low_step(title="A", handler=lambda s: {}),
        low_step(title="B", handler=lambda s: {}),
    )

    executor.run(plan, approver=lambda action, step: approver(step))

    assert asked == ["A", "B"]


# ------------------------------------------------------------- human-only steps


@pytest.mark.parametrize("verb", sorted(HUMAN_ONLY))
def test_steps_involving_money_or_identity_are_never_attempted(verb: str) -> None:
    """Not faked, not automated: reported as needing a person."""
    step = Step(verb=verb, title="Human step", risk=RiskLevel.HIGH)

    assert step.needs_a_human is True


def test_a_human_only_step_is_blocked_even_when_the_plan_is_approved(tmp_path: Path) -> None:
    executor, _audit = engine(tmp_path)
    attempted: list[str] = []
    step = Step(
        verb="payment.transfer",
        title="Send the money",
        risk=RiskLevel.HIGH,
        irreversible=True,
        handler=lambda s: attempted.append("no") or {},
    )

    executor.run(approved_plan(step))

    assert attempted == []
    assert step.state is StepState.BLOCKED_ON_USER
    assert "needs you" in step.message


def test_the_report_tells_the_user_how_many_steps_need_them(tmp_path: Path) -> None:
    executor, _audit = engine(tmp_path)
    plan = approved_plan(
        low_step(title="Automatable", handler=lambda s: {}),
        Step(verb="payment.collect", title="Take payment", risk=RiskLevel.HIGH),
        Step(verb="account.create", title="Open the account", risk=RiskLevel.HIGH),
    )

    report = executor.run(plan)

    assert report.counts["blocked_on_user"] == 2
    assert "2 step(s) need you" in report.next_action


# ------------------------------------------------------------------- persistence


def test_the_ledger_survives_a_new_executor(tmp_path: Path) -> None:
    executor, _audit = engine(tmp_path)
    plan = approved_plan(low_step(title="Persisted", handler=lambda s: {}))
    executor.run(plan)

    reopened = Executor(PolicyEngine(RiskEngine(), autonomy="review"), None, ledger=tmp_path / "ledger.json")

    records = reopened.load()
    assert len(records) == 1
    assert records[0]["counts"]["done"] == 1


def test_rerunning_a_plan_updates_its_record_rather_than_duplicating_it(tmp_path: Path) -> None:
    executor, _audit = engine(tmp_path)
    plan = approved_plan(low_step(title="Once", handler=lambda s: {}))

    executor.run(plan)
    executor.run(plan)

    assert len(executor.load()) == 1


def test_a_corrupt_ledger_is_quarantined_not_overwritten(tmp_path: Path) -> None:
    """Erasing the audit trail to make the run work is not acceptable."""
    ledger = tmp_path / "ledger.json"
    ledger.write_text("{ this is not valid json")
    executor, _audit = engine(tmp_path)

    executor.approve(ExecutionPlan(title="After corruption"), approver="user")

    assert ledger.with_suffix(".corrupt").exists()
    assert len(executor.load()) == 1


def test_an_executor_with_no_ledger_still_runs(tmp_path: Path) -> None:
    executor, _audit = engine(tmp_path, ledger=False)
    plan = approved_plan(low_step(handler=lambda s: {"message": "ok"}))

    assert executor.run(plan).counts["done"] == 1
    assert executor.load() == []


# --------------------------------------------------------------------- reporting


def test_the_report_serialises_to_json(tmp_path: Path) -> None:
    executor, _audit = engine(tmp_path)
    plan = approved_plan(low_step(handler=lambda s: {}))

    json.dumps(executor.run(plan).as_dict())


def test_a_plan_with_no_steps_reports_that_it_finished(tmp_path: Path) -> None:
    executor, _audit = engine(tmp_path)

    report = executor.run(approved_plan())

    assert report.ran is True
    assert report.next_action == "Every step completed."


# ------------------------------------------------------------------ monitoring


def test_a_breached_threshold_is_reported() -> None:
    monitor = Monitor(
        [MonitorRule(name="CAC too high", metric="cac", threshold=40.0, direction="above", advice="pause spend")]
    )

    breaches = monitor.check({"cac": 55.0})

    assert len(breaches) == 1
    assert breaches[0]["rule"] == "CAC too high"
    assert breaches[0]["advice"] == "pause spend"


def test_a_value_within_limits_produces_no_report() -> None:
    """An everything-is-fine report is noise at the moment it matters."""
    monitor = Monitor([MonitorRule(name="CAC", metric="cac", threshold=40.0, direction="above")])

    assert monitor.check({"cac": 12.0}) == []


def test_a_downward_threshold_detects_a_falling_metric() -> None:
    monitor = Monitor(
        [MonitorRule(name="Margin collapsing", metric="margin", threshold=0.2, direction="below")]
    )

    assert len(monitor.check({"margin": 0.05})) == 1
    assert monitor.check({"margin": 0.4}) == []


def test_a_metric_that_is_not_being_measured_is_not_reported_as_breached() -> None:
    """Absence of data is not the same as a good reading."""
    monitor = Monitor([MonitorRule(name="Churn", metric="churn", threshold=0.05, direction="above")])

    assert monitor.check({"something_else": 99.0}) == []


def test_the_monitor_records_the_last_state_of_every_rule() -> None:
    monitor = Monitor(
        [
            MonitorRule(name="CAC", metric="cac", threshold=40.0, direction="above"),
            MonitorRule(name="Churn", metric="churn", threshold=0.05, direction="above"),
        ]
    )

    monitor.check({"cac": 55.0, "churn": 0.01})

    status = monitor.status()
    assert [b["name"] for b in status["breached"]] == ["CAC"]
    assert status["rules"] == 2


def test_breaches_are_audited(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    monitor = Monitor(
        [MonitorRule(name="CAC", metric="cac", threshold=40.0, direction="above")], audit
    )

    monitor.check({"cac": 90.0})

    assert any(e["action"] == "revenue.monitor_breach" for e in audit.entries())


def test_an_invalid_threshold_direction_is_rejected() -> None:
    with pytest.raises(ValueError, match="direction"):
        MonitorRule(name="x", metric="y", threshold=1.0, direction="sideways")


def test_monitor_state_serialises_to_json() -> None:
    monitor = Monitor([MonitorRule(name="CAC", metric="cac", threshold=40.0)])
    monitor.check({"cac": 1.0})

    json.dumps(monitor.status())


def test_a_rule_tracks_its_own_last_reading() -> None:
    rule = MonitorRule(name="CAC", metric="cac", threshold=40.0)

    rule.evaluate(10.0)
    assert rule.last_value == 10.0
    assert rule.last_state == "ok"
    assert rule.triggered_at == ""

    rule.evaluate(80.0)
    assert rule.last_state == "breached"
    assert rule.triggered_at != ""


# ------------------------------------------------------- agent / runtime wiring


def _opportunity():
    from jarvis.agents.revenue import Opportunity

    return Opportunity(
        title="Invoice template pack",
        description="Sell freelance invoice templates",
        price_per_unit=29.0,
        clients_per_month_at_maturity=40,
        monthly_fixed_cost=20.0,
        variable_cost_per_unit=1.0,
        competition=3.0,
    )


def test_an_assessment_becomes_a_plan_of_concrete_steps(tmp_path: Path) -> None:
    from jarvis.agents.revenue import RevenueAgent

    agent = RevenueAgent()
    plan = agent.plan_from_assessment(agent.analyze(_opportunity()))

    assert plan.steps, "an assessment must produce something actionable"
    assert plan.opportunity == "Invoice template pack"
    assert all(step.title for step in plan.steps)


def test_requirements_about_money_or_contracts_are_marked_as_needing_a_person(tmp_path: Path) -> None:
    """Automating payment or signature steps would breach platform rules or fail."""
    from jarvis.agents.revenue import RevenueAgent

    agent = RevenueAgent()
    plan = agent.plan_from_assessment(agent.analyze(_opportunity()))

    human = [s for s in plan.steps if s.needs_a_human]
    assert human, "a launch plan always involves payment or contracts"
    assert all(s.risk is not RiskLevel.LOW for s in human)


def test_an_unapproved_plan_still_executes_nothing_through_the_agent(tmp_path: Path) -> None:
    from jarvis.agents.revenue import RevenueAgent

    agent = RevenueAgent()
    plan = agent.plan_from_assessment(agent.analyze(_opportunity()))
    executor, _audit = engine(tmp_path)

    report = executor.run(plan)

    assert report.ran is False
    assert report.counts["done"] == 0


def test_monitor_thresholds_come_from_the_model_not_from_invented_defaults() -> None:
    """The break-even month is where the plan stops working, not a round number."""
    from jarvis.agents.revenue import RevenueAgent

    agent = RevenueAgent()
    assessment = agent.analyze(_opportunity())
    rules = agent.monitor_rules(assessment)

    cac_rule = next(r for r in rules if r.metric == "cac")
    assert cac_rule.threshold == pytest.approx(assessment.opportunity.price_per_unit * 0.5)

    if assessment.break_even_month is not None:
        months = next(r for r in rules if r.metric == "months_elapsed")
        assert months.threshold == float(assessment.break_even_month)


def test_the_runtime_gives_the_revenue_agent_a_real_executor() -> None:
    """It must share the runtime's policy engine, not hold authority of its own."""
    import tempfile
    from pathlib import Path as _Path

    from jarvis.config import Settings
    from jarvis.revenue.execution import Executor, Monitor
    from jarvis.runtime import build_runtime

    with tempfile.TemporaryDirectory() as home:
        settings = Settings(home=_Path(home))
        runtime = build_runtime(settings)
        agent = runtime.registry.get("revenue")

        assert isinstance(agent.executor, Executor)
        assert isinstance(agent.monitor, Monitor)
        #: The whole point: same engine, same audit trail.
        assert agent.executor.policy is runtime.policy
        assert agent.executor.audit is runtime.audit


def test_the_ledger_is_written_under_the_runtime_home() -> None:
    import tempfile
    from pathlib import Path as _Path

    from jarvis.config import Settings
    from jarvis.runtime import build_runtime

    with tempfile.TemporaryDirectory() as home:
        settings = Settings(home=_Path(home))
        runtime = build_runtime(settings)
        agent = runtime.registry.get("revenue")
        plan = agent.plan_from_assessment(agent.analyze(_opportunity()))

        agent.executor.approve(plan, approver="owner")

        assert (_Path(home) / "revenue_ledger.json").exists()
