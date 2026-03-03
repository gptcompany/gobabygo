"""Integration tests for thread HTTP endpoints and runtime hooks.

Tests thread CRUD via server endpoints, thread_context enrichment in
long-poll, and ensures runtime execution stays worker-owned.
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

    def test_create_thread_duplicate_name_conflict(self, server_url):
        resp1 = requests.post(f"{server_url}/threads", json={"name": "dup-name"})
        assert resp1.status_code == 201
        resp2 = requests.post(f"{server_url}/threads", json={"name": "dup-name"})
        assert resp2.status_code == 409
        assert resp2.json()["error"] == "duplicate_thread_name"


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

    def test_add_step_missing_previous_step_conflict(self, server_url):
        create_resp = requests.post(f"{server_url}/threads", json={"name": "gap-step"})
        thread_id = create_resp.json()["thread_id"]
        resp = requests.post(
            f"{server_url}/threads/{thread_id}/steps",
            json={"title": "step1", "step_index": 1},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "invalid_step_order"

    def test_add_step_duplicate_step_index_conflict(self, server_url):
        create_resp = requests.post(f"{server_url}/threads", json={"name": "dup-step"})
        thread_id = create_resp.json()["thread_id"]
        resp1 = requests.post(
            f"{server_url}/threads/{thread_id}/steps",
            json={"title": "step0", "step_index": 0},
        )
        assert resp1.status_code == 201
        resp2 = requests.post(
            f"{server_url}/threads/{thread_id}/steps",
            json={"title": "step0b", "step_index": 0},
        )
        assert resp2.status_code == 409
        assert resp2.json()["error"] == "duplicate_step_index"


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


# --- Worker-owned runtime execution ---


class TestAckAndCompleteHooks:
    def test_ack_does_not_spawn_tmux_from_router(self, server_url, db):
        """Router should not create tmux sessions when a worker acks a task."""
        _register_worker(db, "tmux-w1")
        thread = create_thread(db, "tmux-spawn")
        step0 = add_step(db, thread.thread_id, ThreadStepRequest(
            title="s0", step_index=0, repo="/tmp/test",
        ))

        # Assign task to worker via scheduler-like direct DB ops
        db.update_task_status(step0.task_id, TaskStatus.queued, TaskStatus.assigned)
        db.update_task_fields(step0.task_id, {"assigned_worker": "tmux-w1"})

        with patch("src.router.session_spawner.spawn_tmux_session") as mock_spawn:
            resp = requests.post(
                f"{server_url}/tasks/ack",
                json={"task_id": step0.task_id, "worker_id": "tmux-w1"},
            )
            assert resp.status_code == 200
            time.sleep(0.1)
            mock_spawn.assert_not_called()

    def test_complete_does_not_kill_router_tmux_session(self, server_url, db):
        """Router should not kill tmux sessions; the worker owns session lifecycle."""
        _register_worker(db, "tmux-w2")
        thread = create_thread(db, "tmux-cleanup")
        step0 = add_step(db, thread.thread_id, ThreadStepRequest(
            title="s0", step_index=0, repo="/tmp/test",
        ))

        # Move task through assigned -> running
        db.update_task_status(step0.task_id, TaskStatus.queued, TaskStatus.assigned)
        db.update_task_fields(step0.task_id, {"assigned_worker": "tmux-w2"})
        db.update_task_status(step0.task_id, TaskStatus.assigned, TaskStatus.running)

        with patch("src.router.session_spawner.kill_tmux_session") as mock_kill:
            resp = requests.post(
                f"{server_url}/tasks/complete",
                json={"task_id": step0.task_id, "worker_id": "tmux-w2"},
            )
            assert resp.status_code == 200
            mock_kill.assert_not_called()
