"""Event-driven dependency resolution for the AI Mesh Router.

Dependencies are resolved event-driven via on_task_terminal (no polling):
- When any task reaches terminal state (completed/failed/canceled),
  on_task_terminal checks if blocked tasks can now proceed.
- resolve_blocked_tasks is a batch fallback for recovery scenarios only.

State transitions (blocked -> queued) go through FSM apply_transition when
available, falling back to direct CAS update_task_status otherwise.
The FSM supports blocked -> queued as a valid transition.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from src.router.db import RouterDB
from src.router.models import TaskStatus

# Terminal states — a dependency is "resolved" when its task is in one of these
TERMINAL_STATES = frozenset({
    TaskStatus.completed.value,
    TaskStatus.failed.value,
    TaskStatus.timeout.value,
    TaskStatus.canceled.value,
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _apply_blocked_to_queued(db: RouterDB, task_id: str) -> bool:
    """Transition a task from blocked to queued via FSM or direct CAS.

    Uses FSM apply_transition (which validates the transition and manages
    its own transaction) when available. Falls back to direct CAS otherwise.
    """
    try:
        from src.router.fsm import TransitionRequest, apply_transition
        request = TransitionRequest(
            task_id=task_id,
            from_status=TaskStatus.blocked,
            to_status=TaskStatus.queued,
            reason="dependencies_resolved",
        )
        result = apply_transition(db, request)
        return result.success
    except ImportError:
        return db.update_task_status(
            task_id, TaskStatus.blocked, TaskStatus.queued
        )


def check_dependencies(db: RouterDB, task_id: str) -> tuple[bool, list[str]]:
    """Check whether all dependencies for a task are resolved.

    Returns:
        (all_resolved, unresolved_task_ids)
        - all_resolved: True if every task in depends_on is in a terminal state
        - unresolved_task_ids: list of dependency task_ids not yet terminal

    A dependency is resolved when its task is completed, failed, or canceled.
    """
    task = db.get_task(task_id)
    if task is None:
        return (True, [])

    if not task.depends_on:
        return (True, [])

    unresolved: list[str] = []
    for dep_id in task.depends_on:
        dep_task = db.get_task(dep_id)
        if dep_task is None:
            # Missing dependency task — treat as unresolved
            unresolved.append(dep_id)
            continue
        if dep_task.status.value not in TERMINAL_STATES:
            unresolved.append(dep_id)

    return (len(unresolved) == 0, unresolved)


def resolve_blocked_tasks(db: RouterDB) -> int:
    """Batch fallback: find all blocked tasks and unblock those with resolved deps.

    This is NOT the primary mechanism — use on_task_terminal for event-driven
    resolution. This function exists for recovery scenarios where events may
    have been missed.

    Respects on_failure policies: a failed dep with on_failure=abort blocks dependents.

    Returns count of tasks unblocked.
    """
    blocked_rows = db._conn.execute(
        "SELECT task_id, depends_on FROM tasks WHERE status = ?",
        (TaskStatus.blocked.value,),
    ).fetchall()

    unblocked = 0

    for row in blocked_rows:
        task_id = row["task_id"]
        depends_on = json.loads(row["depends_on"]) if row["depends_on"] else []
        if not depends_on:
            continue
        all_allow = all(_dep_allows_unblock(db, dep_id) for dep_id in depends_on)
        if all_allow:
            transitioned = _apply_blocked_to_queued(db, task_id)
            if transitioned:
                unblocked += 1

    return unblocked


def _dep_allows_unblock(db: RouterDB, dep_task_id: str) -> bool:
    """Check if a dependency task allows its dependents to proceed.

    For thread steps (thread_id set):
    - Completed: always allows
    - Failed/timeout/canceled with on_failure=skip: allows
    - Failed/timeout/canceled with on_failure=abort/retry: blocks

    For non-thread tasks (legacy behavior):
    - Any terminal state allows unblocking (completed, failed, timeout, canceled)
    """
    dep = db.get_task(dep_task_id)
    if dep is None:
        return False
    status = dep.status.value if hasattr(dep.status, "value") else str(dep.status)
    if status not in TERMINAL_STATES:
        return False
    if status == TaskStatus.completed.value:
        return True
    # Non-thread tasks: legacy behavior — failed is terminal and allows unblock
    if not dep.thread_id:
        return True
    # Thread steps: failed only allows unblock if on_failure=skip
    return dep.on_failure == "skip"


def on_task_terminal(db: RouterDB, completed_task_id: str) -> int:
    """Event-driven dependency resolution — called when a task reaches terminal state.

    Finds all tasks that have completed_task_id in their depends_on list
    AND are in blocked status. For each, checks if ALL dependencies allow
    unblocking (completed, or failed with on_failure=skip). If yes,
    transitions blocked -> queued.

    This is the primary mechanism — no polling needed.

    Returns count of newly unblocked tasks.
    """
    # Find all blocked tasks that depend on the completed task.
    # depends_on is stored as JSON array, so we search for the task_id string.
    # We use LIKE with the task_id embedded — safe because task_ids are UUIDs.
    blocked_rows = db._conn.execute(
        """SELECT task_id, depends_on FROM tasks
           WHERE status = ?
           AND depends_on LIKE ?""",
        (TaskStatus.blocked.value, f"%{completed_task_id}%"),
    ).fetchall()

    unblocked = 0

    for row in blocked_rows:
        task_id = row["task_id"]
        depends_on = json.loads(row["depends_on"])

        # Verify the completed_task_id is actually in depends_on (not a substring match)
        if completed_task_id not in depends_on:
            continue

        # Check if ALL dependencies allow unblocking
        all_allow = all(_dep_allows_unblock(db, dep_id) for dep_id in depends_on)
        if all_allow:
            transitioned = _apply_blocked_to_queued(db, task_id)
            if transitioned:
                unblocked += 1

    return unblocked
