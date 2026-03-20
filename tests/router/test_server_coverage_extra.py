"""Additional tests for mesh router server to increase coverage further."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
import pytest
import requests
from unittest.mock import MagicMock, patch

from src.router.db import RouterDB
from src.router.heartbeat import HeartbeatManager
from src.router.longpoll import LongPollRegistry
from src.router.metrics import MeshMetrics
from src.router.models import Task, TaskCreateRequest, TaskStatus, Worker, ThreadCreateRequest, ThreadStepRequest
from src.router.scheduler import Scheduler
from src.router.server import MeshRouterHandler, build_mesh_http_server
from src.router.worker_manager import WorkerManager
from src.router.bridge.transport import InProcessTransport


@pytest.fixture
def db(tmp_path):
    """Create a fresh in-memory-like DB for each test."""
    db_path = str(tmp_path / "test_router_coverage_extra.db")
    db = RouterDB(db_path, check_same_thread=False)
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def server_url(db):
    """Start a test HTTP server in dev mode."""
    longpoll_registry = LongPollRegistry()
    worker_manager = WorkerManager(db, tokens=[], dev_mode=True, longpoll_registry=longpoll_registry)
    heartbeat = HeartbeatManager(db, longpoll_registry=longpoll_registry)
    scheduler = Scheduler(db, longpoll_registry=longpoll_registry)
    transport = InProcessTransport(db)
    metrics = MeshMetrics()

    server = build_mesh_http_server("127.0.0.1", 0, request_queue_size=32)
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


class TestServerCoverageExtra:
    def test_build_mesh_http_server_uses_custom_request_queue_size(self):
        server = build_mesh_http_server("127.0.0.1", 0, request_queue_size=64)
        try:
            assert server.request_queue_size == 64
            assert server.daemon_threads is True
        finally:
            server.server_close()

    def test_handle_events_valid(self, server_url):
        resp = requests.post(f"{server_url}/events", json={"id": "e1", "type": "test"})
        assert resp.status_code == 202

    def test_handle_events_invalid_json(self, server_url):
        resp = requests.post(f"{server_url}/events", data="not json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 400

    def test_handle_heartbeat_missing_worker_id(self, server_url):
        resp = requests.post(f"{server_url}/heartbeat", json={})
        assert resp.status_code == 400

    def test_handle_register_invalid_worker(self, server_url):
        resp = requests.post(f"{server_url}/register", json={"cli_type": "not-a-cli"})
        assert resp.status_code == 400

    def test_handle_task_ack_missing_fields(self, server_url):
        resp = requests.post(f"{server_url}/tasks/ack", json={"task_id": "t1"})
        assert resp.status_code == 400

    def test_handle_task_complete_invalid_result(self, server_url):
        resp = requests.post(f"{server_url}/tasks/complete", json={"task_id": "t1", "worker_id": "w1", "result": "not a dict"})
        assert resp.status_code == 400

    def test_handle_task_fail_missing_fields(self, server_url):
        resp = requests.post(f"{server_url}/tasks/fail", json={"task_id": "t1"})
        assert resp.status_code == 400

    def test_handle_task_cancel_not_found(self, server_url):
        resp = requests.post(f"{server_url}/tasks/cancel", json={"task_id": "none"})
        assert resp.status_code == 404

    def test_handle_task_admin_fail_not_found(self, server_url):
        resp = requests.post(f"{server_url}/tasks/admin-fail", json={"task_id": "none"})
        assert resp.status_code == 404

    def test_handle_cleanup_stale_state_dry_run(self, server_url):
        requests.post(f"{server_url}/register", json={
            "worker_id": "w1",
            "machine": "m1",
            "cli_type": "claude",
            "account_profile": "default",
        })
        task_resp = requests.post(f"{server_url}/tasks", json={"title": "t1"})
        task_id = task_resp.json()["task_id"]
        requests.post(f"{server_url}/sessions/open", json={
            "session_id": "s1",
            "worker_id": "w1",
            "cli_type": "claude",
            "task_id": task_id,
        })
        requests.post(
            f"{server_url}/tasks/admin-fail",
            json={"task_id": task_id, "reason": "cleanup_test"},
        )

        resp = requests.post(f"{server_url}/admin/cleanup-stale-state", json={"apply": False})

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "dry_run"
        assert body["updated_sessions"] == 0
        assert body["sessions"][0]["session_id"] == "s1"
        assert body["skipped_taskless_sessions"] == 0

    def test_handle_cleanup_stale_state_can_include_taskless_sessions(self, server_url):
        requests.post(f"{server_url}/register", json={
            "worker_id": "w1",
            "machine": "m1",
            "cli_type": "claude",
            "account_profile": "default",
        })
        requests.post(f"{server_url}/sessions/open", json={
            "session_id": "s-taskless",
            "worker_id": "w1",
            "cli_type": "claude",
        })

        resp = requests.post(
            f"{server_url}/admin/cleanup-stale-state",
            json={"apply": False, "include_taskless_sessions": True},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["sessions"][0]["session_id"] == "s-taskless"
        assert body["sessions"][0]["reason"] == "taskless_session"
        assert body["skipped_taskless_sessions"] == 0

    def test_handle_cleanup_stale_state_invalid_limit(self, server_url):
        resp = requests.post(
            f"{server_url}/admin/cleanup-stale-state",
            json={"apply": False, "session_limit": 0},
        )
        assert resp.status_code == 400

    def test_handle_task_review_approve_invalid_id(self, server_url):
        resp = requests.post(f"{server_url}/tasks/review/approve", json={"task_id": "t1", "verifier_id": " "})
        assert resp.status_code == 400

    def test_handle_task_review_reject_invalid_reason(self, server_url):
        resp = requests.post(f"{server_url}/tasks/review/reject", json={"task_id": "t1", "verifier_id": "v1", "reason": " "})
        assert resp.status_code == 400

    def test_handle_create_thread_invalid(self, server_url):
        resp = requests.post(f"{server_url}/threads", json={}) # missing name
        assert resp.status_code == 400

    def test_handle_add_step_missing_thread_id(self, server_url):
        resp = requests.post(f"{server_url}/threads//steps", json={})
        assert resp.status_code == 404

    def test_handle_add_step_invalid_payload(self, server_url):
        resp = requests.post(f"{server_url}/threads/t1/steps", json={"step_index": "abc"})
        assert resp.status_code == 400

    def test_handle_get_thread_not_found(self, server_url):
        resp = requests.get(f"{server_url}/threads/none")
        assert resp.status_code == 404

    def test_handle_thread_status_not_found(self, server_url):
        resp = requests.get(f"{server_url}/threads/none/status")
        assert resp.status_code == 404

    def test_handle_thread_context_not_found(self, server_url):
        resp = requests.get(f"{server_url}/threads/none/context")
        assert resp.status_code == 404

    def test_handle_list_threads_invalid_limit(self, server_url):
        resp = requests.get(f"{server_url}/threads", params={"limit": "abc"})
        assert resp.status_code == 400

    def test_handle_get_worker_not_found(self, server_url):
        resp = requests.get(f"{server_url}/workers/none")
        assert resp.status_code == 404

    def test_handle_task_pending_fixes_not_found(self, server_url):
        resp = requests.get(f"{server_url}/tasks/none/pending-fixes")
        assert resp.status_code == 404

    def test_handle_list_workers(self, server_url):
        resp = requests.get(f"{server_url}/workers")
        assert resp.status_code == 200
        assert "workers" in resp.json()

    def test_handle_get_worker_success(self, server_url):
        # Register a worker first
        reg_resp = requests.post(f"{server_url}/register", json={
            "worker_id": "w1",
            "machine": "m1",
            "cli_type": "claude",
            "account_profile": "default"
        })
        assert reg_resp.status_code == 201
        resp = requests.get(f"{server_url}/workers/w1")
        assert resp.status_code == 200
        assert resp.json()["worker_id"] == "w1"

    def test_handle_session_ops_success(self, server_url):
        # Register worker first
        requests.post(f"{server_url}/register", json={
            "worker_id": "w1",
            "machine": "m1",
            "cli_type": "claude",
            "account_profile": "default"
        })
        
        # Create a task to get a real task_id
        task_resp = requests.post(f"{server_url}/tasks", json={
            "title": "t1",
        })
        task_id = task_resp.json()["task_id"]

        # Open a session
        open_resp = requests.post(f"{server_url}/sessions/open", json={
            "session_id": "s1",
            "worker_id": "w1",
            "cli_type": "claude",
            "task_id": task_id
        })
        assert open_resp.status_code == 201
        session_id = open_resp.json()["session"]["session_id"]
        
        # Send key
        resp = requests.post(f"{server_url}/sessions/send-key", json={"session_id": session_id, "key": "Enter"})
        assert resp.status_code == 201
        
        # Resize
        resp = requests.post(f"{server_url}/sessions/resize", json={"session_id": session_id, "cols": 80, "rows": 24})
        assert resp.status_code == 201
        
        # Signal
        resp = requests.post(f"{server_url}/sessions/signal", json={"session_id": session_id, "signal": "interrupt"})
        assert resp.status_code == 201
        
        # Get messages
        resp = requests.get(f"{server_url}/sessions/messages", params={"session_id": session_id})
        assert resp.status_code == 200
        assert "messages" in resp.json()

    def test_handle_notifications_success(self, server_url):
        # Create notification
        resp = requests.post(f"{server_url}/notifications", json={
            "trace_id": "ntf_abc1234567890abcdef12345",
            "trigger": "thread_completed",
            "room_id": "r1",
            "status": "sent"
        })
        assert resp.status_code == 201
        
        # List notifications
        resp = requests.get(f"{server_url}/notifications")
        assert resp.status_code == 200
        assert len(resp.json()["notifications"]) >= 1

    def test_handle_task_review_ops_success(self, server_url, db):
        # Create a task
        resp = requests.post(f"{server_url}/tasks", json={
            "title": "review-me",
        })
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]
        
        # Manually move to review status in DB
        db.update_task_status(task_id, TaskStatus.queued, TaskStatus.review)
        
        # Approve
        resp = requests.post(f"{server_url}/tasks/review/approve", json={"task_id": task_id, "verifier_id": "v1"})
        assert resp.status_code == 200
        
        # Create another for reject
        resp = requests.post(f"{server_url}/tasks", json={
            "title": "reject-me",
        })
        task_id_reject = resp.json()["task_id"]
        db.update_task_status(task_id_reject, TaskStatus.queued, TaskStatus.review)
        # Reject
        resp = requests.post(f"{server_url}/tasks/review/reject", json={"task_id": task_id_reject, "verifier_id": "v1", "reason": "bad code"})
        assert resp.status_code == 200
