"""Always-active mode (spec sections 20, 21).

A background loop that keeps working. The design constraint that matters most
is the one the brief states twice: **the authorised user must always be able to
stop it.** A loop the user cannot halt is not autonomy, it is a malfunction.

So every iteration checks the stop flag first, before doing anything else, and
the check is not cached or debounced. `stop()` sets the flag and joins the
thread, so it returns only once the loop has actually ended.

The loop also refuses to work while an emergency stop is active, and it records
a heartbeat so "is it running?" is answerable from outside rather than by
guessing.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from jarvis.runtime import Runtime
from jarvis.util.clock import now_iso

log = logging.getLogger("jarvis.loop")


@dataclass
class LoopState:
    """Observable state of the background loop."""

    running: bool = False
    iterations: int = 0
    tasks_processed: int = 0
    skipped_paused: int = 0
    skipped_stopped: int = 0
    last_heartbeat: str = ""
    last_error: str = ""
    started_at: str = ""
    stopped_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "iterations": self.iterations,
            "tasks_processed": self.tasks_processed,
            "skipped_paused": self.skipped_paused,
            "skipped_stopped": self.skipped_stopped,
            "last_heartbeat": self.last_heartbeat,
            "last_error": self.last_error,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
        }


class AlwaysActiveLoop:
    """Runs the task queue in the background, stopping when told to."""

    def __init__(
        self,
        runtime: Runtime,
        *,
        interval_s: float = 2.0,
        max_tasks_per_tick: int = 1,
    ) -> None:
        self.runtime = runtime
        self.interval_s = max(0.05, float(interval_s))
        self.max_tasks_per_tick = max(1, int(max_tasks_per_tick))
        self.state = LoopState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ control
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> str:
        if self.running:
            return "Already active."
        self._stop.clear()
        self.state = LoopState(running=True, started_at=now_iso())
        self._thread = threading.Thread(
            target=self._loop, name="jarvis-always-active", daemon=True
        )
        self._thread.start()
        self.runtime.events.publish("loop.started", interval_s=self.interval_s)
        self.runtime.audit.log(
            "loop.started", agent="loop", result="always-active loop started",
            risk="low", meta={"interval_s": self.interval_s},
        )
        return (
            f"Always-active mode ON - checking every {self.interval_s:.1f}s. "
            "PAUSE pauses it, STOP halts it. Both take effect on the next tick."
        )

    def stop(self, *, timeout_s: float = 5.0) -> str:
        """Halt the loop and wait for it to actually stop.

        Returning before the thread has ended would let a caller believe the
        agent is stopped while one more task is still running.
        """
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout_s)
        alive = self.running
        self.state.running = False
        self.state.stopped_at = now_iso()
        self.runtime.audit.log(
            "loop.stopped",
            agent="loop",
            result="always-active loop stopped",
            risk="low",
            meta={
                "iterations": self.state.iterations,
                "tasks_processed": self.state.tasks_processed,
                "thread_still_alive": alive,
            },
        )
        if alive:
            return (
                "STOP signalled, but the loop had not finished its current tick "
                f"within {timeout_s:.1f}s. It is a daemon thread and will not "
                "survive the process."
            )
        return (
            f"Always-active mode OFF after {self.state.iterations} tick(s), "
            f"{self.state.tasks_processed} task(s) processed."
        )

    def pause(self) -> str:
        self.runtime.pause()
        return "PAUSED - the loop stays alive but does no work until you resume."

    def resume(self) -> str:
        return self.runtime.resume()

    # --------------------------------------------------------------------- loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            self.state.iterations += 1
            self.state.last_heartbeat = now_iso()

            # Checked every tick, in this order, and never cached.
            if self.runtime.emergency_stopped:
                self.state.skipped_stopped += 1
                self._sleep()
                continue
            if self.runtime.paused:
                self.state.skipped_paused += 1
                self._sleep()
                continue

            try:
                processed = self.runtime.tasks.run(
                    max_tasks=self.max_tasks_per_tick, wait=True
                )
                self.state.tasks_processed += processed
            except Exception as exc:  # noqa: BLE001 - one bad tick must not kill the loop
                self.state.last_error = f"{type(exc).__name__}: {exc}"
                log.exception("always-active loop tick failed")
                self.runtime.audit.log(
                    "loop.error",
                    agent="loop",
                    result="tick failed",
                    error=str(exc),
                    risk="low",
                    recovery="loop continues on the next tick",
                )
            self._sleep()

        self.state.running = False

    def _sleep(self) -> None:
        """Sleep in small slices so STOP lands quickly.

        Sleeping the whole interval would mean a stop request waits up to a full
        interval before it is even noticed.
        """
        deadline = time.monotonic() + self.interval_s
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))


__all__ = ["AlwaysActiveLoop", "LoopState"]
