"""Rush mode (spec section 22).

"I'm in a rush" means: do the one thing that matters, and tell me what you did
not do. It does **not** mean skip the safety checks.

So this engine reorders work; it never bypasses anything. A HIGH or CRITICAL
task is not accelerated by being urgent - it moves into a separate "needs you"
bucket, because the fastest thing Jarvis can do with a task that requires
approval is not to do it unapproved.

The plan is always explicit about what was deferred. Silently dropping work
because the user was in a hurry is how an agent loses things.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from jarvis.core.risk import RiskLevel
from jarvis.core.tasks import Priority, Task, TaskManager, TaskState
from jarvis.util.clock import utcnow

#: Minutes a task is assumed to take when it does not say. Deliberately
#: conservative: underestimating is what makes a rushed plan fail.
DEFAULT_TASK_MINUTES = 10.0

#: Rush mode will not plan more than this many tasks, however much time is left.
#: A plan with twelve items is not a plan for someone in a hurry.
MAX_RUSH_TASKS = 3


@dataclass
class RushItem:
    """One task in a rush plan, with the reason it is where it is."""

    task: Task
    bucket: str  # now | needs_you | deferred | skipped
    reason: str
    estimated_minutes: float
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.task.id,
            "title": self.task.title,
            "bucket": self.bucket,
            "reason": self.reason,
            "estimated_minutes": round(self.estimated_minutes, 1),
            "score": round(self.score, 3),
        }


@dataclass
class RushPlan:
    """What to do now, what needs the user, and what was deliberately left."""

    budget_minutes: float
    now: list[RushItem] = field(default_factory=list)
    needs_you: list[RushItem] = field(default_factory=list)
    deferred: list[RushItem] = field(default_factory=list)
    skipped: list[RushItem] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: utcnow().isoformat())

    @property
    def planned_minutes(self) -> float:
        return sum(item.estimated_minutes for item in self.now)

    def all(self) -> list[RushItem]:
        return self.now + self.needs_you + self.deferred + self.skipped

    def as_dict(self) -> dict[str, Any]:
        return {
            "budget_minutes": self.budget_minutes,
            "planned_minutes": round(self.planned_minutes, 1),
            "now": [i.as_dict() for i in self.now],
            "needs_you": [i.as_dict() for i in self.needs_you],
            "deferred": [i.as_dict() for i in self.deferred],
            "skipped": [i.as_dict() for i in self.skipped],
        }

    def render(self) -> str:
        lines = [
            f"Rush plan - {self.budget_minutes:.0f} minute(s) available, "
            f"{self.planned_minutes:.0f} planned"
        ]
        if self.now:
            lines.append("DOING NOW:")
            for item in self.now:
                lines.append(
                    f"  - {item.task.title} (~{item.estimated_minutes:.0f}m) - {item.reason}"
                )
        if self.needs_you:
            lines.append("NEEDS YOU (not accelerated by being urgent):")
            for item in self.needs_you:
                lines.append(f"  - {item.task.title} - {item.reason}")
        if self.deferred:
            lines.append("DEFERRED - still queued, not lost:")
            for item in self.deferred:
                lines.append(f"  - {item.task.title} - {item.reason}")
        if self.skipped:
            lines.append("SKIPPED:")
            for item in self.skipped:
                lines.append(f"  - {item.task.title} - {item.reason}")
        if not (self.now or self.needs_you or self.deferred or self.skipped):
            lines.append("  nothing is queued.")
        return "\n".join(lines)


def reminders_from_memory(memory: Any, *, limit: int = 25) -> list[Task]:
    """Turn stored reminders into planning-only tasks.

    Reminders live in memory, not in the task queue, and each CLI invocation is a
    fresh process - so without this, rush mode plans an empty queue while the
    user's actual to-do list sits in the other store. That made the feature
    useless exactly when it was needed.

    These are marked ``planning_only`` so they are never submitted or executed:
    a reminder is a note, and turning it into runnable work silently would be a
    different and much worse surprise.
    """
    from jarvis.core.memory import MemoryCategory

    if memory is None or not getattr(memory, "enabled", False):
        return []
    try:
        records = memory.search(category=MemoryCategory.TASK, limit=limit)
    except Exception:  # noqa: BLE001 - a memory failure must not break planning
        return []
    out: list[Task] = []
    for record in records:
        out.append(
            Task(
                title=record.title or record.content[:60] or "reminder",
                priority=Priority(user_priority=min(10, 3 + int(record.importance or 3))),
                meta={"planning_only": True, "memory_id": record.id},
            )
        )
    return out


def _estimated_minutes(task: Task) -> float:
    """The task's own estimate if it has one, otherwise a conservative default."""
    for key in ("estimated_minutes", "estimate_minutes", "minutes"):
        value = task.meta.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return DEFAULT_TASK_MINUTES


def plan_rush(
    tasks: TaskManager,
    *,
    budget_minutes: float = 30.0,
    max_tasks: int = MAX_RUSH_TASKS,
    extra_items: Sequence[Task] = (),
) -> RushPlan:
    """Decide what to do in the next ``budget_minutes``.

    Nothing is cancelled and nothing is run here - this is a plan. Applying it
    is a separate, explicit step, so a rushed user still sees what was decided
    before any of it happens.

    ``extra_items`` are planning-only entries (reminders from memory, say) that
    are considered for the plan but never submitted to the queue.
    """
    plan = RushPlan(budget_minutes=max(0.0, float(budget_minutes)))
    now = utcnow()

    candidates = [
        t
        for t in tasks.tasks
        if t.state in (TaskState.PENDING, TaskState.WAITING_APPROVAL, TaskState.RUNNING)
    ]
    candidates.extend(extra_items)

    scored: list[tuple[float, Task]] = []
    for task in candidates:
        try:
            score = task.priority.score(now)
        except Exception:  # noqa: BLE001 - a bad estimate must not break planning
            score = 0.0
        scored.append((score, task))

    # Highest score first; ties broken by the sooner deadline, then by id so the
    # order is stable and therefore explainable.
    scored.sort(
        key=lambda pair: (
            -pair[0],
            pair[1].priority.deadline or now + timedelta(days=3650),
            pair[1].id,
        )
    )

    remaining = plan.budget_minutes
    for score, task in scored:
        minutes = _estimated_minutes(task)
        item_base = {"task": task, "estimated_minutes": minutes, "score": score}

        # Urgency never buys its way past the approval gate.
        if task.priority.risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            plan.needs_you.append(
                RushItem(
                    **item_base,
                    bucket="needs_you",
                    reason=(
                        f"{task.priority.risk.value} risk needs your approval; "
                        "rush mode reorders work, it does not skip approvals"
                    ),
                )
            )
            continue

        if len(plan.now) >= max_tasks:
            plan.deferred.append(
                RushItem(
                    **item_base,
                    bucket="deferred",
                    reason="rush mode plans at most "
                    f"{max_tasks} task(s) - a longer list is not a plan for a hurry",
                )
            )
            continue

        if minutes > remaining:
            plan.deferred.append(
                RushItem(
                    **item_base,
                    bucket="deferred",
                    reason=(
                        f"needs ~{minutes:.0f}m and only {remaining:.0f}m of the "
                        "budget is left; starting it now would leave it half-done"
                    ),
                )
            )
            continue

        origin = (
            "a reminder from memory - not queued, so you will have to do this one yourself"
            if task.meta.get("planning_only")
            else f"highest score ({score:.2f}) that fits the remaining budget"
        )
        plan.now.append(
            RushItem(**item_base, bucket="now", reason=origin)
        )
        remaining -= minutes

    return plan


def apply_rush(tasks: TaskManager, plan: RushPlan) -> dict[str, int]:
    """Reorder the queue to match a plan.

    Deferred tasks are not cancelled - they are deprioritised so the planned
    work really does come first. Cancelling would be losing the user's work
    because they were in a hurry.
    """
    applied = {"now": 0, "deferred": 0}
    for item in plan.now:
        # Planning-only items are not in the queue, so reordering them is a no-op
        # that would only look like it did something.
        if item.task.meta.get("planning_only"):
            continue
        item.task.priority.user_priority = min(10, item.task.priority.user_priority + 3)
        applied["now"] += 1
    for item in plan.deferred:
        item.task.priority.user_priority = max(1, item.task.priority.user_priority - 2)
        applied["deferred"] += 1
    return applied


__all__ = [
    "DEFAULT_TASK_MINUTES",
    "MAX_RUSH_TASKS",
    "RushItem",
    "RushPlan",
    "apply_rush",
    "plan_rush",
    "reminders_from_memory",
]
