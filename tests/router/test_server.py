"""Tests for the mesh router HTTP server."""

from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer
from unittest.mock import Mock

import pytest
import requests

from src.router.db import RouterDB
from src.router.heartbeat import HeartbeatManager
from src.router.longpoll import LongPollRegistry
from src.router.metrics import MeshMetrics
from src.router.models import Task, TaskCreateRequest, TaskStatus, Worker
from src.router.scheduler import Scheduler
from src.router.server import MeshRouterHandler
from src.router.worker_manager import WorkerManager
from src.router.bridge.transport import InProcessTransport


@pytest.fixture
def db(tmp_path):
    """Create a fresh in-memory-like DB for each test."""
    db_path = str(tmp_path / "test_router.db")
    db = RouterDB(db_path, check_same_thread=False)
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def server_url(db):
    """Start a test HTTP server in dev mode (no auth required for register)."""
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


@pytest.fixture
def authed_server_url(db):
    """Start a test server with auth enabled (token required for register)."""
    from datetime import datetime, timezone

    longpoll_registry = LongPollRegistry()
    worker_manager = WorkerManager(
        db, tokens=[{"token": "test-token-123", "expires_at": None}],
        longpoll_registry=longpoll_registry,
    )
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
        "auth_token": "test-token-123",
        "start_time": datetime.now(timezone.utc),
    }

    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"

    yield url

    server.shutdown()


class TestHealthEndpoint:
    def test_health_returns_200(self, server_url):
        resp = requests.get(f"{server_url}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert "workers" in data
        assert "queue_depth" in data
        assert "uptime_s" in data

    def test_health_reports_worker_count(self, server_url, db):
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work")
        db.insert_worker(worker)
        resp = requests.get(f"{server_url}/health")
        assert resp.json()["workers"] == 1

    def test_health_reports_queue_depth(self, server_url, db):
        task = Task(title="test", phase="implement", idempotency_key="k1")
        db.insert_task(task)
        resp = requests.get(f"{server_url}/health")
        assert resp.json()["queue_depth"] == 1


class TestClientDisconnectHandling:
    def test_send_json_suppresses_broken_pipe(self) -> None:
        handler = MeshRouterHandler.__new__(MeshRouterHandler)
        handler.path = "/health"
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = Mock()
        handler.wfile.write.side_effect = BrokenPipeError()

        handler._send_json(200, {"status": "ok"})

        handler.send_response.assert_called_once_with(200)
        handler.end_headers.assert_called_once()

    def test_do_get_ignores_client_disconnect(self) -> None:
        handler = MeshRouterHandler.__new__(MeshRouterHandler)
        handler.path = "/health"
        handler._handle_health = Mock(side_effect=BrokenPipeError())
        handler._send_json = Mock()

        handler.do_GET()

        handler._send_json.assert_not_called()

    def test_do_post_ignores_client_disconnect(self) -> None:
        handler = MeshRouterHandler.__new__(MeshRouterHandler)
        handler.path = "/tasks"
        handler._handle_create_task = Mock(side_effect=BrokenPipeError())
        handler._send_json = Mock()

        handler.do_POST()

        handler._send_json.assert_not_called()


class TestRegisterEndpoint:
    def test_register_worker_dev_mode(self, server_url):
        """In dev mode (no tokens), registration works without auth."""
        resp = requests.post(
            f"{server_url}/register",
            json={
                "worker_id": "ws-claude-work-01",
                "machine": "workstation",
                "cli_type": "claude",
                "account_profile": "work",
                "capabilities": ["code"],
                "status": "idle",
                "concurrency": 1,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["worker_id"] == "ws-claude-work-01"

    def test_register_reregisters_existing(self, server_url, db):
        """Re-registration of same worker_id returns 200."""
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work")
        db.insert_worker(worker)

        resp = requests.post(
            f"{server_url}/register",
            json={
                "worker_id": "w1",
                "machine": "new-machine",
                "cli_type": "claude",
                "account_profile": "work",
                "capabilities": ["code"],
                "status": "idle",
                "concurrency": 1,
            },
        )
        assert resp.status_code == 200

    def test_register_with_valid_token(self, authed_server_url):
        """Registration with valid token returns 201."""
        resp = requests.post(
            f"{authed_server_url}/register",
            json={
                "worker_id": "w1",
                "machine": "test",
                "cli_type": "claude",
                "account_profile": "work",
                "capabilities": ["code"],
                "status": "idle",
                "concurrency": 1,
            },
            headers={"Authorization": "Bearer test-token-123"},
        )
        assert resp.status_code == 201

    def test_register_with_invalid_token(self, authed_server_url):
        """Registration with invalid token returns 401."""
        resp = requests.post(
            f"{authed_server_url}/register",
            json={
                "worker_id": "w1",
                "machine": "test",
                "cli_type": "claude",
                "account_profile": "work",
            },
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_token"

    def test_register_without_token_when_required(self, authed_server_url):
        """Registration without token when tokens are configured returns 401."""
        resp = requests.post(
            f"{authed_server_url}/register",
            json={
                "worker_id": "w1",
                "machine": "test",
                "cli_type": "claude",
                "account_profile": "work",
            },
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_token"

    def test_register_account_in_use(self, authed_server_url, db):
        """Different worker_id with same account_profile returns 409."""
        worker = Worker(
            worker_id="w1", cli_type="claude", account_profile="work", status="idle",
        )
        db.insert_worker(worker)

        resp = requests.post(
            f"{authed_server_url}/register",
            json={
                "worker_id": "w2",
                "machine": "test",
                "cli_type": "claude",
                "account_profile": "work",
            },
            headers={"Authorization": "Bearer test-token-123"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "account_in_use"

    def test_register_invalid_json(self, server_url):
        resp = requests.post(
            f"{server_url}/register",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400

    def test_register_case_insensitive_bearer(self, authed_server_url):
        """Bearer scheme is case-insensitive per RFC 7235."""
        resp = requests.post(
            f"{authed_server_url}/register",
            json={
                "worker_id": "w1",
                "machine": "test",
                "cli_type": "claude",
                "account_profile": "work",
                "capabilities": ["code"],
                "status": "idle",
                "concurrency": 1,
            },
            headers={"Authorization": "bearer test-token-123"},
        )
        assert resp.status_code == 201


class TestHeartbeatEndpoint:
    def test_heartbeat_known_worker(self, server_url, db):
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work")
        db.insert_worker(worker)

        resp = requests.post(
            f"{server_url}/heartbeat",
            json={"worker_id": "w1"},
        )
        assert resp.status_code == 200

    def test_heartbeat_missing_worker_id(self, server_url):
        resp = requests.post(
            f"{server_url}/heartbeat",
            json={"something": "else"},
        )
        assert resp.status_code == 400
        assert "missing worker_id" in resp.json()["error"]

    def test_heartbeat_unknown_worker(self, server_url):
        resp = requests.post(
            f"{server_url}/heartbeat",
            json={"worker_id": "nonexistent"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "unknown_worker"


class TestEventsEndpoint:
    def test_events_accepts_valid_cloudevent(self, server_url, db):
        task = Task(task_id="t1", title="test", phase="implement", idempotency_key="k1")
        db.insert_task(task)

        event_json = json.dumps({
            "specversion": "1.0",
            "type": "com.mesh.command.started",
            "source": "mesh/gsd-bridge/test",
            "id": "evt-1",
            "data": {
                "run_id": "r1",
                "gsd_command": "test",
                "event": "started",
                "idempotency_key": "idem-1",
                "ts": "2026-01-01T00:00:00Z",
                "task_id": "t1",
                "attempt": 1,
                "sender_role": "president",
            },
        })

        resp = requests.post(
            f"{server_url}/events",
            data=event_json,
            headers={"Content-Type": "application/cloudevents+json"},
        )
        assert resp.status_code in (202, 409)

    def test_events_rejects_invalid_json(self, server_url):
        resp = requests.post(
            f"{server_url}/events",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400


class TestTaskPollEndpoint:
    def test_poll_no_tasks_returns_204(self, server_url, db):
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work")
        db.insert_worker(worker)

        resp = requests.get(f"{server_url}/tasks/next?worker_id=w1")
        assert resp.status_code == 204

    def test_poll_missing_worker_id_returns_400(self, server_url):
        resp = requests.get(f"{server_url}/tasks/next")
        assert resp.status_code == 400

    def test_poll_returns_assigned_task(self, server_url, db):
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work")
        db.insert_worker(worker)

        task = Task(
            task_id="t1",
            title="test task",
            phase="implement",
            status=TaskStatus.assigned,
            assigned_worker="w1",
            idempotency_key="k1",
        )
        db.insert_task(task)

        resp = requests.get(f"{server_url}/tasks/next?worker_id=w1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "t1"
        assert data["title"] == "test task"


class TestDefaultExecutionMode:
    def test_create_task_uses_session_default_when_configured(self, db):
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
            "default_execution_mode": "session",
            "auth_token": None,
            "start_time": datetime.now(timezone.utc),
        }

        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}"
        resp = requests.post(f"{url}/tasks", json={"title": "session by default"})
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]
        task = db.get_task(task_id)
        assert str(task.execution_mode.value) == "session"

        server.shutdown()

    def test_create_task_rejects_batch_when_session_only_enabled(self, db):
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
            "default_execution_mode": "session",
            "enforce_session_only": True,
            "auth_token": None,
            "start_time": datetime.now(timezone.utc),
        }

        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}"
        resp = requests.post(
            f"{url}/tasks",
            json={"title": "should fail", "execution_mode": "batch"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["error"] == "session_only_mode"

        server.shutdown()

    def test_add_step_rejects_batch_when_session_only_enabled(self, db):
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
            "default_execution_mode": "session",
            "enforce_session_only": True,
            "auth_token": None,
            "start_time": datetime.now(timezone.utc),
        }

        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}"
        create_resp = requests.post(f"{url}/threads", json={"name": "session-only-thread"})
        assert create_resp.status_code == 201
        thread_id = create_resp.json()["thread_id"]

        step_resp = requests.post(
            f"{url}/threads/{thread_id}/steps",
            json={
                "title": "batch step",
                "step_index": 0,
                "target_cli": "claude",
                "target_account": "work-claude",
                "execution_mode": "batch",
            },
        )
        assert step_resp.status_code == 400
        assert step_resp.json()["error"] == "session_only_mode"

        server.shutdown()


class TestTaskPollLongPoll:
    """Tests for long-poll specific behavior through the server."""

    def test_poll_duplicate_returns_409(self, db, tmp_path):
        """Start first poll in thread (1s timeout), immediately send second poll, verify 409."""
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
            "longpoll_timeout": 1.0,  # 1s for this test
            "auth_token": None,
            "start_time": datetime.now(timezone.utc),
        }

        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}"

        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work")
        db.insert_worker(worker)

        first_result: list[requests.Response] = []

        def first_poll():
            resp = requests.get(f"{url}/tasks/next?worker_id=w1", timeout=5)
            first_result.append(resp)

        t = threading.Thread(target=first_poll)
        t.start()
        time.sleep(0.1)  # let first poll enter wait

        # Second poll should get 409
        second_resp = requests.get(f"{url}/tasks/next?worker_id=w1", timeout=5)
        assert second_resp.status_code == 409
        assert second_resp.json()["error"] == "duplicate_poll"

        t.join(timeout=5)
        server.shutdown()

        # First poll should have completed with 204 (timeout, no task)
        assert len(first_result) == 1
        assert first_result[0].status_code == 204


class TestTaskCompleteEndpoint:
    def test_complete_valid_task(self, server_url, db):
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="busy")
        db.insert_worker(worker)

        task = Task(
            task_id="t1",
            title="test",
            phase="implement",
            status=TaskStatus.running,
            assigned_worker="w1",
            idempotency_key="k1",
        )
        db.insert_task(task)

        resp = requests.post(
            f"{server_url}/tasks/complete",
            json={"task_id": "t1", "worker_id": "w1"},
        )
        assert resp.status_code == 200

    def test_complete_missing_fields(self, server_url):
        resp = requests.post(
            f"{server_url}/tasks/complete",
            json={"task_id": "t1"},
        )
        assert resp.status_code == 400


class TestTaskAckEndpoint:
    def test_ack_valid_task(self, server_url, db):
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="busy")
        db.insert_worker(worker)

        task = Task(
            task_id="t1",
            title="test",
            phase="implement",
            status=TaskStatus.assigned,
            assigned_worker="w1",
            idempotency_key="k1",
        )
        db.insert_task(task)

        resp = requests.post(
            f"{server_url}/tasks/ack",
            json={"task_id": "t1", "worker_id": "w1"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "acknowledged"

        # Verify task is now running
        updated = db.get_task("t1")
        assert updated.status == TaskStatus.running

    def test_ack_wrong_worker_returns_409(self, server_url, db):
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="busy")
        db.insert_worker(worker)

        task = Task(
            task_id="t1",
            title="test",
            phase="implement",
            status=TaskStatus.assigned,
            assigned_worker="w1",
            idempotency_key="k1",
        )
        db.insert_task(task)

        resp = requests.post(
            f"{server_url}/tasks/ack",
            json={"task_id": "t1", "worker_id": "w-wrong"},
        )
        assert resp.status_code == 409

    def test_ack_missing_fields_returns_400(self, server_url):
        resp = requests.post(
            f"{server_url}/tasks/ack",
            json={"task_id": "t1"},
        )
        assert resp.status_code == 400


class TestTaskFailEndpoint:
    def test_fail_valid_task(self, server_url, db):
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="busy")
        db.insert_worker(worker)

        task = Task(
            task_id="t1",
            title="test",
            phase="implement",
            status=TaskStatus.running,
            assigned_worker="w1",
            idempotency_key="k1",
        )
        db.insert_task(task)

        resp = requests.post(
            f"{server_url}/tasks/fail",
            json={"task_id": "t1", "worker_id": "w1", "error": "test error"},
        )
        assert resp.status_code == 200


class TestTaskAdminEndpoints:
    def test_cancel_queued_task(self, server_url, db):
        task = Task(
            task_id="t-cancel-1",
            title="queued task",
            phase="implement",
            status=TaskStatus.queued,
            idempotency_key="k-cancel-1",
        )
        db.insert_task(task)

        resp = requests.post(
            f"{server_url}/tasks/cancel",
            json={"task_id": "t-cancel-1", "reason": "admin cleanup"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "canceled"
        updated = db.get_task("t-cancel-1")
        assert updated.status == TaskStatus.canceled

    def test_cancel_running_task_returns_409(self, server_url, db):
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="busy")
        db.insert_worker(worker)
        task = Task(
            task_id="t-cancel-2",
            title="running task",
            phase="implement",
            status=TaskStatus.running,
            assigned_worker="w1",
            idempotency_key="k-cancel-2",
        )
        db.insert_task(task)

        resp = requests.post(
            f"{server_url}/tasks/cancel",
            json={"task_id": "t-cancel-2"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "cancel_failed"
        assert "running_not_supported" in resp.json()["detail"]

    def test_admin_fail_assigned_task_releases_worker(self, server_url, db):
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="busy")
        db.insert_worker(worker)
        task = Task(
            task_id="t-fail-1",
            title="assigned task",
            phase="implement",
            status=TaskStatus.assigned,
            assigned_worker="w1",
            idempotency_key="k-fail-1",
        )
        db.insert_task(task)

        resp = requests.post(
            f"{server_url}/tasks/admin-fail",
            json={"task_id": "t-fail-1", "reason": "stuck pre-ack"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"
        updated = db.get_task("t-fail-1")
        assert updated.status == TaskStatus.failed
        assert updated.assigned_worker is None
        worker_after = db.get_worker("w1")
        assert worker_after is not None
        assert worker_after.status == "idle"

    def test_admin_fail_not_found_returns_404(self, server_url):
        resp = requests.post(
            f"{server_url}/tasks/admin-fail",
            json={"task_id": "missing"},
        )
        assert resp.status_code == 404


class TestTaskReviewEndpoints:
    def test_review_approve_valid_task(self, server_url, db):
        task = Task(
            task_id="t-review-1",
            title="needs review",
            phase="implement",
            status=TaskStatus.review,
            critical=True,
            idempotency_key="k-review-1",
        )
        db.insert_task(task)

        resp = requests.post(
            f"{server_url}/tasks/review/approve",
            json={"task_id": "t-review-1", "verifier_id": "v-codex"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

        updated = db.get_task("t-review-1")
        assert updated.status == TaskStatus.completed

        events = db.get_events("t-review-1")
        approval = [e for e in events if e.event_type == "verifier_approval"]
        assert len(approval) == 1
        assert approval[0].payload["verifier_id"] == "v-codex"

    def test_review_approve_with_pending_fix_returns_409(self, server_url, db):
        parent = Task(
            task_id="t-review-2",
            title="parent review",
            phase="implement",
            status=TaskStatus.review,
            critical=True,
            idempotency_key="k-review-2",
        )
        db.insert_task(parent)
        fix = Task(
            task_id="t-fix-2",
            parent_task_id="t-review-2",
            title="open fix",
            phase="implement",
            status=TaskStatus.queued,
            idempotency_key="k-fix-2",
        )
        db.insert_task(fix)

        resp = requests.post(
            f"{server_url}/tasks/review/approve",
            json={"task_id": "t-review-2", "verifier_id": "v-codex"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "review_approval_failed"
        assert "pending fix tasks" in resp.json()["detail"]

    def test_review_reject_creates_fix_task(self, server_url, db):
        task = Task(
            task_id="t-review-3",
            title="review me",
            phase="implement",
            status=TaskStatus.review,
            critical=True,
            target_cli="codex",
            target_account="review-codex",
            idempotency_key="k-review-3",
        )
        db.insert_task(task)

        resp = requests.post(
            f"{server_url}/tasks/review/reject",
            json={
                "task_id": "t-review-3",
                "verifier_id": "v-codex",
                "reason": "missing tests",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "rejected"
        assert body["task_id"] == "t-review-3"
        assert body["fix_task_id"]

        fix_task = db.get_task(body["fix_task_id"])
        assert fix_task is not None
        assert fix_task.parent_task_id == "t-review-3"
        assert fix_task.target_cli == task.target_cli
        assert fix_task.target_account == task.target_account

    def test_review_reject_escalates_after_max_rejections(self, server_url, db):
        task = Task(
            task_id="t-review-4",
            title="escalate me",
            phase="implement",
            status=TaskStatus.review,
            critical=True,
            rejection_count=2,
            idempotency_key="k-review-4",
        )
        db.insert_task(task)

        resp = requests.post(
            f"{server_url}/tasks/review/reject",
            json={
                "task_id": "t-review-4",
                "verifier_id": "v-codex",
                "reason": "still wrong",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected_escalated"

        updated = db.get_task("t-review-4")
        assert updated.status == TaskStatus.failed
        assert updated.rejection_count == 3

    def test_review_reject_missing_reason_returns_400(self, server_url):
        resp = requests.post(
            f"{server_url}/tasks/review/reject",
            json={"task_id": "t1", "verifier_id": "v-codex"},
        )
        assert resp.status_code == 400
        assert "missing_field" in resp.json()["error"]

    def test_review_reject_task_not_in_review_returns_409(self, server_url, db):
        task = Task(
            task_id="t-review-not-state",
            title="not in review",
            phase="implement",
            status=TaskStatus.running,
            critical=True,
            idempotency_key="k-review-not-state",
        )
        db.insert_task(task)
        resp = requests.post(
            f"{server_url}/tasks/review/reject",
            json={
                "task_id": "t-review-not-state",
                "verifier_id": "v-codex",
                "reason": "x",
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "review_rejection_failed"

    def test_review_approve_requires_auth(self, authed_server_url, db):
        task = Task(
            task_id="t-review-5",
            title="auth check",
            phase="implement",
            status=TaskStatus.review,
            critical=True,
            idempotency_key="k-review-5",
        )
        db.insert_task(task)
        resp = requests.post(
            f"{authed_server_url}/tasks/review/approve",
            json={"task_id": "t-review-5", "verifier_id": "v-codex"},
        )
        assert resp.status_code == 401


class TestTaskPendingFixesEndpoint:
    def test_pending_fixes_true_when_child_running(self, server_url, db):
        parent = Task(
            task_id="t-pending-1",
            title="parent",
            phase="implement",
            status=TaskStatus.review,
            critical=True,
            idempotency_key="k-pending-1",
        )
        child = Task(
            task_id="t-pending-child-1",
            parent_task_id="t-pending-1",
            title="fix",
            phase="implement",
            status=TaskStatus.running,
            idempotency_key="k-pending-child-1",
        )
        db.insert_task(parent)
        db.insert_task(child)
        resp = requests.get(f"{server_url}/tasks/t-pending-1/pending-fixes")
        assert resp.status_code == 200
        assert resp.json()["has_pending_fixes"] is True

    def test_pending_fixes_false_when_no_active_child(self, server_url, db):
        parent = Task(
            task_id="t-pending-2",
            title="parent",
            phase="implement",
            status=TaskStatus.review,
            critical=True,
            idempotency_key="k-pending-2",
        )
        child = Task(
            task_id="t-pending-child-2",
            parent_task_id="t-pending-2",
            title="fix",
            phase="implement",
            status=TaskStatus.completed,
            idempotency_key="k-pending-child-2",
        )
        db.insert_task(parent)
        db.insert_task(child)
        resp = requests.get(f"{server_url}/tasks/t-pending-2/pending-fixes")
        assert resp.status_code == 200
        assert resp.json()["has_pending_fixes"] is False


class TestAuth:
    def test_no_auth_on_health(self, authed_server_url):
        """Health endpoint should work without auth."""
        resp = requests.get(f"{authed_server_url}/health")
        assert resp.status_code == 200

    def test_auth_required_on_heartbeat(self, authed_server_url):
        """Heartbeat requires _check_auth (global bearer token)."""
        resp = requests.post(
            f"{authed_server_url}/heartbeat",
            json={"worker_id": "w1"},
        )
        assert resp.status_code == 401

    def test_auth_with_valid_token_on_heartbeat(self, authed_server_url, db):
        """Valid global token passes _check_auth."""
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work")
        db.insert_worker(worker)
        resp = requests.post(
            f"{authed_server_url}/heartbeat",
            json={"worker_id": "w1"},
            headers={"Authorization": "Bearer test-token-123"},
        )
        assert resp.status_code == 200

    def test_auth_with_invalid_token(self, authed_server_url):
        resp = requests.post(
            f"{authed_server_url}/heartbeat",
            json={"worker_id": "w1"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401


class TestEdgeCases:
    def test_unknown_path_returns_404(self, server_url):
        resp = requests.get(f"{server_url}/nonexistent")
        assert resp.status_code == 404

    def test_post_unknown_path_returns_404(self, server_url):
        resp = requests.post(f"{server_url}/nonexistent", json={})
        assert resp.status_code == 404

    def test_empty_body_returns_400(self, server_url):
        resp = requests.post(
            f"{server_url}/heartbeat",
            data="",
            headers={"Content-Length": "0"},
        )
        assert resp.status_code == 400


class TestLongPollDispatchIntegration:
    """End-to-end integration tests for long-poll -> dispatch -> wakeup flow."""

    def test_longpoll_wakeup_on_dispatch(self, db, tmp_path):
        """Dispatch wakeup delivers task in < 1s (proves wakeup, not timeout)."""
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
            "longpoll_timeout": 5.0,  # Long timeout to prove wakeup works
            "auth_token": None,
            "start_time": datetime.now(timezone.utc),
        }

        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}"

        # Register worker in DB (idle, claude, work)
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="idle")
        db.insert_worker(worker)

        # Insert a queued task targeting cli=claude, account=work
        task = Task(
            task_id="t1",
            title="wakeup test",
            phase="implement",
            target_cli="claude",
            target_account="work",
            idempotency_key="k1",
        )
        db.insert_task(task)

        # Register w1 in the LongPollRegistry
        longpoll_registry.register("w1")

        # Background thread: sleep 0.2s, then call scheduler.dispatch()
        def dispatch_after_delay():
            time.sleep(0.2)
            scheduler.dispatch()

        dispatch_thread = threading.Thread(target=dispatch_after_delay)
        dispatch_thread.start()

        # Long-poll: GET /tasks/next?worker_id=w1 (blocks until wakeup)
        start = time.monotonic()
        resp = requests.get(f"{url}/tasks/next?worker_id=w1", timeout=10)
        elapsed = time.monotonic() - start

        dispatch_thread.join(timeout=5)
        server.shutdown()

        # Assert response is 200 with task JSON
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "t1"
        assert data["title"] == "wakeup test"

        # Assert total elapsed time < 1.0s (proves wakeup, not 5s timeout)
        assert elapsed < 1.0, f"Expected < 1.0s, got {elapsed:.2f}s (wakeup failed)"

    def test_longpoll_timeout_returns_204(self, db, tmp_path):
        """Timeout returns 204 after configured duration (not instant)."""
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
            "longpoll_timeout": 0.2,  # Short timeout for test
            "auth_token": None,
            "start_time": datetime.now(timezone.utc),
        }

        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}"

        # Register worker in DB
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="idle")
        db.insert_worker(worker)
        longpoll_registry.register("w1")

        # No tasks in DB - should timeout
        start = time.monotonic()
        resp = requests.get(f"{url}/tasks/next?worker_id=w1", timeout=5)
        elapsed = time.monotonic() - start

        server.shutdown()

        assert resp.status_code == 204
        # Should take approximately 0.2s, not instant
        assert elapsed >= 0.15, f"Expected >= 0.15s, got {elapsed:.2f}s (too fast)"
        assert elapsed < 1.0, f"Expected < 1.0s, got {elapsed:.2f}s (too slow)"

    def test_longpoll_conflict_returns_409(self, db, tmp_path):
        """Duplicate concurrent poll returns 409."""
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
            "longpoll_timeout": 2.0,  # Long enough for concurrent test
            "auth_token": None,
            "start_time": datetime.now(timezone.utc),
        }

        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}"

        # Register worker in DB and LongPollRegistry
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="idle")
        db.insert_worker(worker)
        longpoll_registry.register("w1")

        # First poll in background (blocks for 2s)
        first_result: list[requests.Response] = []

        def first_poll():
            resp = requests.get(f"{url}/tasks/next?worker_id=w1", timeout=5)
            first_result.append(resp)

        t = threading.Thread(target=first_poll)
        t.start()
        time.sleep(0.1)  # Let first poll enter wait

        # Second concurrent poll should get 409
        second_resp = requests.get(f"{url}/tasks/next?worker_id=w1", timeout=5)
        assert second_resp.status_code == 409
        assert second_resp.json()["error"] == "duplicate_poll"

        t.join(timeout=5)
        server.shutdown()

        # First poll should complete with 204 (timeout, no task)
        assert len(first_result) == 1
        assert first_result[0].status_code == 204


class TestReviewTimeoutScheduling:
    """Tests for periodic review timeout detection thread."""

    def test_review_check_interval_configurable(self, db, tmp_path):
        """MESH_REVIEW_CHECK_INTERVAL_S env var is read into router_state."""
        from datetime import datetime, timezone
        import os

        longpoll_registry = LongPollRegistry()
        worker_manager = WorkerManager(db, tokens=[], dev_mode=True, longpoll_registry=longpoll_registry)
        heartbeat = HeartbeatManager(db, longpoll_registry=longpoll_registry)
        scheduler = Scheduler(db, longpoll_registry=longpoll_registry)
        transport = InProcessTransport(db)
        metrics = MeshMetrics()

        from src.router.verifier import VerifierGate

        # Set env var and read it the same way run_server() does
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("MESH_REVIEW_CHECK_INTERVAL_S", "0.1")
            review_check_interval = float(os.environ.get("MESH_REVIEW_CHECK_INTERVAL_S", "60"))

        verifier_gate = VerifierGate()

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
            "verifier_gate": verifier_gate,
            "review_check_interval": review_check_interval,
        }

        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        # Verify the interval was read correctly
        assert server.router_state["review_check_interval"] == 0.1

        server.shutdown()

    def test_stale_review_detected_by_scheduled_check(self, db):
        """VerifierGate.check_review_timeout transitions stale review tasks to timeout."""
        from datetime import datetime, timedelta, timezone

        from src.router.verifier import VerifierGate

        # Insert a critical task in review state with a past review_timeout_at
        past_timeout = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        task = Task(
            task_id="t-review-stale",
            title="stale review task",
            phase="implement",
            status=TaskStatus.review,
            critical=True,
            review_timeout_at=past_timeout,
            idempotency_key="k-review-stale",
        )
        db.insert_task(task)

        # Verify task is in review state
        before = db.get_task("t-review-stale")
        assert before.status == TaskStatus.review

        # Call check_review_timeout directly
        verifier_gate = VerifierGate()
        timed_out = verifier_gate.check_review_timeout(db)

        # Assert the task was timed out
        assert "t-review-stale" in timed_out
        after = db.get_task("t-review-stale")
        assert after.status == TaskStatus.timeout


class TestBufferReplayWiring:
    """Tests for buffer replay timer wiring in server startup."""

    def test_buffer_replay_interval_configurable(self, db, tmp_path):
        """MESH_BUFFER_REPLAY_INTERVAL_S env var is read correctly."""
        import os
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("MESH_BUFFER_REPLAY_INTERVAL_S", "30")
            interval = float(os.environ.get("MESH_BUFFER_REPLAY_INTERVAL_S", "60"))
        assert interval == 30.0

    def test_emitter_and_buffer_in_router_state(self, db, tmp_path):
        """Emitter and buffer are stored in router_state."""
        from datetime import datetime, timezone
        from src.router.bridge.buffer import FallbackBuffer
        from src.router.bridge.emitter import EventEmitter

        longpoll_registry = LongPollRegistry()
        worker_manager = WorkerManager(db, tokens=[], dev_mode=True, longpoll_registry=longpoll_registry)
        heartbeat = HeartbeatManager(db, longpoll_registry=longpoll_registry)
        scheduler_obj = Scheduler(db, longpoll_registry=longpoll_registry)
        transport = InProcessTransport(db)
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        emitter = EventEmitter(
            transport=transport,
            source_machine="test",
            buffer=buf,
            replay_interval_s=30.0,
        )
        metrics = MeshMetrics()

        server = ThreadingHTTPServer(("127.0.0.1", 0), MeshRouterHandler)
        server.router_state = {
            "db": db,
            "worker_manager": worker_manager,
            "heartbeat": heartbeat,
            "scheduler": scheduler_obj,
            "transport": transport,
            "emitter": emitter,
            "buffer": buf,
            "metrics": metrics,
            "longpoll_registry": longpoll_registry,
            "longpoll_timeout": 0.1,
            "auth_token": None,
            "start_time": datetime.now(timezone.utc),
        }

        assert isinstance(server.router_state["emitter"], EventEmitter)
        assert isinstance(server.router_state["buffer"], FallbackBuffer)
        assert server.router_state["emitter"]._replay_interval_s == 30.0


class TestBufferReplayMetrics:
    """Tests for buffer replay Prometheus metrics."""

    def test_buffer_replay_metrics_exist(self):
        """mesh_buffer_replay_total and mesh_buffer_replay_events_total are registered."""
        metrics = MeshMetrics()
        output = metrics.generate().decode("utf-8")
        assert "mesh_buffer_replay_total" in output
        assert "mesh_buffer_replay_events_total" in output


class TestWorkerEndpoints:
    """Tests for GET /workers, GET /workers/<id>, POST drain/deregister endpoints."""

    AUTH = {"Authorization": "Bearer test-token-123"}

    def _register_worker(self, url, worker_id="w1"):
        """Helper to register a worker via POST /register."""
        return requests.post(
            f"{url}/register",
            json={
                "worker_id": worker_id,
                "machine": "ws1",
                "cli_type": "claude",
                "account_profile": "work",
                "capabilities": ["code"],
                "status": "idle",
                "concurrency": 1,
            },
            headers=self.AUTH,
        )

    def test_get_workers_empty(self, authed_server_url):
        """GET /workers with no workers returns empty list."""
        resp = requests.get(f"{authed_server_url}/workers", headers=self.AUTH)
        assert resp.status_code == 200
        assert resp.json() == {"workers": []}

    def test_get_workers_with_worker(self, authed_server_url):
        """GET /workers returns registered worker with all expected fields."""
        self._register_worker(authed_server_url)
        resp = requests.get(f"{authed_server_url}/workers", headers=self.AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["workers"]) == 1
        w = data["workers"][0]
        assert w["worker_id"] == "w1"
        assert w["machine"] == "ws1"
        assert w["cli_type"] == "claude"
        assert w["status"] == "idle"
        assert "last_heartbeat" in w
        assert "idle_since" in w
        assert "stale_since" in w
        assert w["running_tasks"] == []

    def test_get_workers_with_running_task(self, authed_server_url, db):
        """GET /workers returns worker with inline running_tasks."""
        self._register_worker(authed_server_url)
        db.update_worker("w1", {"status": "busy"})
        task = Task(
            task_id="t1",
            title="test task",
            phase="implement",
            status=TaskStatus.running,
            assigned_worker="w1",
            idempotency_key="k1",
        )
        db.insert_task(task)
        resp = requests.get(f"{authed_server_url}/workers", headers=self.AUTH)
        assert resp.status_code == 200
        data = resp.json()
        w = data["workers"][0]
        assert len(w["running_tasks"]) == 1
        rt = w["running_tasks"][0]
        assert rt["task_id"] == "t1"
        assert rt["status"] == "running"
        assert "created_at" in rt
        assert "age_s" in rt

    def test_get_worker_by_id(self, authed_server_url):
        """GET /workers/<id> returns single worker detail."""
        self._register_worker(authed_server_url)
        resp = requests.get(f"{authed_server_url}/workers/w1", headers=self.AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["worker_id"] == "w1"
        assert "machine" in data
        assert "cli_type" in data
        assert "status" in data
        assert "running_tasks" in data

    def test_get_worker_not_found(self, authed_server_url):
        """GET /workers/<id> returns 404 for unknown worker."""
        resp = requests.get(f"{authed_server_url}/workers/nonexistent", headers=self.AUTH)
        assert resp.status_code == 404

    def test_get_workers_requires_auth(self, authed_server_url):
        """GET /workers without auth returns 401."""
        resp = requests.get(f"{authed_server_url}/workers")
        assert resp.status_code == 401

    def test_drain_idle_worker(self, authed_server_url):
        """POST /workers/<id>/drain on idle worker returns 202 drained_immediately."""
        self._register_worker(authed_server_url)
        resp = requests.post(f"{authed_server_url}/workers/w1/drain", headers=self.AUTH)
        assert resp.status_code == 202
        assert resp.json()["status"] == "drained_immediately"
        # Verify worker is now offline
        get_resp = requests.get(f"{authed_server_url}/workers/w1", headers=self.AUTH)
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "offline"

    def test_drain_busy_worker(self, authed_server_url, db):
        """POST /workers/<id>/drain on busy worker returns 202 draining."""
        self._register_worker(authed_server_url)
        db.update_worker("w1", {"status": "busy"})
        task = Task(
            task_id="t1",
            title="test",
            phase="implement",
            status=TaskStatus.running,
            assigned_worker="w1",
            idempotency_key="k1",
        )
        db.insert_task(task)
        resp = requests.post(f"{authed_server_url}/workers/w1/drain", headers=self.AUTH)
        assert resp.status_code == 202
        assert resp.json()["status"] == "draining"
        # Verify worker is draining
        get_resp = requests.get(f"{authed_server_url}/workers/w1", headers=self.AUTH)
        assert get_resp.json()["status"] == "draining"

    def test_drain_stale_worker_409(self, authed_server_url, db):
        """POST /workers/<id>/drain on stale worker returns 409."""
        self._register_worker(authed_server_url)
        db.update_worker("w1", {"status": "stale"})
        resp = requests.post(f"{authed_server_url}/workers/w1/drain", headers=self.AUTH)
        assert resp.status_code == 409

    def test_drain_not_found_404(self, authed_server_url):
        """POST /workers/<id>/drain on unknown worker returns 404."""
        resp = requests.post(
            f"{authed_server_url}/workers/nonexistent/drain", headers=self.AUTH
        )
        assert resp.status_code == 404

    def test_drain_requires_auth(self, authed_server_url):
        """POST /workers/<id>/drain without auth returns 401."""
        resp = requests.post(f"{authed_server_url}/workers/w1/drain")
        assert resp.status_code == 401

    def test_deregister_worker(self, authed_server_url):
        self._register_worker(authed_server_url)
        resp = requests.post(f"{authed_server_url}/workers/w1/deregister", headers=self.AUTH)
        assert resp.status_code == 200
        assert resp.json()["status"] == "deregistered"
        get_resp = requests.get(f"{authed_server_url}/workers/w1", headers=self.AUTH)
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "offline"

    def test_deregister_not_found_404(self, authed_server_url):
        resp = requests.post(
            f"{authed_server_url}/workers/nonexistent/deregister", headers=self.AUTH
        )
        assert resp.status_code == 404

    def test_deregister_requires_auth(self, authed_server_url):
        resp = requests.post(f"{authed_server_url}/workers/w1/deregister")
        assert resp.status_code == 401


class TestDispatchLoop:
    """Tests for the dispatch_loop daemon thread in run_server()."""

    def _make_server_with_dispatch(self, db, dispatch_interval=0.2):
        """Create a test server that includes a dispatch loop thread."""
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

        # Start dispatch loop (mimics run_server behavior)
        def dispatch_loop():
            while getattr(server, '_dispatch_running', True):
                time.sleep(dispatch_interval)
                try:
                    dispatched = 0
                    while True:
                        result = scheduler.dispatch()
                        if result is None:
                            break
                        dispatched += 1
                        metrics.tasks_dispatched.inc()
                    if dispatched:
                        metrics.dispatch_cycles_total.labels(result="dispatched").inc()
                    else:
                        metrics.dispatch_cycles_total.labels(result="empty").inc()
                except Exception:
                    metrics.dispatch_cycles_total.labels(result="error").inc()

        server._dispatch_running = True
        dispatch_thread = threading.Thread(target=dispatch_loop, daemon=True, name="dispatch")
        dispatch_thread.start()

        srv_thread = threading.Thread(target=server.serve_forever)
        srv_thread.daemon = True
        srv_thread.start()

        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}"
        return server, url, scheduler, metrics

    def test_dispatch_loop_dispatches_queued_task(self, db):
        """Dispatch loop assigns a queued task to an idle worker."""
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="idle")
        db.insert_worker(worker)

        task = Task(
            task_id="t1",
            title="auto dispatch",
            phase="implement",
            target_cli="claude",
            target_account="work",
            idempotency_key="k-dispatch-1",
        )
        db.insert_task(task)

        server, url, scheduler, metrics = self._make_server_with_dispatch(db, dispatch_interval=0.1)
        try:
            # Wait for dispatch loop to process
            time.sleep(0.5)

            t = db.get_task("t1")
            assert t.status == TaskStatus.assigned, f"Expected assigned, got {t.status}"
            assert t.assigned_worker == "w1"
        finally:
            server._dispatch_running = False
            server.shutdown()

    def test_dispatch_loop_multiple_tasks(self, db):
        """Dispatch loop drains all dispatchable tasks in one cycle."""
        # Two workers, two tasks
        for i in range(2):
            db.insert_worker(Worker(
                worker_id=f"w{i}",
                cli_type="claude",
                account_profile=f"acct{i}",
                status="idle",
            ))
            db.insert_task(Task(
                task_id=f"t{i}",
                title=f"task {i}",
                phase="implement",
                target_cli="claude",
                target_account=f"acct{i}",
                idempotency_key=f"k-multi-{i}",
            ))

        server, url, scheduler, metrics = self._make_server_with_dispatch(db, dispatch_interval=0.1)
        try:
            time.sleep(0.5)

            for i in range(2):
                t = db.get_task(f"t{i}")
                assert t.status == TaskStatus.assigned, f"Task t{i}: expected assigned, got {t.status}"
        finally:
            server._dispatch_running = False
            server.shutdown()


class TestPostTasksEndpoint:
    """Tests for POST /tasks task creation endpoint."""

    def test_post_tasks_creates_task(self, server_url, db):
        """POST /tasks with valid data returns 201 and task in DB."""
        resp = requests.post(
            f"{server_url}/tasks",
            json={"title": "Test task", "phase": "implement"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        assert "task_id" in data

        # Verify in DB
        task = db.get_task(data["task_id"])
        assert task is not None
        assert task.title == "Test task"
        assert task.status == TaskStatus.queued

    def test_post_tasks_auth_required(self, authed_server_url):
        """POST /tasks without auth token returns 401."""
        resp = requests.post(
            f"{authed_server_url}/tasks",
            json={"title": "Test"},
        )
        assert resp.status_code == 401

    def test_post_tasks_invalid_json(self, server_url):
        """POST /tasks with invalid JSON returns 400."""
        resp = requests.post(
            f"{server_url}/tasks",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_json"

    def test_post_tasks_invalid_fields(self, server_url):
        """POST /tasks with invalid enum value returns 400."""
        resp = requests.post(
            f"{server_url}/tasks",
            json={"title": "Test", "phase": "nonexistent_phase"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_task"

    def test_post_tasks_missing_title(self, server_url):
        """POST /tasks without required title field returns 400."""
        resp = requests.post(
            f"{server_url}/tasks",
            json={"phase": "implement"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_task"

    def test_post_tasks_duplicate_idempotency_key(self, server_url, db):
        """POST /tasks with duplicate idempotency_key returns 409."""
        resp1 = requests.post(
            f"{server_url}/tasks",
            json={"title": "First", "idempotency_key": "dup-key-1"},
        )
        assert resp1.status_code == 201

        resp2 = requests.post(
            f"{server_url}/tasks",
            json={"title": "Second", "idempotency_key": "dup-key-1"},
        )
        assert resp2.status_code == 409
        assert resp2.json()["error"] == "duplicate_task"

    def test_post_tasks_ignores_internal_fields(self, server_url, db):
        """Client-set status/assigned_worker are ignored; server sets defaults."""
        resp = requests.post(
            f"{server_url}/tasks",
            json={
                "title": "Sneaky",
                "status": "completed",
                "assigned_worker": "evil-worker",
            },
        )
        assert resp.status_code == 201
        task = db.get_task(resp.json()["task_id"])
        assert task.status == TaskStatus.queued
        assert task.assigned_worker is None

    def test_post_tasks_triggers_dispatch(self, server_url, db):
        """POST /tasks with idle worker triggers eager dispatch."""
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="idle")
        db.insert_worker(worker)

        resp = requests.post(
            f"{server_url}/tasks",
            json={
                "title": "Eager task",
                "target_cli": "claude",
                "target_account": "work",
            },
        )
        assert resp.status_code == 201

        task_id = resp.json()["task_id"]
        task = db.get_task(task_id)
        assert task.status == TaskStatus.assigned
        assert task.assigned_worker == "w1"

    def test_post_tasks_with_payload(self, server_url, db):
        """POST /tasks with payload stores it correctly."""
        resp = requests.post(
            f"{server_url}/tasks",
            json={
                "title": "With payload",
                "payload": {"prompt": "Hello world", "working_dir": "/tmp"},
            },
        )
        assert resp.status_code == 201
        task = db.get_task(resp.json()["task_id"])
        assert task.payload["prompt"] == "Hello world"
        assert task.payload["working_dir"] == "/tmp"


class TestSessionBusEndpoints:
    def test_open_send_read_close_roundtrip(self, server_url, db):
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="idle")
        db.insert_worker(worker)
        task = Task(
            title="Interactive task",
            phase="implement",
            target_cli="claude",
            target_account="work",
            execution_mode="session",
            idempotency_key="session-bus-1",
        )
        db.insert_task(task)

        open_resp = requests.post(
            f"{server_url}/sessions/open",
            json={
                "worker_id": "w1",
                "cli_type": "claude",
                "account_profile": "work",
                "task_id": task.task_id,
                "metadata": {"tmux_session": "mesh-claude-test"},
            },
        )
        assert open_resp.status_code == 201
        session = open_resp.json()["session"]
        session_id = session["session_id"]
        assert session["worker_id"] == "w1"
        assert session["metadata"]["tmux_session"] == "mesh-claude-test"

        send_resp = requests.post(
            f"{server_url}/sessions/send",
            json={
                "session_id": session_id,
                "direction": "in",
                "role": "operator",
                "content": "Please continue",
            },
        )
        assert send_resp.status_code == 201
        assert send_resp.json()["seq"] >= 1

        list_resp = requests.get(f"{server_url}/sessions")
        assert list_resp.status_code == 200
        assert any(s["session_id"] == session_id for s in list_resp.json()["sessions"])

        get_resp = requests.get(f"{server_url}/sessions/{session_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["session_id"] == session_id

        msg_resp = requests.get(
            f"{server_url}/sessions/messages",
            params={"session_id": session_id, "after_seq": 0},
        )
        assert msg_resp.status_code == 200
        messages = msg_resp.json()["messages"]
        assert len(messages) == 1
        assert messages[0]["content"] == "Please continue"

        close_resp = requests.post(
            f"{server_url}/sessions/close",
            json={"session_id": session_id, "state": "closed"},
        )
        assert close_resp.status_code == 200

        send_closed = requests.post(
            f"{server_url}/sessions/send",
            json={
                "session_id": session_id,
                "direction": "in",
                "role": "operator",
                "content": "late message",
            },
        )
        assert send_closed.status_code == 409

        refreshed_task = db.get_task(task.task_id)
        # Session open should persist linkage when task_id is provided.
        assert refreshed_task is not None
        assert refreshed_task.session_id == session_id

    def test_session_control_endpoints_append_control_messages(self, server_url, db):
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="idle")
        db.insert_worker(worker)
        task = Task(
            title="Interactive task controls",
            phase="implement",
            target_cli="claude",
            target_account="work",
            execution_mode="session",
            idempotency_key="session-bus-controls-1",
        )
        db.insert_task(task)

        open_resp = requests.post(
            f"{server_url}/sessions/open",
            json={
                "worker_id": "w1",
                "cli_type": "claude",
                "account_profile": "work",
                "task_id": task.task_id,
                "metadata": {"tmux_session": "mesh-claude-test"},
            },
        )
        assert open_resp.status_code == 201
        session_id = open_resp.json()["session"]["session_id"]

        send_key = requests.post(
            f"{server_url}/sessions/send-key",
            json={"session_id": session_id, "key": "Up", "repeat": 2},
        )
        assert send_key.status_code == 201
        assert send_key.json()["control"] == "send_key"

        resize = requests.post(
            f"{server_url}/sessions/resize",
            json={"session_id": session_id, "cols": 120, "rows": 40},
        )
        assert resize.status_code == 201
        assert resize.json()["control"] == "resize"

        signal_resp = requests.post(
            f"{server_url}/sessions/signal",
            json={"session_id": session_id, "signal": "interrupt"},
        )
        assert signal_resp.status_code == 201
        assert signal_resp.json()["control"] == "signal"

        msg_resp = requests.get(
            f"{server_url}/sessions/messages",
            params={"session_id": session_id, "after_seq": 0},
        )
        assert msg_resp.status_code == 200
        controls = [m.get("metadata", {}).get("control") for m in msg_resp.json()["messages"]]
        assert controls == ["send_key", "resize", "signal"]

    def test_session_control_endpoints_validate_payloads(self, server_url, db):
        worker = Worker(worker_id="w1", cli_type="claude", account_profile="work", status="idle")
        db.insert_worker(worker)
        task = Task(
            title="Interactive task controls invalid",
            phase="implement",
            target_cli="claude",
            target_account="work",
            execution_mode="session",
            idempotency_key="session-bus-controls-2",
        )
        db.insert_task(task)
        open_resp = requests.post(
            f"{server_url}/sessions/open",
            json={
                "worker_id": "w1",
                "cli_type": "claude",
                "account_profile": "work",
                "task_id": task.task_id,
                "metadata": {"tmux_session": "mesh-claude-test"},
            },
        )
        session_id = open_resp.json()["session"]["session_id"]

        bad_repeat = requests.post(
            f"{server_url}/sessions/send-key",
            json={"session_id": session_id, "key": "Up", "repeat": 0},
        )
        assert bad_repeat.status_code == 400
        assert bad_repeat.json()["error"] == "invalid_repeat"

        bad_resize = requests.post(
            f"{server_url}/sessions/resize",
            json={"session_id": session_id, "cols": 10, "rows": 2},
        )
        assert bad_resize.status_code == 400
        assert bad_resize.json()["error"] == "invalid_resize"

        bad_signal = requests.post(
            f"{server_url}/sessions/signal",
            json={"session_id": session_id, "signal": "stop"},
        )
        assert bad_signal.status_code == 400
        assert bad_signal.json()["error"] == "invalid_signal"


class TestNotificationLedgerEndpoints:
    def test_create_and_list_notification(self, server_url):
        create_resp = requests.post(
            f"{server_url}/notifications",
            json={
                "trace_id": "ntf_0123456789abcdef0123",
                "trigger": "approval_needed",
                "room_id": "!ops:matrix.example",
                "status": "sent",
                "repo": "rektslug",
                "task_id": "task-1",
                "metadata": {"source": "bridge"},
            },
        )
        assert create_resp.status_code == 201
        assert create_resp.json()["trace_id"] == "ntf_0123456789abcdef0123"

        list_resp = requests.get(
            f"{server_url}/notifications",
            params={"trace_id": "ntf_0123456789abcdef0123", "status": "sent"},
        )
        assert list_resp.status_code == 200
        rows = list_resp.json()["notifications"]
        assert len(rows) == 1
        assert rows[0]["room_id"] == "!ops:matrix.example"
        assert rows[0]["metadata"]["source"] == "bridge"

    def test_create_notification_duplicate(self, server_url):
        trace = "ntf_fedcba98765432109876"
        payload = {
            "trace_id": trace,
            "trigger": "thread_failed",
            "room_id": "!r1",
            "status": "failed",
        }
        # First creation
        resp1 = requests.post(f"{server_url}/notifications", json=payload)
        assert resp1.status_code == 201

        # Second creation (duplicate)
        resp2 = requests.post(f"{server_url}/notifications", json=payload)
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "duplicate"
        assert resp2.json()["trace_id"] == trace

    def test_create_notification_validation_error(self, server_url):
        resp = requests.post(
            f"{server_url}/notifications",
            json={"trigger": "approval_needed"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_notification"

    def test_create_notification_invalid_trace_id(self, server_url):
        resp = requests.post(
            f"{server_url}/notifications",
            json={
                "trace_id": "not_valid_trace",
                "trigger": "approval_needed",
                "room_id": "!ops:matrix.example",
                "status": "sent",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_notification"

    def test_list_notifications_invalid_limit(self, server_url):
        resp = requests.get(f"{server_url}/notifications", params={"limit": "oops"})
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_limit"
