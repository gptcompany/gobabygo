"""Smoke tests for MEM-04: Router operates correctly when OpenMemory is unavailable.

OpenMemory is a client-side MCP concern — the router has zero dependency on it.
These tests verify that contract explicitly.
"""

from __future__ import annotations

import socket
import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import requests
import yaml

from src.router.db import RouterDB
from src.router.heartbeat import HeartbeatManager
from src.router.longpoll import LongPollRegistry
from src.router.metrics import MeshMetrics
from src.router.models import CLIType, Task, TaskPhase, TaskStatus
from src.router.scheduler import Scheduler
from src.router.server import MeshRouterHandler
from src.router.worker_manager import WorkerManager
from src.router.bridge.transport import InProcessTransport


@pytest.fixture
def mesh(tmp_path):
    """Boot a complete mesh stack (router + DB) on a random port."""
    db_path = str(tmp_path / "degradation.db")
    db = RouterDB(db_path, check_same_thread=False)
    db.init_schema()

    longpoll_registry = LongPollRegistry()
    worker_manager = WorkerManager(
        db, tokens=[], dev_mode=True, longpoll_registry=longpoll_registry,
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
        "longpoll_timeout": 2.0,
        "auth_token": None,
        "start_time": datetime.now(timezone.utc),
    }

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"

    yield {
        "url": url,
        "db": db,
        "scheduler": scheduler,
        "longpoll_registry": longpoll_registry,
    }

    server.shutdown()
    db.close()


class TestRouterWithoutOpenMemory:
    """MEM-04: Router dispatch/ack/complete works when OpenMemory is down."""

    def test_health_without_openmemory(self, mesh):
        """Router health endpoint works regardless of OpenMemory status."""
        resp = requests.get(f"{mesh['url']}/health", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_full_lifecycle_without_openmemory(self, mesh):
        """Full task lifecycle completes with no OpenMemory dependency."""
        url = mesh["url"]
        db = mesh["db"]
        scheduler = mesh["scheduler"]
        lp = mesh["longpoll_registry"]

        # Register worker
        resp = requests.post(
            f"{url}/register",
            json={
                "worker_id": "mem04-w1",
                "machine": "ci",
                "cli_type": "claude",
                "account_profile": "work",
                "capabilities": ["code"],
                "status": "idle",
                "concurrency": 1,
            },
            timeout=5,
        )
        assert resp.status_code == 201

        # Insert task
        task = Task(
            task_id="mem04-t1",
            title="MEM-04 degradation test task",
            phase=TaskPhase.implement,
            target_cli=CLIType.claude,
            target_account="work",
            priority=10,
            idempotency_key="mem04-001",
        )
        db.insert_task(task)
        assert db.get_task("mem04-t1").status == TaskStatus.queued

        # Dispatch
        lp.register("mem04-w1")
        dispatch_running = True
        dispatch_error: list[Exception] = []

        def dispatch_loop():
            while dispatch_running:
                time.sleep(0.1)
                while True:
                    try:
                        result = scheduler.dispatch()
                    except Exception as exc:
                        dispatch_error.append(exc)
                        return
                    if result is None:
                        break

        dt = threading.Thread(target=dispatch_loop, daemon=True)
        dt.start()

        # Poll for assignment (long-poll)
        resp = requests.get(
            f"{url}/tasks/next?worker_id=mem04-w1", timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["task_id"] == "mem04-t1"

        # Ack
        resp = requests.post(
            f"{url}/tasks/ack",
            json={"task_id": "mem04-t1", "worker_id": "mem04-w1"},
            timeout=5,
        )
        assert resp.status_code == 200

        # Complete
        resp = requests.post(
            f"{url}/tasks/complete",
            json={"task_id": "mem04-t1", "worker_id": "mem04-w1"},
            timeout=5,
        )
        assert resp.status_code == 200

        dispatch_running = False
        dt.join(timeout=2)
        assert not dispatch_error, f"unexpected scheduler dispatch error: {dispatch_error[0]!r}"
        assert db.get_task("mem04-t1").status == TaskStatus.completed


class TestTopologyMemoryConfig:
    """Topology config declares memory as non-required."""

    def test_memory_not_required(self):
        """Topology example has memory.required = false."""
        topology_path = Path("deploy/topology.v1.4.example.yml")
        if not topology_path.exists():
            pytest.skip("topology.v1.4.example.yml not found")

        topology = yaml.safe_load(topology_path.read_text())
        memory = topology.get("global", {}).get("memory", {})

        assert memory.get("required") is False, (
            f"memory.required must be false, got {memory.get('required')}"
        )
        assert memory.get("write_policy") == "best_effort", (
            f"write_policy must be best_effort, got {memory.get('write_policy')}"
        )


class TestMCPClientDegradation:
    """MCP HTTP client to unreachable host fails gracefully."""

    def test_connection_refused_times_out_fast(self):
        """HTTP request to closed port fails quickly, no hang."""
        # Find a port that's definitely not listening
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()  # Port is now closed

        start = time.monotonic()
        with pytest.raises(requests.ConnectionError):
            requests.get(f"http://127.0.0.1:{port}/mcp", timeout=3)
        elapsed = time.monotonic() - start

        assert elapsed < 5, f"Connection refused took {elapsed:.1f}s, expected < 5s"

    def test_unreachable_host_respects_timeout(self):
        """HTTP request to non-routable host respects timeout setting."""
        start = time.monotonic()
        with pytest.raises((requests.ConnectionError, requests.Timeout)):
            # RFC 5737 TEST-NET: guaranteed non-routable
            requests.get("http://192.0.2.1:8080/mcp", timeout=2)
        elapsed = time.monotonic() - start

        assert elapsed < 5, f"Timeout took {elapsed:.1f}s, expected < 5s"
