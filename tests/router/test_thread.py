"""Tests for thread model, thread module, and scheduler thread integration."""

from __future__ import annotations

import json

import pytest

from pydantic import ValidationError

from src.router.db import RouterDB
from src.router.models import (
    CROSS_REPO_HANDOFF_ROLE,
    CLIType,
    ExecutionMode,
    HandoffRepoError,
    HandoffRoleError,
    Task,
    TaskStatus,
    Thread,
    ThreadStatus,
    ThreadStepRequest,
    Worker,
)
from src.router.scheduler import Scheduler
from src.router.thread import add_step, compute_thread_status, create_thread, get_thread_context
from src.router.topology import Topology


@pytest.fixture
def db() -> RouterDB:
    rdb = RouterDB(":memory:")
    rdb.init_schema()
    return rdb


@pytest.fixture
def sched(db: RouterDB) -> Scheduler:
    return Scheduler(db=db, lease_duration_s=300)


def _step_request(title: str, step_index: int, **kwargs) -> ThreadStepRequest:
    return ThreadStepRequest(title=title, step_index=step_index, **kwargs)


def _add_worker(db: RouterDB, worker_id: str = "w1") -> Worker:
    from datetime import datetime, timezone
    w = Worker(
        worker_id=worker_id,
        machine="ws1",
        cli_type=CLIType.claude,
        account_profile="work",
        status="idle",
        last_heartbeat=datetime.now(timezone.utc).isoformat(),
        idle_since=datetime.now(timezone.utc).isoformat(),
    )
    db.insert_worker(w)
    return w


# -- Thread creation --


def test_create_thread(db: RouterDB) -> None:
    thread = create_thread(db, "cross-repo-refactor")
    assert thread.name == "cross-repo-refactor"
    assert thread.status == ThreadStatus.pending
    assert thread.thread_id
    assert thread.created_at
    # Verify persisted
    retrieved = db.get_thread(thread.thread_id)
    assert retrieved is not None
    assert retrieved.name == "cross-repo-refactor"


def test_create_thread_duplicate_name_rejected(db: RouterDB) -> None:
    create_thread(db, "same-name")
    with pytest.raises(ValueError, match="Thread name already exists"):
        create_thread(db, "same-name")


def test_get_thread_by_name(db: RouterDB) -> None:
    create_thread(db, "my-thread")
    found = db.get_thread_by_name("my-thread")
    assert found is not None
    assert found.name == "my-thread"
    assert db.get_thread_by_name("nonexistent") is None


# -- Add step --


def test_add_step_to_thread(db: RouterDB) -> None:
    thread = create_thread(db, "t1")
    req = _step_request("step zero", 0, repo="frontend")
    task = add_step(db, thread.thread_id, req)
    assert task.thread_id == thread.thread_id
    assert task.step_index == 0
    assert task.repo == "frontend"
    assert task.title == "step zero"
    assert task.status == TaskStatus.queued


def test_add_step_auto_depends_on(db: RouterDB) -> None:
    thread = create_thread(db, "t1")
    step0 = add_step(db, thread.thread_id, _step_request("s0", 0))
    step1 = add_step(db, thread.thread_id, _step_request("s1", 1))
    assert step1.depends_on == [step0.task_id]


def test_add_step_blocked_status(db: RouterDB) -> None:
    thread = create_thread(db, "t1")
    add_step(db, thread.thread_id, _step_request("s0", 0))
    step1 = add_step(db, thread.thread_id, _step_request("s1", 1))
    assert step1.status == TaskStatus.blocked


def test_add_step_missing_previous_step_rejected(db: RouterDB) -> None:
    thread = create_thread(db, "missing-prev")
    with pytest.raises(ValueError, match="Cannot add step 1 before step 0"):
        add_step(db, thread.thread_id, _step_request("s1", 1))


def test_add_step_to_missing_thread_raises(db: RouterDB) -> None:
    req = _step_request("step", 0)
    with pytest.raises(ValueError, match="not found"):
        add_step(db, "no-such-id", req)


def test_add_step_to_completed_thread_raises(db: RouterDB) -> None:
    thread = create_thread(db, "done-thread")
    db.update_thread(thread.thread_id, {"status": ThreadStatus.completed.value})
    req = _step_request("step", 0)
    with pytest.raises(ValueError, match="terminal state"):
        add_step(db, thread.thread_id, req)


def test_add_step_explicit_depends_on(db: RouterDB) -> None:
    thread = create_thread(db, "t1")
    step0 = add_step(db, thread.thread_id, _step_request("s0", 0))
    # Explicit depends_on overrides auto
    other_task = Task(title="external", target_cli=CLIType.claude, target_account="work")
    db.insert_task(other_task)
    step1 = add_step(
        db,
        thread.thread_id,
        _step_request("s1", 1, depends_on=[other_task.task_id]),
    )
    assert step1.depends_on == [other_task.task_id]
    assert step0.task_id not in step1.depends_on


# -- Thread context --


def test_get_thread_context(db: RouterDB) -> None:
    thread = create_thread(db, "ctx")
    step0 = add_step(db, thread.thread_id, _step_request("s0", 0, repo="backend"))
    step1 = add_step(db, thread.thread_id, _step_request("s1", 1, repo="frontend"))

    # Mark step0 as completed with result
    db.update_task_status(step0.task_id, TaskStatus.queued, TaskStatus.assigned)
    db.update_task_status(step0.task_id, TaskStatus.assigned, TaskStatus.running)
    db.update_task_status(step0.task_id, TaskStatus.running, TaskStatus.completed)
    db.update_task_fields(step0.task_id, {"result_json": json.dumps({"output": "done"})})

    ctx = get_thread_context(db, thread.thread_id, up_to_step_index=1)
    assert len(ctx) == 1
    assert ctx[0]["step_index"] == 0
    assert ctx[0]["repo"] == "backend"
    assert ctx[0]["result"] == {"output": "done"}


def test_get_thread_context_with_skipped_step(db: RouterDB) -> None:
    from src.router.models import OnFailurePolicy
    thread = create_thread(db, "ctx-skip")
    step0 = add_step(db, thread.thread_id, _step_request("s0", 0, repo="backend", on_failure=OnFailurePolicy.skip))
    
    # Step0 fails but on_failure=skip
    db.update_task_status(step0.task_id, TaskStatus.queued, TaskStatus.failed)
    
    ctx = get_thread_context(db, thread.thread_id, up_to_step_index=1)
    assert len(ctx) == 1
    assert ctx[0]["status"] == "skipped"
    assert ctx[0]["result"] is None


def test_get_thread_context_cap_32kb(db: RouterDB) -> None:
    thread = create_thread(db, "big")
    # Create a step with a large result
    step0 = add_step(db, thread.thread_id, _step_request("s0", 0))
    db.update_task_status(step0.task_id, TaskStatus.queued, TaskStatus.assigned)
    db.update_task_status(step0.task_id, TaskStatus.assigned, TaskStatus.running)
    db.update_task_status(step0.task_id, TaskStatus.running, TaskStatus.completed)
    big_result = {"data": "x" * 40000}
    db.update_task_fields(step0.task_id, {"result_json": json.dumps(big_result)})

    step1 = add_step(db, thread.thread_id, _step_request("s1", 1))

    ctx = get_thread_context(db, thread.thread_id, up_to_step_index=2)
    serialized = json.dumps(ctx).encode("utf-8")
    assert len(serialized) <= 32768


# -- Thread status computation --


def test_compute_thread_status_pending(db: RouterDB) -> None:
    thread = create_thread(db, "empty")
    assert compute_thread_status(db, thread.thread_id) == ThreadStatus.pending


def test_compute_thread_status_active(db: RouterDB) -> None:
    thread = create_thread(db, "active")
    step0 = add_step(db, thread.thread_id, _step_request("s0", 0))
    db.update_task_status(step0.task_id, TaskStatus.queued, TaskStatus.assigned)
    db.update_task_status(step0.task_id, TaskStatus.assigned, TaskStatus.running)
    assert compute_thread_status(db, thread.thread_id) == ThreadStatus.active


def test_compute_thread_status_completed(db: RouterDB) -> None:
    thread = create_thread(db, "done")
    step0 = add_step(db, thread.thread_id, _step_request("s0", 0))
    db.update_task_status(step0.task_id, TaskStatus.queued, TaskStatus.assigned)
    db.update_task_status(step0.task_id, TaskStatus.assigned, TaskStatus.running)
    db.update_task_status(step0.task_id, TaskStatus.running, TaskStatus.completed)
    assert compute_thread_status(db, thread.thread_id) == ThreadStatus.completed


def test_compute_thread_status_failed(db: RouterDB) -> None:
    thread = create_thread(db, "fail")
    step0 = add_step(db, thread.thread_id, _step_request("s0", 0))
    db.update_task_status(step0.task_id, TaskStatus.queued, TaskStatus.assigned)
    db.update_task_status(step0.task_id, TaskStatus.assigned, TaskStatus.running)
    db.update_task_status(step0.task_id, TaskStatus.running, TaskStatus.failed)
    assert compute_thread_status(db, thread.thread_id) == ThreadStatus.failed


def test_compute_thread_status_failed_soft(db: RouterDB) -> None:
    from src.router.models import OnFailurePolicy
    thread = create_thread(db, "soft-fail")
    # Step fails but with on_failure=skip -> thread stays completed
    step0 = add_step(db, thread.thread_id, _step_request("s0", 0, on_failure=OnFailurePolicy.skip))
    db.update_task_status(step0.task_id, TaskStatus.queued, TaskStatus.failed)
    assert compute_thread_status(db, thread.thread_id) == ThreadStatus.completed


# -- Scheduler integration --


def test_thread_status_update_on_complete(db: RouterDB, sched: Scheduler) -> None:
    thread = create_thread(db, "sched-complete")
    step0 = add_step(db, thread.thread_id, _step_request("s0", 0))
    _add_worker(db, "w1")

    result = sched.dispatch()
    assert result is not None
    sched.ack_task(step0.task_id, "w1")
    sched.complete_task(step0.task_id, "w1")

    updated = db.get_thread(thread.thread_id)
    assert updated is not None
    assert updated.status == ThreadStatus.completed


def test_thread_status_pending_to_active(db: RouterDB, sched: Scheduler) -> None:
    thread = create_thread(db, "dispatch-test")
    add_step(db, thread.thread_id, _step_request("s0", 0))
    _add_worker(db, "w1")

    # Before dispatch, thread is pending
    assert db.get_thread(thread.thread_id).status == ThreadStatus.pending

    sched.dispatch()

    # After dispatch, thread should be active
    updated = db.get_thread(thread.thread_id)
    assert updated.status == ThreadStatus.active


def test_add_step_duplicate_step_index_rejected(db: RouterDB) -> None:
    thread = create_thread(db, "dup")
    add_step(db, thread.thread_id, _step_request("s0", 0))
    with pytest.raises(Exception):
        add_step(db, thread.thread_id, _step_request("s0-dup", 0))


def test_compute_thread_status_blocked_is_active(db: RouterDB) -> None:
    thread = create_thread(db, "blocked-active")
    step0 = add_step(db, thread.thread_id, _step_request("s0", 0))
    # step1 auto-depends on step0, so it starts blocked
    step1 = add_step(db, thread.thread_id, _step_request("s1", 1))
    assert step1.status == TaskStatus.blocked
    # Thread has steps (one queued, one blocked) -> active
    assert compute_thread_status(db, thread.thread_id) == ThreadStatus.active


# -- Handoff tests (Phase 20) --

def _handoff_payload(
    source_repo: str = "backend",
    target_repo: str = "platform",
    summary: str = "Migrate auth module to platform",
    **kwargs,
) -> dict:
    handoff = {"source_repo": source_repo, "target_repo": target_repo, "summary": summary}
    handoff.update(kwargs)
    return {"handoff": handoff}


def _make_topology(repos: dict | None = None) -> Topology:
    """Create a Topology with given repos (or sensible defaults)."""
    if repos is None:
        repos = {
            "backend": {"worker_pool": ["w1"]},
            "platform": {"worker_pool": ["w2"]},
        }
    return Topology({
        "version": "1",
        "global": {"cross_repo_policy": {"require_president_handoff": True}},
        "hosts": {},
        "workers": {},
        "repos": repos,
    })


def test_add_step_with_valid_handoff(db: RouterDB) -> None:
    """Step creation succeeds with valid handoff payload (same-repo, no role needed)."""
    thread = create_thread(db, "handoff-valid")
    payload = _handoff_payload(source_repo="backend", target_repo="backend")
    task = add_step(db, thread.thread_id, _step_request("h0", 0, repo="backend", payload=payload))
    assert task.payload["handoff"]["source_repo"] == "backend"
    assert task.payload["handoff"]["target_repo"] == "backend"


def test_add_step_with_invalid_handoff_missing_fields(db: RouterDB) -> None:
    """400 on missing required handoff fields."""
    thread = create_thread(db, "handoff-missing")
    payload = {"handoff": {"source_repo": "backend"}}  # missing target_repo, summary
    with pytest.raises(ValidationError):
        add_step(db, thread.thread_id, _step_request("h0", 0, payload=payload))


def test_add_step_with_handoff_summary_too_long(db: RouterDB) -> None:
    """400 on oversized summary."""
    thread = create_thread(db, "handoff-long")
    payload = _handoff_payload(summary="x" * 5000)
    with pytest.raises(ValidationError):
        add_step(db, thread.thread_id, _step_request("h0", 0, payload=payload))


def test_add_step_with_handoff_list_too_many_items(db: RouterDB) -> None:
    """400 on list exceeding max items."""
    thread = create_thread(db, "handoff-many")
    payload = _handoff_payload(decisions=["d"] * 25)
    with pytest.raises(ValidationError):
        add_step(db, thread.thread_id, _step_request("h0", 0, payload=payload))


def test_add_step_cross_repo_handoff_requires_president(db: RouterDB) -> None:
    """403 when cross-repo handoff without PRESIDENT_GLOBAL role."""
    thread = create_thread(db, "handoff-norole")
    payload = _handoff_payload(source_repo="backend", target_repo="platform")
    with pytest.raises(HandoffRoleError, match="PRESIDENT_GLOBAL"):
        add_step(db, thread.thread_id, _step_request("h0", 0, repo="platform", payload=payload))


def test_add_step_cross_repo_handoff_with_president_role(db: RouterDB) -> None:
    """Cross-repo handoff succeeds with PRESIDENT_GLOBAL role."""
    thread = create_thread(db, "handoff-president")
    payload = _handoff_payload(source_repo="backend", target_repo="platform")
    task = add_step(
        db, thread.thread_id,
        _step_request("h0", 0, repo="platform", role=CROSS_REPO_HANDOFF_ROLE, payload=payload),
    )
    assert task.role == CROSS_REPO_HANDOFF_ROLE
    assert task.payload["handoff"]["target_repo"] == "platform"


def test_add_step_cross_repo_handoff_infers_repo_for_scheduler(db: RouterDB) -> None:
    """When step.repo is omitted, task.repo is inferred from handoff.target_repo."""
    _add_worker(db, "w-backend")
    _add_worker(db, "w-platform")
    topo = _make_topology({
        "backend": {"worker_pool": ["w-backend"]},
        "platform": {"worker_pool": ["w-platform"]},
    })
    sched = Scheduler(db=db, topology=topo)

    thread = create_thread(db, "handoff-infer-repo")
    payload = _handoff_payload(source_repo="backend", target_repo="platform")
    task = add_step(
        db,
        thread.thread_id,
        _step_request(
            "h0",
            0,
            repo="",  # intentionally omitted
            role=CROSS_REPO_HANDOFF_ROLE,
            payload=payload,
        ),
        topology=topo,
    )
    assert task.repo == "platform"

    dispatch = sched.dispatch()
    assert dispatch is not None
    assert dispatch.worker.worker_id == "w-platform"


def test_add_step_same_repo_handoff_no_role_check(db: RouterDB) -> None:
    """Same-repo handoff succeeds without role restriction."""
    thread = create_thread(db, "handoff-samerc")
    payload = _handoff_payload(source_repo="backend", target_repo="backend")
    task = add_step(db, thread.thread_id, _step_request("h0", 0, repo="backend", payload=payload))
    assert task.payload["handoff"]["source_repo"] == "backend"


def test_add_step_handoff_unknown_target_repo(db: RouterDB) -> None:
    """400 when topology loaded and repo unknown."""
    thread = create_thread(db, "handoff-unknownrepo")
    topo = _make_topology()
    payload = _handoff_payload(source_repo="backend", target_repo="nonexistent")
    with pytest.raises(HandoffRepoError, match="unknown target_repo"):
        add_step(
            db, thread.thread_id,
            _step_request("h0", 0, repo="nonexistent", role=CROSS_REPO_HANDOFF_ROLE, payload=payload),
            topology=topo,
        )


def test_add_step_handoff_no_topology_skips_repo_check(db: RouterDB) -> None:
    """Step creation succeeds when no topology file — backward compat."""
    thread = create_thread(db, "handoff-notopo")
    payload = _handoff_payload(source_repo="backend", target_repo="anything")
    # Cross-repo requires president role, but no topology check
    task = add_step(
        db, thread.thread_id,
        _step_request("h0", 0, repo="anything", role=CROSS_REPO_HANDOFF_ROLE, payload=payload),
        topology=None,
    )
    assert task.payload["handoff"]["target_repo"] == "anything"


def test_thread_context_includes_handoff_step_result(db: RouterDB) -> None:
    """Completed handoff step result appears in thread_context."""
    thread = create_thread(db, "handoff-ctx")
    payload = _handoff_payload(source_repo="backend", target_repo="backend")
    task = add_step(db, thread.thread_id, _step_request("h0", 0, repo="backend", payload=payload))

    # Complete the step with a result
    result = {"output": "auth module migrated", "files_changed": 3}
    db.update_task_status(task.task_id, TaskStatus.queued, TaskStatus.completed)
    db.update_task_fields(task.task_id, {"result_json": json.dumps(result)})

    # Add step 1 and check context
    add_step(db, thread.thread_id, _step_request("h1", 1, repo="backend"))
    context = get_thread_context(db, thread.thread_id, up_to_step_index=1)
    assert len(context) == 1
    assert context[0]["step_index"] == 0
    assert context[0]["result"]["output"] == "auth module migrated"


def test_add_step_handoff_target_repo_mismatch(db: RouterDB) -> None:
    """400 when handoff.target_repo differs from step.repo."""
    thread = create_thread(db, "handoff-mismatch")
    payload = _handoff_payload(source_repo="backend", target_repo="platform")
    with pytest.raises(HandoffRepoError, match="does not match step repo"):
        add_step(
            db, thread.thread_id,
            _step_request("h0", 0, repo="frontend", role=CROSS_REPO_HANDOFF_ROLE, payload=payload),
        )
