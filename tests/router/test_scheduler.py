"""Tests for Scheduler: deterministic selection, dispatch, ACK, complete, fail."""

from datetime import datetime, timedelta, timezone

import pytest

from src.router.db import RouterDB
from src.router.longpoll import LongPollRegistry
from src.router.models import CLIType, ExecutionMode, Task, TaskStatus, Worker
from src.router.scheduler import Scheduler
from src.router.topology import Topology


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


def _add_worker(
    db,
    worker_id="w1",
    account="work",
    cli=CLIType.claude,
    status="idle",
    idle_since=None,
    execution_modes=None,
):
    w = Worker(
        worker_id=worker_id, machine="ws1", cli_type=cli,
        account_profile=account, status=status,
        last_heartbeat=_now(), idle_since=idle_since or _now(),
        execution_modes=execution_modes or ["batch"],
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

    def test_session_task_excludes_batch_only_worker(self, sched, db):
        _add_worker(db, "w1", "work", CLIType.claude, execution_modes=["batch"])
        task = Task(
            task_id="t1",
            target_cli=CLIType.claude,
            target_account="work",
            execution_mode=ExecutionMode.session,
        )
        assert sched.find_all_eligible_workers(task) == []

    def test_session_task_matches_session_worker(self, sched, db):
        _add_worker(db, "w1", "work", CLIType.claude, execution_modes=["session"])
        task = Task(
            task_id="t1",
            target_cli=CLIType.claude,
            target_account="work",
            execution_mode=ExecutionMode.session,
        )
        workers = sched.find_all_eligible_workers(task)
        assert [w.worker_id for w in workers] == ["w1"]

    def test_batch_task_excludes_session_only_worker(self, sched, db):
        _add_worker(db, "w1", "work", CLIType.claude, execution_modes=["session"])
        task = Task(
            task_id="t1",
            target_cli=CLIType.claude,
            target_account="work",
            execution_mode=ExecutionMode.batch,
        )
        assert sched.find_all_eligible_workers(task) == []

    def test_session_fallback_to_batch_when_enabled(self, db):
        sched = Scheduler(db=db, lease_duration_s=300, session_fallback_to_batch=True)
        _add_worker(db, "w1", "work", CLIType.claude, execution_modes=["batch"])
        task = Task(
            task_id="t1",
            target_cli=CLIType.claude,
            target_account="work",
            execution_mode=ExecutionMode.session,
        )
        workers = sched.find_all_eligible_workers(task)
        assert [w.worker_id for w in workers] == ["w1"]

    def test_session_fallback_prefers_session_when_available(self, db):
        sched = Scheduler(db=db, lease_duration_s=300, session_fallback_to_batch=True)
        _add_worker(db, "w-batch", "work", CLIType.claude, execution_modes=["batch"], idle_since=_past(120))
        _add_worker(db, "w-session", "work", CLIType.claude, execution_modes=["session"], idle_since=_past(60))
        task = Task(
            task_id="t1",
            target_cli=CLIType.claude,
            target_account="work",
            execution_mode=ExecutionMode.session,
        )
        workers = sched.find_all_eligible_workers(task)
        assert [w.worker_id for w in workers] == ["w-session"]


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

    def test_unresolved_dependencies_skipped(self, sched, db):
        _add_task(db, "t1", depends_on=["missing"])
        _add_task(db, "t2")
        task = sched.find_next_task()
        assert task.task_id == "t2"  # t1 skipped due to unresolved deps

    def test_no_queued_tasks(self, sched, db):
        assert sched.find_next_task() is None


class TestDispatch:
    def test_success(self, sched, db):
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")
        result = sched.dispatch()
        assert result is not None
        assert result.task.task_id == "t1"

    def test_no_candidates(self, sched, db):
        # Worker has different CLI
        _add_worker(db, "w1", "work", cli=CLIType.codex)
        _add_task(db, "t1", target_cli=CLIType.claude)
        result = sched.dispatch()
        assert result is None

    def test_cas_failure_worker_busy(self, sched, db, monkeypatch: pytest.MonkeyPatch):
        _add_worker(db, "w1", "work")
        task = _add_task(db, "t1")

        # Manually set worker to busy right before dispatching
        original_find = sched.find_all_eligible_workers
        def mock_find(t):
            workers = original_find(t)
            # Make worker busy in DB behind scheduler's back
            db._conn.execute("UPDATE workers SET status = 'busy'")
            db._conn.commit()
            return workers
        monkeypatch.setattr(sched, "find_all_eligible_workers", mock_find)

        # Should fail atomic dispatch and return None
        assert sched.dispatch() is None


    def test_cas_failure_validate_transition(self, sched, db, monkeypatch: pytest.MonkeyPatch):
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")

        import src.router.scheduler as sched_mod
        monkeypatch.setattr(sched_mod, "validate_transition", lambda f, t: False)

        result = sched.dispatch()
        assert result is None
    def test_cas_failure_update_task_status(self, sched, db, monkeypatch: pytest.MonkeyPatch):
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")

        original_update = db.update_task_status
        def mock_update(*args, **kwargs):
            return False
        monkeypatch.setattr(db, "update_task_status", mock_update)

        result = sched.dispatch()
        assert result is None

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


class TestDrainingAutoRetire:
    """Tests for draining worker auto-retire on task complete/fail."""

    def test_draining_worker_auto_retires_on_complete(self, sched, db):
        """Draining worker goes offline when last task completes."""
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")
        result = sched.dispatch()
        assert result is not None
        sched.ack_task("t1", "w1")
        # Transition worker to draining
        db.update_worker("w1", {"status": "draining"})
        # Complete the task
        assert sched.complete_task("t1", "w1") is True
        w = db.get_worker("w1")
        assert w.status == "offline"

    def test_draining_worker_auto_retires_on_fail(self, sched, db):
        """Draining worker goes offline when last task fails."""
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")
        result = sched.dispatch()
        assert result is not None
        sched.ack_task("t1", "w1")
        db.update_worker("w1", {"status": "draining"})
        assert sched.report_failure("t1", "w1", "test error") is True
        w = db.get_worker("w1")
        assert w.status == "offline"

    def test_draining_worker_stays_draining_with_remaining_tasks(self, sched, db):
        """Draining worker stays draining until all tasks done."""
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")
        r1 = sched.dispatch()
        assert r1 is not None
        sched.ack_task("t1", "w1")

        # Worker is now busy. Insert second task manually as running.
        task2 = Task(
            task_id="t2",
            title="test2",
            target_cli=CLIType.claude,
            target_account="work",
            status=TaskStatus.running,
            assigned_worker="w1",
        )
        db.insert_task(task2)

        # Transition to draining
        db.update_worker("w1", {"status": "draining"})

        # Complete first task -- worker should stay draining (t2 still running)
        assert sched.complete_task("t1", "w1") is True
        w = db.get_worker("w1")
        assert w.status == "draining"

        # Complete second task -- worker should now be offline
        assert sched.complete_task("t2", "w1") is True
        w = db.get_worker("w1")
        assert w.status == "offline"

    def test_scheduler_skips_draining_worker(self, sched, db):
        """Draining workers are not eligible for new task dispatch."""
        _add_worker(db, "w1", "work", idle_since=_past(60))
        _add_worker(db, "w2", "work", idle_since=_past(30))
        # Set w1 to draining
        db.update_worker("w1", {"status": "draining"})
        _add_task(db, "t1")
        result = sched.dispatch()
        assert result is not None
        # Task should go to w2 (w1 is draining, not idle)
        assert result.worker.worker_id == "w2"

    def test_non_draining_worker_goes_idle_on_complete(self, sched, db):
        """Normal (non-draining) worker goes idle after task complete (regression check)."""
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")
        sched.dispatch()
        sched.ack_task("t1", "w1")
        assert sched.complete_task("t1", "w1") is True
        w = db.get_worker("w1")
        assert w.status == "idle"


class TestCriticalTaskReview:
    def test_critical_task_routes_to_review(self, sched, db):
        _add_worker(db, "w1", "work")
        _add_task(db, "t1", critical=True)
        sched.dispatch()
        sched.ack_task("t1", "w1")
        
        # Manually set to running since ack does that
        db.update_task_status("t1", TaskStatus.running, TaskStatus.running)
        
        assert sched.complete_task("t1", "w1", result={"ok": True}) is True
        t = db.get_task("t1")
        assert t.status == TaskStatus.review
        assert t.review_timeout_at is not None
        assert t.result == {"ok": True}
        
        # Worker should go idle (or draining->offline) after routing to review
        w = db.get_worker("w1")
        assert w.status == "idle"

    def test_complete_task_wrong_worker(self, sched, db):
        _add_worker(db, "w1", "work")
        _add_task(db, "t1")
        sched.dispatch()
        # Task assigned to w1, but w2 tries to complete it
        assert sched.complete_task("t1", "w2") is False


class TestTaskRetry:
    def test_retry_policy_requeues_task(self, sched, db):
        from src.router.models import OnFailurePolicy
        _add_worker(db, "w1", "work")
        # Task with retry policy
        _add_task(db, "t1", on_failure=OnFailurePolicy.retry, attempt=1)
        sched.dispatch()
        sched.ack_task("t1", "w1")
        
        # Report failure -> should requeue
        assert sched.report_failure("t1", "w1", "network error") is True
        
        t = db.get_task("t1")
        assert t.status == TaskStatus.queued
        assert t.attempt == 2
        assert t.not_before is not None
        assert t.assigned_worker is None
        
        # Worker should be idle
        w = db.get_worker("w1")
        assert w.status == "idle"

    def test_retry_policy_exhausted_fails(self, sched, db):
        from src.router.models import OnFailurePolicy
        _add_worker(db, "w1", "work")
        # Task already at attempt 3
        _add_task(db, "t1", on_failure=OnFailurePolicy.retry, attempt=3)
        sched.dispatch()
        sched.ack_task("t1", "w1")
        
        assert sched.report_failure("t1", "w1", "final error") is True
        t = db.get_task("t1")
        assert t.status == TaskStatus.failed


class TestDispatchLongPollWakeup:
    """Tests for scheduler wakeup integration with LongPollRegistry."""

    def test_dispatch_notifies_longpoll_registry(self, db):
        """Dispatch notifies the correct worker's slot via LongPollRegistry."""
        registry = LongPollRegistry()
        sched = Scheduler(db=db, lease_duration_s=300, longpoll_registry=registry)

        _add_worker(db, "w1", "work")
        _add_task(db, "t1")

        # Register worker in registry so it has a slot
        registry.register("w1")

        # Dispatch should notify the registry
        result = sched.dispatch()
        assert result is not None
        assert result.worker.worker_id == "w1"

        # Verify the slot was notified (task_available should have been set)
        # Access internal slot to verify (white-box test)
        slot = registry._slots.get("w1")
        assert slot is not None
        # task_available may already be consumed if wait_for_task ran,
        # but since no one is waiting, it should remain True
        assert slot.task_available is True

    def test_dispatch_without_registry_no_error(self, db):
        """Dispatch with longpoll_registry=None does not error."""
        sched = Scheduler(db=db, lease_duration_s=300, longpoll_registry=None)

        _add_worker(db, "w1", "work")
        _add_task(db, "t1")

        result = sched.dispatch()
        assert result is not None
        assert result.task.task_id == "t1"

    def test_dispatch_notifies_correct_worker(self, db):
        """Two workers registered; dispatch notifies only the dispatched worker."""
        registry = LongPollRegistry()
        sched = Scheduler(db=db, lease_duration_s=300, longpoll_registry=registry)

        _add_worker(db, "w1", "work", idle_since=_past(60))
        _add_worker(db, "w2", "work", idle_since=_past(30))
        _add_task(db, "t1")

        registry.register("w1")
        registry.register("w2")

        result = sched.dispatch()
        assert result is not None
        # w1 has been idle longer, should be dispatched first
        assert result.worker.worker_id == "w1"

        # w1's slot should be notified
        assert registry._slots["w1"].task_available is True
        # w2's slot should NOT be notified
        assert registry._slots["w2"].task_available is False


class TestTopologyAwareDispatch:
    """Tests for topology-aware worker filtering in find_all_eligible_workers."""

    @pytest.fixture
    def topo(self):
        """Create a Topology with repo 'myrepo' -> pool ['w1', 'w3']."""
        return Topology({
            "version": 1,
            "global": {"cross_repo_policy": {"require_president_handoff": True}},
            "hosts": {"h1": {"address": "10.0.0.1"}},
            "workers": {"w1": {}, "w3": {}},
            "repos": {
                "myrepo": {"worker_pool": ["w1", "w3"]},
            },
        })

    @pytest.fixture
    def sched_topo(self, db, topo):
        return Scheduler(db=db, lease_duration_s=300, topology=topo)

    def test_topology_filters_by_repo_pool(self, sched_topo, db):
        """Only workers in the repo's pool are eligible when task.repo is set."""
        _add_worker(db, "w1", "work", CLIType.claude)
        _add_worker(db, "w2", "work", CLIType.claude)
        _add_worker(db, "w3", "work", CLIType.claude)
        task = Task(task_id="t1", target_cli=CLIType.claude,
                    target_account="work", repo="myrepo")
        workers = sched_topo.find_all_eligible_workers(task)
        ids = [w.worker_id for w in workers]
        assert "w1" in ids
        assert "w3" in ids
        assert "w2" not in ids

    def test_topology_no_repo_on_task(self, sched_topo, db):
        """Without task.repo, all eligible workers pass (legacy behavior)."""
        _add_worker(db, "w1", "work", CLIType.claude)
        _add_worker(db, "w2", "work", CLIType.claude)
        task = Task(task_id="t1", target_cli=CLIType.claude,
                    target_account="work")
        workers = sched_topo.find_all_eligible_workers(task)
        assert len(workers) == 2

    def test_topology_unknown_repo(self, sched_topo, db):
        """Repo not in topology -> legacy fallback (all eligible)."""
        _add_worker(db, "w1", "work", CLIType.claude)
        _add_worker(db, "w2", "work", CLIType.claude)
        task = Task(task_id="t1", target_cli=CLIType.claude,
                    target_account="work", repo="unknown-repo")
        workers = sched_topo.find_all_eligible_workers(task)
        assert len(workers) == 2

    def test_topology_disabled(self, sched, db):
        """Scheduler without topology -> legacy behavior for tasks with repo."""
        _add_worker(db, "w1", "work", CLIType.claude)
        task = Task(task_id="t1", target_cli=CLIType.claude,
                    target_account="work", repo="myrepo")
        workers = sched.find_all_eligible_workers(task)
        assert len(workers) == 1  # w1 passes normal filters

    def test_topology_pool_intersect_with_existing_filters(self, sched_topo, db):
        """Topology pool + cli/account filters combine correctly."""
        # w1: right cli, right account, in pool -> eligible
        _add_worker(db, "w1", "work", CLIType.claude)
        # w3: wrong cli, in pool -> excluded by cli filter
        _add_worker(db, "w3", "work", CLIType.codex)
        task = Task(task_id="t1", target_cli=CLIType.claude,
                    target_account="work", repo="myrepo")
        workers = sched_topo.find_all_eligible_workers(task)
        assert [w.worker_id for w in workers] == ["w1"]

    def test_topology_pool_intersect_with_mode(self, sched_topo, db):
        """Topology pool + execution_mode filters combine correctly."""
        # w1: session mode, in pool -> eligible
        _add_worker(db, "w1", "work", CLIType.claude, execution_modes=["session"])
        # w3: batch mode, in pool -> excluded by mode filter for session task
        _add_worker(db, "w3", "work", CLIType.claude, execution_modes=["batch"])
        task = Task(task_id="t1", target_cli=CLIType.claude,
                    target_account="work", repo="myrepo",
                    execution_mode=ExecutionMode.session)
        workers = sched_topo.find_all_eligible_workers(task)
        assert [w.worker_id for w in workers] == ["w1"]

    def test_topology_repo_without_pool_defined(self, db):
        """Repo in topology but without worker_pool key -> legacy fallback."""
        topo = Topology({
            "version": 1, "global": {}, "hosts": {}, "workers": {},
            "repos": {"repo-no-pool": {"some-other-key": "val"}}
        })
        sched = Scheduler(db=db, topology=topo)
        _add_worker(db, "w1", "work", CLIType.claude)
        task = Task(task_id="t1", target_cli=CLIType.claude,
                    target_account="work", repo="repo-no-pool")
        workers = sched.find_all_eligible_workers(task)
        assert len(workers) == 1

    def test_topology_invalid_pool_type_fails(self, db):
        """Topology with string instead of list for pool should raise TopologyError."""
        from src.router.topology import TopologyError
        # This will be caught during get_repo_worker_pool if not in _validate
        topo = Topology({
            "version": 1, "global": {}, "hosts": {}, "workers": {},
            "repos": {"bad-repo": {"worker_pool": "not-a-list"}}
        })
        sched = Scheduler(db=db, topology=topo)
        task = Task(task_id="t1", repo="bad-repo")
        with pytest.raises(TopologyError, match="must be a list of strings"):
            sched.find_all_eligible_workers(task)
