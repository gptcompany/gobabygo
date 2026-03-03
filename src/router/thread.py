"""Thread model: grouping of ordered steps (tasks) with shared context.

A thread is a logical unit of cross-repo work. Each step maps to a Task
with thread_id and step_index set. Steps can auto-depend on previous steps.
"""

from __future__ import annotations

import json
from typing import Any

from src.router.db import RouterDB
from src.router.models import Task, TaskStatus, Thread, ThreadStatus, _utc_now, _uuid4


def create_thread(db: RouterDB, name: str) -> Thread:
    """Create a new thread and persist it."""
    thread = Thread(name=name)
    return db.insert_thread(thread)


def add_step(
    db: RouterDB,
    thread_id: str,
    step_request,
) -> Task:
    """Add a step to a thread. Returns the created Task.

    - Validates thread exists and is not in a terminal state.
    - If step_index > 0 and no explicit depends_on, auto-depends on previous step.
    - Sets initial status to blocked if depends_on is non-empty, queued otherwise.
    """
    thread = db.get_thread(thread_id)
    if thread is None:
        raise ValueError(f"Thread {thread_id} not found")
    if thread.status in (ThreadStatus.completed, ThreadStatus.failed):
        raise ValueError(f"Thread {thread_id} is in terminal state: {thread.status.value}")

    depends_on = list(step_request.depends_on)

    # Auto-depend on previous step if step_index > 0 and no explicit depends_on
    if step_request.step_index > 0 and not depends_on:
        cur = db._conn.execute(
            "SELECT task_id FROM tasks WHERE thread_id = ? AND step_index = ?",
            (thread_id, step_request.step_index - 1),
        )
        row = cur.fetchone()
        if row:
            depends_on = [row["task_id"]]

    initial_status = TaskStatus.blocked if depends_on else TaskStatus.queued

    task = Task(
        title=step_request.title,
        target_cli=step_request.target_cli,
        target_account=step_request.target_account,
        execution_mode=step_request.execution_mode,
        payload=step_request.payload,
        depends_on=depends_on,
        priority=step_request.priority,
        critical=step_request.critical,
        status=initial_status,
        thread_id=thread_id,
        step_index=step_request.step_index,
        repo=step_request.repo or None,
        role=None,
    )

    return db.insert_task(task)


def get_thread_context(
    db: RouterDB,
    thread_id: str,
    up_to_step_index: int,
) -> list[dict[str, Any]]:
    """Get aggregated context from completed steps up to (exclusive) a step index.

    Returns list of dicts: [{"step_index": N, "repo": "...", "result": {...}}, ...]
    Applies 32KB cap: if aggregate JSON > 32KB, drops oldest results first.
    """
    cur = db._conn.execute(
        """SELECT step_index, repo, result_json FROM tasks
           WHERE thread_id = ? AND step_index < ? AND status = 'completed'
           ORDER BY step_index ASC""",
        (thread_id, up_to_step_index),
    )
    rows = cur.fetchall()

    context: list[dict[str, Any]] = []
    for row in rows:
        result = json.loads(row["result_json"]) if row["result_json"] else None
        context.append({
            "step_index": row["step_index"],
            "repo": row["repo"],
            "result": result,
        })

    # Apply 32KB cap, dropping oldest results first
    max_bytes = 32768
    while context and len(json.dumps(context).encode("utf-8")) > max_bytes:
        # Drop the oldest (first) entry's result, keeping metadata
        context[0]["result"] = None
        context[0]["_truncated"] = True
        # If still too large after nulling result, remove the entry entirely
        if len(json.dumps(context).encode("utf-8")) > max_bytes:
            context.pop(0)

    return context


def compute_thread_status(db: RouterDB, thread_id: str) -> ThreadStatus:
    """Compute the current status of a thread based on its steps.

    - No steps: pending
    - Any step failed/timeout/canceled: failed
    - All steps completed: completed
    - Otherwise (any step running/assigned/queued/blocked): active
    """
    steps = db.list_thread_steps(thread_id)

    if not steps:
        return ThreadStatus.pending

    terminal_fail = {TaskStatus.failed, TaskStatus.timeout, TaskStatus.canceled}
    statuses = [s.status for s in steps]

    if any(s in terminal_fail for s in statuses):
        return ThreadStatus.failed

    if all(s == TaskStatus.completed for s in statuses):
        return ThreadStatus.completed

    return ThreadStatus.active
