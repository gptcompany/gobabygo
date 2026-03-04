"""E2E tests for thread cross-repo orchestration with on_failure policies.

Tests the complete lifecycle: thread creation, step dispatch, completion,
failure handling (skip/retry/abort), context propagation, and audit trail.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.router.db import RouterDB
from src.router.dependency import on_task_terminal
from src.router.models import (
    CLIType,
    OnFailurePolicy,
    Task,
    TaskStatus,
    ThreadStatus,
    ThreadStepRequest,
    Worker,
)
from src.router.scheduler import Scheduler
from src.router.thread import add_step, compute_thread_status, create_thread, get_thread_context


@pytest.fixture
def db() -> RouterDB:
    rdb = RouterDB(":memory:")
    rdb.init_schema()
    return rdb


@pytest.fixture
def sched(db: RouterDB) -> Scheduler:
    return Scheduler(db=db, lease_duration_s=300)


def _add_worker(db: RouterDB, worker_id: str = "w1") -> Worker:
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


def _step(title: str, step_index: int, **kwargs) -> ThreadStepRequest:
    return ThreadStepRequest(title=title, step_index=step_index, **kwargs)


def _dispatch_and_ack(sched: Scheduler, db: RouterDB, worker_id: str) -> Task:
    """Dispatch next task and acknowledge it. Returns the task."""
    result = sched.dispatch()
    assert result is not None, "Expected a task to dispatch"
    sched.ack_task(result.task.task_id, worker_id)
    # Re-idle worker so it can receive next task
    return db.get_task(result.task.task_id)


# =============================================================================
# E2E: 3-step cross-repo thread, all succeed
# =============================================================================

class TestThread3StepE2ESuccess:
    """Thread with 3 steps across different repos, all completing successfully."""

    def test_full_lifecycle(self, db: RouterDB, sched: Scheduler) -> None:
        w = _add_worker(db, "w-claude")
        thread = create_thread(db, "cross-repo-deploy")

        # Add 3 steps with different repos
        s0 = add_step(db, thread.thread_id, _step("Analyze gobabygo", 0, repo="gobabygo"))
        s1 = add_step(db, thread.thread_id, _step("Merge backstage", 1, repo="backstage"))
        s2 = add_step(db, thread.thread_id, _step("Deploy infra", 2, repo="infra"))

        # Step 0: queued, steps 1-2: blocked
        assert db.get_task(s0.task_id).status == TaskStatus.queued
        assert db.get_task(s1.task_id).status == TaskStatus.blocked
        assert db.get_task(s2.task_id).status == TaskStatus.blocked

        # Dispatch + complete step 0
        task0 = _dispatch_and_ack(sched, db, "w-claude")
        assert task0.status == TaskStatus.running
        sched.complete_task(s0.task_id, "w-claude", result={"analysis": "ok", "files": 42})

        # Step 1 should now be queued (unblocked by on_task_terminal)
        assert db.get_task(s1.task_id).status == TaskStatus.queued
        assert db.get_task(s2.task_id).status == TaskStatus.blocked

        # Dispatch + complete step 1
        db.update_worker("w-claude", {"status": "idle", "idle_since": datetime.now(timezone.utc).isoformat()})
        task1 = _dispatch_and_ack(sched, db, "w-claude")
        assert task1.task_id == s1.task_id
        sched.complete_task(s1.task_id, "w-claude", result={"merged": True})

        # Step 2 should now be queued
        assert db.get_task(s2.task_id).status == TaskStatus.queued

        # Dispatch + complete step 2
        db.update_worker("w-claude", {"status": "idle", "idle_since": datetime.now(timezone.utc).isoformat()})
        task2 = _dispatch_and_ack(sched, db, "w-claude")
        assert task2.task_id == s2.task_id
        sched.complete_task(s2.task_id, "w-claude", result={"deployed": True})

        # Thread should be completed
        assert compute_thread_status(db, thread.thread_id) == ThreadStatus.completed

    def test_context_propagation(self, db: RouterDB, sched: Scheduler) -> None:
        """Verify that step N+1 receives context from completed steps 0..N."""
        w = _add_worker(db, "w-claude")
        thread = create_thread(db, "ctx-test")
        s0 = add_step(db, thread.thread_id, _step("Step 0", 0, repo="repo-a"))
        s1 = add_step(db, thread.thread_id, _step("Step 1", 1, repo="repo-b"))
        s2 = add_step(db, thread.thread_id, _step("Step 2", 2, repo="repo-c"))

        # Complete step 0 with result
        _dispatch_and_ack(sched, db, "w-claude")
        sched.complete_task(s0.task_id, "w-claude", result={"step0_data": "alpha"})

        # Context for step 1 should include step 0's result
        ctx1 = get_thread_context(db, thread.thread_id, 1)
        assert len(ctx1) == 1
        assert ctx1[0]["step_index"] == 0
        assert ctx1[0]["result"]["step0_data"] == "alpha"

        # Complete step 1
        db.update_worker("w-claude", {"status": "idle", "idle_since": datetime.now(timezone.utc).isoformat()})
        _dispatch_and_ack(sched, db, "w-claude")
        sched.complete_task(s1.task_id, "w-claude", result={"step1_data": "beta"})

        # Context for step 2 should include both step 0 and step 1 results
        ctx2 = get_thread_context(db, thread.thread_id, 2)
        assert len(ctx2) == 2
        assert ctx2[0]["step_index"] == 0
        assert ctx2[1]["step_index"] == 1
        assert ctx2[1]["result"]["step1_data"] == "beta"

    def test_audit_trail_per_step(self, db: RouterDB, sched: Scheduler) -> None:
        """Verify every step has input, output, timestamps, worker, repo in DB."""
        w = _add_worker(db, "w-claude")
        thread = create_thread(db, "audit-test")
        s0 = add_step(db, thread.thread_id, _step("Audit step", 0, repo="gobabygo"))

        _dispatch_and_ack(sched, db, "w-claude")
        sched.complete_task(s0.task_id, "w-claude", result={"output": "done"})

        task = db.get_task(s0.task_id)
        assert task.repo == "gobabygo"
        assert task.assigned_worker == "w-claude"
        assert task.created_at is not None
        assert task.updated_at is not None
        assert task.result is not None
        assert task.result["output"] == "done"
        assert task.thread_id == thread.thread_id
        assert task.step_index == 0

        # Check events exist for the task
        events = db.get_events(s0.task_id)
        event_types = [e.event_type for e in events]
        assert "state_transition" in event_types


# =============================================================================
# on_failure=skip: failed step does not block thread
# =============================================================================

class TestOnFailureSkip:
    """Tests for the skip failure policy."""

    def test_skip_does_not_block_thread(self, db: RouterDB, sched: Scheduler) -> None:
        """Step with on_failure=skip that fails does not block subsequent steps."""
        w = _add_worker(db, "w-claude")
        thread = create_thread(db, "skip-test")
        s0 = add_step(db, thread.thread_id, _step("Step 0", 0))
        s1 = add_step(db, thread.thread_id, _step("Skippable", 1, on_failure=OnFailurePolicy.skip))
        s2 = add_step(db, thread.thread_id, _step("Final", 2))

        # Complete step 0
        _dispatch_and_ack(sched, db, "w-claude")
        sched.complete_task(s0.task_id, "w-claude", result={"ok": True})

        # Dispatch step 1, then fail it
        db.update_worker("w-claude", {"status": "idle", "idle_since": datetime.now(timezone.utc).isoformat()})
        _dispatch_and_ack(sched, db, "w-claude")
        sched.report_failure(s1.task_id, "w-claude", "timeout_error")

        # Step 1 should be failed
        assert db.get_task(s1.task_id).status == TaskStatus.failed

        # Step 2 should be unblocked (because failed IS terminal, dependency resolves)
        assert db.get_task(s2.task_id).status == TaskStatus.queued

        # Thread should be active (not failed, because step 1 has on_failure=skip)
        assert compute_thread_status(db, thread.thread_id) == ThreadStatus.active

        # Complete step 2
        db.update_worker("w-claude", {"status": "idle", "idle_since": datetime.now(timezone.utc).isoformat()})
        _dispatch_and_ack(sched, db, "w-claude")
        sched.complete_task(s2.task_id, "w-claude", result={"final": True})

        # Thread should be completed (step 1 failed with skip, step 0+2 completed)
        assert compute_thread_status(db, thread.thread_id) == ThreadStatus.completed

    def test_skip_context_includes_marker(self, db: RouterDB, sched: Scheduler) -> None:
        """Skipped step appears in context as a marker with result=null."""
        w = _add_worker(db, "w-claude")
        thread = create_thread(db, "skip-ctx-test")
        s0 = add_step(db, thread.thread_id, _step("Step 0", 0, on_failure=OnFailurePolicy.skip))
        s1 = add_step(db, thread.thread_id, _step("Step 1", 1))

        # Dispatch step 0, fail it
        _dispatch_and_ack(sched, db, "w-claude")
        sched.report_failure(s0.task_id, "w-claude", "error")

        # Context for step 1 should include skipped marker
        ctx = get_thread_context(db, thread.thread_id, 1)
        assert len(ctx) == 1
        assert ctx[0]["step_index"] == 0
        assert ctx[0]["status"] == "skipped"
        assert ctx[0]["result"] is None

    def test_abort_still_fails_thread(self, db: RouterDB, sched: Scheduler) -> None:
        """Step with on_failure=abort (default) causes thread failure."""
        w = _add_worker(db, "w-claude")
        thread = create_thread(db, "abort-test")
        s0 = add_step(db, thread.thread_id, _step("Will fail", 0))  # default abort

        _dispatch_and_ack(sched, db, "w-claude")
        sched.report_failure(s0.task_id, "w-claude", "crash")

        assert compute_thread_status(db, thread.thread_id) == ThreadStatus.failed


# =============================================================================
# on_failure=retry: step requeued with backoff
# =============================================================================

class TestOnFailureRetry:
    """Tests for the retry failure policy."""

    def test_retry_requeues_on_first_failure(self, db: RouterDB, sched: Scheduler) -> None:
        """Step with on_failure=retry gets requeued on failure (not failed)."""
        w = _add_worker(db, "w-claude")
        thread = create_thread(db, "retry-test")
        s0 = add_step(db, thread.thread_id, _step("Retryable", 0, on_failure=OnFailurePolicy.retry))
        s1 = add_step(db, thread.thread_id, _step("Next", 1))

        # Dispatch + ack + fail step 0
        _dispatch_and_ack(sched, db, "w-claude")
        sched.report_failure(s0.task_id, "w-claude", "transient_error")

        # Step 0 should be requeued, not failed
        task = db.get_task(s0.task_id)
        assert task.status == TaskStatus.queued
        assert task.attempt == 2
        assert task.not_before is not None  # backoff set

        # Step 1 still blocked (step 0 is not terminal)
        assert db.get_task(s1.task_id).status == TaskStatus.blocked

        # Thread stays active
        assert compute_thread_status(db, thread.thread_id) == ThreadStatus.active

    def test_retry_exhaustion_fails_task(self, db: RouterDB, sched: Scheduler) -> None:
        """After max retries, step transitions to failed normally."""
        w = _add_worker(db, "w-claude")
        thread = create_thread(db, "retry-exhaust-test")
        s0 = add_step(db, thread.thread_id, _step("Retryable", 0, on_failure=OnFailurePolicy.retry))
        s1 = add_step(db, thread.thread_id, _step("Next", 1))

        # Attempt 1: dispatch + fail -> requeue
        _dispatch_and_ack(sched, db, "w-claude")
        sched.report_failure(s0.task_id, "w-claude", "fail1")
        assert db.get_task(s0.task_id).status == TaskStatus.queued
        assert db.get_task(s0.task_id).attempt == 2

        # Clear not_before to allow immediate dispatch for testing
        db.update_task_fields(s0.task_id, {"not_before": None})

        # Attempt 2: dispatch + fail -> requeue
        db.update_worker("w-claude", {"status": "idle", "idle_since": datetime.now(timezone.utc).isoformat()})
        _dispatch_and_ack(sched, db, "w-claude")
        sched.report_failure(s0.task_id, "w-claude", "fail2")
        assert db.get_task(s0.task_id).status == TaskStatus.queued
        assert db.get_task(s0.task_id).attempt == 3

        # Clear not_before for test
        db.update_task_fields(s0.task_id, {"not_before": None})

        # Attempt 3: dispatch + fail -> FAILED (exhausted, attempt 3 >= 3)
        db.update_worker("w-claude", {"status": "idle", "idle_since": datetime.now(timezone.utc).isoformat()})
        _dispatch_and_ack(sched, db, "w-claude")
        sched.report_failure(s0.task_id, "w-claude", "fail3")

        # Step 0 should now be failed (retries exhausted)
        assert db.get_task(s0.task_id).status == TaskStatus.failed

        # Step 1 stays blocked (retry exhausted = abort: dependents not unblocked)
        assert db.get_task(s1.task_id).status == TaskStatus.blocked

        # Thread should be failed (on_failure=retry but exhausted = abort behavior)
        assert compute_thread_status(db, thread.thread_id) == ThreadStatus.failed

    def test_retry_event_logged(self, db: RouterDB, sched: Scheduler) -> None:
        """Retry requeue logs both the transition and the retry event."""
        w = _add_worker(db, "w-claude")
        thread = create_thread(db, "retry-event-test")
        s0 = add_step(db, thread.thread_id, _step("Retryable", 0, on_failure=OnFailurePolicy.retry))

        _dispatch_and_ack(sched, db, "w-claude")
        sched.report_failure(s0.task_id, "w-claude", "oops")

        events = db.get_events(s0.task_id)
        event_types = [e.event_type for e in events]
        assert "step_retry_requeued" in event_types

        transition = next(
            e for e in events
            if e.event_type == "state_transition"
            and e.payload["from"] == "running"
            and e.payload["to"] == "queued"
        )
        assert transition.payload["reason"] == "retry_requeue: oops"

        retry_event = next(e for e in events if e.event_type == "step_retry_requeued")
        assert retry_event.payload["attempt"] == 2
        assert "not_before" in retry_event.payload


# =============================================================================
# on_failure field persistence
# =============================================================================

class TestOnFailureField:
    """Tests for on_failure field in model and DB."""

    def test_default_is_abort(self, db: RouterDB) -> None:
        """Default on_failure is abort."""
        thread = create_thread(db, "default-test")
        task = add_step(db, thread.thread_id, _step("Default", 0))
        assert task.on_failure == OnFailurePolicy.abort

    def test_skip_persisted(self, db: RouterDB) -> None:
        """on_failure=skip is persisted in DB."""
        thread = create_thread(db, "skip-persist")
        task = add_step(db, thread.thread_id, _step("Skip", 0, on_failure=OnFailurePolicy.skip))
        assert task.on_failure == OnFailurePolicy.skip

        # Round-trip through DB
        loaded = db.get_task(task.task_id)
        assert loaded.on_failure == OnFailurePolicy.skip

    def test_retry_persisted(self, db: RouterDB) -> None:
        """on_failure=retry is persisted in DB."""
        thread = create_thread(db, "retry-persist")
        task = add_step(db, thread.thread_id, _step("Retry", 0, on_failure=OnFailurePolicy.retry))
        assert task.on_failure == OnFailurePolicy.retry
        loaded = db.get_task(task.task_id)
        assert loaded.on_failure == OnFailurePolicy.retry

    def test_invalid_policy_rejected(self) -> None:
        """Task rejects invalid on_failure values."""
        with pytest.raises(ValidationError):
            Task(title="invalid", on_failure="not-a-policy")


# =============================================================================
# Thread status computation with mixed policies
# =============================================================================

class TestComputeThreadStatusPolicies:
    """Tests for compute_thread_status with different on_failure policies."""

    def test_all_completed(self, db: RouterDB) -> None:
        thread = create_thread(db, "all-done")
        s0 = add_step(db, thread.thread_id, _step("A", 0))
        s1 = add_step(db, thread.thread_id, _step("B", 1))
        db.update_task_status(s0.task_id, TaskStatus.queued, TaskStatus.completed)
        db.update_task_status(s1.task_id, TaskStatus.blocked, TaskStatus.completed)
        assert compute_thread_status(db, thread.thread_id) == ThreadStatus.completed

    def test_failed_with_skip_is_completed(self, db: RouterDB) -> None:
        """A failed step with on_failure=skip counts as ok for thread status."""
        thread = create_thread(db, "skip-complete")
        s0 = add_step(db, thread.thread_id, _step("Skip", 0, on_failure=OnFailurePolicy.skip))
        s1 = add_step(db, thread.thread_id, _step("OK", 1))
        db.update_task_status(s0.task_id, TaskStatus.queued, TaskStatus.failed)
        db.update_task_status(s1.task_id, TaskStatus.blocked, TaskStatus.completed)
        assert compute_thread_status(db, thread.thread_id) == ThreadStatus.completed

    def test_failed_with_abort_is_failed(self, db: RouterDB) -> None:
        """A failed step with on_failure=abort (default) means thread failed."""
        thread = create_thread(db, "abort-fail")
        s0 = add_step(db, thread.thread_id, _step("Abort", 0))
        db.update_task_status(s0.task_id, TaskStatus.queued, TaskStatus.failed)
        assert compute_thread_status(db, thread.thread_id) == ThreadStatus.failed

    def test_mixed_skip_and_running_is_active(self, db: RouterDB) -> None:
        """Thread with skipped step + running step = active."""
        thread = create_thread(db, "mixed-active")
        s0 = add_step(db, thread.thread_id, _step("Skip", 0, on_failure=OnFailurePolicy.skip))
        s1 = add_step(db, thread.thread_id, _step("Running", 1))
        db.update_task_status(s0.task_id, TaskStatus.queued, TaskStatus.failed)
        db.update_task_status(s1.task_id, TaskStatus.blocked, TaskStatus.running)
        assert compute_thread_status(db, thread.thread_id) == ThreadStatus.active
