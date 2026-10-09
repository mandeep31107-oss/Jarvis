"""Task engine: priorities, dependencies, interruption and failure recovery."""

from __future__ import annotations

import threading
import time
from datetime import timedelta

from jarvis.core.options import Dilemma
from jarvis.core.risk import RiskLevel
from jarvis.core.tasks import (
    Priority,
    RetryPolicy,
    Task,
    TaskContext,
    TaskInterrupted,
    TaskManager,
    TaskState,
)
from jarvis.errors import JarvisError, ProviderUnavailable
from jarvis.util.clock import utcnow


# ------------------------------------------------------------------- priority
def test_overdue_deadline_outranks_a_distant_one():
    now = utcnow()
    urgent = Priority(deadline=now - timedelta(hours=1))
    relaxed = Priority(deadline=now + timedelta(days=30))
    assert urgent.score() > relaxed.score()


def test_high_risk_work_is_deliberately_slowed():
    low = Priority(risk=RiskLevel.LOW)
    critical = Priority(risk=RiskLevel.CRITICAL)
    assert critical.score() < low.score()


def test_user_priority_moves_the_score():
    assert Priority(user_priority=10).score() > Priority(user_priority=1).score()


def test_blocking_others_raises_priority():
    assert Priority(blocking_others=4).score() > Priority(blocking_others=0).score()


def test_priority_score_is_bounded():
    extreme = Priority(urgency=1, importance=1, user_priority=10, blocking_others=100)
    assert 0.0 <= extreme.score() <= 1.0


# --------------------------------------------------------------- dependencies
def test_a_task_waits_for_its_dependency(tasks):
    order: list[str] = []
    first = Task(title="first").add_step("s", lambda ctx: order.append("first"))
    second = Task(title="second").add_step("s", lambda ctx: order.append("second"))
    second.deps = [first.id]
    tasks.submit(second)
    tasks.submit(first)
    tasks.run()
    assert order == ["first", "second"]
    assert first.state is TaskState.DONE
    assert second.state is TaskState.DONE


def test_a_task_is_cancelled_when_its_dependency_fails(tasks):
    def boom(ctx):
        raise RuntimeError("nope")

    first = Task(title="first", retry=RetryPolicy(max_attempts=0)).add_step("s", boom)
    second = Task(title="second").add_step("s", lambda ctx: "ran")
    second.deps = [first.id]
    tasks.submit(first)
    tasks.submit(second)
    tasks.run()
    assert first.state is TaskState.FAILED
    assert second.state is TaskState.CANCELLED
    assert "dependency" in second.error


def test_plan_view_shows_blockers_and_scores(tasks):
    first = Task(title="first").add_step("s", lambda ctx: None)
    second = Task(title="second").add_step("s", lambda ctx: None)
    second.deps = [first.id]
    tasks.submit(first)
    tasks.submit(second)
    view = {row["title"]: row for row in tasks.plan_view()}
    assert view["second"]["waiting_on"] == [first.id]
    assert isinstance(view["first"]["score"], float)


# --------------------------------------------------------------- interruption
def test_interrupt_between_steps_checkpoints_the_task(tasks):
    done: list[str] = []
    gate = threading.Event()

    def slow_first(ctx: TaskContext):
        done.append("first")
        gate.set()
        # Long enough for the interrupt to land before step two starts.
        for _ in range(200):
            ctx.check_interrupt()
            time.sleep(0.001)
        done.append("first-finished")

    task = Task(title="interruptible")
    task.add_step("one", slow_first)
    task.add_step("two", lambda ctx: done.append("second"))
    tasks.submit(task)
    worker = threading.Thread(target=tasks.run, daemon=True)
    worker.start()
    assert gate.wait(2.0), "first step never started"
    assert tasks.interrupt(task.id) is True
    worker.join(timeout=5)
    assert task.state is TaskState.PAUSED
    assert task.checkpoint == 0, "a step interrupted mid-flight must not count as complete"
    assert "second" not in done


def test_resume_continues_from_the_checkpoint(tasks):
    executed: list[str] = []
    interrupt_after = {"one"}

    def step(name):
        def run(ctx: TaskContext):
            ctx.check_interrupt()
            executed.append(name)
            if name in interrupt_after:
                raise TaskInterrupted(f"stop after {name}")
            return name

        return run

    task = Task(title="resumable")
    task.add_step("one", step("one"))
    task.add_step("two", step("two"))
    tasks.submit(task)
    tasks.run()
    assert task.state is TaskState.PAUSED
    assert task.checkpoint == 0

    interrupt_after.clear()
    task.state = TaskState.PENDING
    tasks.run()
    assert task.state is TaskState.DONE
    assert executed == ["one", "one", "two"] or executed == ["one", "two"]


def test_checkpoint_survives_a_restart(tmp_path):
    """A step interrupted *mid-flight* is not durable; a completed one is.

    Here step one completes and step two is interrupted, so a fresh manager
    resumes at step two and never re-runs step one.
    """
    executed: list[str] = []
    first = TaskManager(checkpoint_dir=tmp_path / "cp")

    def two(ctx):
        ctx.check_interrupt()
        raise TaskInterrupted("user cut in")

    task = Task(title="persisted")
    task.add_step("one", lambda ctx: executed.append("one"))
    task.add_step("two", two)
    first.submit(task)
    first.run()
    assert task.state is TaskState.PAUSED
    assert task.checkpoint == 1

    # A brand new manager restores the saved position.
    second = TaskManager(checkpoint_dir=tmp_path / "cp")
    fresh = Task(title="persisted")
    fresh.id = task.id
    fresh.add_step("one", lambda ctx: executed.append("one-again"))
    fresh.add_step("two", lambda ctx: executed.append("two"))
    second.restore(fresh)
    assert fresh.checkpoint == 1
    second.run()
    assert "two" in executed
    assert "one-again" not in executed, "a completed step must not be re-run"


def test_an_interrupted_step_is_re_run_from_its_start(tmp_path):
    """The other half of the guarantee: partial work inside a step is discarded."""
    executed: list[str] = []
    manager = TaskManager(checkpoint_dir=tmp_path / "cp")

    def half_done(ctx):
        executed.append("started")
        raise TaskInterrupted("cut in halfway")

    task = Task(title="partial")
    task.add_step("one", half_done)
    manager.submit(task)
    manager.run()
    assert task.checkpoint == 0
    assert executed == ["started"]


def test_cancel_marks_the_task_terminal(tasks):
    task = Task(title="cancel me").add_step("s", lambda ctx: None)
    tasks.submit(task)
    assert tasks.cancel(task.id) is True
    assert task.state is TaskState.CANCELLED
    assert tasks.cancel(task.id) is False


# ----------------------------------------------------------- failure recovery
def test_retry_policy_respects_the_attempt_cap():
    policy = RetryPolicy(max_attempts=2)
    assert policy.allows_retry(ProviderUnavailable("x"), 1) is True
    assert policy.allows_retry(ProviderUnavailable("x"), 2) is False


def test_non_retryable_errors_are_not_retried():
    policy = RetryPolicy(max_attempts=5)

    class Fatal(JarvisError):
        cause = "configuration"
        safe_to_retry = False

    assert policy.allows_retry(Fatal("missing key"), 1) is False


def test_backoff_is_capped():
    policy = RetryPolicy(backoff_base=1.0, backoff_cap=2.0)
    assert policy.delay(10) == 2.0


def test_a_failure_produces_user_facing_options(tasks):
    def boom(ctx):
        raise ProviderUnavailable("the API is down")

    task = Task(title="flaky", retry=RetryPolicy(max_attempts=0)).add_step("s", boom)
    tasks.submit(task)
    tasks.run()
    assert task.state is TaskState.FAILED
    assert isinstance(task.options, Dilemma)
    assert not task.options.validate(), task.options.validate()
    assert any(o.kind == "cancel" for o in task.options.options)


def test_a_configuration_failure_offers_the_credential_option(tasks):
    from jarvis.errors import ConfigurationError

    def boom(ctx):
        raise ConfigurationError("STRIPE_KEY is not set")

    task = Task(title="needs a key", retry=RetryPolicy(max_attempts=0)).add_step("s", boom)
    tasks.submit(task)
    tasks.run()
    labels = [o.title for o in task.options.options]
    assert any("credential" in t.lower() for t in labels)


def test_an_alternative_method_recovers_the_task(tasks):
    attempts = {"n": 0}

    def primary(ctx):
        attempts["n"] += 1
        raise ProviderUnavailable("primary is down")

    def fallback(ctx):
        return "fallback-result"

    task = Task(title="with fallback", retry=RetryPolicy(max_attempts=0))
    task.add_step("s", primary)
    task.alternatives = [fallback]
    tasks.submit(task)
    tasks.run()
    assert task.state is TaskState.DONE
    assert task.results["alternative"] == "fallback-result"
    assert attempts["n"] == 1, "the primary must not be retried in a loop"


def test_a_failing_alternative_does_not_crash_the_runner(tasks):
    def primary(ctx):
        raise ProviderUnavailable("down")

    def bad_alternative(ctx):
        raise RuntimeError("also broken")

    task = Task(title="both fail", retry=RetryPolicy(max_attempts=0))
    task.add_step("s", primary)
    task.alternatives = [bad_alternative]
    tasks.submit(task)
    tasks.run()
    assert task.state is TaskState.FAILED


def test_retry_eventually_succeeds(tasks):
    calls = {"n": 0}

    def flaky(ctx):
        calls["n"] += 1
        if calls["n"] < 2:
            raise ProviderUnavailable("transient")
        return "ok"

    task = Task(
        title="flaky",
        retry=RetryPolicy(max_attempts=3, backoff_base=0.0, backoff_cap=0.0),
    )
    task.add_step("s", flaky)
    tasks.submit(task)
    tasks.run()
    assert task.state is TaskState.DONE
    assert calls["n"] == 2


# ------------------------------------------------------------------- controls
def test_pause_blocks_processing(tasks):
    task = Task(title="paused work").add_step("s", lambda ctx: None)
    tasks.submit(task)
    tasks.pause()
    assert tasks.run() == 0
    assert task.state is TaskState.PENDING
    tasks.resume()
    assert tasks.run() == 1
    assert task.state is TaskState.DONE


def test_stop_is_always_available(tasks):
    """Spec section 21: the user can never be locked out of stopping."""
    started = threading.Event()

    def slow(ctx):
        started.set()
        for _ in range(500):
            ctx.check_interrupt()
            time.sleep(0.002)

    task = Task(title="long").add_step("s", slow)
    tasks.submit(task)
    worker = threading.Thread(target=tasks.start_background, daemon=True)
    worker.start()
    assert started.wait(3.0)
    tasks.stop(timeout=5)
    assert task.state in (TaskState.PAUSED, TaskState.CANCELLED, TaskState.PENDING)


def test_background_loop_processes_and_stops(tasks):
    task = Task(title="background").add_step("s", lambda ctx: "done")
    tasks.submit(task)
    tasks.start_background()
    deadline = time.time() + 5
    while task.state is not TaskState.DONE and time.time() < deadline:
        time.sleep(0.02)
    tasks.stop()
    assert task.state is TaskState.DONE


def test_approval_gate_can_hold_a_task(tasks):
    task = Task(title="needs approval").add_step("s", lambda ctx: None)
    tasks.approval_gate = lambda t: False
    tasks.submit(task)
    tasks.run()
    assert task.state is TaskState.WAITING_APPROVAL
    task.meta["approved"] = True
    tasks.run()
    assert task.state is TaskState.DONE


def test_independent_tasks_run_in_parallel(tasks):
    tasks.max_workers = 3
    barrier = threading.Barrier(3, timeout=5)

    def wait_for_the_others(ctx):
        barrier.wait()
        return "synced"

    for i in range(3):
        tasks.submit(Task(title=f"parallel-{i}").add_step("s", wait_for_the_others))
    tasks.run()
    assert all(t.state is TaskState.DONE for t in tasks.tasks)


def test_render_reports_an_empty_queue(tasks):
    assert "no tasks" in tasks.render()


def test_task_snapshot_is_json_safe(tasks):
    task = Task(title="snap").add_step("s", lambda ctx: {"obj": object()})
    tasks.submit(task)
    tasks.run()
    snapshot = task.snapshot()
    import json

    json.dumps(snapshot)
    assert snapshot["state"] == "done"
