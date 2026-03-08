"""Heartbeat receiver and stale detection sweep.

Handles worker heartbeat updates, ghost execution prevention,
and periodic stale worker detection with task cleanup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.router.db import RouterDB
from src.router.longpoll import LongPollRegistry
from src.router.models import TaskEvent, TaskStatus, Worker

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid4() -> str:
    import uuid
    return str(uuid.uuid4())


@dataclass
class SweepResult:
    """Result of a stale detection sweep."""

    workers_marked_stale: int = 0
    tasks_requeued: int = 0
    tasks_failed: int = 0
    errors: list[str] = field(default_factory=list)


def requeue_task(
    db: RouterDB,
    task_id: str,
    reason: str,
    max_attempts: int = 3,
    conn=None,
) -> tuple[bool, bool]:
    """Shared requeue logic: requeue or fail a task based on attempt count.

    Returns (requeued: bool, failed_terminal: bool).
    Used by heartbeat stale sweep, worker deregister, and RetryPolicy.
    """
    task = db.get_task(task_id)
    if task is None:
        return False, False

    new_attempt = task.attempt + 1
    if new_attempt <= max_attempts:
        db.update_task_status(task_id, task.status, TaskStatus.queued, conn=conn)
        db.update_task_fields(
            task_id,
            {
                "attempt": new_attempt,
                "assigned_worker": None,
                "lease_expires_at": None,
            },
            conn=conn,
        )
        db.insert_event(
            TaskEvent(
                task_id=task_id,
                event_type=f"{reason}_requeue",
                payload={"new_attempt": new_attempt, "reason": reason},
                idempotency_key=f"{reason}-requeue-{task_id}-{new_attempt}",
            ),
            conn=conn,
        )
        return True, False
    else:
        db.update_task_status(task_id, task.status, TaskStatus.failed, conn=conn)
        db.update_task_fields(
            task_id,
            {"assigned_worker": None, "lease_expires_at": None},
            conn=conn,
        )
        db.insert_event(
            TaskEvent(
                task_id=task_id,
                event_type=f"{reason}_max_attempts",
                payload={"attempt": task.attempt, "reason": reason},
                idempotency_key=f"{reason}-failed-{task_id}-{task.attempt}",
            ),
            conn=conn,
        )
        return False, True


class HeartbeatManager:
    """Manages worker heartbeat protocol and stale detection."""

    def __init__(
        self,
        db: RouterDB,
        stale_threshold_s: int = 35,
        max_attempts: int = 3,
        lease_duration_s: int = 300,
        longpoll_registry: LongPollRegistry | None = None,
    ) -> None:
        self._db = db
        self._stale_threshold_s = stale_threshold_s
        self._max_attempts = max_attempts
        self._lease_duration_s = lease_duration_s
        self._longpoll_registry = longpoll_registry

    def receive_heartbeat(self, worker_id: str) -> dict:
        """Process a heartbeat from a worker.

        Returns response dict indicating worker status.
        """
        worker = self._db.get_worker(worker_id)
        if worker is None:
            return {"status": "unknown_worker"}

        now = _utc_now()

        if worker.status == "offline":
            return {"status": "offline"}

        if worker.status == "stale":
            # Recover from stale: transition back to idle
            self._db.update_worker(
                worker_id,
                {
                    "status": "idle",
                    "last_heartbeat": now,
                    "idle_since": now,
                    "stale_since": None,
                },
            )
            # Find tasks that were requeued due to this worker going stale
            events = self._db.get_events(worker_id)
            requeued_tasks = [
                e.payload.get("task_id", e.task_id)
                for e in events
                if e.event_type == "worker_stale"
                and e.ts >= (worker.stale_since or "")
            ]
            logger.info(
                "Worker %s recovered from stale, requeued tasks: %s",
                worker_id,
                requeued_tasks,
            )
            return {"status": "stale_recovered", "requeued_tasks": requeued_tasks}

        if worker.status in ("idle", "busy"):
            self._db.update_worker(worker_id, {"last_heartbeat": now})
            self._renew_active_leases(worker_id, now)
            return {"status": "ok"}

        return {"status": "unknown"}

    def _renew_active_leases(self, worker_id: str, now: str) -> None:
        """Extend leases for active tasks owned by a healthy worker."""
        new_expiry = (
            datetime.now(timezone.utc) + timedelta(seconds=self._lease_duration_s)
        ).isoformat()

        with self._db.transaction() as conn:
            for lease in self._db.list_worker_leases(worker_id):
                task = self._db.get_task(lease.task_id)
                if task is None or task.status.value not in ("assigned", "running"):
                    continue
                self._db.renew_lease(lease.lease_id, new_expiry, conn=conn)
                self._db.update_task_fields(
                    task.task_id,
                    {"lease_expires_at": new_expiry},
                    conn=conn,
                )

    def run_stale_sweep(self) -> SweepResult:
        """Detect stale workers and clean up their tasks.

        Each worker's cleanup runs in a single atomic transaction.
        """
        result = SweepResult()
        threshold = (
            datetime.now(timezone.utc)
            - timedelta(seconds=self._stale_threshold_s)
        ).isoformat()

        candidates = self._db.list_stale_candidates(threshold)
        if not candidates:
            return result

        for worker in candidates:
            try:
                self._sweep_one_worker(worker, result)
            except Exception as e:
                result.errors.append(
                    f"Error sweeping worker {worker.worker_id}: {e}"
                )
                logger.error(
                    "Stale sweep error for worker %s: %s",
                    worker.worker_id,
                    e,
                )

        return result

    def _sweep_one_worker(
        self, worker: Worker, result: SweepResult
    ) -> None:
        """Clean up a single stale worker within one atomic transaction."""
        now = _utc_now()

        with self._db.transaction() as conn:
            # Mark worker as stale
            self._db.update_worker(
                worker.worker_id,
                {"status": "stale", "stale_since": now},
                conn=conn,
            )
            result.workers_marked_stale += 1

            # Clean up long-poll Condition for stale worker
            if self._longpoll_registry is not None:
                self._longpoll_registry.unregister(worker.worker_id)

            # Find and process all leases for this worker
            leases = self._db.list_worker_leases(worker.worker_id)
            for lease in leases:
                task = self._db.get_task(lease.task_id)
                if task and task.status.value in ("assigned", "running"):
                    self._db.expire_lease(lease.lease_id, conn=conn)
                    requeued, failed = requeue_task(
                        self._db,
                        task.task_id,
                        "worker_stale",
                        self._max_attempts,
                        conn=conn,
                    )
                    if requeued:
                        result.tasks_requeued += 1
                    elif failed:
                        result.tasks_failed += 1

            # Emit worker stale event
            self._db.insert_event(
                TaskEvent(
                    task_id=worker.worker_id,
                    event_type="worker_stale",
                    payload={
                        "worker_id": worker.worker_id,
                        "tasks_requeued": result.tasks_requeued,
                        "tasks_failed": result.tasks_failed,
                    },
                    idempotency_key=_uuid4(),
                ),
                conn=conn,
            )
