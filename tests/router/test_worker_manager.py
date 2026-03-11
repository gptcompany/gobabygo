"""Tests for WorkerManager: registration, deregistration, token auth, account uniqueness."""

import pytest

from src.router.db import RouterDB
from src.router.models import CLIType, Task, TaskStatus, Worker
from src.router.worker_manager import WorkerManager


@pytest.fixture
def db():
    d = RouterDB(":memory:")
    d.init_schema()
    return d


@pytest.fixture
def tokens():
    return [
        {"token": "valid-token-1", "expires_at": None},
        {"token": "expiring-token", "expires_at": "2099-12-31T23:59:59+00:00"},
        {"token": "expired-token", "expires_at": "2020-01-01T00:00:00+00:00"},
    ]


@pytest.fixture
def wm(db, tokens):
    return WorkerManager(db=db, tokens=tokens)


def _make_worker(worker_id="w1", account="work", cli=CLIType.claude):
    return Worker(
        worker_id=worker_id,
        machine="ws1",
        cli_type=cli,
        account_profile=account,
        capabilities=["code"],
    )


class TestRegisterWorker:
    def test_register_valid_token(self, wm, db):
        w = _make_worker()
        ok, msg = wm.register_worker(w, "valid-token-1")
        assert ok is True
        assert msg == "registered"
        fetched = db.get_worker("w1")
        assert fetched is not None
        assert fetched.status == "idle"
        assert fetched.idle_since is not None

    def test_register_invalid_token(self, wm):
        w = _make_worker()
        ok, msg = wm.register_worker(w, "bad-token")
        assert ok is False
        assert msg == "invalid_token"

    def test_register_expired_token(self, wm):
        w = _make_worker()
        ok, msg = wm.register_worker(w, "expired-token")
        assert ok is False
        assert msg == "invalid_token"

    def test_register_account_in_use(self, wm):
        w1 = _make_worker("w1", "work")
        wm.register_worker(w1, "valid-token-1")
        # Different worker_id, same account_profile
        w2 = _make_worker("w2", "work")
        ok, msg = wm.register_worker(w2, "valid-token-1")
        assert ok is False
        assert msg == "account_in_use"

    def test_reregister_same_worker_id(self, wm, db):
        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        # Mark as stale
        db.update_worker("w1", {"status": "stale"})
        # Re-register same worker_id
        w2 = _make_worker()
        ok, msg = wm.register_worker(w2, "valid-token-1")
        assert ok is True
        assert msg == "re-registered"
        fetched = db.get_worker("w1")
        assert fetched.status == "idle"

    def test_account_uniqueness_includes_stale(self, wm, db):
        """Stale workers still reserve their account_profile."""
        w1 = _make_worker("w1", "work")
        wm.register_worker(w1, "valid-token-1")
        db.update_worker("w1", {"status": "stale"})
        # Different worker_id, same account — should be rejected
        w2 = _make_worker("w2", "work")
        ok, msg = wm.register_worker(w2, "valid-token-1")
        assert ok is False
        assert msg == "account_in_use"


class TestDeregisterWorker:
    def test_deregister_idle_worker(self, wm, db):
        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        ok, msg = wm.deregister_worker("w1")
        assert ok is True
        assert msg == "deregistered"
        fetched = db.get_worker("w1")
        assert fetched.status == "offline"

    def test_deregister_not_found(self, wm):
        ok, msg = wm.deregister_worker("nonexistent")
        assert ok is False
        assert msg == "not_found"

    def test_deregister_busy_fails_tasks_without_requeue(self, wm, db):
        from src.router.models import Lease

        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        db.update_worker("w1", {"status": "busy"})
        # Create a task assigned to this worker
        task = Task(task_id="t1", title="test task", status=TaskStatus.running, assigned_worker="w1")
        db.insert_task(task)
        lease = Lease(task_id="t1", worker_id="w1", expires_at="2099-01-01T00:00:00+00:00")
        db.create_lease(lease)

        ok, msg = wm.deregister_worker("w1")
        assert ok is True
        # Task should fail terminally to avoid dual execution while the host
        # tmux session may still be alive.
        t = db.get_task("t1")
        assert t.status == TaskStatus.failed
        assert t.attempt == 1
        assert t.assigned_worker is None

    def test_deregister_busy_max_attempts_fails_task(self, wm, db):
        from src.router.models import Lease

        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        db.update_worker("w1", {"status": "busy"})
        # Task at max attempts
        task = Task(task_id="t1", title="test", status=TaskStatus.running, assigned_worker="w1", attempt=3)
        db.insert_task(task)
        lease = Lease(task_id="t1", worker_id="w1", expires_at="2099-01-01T00:00:00+00:00")
        db.create_lease(lease)

        wm.deregister_worker("w1")
        t = db.get_task("t1")
        assert t.status == TaskStatus.failed

    def test_deregister_draining_fails_assigned_task(self, wm, db):
        from src.router.models import Lease

        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        db.update_worker("w1", {"status": "draining"})
        task = Task(task_id="t1", title="test", status=TaskStatus.assigned, assigned_worker="w1")
        db.insert_task(task)
        lease = Lease(task_id="t1", worker_id="w1", expires_at="2099-01-01T00:00:00+00:00")
        db.create_lease(lease)

        ok, msg = wm.deregister_worker("w1")
        assert ok is True
        assert msg == "deregistered"
        t = db.get_task("t1")
        assert t.status == TaskStatus.failed
        assert t.attempt == 1
        assert t.assigned_worker is None


class TestWorkerStatusTransitions:
    def test_valid_transitions(self, wm, db):
        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        # idle -> busy
        assert wm.transition_worker_status("w1", "idle", "busy") is True
        assert db.get_worker("w1").status == "busy"
        # busy -> idle
        assert wm.transition_worker_status("w1", "busy", "idle") is True
        assert db.get_worker("w1").status == "idle"
        assert db.get_worker("w1").idle_since is not None

    def test_invalid_transition(self, wm, db):
        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        # idle -> running (not a valid worker status)
        assert wm.transition_worker_status("w1", "idle", "running") is False

    def test_stale_transition_sets_stale_since(self, wm, db):
        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        assert wm.transition_worker_status("w1", "idle", "stale") is True
        fetched = db.get_worker("w1")
        assert fetched.status == "stale"
        assert fetched.stale_since is not None


class TestDrainWorker:
    def test_drain_idle_worker_no_tasks(self, wm, db):
        """Draining an idle worker with no tasks retires immediately."""
        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        ok, msg = wm.drain_worker("w1")
        assert ok is True
        assert msg == "drained_immediately"
        fetched = db.get_worker("w1")
        assert fetched.status == "offline"

    def test_drain_busy_worker(self, wm, db):
        """Draining a busy worker with running tasks sets to draining."""
        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        db.update_worker("w1", {"status": "busy"})
        # Insert a task assigned to this worker with status running
        task = Task(
            task_id="t1",
            title="test task",
            status=TaskStatus.running,
            assigned_worker="w1",
        )
        db.insert_task(task)
        ok, msg = wm.drain_worker("w1")
        assert ok is True
        assert msg == "draining"
        fetched = db.get_worker("w1")
        assert fetched.status == "draining"

    def test_drain_stale_worker_rejected(self, wm, db):
        """Stale workers cannot be drained."""
        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        db.update_worker("w1", {"status": "stale"})
        ok, msg = wm.drain_worker("w1")
        assert ok is False
        assert msg == "invalid_state"

    def test_drain_offline_worker_rejected(self, wm, db):
        """Offline workers cannot be drained."""
        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        db.update_worker("w1", {"status": "offline"})
        ok, msg = wm.drain_worker("w1")
        assert ok is False
        assert msg == "invalid_state"

    def test_drain_not_found(self, wm):
        """Draining a nonexistent worker returns not_found."""
        ok, msg = wm.drain_worker("nonexistent")
        assert ok is False
        assert msg == "not_found"

    def test_drain_already_draining(self, wm, db):
        """Draining a worker already in draining state returns already_draining."""
        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        # Manually set to draining (simulating prior drain call)
        db.update_worker("w1", {"status": "draining"})
        ok, msg = wm.drain_worker("w1")
        assert ok is True
        assert msg == "already_draining"

    def test_fsm_draining_to_idle_rejected(self, wm, db):
        """draining -> idle is not a valid FSM transition."""
        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        db.update_worker("w1", {"status": "draining"})
        assert wm.transition_worker_status("w1", "draining", "idle") is False

    def test_fsm_draining_to_offline_allowed(self, wm, db):
        """draining -> offline is a valid FSM transition."""
        w = _make_worker()
        wm.register_worker(w, "valid-token-1")
        db.update_worker("w1", {"status": "draining"})
        assert wm.transition_worker_status("w1", "draining", "offline") is True
        fetched = db.get_worker("w1")
        assert fetched.status == "offline"


class TestTokenRotation:
    def test_grace_period_both_tokens_valid(self, db):
        """During rotation, both old and new tokens are accepted."""
        tokens = [
            {"token": "old-token", "expires_at": "2099-12-31T00:00:00+00:00"},
            {"token": "new-token", "expires_at": None},
        ]
        wm = WorkerManager(db=db, tokens=tokens)
        assert wm.validate_token("old-token") is True
        assert wm.validate_token("new-token") is True
        assert wm.validate_token("unknown") is False
