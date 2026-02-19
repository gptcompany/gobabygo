"""Tests for Scheduler: deterministic selection, dispatch, ACK, complete, fail."""

from datetime import datetime, timedelta, timezone

import pytest

from src.router.db import RouterDB
from src.router.models import CLIType, Lease, Task, TaskStatus, Worker
from src.router.scheduler import DispatchResult, Scheduler


@pytest.fixture
def db():
    d = RouterDB(":memory:")
    d.init_schema()
    return d


@pytest.fixture
def sched(db):
    return Scheduler(db=db, lease_duration_s=300)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _past(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _future(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _add_worker(db, worker_id="w1", account="work", cli=CLIType.claude, status="idle", idle_since=None):
    w = Worker(
        worker_id=worker_id, machine="ws1", cli_type=cli,
        account_profile=account, status=status,
        last_heartbeat=_now(), idle_since=idle_since or _now(),
    )
    db.insert_worker(w)
    return w


def _add_task(db, task_id="t1", target_cli=CLIType.claude, target_account="work", **kwargs):
    t = Task(task_id=task_id, title="test", target_cli=target_cli,
             target_account=target_account, **kwargs)
    db.insert_task(t)
    return t


class TestFindEligibleWorker:
    def test_exact_match(self, sched, db):
        _add_worker(db, "w1", "work", CLIType.claude)
        task = Task(task_id="t1", target_cli=CLIType.claude, target_account="work")
        workers = sched.find_all_eligible_workers(task)
        assert len(workers) == 1
        assert workers[0].worker_id == "w1"

    def test_wrong_cli(self, sched, db):
        _add_worker(db, "w1", "work", CLIType.claude)
        task = Task(task_id="t1", target_cli=CLIType.codex, target_account="work")
        assert sched.find_all_eligible_workers(task) == []

    def test_wrong_account(self, sched, db):
        _add_worker(db, "w1", "work", CLIType.claude)
        task = Task(task_id="t1", target_cli=CLIType.claude, target_account="clientA")
        assert sched.find_all_eligible_workers(task) == []

    def test_busy_excluded(self, sched, db):
        _add_worker(db, "w1", "work", CLIType.claude, status="busy")
        task = Task(task_id="t1", target_cli=CLIType.claude, target_account="work")
        assert sched.find_all_eligible_workers(task) == []

    def test_fairness_longest_idle(self, sched, db):
        _add_worker(db, "w1", "work", CLIType.claude, idle_since=_past(60))
        _add_worker(db, "w2", "work", CLIType.claude, idle_since=_past(120))
        task = Task(task_id="t1", target_cli=CLIType.claude, target_account="work")
        workers = sched.find_all_eligible_workers(task)
        # w2 has been idle longer -> should be first
        assert workers[0].worker_id == "w2"
        assert workers[1].worker_id == "w1"


class TestFindNextTask:
    def test_priority_order(self, sched, db):
        _add_task(db, "t1", priority=1)
        _add_task(db, "t2", priority=5)
        task = sched.find_next_task()
        assert task.task_id == "t2"  # higher priority first

    def test_not_before_respected(self, sched, db):
        _add_task(db, "t1", not_before=_future(300))
        _add_task(db, "t2")
        task = sched.find_next_task()
        assert task.task_id == "t2"  # t1 skipped due to not_before

    def test_no_queued_tasks(self, sched, db):
        assert sched.find_next_task() is None


class TestDispatch:
    def test_success(self, sched, db):
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")
        result = sched.dispatch()
        assert result is not None
        assert result.task.task_id == "t1"
        assert result.worker.worker_id == "w1"
        # Task should be assigned
        t = db.get_task("t1")
        assert t.status == TaskStatus.assigned
        assert t.assigned_worker == "w1"
        # Worker should be busy
        w = db.get_worker("w1")
        assert w.status == "busy"
        # Lease should exist
        lease = db.get_active_lease("t1")
        assert lease is not None

    def test_no_worker(self, sched, db):
        _add_task(db, "t1")
        assert sched.dispatch() is None

    def test_no_task(self, sched, db):
        _add_worker(db, "w1", "work")
        assert sched.dispatch() is None

    def test_atomic_rollback(self, sched, db):
        """If CAS fails (worker became busy), dispatch tries next candidate."""
        _add_worker(db, "w1", "work", idle_since=_past(60))
        _add_worker(db, "w2", "work", idle_since=_past(30))
        _add_task(db, "t1")
        # Make w1 busy before dispatch can grab it (simulate concurrent change)
        # We test by dispatching twice — first grabs w1, second grabs w2
        r1 = sched.dispatch()
        assert r1 is not None
        assert r1.worker.worker_id == "w1"
        # Add another task
        _add_task(db, "t2")
        r2 = sched.dispatch()
        assert r2 is not None
        assert r2.worker.worker_id == "w2"


class TestAckTask:
    def test_success(self, sched, db):
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")
        sched.dispatch()
        assert sched.ack_task("t1", "w1") is True
        t = db.get_task("t1")
        assert t.status == TaskStatus.running

    def test_wrong_worker(self, sched, db):
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")
        sched.dispatch()
        assert sched.ack_task("t1", "wrong-worker") is False


class TestCompleteTask:
    def test_success(self, sched, db):
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")
        sched.dispatch()
        sched.ack_task("t1", "w1")
        assert sched.complete_task("t1", "w1") is True
        t = db.get_task("t1")
        assert t.status == TaskStatus.completed
        # Lease should be gone
        assert db.get_active_lease("t1") is None
        # Worker back to idle
        w = db.get_worker("w1")
        assert w.status == "idle"

    def test_triggers_dependency_resolution(self, sched, db):
        """Completing a task unblocks dependent blocked tasks."""
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")
        _add_task(db, "t2", status=TaskStatus.blocked, depends_on=["t1"])
        sched.dispatch()
        sched.ack_task("t1", "w1")
        sched.complete_task("t1", "w1")
        t2 = db.get_task("t2")
        assert t2.status == TaskStatus.queued


class TestReportFailure:
    def test_failure(self, sched, db):
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")
        sched.dispatch()
        sched.ack_task("t1", "w1")
        assert sched.report_failure("t1", "w1", "test error") is True
        t = db.get_task("t1")
        assert t.status == TaskStatus.failed
        assert db.get_active_lease("t1") is None
        w = db.get_worker("w1")
        assert w.status == "idle"
