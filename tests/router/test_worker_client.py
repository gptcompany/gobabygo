"""Tests for the mesh worker client."""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from src.router.worker_client import MeshWorker, WorkerConfig


class MockRouterHandler(BaseHTTPRequestHandler):
    """Mock router server for testing worker client."""

    # Class-level tracking
    register_calls = []
    heartbeat_calls = []
    poll_count = 0
    complete_calls = []
    fail_calls = []
    task_to_serve = None

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}

        if self.path == "/register":
            MockRouterHandler.register_calls.append(body)
            self._respond(201, {"status": "registered", "worker_id": body.get("worker_id")})
        elif self.path == "/heartbeat":
            MockRouterHandler.heartbeat_calls.append(body)
            self._respond(200, {"status": "ok"})
        elif self.path == "/tasks/complete":
            MockRouterHandler.complete_calls.append(body)
            self._respond(200, {"status": "completed"})
        elif self.path == "/tasks/fail":
            MockRouterHandler.fail_calls.append(body)
            self._respond(200, {"status": "failed_recorded"})
        else:
            self._respond(404, {"error": "not_found"})

    def do_GET(self):
        if self.path.startswith("/tasks/next"):
            MockRouterHandler.poll_count += 1
            if MockRouterHandler.task_to_serve:
                task = MockRouterHandler.task_to_serve
                MockRouterHandler.task_to_serve = None  # Serve once
                self._respond(200, task)
            else:
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
        else:
            self._respond(404, {"error": "not_found"})

    def _respond(self, status, data):
        self.send_response(status)
        body = json.dumps(data).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # Suppress output


@pytest.fixture(autouse=True)
def reset_mock():
    """Reset mock state before each test."""
    MockRouterHandler.register_calls = []
    MockRouterHandler.heartbeat_calls = []
    MockRouterHandler.poll_count = 0
    MockRouterHandler.complete_calls = []
    MockRouterHandler.fail_calls = []
    MockRouterHandler.task_to_serve = None


@pytest.fixture
def mock_router():
    """Start a mock router server."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockRouterHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


class TestWorkerConfig:
    def test_defaults(self):
        config = WorkerConfig()
        assert config.worker_id == "ws-unknown-01"
        assert config.router_url == "http://localhost:8780"
        assert config.cli_type == "claude"
        assert config.heartbeat_interval == 5.0
        assert config.poll_interval == 2.0

    def test_from_env(self):
        env = {
            "MESH_WORKER_ID": "ws-test-01",
            "MESH_ROUTER_URL": "http://10.0.0.1:8780",
            "MESH_CLI_TYPE": "codex",
            "MESH_ACCOUNT_PROFILE": "clientA",
            "MESH_AUTH_TOKEN": "tok-123",
        }
        with patch.dict(os.environ, env):
            config = WorkerConfig.from_env()
            assert config.worker_id == "ws-test-01"
            assert config.router_url == "http://10.0.0.1:8780"
            assert config.cli_type == "codex"
            assert config.account_profile == "clientA"
            assert config.auth_token == "tok-123"


class TestMeshWorkerRegistration:
    def test_register_sends_correct_payload(self, mock_router):
        config = WorkerConfig(
            worker_id="ws-test-01",
            router_url=mock_router,
            cli_type="claude",
            account_profile="work",
        )
        worker = MeshWorker(config)
        worker._register()

        assert len(MockRouterHandler.register_calls) == 1
        call = MockRouterHandler.register_calls[0]
        assert call["worker_id"] == "ws-test-01"
        assert call["cli_type"] == "claude"
        assert call["account_profile"] == "work"
        assert call["status"] == "idle"

    def test_register_includes_auth_header(self, mock_router):
        config = WorkerConfig(
            worker_id="ws-test-01",
            router_url=mock_router,
            auth_token="secret-token",
        )
        worker = MeshWorker(config)
        worker._register()
        assert len(MockRouterHandler.register_calls) == 1


class TestMeshWorkerHeartbeat:
    def test_heartbeat_sends_periodically(self, mock_router):
        config = WorkerConfig(
            worker_id="ws-test-01",
            router_url=mock_router,
            heartbeat_interval=0.1,  # Fast for testing
        )
        worker = MeshWorker(config)
        worker._running = True
        worker._start_heartbeat()
        time.sleep(0.35)
        worker.stop()

        assert len(MockRouterHandler.heartbeat_calls) >= 2
        assert all(c["worker_id"] == "ws-test-01" for c in MockRouterHandler.heartbeat_calls)


class TestMeshWorkerPolling:
    def test_poll_handles_204_no_tasks(self, mock_router):
        config = WorkerConfig(
            worker_id="ws-test-01",
            router_url=mock_router,
            poll_interval=0.05,
        )
        worker = MeshWorker(config)
        worker._running = True

        # Run poll loop in background, stop after a few iterations
        thread = threading.Thread(target=worker._poll_loop)
        thread.daemon = True
        thread.start()
        time.sleep(0.2)
        worker._running = False
        thread.join(timeout=2)

        assert MockRouterHandler.poll_count >= 2

    def test_poll_executes_received_task(self, mock_router):
        MockRouterHandler.task_to_serve = {
            "task_id": "task-1",
            "title": "test task",
            "phase": "implement",
        }

        config = WorkerConfig(
            worker_id="ws-test-01",
            router_url=mock_router,
            poll_interval=0.05,
        )
        worker = MeshWorker(config)
        worker._running = True

        thread = threading.Thread(target=worker._poll_loop)
        thread.daemon = True
        thread.start()
        time.sleep(0.3)
        worker._running = False
        thread.join(timeout=2)

        assert len(MockRouterHandler.complete_calls) == 1
        assert MockRouterHandler.complete_calls[0]["task_id"] == "task-1"


class TestMeshWorkerStop:
    def test_stop_terminates_heartbeat(self, mock_router):
        config = WorkerConfig(
            worker_id="ws-test-01",
            router_url=mock_router,
            heartbeat_interval=0.1,
        )
        worker = MeshWorker(config)
        worker._running = True
        worker._start_heartbeat()
        time.sleep(0.15)
        worker.stop()

        assert not worker._running
        count_before = len(MockRouterHandler.heartbeat_calls)
        time.sleep(0.2)
        count_after = len(MockRouterHandler.heartbeat_calls)
        # Should not have sent many more heartbeats after stop
        assert count_after - count_before <= 1
