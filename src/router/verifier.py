"""Mandatory verifier gate for critical tasks.

Critical tasks go through a review step before completion.
The VerifierGate handles approval, rejection (with fix-task creation),
escalation after repeated rejections, and review timeout detection.

Follows the EscalationCallback protocol from retry.py for escalation,
and uses apply_transition() from fsm.py for state changes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from src.router.db import RouterDB
from src.router.fsm import TERMINAL_STATES, TransitionRequest, TransitionResult, apply_transition
from src.router.models import Task, TaskEvent, TaskStatus

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid4() -> str:
    return str(uuid.uuid4())


_MAX_REJECTIONS = 3


class VerifierGate:
    """Gate that enforces review for critical tasks."""

    def should_review(self, task: Task) -> bool:
        """Returns True if the task requires verifier review."""
        return task.critical

    def has_pending_fixes(self, db: RouterDB, task_id: str) -> bool:
        """Check if any non-terminal fix tasks exist for this task.

        Queries tasks where parent_task_id == task_id and status
        is NOT in a terminal state.
        """
        cur = db._conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE parent_task_id = ? AND status NOT IN (?, ?, ?, ?)",
            (
                task_id,
                TaskStatus.completed.value,
                TaskStatus.failed.value,
                TaskStatus.timeout.value,
                TaskStatus.canceled.value,
            ),
        )
        return cur.fetchone()[0] > 0

    def approve_task(
        self, db: RouterDB, task_id: str, verifier_id: str
    ) -> TransitionResult:
        """Approve a task in review: review -> completed.

        Blocks if pending fix tasks exist.
        Logs approval event with verifier_id.
        """
        if self.has_pending_fixes(db, task_id):
            return TransitionResult(
                success=False,
                reason="Cannot approve: pending fix tasks exist",
            )

        request = TransitionRequest(
            task_id=task_id,
            from_status=TaskStatus.review,
            to_status=TaskStatus.completed,
            reason=f"approved_by:{verifier_id}",
        )
        result = apply_transition(db, request)

        if result.success:
            db.insert_event(
                TaskEvent(
                    task_id=task_id,
                    event_type="verifier_approval",
                    payload={
                        "verifier_id": verifier_id,
                        "action": "approve",
                    },
                )
            )

        return result

    def reject_task(
        self,
        db: RouterDB,
        task_id: str,
        verifier_id: str,
        reason: str,
        escalation_callbacks: list | None = None,
    ) -> Task | None:
        """Reject a task in review, creating a fix task.

        Increments rejection_count. If >= MAX_REJECTIONS, triggers
        escalation callbacks instead of creating a fix task.

        Returns the fix Task on success, None on escalation.
        """
        task = db.get_task(task_id)
        if task is None:
            return None

        new_count = task.rejection_count + 1
        db.update_task_fields(task_id, {"rejection_count": new_count})

        # Log rejection event
        db.insert_event(
            TaskEvent(
                task_id=task_id,
                event_type="verifier_rejection",
                payload={
                    "verifier_id": verifier_id,
                    "reason": reason,
                    "rejection_count": new_count,
                },
            )
        )

        if new_count >= _MAX_REJECTIONS:
            # Escalate instead of creating fix task
            db.insert_event(
                TaskEvent(
                    task_id=task_id,
                    event_type="escalation_to_boss",
                    payload={
                        "rejection_count": new_count,
                        "reason": reason,
                        "verifier_id": verifier_id,
                    },
                    idempotency_key=f"escalate-reject-{task_id}-{new_count}",
                )
            )
            callbacks = escalation_callbacks or []
            for cb in callbacks:
                try:
                    cb.on_escalation(
                        task,
                        task.assigned_worker,
                        task.attempt,
                        f"rejected {new_count} times: {reason}",
                    )
                except Exception as e:
                    logger.error("Escalation callback error: %s", e)
            return None

        # Create fix task
        fix_task = Task(
            parent_task_id=task_id,
            title=f"Fix: {task.title} (rejection #{new_count})",
            target_cli=task.target_cli,
            target_account=task.target_account,
            critical=False,
            created_by=verifier_id,
            payload={"fix_reason": reason, "original_task_id": task_id},
            phase=task.phase,
            priority=task.priority,
        )
        db.insert_task(fix_task)
        return fix_task

    def check_review_timeout(self, db: RouterDB) -> list[str]:
        """Find tasks in review past their timeout and transition them.

        Returns list of timed-out task_ids.
        """
        now = _utc_now()
        cur = db._conn.execute(
            "SELECT * FROM tasks WHERE status = ? AND review_timeout_at IS NOT NULL AND review_timeout_at < ?",
            (TaskStatus.review.value, now),
        )
        rows = cur.fetchall()

        timed_out: list[str] = []
        for row in rows:
            task_id = row["task_id"]
            # review -> failed (timeout is not a valid target from review in FSM)
            # FSM allows review -> completed, failed, canceled
            request = TransitionRequest(
                task_id=task_id,
                from_status=TaskStatus.review,
                to_status=TaskStatus.failed,
                reason="review_timeout",
            )
            result = apply_transition(db, request)
            if result.success:
                timed_out.append(task_id)

        return timed_out
