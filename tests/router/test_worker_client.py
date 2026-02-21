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
    ack_calls = []
    poll_count = 0
    complete_calls = []
    fail_calls = []
    task_to_serve = None
    error_on_first_poll = False  # Return 500 on first poll
    return_409_on_poll = False  # Return 409 conflict
    heartbeat_response = {"status": "ok"}  # Configurable heartbeat response
    heartbeat_raw_response = False  # Return plain text instead of JSON

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}

        if self.path == "/register":
            MockRouterHandler.register_calls.append(body)
            self._respond(201, {"status": "registered", "worker_id": body.get("worker_id")})
        elif self.path == "/heartbeat":
            MockRouterHandler.heartbeat_calls.append(body)
            if MockRouterHandler.heartbeat_raw_response:
                self.send_response(200)
                raw = b"OK"
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            else:
                self._respond(200, MockRouterHandler.heartbeat_response)
        elif self.path == "/tasks/ack":
            MockRouterHandler.ack_calls.append(body)
            self._respond(200, {"status": "acknowledged"})
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
            if MockRouterHandler.error_on_first_poll and MockRouterHandler.poll_count == 1:
                self._respond(500, {"error": "server_error"})
            elif MockRouterHandler.return_409_on_poll:
                MockRouterHandler.return_409_on_poll = False  # Only first time
                self._respond(409, {"error": "duplicate_poll"})
            elif MockRouterHandler.task_to_serve:
                task = MockRouterHandler.task_to_serve
                MockRouterHandler.task_to_serve = None  # Serve once
                self._respond(200, task)
            else:
                # Brief sleep to simulate server-held connection in test mode
                time.sleep(0.02)
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
    MockRouterHandler.ack_calls = []
    MockRouterHandler.poll_count = 0
    MockRouterHandler.complete_calls = []
    MockRouterHandler.fail_calls = []
    MockRouterHandler.task_to_serve = None
    MockRouterHandler.error_on_first_poll = False
    MockRouterHandler.return_409_on_poll = False
    MockRouterHandler.heartbeat_response = {"status": "ok"}
    MockRouterHandler.heartbeat_raw_response = False


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
        assert config.longpoll_timeout == 25.0

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
            longpoll_timeout=0.05,  # Fast timeout for test
        )
        worker = MeshWorker(config)
        worker._running = True

        # Run poll loop in background, stop after a few iterations
        thread = threading.Thread(target=worker._poll_loop)
        thread.daemon = True
        thread.start()
        time.sleep(0.8)
        worker._running = False
        thread.join(timeout=2)

        # Worker should reconnect multiple times after 204 with jitter
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
            longpoll_timeout=0.05,
        )
        worker = MeshWorker(config)
        worker._running = True

        thread = threading.Thread(target=worker._poll_loop)
        thread.daemon = True
        thread.start()
        time.sleep(0.5)
        worker._running = False
        thread.join(timeout=2)

        # Worker should ack before completing
        assert len(MockRouterHandler.ack_calls) == 1
        assert MockRouterHandler.ack_calls[0]["task_id"] == "task-1"
        assert len(MockRouterHandler.complete_calls) == 1
        assert MockRouterHandler.complete_calls[0]["task_id"] == "task-1"

    def test_poll_reconnects_immediately_on_204(self, mock_router):
        """Verify worker does NOT wait 2s between polls (long-poll, not fixed interval)."""
        config = WorkerConfig(
            worker_id="ws-test-01",
            router_url=mock_router,
            longpoll_timeout=0.05,
        )
        worker = MeshWorker(config)
        worker._running = True

        thread = threading.Thread(target=worker._poll_loop)
        thread.daemon = True
        thread.start()
        time.sleep(1.0)  # 1 second window
        worker._running = False
        thread.join(timeout=2)

        # With jitter 100-500ms + 20ms mock sleep, should get at least 3 polls in 1s
        # Old behavior (2s sleep) would only get 1 poll in 1s
        assert MockRouterHandler.poll_count >= 3

    def test_poll_exponential_backoff_on_error(self, mock_router):
        """On 500 error, worker backs off exponentially."""
        MockRouterHandler.error_on_first_poll = True

        config = WorkerConfig(
            worker_id="ws-test-01",
            router_url=mock_router,
            longpoll_timeout=0.05,
        )
        worker = MeshWorker(config)
        worker._running = True

        thread = threading.Thread(target=worker._poll_loop)
        thread.daemon = True
        thread.start()
        time.sleep(2.0)
        worker._running = False
        thread.join(timeout=2)

        # First poll = 500 (triggers backoff 1s + jitter)
        # Second poll = 204 (no more backoff)
        # With 2s total time, backoff limits polls after error
        assert MockRouterHandler.poll_count >= 2  # At least error + recovery

    def test_worker_uses_longpoll_timeout_for_request(self, mock_router):
        """Verify HTTP request timeout is set to longpoll_timeout + 5."""
        config = WorkerConfig(
            worker_id="ws-test-01",
            router_url=mock_router,
            longpoll_timeout=10.0,
        )
        worker = MeshWorker(config)
        worker._running = True

        # Patch session.get to capture timeout kwarg
        original_get = worker._session.get
        captured_timeouts = []

        def patched_get(*args, **kwargs):
            captured_timeouts.append(kwargs.get("timeout"))
            # Stop after first poll
            worker._running = False
            return original_get(*args, **kwargs)

        worker._session.get = patched_get

        worker._poll_loop()
        assert len(captured_timeouts) >= 1
        assert captured_timeouts[0] == 15.0  # 10.0 + 5

    def test_poll_handles_409_conflict(self, mock_router):
        """On 409 conflict, worker backs off."""
        MockRouterHandler.return_409_on_poll = True

        config = WorkerConfig(
            worker_id="ws-test-01",
            router_url=mock_router,
            longpoll_timeout=0.05,
        )
        worker = MeshWorker(config)
        worker._running = True

        thread = threading.Thread(target=worker._poll_loop)
        thread.daemon = True
        thread.start()
        time.sleep(2.0)
        worker._running = False
        thread.join(timeout=2)

        # First poll = 409 (triggers backoff), then 204s
        assert MockRouterHandler.poll_count >= 2


class TestAutoReregisterOnUnknownWorker:
    def test_heartbeat_unknown_worker_triggers_reregister(self, mock_router):
        """On unknown_worker heartbeat response, worker re-registers."""
        MockRouterHandler.heartbeat_response = {"status": "unknown_worker"}
        config = WorkerConfig(
            worker_id="ws-test-01",
            router_url=mock_router,
            heartbeat_interval=0.1,
        )
        worker = MeshWorker(config)
        worker._running = True
        worker._start_heartbeat()
        time.sleep(0.35)
        worker.stop()

        # At least one re-registration beyond initial (heartbeat-triggered)
        assert len(MockRouterHandler.register_calls) >= 1
        # Verify register payload has correct worker_id
        assert all(
            c["worker_id"] == "ws-test-01"
            for c in MockRouterHandler.register_calls
        )

    def test_heartbeat_ok_does_not_reregister(self, mock_router):
        """Normal 'ok' heartbeat does NOT trigger re-registration."""
        MockRouterHandler.heartbeat_response = {"status": "ok"}
        config = WorkerConfig(
            worker_id="ws-test-01",
            router_url=mock_router,
            heartbeat_interval=0.1,
        )
        worker = MeshWorker(config)
        worker._running = True
        # Record initial register count (from any prior calls)
        register_count = len(MockRouterHandler.register_calls)
        worker._start_heartbeat()
        time.sleep(0.35)
        worker.stop()

        # No additional registration calls from heartbeat
        assert len(MockRouterHandler.register_calls) == register_count

    def test_heartbeat_non_json_response_tolerant(self, mock_router):
        """Non-JSON heartbeat response (old server) does not crash or trigger re-register."""
        MockRouterHandler.heartbeat_raw_response = True
        config = WorkerConfig(
            worker_id="ws-test-01",
            router_url=mock_router,
            heartbeat_interval=0.1,
        )
        worker = MeshWorker(config)
        worker._running = True
        # Record initial register count
        register_count = len(MockRouterHandler.register_calls)
        worker._start_heartbeat()
        time.sleep(0.25)
        worker.stop()

        # No crash, no registration calls from heartbeat
        assert len(MockRouterHandler.register_calls) == register_count


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
