"""Tests for RetryPolicy: bounded retry, backoff, escalation, unschedulable detection."""

from datetime import datetime, timedelta, timezone

import pytest

from src.router.db import RouterDB
from src.router.models import Task, TaskStatus
from src.router.retry import LogEscalation, RetryPolicy, RetryResult


@pytest.fixture
def db():
    d = RouterDB(":memory:")
    d.init_schema()
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _past(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _add_task(db, task_id="t1", status=TaskStatus.running, attempt=1, **kwargs):
    t = Task(task_id=task_id, title="test", status=status, attempt=attempt, **kwargs)
    db.insert_task(t)
    return t


class MockEscalationCallback:
    """Records escalation invocations for testing."""

    def __init__(self):
        self.calls = []

    def on_escalation(self, task, last_worker_id, attempt, reason):
        self.calls.append({
            "task_id": task.task_id,
            "last_worker_id": last_worker_id,
            "attempt": attempt,
            "reason": reason,
        })


class TestShouldRetry:
    def test_within_limit(self, db):
        rp = RetryPolicy(db=db)
        task = Task(task_id="t1", attempt=1)
        assert rp.should_retry(task) is True

    def test_at_limit(self, db):
        rp = RetryPolicy(db=db, max_attempts=3)
        task = Task(task_id="t1", attempt=3)
        assert rp.should_retry(task) is False

    def test_above_limit(self, db):
        rp = RetryPolicy(db=db, max_attempts=3)
        task = Task(task_id="t1", attempt=5)
        assert rp.should_retry(task) is False


class TestCalculateNotBefore:
    def test_backoff_schedule(self, db):
        rp = RetryPolicy(db=db, backoff_schedule=[15, 60, 180])
        now = datetime.now(timezone.utc)
        t1 = Task(task_id="t1", attempt=1)
        nb1 = rp.calculate_not_before(t1)
        # Should be approximately now + 15s
        nb1_dt = datetime.fromisoformat(nb1)
        assert (nb1_dt - now).total_seconds() == pytest.approx(15, abs=2)

        t2 = Task(task_id="t2", attempt=2)
        nb2 = rp.calculate_not_before(t2)
        nb2_dt = datetime.fromisoformat(nb2)
        assert (nb2_dt - now).total_seconds() == pytest.approx(60, abs=2)

        t3 = Task(task_id="t3", attempt=3)
        nb3 = rp.calculate_not_before(t3)
        nb3_dt = datetime.fromisoformat(nb3)
        assert (nb3_dt - now).total_seconds() == pytest.approx(180, abs=2)

    def test_exceeds_schedule_uses_last(self, db):
        rp = RetryPolicy(db=db, backoff_schedule=[10, 20])
        now = datetime.now(timezone.utc)
        t = Task(task_id="t1", attempt=5)
        nb = rp.calculate_not_before(t)
        nb_dt = datetime.fromisoformat(nb)
        assert (nb_dt - now).total_seconds() == pytest.approx(20, abs=2)


class TestRequeueWithBackoff:
    def test_success(self, db):
        _add_task(db, "t1", attempt=1)
        rp = RetryPolicy(db=db)
        result = rp.requeue_with_backoff("t1", "test_reason")
        assert result.retried is True
        assert result.new_attempt == 2
        assert result.not_before is not None
        t = db.get_task("t1")
        assert t.status == TaskStatus.queued
        assert t.not_before is not None

    def test_max_attempts_escalates(self, db):
        cb = MockEscalationCallback()
        _add_task(db, "t1", attempt=3)
        rp = RetryPolicy(db=db, max_attempts=3, escalation_callbacks=[cb])
        result = rp.requeue_with_backoff("t1", "test_reason")
        assert result.retried is False
        assert result.escalated is True
        t = db.get_task("t1")
        assert t.status == TaskStatus.failed
        # Escalation callback was invoked
        assert len(cb.calls) == 1
        assert cb.calls[0]["task_id"] == "t1"
        assert cb.calls[0]["reason"] == "test_reason"

    def test_escalation_event_emitted(self, db):
        _add_task(db, "t1", attempt=3)
        rp = RetryPolicy(db=db, max_attempts=3)
        rp.requeue_with_backoff("t1", "test_reason")
        events = db.get_events("t1")
        escalation_events = [e for e in events if e.event_type == "escalation_to_boss"]
        assert len(escalation_events) == 1

    def test_task_not_found(self, db):
        rp = RetryPolicy(db=db)
        result = rp.requeue_with_backoff("nonexistent", "test")
        assert result.retried is False
        assert result.error == "task_not_found"


class TestMultipleCallbacks:
    def test_all_invoked(self, db):
        cb1 = MockEscalationCallback()
        cb2 = MockEscalationCallback()
        _add_task(db, "t1", attempt=3)
        rp = RetryPolicy(db=db, max_attempts=3, escalation_callbacks=[cb1, cb2])
        rp.requeue_with_backoff("t1", "test")
        assert len(cb1.calls) == 1
        assert len(cb2.calls) == 1


class TestLogEscalation:
    def test_does_not_raise(self):
        cb = LogEscalation()
        task = Task(task_id="t1")
        # Should just log, not raise
        cb.on_escalation(task, "w1", 3, "test reason")


class TestUnschedulableTasks:
    def test_find_old_queued_tasks(self, db):
        # Task queued 2 hours ago (> 30 min default)
        _add_task(db, "t1", status=TaskStatus.queued, created_at=_past(7200))
        # Task queued 5 minutes ago (< 30 min)
        _add_task(db, "t2", status=TaskStatus.queued, created_at=_now())
        rp = RetryPolicy(db=db)
        tasks = rp.find_unschedulable_tasks()
        assert len(tasks) == 1
        assert tasks[0].task_id == "t1"

    def test_emit_events_idempotent(self, db):
        _add_task(db, "t1", status=TaskStatus.queued, created_at=_past(7200))
        rp = RetryPolicy(db=db)
        count1 = rp.emit_unschedulable_events()
        assert count1 == 1
        # Second call same day — idempotent
        count2 = rp.emit_unschedulable_events()
        assert count2 == 0

    def test_no_unschedulable(self, db):
        _add_task(db, "t1", status=TaskStatus.queued, created_at=_now())
        rp = RetryPolicy(db=db)
        assert rp.find_unschedulable_tasks() == []
