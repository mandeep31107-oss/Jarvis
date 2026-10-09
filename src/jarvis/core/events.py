"""A tiny thread-safe event bus.

Spec section 37 calls for an event-driven core, and sections 21/30 need the UI
to see live agent activity. This is deliberately synchronous-and-cheap: handlers
run on the publishing thread, so a slow handler is a bug in the handler.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jarvis.util.clock import now_iso
from jarvis.util.ids import new_id

log = logging.getLogger("jarvis.events")

Handler = Callable[["Event"], None]


@dataclass
class Event:
    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("ev"))
    at: str = field(default_factory=now_iso)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "topic": self.topic, "at": self.at, "payload": dict(self.payload)}


class EventBus:
    """Publish/subscribe with a bounded replay ring buffer.

    The ring buffer is what lets a dashboard that opens late still show the most
    recent activity instead of a blank screen.
    """

    def __init__(self, history: int = 500) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._wildcards: list[Handler] = []
        self._lock = threading.RLock()
        self._history: deque[Event] = deque(maxlen=history)

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Subscribe to ``topic``; ``*`` receives everything. Returns an unsubscribe fn."""
        with self._lock:
            if topic == "*":
                self._wildcards.append(handler)
            else:
                self._handlers[topic].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                bucket = self._wildcards if topic == "*" else self._handlers.get(topic, [])
                if handler in bucket:
                    bucket.remove(handler)

        return unsubscribe

    def publish(self, topic: str, **payload: Any) -> Event:
        event = Event(topic=topic, payload=payload)
        with self._lock:
            self._history.append(event)
            handlers = list(self._handlers.get(topic, ())) + list(self._wildcards)
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # noqa: BLE001 - one bad subscriber must not break the bus
                log.exception("event handler failed for topic %s", topic)
        return event

    def recent(self, limit: int = 50, topic: str | None = None) -> list[Event]:
        with self._lock:
            events = list(self._history)
        if topic:
            events = [e for e in events if e.topic == topic]
        return events[-limit:]
