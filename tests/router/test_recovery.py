"""Tests for crash recovery and event-driven dependency resolution.

Covers:
  Recovery:
  - Expired lease requeues task with attempt+1
  - Max attempts reached -> terminal failed state + escalation event
  - Orphaned assigned task (no lease) gets requeued
  - Recovery is idempotent (running twice = same result)
  - Recovery actions logged as TaskEvent entries
  - audit_timeline returns events in chronological order

  Dependencies:
  - check_dependencies returns (True, []) when all deps terminal
  - check_dependencies returns (False, [dep_id]) when dep pending
  - on_task_terminal unblocks waiting task
  - on_task_terminal with partial deps doesn't unblock
  - resolve_blocked_tasks batch unblocks all eligible
  - Dependency resolution uses FSM (blocked->queued via apply_transition)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


import pytest

from src.router.db import RouterDB
from src.router.dependency import (
    check_dependencies,
    on_task_terminal,
    resolve_blocked_tasks,
)
from src.router.models import (
    CLIType,
    Lease,
    Task,
    TaskEvent,
    TaskPhase,
    TaskStatus,
    Worker,
)
from src.router.recovery import audit_timeline, recover_on_startup


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _past_ts(minutes: int = 10) -> str:
    """Return a UTC timestamp `minutes` in the past."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _future_ts(minutes: int = 60) -> str:
    """Return a UTC timestamp `minutes` in the future."""
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


@pytest.fixture
def db() -> RouterDB:
    """Create an in-memory RouterDB with schema initialized."""
    rdb = RouterDB(":memory:")
    rdb.init_schema()
    return rdb


@pytest.fixture
def worker(db: RouterDB) -> Worker:
    """Insert and return a sample worker."""
    w = Worker(
        machine="vps-01",
        cli_type=CLIType.claude,
        account_profile="work",
        status="busy",
        concurrency=1,
    )
    db.insert_worker(w)
    return w


def _make_task(
    status: TaskStatus = TaskStatus.queued,
    attempt: int = 1,
    depends_on: list[str] | None = None,
    **kwargs,
) -> Task:
    """Helper to create a Task with explicit fields."""
    return Task(
        title="Test task",
        phase=TaskPhase.implement,
        target_cli=CLIType.claude,
        status=status,
        attempt=attempt,
        depends_on=depends_on or [],
        **kwargs,
    )


# =============================================================================
# Recovery tests
# =============================================================================


class TestRecoverExpiredLease:
    """Task with expired lease gets requeued, attempt incremented."""

    def test_requeues_and_increments_attempt(self, db: RouterDB, worker: Worker) -> None:
        task = _make_task(status=TaskStatus.running, attempt=1)
        db.insert_task(task)

        # Create expired lease
        lease = Lease(
            task_id=task.task_id,
            worker_id=worker.worker_id,
            expires_at=_past_ts(10),
        )
        db.create_lease(lease)

        # Set denormalized fields on task
        db._conn.execute(
            "UPDATE tasks SET assigned_worker = ?, lease_expires_at = ? WHERE task_id = ?",
            (worker.worker_id, lease.expires_at, task.task_id),
        )
        db._conn.commit()

        result = recover_on_startup(db, max_attempts=3)

        assert result.leases_expired == 1
        assert result.tasks_requeued == 1
        assert result.errors == []

        # Verify task state
        recovered = db.get_task(task.task_id)
        assert recovered is not None
        assert recovered.status == TaskStatus.queued
        assert recovered.attempt == 2
        assert recovered.assigned_worker is None


class TestRecoverMaxAttempts:
    """Task at max_attempts transitions to failed (not requeued)."""

    def test_transitions_to_failed(self, db: RouterDB, worker: Worker) -> None:
        task = _make_task(status=TaskStatus.running, attempt=3)
        db.insert_task(task)

        lease = Lease(
            task_id=task.task_id,
            worker_id=worker.worker_id,
            expires_at=_past_ts(5),
        )
        db.create_lease(lease)

        result = recover_on_startup(db, max_attempts=3)

        assert result.leases_expired == 1
        assert result.tasks_requeued == 0  # NOT requeued
        assert result.errors == []

        recovered = db.get_task(task.task_id)
        assert recovered is not None
        assert recovered.status == TaskStatus.failed

        # Escalation event should exist
        events = db.get_events(task.task_id)
        escalation_events = [e for e in events if e.event_type == "recovery_max_attempts_exceeded"]
        assert len(escalation_events) == 1
        assert escalation_events[0].payload["reason"] == "max_attempts_exceeded"
        assert escalation_events[0].payload["attempt"] == 3


class TestRecoverOrphanedAssigned:
    """Assigned task with no lease gets requeued."""

    def test_requeues_orphaned(self, db: RouterDB) -> None:
        task = _make_task(status=TaskStatus.assigned, attempt=1)
        db.insert_task(task)

        # No lease created — task is orphaned

        result = recover_on_startup(db, max_attempts=3)

        assert result.tasks_requeued == 1
        assert result.leases_expired == 0  # No lease to expire

        recovered = db.get_task(task.task_id)
        assert recovered is not None
        assert recovered.status == TaskStatus.queued
        assert recovered.attempt == 2


class TestRecoverIdempotent:
    """Running recovery twice produces same result."""

    def test_second_run_is_noop(self, db: RouterDB, worker: Worker) -> None:
        task = _make_task(status=TaskStatus.running, attempt=1)
        db.insert_task(task)

        lease = Lease(
            task_id=task.task_id,
            worker_id=worker.worker_id,
            expires_at=_past_ts(5),
        )
        db.create_lease(lease)

        # First recovery
        result1 = recover_on_startup(db, max_attempts=3)
        assert result1.tasks_requeued == 1
        assert result1.leases_expired == 1

        # Second recovery — task is now queued, no expired leases
        result2 = recover_on_startup(db, max_attempts=3)
        assert result2.tasks_requeued == 0
        assert result2.leases_expired == 0
        assert result2.errors == []

        # Task still queued with attempt=2 (not incremented again)
        recovered = db.get_task(task.task_id)
        assert recovered is not None
        assert recovered.status == TaskStatus.queued
        assert recovered.attempt == 2


class TestRecoverCreatesEvents:
    """Recovery actions logged as TaskEvents."""

    def test_events_logged(self, db: RouterDB, worker: Worker) -> None:
        task = _make_task(status=TaskStatus.running, attempt=1)
        db.insert_task(task)

        lease = Lease(
            task_id=task.task_id,
            worker_id=worker.worker_id,
            expires_at=_past_ts(5),
        )
        db.create_lease(lease)

        result = recover_on_startup(db, max_attempts=3)

        events = db.get_events(task.task_id)
        assert len(events) >= 1

        requeue_events = [e for e in events if e.event_type == "recovery_requeued"]
        assert len(requeue_events) == 1
        assert requeue_events[0].payload["reason"] == "expired_lease"
        assert requeue_events[0].payload["new_attempt"] == 2
        assert result.events_replayed == 1


class TestAuditTimeline:
    """Events returned in chronological order for a task."""

    def test_chronological_order(self, db: RouterDB) -> None:
        task = _make_task(status=TaskStatus.queued)
        db.insert_task(task)

        # Insert events in order
        for i in range(5):
            event = TaskEvent(
                task_id=task.task_id,
                event_type=f"event_{i}",
            )
            db.insert_event(event)

        timeline = audit_timeline(db, task.task_id)
        assert len(timeline) == 5
        for i, ev in enumerate(timeline):
            assert ev.event_type == f"event_{i}"


# =============================================================================
# Dependency tests
# =============================================================================


class TestCheckDependenciesAllResolved:
    """Task with completed deps returns (True, [])."""

    def test_all_resolved(self, db: RouterDB) -> None:
        dep1 = _make_task(status=TaskStatus.completed)
        dep2 = _make_task(status=TaskStatus.failed)
        db.insert_task(dep1)
        db.insert_task(dep2)

        task = _make_task(
            status=TaskStatus.blocked,
            depends_on=[dep1.task_id, dep2.task_id],
        )
        db.insert_task(task)

        resolved, unresolved = check_dependencies(db, task.task_id)
        assert resolved is True
        assert unresolved == []


class TestCheckDependenciesPending:
    """Task with non-terminal dep returns (False, [dep_id])."""

    def test_pending_dep(self, db: RouterDB) -> None:
        dep_done = _make_task(status=TaskStatus.completed)
        dep_pending = _make_task(status=TaskStatus.running)
        db.insert_task(dep_done)
        db.insert_task(dep_pending)

        task = _make_task(
            status=TaskStatus.blocked,
            depends_on=[dep_done.task_id, dep_pending.task_id],
        )
        db.insert_task(task)

        resolved, unresolved = check_dependencies(db, task.task_id)
        assert resolved is False
        assert unresolved == [dep_pending.task_id]


class TestOnTaskTerminalUnblocks:
    """Completing a dependency unblocks waiting task."""

    def test_unblocks(self, db: RouterDB) -> None:
        dep = _make_task(status=TaskStatus.running)
        db.insert_task(dep)

        task = _make_task(
            status=TaskStatus.blocked,
            depends_on=[dep.task_id],
        )
        db.insert_task(task)

        # Dep transitions to completed
        db.update_task_status(dep.task_id, TaskStatus.running, TaskStatus.completed)

        # on_task_terminal triggers
        count = on_task_terminal(db, dep.task_id)
        assert count == 1

        unblocked = db.get_task(task.task_id)
        assert unblocked is not None
        assert unblocked.status == TaskStatus.queued


class TestOnTaskTerminalPartial:
    """Completing one of two deps doesn't unblock (still one pending)."""

    def test_partial_not_unblocked(self, db: RouterDB) -> None:
        dep1 = _make_task(status=TaskStatus.running)
        dep2 = _make_task(status=TaskStatus.running)
        db.insert_task(dep1)
        db.insert_task(dep2)

        task = _make_task(
            status=TaskStatus.blocked,
            depends_on=[dep1.task_id, dep2.task_id],
        )
        db.insert_task(task)

        # Only dep1 completes
        db.update_task_status(dep1.task_id, TaskStatus.running, TaskStatus.completed)

        count = on_task_terminal(db, dep1.task_id)
        assert count == 0  # Still blocked — dep2 not done

        still_blocked = db.get_task(task.task_id)
        assert still_blocked is not None
        assert still_blocked.status == TaskStatus.blocked


class TestResolveBlockedBatch:
    """Batch resolver unblocks all eligible tasks."""

    def test_batch_unblocks(self, db: RouterDB) -> None:
        dep = _make_task(status=TaskStatus.completed)
        db.insert_task(dep)

        # Two blocked tasks depending on same dep
        task1 = _make_task(status=TaskStatus.blocked, depends_on=[dep.task_id])
        task2 = _make_task(status=TaskStatus.blocked, depends_on=[dep.task_id])
        db.insert_task(task1)
        db.insert_task(task2)

        # One task with unresolved dep — should NOT be unblocked
        dep_pending = _make_task(status=TaskStatus.running)
        db.insert_task(dep_pending)
        task3 = _make_task(status=TaskStatus.blocked, depends_on=[dep_pending.task_id])
        db.insert_task(task3)

        count = resolve_blocked_tasks(db)
        assert count == 2

        assert db.get_task(task1.task_id).status == TaskStatus.queued
        assert db.get_task(task2.task_id).status == TaskStatus.queued
        assert db.get_task(task3.task_id).status == TaskStatus.blocked  # Still blocked


class TestDependencyResolutionUsesFSM:
    """blocked->queued goes through FSM apply_transition."""

    def test_uses_fsm_transition(self, db: RouterDB) -> None:
        dep = _make_task(status=TaskStatus.completed)
        db.insert_task(dep)

        task = _make_task(status=TaskStatus.blocked, depends_on=[dep.task_id])
        db.insert_task(task)

        # _apply_blocked_to_queued does a dynamic import of apply_transition.
        # The simplest way to verify FSM usage: call on_task_terminal and check
        # the FSM-generated event (state_transition) exists.
        count = on_task_terminal(db, dep.task_id)
        assert count == 1

        # Verify task transitioned
        assert db.get_task(task.task_id).status == TaskStatus.queued

        # The FSM's apply_transition creates a "state_transition" event
        events = db.get_events(task.task_id)
        fsm_events = [e for e in events if e.event_type == "state_transition"]
        assert len(fsm_events) == 1
        assert fsm_events[0].payload["from"] == "blocked"
        assert fsm_events[0].payload["to"] == "queued"
