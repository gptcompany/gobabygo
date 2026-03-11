"""Additional tests for mesh router server to increase coverage."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

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
    db_path = str(tmp_path / "test_router_coverage.db")
    db = RouterDB(db_path, check_same_thread=False)
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def server_url(db):
    """Start a test HTTP server in dev mode (no auth required for register)."""
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


class TestServerCoverage:
    def test_get_unknown_path(self, server_url):
        resp = requests.get(f"{server_url}/unknown")
        assert resp.status_code == 404

    def test_post_unknown_path(self, server_url):
        resp = requests.post(f"{server_url}/unknown", json={})
        assert resp.status_code == 404

    def test_health_check(self, server_url):
        resp = requests.get(f"{server_url}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_metrics_endpoint(self, server_url):
        resp = requests.get(f"{server_url}/metrics")
        assert resp.status_code == 200
        assert "mesh_tasks_created_total" in resp.text

    def test_list_tasks_invalid_limit(self, server_url):
        resp = requests.get(f"{server_url}/tasks", params={"limit": "abc"})
        assert resp.status_code == 400

    def test_get_task_not_found(self, server_url):
        resp = requests.get(f"{server_url}/tasks/non-existent")
        assert resp.status_code == 404

    def test_task_poll_missing_worker_id(self, server_url):
        resp = requests.get(f"{server_url}/tasks/next")
        assert resp.status_code == 400

    def test_create_task_invalid_json(self, server_url):
        resp = requests.post(f"{server_url}/tasks", data="not json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 400

    def test_create_task_empty_body(self, server_url):
        resp = requests.post(f"{server_url}/tasks", data="", headers={"Content-Type": "application/json"})
        assert resp.status_code == 400

    def test_create_task_too_large(self, server_url):
        large_body = "x" * 1000001
        resp = requests.post(f"{server_url}/tasks", data=large_body, headers={"Content-Type": "application/json"})
        assert resp.status_code == 413

    def test_open_session_invalid_data(self, server_url):
        resp = requests.post(f"{server_url}/sessions/open", json={"invalid": "field"})
        assert resp.status_code == 400

    def test_send_session_message_not_found(self, server_url):
        resp = requests.post(f"{server_url}/sessions/send", json={
            "session_id": "none",
            "direction": "in",
            "role": "operator",
            "content": "hi"
        })
        assert resp.status_code == 404

    def test_send_session_key_missing_fields(self, server_url):
        resp = requests.post(f"{server_url}/sessions/send-key", json={"session_id": "s1"})
        assert resp.status_code == 400

    def test_resize_session_invalid_dims(self, server_url):
        resp = requests.post(f"{server_url}/sessions/resize", json={"session_id": "s1", "cols": 1, "rows": 1})
        assert resp.status_code == 400

    def test_signal_session_invalid_signal(self, server_url):
        resp = requests.post(f"{server_url}/sessions/signal", json={"session_id": "s1", "signal": "kill"})
        assert resp.status_code == 400

    def test_close_session_not_found(self, server_url):
        resp = requests.post(f"{server_url}/sessions/close", json={"session_id": "s1", "state": "closed"})
        assert resp.status_code == 404

    def test_drain_worker_not_found(self, server_url):
        resp = requests.post(f"{server_url}/workers/w-none/drain")
        assert resp.status_code == 404

    def test_deregister_worker_not_found(self, server_url):
        resp = requests.post(f"{server_url}/workers/w-none/deregister")
        assert resp.status_code == 404

    def test_create_notification_invalid(self, server_url):
        resp = requests.post(f"{server_url}/notifications", json={"invalid": "data"})
        assert resp.status_code == 400

    def test_list_notifications_invalid_limit(self, server_url):
        resp = requests.get(f"{server_url}/notifications", params={"limit": "abc"})
        assert resp.status_code == 400

    def test_enforce_session_only(self, db):
        # We need a server with enforce_session_only=True
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
            "enforce_session_only": True,
        }
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"

        resp = requests.post(f"{url}/tasks", json={
            "phase": "test",
            "title": "test",
            "payload": {},
            "target_cli": "claude",
            "target_account": "work",
            "execution_mode": "batch"
        })
        assert resp.status_code == 400
        assert "session_only_mode" in resp.text

        server.shutdown()
