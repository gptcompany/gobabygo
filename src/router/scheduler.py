"""Deterministic task scheduler with TOCTOU-safe dispatch.

Routes queued tasks to idle workers using strict selection order:
target_cli -> target_account -> longest idle (idle_since ASC).

Uses direct CAS + event insert for compound operations that need
multiple state changes in a single transaction (dispatch, complete, fail).
Uses apply_transition for simple standalone transitions (ack).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from typing import Any

from src.router.db import RouterDB
from src.router.dependency import check_dependencies
from src.router.fsm import TransitionRequest, apply_transition, validate_transition
from src.router.longpoll import LongPollRegistry
from src.router.models import Lease, Task, TaskEvent, TaskStatus, Worker
from src.router.topology import Topology
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
        longpoll_registry: LongPollRegistry | None = None,
        topology: Topology | None = None,
    ) -> None:
        self._db = db
        self._lease_duration_s = lease_duration_s
        self._review_timeout_s = review_timeout_s
        self._verifier = VerifierGate()
        self._longpoll_registry = longpoll_registry
        self._topology = topology

    def find_all_eligible_workers(self, task: Task) -> list[Worker]:
        """Find all eligible workers for a task, sorted by idle_since ASC."""
        idle_workers = self._db.list_workers(status="idle")
        task_mode = task.execution_mode.value if hasattr(task.execution_mode, "value") else str(task.execution_mode)
        eligible = [
            w
            for w in idle_workers
            if w.cli_type == task.target_cli
            and w.account_profile == task.target_account
            and task_mode in (w.execution_modes or ["batch"])
        ]
        # Topology-aware filter: restrict to repo worker pool if defined
        if self._topology and task.repo:
            pool = self._topology.get_repo_worker_pool(task.repo)
            if pool is not None:
                pool_set = set(pool)
                eligible = [w for w in eligible if w.worker_id in pool_set]
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

                # Transition thread pending -> active on first dispatch
                if task.thread_id:
                    thread = self._db.get_thread(task.thread_id)
                    if thread and thread.status.value == "pending":
                        self._db.update_thread(
                            task.thread_id,
                            {"status": "active", "updated_at": _utc_now()},
                            conn=conn,
                        )

                result = DispatchResult(task=task, worker=worker, lease=lease)

        except _CASFailure:
            return None

        # Notify AFTER transaction commits so DB state is visible to the worker
        if result is not None and self._longpoll_registry is not None:
            self._longpoll_registry.notify_task_available(worker.worker_id)
        return result

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

    def _update_worker_post_task(
        self, worker_id: str, completed_task_id: str, conn, reason: str
    ) -> None:
        """After task finalization, either idle the worker or auto-retire if draining."""
        worker = self._db.get_worker(worker_id)
        if worker and worker.status == "draining":
            remaining_running = self._db.get_tasks_by_worker(worker_id, status="running")
            remaining_assigned = self._db.get_tasks_by_worker(worker_id, status="assigned")
            remaining = [
                t for t in remaining_running + remaining_assigned
                if t.task_id != completed_task_id
            ]
            if not remaining:
                self._db.update_worker(worker_id, {"status": "offline"}, conn=conn)
                if self._longpoll_registry is not None:
                    self._longpoll_registry.unregister(worker_id)
                logger.info("Draining worker %s auto-retired (%s)", worker_id, reason)
            else:
                logger.info(
                    "Draining worker %s has %d remaining tasks",
                    worker_id, len(remaining),
                )
        else:
            self._db.update_worker(
                worker_id,
                {"status": "idle", "idle_since": _utc_now()},
                conn=conn,
            )

    def complete_task(
        self, task_id: str, worker_id: str, result: dict[str, Any] | None = None
    ) -> bool:
        """Worker reports task completion.

        Critical tasks: running -> review (with review_timeout_at set).
        Non-critical tasks: running -> completed + cleanup.
        Result (if provided) is sanitized and persisted in the same transaction.
        """
        task = self._db.get_task(task_id)
        if task is None or task.assigned_worker != worker_id:
            return False

        if self._verifier.should_review(task):
            return self._route_to_review(task, worker_id, result=result)

        return self._route_to_completed(task, worker_id, result=result)

    def _route_to_review(
        self, task: Task, worker_id: str, result: dict[str, Any] | None = None
    ) -> bool:
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

            # Persist result in same transaction
            update_fields: dict[str, str | int | None] = {"review_timeout_at": review_timeout}
            if result is not None:
                sanitized = self._db._sanitize_result(result)
                update_fields["result_json"] = sanitized
            self._db.update_task_fields(
                task.task_id,
                update_fields,
                conn=conn,
            )

            # Update thread status if this task belongs to a thread
            if task.thread_id:
                from src.router.thread import compute_thread_status
                new_thread_status = compute_thread_status(self._db, task.thread_id)
                self._db.update_thread(
                    task.thread_id,
                    {"status": new_thread_status.value, "updated_at": _utc_now()},
                    conn=conn,
                )

            lease = self._db.get_active_lease(task.task_id)
            if lease:
                self._db.expire_lease(lease.lease_id, conn=conn)

            self._update_worker_post_task(worker_id, task.task_id, conn, "last task to review")

        return True

    def _route_to_completed(
        self, task: Task, worker_id: str, result: dict[str, Any] | None = None
    ) -> bool:
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

            # Persist result in same transaction
            if result is not None:
                sanitized = self._db._sanitize_result(result)
                self._db.update_task_fields(
                    task.task_id,
                    {"result_json": sanitized},
                    conn=conn,
                )

            # Update thread status if this task belongs to a thread
            if task.thread_id:
                from src.router.thread import compute_thread_status
                new_thread_status = compute_thread_status(self._db, task.thread_id)
                self._db.update_thread(
                    task.thread_id,
                    {"status": new_thread_status.value, "updated_at": _utc_now()},
                    conn=conn,
                )

            lease = self._db.get_active_lease(task.task_id)
            if lease:
                self._db.expire_lease(lease.lease_id, conn=conn)

            self._update_worker_post_task(worker_id, task.task_id, conn, "last task completed")

        from src.router.dependency import on_task_terminal
        on_task_terminal(self._db, task.task_id)
        return True

    def report_failure(
        self, task_id: str, worker_id: str, reason: str = ""
    ) -> bool:
        """Worker reports task failure: running -> failed + cleanup.

        If the task has on_failure=retry and attempts remain, the task is
        requeued with backoff instead of transitioning to failed.
        """
        task = self._db.get_task(task_id)
        if task is None or task.assigned_worker != worker_id:
            return False

        # Retry policy: requeue instead of failing if retries remain
        if task.on_failure == "retry" and task.attempt < 3:
            return self._retry_step(task, worker_id, reason)

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

            # Update thread status if this task belongs to a thread
            if task.thread_id:
                from src.router.thread import compute_thread_status
                new_thread_status = compute_thread_status(self._db, task.thread_id)
                self._db.update_thread(
                    task.thread_id,
                    {"status": new_thread_status.value, "updated_at": _utc_now()},
                    conn=conn,
                )

            lease = self._db.get_active_lease(task_id)
            if lease:
                self._db.expire_lease(lease.lease_id, conn=conn)

            self._update_worker_post_task(worker_id, task_id, conn, "last task failed")

        from src.router.dependency import on_task_terminal
        on_task_terminal(self._db, task_id)
        return True

    def _retry_step(self, task: Task, worker_id: str, reason: str) -> bool:
        """Requeue a step for retry with backoff. Task stays non-terminal."""
        from src.router.retry import RetryPolicy
        retry_policy = RetryPolicy(self._db)
        not_before = retry_policy.calculate_not_before(task)
        new_attempt = task.attempt + 1

        with self._db.transaction() as conn:
            # running -> queued (requeue for retry)
            cas_ok = self._db.update_task_status(
                task.task_id, TaskStatus.running, TaskStatus.queued, conn=conn
            )
            if not cas_ok:
                return False

            self._db.insert_event(
                TaskEvent(
                    task_id=task.task_id,
                    event_type="state_transition",
                    payload={
                        "from": "running",
                        "to": "queued",
                        "reason": f"retry_requeue: {reason}",
                    },
                ),
                conn=conn,
            )

            self._db.update_task_fields(
                task.task_id,
                {
                    "attempt": new_attempt,
                    "not_before": not_before,
                    "assigned_worker": None,
                    "lease_expires_at": None,
                },
                conn=conn,
            )

            self._db.insert_event(
                TaskEvent(
                    task_id=task.task_id,
                    event_type="step_retry_requeued",
                    payload={
                        "attempt": new_attempt,
                        "reason": f"retry: {reason}",
                        "not_before": not_before,
                    },
                ),
                conn=conn,
            )

            # Thread stays active (task is not terminal)
            if task.thread_id:
                from src.router.thread import compute_thread_status
                new_thread_status = compute_thread_status(self._db, task.thread_id)
                self._db.update_thread(
                    task.thread_id,
                    {"status": new_thread_status.value, "updated_at": _utc_now()},
                    conn=conn,
                )

            lease = self._db.get_active_lease(task.task_id)
            if lease:
                self._db.expire_lease(lease.lease_id, conn=conn)

            self._update_worker_post_task(worker_id, task.task_id, conn, "step retrying")

        # Do NOT call on_task_terminal — task is not terminal (requeued)
        logger.info(
            "step_retry task=%s attempt=%d/%d not_before=%s",
            task.task_id, new_attempt, 3, not_before,
        )
        return True
