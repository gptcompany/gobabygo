"""Thread model: grouping of ordered steps (tasks) with shared context.

A thread is a logical unit of cross-repo work. Each step maps to a Task
with thread_id and step_index set. Steps can auto-depend on previous steps.
"""

from __future__ import annotations

import json
from typing import Any

from src.router.db import RouterDB
from src.router.models import (
    CROSS_REPO_HANDOFF_ROLE,
    HandoffRepoError,
    HandoffRoleError,
    Task,
    TaskStatus,
    Thread,
    ThreadStatus,
    _utc_now,
    _uuid4,
    validate_handoff,
)
from src.router.topology import Topology


def create_thread(db: RouterDB, name: str) -> Thread:
    """Create a new thread and persist it."""
    if db.get_thread_by_name(name) is not None:
        raise ValueError(f"Thread name already exists: {name}")
    thread = Thread(name=name)
    return db.insert_thread(thread)


def add_step(
    db: RouterDB,
    thread_id: str,
    step_request,
    topology: Topology | None = None,
) -> Task:
    """Add a step to a thread. Returns the created Task.

    - Validates thread exists and is not in a terminal state.
    - If payload contains 'handoff', validates structure and enforces role/topology.
    - If step_index > 0 and no explicit depends_on, auto-depends on previous step.
    - Sets initial status to blocked if depends_on is non-empty, queued otherwise.

    Raises:
        ValueError: thread not found or terminal, invalid step order.
        pydantic.ValidationError: malformed handoff payload.
        HandoffRoleError: cross-repo handoff without PRESIDENT_GLOBAL role.
        HandoffRepoError: handoff references unknown repo in topology.
    """
    thread = db.get_thread(thread_id)
    if thread is None:
        raise ValueError(f"Thread {thread_id} not found")
    if thread.status in (ThreadStatus.completed, ThreadStatus.failed):
        raise ValueError(f"Thread {thread_id} is in terminal state: {thread.status.value}")

    step_repo = step_request.repo or ""

    # Validate handoff payload if present
    handoff = validate_handoff(step_request.payload)
    if handoff is not None:
        # Cross-check: handoff.target_repo must match step.repo (if step.repo is set)
        if step_repo and handoff.target_repo != step_repo:
            raise HandoffRepoError(
                f"handoff.target_repo '{handoff.target_repo}' does not match step repo '{step_repo}'"
            )
        # If repo is omitted on the step, inherit target_repo from the handoff packet
        # so scheduler topology constraints are enforced on dispatch.
        if not step_repo and handoff.target_repo:
            step_repo = handoff.target_repo

        # Enforce PRESIDENT_GLOBAL role for cross-repo handoffs
        is_cross_repo = handoff.source_repo != handoff.target_repo
        if is_cross_repo:
            step_role = getattr(step_request, "role", None) or ""
            if step_role != CROSS_REPO_HANDOFF_ROLE:
                raise HandoffRoleError(
                    f"cross-repo handoff requires {CROSS_REPO_HANDOFF_ROLE} role, "
                    f"got '{step_role}'"
                )

        # Validate repos against topology if loaded
        if topology is not None:
            known_repos = set(topology._repos.keys())
            if known_repos:
                if handoff.target_repo and handoff.target_repo not in known_repos:
                    raise HandoffRepoError(
                        f"unknown target_repo '{handoff.target_repo}' in topology"
                    )
                if handoff.source_repo and handoff.source_repo not in known_repos:
                    raise HandoffRepoError(
                        f"unknown source_repo '{handoff.source_repo}' in topology"
                    )

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
        else:
            raise ValueError(
                f"Cannot add step {step_request.step_index} before step {step_request.step_index - 1}"
            )

    initial_status = TaskStatus.blocked if depends_on else TaskStatus.queued

    on_failure_val = step_request.on_failure.value if hasattr(step_request.on_failure, "value") else str(step_request.on_failure)

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
        repo=step_repo or None,
        role=getattr(step_request, "role", None) or None,
        on_failure=on_failure_val,
    )

    return db.insert_task(task)


def get_thread_context(
    db: RouterDB,
    thread_id: str,
    up_to_step_index: int,
) -> list[dict[str, Any]]:
    """Get aggregated context from completed and skipped steps up to (exclusive) a step index.

    Returns list of dicts:
    - Completed: {"step_index": N, "repo": "...", "result": {...}}
    - Skipped:   {"step_index": N, "repo": "...", "status": "skipped", "result": null}
    Applies 32KB cap: if aggregate JSON > 32KB, drops oldest results first.
    """
    # Completed steps with results
    cur = db._conn.execute(
        """SELECT step_index, repo, result_json, status, on_failure FROM tasks
           WHERE thread_id = ? AND step_index < ?
           AND (status = 'completed'
                OR (status IN ('failed', 'timeout', 'canceled') AND on_failure = 'skip'))
           ORDER BY step_index ASC""",
        (thread_id, up_to_step_index),
    )
    rows = cur.fetchall()

    context: list[dict[str, Any]] = []
    for row in rows:
        if row["status"] == "completed":
            result = json.loads(row["result_json"]) if row["result_json"] else None
            context.append({
                "step_index": row["step_index"],
                "repo": row["repo"],
                "result": result,
            })
        else:
            # Skipped step — failed but on_failure=skip
            context.append({
                "step_index": row["step_index"],
                "repo": row["repo"],
                "status": "skipped",
                "result": None,
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
    - Any step failed/timeout/canceled WITH on_failure != skip: failed
    - All steps in terminal state (completed or failed-with-skip): completed
    - Otherwise (any step running/assigned/queued/blocked): active
    """
    steps = db.list_thread_steps(thread_id)

    if not steps:
        return ThreadStatus.pending

    terminal_fail = {TaskStatus.failed, TaskStatus.timeout, TaskStatus.canceled}

    # Check for hard failures (on_failure != "skip")
    for s in steps:
        if s.status in terminal_fail and s.on_failure != "skip":
            return ThreadStatus.failed

    # Check if all steps are in a terminal-ok state
    # (completed, or failed/timeout/canceled with on_failure=skip)
    terminal_ok = {TaskStatus.completed}
    all_done = all(
        s.status in terminal_ok or (s.status in terminal_fail and s.on_failure == "skip")
        for s in steps
    )
    if all_done:
        return ThreadStatus.completed

    return ThreadStatus.active
