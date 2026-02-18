"""Tests for HeartbeatManager: heartbeat protocol and stale detection sweep."""

from datetime import datetime, timedelta, timezone

import pytest

from src.router.db import RouterDB
from src.router.heartbeat import HeartbeatManager, SweepResult, requeue_task
from src.router.models import CLIType, Lease, Task, TaskStatus, Worker


@pytest.fixture
def db():
    d = RouterDB(":memory:")
    d.init_schema()
    return d


@pytest.fixture
def hm(db):
    return HeartbeatManager(db=db, stale_threshold_s=35)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _past(seconds: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _register_worker(db, worker_id="w1", status="idle", heartbeat=None):
    w = Worker(
        worker_id=worker_id,
        machine="ws1",
        cli_type=CLIType.claude,
        account_profile="work",
        status=status,
        last_heartbeat=heartbeat or _now(),
        idle_since=_now(),
    )
    db.insert_worker(w)
    return w


class TestReceiveHeartbeat:
    def test_healthy_worker(self, hm, db):
        _register_worker(db)
        resp = hm.receive_heartbeat("w1")
        assert resp["status"] == "ok"

    def test_unknown_worker(self, hm):
        resp = hm.receive_heartbeat("nonexistent")
        assert resp["status"] == "unknown_worker"

    def test_offline_worker(self, hm, db):
        _register_worker(db, status="offline")
        resp = hm.receive_heartbeat("w1")
        assert resp["status"] == "offline"

    def test_stale_recovery(self, hm, db):
        _register_worker(db, status="stale")
        db.update_worker("w1", {"stale_since": _past(10)})
        resp = hm.receive_heartbeat("w1")
        assert resp["status"] == "stale_recovered"
        assert "requeued_tasks" in resp
        # Worker should now be idle
        w = db.get_worker("w1")
        assert w.status == "idle"
        assert w.stale_since is None

    def test_busy_worker_heartbeat(self, hm, db):
        _register_worker(db, status="busy")
        resp = hm.receive_heartbeat("w1")
        assert resp["status"] == "ok"


class TestStaleSweep:
    def test_marks_stale(self, hm, db):
        """Workers past threshold are marked stale."""
        _register_worker(db, heartbeat=_past(40))
        result = hm.run_stale_sweep()
        assert result.workers_marked_stale == 1
        w = db.get_worker("w1")
        assert w.status == "stale"
        assert w.stale_since is not None

    def test_no_false_positives(self, hm, db):
        """Workers within threshold are NOT marked stale."""
        _register_worker(db, heartbeat=_past(10))
        result = hm.run_stale_sweep()
        assert result.workers_marked_stale == 0
        w = db.get_worker("w1")
        assert w.status == "idle"

    def test_requeues_tasks(self, hm, db):
        """Stale sweep expires leases and requeues tasks."""
        _register_worker(db, worker_id="w1", status="busy", heartbeat=_past(40))
        task = Task(
            task_id="t1", title="test", status=TaskStatus.running,
            assigned_worker="w1", attempt=1,
        )
        db.insert_task(task)
        lease = Lease(task_id="t1", worker_id="w1", expires_at="2099-01-01T00:00:00+00:00")
        db.create_lease(lease)

        result = hm.run_stale_sweep()
        assert result.tasks_requeued == 1
        t = db.get_task("t1")
        assert t.status == TaskStatus.queued
        assert t.attempt == 2
        assert t.assigned_worker is None

    def test_fails_max_attempts(self, hm, db):
        """Task at max attempts transitions to failed."""
        _register_worker(db, worker_id="w1", status="busy", heartbeat=_past(40))
        task = Task(
            task_id="t1", title="test", status=TaskStatus.running,
            assigned_worker="w1", attempt=3,
        )
        db.insert_task(task)
        lease = Lease(task_id="t1", worker_id="w1", expires_at="2099-01-01T00:00:00+00:00")
        db.create_lease(lease)

        result = hm.run_stale_sweep()
        assert result.tasks_failed == 1
        t = db.get_task("t1")
        assert t.status == TaskStatus.failed

    def test_sweep_idempotent(self, hm, db):
        """Running sweep twice doesn't double-process."""
        _register_worker(db, heartbeat=_past(40))
        r1 = hm.run_stale_sweep()
        assert r1.workers_marked_stale == 1
        # Second sweep: worker is already stale, not in candidates
        r2 = hm.run_stale_sweep()
        assert r2.workers_marked_stale == 0

    def test_multiple_workers(self, hm, db):
        """Sweep handles multiple stale workers."""
        _register_worker(db, worker_id="w1", heartbeat=_past(40))
        _register_worker(db, worker_id="w2", heartbeat=_past(50))
        # w2 has different account to avoid uniqueness issue
        db.update_worker("w2", {"account_profile": "work2"})
        _register_worker(db, worker_id="w3", heartbeat=_past(5))
        db.update_worker("w3", {"account_profile": "work3"})

        result = hm.run_stale_sweep()
        assert result.workers_marked_stale == 2
        assert db.get_worker("w1").status == "stale"
        assert db.get_worker("w2").status == "stale"
        assert db.get_worker("w3").status == "idle"


class TestRequeueTask:
    def test_requeue_within_limit(self, db):
        task = Task(task_id="t1", title="test", status=TaskStatus.running, attempt=1)
        db.insert_task(task)
        requeued, failed = requeue_task(db, "t1", "test_reason")
        assert requeued is True
        assert failed is False
        t = db.get_task("t1")
        assert t.status == TaskStatus.queued
        assert t.attempt == 2

    def test_requeue_at_limit(self, db):
        task = Task(task_id="t1", title="test", status=TaskStatus.running, attempt=3)
        db.insert_task(task)
        requeued, failed = requeue_task(db, "t1", "test_reason")
        assert requeued is False
        assert failed is True
        t = db.get_task("t1")
        assert t.status == TaskStatus.failed

    def test_requeue_nonexistent(self, db):
        requeued, failed = requeue_task(db, "nonexistent", "test")
        assert requeued is False
        assert failed is False
