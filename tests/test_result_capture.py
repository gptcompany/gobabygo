"""Tests for Phase 14: Result Persistence + Read Path.

Covers: result field on Task, DB sanitization/truncation, scheduler persistence,
GET /tasks/{id}, GET /tasks?status=... endpoints.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

import pytest
import requests

from src.router.db import RouterDB
from src.router.heartbeat import HeartbeatManager
from src.router.longpoll import LongPollRegistry
from src.router.metrics import MeshMetrics
from src.router.models import Task, TaskStatus, Worker
from src.router.scheduler import Scheduler
from src.router.server import MeshRouterHandler
from src.router.worker_manager import WorkerManager
from src.router.bridge.transport import InProcessTransport


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "test_result.db")
    d = RouterDB(db_path, check_same_thread=False)
    d.init_schema()
    yield d
    d.close()


@pytest.fixture
def sched(db):
    return Scheduler(db=db, lease_duration_s=300)


@pytest.fixture
def server_url(db):
    longpoll_registry = LongPollRegistry()
    worker_manager = WorkerManager(db, tokens=[], dev_mode=True, longpoll_registry=longpoll_registry)
    heartbeat = HeartbeatManager(db, longpoll_registry=longpoll_registry)
    scheduler = Scheduler(db, longpoll_registry=longpoll_registry)
    transport = InProcessTransport(db)
    metrics = MeshMetrics()

    server = ThreadingHTTPServer(("127.0.0.1", 0), MeshRouterHandler)
    server.router_state = {
        "db": db,
        "worker_manager": worker_manager,
        "heartbeat": heartbeat,
        "scheduler": scheduler,
        "transport": transport,
        "metrics": metrics,
        "longpoll_registry": longpoll_registry,
        "longpoll_timeout": 0.1,
        "auth_token": None,
        "start_time": datetime.now(timezone.utc),
    }

    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"
    yield url
    server.shutdown()


def _setup_running_task(db, task_id="t1", worker_id="w1", critical=False):
    """Helper: insert a worker + task in running state with active lease."""
    worker = Worker(
        worker_id=worker_id, cli_type="claude",
        account_profile="work", status="busy",
        last_heartbeat=datetime.now(timezone.utc).isoformat(),
    )
    db.insert_worker(worker)
    task = Task(
        task_id=task_id, title="test task", phase="implement",
        target_cli="claude", target_account="work",
        status=TaskStatus.running, assigned_worker=worker_id,
        critical=critical,
    )
    db.insert_task(task)
    return task, worker


# -- Test 1: Complete with result persists --

class TestCompleteWithResult:
    def test_complete_with_result_persists(self, server_url, db):
        """POST /tasks/complete with result -> result stored in DB."""
        _setup_running_task(db)
        result_data = {"output": "hello world", "exit_code": 0}
        resp = requests.post(
            f"{server_url}/tasks/complete",
            json={"task_id": "t1", "worker_id": "w1", "result": result_data},
        )
        assert resp.status_code == 200
        task = db.get_task("t1")
        assert task.status == TaskStatus.completed
        assert task.result is not None
        assert task.result["output"] == "hello world"
        assert task.result["exit_code"] == 0

    def test_complete_without_result_backward_compat(self, server_url, db):
        """POST /tasks/complete without result -> OK, result is None."""
        _setup_running_task(db)
        resp = requests.post(
            f"{server_url}/tasks/complete",
            json={"task_id": "t1", "worker_id": "w1"},
        )
        assert resp.status_code == 200
        task = db.get_task("t1")
        assert task.status == TaskStatus.completed
        assert task.result is None


# -- Test 3: Result persisted on review transition --

class TestReviewTransition:
    def test_result_persisted_on_review_transition(self, sched, db):
        """Critical task: running -> review with result persisted."""
        _setup_running_task(db, critical=True)
        # Create lease for cleanup
        from src.router.models import Lease
        lease = Lease(task_id="t1", worker_id="w1", expires_at="2099-01-01T00:00:00Z")
        db.create_lease(lease)

        result_data = {"output": "review me", "exit_code": 0}
        ok = sched.complete_task("t1", "w1", result=result_data)
        assert ok is True
        task = db.get_task("t1")
        assert task.status == TaskStatus.review
        assert task.result is not None
        assert task.result["output"] == "review me"


# -- Test 4: Truncation --

class TestResultTruncation:
    def test_result_truncation_32kb(self, db):
        """Result > 32KB gets string values truncated + _truncated flag."""
        # Create a large result that exceeds 32KB when serialized
        big_output = "x" * 35000
        result = {"output": big_output, "exit_code": 0}
        sanitized = db._sanitize_result(result)
        assert sanitized is not None
        parsed = json.loads(sanitized)
        assert parsed["_truncated"] is True
        assert len(parsed["output"]) < len(big_output)
        assert parsed["output"].endswith("...[truncated]")


# -- Test 5: Secret filtering --

class TestSecretFiltering:
    def test_result_secret_filtering(self, db):
        """Result containing secret patterns gets them redacted."""
        result = {
            "output": "Using key sk-abc123def456ghi789jkl012mno with ghp_abcdefghijklmnopqrstuvwxyz0123456789AB",
            "token": "xoxb-" + "a" * 50,
        }
        sanitized = db._sanitize_result(result)
        assert sanitized is not None
        parsed = json.loads(sanitized)
        assert "sk-" not in parsed["output"] or "[REDACTED]" in parsed["output"]
        assert "[REDACTED]" in parsed["output"]
        assert "[REDACTED]" in parsed["token"]
        # Original secret values must not appear
        assert "sk-abc123def456ghi789jkl012mno" not in sanitized
        assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789AB" not in sanitized


# -- Tests 6-7: GET /tasks/{id} --

class TestGetTaskById:
    def test_get_task_by_id(self, server_url, db):
        """GET /tasks/{id} returns task with result field."""
        _setup_running_task(db)
        # Complete with result via scheduler
        from src.router.models import Lease
        lease = Lease(task_id="t1", worker_id="w1", expires_at="2099-01-01T00:00:00Z")
        db.create_lease(lease)
        sched = Scheduler(db=db, lease_duration_s=300)
        sched.complete_task("t1", "w1", result={"output": "done", "exit_code": 0})

        resp = requests.get(f"{server_url}/tasks/t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "t1"
        assert data["result"]["output"] == "done"
        assert data["status"] == "completed"

    def test_get_task_not_found(self, server_url):
        """GET /tasks/{id} with nonexistent ID returns 404."""
        resp = requests.get(f"{server_url}/tasks/nonexistent-id")
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"


# -- Tests 8-10: GET /tasks --

class TestListTasks:
    def test_list_tasks_all(self, server_url, db):
        """GET /tasks returns list of all tasks."""
        task1 = Task(task_id="t1", title="first", phase="implement")
        task2 = Task(task_id="t2", title="second", phase="implement")
        db.insert_task(task1)
        db.insert_task(task2)

        resp = requests.get(f"{server_url}/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert "tasks" in data
        assert len(data["tasks"]) == 2

    def test_list_tasks_by_status(self, server_url, db):
        """GET /tasks?status=completed returns only completed tasks."""
        task1 = Task(task_id="t1", title="first", phase="implement", status=TaskStatus.completed)
        task2 = Task(task_id="t2", title="second", phase="implement", status=TaskStatus.queued)
        db.insert_task(task1)
        db.insert_task(task2)

        resp = requests.get(f"{server_url}/tasks?status=completed")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["task_id"] == "t1"
        assert data["tasks"][0]["status"] == "completed"

    def test_list_tasks_empty(self, server_url):
        """GET /tasks?status=completed with no matching tasks returns empty list."""
        resp = requests.get(f"{server_url}/tasks?status=completed")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"] == []
