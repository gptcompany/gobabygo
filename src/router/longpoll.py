"""Long-poll registry for per-worker blocking task dispatch.

Manages per-worker threading.Condition objects with state predicate pattern.
Workers block on the server until a task is available or timeout expires.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.router.db import RouterDB
    from src.router.models import Task


@dataclass
class PollResult:
    """Result of a long-poll wait operation."""

    task: Task | None = None
    conflict: bool = False


@dataclass
class _WorkerSlot:
    """Per-worker long-poll state."""

    condition: threading.Condition = field(default_factory=threading.Condition)
    task_available: bool = False
    in_flight_poll: bool = False
    in_flight_since: float | None = None  # time.monotonic() timestamp


class LongPollRegistry:
    """Registry of per-worker Condition objects for long-poll blocking.

    Thread-safe: registry-level mutations use ``_lock``, per-slot mutations
    use each slot's own ``Condition`` lock.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: dict[str, _WorkerSlot] = {}

    def register(self, worker_id: str) -> None:
        """Create or replace a Condition for *worker_id*.

        On re-registration the old Condition is replaced with a fresh one.
        """
        with self._lock:
            self._slots[worker_id] = _WorkerSlot()

    def unregister(self, worker_id: str) -> None:
        """Remove worker's slot from registry."""
        with self._lock:
            self._slots.pop(worker_id, None)

    def wait_for_task(
        self,
        worker_id: str,
        timeout_s: float,
        db: RouterDB,
    ) -> PollResult:
        """Block until a task is dispatched to *worker_id* or *timeout_s* expires.

        Returns:
            PollResult with task set on dispatch, conflict=True on duplicate
            concurrent poll, or task=None on timeout.
        """
        # --- registry-level check ---
        with self._lock:
            slot = self._slots.get(worker_id)
            if slot is None:
                slot = _WorkerSlot()
                self._slots[worker_id] = slot

            if slot.in_flight_poll:
                # Zombie detection: if older than timeout + 5s grace, reset
                if (
                    slot.in_flight_since is not None
                    and (time.monotonic() - slot.in_flight_since) > (timeout_s + 5.0)
                ):
                    # Zombie -- replace with fresh slot
                    slot = _WorkerSlot()
                    self._slots[worker_id] = slot
                else:
                    return PollResult(conflict=True)

            slot.in_flight_poll = True
            slot.in_flight_since = time.monotonic()

        # --- per-slot blocking wait ---
        deadline = time.monotonic() + timeout_s

        with slot.condition:
            while not slot.task_available:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                slot.condition.wait(timeout=remaining)

            if slot.task_available:
                slot.task_available = False
                slot.in_flight_poll = False
                tasks = db.get_tasks_by_worker(worker_id, status="assigned")
                return PollResult(task=tasks[0] if tasks else None)

        # Timeout path -- race condition mitigation: check DB one more time
        tasks = db.get_tasks_by_worker(worker_id, status="assigned")
        if tasks:
            slot.in_flight_poll = False
            return PollResult(task=tasks[0])

        slot.in_flight_poll = False
        return PollResult()

    def notify_task_available(self, worker_id: str) -> None:
        """Wake a waiting worker after the scheduler dispatches a task.

        If the worker is not currently polling, this is a no-op -- the task
        stays assigned in DB and will be picked up on next reconnect.
        """
        with self._lock:
            slot = self._slots.get(worker_id)
            if slot is None:
                return

        with slot.condition:
            slot.task_available = True
            slot.condition.notify()

    def waiting_count(self) -> int:
        """Return count of workers currently blocked in a long-poll."""
        with self._lock:
            return sum(1 for s in self._slots.values() if s.in_flight_poll)
