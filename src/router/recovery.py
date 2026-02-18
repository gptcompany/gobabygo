"""Crash recovery module for the AI Mesh Router.

Recovers consistent state on startup by:
- Expiring stale leases (expires_at < now)
- Requeuing orphaned tasks (assigned/running with no active lease)
- Respecting max_attempts (3) to prevent infinite requeue loops
- Logging all recovery actions as TaskEvent entries

Recovery uses direct CAS (db.update_task_status) rather than FSM apply_transition
because:
1. Recovery transitions (running->queued, assigned->queued) are not in the FSM
   transition table — they are recovery-only paths that bypass normal workflow.
2. Recovery needs atomic compound operations (lease expiry + status change +
   attempt update + event insert) in a single transaction. The FSM manages
   its own transactions and cannot participate in recovery's compound txn.
3. The FSM validates normal workflow transitions; recovery is a startup
   consistency-restoration operation, not a normal workflow step.

Recovery is idempotent: running it twice on the same state produces
the same result (no double-requeue).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.router.db import RouterDB
from src.router.models import TaskEvent, TaskStatus


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RecoveryResult:
    """Summary of recovery actions taken on startup."""
    tasks_requeued: int = 0
    leases_expired: int = 0
    events_replayed: int = 0
    errors: list[str] = field(default_factory=list)


def recover_on_startup(db: RouterDB, max_attempts: int = 3) -> RecoveryResult:
    """Recover consistent state on router startup.

    Steps:
      a. Find all active leases where expires_at < now() -> expire each lease
      b. For each expired lease: get the associated task
         - If task.attempt < max_attempts: transition to queued, increment attempt
         - If task.attempt >= max_attempts: transition to failed (terminal),
           create escalation event
      c. Find tasks in assigned/running status with no active lease -> same logic
      d. Log all recovery actions as TaskEvent entries
      e. Return RecoveryResult with counts

    Idempotent: running twice on the same state produces the same result.
    Tasks already in queued/failed/completed states are not touched.
    """
    result = RecoveryResult()
    now = _utc_now()

    # --- Step a+b: Find and expire stale leases ---
    expired_leases = db._conn.execute(
        "SELECT * FROM leases WHERE expires_at < ?", (now,)
    ).fetchall()

    # Track task_ids processed via lease expiry to avoid double-processing in step c
    processed_task_ids: set[str] = set()

    for lease_row in expired_leases:
        lease_id = lease_row["lease_id"]
        task_id = lease_row["task_id"]

        task = db.get_task(task_id)
        if task is None:
            result.errors.append(f"Lease {lease_id} references missing task {task_id}")
            continue

        # Only process tasks that are still in assigned/running state
        # (idempotency: if already queued/failed, skip)
        if task.status not in (TaskStatus.assigned, TaskStatus.running):
            # Lease is stale but task already transitioned — just clean up lease
            with db.transaction() as conn:
                db.expire_lease(lease_id, conn=conn)
            result.leases_expired += 1
            processed_task_ids.add(task_id)
            continue

        with db.transaction() as conn:
            # Expire the lease
            db.expire_lease(lease_id, conn=conn)
            result.leases_expired += 1

            # Clear denormalized lease fields on task
            conn.execute(
                "UPDATE tasks SET assigned_worker = NULL, lease_expires_at = NULL, updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )

            if task.attempt < max_attempts:
                # Requeue with attempt+1 (recovery-only: running/assigned -> queued)
                new_attempt = task.attempt + 1
                transitioned = db.update_task_status(
                    task_id, task.status, TaskStatus.queued, conn=conn
                )
                if transitioned:
                    conn.execute(
                        "UPDATE tasks SET attempt = ?, updated_at = ? WHERE task_id = ?",
                        (new_attempt, now, task_id),
                    )
                    result.tasks_requeued += 1

                    # Log recovery event
                    event = TaskEvent(
                        task_id=task_id,
                        event_type="recovery_requeued",
                        payload={
                            "reason": "expired_lease",
                            "lease_id": lease_id,
                            "old_status": task.status.value,
                            "new_attempt": new_attempt,
                        },
                        idempotency_key=f"recovery-requeue-{task_id}-{new_attempt}",
                        ts=now,
                    )
                    db.insert_event(event, conn=conn)
                    result.events_replayed += 1
                else:
                    result.errors.append(
                        f"CAS failed for task {task_id}: expected {task.status.value}, concurrent modification"
                    )
            else:
                # Max attempts reached — terminal failure
                transitioned = db.update_task_status(
                    task_id, task.status, TaskStatus.failed, conn=conn
                )
                if transitioned:
                    # Escalation event
                    event = TaskEvent(
                        task_id=task_id,
                        event_type="recovery_max_attempts_exceeded",
                        payload={
                            "reason": "max_attempts_exceeded",
                            "lease_id": lease_id,
                            "attempt": task.attempt,
                            "max_attempts": max_attempts,
                        },
                        idempotency_key=f"recovery-failed-{task_id}-{task.attempt}",
                        ts=now,
                    )
                    db.insert_event(event, conn=conn)
                    result.events_replayed += 1
                else:
                    result.errors.append(
                        f"CAS failed for task {task_id}: expected {task.status.value}, concurrent modification"
                    )

        processed_task_ids.add(task_id)

    # --- Step c: Find orphaned tasks (assigned/running with no active lease) ---
    orphaned_rows = db._conn.execute(
        """SELECT task_id FROM tasks
           WHERE status IN ('assigned', 'running')
           AND task_id NOT IN (SELECT task_id FROM leases)""",
    ).fetchall()

    for row in orphaned_rows:
        task_id = row["task_id"]

        # Skip if already processed via lease expiry
        if task_id in processed_task_ids:
            continue

        task = db.get_task(task_id)
        if task is None:
            continue

        # Double-check status (idempotency)
        if task.status not in (TaskStatus.assigned, TaskStatus.running):
            continue

        with db.transaction() as conn:
            # Clear denormalized fields
            conn.execute(
                "UPDATE tasks SET assigned_worker = NULL, lease_expires_at = NULL, updated_at = ? WHERE task_id = ?",
                (now, task_id),
            )

            if task.attempt < max_attempts:
                new_attempt = task.attempt + 1
                transitioned = db.update_task_status(
                    task_id, task.status, TaskStatus.queued, conn=conn
                )
                if transitioned:
                    conn.execute(
                        "UPDATE tasks SET attempt = ?, updated_at = ? WHERE task_id = ?",
                        (new_attempt, now, task_id),
                    )
                    result.tasks_requeued += 1

                    event = TaskEvent(
                        task_id=task_id,
                        event_type="recovery_requeued",
                        payload={
                            "reason": "orphaned_task",
                            "old_status": task.status.value,
                            "new_attempt": new_attempt,
                        },
                        idempotency_key=f"recovery-requeue-{task_id}-{new_attempt}",
                        ts=now,
                    )
                    db.insert_event(event, conn=conn)
                    result.events_replayed += 1
                else:
                    result.errors.append(
                        f"CAS failed for orphaned task {task_id}: expected {task.status.value}"
                    )
            else:
                transitioned = db.update_task_status(
                    task_id, task.status, TaskStatus.failed, conn=conn
                )
                if transitioned:
                    event = TaskEvent(
                        task_id=task_id,
                        event_type="recovery_max_attempts_exceeded",
                        payload={
                            "reason": "max_attempts_exceeded_orphaned",
                            "attempt": task.attempt,
                            "max_attempts": max_attempts,
                        },
                        idempotency_key=f"recovery-failed-{task_id}-{task.attempt}",
                        ts=now,
                    )
                    db.insert_event(event, conn=conn)
                    result.events_replayed += 1
                else:
                    result.errors.append(
                        f"CAS failed for orphaned task {task_id}: expected {task.status.value}"
                    )

    return result


def audit_timeline(db: RouterDB, task_id: str) -> list[TaskEvent]:
    """Replay events for a task in chronological order (for debugging).

    Returns all TaskEvent entries for the given task_id, ordered by
    event_id ASC (chronological insertion order).
    """
    return db.get_events(task_id)
