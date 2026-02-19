"""Bounded retry policy with backoff and configurable escalation callbacks.

Builds on the shared requeue_task() helper from heartbeat module,
adding not_before calculation, escalation callbacks, and unschedulable detection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from src.router.db import RouterDB
from src.router.heartbeat import requeue_task
from src.router.models import Task, TaskEvent

logger = logging.getLogger(__name__)

_DEFAULT_BACKOFF = [15, 60, 180]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid4() -> str:
    import uuid
    return str(uuid.uuid4())


@runtime_checkable
class EscalationCallback(Protocol):
    """Protocol for escalation callbacks invoked when retries are exhausted."""

    def on_escalation(
        self,
        task: Task,
        last_worker_id: str | None,
        attempt: int,
        reason: str,
    ) -> None: ...


class LogEscalation:
    """Default escalation callback that logs a warning."""

    def on_escalation(
        self,
        task: Task,
        last_worker_id: str | None,
        attempt: int,
        reason: str,
    ) -> None:
        logger.warning(
            "ESCALATION: task %s failed after %d attempts. "
            "Last worker: %s. Reason: %s",
            task.task_id,
            attempt,
            last_worker_id,
            reason,
        )


@dataclass
class RetryResult:
    """Result of a retry attempt."""

    retried: bool
    escalated: bool = False
    new_attempt: int = 0
    not_before: str | None = None
    error: str | None = None


class RetryPolicy:
    """Bounded retry with backoff and escalation."""

    def __init__(
        self,
        db: RouterDB,
        max_attempts: int = 3,
        backoff_schedule: list[int] | None = None,
        escalation_callbacks: list[EscalationCallback] | None = None,
        unschedulable_timeout_s: int = 1800,
    ) -> None:
        self._db = db
        self._max_attempts = max_attempts
        self._backoff = backoff_schedule or _DEFAULT_BACKOFF
        self._callbacks = escalation_callbacks or []
        self._unschedulable_timeout_s = unschedulable_timeout_s

    def should_retry(self, task: Task) -> bool:
        """Check if task has retries remaining."""
        return task.attempt < self._max_attempts

    def calculate_not_before(self, task: Task) -> str:
        """Calculate the not_before timestamp based on backoff schedule."""
        idx = min(task.attempt - 1, len(self._backoff) - 1)
        delay = self._backoff[idx]
        return (
            datetime.now(timezone.utc) + timedelta(seconds=delay)
        ).isoformat()

    def requeue_with_backoff(
        self, task_id: str, reason: str
    ) -> RetryResult:
        """Requeue a task with backoff, or escalate if retries exhausted."""
        task = self._db.get_task(task_id)
        if task is None:
            return RetryResult(retried=False, error="task_not_found")

        if self.should_retry(task):
            not_before = self.calculate_not_before(task)
            # Use shared requeue logic
            requeued, _ = requeue_task(
                self._db, task_id, reason, self._max_attempts
            )
            if requeued:
                # Set not_before for backoff
                self._db.update_task_fields(
                    task_id, {"not_before": not_before}
                )
                return RetryResult(
                    retried=True,
                    new_attempt=task.attempt + 1,
                    not_before=not_before,
                )
            return RetryResult(retried=False, error="requeue_failed")
        else:
            # Max attempts exhausted — fail and escalate
            _, failed = requeue_task(
                self._db, task_id, reason, self._max_attempts
            )
            if failed:
                # Emit escalation event
                self._db.insert_event(
                    TaskEvent(
                        task_id=task_id,
                        event_type="escalation_to_boss",
                        payload={
                            "attempt": task.attempt,
                            "reason": reason,
                            "last_worker": task.assigned_worker,
                        },
                        idempotency_key=f"escalate-{task_id}-{task.attempt}",
                    )
                )
                # Invoke all escalation callbacks
                for cb in self._callbacks:
                    try:
                        cb.on_escalation(
                            task, task.assigned_worker, task.attempt, reason
                        )
                    except Exception as e:
                        logger.error(
                            "Escalation callback error: %s", e
                        )
                return RetryResult(
                    retried=False,
                    escalated=True,
                    new_attempt=task.attempt,
                )
            return RetryResult(retried=False, error="fail_failed")

    def find_unschedulable_tasks(self) -> list[Task]:
        """Find tasks queued longer than the unschedulable timeout."""
        threshold = (
            datetime.now(timezone.utc)
            - timedelta(seconds=self._unschedulable_timeout_s)
        ).isoformat()
        return self._db.list_queued_tasks(before_iso=threshold)

    def emit_unschedulable_events(self) -> int:
        """Emit events for unschedulable tasks (idempotent per day per task)."""
        tasks = self.find_unschedulable_tasks()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count = 0
        for task in tasks:
            idem_key = f"unschedulable-{task.task_id}-{today}"
            inserted = self._db.insert_event(
                TaskEvent(
                    task_id=task.task_id,
                    event_type="task_unschedulable",
                    payload={
                        "queued_since": task.created_at,
                        "timeout_s": self._unschedulable_timeout_s,
                    },
                    idempotency_key=idem_key,
                )
            )
            if inserted:
                count += 1
        return count
