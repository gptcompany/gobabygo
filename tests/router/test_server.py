"""Tests for the mesh router HTTP server."""

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
from src.router.metrics import MeshMetrics
from src.router.models import Task, TaskStatus, Worker
from src.router.scheduler import Scheduler
from src.router.server import MeshRouterHandler
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
    """Start a test HTTP server and return its URL."""
    from datetime import datetime, timezone

    heartbeat = HeartbeatManager(db)
    scheduler = Scheduler(db)
    transport = InProcessTransport(db)
    metrics = MeshMetrics()

    server = ThreadingHTTPServer(("127.0.0.1", 0), MeshRouterHandler)
    server.router_state = {
        "db": db,
        "heartbeat": heartbeat,
        "scheduler": scheduler,
        "transport": transport,
        "metrics": metrics,
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
    """Start a test server with auth enabled."""
    from datetime import datetime, timezone

    heartbeat = HeartbeatManager(db)
    scheduler = Scheduler(db)
    transport = InProcessTransport(db)

    server = ThreadingHTTPServer(("127.0.0.1", 0), MeshRouterHandler)
    server.router_state = {
        "db": db,
        "heartbeat": heartbeat,
        "scheduler": scheduler,
        "transport": transport,
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


class TestRegisterEndpoint:
    def test_register_worker(self, server_url):
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
        assert resp.status_code == 201

    def test_register_invalid_json(self, server_url):
        resp = requests.post(
            f"{server_url}/register",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400


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


class TestAuth:
    def test_no_auth_on_health(self, authed_server_url):
        """Health endpoint should work without auth."""
        resp = requests.get(f"{authed_server_url}/health")
        assert resp.status_code == 200

    def test_auth_required_on_register(self, authed_server_url):
        resp = requests.post(
            f"{authed_server_url}/register",
            json={"worker_id": "w1", "cli_type": "claude", "account_profile": "work"},
        )
        assert resp.status_code == 401

    def test_auth_with_valid_token(self, authed_server_url):
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
