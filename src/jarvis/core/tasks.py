"""Task management, interruption and failure recovery (spec sections 13, 22, 34).

* **Priority engine** (22): urgency, importance, deadline, user priority, risk,
  dependencies and resource cost are combined into one score.
* **Dependency analysis** (22): a task only becomes *ready* when everything it
  depends on is done.
* **Interruption** (13): tasks are sequences of steps. The runner checks for an
  interrupt between steps, saves a checkpoint, and can later restore from it, so
  "no wait, open the other one" does not lose half-finished work.
* **Failure recovery** (34): classify the cause, retry only when safe, try the
  declared alternatives, then stop and hand the user a set of options. No
  infinite retry loops.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis.core.events import EventBus
from jarvis.core.options import Dilemma, Option, failure_dilemma
from jarvis.core.risk import RiskLevel
from jarvis.errors import JarvisError, TaskFailed
from jarvis.util.clock import now_iso, utcnow
from jarvis.util.ids import short_id
from jarvis.util.store import read_json, write_json

log = logging.getLogger("jarvis.tasks")


class TaskState(str, enum.Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED)


class TaskInterrupted(JarvisError):
    """Raised inside a step when the user interrupts. The runner catches it and
    checkpoints the task instead of failing it."""

    cause = "interrupted"
    safe_to_retry = True


@dataclass
class Priority:
    """Inputs to the priority engine. All weights are 0..1 except user_priority."""

    urgency: float = 0.5
    importance: float = 0.5
    deadline: datetime | None = None
    user_priority: int = 5
    risk: RiskLevel = RiskLevel.LOW
    blocking_others: int = 0
    resource_weight: float = 0.3

    def score(self, now: datetime | None = None) -> float:
        now = now or utcnow()
        urgency = self.urgency
        if self.deadline is not None:
            hours_left = (self.deadline - now).total_seconds() / 3600.0
            if hours_left <= 0:
                urgency = 1.0  # already overdue
            else:
                # Under 1h -> ~1.0, 24h -> ~0.6, 7d -> ~0.3
                urgency = max(urgency, min(1.0, 1.0 / (1.0 + hours_left / 12.0)))
        # High-risk work is *not* accelerated; it is deliberately slowed so it
        # waits for a human rather than jumping the queue.
        risk_factor = {
            RiskLevel.LOW: 1.0,
            RiskLevel.MEDIUM: 0.95,
            RiskLevel.HIGH: 0.7,
            RiskLevel.CRITICAL: 0.4,
        }[self.risk]
        blocking_bonus = min(0.2, 0.05 * self.blocking_others)
        cheapness = 1.0 - 0.3 * max(0.0, min(1.0, self.resource_weight))
        raw = (
            0.30 * urgency
            + 0.30 * self.importance
            + 0.20 * (self.user_priority / 10.0)
            + 0.10 * blocking_bonus / 0.2
            + 0.10 * cheapness
        )
        return round(raw * risk_factor, 4)


@dataclass
class RetryPolicy:
    max_attempts: int = 2
    backoff_base: float = 0.05
    backoff_cap: float = 2.0
    #: Exception ``cause`` buckets that are safe to retry automatically.
    retry_on: frozenset[str] = frozenset({"timeout", "provider_unavailable", "unknown"})

    def delay(self, attempt: int) -> float:
        return min(self.backoff_cap, self.backoff_base * (2 ** max(0, attempt - 1)))

    def allows_retry(self, exc: BaseException, attempt: int) -> bool:
        if attempt >= self.max_attempts:
            return False
        cause = getattr(exc, "cause", "unknown")
        return getattr(exc, "safe_to_retry", False) or cause in self.retry_on


@dataclass
class Step:
    """One unit of work inside a task. Steps are the checkpoint boundary."""

    name: str
    fn: Callable[[TaskContext], Any]


@dataclass
class TaskContext:
    """Handed to each step. Carries results between steps and the interrupt flag."""

    task: Task
    results: dict[str, Any] = field(default_factory=dict)
    events: EventBus | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)
    _step: str = ""

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_interrupt(self) -> None:
        """Call this at the top of any long-running step."""
        if self.cancelled:
            raise TaskInterrupted(f"interrupted during '{self._step}'")

    def publish(self, topic: str, **payload: Any) -> None:
        if self.events is not None:
            self.events.publish(topic, task_id=self.task.id, title=self.task.title, **payload)

    def log_activity(self, agent: str, message: str) -> None:
        """Section 30: a concise, user-safe description of what is happening now."""
        self.publish("activity", agent=agent, message=message)


@dataclass
class Task:
    title: str
    agent: str = "supervisor"
    steps: list[Step] = field(default_factory=list)
    priority: Priority = field(default_factory=Priority)
    deps: list[str] = field(default_factory=list)
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    #: Alternative approaches tried in order when the primary keeps failing.
    alternatives: list[Callable[[TaskContext], Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: short_id("task"))

    # --- runtime state (not part of construction) ----------------------------
    state: TaskState = field(default=TaskState.PENDING, init=False)
    attempts: int = field(default=0, init=False)
    checkpoint: int = field(default=0, init=False)
    results: dict[str, Any] = field(default_factory=dict, init=False)
    error: str = field(default="", init=False)
    cause: str = field(default="", init=False)
    started_at: str | None = field(default=None, init=False)
    finished_at: str | None = field(default=None, init=False)
    activity: list[str] = field(default_factory=list, init=False)
    options: Dilemma | None = field(default=None, init=False)

    # --- helpers -------------------------------------------------------------
    def add_step(self, name: str, fn: Callable[[TaskContext], Any]) -> Task:
        self.steps.append(Step(name=name, fn=fn))
        return self

    @property
    def score(self) -> float:
        return self.priority.score()

    def note(self, message: str) -> None:
        self.activity.append(f"{now_iso()[11:19]} {message}")
        self.activity = self.activity[-50:]

    def snapshot(self) -> dict[str, Any]:
        """Serialisable state, so an interrupted task survives a restart."""
        return {
            "id": self.id,
            "title": self.title,
            "agent": self.agent,
            "state": self.state.value,
            "checkpoint": self.checkpoint,
            "attempts": self.attempts,
            "results": _jsonable(self.results),
            "error": self.error,
            "cause": self.cause,
            "deps": list(self.deps),
            "steps": [s.name for s in self.steps],
            "meta": _jsonable(self.meta),
            "activity": list(self.activity),
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


class TaskManager:
    """Priority-ordered, dependency-aware, interruptible task runner.

    ``run()`` drains the queue synchronously (used by the CLI and tests);
    ``start_background()`` runs the same loop on a worker thread for the
    always-active mode. ``stop()`` always works -- spec section 21 requires that
    the user can never be locked out of stopping the agent.
    """

    def __init__(
        self,
        *,
        max_workers: int = 3,
        events: EventBus | None = None,
        checkpoint_dir: str | Path | None = None,
        approval_gate: Callable[[Task], bool] | None = None,
    ) -> None:
        self.max_workers = max(1, max_workers)
        self.events = events
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else None
        self.approval_gate = approval_gate
        self._tasks: dict[str, Task] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()
        self._contexts: dict[str, TaskContext] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._paused = threading.Event()

    # ------------------------------------------------------------------ submit
    def submit(self, task: Task) -> Task:
        with self._lock:
            self._tasks[task.id] = task
            self._order.append(task.id)
        task.note("queued")
        self._emit("task.queued", task)
        return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    @property
    def tasks(self) -> list[Task]:
        with self._lock:
            return [self._tasks[i] for i in self._order]

    # ------------------------------------------------------------------ status
    def by_state(self, state: TaskState) -> list[Task]:
        return [t for t in self.tasks if t.state is state]

    def pending_count(self) -> int:
        return sum(1 for t in self.tasks if not t.state.terminal)

    def ready(self) -> list[Task]:
        """Tasks whose dependencies are all satisfied, ordered by priority."""
        done = {t.id for t in self.tasks if t.state is TaskState.DONE}
        failed = {t.id for t in self.tasks if t.state in (TaskState.FAILED, TaskState.CANCELLED)}
        out: list[Task] = []
        with self._lock:
            for task in (self._tasks[i] for i in self._order):
                if task.state is TaskState.WAITING_APPROVAL and task.meta.get("approved"):
                    pass  # approval arrived; re-gate it in _execute
                elif task.state is not TaskState.PENDING:
                    continue
                if any(d in failed for d in task.deps):
                    task.state = TaskState.CANCELLED
                    task.error = "dependency failed or was cancelled"
                    task.finished_at = now_iso()
                    self._emit("task.cancelled", task, reason=task.error)
                    continue
                if all(d in done for d in task.deps):
                    out.append(task)
        out.sort(key=lambda t: -t.score)
        return out

    def plan_view(self) -> list[dict[str, Any]]:
        """The queue as the dashboard shows it: score, state, blockers."""
        done = {t.id for t in self.tasks if t.state is TaskState.DONE}
        rows = []
        for t in sorted(self.tasks, key=lambda x: -x.score):
            rows.append(
                {
                    "id": t.id,
                    "title": t.title,
                    "agent": t.agent,
                    "state": t.state.value,
                    "score": t.score,
                    "checkpoint": t.checkpoint,
                    "steps": [s.name for s in t.steps],
                    "waiting_on": [d for d in t.deps if d not in done],
                    "attempts": t.attempts,
                    "error": t.error,
                }
            )
        return rows

    # ------------------------------------------------------------------ control
    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def interrupt(self, task_id: str) -> bool:
        """Stop a running task at its next safe point and checkpoint it."""
        ctx = self._contexts.get(task_id)
        if ctx is None:
            task = self.get(task_id)
            if task and not task.state.terminal:
                task.state = TaskState.PAUSED
                task.note("interrupted while queued")
                self._save_checkpoint(task)
                return True
            return False
        ctx._cancel.set()
        return True

    def cancel(self, task_id: str) -> bool:
        self.interrupt(task_id)
        task = self.get(task_id)
        if task is None or task.state.terminal:
            return False
        task.state = TaskState.CANCELLED
        task.finished_at = now_iso()
        task.note("cancelled by user")
        self._emit("task.cancelled", task)
        return True

    def stop(self, timeout: float = 10.0) -> None:
        """Always-on kill switch. Interrupts everything, then stops the loop."""
        self._stop.set()
        with self._lock:
            ids = list(self._order)
        for tid in ids:
            ctx = self._contexts.get(tid)
            if ctx:
                ctx._cancel.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------ running
    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._background_loop, name="jarvis-tasks", daemon=True)
        self._thread.start()

    def _background_loop(self) -> None:
        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.05)
                continue
            processed = self.run(max_tasks=1, wait=False)
            if processed == 0:
                time.sleep(0.1)

    def run(self, max_tasks: int | None = None, *, wait: bool = True) -> int:
        """Process ready tasks. Returns how many tasks were processed."""
        processed = 0
        while not self._stop.is_set():
            if self._paused.is_set():
                break  # PAUSE means stop processing, not "skip the background loop"
            batch = self.ready()
            if not batch:
                break
            if max_tasks is not None:
                batch = batch[: max(0, max_tasks - processed)]
                if not batch:
                    break
            batch = batch[: self.max_workers]
            if len(batch) == 1 or not wait:
                for task in batch:
                    self._execute(task)
            else:
                # Independent, already-policy-cleared tasks run in parallel.
                with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                    futures = [pool.submit(self._execute, t) for t in batch]
                    for f in futures:
                        f.result()
            processed += len(batch)
            if max_tasks is not None and processed >= max_tasks:
                break
        return processed

    # ------------------------------------------------------------------ execute
    def _execute(self, task: Task) -> None:
        if self.approval_gate is not None and not task.meta.get("approved") \
                and not self.approval_gate(task):
            task.state = TaskState.WAITING_APPROVAL
            task.note("waiting for approval")
            self._emit("task.waiting_approval", task)
            return
        task.meta.pop("approved", None)

        ctx = TaskContext(task=task, results=dict(task.results), events=self.events)
        self._contexts[task.id] = ctx
        task.state = TaskState.RUNNING
        task.started_at = task.started_at or now_iso()
        task.note("started")
        self._emit("task.started", task)

        try:
            self._run_steps(task, ctx)
        except TaskInterrupted as exc:
            task.state = TaskState.PAUSED
            task.error = str(exc)
            task.note(f"paused: {exc}")
            self._save_checkpoint(task)
            self._emit("task.paused", task, checkpoint=task.checkpoint)
            return
        except BaseException as exc:  # noqa: BLE001 - recovery needs to see everything
            self._recover(task, ctx, exc)
            return
        finally:
            self._contexts.pop(task.id, None)

        task.state = TaskState.DONE
        task.finished_at = now_iso()
        task.results = dict(ctx.results)
        task.note("done")
        self._emit("task.done", task, result=_jsonable(task.results))

    def _run_steps(self, task: Task, ctx: TaskContext) -> None:
        """Execute steps from the checkpoint forward, checking for interrupts."""
        index = task.checkpoint
        while index < len(task.steps):
            if ctx.cancelled:
                raise TaskInterrupted(f"interrupted before '{task.steps[index].name}'")
            step = task.steps[index]
            ctx._step = step.name
            ctx.log_activity(task.agent, f"step {index + 1}/{len(task.steps)}: {step.name}")
            task.note(f"step: {step.name}")
            self._emit("task.step", task, step=step.name, index=index)
            ctx.results[step.name] = step.fn(ctx)
            index += 1
            task.checkpoint = index  # durable progress marker
            self._save_checkpoint(task)
        task.results = dict(ctx.results)

    # ------------------------------------------------------------------ recovery
    def _recover(self, task: Task, ctx: TaskContext, exc: BaseException) -> None:
        task.attempts += 1
        task.error = f"{type(exc).__name__}: {exc}"
        task.cause = getattr(exc, "cause", "unknown")
        task.note(f"attempt {task.attempts} failed ({task.cause})")
        self._emit("task.error", task, error=task.error, cause=task.cause)

        if task.retry.allows_retry(exc, task.attempts):
            delay = task.retry.delay(task.attempts)
            task.note(f"retrying in {delay:.2f}s")
            self._emit("task.retry", task, attempt=task.attempts, delay=delay)
            time.sleep(delay)
            task.state = TaskState.PENDING  # re-queued through the normal priority path
            return

        # Alternatives, one at a time, never in a loop with the primary.
        for name, alt in enumerate(task.alternatives, start=1):
            ctx._step = f"alternative-{name}"
            try:
                task.note(f"trying alternative {name}")
                self._emit("task.alternative", task, index=name)
                ctx.results["alternative"] = alt(ctx)
            except BaseException as alt_exc:  # noqa: BLE001
                task.note(f"alternative {name} failed: {alt_exc}")
                continue
            task.state = TaskState.DONE
            task.finished_at = now_iso()
            task.results = dict(ctx.results)
            task.error = ""
            task.note("recovered via alternative")
            self._emit("task.done", task, recovered=True)
            return

        task.state = TaskState.FAILED
        task.finished_at = now_iso()
        self._save_checkpoint(task)
        task.options = self.recovery_options(task, exc)
        self._emit("task.failed", task, error=task.error, options=task.options.as_dict())

    def recovery_options(self, task: Task, exc: BaseException) -> Dilemma:
        """Section 34: don't spin - tell the user what happened and what's next."""
        cause = getattr(exc, "cause", "unknown")
        safe = Option(
            label="A",
            title="Stop here and keep the partial result",
            kind="conservative",
            expected_result=f"Task '{task.title}' stays paused at step {task.checkpoint}.",
            risk=RiskLevel.LOW,
            cost="none",
            time="immediate",
            notes=[f"Partial output preserved: {sorted(task.results)[:5] or 'nothing yet'}"],
        )
        retry = Option(
            label="B",
            title="Retry the failed step",
            kind="fast",
            expected_result="The task resumes from its checkpoint.",
            risk=RiskLevel.MEDIUM if task.priority.risk.rank >= 1 else RiskLevel.LOW,
            cost="time",
            time="short",
            notes=[f"Cause was '{cause}' - retrying only helps if that cause is transient."],
        )
        dilemma = failure_dilemma(
            task=task.title, cause=f"{type(exc).__name__}: {exc}", safe=safe, alternatives=[retry]
        )
        if cause == "configuration":
            dilemma.add(
                Option(
                    label="C",
                    title="Configure the missing credential securely, then resume",
                    kind="safe",
                    expected_result="The task completes once the setting exists.",
                    risk=RiskLevel.LOW,
                    cost="a few minutes",
                    time="short",
                    compliance="compliant",
                    notes=["Set it in your environment or secret store - never in source code."],
                )
            )
        if cause in ("capability_unavailable", "consent_required"):
            dilemma.add(
                Option(
                    label="C",
                    title="Skip this step and continue without it",
                    kind="safe",
                    expected_result="The task finishes with a documented gap.",
                    risk=RiskLevel.LOW,
                    cost="reduced output",
                    time="immediate",
                    compliance="compliant",
                )
            )
        return dilemma

    # ------------------------------------------------------------------ checkpoints
    def _save_checkpoint(self, task: Task) -> None:
        if self.checkpoint_dir is None:
            return
        write_json(self.checkpoint_dir / f"{task.id}.json", task.snapshot())

    def restore(self, task: Task) -> Task:
        """Re-attach a task to saved checkpoint state after a restart."""
        if self.checkpoint_dir is None:
            return task
        data = read_json(self.checkpoint_dir / f"{task.id}.json")
        if not data:
            return task
        task.checkpoint = int(data.get("checkpoint", 0))
        task.attempts = int(data.get("attempts", 0))
        task.results = dict(data.get("results", {}))
        task.activity = list(data.get("activity", []))
        if data.get("state") in (TaskState.PAUSED.value, TaskState.RUNNING.value):
            task.state = TaskState.PENDING  # resumable
        task.note(f"restored from checkpoint {task.checkpoint}")
        return self.submit(task)

    # ------------------------------------------------------------------ reporting
    def render(self, limit: int = 20) -> str:
        rows = self.plan_view()[:limit]
        if not rows:
            return "(no tasks)"
        lines = []
        for r in rows:
            blocked = f" waiting_on={r['waiting_on']}" if r["waiting_on"] else ""
            lines.append(
                f"- [{r['state']:<17}] {r['score']:.3f}  {r['id']}  {r['agent']:<12} "
                f"{r['title']}{blocked}"
                + (f"\n    error: {r['error']}" if r["error"] else "")
            )
        return "\n".join(lines)

    def _emit(self, topic: str, task: Task, **payload: Any) -> None:
        if self.events is not None:
            self.events.publish(topic, task_id=task.id, title=task.title, agent=task.agent, **payload)


class TaskFailedError(TaskFailed):
    """Convenience alias for callers that want to raise a typed failure."""
