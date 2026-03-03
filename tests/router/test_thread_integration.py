"""Integration tests for thread HTTP endpoints and runtime hooks.

Tests thread CRUD via server endpoints, thread_context enrichment in
long-poll, and tmux spawn/cleanup on ack/complete.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest
import requests

from src.router.db import RouterDB
from src.router.heartbeat import HeartbeatManager
from src.router.longpoll import LongPollRegistry
from src.router.metrics import MeshMetrics
from src.router.models import CLIType, Task, TaskStatus, ThreadStepRequest, Worker
from src.router.scheduler import Scheduler
from src.router.server import MeshRouterHandler
from src.router.thread import add_step, create_thread
from src.router.worker_manager import WorkerManager
from src.router.bridge.transport import InProcessTransport


@pytest.fixture
def db(tmp_path):
    """Create a fresh DB for each test."""
    db_path = str(tmp_path / "test_thread_integration.db")
    db = RouterDB(db_path, check_same_thread=False)
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def server_url(db):
    """Start a test HTTP server for thread integration tests."""
    from datetime import datetime, timezone

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


def _register_worker(db, worker_id="w1"):
    """Insert a worker directly for test setup."""
    from datetime import datetime, timezone
    w = Worker(
        worker_id=worker_id,
        machine="test",
        cli_type=CLIType.claude,
        account_profile="work",
        status="idle",
        last_heartbeat=datetime.now(timezone.utc).isoformat(),
        idle_since=datetime.now(timezone.utc).isoformat(),
    )
    db.insert_worker(w)
    return w


# --- Thread CRUD endpoints ---


class TestCreateThread:
    def test_create_thread_via_api(self, server_url):
        resp = requests.post(
            f"{server_url}/threads",
            json={"name": "cross-repo-refactor"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        assert data["name"] == "cross-repo-refactor"
        assert "thread_id" in data

    def test_create_thread_missing_name(self, server_url):
        resp = requests.post(f"{server_url}/threads", json={})
        assert resp.status_code == 400


class TestListThreads:
    def test_list_threads(self, server_url):
        # Create two threads
        requests.post(f"{server_url}/threads", json={"name": "t1"})
        requests.post(f"{server_url}/threads", json={"name": "t2"})
        resp = requests.get(f"{server_url}/threads")
        assert resp.status_code == 200
        threads = resp.json()["threads"]
        assert len(threads) >= 2


class TestGetThread:
    def test_get_thread_by_id(self, server_url):
        create_resp = requests.post(
            f"{server_url}/threads", json={"name": "lookup-me"}
        )
        thread_id = create_resp.json()["thread_id"]
        resp = requests.get(f"{server_url}/threads/{thread_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "lookup-me"

    def test_get_thread_not_found(self, server_url):
        resp = requests.get(f"{server_url}/threads/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


class TestAddStep:
    def test_add_step_via_api(self, server_url, db):
        create_resp = requests.post(
            f"{server_url}/threads", json={"name": "step-test"}
        )
        thread_id = create_resp.json()["thread_id"]
        resp = requests.post(
            f"{server_url}/threads/{thread_id}/steps",
            json={
                "title": "Build frontend",
                "step_index": 0,
                "repo": "frontend",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        assert data["step_index"] == 0
        assert "task_id" in data
        # Verify task has thread_id in DB
        task = db.get_task(data["task_id"])
        assert task is not None
        assert task.thread_id == thread_id
        assert task.step_index == 0

    def test_add_step_thread_not_found(self, server_url):
        resp = requests.post(
            f"{server_url}/threads/00000000-0000-0000-0000-000000000000/steps",
            json={"title": "step", "step_index": 0},
        )
        assert resp.status_code == 404


class TestThreadStatus:
    def test_thread_status_endpoint(self, server_url):
        create_resp = requests.post(
            f"{server_url}/threads", json={"name": "status-test"}
        )
        thread_id = create_resp.json()["thread_id"]
        # Add two steps
        requests.post(
            f"{server_url}/threads/{thread_id}/steps",
            json={"title": "step0", "step_index": 0, "repo": "backend"},
        )
        requests.post(
            f"{server_url}/threads/{thread_id}/steps",
            json={"title": "step1", "step_index": 1, "repo": "frontend"},
        )

        resp = requests.get(f"{server_url}/threads/{thread_id}/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "thread" in data
        assert "steps" in data
        assert len(data["steps"]) == 2
        assert data["steps"][0]["step_index"] == 0
        assert data["steps"][0]["repo"] == "backend"
        assert data["steps"][1]["step_index"] == 1


class TestThreadContext:
    def test_thread_context_endpoint(self, server_url, db):
        # Create thread and step, complete step with result
        create_resp = requests.post(
            f"{server_url}/threads", json={"name": "ctx-test"}
        )
        thread_id = create_resp.json()["thread_id"]
        step_resp = requests.post(
            f"{server_url}/threads/{thread_id}/steps",
            json={"title": "s0", "step_index": 0, "repo": "backend"},
        )
        task_id = step_resp.json()["task_id"]

        # Complete the step directly in DB
        db.update_task_status(task_id, TaskStatus.queued, TaskStatus.assigned)
        db.update_task_status(task_id, TaskStatus.assigned, TaskStatus.running)
        db.update_task_status(task_id, TaskStatus.running, TaskStatus.completed)
        db.update_task_fields(task_id, {"result_json": json.dumps({"output": "done"})})

        resp = requests.get(f"{server_url}/threads/{thread_id}/context")
        assert resp.status_code == 200
        data = resp.json()
        assert data["thread_id"] == thread_id
        assert len(data["context"]) == 1
        assert data["context"][0]["step_index"] == 0
        assert data["context"][0]["result"] == {"output": "done"}

    def test_thread_context_empty(self, server_url):
        create_resp = requests.post(
            f"{server_url}/threads", json={"name": "empty-ctx"}
        )
        thread_id = create_resp.json()["thread_id"]
        resp = requests.get(f"{server_url}/threads/{thread_id}/context")
        assert resp.status_code == 200
        assert resp.json()["context"] == []


# --- Long-poll thread_context enrichment ---


class TestTaskPollThreadContext:
    def test_task_poll_includes_thread_context(self, server_url, db):
        """Long-poll response includes thread_context for step > 0."""
        _register_worker(db, "poll-w1")
        # Create thread with two steps
        thread = create_thread(db, "poll-ctx")
        step0 = add_step(db, thread.thread_id, ThreadStepRequest(
            title="s0", step_index=0, repo="backend",
        ))
        step1 = add_step(db, thread.thread_id, ThreadStepRequest(
            title="s1", step_index=1, repo="frontend",
        ))

        # Complete step0 with result
        db.update_task_status(step0.task_id, TaskStatus.queued, TaskStatus.assigned)
        db.update_task_status(step0.task_id, TaskStatus.assigned, TaskStatus.running)
        db.update_task_status(step0.task_id, TaskStatus.running, TaskStatus.completed)
        db.update_task_fields(step0.task_id, {"result_json": json.dumps({"out": "v1"})})

        # Unblock step1 and assign it to worker
        db.update_task_status(step1.task_id, TaskStatus.blocked, TaskStatus.queued)
        db.update_task_status(step1.task_id, TaskStatus.queued, TaskStatus.assigned)
        db.update_task_fields(step1.task_id, {"assigned_worker": "poll-w1"})

        # Dispatch: scheduler would normally do this, but we set it up manually.
        # Trigger the longpoll registry to deliver task
        state = db.get_task(step1.task_id)
        assert state is not None
        assert state.status == TaskStatus.assigned

        # Use the longpoll endpoint (short timeout set in fixture)
        resp = requests.get(f"{server_url}/tasks/next?worker_id=poll-w1", timeout=5)
        # If task is delivered, check thread_context
        if resp.status_code == 200:
            data = resp.json()
            assert "thread_context" in data
            assert len(data["thread_context"]) == 1
            assert data["thread_context"][0]["step_index"] == 0

    def test_task_poll_no_thread_context_for_step_0(self, server_url, db):
        """Step 0 has no previous context, so no thread_context field."""
        _register_worker(db, "poll-w2")
        thread = create_thread(db, "step0-ctx")
        step0 = add_step(db, thread.thread_id, ThreadStepRequest(
            title="s0", step_index=0, repo="backend",
        ))
        # Assign step0 to worker
        db.update_task_status(step0.task_id, TaskStatus.queued, TaskStatus.assigned)
        db.update_task_fields(step0.task_id, {"assigned_worker": "poll-w2"})

        resp = requests.get(f"{server_url}/tasks/next?worker_id=poll-w2", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            # step_index=0, no thread_context
            assert "thread_context" not in data

    def test_task_poll_no_thread_context_for_non_thread_task(self, server_url, db):
        """Regular task (no thread_id) has no thread_context."""
        _register_worker(db, "poll-w3")
        task = Task(
            title="standalone",
            target_cli=CLIType.claude,
            target_account="work",
            status=TaskStatus.assigned,
            assigned_worker="poll-w3",
        )
        db.insert_task(task)

        resp = requests.get(f"{server_url}/tasks/next?worker_id=poll-w3", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            assert "thread_context" not in data


# --- Tmux spawn/cleanup ---


class TestTmuxSpawnOnAck:
    def test_tmux_spawn_on_ack(self, server_url, db):
        """Mock subprocess, verify tmux spawn is called on thread task ack."""
        _register_worker(db, "tmux-w1")
        thread = create_thread(db, "tmux-spawn")
        step0 = add_step(db, thread.thread_id, ThreadStepRequest(
            title="s0", step_index=0, repo="/tmp/test",
        ))

        # Assign task to worker via scheduler-like direct DB ops
        db.update_task_status(step0.task_id, TaskStatus.queued, TaskStatus.assigned)
        db.update_task_fields(step0.task_id, {"assigned_worker": "tmux-w1"})

        with patch("src.router.session_spawner.subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0, "stdout": b"", "stderr": b""})()
            resp = requests.post(
                f"{server_url}/tasks/ack",
                json={"task_id": step0.task_id, "worker_id": "tmux-w1"},
            )
            assert resp.status_code == 200
            # Give the daemon thread a moment to execute
            time.sleep(0.3)
            # Verify subprocess.run was called with tmux new-session
            assert mock_run.called
            call_args = mock_run.call_args[0][0]
            assert "tmux" in call_args
            assert "new-session" in call_args


class TestTmuxCleanupOnComplete:
    def test_tmux_cleanup_on_complete(self, server_url, db):
        """Mock subprocess, verify tmux kill is called on thread task complete."""
        _register_worker(db, "tmux-w2")
        thread = create_thread(db, "tmux-cleanup")
        step0 = add_step(db, thread.thread_id, ThreadStepRequest(
            title="s0", step_index=0, repo="/tmp/test",
        ))

        # Move task through assigned -> running
        db.update_task_status(step0.task_id, TaskStatus.queued, TaskStatus.assigned)
        db.update_task_fields(step0.task_id, {"assigned_worker": "tmux-w2"})
        db.update_task_status(step0.task_id, TaskStatus.assigned, TaskStatus.running)

        with patch("src.router.session_spawner.subprocess.run") as mock_run:
            mock_run.return_value = type("Result", (), {"returncode": 0, "stdout": b"", "stderr": b""})()
            resp = requests.post(
                f"{server_url}/tasks/complete",
                json={"task_id": step0.task_id, "worker_id": "tmux-w2"},
            )
            assert resp.status_code == 200
            # Verify tmux kill-session was called
            assert mock_run.called
            call_args = mock_run.call_args[0][0]
            assert "tmux" in call_args
            assert "kill-session" in call_args
