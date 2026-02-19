"""Tests for VerifierGate: critical task review, approval, rejection, escalation, timeout."""

from datetime import datetime, timedelta, timezone

import pytest

from src.router.db import RouterDB
from src.router.fsm import TransitionRequest, apply_transition
from src.router.models import CLIType, Task, TaskEvent, TaskStatus, Worker
from src.router.scheduler import Scheduler
from src.router.verifier import VerifierGate, _MAX_REJECTIONS


@pytest.fixture
def db():
    d = RouterDB(":memory:")
    d.init_schema()
    return d


@pytest.fixture
def gate():
    return VerifierGate()


@pytest.fixture
def sched(db):
    return Scheduler(db=db, lease_duration_s=300, review_timeout_s=3600)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _past(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _add_worker(db, worker_id="w1", account="work", cli=CLIType.claude, status="idle"):
    w = Worker(
        worker_id=worker_id, machine="ws1", cli_type=cli,
        account_profile=account, status=status,
        last_heartbeat=_now(), idle_since=_now(),
    )
    db.insert_worker(w)
    return w


def _add_task(db, task_id="t1", critical=False, target_cli=CLIType.claude, target_account="work", **kwargs):
    t = Task(
        task_id=task_id, title="test task", target_cli=target_cli,
        target_account=target_account, critical=critical, **kwargs,
    )
    db.insert_task(t)
    return t


def _make_task_review(db, task_id):
    """Helper: move a task from queued -> assigned -> running -> review."""
    db.update_task_status(task_id, TaskStatus.queued, TaskStatus.assigned)
    db.update_task_status(task_id, TaskStatus.assigned, TaskStatus.running)
    db.update_task_status(task_id, TaskStatus.running, TaskStatus.review)


class TestShouldReview:
    def test_critical_true(self, gate):
        task = Task(critical=True)
        assert gate.should_review(task) is True

    def test_critical_false(self, gate):
        task = Task(critical=False)
        assert gate.should_review(task) is False


class TestApproveTask:
    def test_approve_success(self, gate, db):
        _add_task(db, "t1", critical=True)
        _make_task_review(db, "t1")
        result = gate.approve_task(db, "t1", verifier_id="v1")
        assert result.success is True
        task = db.get_task("t1")
        assert task.status == TaskStatus.completed

    def test_approve_logs_event(self, gate, db):
        _add_task(db, "t1", critical=True)
        _make_task_review(db, "t1")
        gate.approve_task(db, "t1", verifier_id="v1")
        events = db.get_events("t1")
        approval_events = [e for e in events if e.event_type == "verifier_approval"]
        assert len(approval_events) == 1
        assert approval_events[0].payload["verifier_id"] == "v1"
        assert approval_events[0].payload["action"] == "approve"

    def test_approve_blocked_by_pending_fixes(self, gate, db):
        _add_task(db, "t1", critical=True)
        _make_task_review(db, "t1")
        # Create a pending fix task (queued = non-terminal)
        _add_task(db, "fix1", parent_task_id="t1")
        result = gate.approve_task(db, "t1", verifier_id="v1")
        assert result.success is False
        assert "pending fix tasks" in result.reason
        # Task should still be in review
        task = db.get_task("t1")
        assert task.status == TaskStatus.review

    def test_approve_after_fix_completed(self, gate, db):
        _add_task(db, "t1", critical=True)
        _make_task_review(db, "t1")
        # Create a fix task and complete it
        fix = _add_task(db, "fix1", parent_task_id="t1")
        db.update_task_status("fix1", TaskStatus.queued, TaskStatus.assigned)
        db.update_task_status("fix1", TaskStatus.assigned, TaskStatus.running)
        db.update_task_status("fix1", TaskStatus.running, TaskStatus.completed)
        # Now approve should succeed
        result = gate.approve_task(db, "t1", verifier_id="v1")
        assert result.success is True


class TestRejectTask:
    def test_reject_creates_fix_task(self, gate, db):
        _add_task(db, "t1", critical=True)
        _make_task_review(db, "t1")
        fix = gate.reject_task(db, "t1", verifier_id="v1", reason="wrong output")
        assert fix is not None
        assert fix.parent_task_id == "t1"
        assert "Fix:" in fix.title
        assert "rejection #1" in fix.title
        assert fix.critical is False
        assert fix.created_by == "v1"
        assert fix.payload["fix_reason"] == "wrong output"

    def test_reject_increments_count(self, gate, db):
        _add_task(db, "t1", critical=True)
        _make_task_review(db, "t1")
        gate.reject_task(db, "t1", verifier_id="v1", reason="bad")
        task = db.get_task("t1")
        assert task.rejection_count == 1

    def test_reject_fix_inherits_target(self, gate, db):
        _add_task(db, "t1", critical=True, target_cli=CLIType.codex, target_account="special")
        _make_task_review(db, "t1")
        fix = gate.reject_task(db, "t1", verifier_id="v1", reason="fix needed")
        assert fix.target_cli == CLIType.codex
        assert fix.target_account == "special"

    def test_reject_fix_not_critical(self, gate, db):
        _add_task(db, "t1", critical=True)
        _make_task_review(db, "t1")
        fix = gate.reject_task(db, "t1", verifier_id="v1", reason="issue")
        assert fix.critical is False

    def test_reject_logs_event(self, gate, db):
        _add_task(db, "t1", critical=True)
        _make_task_review(db, "t1")
        gate.reject_task(db, "t1", verifier_id="v1", reason="wrong")
        events = db.get_events("t1")
        rejection_events = [e for e in events if e.event_type == "verifier_rejection"]
        assert len(rejection_events) == 1
        assert rejection_events[0].payload["verifier_id"] == "v1"
        assert rejection_events[0].payload["reason"] == "wrong"
        assert rejection_events[0].payload["rejection_count"] == 1

    def test_escalation_after_max_rejections(self, gate, db):
        _add_task(db, "t1", critical=True)
        _make_task_review(db, "t1")
        # Reject 3 times (MAX_REJECTIONS)
        for i in range(1, _MAX_REJECTIONS):
            result = gate.reject_task(db, "t1", verifier_id="v1", reason=f"issue #{i}")
            assert result is not None  # Should create fix tasks for first N-1

        # The Nth rejection should escalate (return None)
        result = gate.reject_task(db, "t1", verifier_id="v1", reason="final rejection")
        assert result is None
        # Verify escalation event was emitted
        events = db.get_events("t1")
        escalation_events = [e for e in events if e.event_type == "escalation_to_boss"]
        assert len(escalation_events) == 1
        assert escalation_events[0].payload["rejection_count"] == _MAX_REJECTIONS

    def test_escalation_invokes_callbacks(self, gate, db):
        _add_task(db, "t1", critical=True)
        _make_task_review(db, "t1")
        # Set rejection_count to MAX-1 so next rejection triggers escalation
        db.update_task_fields("t1", {"rejection_count": _MAX_REJECTIONS - 1})

        callback_calls = []

        class MockCallback:
            def on_escalation(self, task, last_worker_id, attempt, reason):
                callback_calls.append({
                    "task_id": task.task_id,
                    "reason": reason,
                })

        result = gate.reject_task(
            db, "t1", verifier_id="v1", reason="escalate this",
            escalation_callbacks=[MockCallback()],
        )
        assert result is None
        assert len(callback_calls) == 1
        assert callback_calls[0]["task_id"] == "t1"

    def test_reject_task_not_found(self, gate, db):
        result = gate.reject_task(db, "nonexistent", verifier_id="v1", reason="x")
        assert result is None


class TestHasPendingFixes:
    def test_no_fixes(self, gate, db):
        _add_task(db, "t1", critical=True)
        assert gate.has_pending_fixes(db, "t1") is False

    def test_fix_in_progress(self, gate, db):
        _add_task(db, "t1", critical=True)
        _add_task(db, "fix1", parent_task_id="t1", status=TaskStatus.running)
        assert gate.has_pending_fixes(db, "t1") is True

    def test_fix_completed(self, gate, db):
        _add_task(db, "t1", critical=True)
        _add_task(db, "fix1", parent_task_id="t1", status=TaskStatus.completed)
        assert gate.has_pending_fixes(db, "t1") is False

    def test_mixed_fix_states(self, gate, db):
        _add_task(db, "t1", critical=True)
        _add_task(db, "fix1", parent_task_id="t1", status=TaskStatus.completed)
        _add_task(db, "fix2", parent_task_id="t1", status=TaskStatus.queued)
        # One completed, one queued (non-terminal) -> has pending
        assert gate.has_pending_fixes(db, "t1") is True


class TestReviewTimeout:
    def test_expired_task_transitions(self, gate, db):
        _add_task(db, "t1", critical=True)
        _make_task_review(db, "t1")
        # Set review_timeout_at to the past
        db.update_task_fields("t1", {"review_timeout_at": _past(60)})
        timed_out = gate.check_review_timeout(db)
        assert "t1" in timed_out
        task = db.get_task("t1")
        assert task.status == TaskStatus.failed

    def test_not_expired_no_change(self, gate, db):
        _add_task(db, "t1", critical=True)
        _make_task_review(db, "t1")
        # Set review_timeout_at to the future
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        db.update_task_fields("t1", {"review_timeout_at": future})
        timed_out = gate.check_review_timeout(db)
        assert timed_out == []
        task = db.get_task("t1")
        assert task.status == TaskStatus.review

    def test_no_timeout_set(self, gate, db):
        _add_task(db, "t1", critical=True)
        _make_task_review(db, "t1")
        # No review_timeout_at set (None)
        timed_out = gate.check_review_timeout(db)
        assert timed_out == []


class TestSchedulerVerifierIntegration:
    def test_critical_task_routes_to_review(self, sched, db):
        _add_worker(db, "w1", "work")
        _add_task(db, "t1", critical=True)
        sched.dispatch()
        sched.ack_task("t1", "w1")
        result = sched.complete_task("t1", "w1")
        assert result is True
        task = db.get_task("t1")
        assert task.status == TaskStatus.review
        assert task.review_timeout_at is not None
        # Worker should be back to idle
        w = db.get_worker("w1")
        assert w.status == "idle"
        # Lease should be cleaned up
        assert db.get_active_lease("t1") is None

    def test_non_critical_task_routes_to_completed(self, sched, db):
        _add_worker(db, "w1", "work")
        _add_task(db, "t1", critical=False)
        sched.dispatch()
        sched.ack_task("t1", "w1")
        result = sched.complete_task("t1", "w1")
        assert result is True
        task = db.get_task("t1")
        assert task.status == TaskStatus.completed

    def test_full_rejection_cycle(self, sched, db):
        """Critical task -> review -> reject -> fix task created -> approve."""
        gate = VerifierGate()
        _add_worker(db, "w1", "work")
        _add_task(db, "t1", critical=True)
        # Dispatch and complete -> goes to review
        sched.dispatch()
        sched.ack_task("t1", "w1")
        sched.complete_task("t1", "w1")
        task = db.get_task("t1")
        assert task.status == TaskStatus.review

        # Reject -> creates fix task
        fix = gate.reject_task(db, "t1", verifier_id="v1", reason="needs fixing")
        assert fix is not None
        assert fix.parent_task_id == "t1"

        # Complete the fix task (simulate)
        db.update_task_status(fix.task_id, TaskStatus.queued, TaskStatus.assigned)
        db.update_task_status(fix.task_id, TaskStatus.assigned, TaskStatus.running)
        db.update_task_status(fix.task_id, TaskStatus.running, TaskStatus.completed)

        # Approve the original task
        result = gate.approve_task(db, "t1", verifier_id="v1")
        assert result.success is True
        task = db.get_task("t1")
        assert task.status == TaskStatus.completed
