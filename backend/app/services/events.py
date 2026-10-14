"""In-process event bus for WebSocket progress + live updates.

Long operations (generation, image analysis, exports) publish progress here and the
``/ws/projects/{id}`` endpoint relays it to every browser watching that project.
Deliberately single-process and dependency free: it is an asyncio queue per
subscriber, so a slow client can never block a generation.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from ..schemas import utcnow

QUEUE_SIZE = 200


class EventBus:
    def __init__(self) -> None:
        self._channels: Dict[str, Set[asyncio.Queue]] = defaultdict(set)
        self._recent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    # -- subscribe ---------------------------------------------------------
    def subscribe(self, channel: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._channels[channel].add(queue)
        # replay the last few events so a client that connects mid-operation
        # still sees what is happening
        for event in self._recent[channel][-5:]:
            self._put(queue, event)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        self._channels[channel].discard(queue)
        if not self._channels[channel]:
            self._channels.pop(channel, None)

    def listeners(self, channel: str) -> int:
        return len(self._channels.get(channel, ()))

    # -- publish -----------------------------------------------------------
    def publish(self, channel: str, event_type: str, **payload: Any) -> None:
        event: Dict[str, Any] = {"type": event_type, "channel": channel,
                                 "at": utcnow().isoformat(), **payload}
        recent = self._recent[channel]
        recent.append(event)
        if len(recent) > 40:
            del recent[: len(recent) - 40]
        for queue in list(self._channels.get(channel, ())):
            self._put(queue, event)

    @staticmethod
    def _put(queue: asyncio.Queue, event: Dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # drop the oldest event rather than block the publisher
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    # -- consume -----------------------------------------------------------
    async def stream(self, channel: str) -> AsyncIterator[Dict[str, Any]]:
        queue = self.subscribe(channel)
        try:
            while True:
                yield await queue.get()
        finally:
            self.unsubscribe(channel, queue)


bus = EventBus()


# ---------------------------------------------------------------------------
# convenience helpers used by the services
# ---------------------------------------------------------------------------

def project_channel(project_id: str) -> str:
    return f"project:{project_id}"


def global_channel() -> str:
    return "global"


def progress(project_id: str, stage: str, percent: float, message: str = "",
             total: Optional[float] = None) -> None:
    bus.publish(project_channel(project_id), "progress", stage=stage,
                percent=round(max(0.0, min(100.0, percent)), 1), message=message,
                total=total)


def done(project_id: str, stage: str, message: str = "", **payload: Any) -> None:
    bus.publish(project_channel(project_id), "stage_complete", stage=stage,
                message=message, **payload)


def failed(project_id: str, stage: str, message: str, **payload: Any) -> None:
    bus.publish(project_channel(project_id), "error", stage=stage, message=message, **payload)


def project_changed(project_id: str, reason: str = "saved", **payload: Any) -> None:
    bus.publish(project_channel(project_id), "project_changed", reason=reason, **payload)


def notify(message: str, level: str = "info", **payload: Any) -> None:
    bus.publish(global_channel(), "notification", message=message, level=level, **payload)


class ProgressTracker:
    """Small helper for multi-stage operations.

    ``with ProgressTracker(project_id, [("generate", 60), ("validate", 20)]) as t:``
    then ``t.step("generate", "Partitioning rooms")``.
    """

    def __init__(self, project_id: str, stages: List[tuple]) -> None:
        self.project_id = project_id
        self.stages = stages
        self.total_weight = float(sum(w for _, w in stages)) or 1.0
        self.index = -1
        self.base = 0.0

    def __enter__(self) -> "ProgressTracker":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            progress(self.project_id, "complete", 100.0, "Finished")
        else:
            failed(self.project_id, self.stages[max(self.index, 0)][0] if self.stages else "task",
                   str(exc))

    def step(self, stage: Optional[str] = None, message: str = "", fraction: float = 0.0) -> None:
        if stage is not None:
            names = [s for s, _ in self.stages]
            if stage in names:
                self.index = names.index(stage)
            else:
                self.index += 1
            self.base = sum(w for _, w in self.stages[:max(self.index, 0)]) / self.total_weight
            weight = self.stages[max(self.index, 0)][1] / self.total_weight if self.stages else 1.0
        else:
            weight = (self.stages[self.index][1] / self.total_weight
                      if self.stages and 0 <= self.index < len(self.stages) else 1.0)
        percent = (self.base + weight * max(0.0, min(1.0, fraction))) * 100.0
        progress(self.project_id, stage or (self.stages[self.index][0] if self.stages else "task"),
                 percent, message)


def serialise(event: Dict[str, Any]) -> str:
    return json.dumps(event, default=str)
