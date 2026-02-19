"""Deterministic task scheduler with TOCTOU-safe dispatch.

Routes queued tasks to idle workers using strict selection order:
target_cli -> target_account -> longest idle (idle_since ASC).

Uses direct CAS + event insert for compound operations that need
multiple state changes in a single transaction (dispatch, complete, fail).
Uses apply_transition for simple standalone transitions (ack).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.router.db import RouterDB
from src.router.dependency import check_dependencies
from src.router.fsm import TransitionRequest, apply_transition, validate_transition
from src.router.models import Lease, Task, TaskEvent, TaskStatus, Worker
from src.router.verifier import VerifierGate

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DispatchResult:
    """Result of a successful dispatch."""

    task: Task
    worker: Worker
    lease: Lease


class _CASFailure(Exception):
    """Internal: CAS check failed, used to abort transaction cleanly."""


class Scheduler:
    """Deterministic task scheduler with atomic dispatch."""

    def __init__(
        self,
        db: RouterDB,
        lease_duration_s: int = 300,
        review_timeout_s: int = 3600,
    ) -> None:
        self._db = db
        self._lease_duration_s = lease_duration_s
        self._review_timeout_s = review_timeout_s
        self._verifier = VerifierGate()

    def find_all_eligible_workers(self, task: Task) -> list[Worker]:
        """Find all eligible workers for a task, sorted by idle_since ASC."""
        idle_workers = self._db.list_workers(status="idle")
        eligible = [
            w
            for w in idle_workers
            if w.cli_type == task.target_cli
            and w.account_profile == task.target_account
        ]
        eligible.sort(key=lambda w: w.idle_since or "")
        return eligible

    def find_next_task(self) -> Task | None:
        """Find the next schedulable queued task."""
        now = _utc_now()
        queued = self._db.list_queued_tasks()
        for task in queued:
            if task.not_before and task.not_before > now:
                continue
            if task.depends_on:
                resolved, _ = check_dependencies(self._db, task.task_id)
                if not resolved:
                    continue
            return task
        return None

    def dispatch(self) -> DispatchResult | None:
        """Dispatch a queued task to an idle worker."""
        task = self.find_next_task()
        if task is None:
            return None

        candidates = self.find_all_eligible_workers(task)
        if not candidates:
            return None

        for candidate in candidates:
            result = self._try_dispatch(task, candidate)
            if result is not None:
                return result

        return None

    def _try_dispatch(
        self, task: Task, worker: Worker
    ) -> DispatchResult | None:
        """Attempt atomic dispatch: CAS worker idle->busy + task queued->assigned + lease."""
        expires_at = (
            datetime.now(timezone.utc)
            + timedelta(seconds=self._lease_duration_s)
        ).isoformat()

        try:
            with self._db.transaction() as conn:
                # CAS: verify worker still idle
                cur = conn.execute(
                    "UPDATE workers SET status = 'busy' WHERE worker_id = ? AND status = 'idle'",
                    (worker.worker_id,),
                )
                if cur.rowcount == 0:
                    raise _CASFailure()

                # CAS: task queued -> assigned
                if not validate_transition(TaskStatus.queued, TaskStatus.assigned):
                    raise _CASFailure()
                cas_ok = self._db.update_task_status(
                    task.task_id, TaskStatus.queued, TaskStatus.assigned, conn=conn
                )
                if not cas_ok:
                    raise _CASFailure()

                # Emit transition event
                self._db.insert_event(
                    TaskEvent(
                        task_id=task.task_id,
                        event_type="state_transition",
                        payload={
                            "from": "queued",
                            "to": "assigned",
                            "reason": "scheduler_dispatch",
                        },
                    ),
                    conn=conn,
                )

                # Create lease
                lease = Lease(
                    task_id=task.task_id,
                    worker_id=worker.worker_id,
                    expires_at=expires_at,
                )
                self._db.create_lease(lease, conn=conn)

                # Update task denormalized fields
                self._db.update_task_fields(
                    task.task_id,
                    {
                        "assigned_worker": worker.worker_id,
                        "lease_expires_at": expires_at,
                    },
                    conn=conn,
                )

                return DispatchResult(task=task, worker=worker, lease=lease)

        except _CASFailure:
            return None

    def ack_task(self, task_id: str, worker_id: str) -> bool:
        """Worker acknowledges task: assigned -> running."""
        task = self._db.get_task(task_id)
        if task is None or task.assigned_worker != worker_id:
            return False

        request = TransitionRequest(
            task_id=task_id,
            from_status=TaskStatus.assigned,
            to_status=TaskStatus.running,
            reason="worker_ack",
        )
        result = apply_transition(self._db, request)
        return result.success

    def complete_task(self, task_id: str, worker_id: str) -> bool:
        """Worker reports task completion.

        Critical tasks: running -> review (with review_timeout_at set).
        Non-critical tasks: running -> completed + cleanup.
        """
        task = self._db.get_task(task_id)
        if task is None or task.assigned_worker != worker_id:
            return False

        if self._verifier.should_review(task):
            return self._route_to_review(task, worker_id)

        return self._route_to_completed(task, worker_id)

    def _route_to_review(self, task: Task, worker_id: str) -> bool:
        """Route a critical task to review state."""
        review_timeout = (
            datetime.now(timezone.utc)
            + timedelta(seconds=self._review_timeout_s)
        ).isoformat()

        with self._db.transaction() as conn:
            cas_ok = self._db.update_task_status(
                task.task_id, TaskStatus.running, TaskStatus.review, conn=conn
            )
            if not cas_ok:
                return False

            self._db.insert_event(
                TaskEvent(
                    task_id=task.task_id,
                    event_type="state_transition",
                    payload={
                        "from": "running",
                        "to": "review",
                        "reason": "critical_task_review",
                    },
                ),
                conn=conn,
            )

            self._db.update_task_fields(
                task.task_id,
                {"review_timeout_at": review_timeout},
                conn=conn,
            )

            lease = self._db.get_active_lease(task.task_id)
            if lease:
                self._db.expire_lease(lease.lease_id, conn=conn)

            self._db.update_worker(
                worker_id,
                {"status": "idle", "idle_since": _utc_now()},
                conn=conn,
            )

        return True

    def _route_to_completed(self, task: Task, worker_id: str) -> bool:
        """Route a non-critical task directly to completed."""
        with self._db.transaction() as conn:
            cas_ok = self._db.update_task_status(
                task.task_id, TaskStatus.running, TaskStatus.completed, conn=conn
            )
            if not cas_ok:
                return False

            self._db.insert_event(
                TaskEvent(
                    task_id=task.task_id,
                    event_type="state_transition",
                    payload={"from": "running", "to": "completed", "reason": "worker_complete"},
                ),
                conn=conn,
            )

            lease = self._db.get_active_lease(task.task_id)
            if lease:
                self._db.expire_lease(lease.lease_id, conn=conn)

            self._db.update_worker(
                worker_id,
                {"status": "idle", "idle_since": _utc_now()},
                conn=conn,
            )

        from src.router.dependency import on_task_terminal
        on_task_terminal(self._db, task.task_id)
        return True

    def report_failure(
        self, task_id: str, worker_id: str, reason: str = ""
    ) -> bool:
        """Worker reports task failure: running -> failed + cleanup."""
        task = self._db.get_task(task_id)
        if task is None or task.assigned_worker != worker_id:
            return False

        with self._db.transaction() as conn:
            cas_ok = self._db.update_task_status(
                task_id, TaskStatus.running, TaskStatus.failed, conn=conn
            )
            if not cas_ok:
                return False

            self._db.insert_event(
                TaskEvent(
                    task_id=task_id,
                    event_type="state_transition",
                    payload={"from": "running", "to": "failed", "reason": f"worker_failure: {reason}"},
                ),
                conn=conn,
            )

            lease = self._db.get_active_lease(task_id)
            if lease:
                self._db.expire_lease(lease.lease_id, conn=conn)

            self._db.update_worker(
                worker_id,
                {"status": "idle", "idle_since": _utc_now()},
                conn=conn,
            )

        from src.router.dependency import on_task_terminal
        on_task_terminal(self._db, task_id)
        return True
