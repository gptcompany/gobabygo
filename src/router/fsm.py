"""FSM transition guard for the AI Mesh Router.

All task state transitions go through apply_transition(), which:
1. Validates the transition against ALLOWED_TRANSITIONS
2. Atomically updates status + inserts event via db.transaction()
3. Writes dead-letter entries for rejected transitions

Terminal states (completed, failed, timeout, canceled) have NO outgoing transitions.
canceled is reachable from ALL non-terminal states.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.router.models import TaskEvent, TaskStatus

# Lazy import to avoid circular dependency (dead_letter imports nothing from fsm)
# The actual import happens inside apply_transition.

ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.queued: {TaskStatus.assigned, TaskStatus.canceled},
    TaskStatus.assigned: {TaskStatus.blocked, TaskStatus.running, TaskStatus.canceled},
    TaskStatus.blocked: {TaskStatus.queued, TaskStatus.canceled},
    TaskStatus.running: {
        TaskStatus.review,
        TaskStatus.failed,
        TaskStatus.timeout,
        TaskStatus.canceled,
    },
    TaskStatus.review: {TaskStatus.completed, TaskStatus.failed, TaskStatus.canceled},
    # Terminal states: no outgoing transitions
    TaskStatus.completed: set(),
    TaskStatus.failed: set(),
    TaskStatus.timeout: set(),
    TaskStatus.canceled: set(),
}

TERMINAL_STATES: set[TaskStatus] = {
    TaskStatus.completed,
    TaskStatus.failed,
    TaskStatus.timeout,
    TaskStatus.canceled,
}


def validate_transition(from_status: TaskStatus, to_status: TaskStatus) -> bool:
    """Check if a transition is allowed by the FSM.

    Returns True if from_status -> to_status is a valid transition.
    """
    allowed = ALLOWED_TRANSITIONS.get(from_status, set())
    return to_status in allowed


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TransitionRequest:
    """Request to transition a task from one status to another."""

    task_id: str
    from_status: TaskStatus
    to_status: TaskStatus
    reason: str = ""
    timestamp: str = field(default_factory=_utc_now)


@dataclass(frozen=True)
class TransitionResult:
    """Result of a transition attempt."""

    success: bool
    reason: str | None = None
    event_id: str | None = None


def apply_transition(db, request: TransitionRequest) -> TransitionResult:
    """Apply a state transition atomically.

    1. Validates the transition against ALLOWED_TRANSITIONS.
    2. If invalid: writes dead-letter, returns failure.
    3. If valid: uses db.transaction() for atomic CAS + event insert.
    4. If CAS fails (concurrent modification): writes dead-letter, returns failure.
    5. If CAS succeeds: returns success with event_id.

    Args:
        db: RouterDB instance.
        request: TransitionRequest with task_id, from_status, to_status, reason.

    Returns:
        TransitionResult with success status, reason, and event_id (on success).
    """
    from src.router.dead_letter import write_dead_letter

    # Step 1: Validate FSM transition
    if not validate_transition(request.from_status, request.to_status):
        reason = (
            f"Invalid transition: {request.from_status.value} -> {request.to_status.value}"
        )
        write_dead_letter(
            db=db,
            task_id=request.task_id,
            from_status=request.from_status.value,
            to_status=request.to_status.value,
            reason=reason,
            payload={"request_reason": request.reason, "ts": request.timestamp},
        )
        return TransitionResult(success=False, reason=reason)

    # Step 2: Atomic CAS + event insert
    event_id = str(uuid.uuid4())
    event = TaskEvent(
        event_id=event_id,
        task_id=request.task_id,
        event_type="state_transition",
        payload={
            "from": request.from_status.value,
            "to": request.to_status.value,
            "reason": request.reason,
        },
    )

    try:
        with db.transaction() as conn:
            # CAS update
            cas_ok = db.update_task_status(
                task_id=request.task_id,
                old_status=request.from_status,
                new_status=request.to_status,
                conn=conn,
            )
            if not cas_ok:
                # CAS failed — concurrent modification or wrong current state.
                # We must raise to trigger rollback, then write dead-letter outside txn.
                raise _CASFailure()

            # Insert transition event
            db.insert_event(event, conn=conn)

    except _CASFailure:
        reason = (
            f"CAS failure: task {request.task_id} not in expected state "
            f"{request.from_status.value}"
        )
        write_dead_letter(
            db=db,
            task_id=request.task_id,
            from_status=request.from_status.value,
            to_status=request.to_status.value,
            reason=reason,
            payload={"request_reason": request.reason, "ts": request.timestamp},
        )
        return TransitionResult(success=False, reason=reason)

    return TransitionResult(success=True, reason=None, event_id=event_id)


class _CASFailure(Exception):
    """Internal sentinel for CAS failure inside a transaction."""
