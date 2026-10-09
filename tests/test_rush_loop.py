"""Rush mode (spec section 22) and always-active mode (spec sections 20, 21)."""

from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest

from jarvis.core.loop import AlwaysActiveLoop
from jarvis.core.risk import RiskLevel
from jarvis.core.rush import MAX_RUSH_TASKS, apply_rush, plan_rush
from jarvis.core.tasks import Priority, Step, Task, TaskManager, TaskState
from jarvis.util.clock import utcnow


def _task(title: str, *, meta: dict | None = None, **priority) -> Task:
    return Task(
        title=title,
        priority=Priority(**priority),
        meta=meta or {},
        steps=[Step("work", lambda _ctx: "done")],
    )


@pytest.fixture()
def tasks() -> TaskManager:
    return TaskManager()


# ------------------------------------------------------------------ rush mode


def test_the_highest_scoring_work_that_fits_is_planned_first(tasks):
    now = utcnow()
    urgent = _task("Send the invoice", deadline=now + timedelta(minutes=10), user_priority=9)
    soon = _task("Refactor the parser", user_priority=3)
    tasks.submit(soon)
    tasks.submit(urgent)

    plan = plan_rush(tasks, budget_minutes=30)
    assert [i.task.title for i in plan.now] == ["Send the invoice", "Refactor the parser"]


def test_work_that_does_not_fit_the_budget_is_deferred_not_started(tasks):
    tasks.submit(_task("Quick note", user_priority=8))
    tasks.submit(_task("Long report", meta={"estimated_minutes": 120}, user_priority=7))

    plan = plan_rush(tasks, budget_minutes=30)
    assert [i.task.title for i in plan.now] == ["Quick note"]
    assert [i.task.title for i in plan.deferred] == ["Long report"]
    assert "half-done" in plan.deferred[0].reason


def test_a_critical_task_is_never_accelerated_by_urgency(tasks):
    """The whole point of rush mode's limits.

    This task has the highest user priority AND a deadline in two minutes. Rush
    mode still refuses to run it, because it needs approval - and the fastest
    thing Jarvis can do with a task that needs approval is not to do it
    unapproved.
    """
    tasks.submit(
        _task(
            "Move money to the new account",
            risk=RiskLevel.CRITICAL,
            user_priority=10,
            deadline=utcnow() + timedelta(minutes=2),
        )
    )
    tasks.submit(_task("Send the invoice", user_priority=2))

    plan = plan_rush(tasks, budget_minutes=60)
    assert [i.task.title for i in plan.needs_you] == ["Move money to the new account"]
    assert [i.task.title for i in plan.now] == ["Send the invoice"]
    assert "does not skip approvals" in plan.needs_you[0].reason


@pytest.mark.parametrize("level", [RiskLevel.HIGH, RiskLevel.CRITICAL])
def test_both_high_and_critical_go_to_needs_you(tasks, level):
    tasks.submit(_task("Sensitive thing", risk=level, user_priority=10))
    plan = plan_rush(tasks, budget_minutes=60)
    assert plan.now == []
    assert len(plan.needs_you) == 1


def test_rush_mode_plans_at_most_a_handful_of_tasks(tasks):
    for i in range(8):
        tasks.submit(_task(f"Small task {i}", user_priority=5))
    plan = plan_rush(tasks, budget_minutes=600)
    assert len(plan.now) == MAX_RUSH_TASKS
    assert len(plan.deferred) == 8 - MAX_RUSH_TASKS


def test_a_rush_plan_cancels_nothing(tasks):
    """Deferring is not dropping. Cancelling would lose the user's work simply
    because they were in a hurry."""
    for i in range(5):
        tasks.submit(_task(f"Task {i}", user_priority=5))
    plan = plan_rush(tasks, budget_minutes=15)
    before = {t.id: t.state for t in tasks.tasks}

    apply_rush(tasks, plan)

    assert {t.id: t.state for t in tasks.tasks} == before
    assert all(t.state is TaskState.PENDING for t in tasks.tasks)


def test_applying_a_plan_really_reorders_the_queue(tasks):
    planned = _task("Planned", user_priority=5)
    deferred = _task("Deferred", meta={"estimated_minutes": 200}, user_priority=5)
    tasks.submit(deferred)
    tasks.submit(planned)

    plan = plan_rush(tasks, budget_minutes=20)
    apply_rush(tasks, plan)

    assert planned.priority.user_priority > deferred.priority.user_priority
    assert [t.id for t in tasks.ready()][0] == planned.id


def test_a_zero_budget_plans_nothing_but_still_reports_everything(tasks):
    tasks.submit(_task("Something", user_priority=5))
    plan = plan_rush(tasks, budget_minutes=0)
    assert plan.now == []
    assert plan.deferred or plan.needs_you
    assert "nothing is queued" not in plan.render()


def test_an_empty_queue_says_so(tasks):
    plan = plan_rush(tasks, budget_minutes=30)
    assert plan.all() == []
    assert "nothing is queued" in plan.render()


def test_the_plan_serialises(tasks):
    tasks.submit(_task("Something", user_priority=5))
    data = plan_rush(tasks, budget_minutes=30).as_dict()
    assert data["budget_minutes"] == 30
    assert data["now"][0]["title"] == "Something"


def test_a_task_estimate_is_taken_from_its_metadata(tasks):
    tasks.submit(_task("Quick", meta={"estimated_minutes": 4}, user_priority=9))
    plan = plan_rush(tasks, budget_minutes=30)
    assert plan.now[0].estimated_minutes == 4


# ---------------------------------------------------------- always-active mode


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    from jarvis.config import settings_from_env
    from jarvis.runtime import build_runtime

    rt = build_runtime(settings_from_env(), with_memory=False)
    yield rt
    if rt.loop is not None and rt.loop.running:
        rt.loop.stop()
    rt.shutdown()


def test_the_loop_processes_queued_work(runtime):
    runtime.tasks.submit(_task("Background work"))
    loop = AlwaysActiveLoop(runtime, interval_s=0.05)
    runtime.loop = loop
    loop.start()
    try:
        for _ in range(100):
            if runtime.tasks.by_state(TaskState.DONE):
                break
            time.sleep(0.05)
        assert runtime.tasks.by_state(TaskState.DONE), "the loop never ran the task"
        assert loop.state.tasks_processed >= 1
    finally:
        loop.stop()


def test_stop_actually_stops_the_thread(runtime):
    """A stop that returns while the loop is still running is not a stop."""
    loop = AlwaysActiveLoop(runtime, interval_s=0.05)
    loop.start()
    assert loop.running
    loop.stop()
    assert not loop.running, "the loop thread was still alive after stop()"


def test_stop_is_quick_even_mid_interval(runtime):
    """_sleep slices the interval, so STOP is noticed without waiting it out."""
    loop = AlwaysActiveLoop(runtime, interval_s=5.0)
    loop.start()
    started = time.monotonic()
    loop.stop()
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"stop took {elapsed:.2f}s against a 5s interval"
    assert not loop.running


def test_a_paused_loop_does_no_work(runtime):
    """Paused *before* starting, so there is no race with the first tick."""
    runtime.tasks.submit(_task("Should not run while paused"))
    loop = AlwaysActiveLoop(runtime, interval_s=0.05)
    loop.pause()
    loop.start()
    try:
        time.sleep(0.3)
        assert loop.running, "pause must not kill the loop"
        assert loop.state.skipped_paused > 0
        assert loop.state.tasks_processed == 0
        assert not runtime.tasks.by_state(TaskState.DONE)
    finally:
        loop.stop()


def test_pausing_mid_run_stops_further_work(runtime):
    """The realistic case: pause arrives while the loop is already going.

    Measured by counting, because asserting an absolute zero would race the tick
    that is already in flight - and a pause that stops after the current task is
    correct behaviour, not a failure.
    """
    for i in range(12):
        runtime.tasks.submit(_task(f"Item {i}"))

    loop = AlwaysActiveLoop(runtime, interval_s=0.02)
    loop.start()
    try:
        time.sleep(0.1)
        loop.pause()
        at_pause = loop.state.tasks_processed
        time.sleep(0.4)
        assert loop.state.tasks_processed == at_pause, (
            "work continued after pause"
        )
        assert loop.running
    finally:
        loop.stop()


def test_an_emergency_stop_prevents_the_loop_from_working(runtime):
    """The strongest guarantee in the system: STOP means no work happens."""
    runtime.tasks.submit(_task("Must not run"))
    runtime.emergency_stop()

    loop = AlwaysActiveLoop(runtime, interval_s=0.05)
    loop.start()
    try:
        time.sleep(0.3)
        assert loop.state.skipped_stopped > 0
        assert loop.state.tasks_processed == 0
        assert not runtime.tasks.by_state(TaskState.DONE)
    finally:
        loop.stop()


def test_resume_lets_the_loop_work_again(runtime):
    runtime.tasks.submit(_task("Deferred work"))
    loop = AlwaysActiveLoop(runtime, interval_s=0.05)
    loop.start()
    try:
        loop.pause()
        time.sleep(0.2)
        runtime.resume()
        for _ in range(100):
            if runtime.tasks.by_state(TaskState.DONE):
                break
            time.sleep(0.05)
        assert runtime.tasks.by_state(TaskState.DONE)
    finally:
        loop.stop()


def test_a_failing_task_does_not_kill_the_loop(runtime):
    def boom(_ctx):
        raise RuntimeError("step exploded")

    runtime.tasks.submit(Task(title="Broken", steps=[Step("boom", boom)]))
    runtime.tasks.submit(_task("Fine"))

    loop = AlwaysActiveLoop(runtime, interval_s=0.05)
    loop.start()
    try:
        time.sleep(0.6)
        assert loop.running, "one failing task killed the loop"
    finally:
        loop.stop()


def test_the_loop_records_a_heartbeat(runtime):
    loop = AlwaysActiveLoop(runtime, interval_s=0.05)
    loop.start()
    try:
        time.sleep(0.2)
        assert loop.state.last_heartbeat
        assert loop.state.iterations > 1
    finally:
        loop.stop()


def test_the_loop_state_is_visible_in_runtime_status(runtime):
    assert runtime.status()["loop"] == {"enabled": False}
    loop = AlwaysActiveLoop(runtime, interval_s=0.05)
    runtime.loop = loop
    loop.start()
    try:
        state = runtime.status()["loop"]
        assert state["running"] is True
        assert "iterations" in state
    finally:
        loop.stop()


def test_the_loop_start_and_stop_are_audited(runtime):
    loop = AlwaysActiveLoop(runtime, interval_s=0.05)
    loop.start()
    loop.stop()
    actions = [r["action"] for r in runtime.audit.entries()]
    assert "loop.started" in actions
    assert "loop.stopped" in actions


def test_the_loop_can_be_stopped_from_another_thread(runtime):
    """The user's stop comes from somewhere other than the loop's own thread."""
    loop = AlwaysActiveLoop(runtime, interval_s=0.05)
    loop.start()
    result: list[str] = []

    def stopper() -> None:
        time.sleep(0.15)
        result.append(loop.stop())

    thread = threading.Thread(target=stopper)
    thread.start()
    thread.join(timeout=5)
    assert not loop.running
    assert result and "OFF" in result[0]


def test_always_active_is_constructed_but_not_started_by_default(
    tmp_path, monkeypatch
):
    """Turning it on is an explicit act, not a side effect of building a runtime."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    monkeypatch.setenv("JARVIS_ALWAYS_ACTIVE", "on")
    from jarvis.config import settings_from_env
    from jarvis.runtime import build_runtime

    rt = build_runtime(settings_from_env(), with_memory=False)
    try:
        assert rt.loop is not None
        assert not rt.loop.running, "the loop started itself"
    finally:
        rt.shutdown()


# ------------------------------------------------- reminders from memory


def test_rush_mode_can_see_reminders_stored_in_memory(tmp_path, monkeypatch):
    """Reminders live in memory, not in the task queue, and every CLI invocation
    is a fresh process. Without this, rush mode planned an empty queue while the
    user's actual to-do list sat in the other store."""
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path / "h"))
    from jarvis.config import settings_from_env
    from jarvis.core.memory import MemoryStore
    from jarvis.core.rush import reminders_from_memory

    settings = settings_from_env()
    settings.ensure_dirs()
    memory = MemoryStore(settings.memory_db)
    memory.remember("task", "Call the bank", "about the transfer")
    memory.remember("preference", "Timezone is IST", "not a task")

    items = reminders_from_memory(memory)
    titles = {i.title for i in items}
    assert "Call the bank" in titles
    assert "Timezone is IST" not in titles, "only the task category is plannable"
    assert all(i.meta.get("planning_only") for i in items)


def test_planning_only_items_are_never_requeued(tasks):
    """A reminder is a note. Silently turning it into runnable work would be a
    different and much worse surprise than not planning it."""
    from jarvis.core.rush import reminders_from_memory  # noqa: F401

    reminder = _task("Call the bank", meta={"planning_only": True})
    queued = _task("Queued work")
    tasks.submit(queued)

    plan = plan_rush(tasks, budget_minutes=30, extra_items=[reminder])
    before = {t.id for t in tasks.tasks}
    apply_rush(tasks, plan)

    assert {t.id for t in tasks.tasks} == before, "a reminder was submitted to the queue"
    assert "reminder from memory" in plan.now[0].reason


def test_rush_mode_works_with_no_memory(tasks):
    from jarvis.core.rush import reminders_from_memory

    assert reminders_from_memory(None) == []
    plan = plan_rush(tasks, budget_minutes=30, extra_items=[])
    assert "nothing is queued" in plan.render()


def test_status_names_the_boundary_of_every_partially_built_phase(tmp_path) -> None:
    """"Partially built" without saying which part is the same as saying nothing."""
    from jarvis.config import Settings
    from jarvis.runtime import build_runtime
    from jarvis.version import PARTIAL_PHASES

    status = build_runtime(Settings(home=tmp_path)).status()

    reported = status["partial_phases"]
    assert set(reported) == {str(n) for n in PARTIAL_PHASES}
    for number, detail in reported.items():
        assert detail["built"].strip(), f"phase {number} must say what is built"
        assert detail["boundary"].strip(), f"phase {number} must say what is missing"
