"""End-to-end smoke test: router + worker lifecycle in-process.

Starts an HTTP server on a random port, registers a worker,
inserts a task, dispatches it, and verifies the full lifecycle
(queued -> assigned -> running -> completed) via HTTP calls.

No external processes required. Runs in ~2 seconds.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

import pytest
import requests

from src.router.db import RouterDB
from src.router.heartbeat import HeartbeatManager
from src.router.longpoll import LongPollRegistry
from src.router.metrics import MeshMetrics
from src.router.models import Task, TaskStatus
from src.router.scheduler import Scheduler
from src.router.server import MeshRouterHandler
from src.router.worker_manager import WorkerManager
from src.router.bridge.transport import InProcessTransport


@pytest.fixture
def mesh(tmp_path):
    """Boot a complete mesh stack (router + DB) on a random port."""
    db_path = str(tmp_path / "smoke.db")
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


class TestE2ELive:
    """Full task lifecycle: create -> dispatch -> ack -> complete."""

    def test_full_task_lifecycle(self, mesh):
        url = mesh["url"]
        db = mesh["db"]
        scheduler = mesh["scheduler"]
        lp = mesh["longpoll_registry"]

        # 1. Health check -- router is alive
        resp = requests.get(f"{url}/health", timeout=5)
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

        # 2. Register worker via HTTP
        resp = requests.post(
            f"{url}/register",
            json={
                "worker_id": "smoke-w1",
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

        # 3. Insert a queued task
        task = Task(
            task_id="smoke-t1",
            title="Smoke test task",
            phase="implement",
            target_cli="claude",
            target_account="work",
            priority=10,
            idempotency_key="smoke-001",
        )
        db.insert_task(task)

        # Verify task is queued
        stored = db.get_task("smoke-t1")
        assert stored.status == TaskStatus.queued

        # 4. Register worker in long-poll registry and start dispatch loop
        lp.register("smoke-w1")

        # Start dispatch loop (mimics run_server behavior, no manual dispatch)
        dispatch_running = True

        def dispatch_loop():
            while dispatch_running:
                time.sleep(0.1)
                try:
                    while True:
                        result = scheduler.dispatch()
                        if result is None:
                            break
                except Exception:
                    pass

        dispatch_thread = threading.Thread(target=dispatch_loop, daemon=True)
        dispatch_thread.start()

        # 5. Long-poll for task (blocks until dispatch loop wakes us)
        start = time.monotonic()
        resp = requests.get(f"{url}/tasks/next?worker_id=smoke-w1", timeout=5)
        elapsed = time.monotonic() - start

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        assert data["task_id"] == "smoke-t1"
        assert data["title"] == "Smoke test task"
        assert elapsed < 2.0, f"Dispatch took {elapsed:.2f}s, expected < 2s"

        # 6. ACK the task (assigned -> running)
        resp = requests.post(
            f"{url}/tasks/ack",
            json={"task_id": "smoke-t1", "worker_id": "smoke-w1"},
            timeout=5,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "acknowledged"

        # Verify running state
        t = db.get_task("smoke-t1")
        assert t.status == TaskStatus.running

        # 7. Complete the task (running -> completed)
        resp = requests.post(
            f"{url}/tasks/complete",
            json={"task_id": "smoke-t1", "worker_id": "smoke-w1"},
            timeout=5,
        )
        assert resp.status_code == 200

        # Verify completed state
        t = db.get_task("smoke-t1")
        assert t.status == TaskStatus.completed

        dispatch_running = False

    def test_meshctl_status_output(self, mesh):
        """meshctl status returns valid JSON with workers and health."""
        url = mesh["url"]

        # Register a worker first
        requests.post(
            f"{url}/register",
            json={
                "worker_id": "ctl-w1",
                "machine": "ci",
                "cli_type": "claude",
                "account_profile": "work",
                "capabilities": ["code"],
                "status": "idle",
                "concurrency": 1,
            },
            timeout=5,
        )

        # Fetch the same endpoints meshctl uses
        workers_resp = requests.get(f"{url}/workers", timeout=5)
        health_resp = requests.get(f"{url}/health", timeout=5)

        assert workers_resp.status_code == 200
        assert health_resp.status_code == 200

        workers = workers_resp.json().get("workers", [])
        health = health_resp.json()

        assert len(workers) >= 1
        assert any(w["worker_id"] == "ctl-w1" for w in workers)
        assert health["status"] == "healthy"
        assert health["workers"] >= 1

    def test_health_reflects_queue_depth(self, mesh):
        """Health endpoint accurately reports queued task count."""
        url = mesh["url"]
        db = mesh["db"]

        # Insert 3 tasks
        for i in range(3):
            db.insert_task(Task(
                task_id=f"q-{i}",
                title=f"Queued task {i}",
                phase="implement",
                idempotency_key=f"queue-{i}",
            ))

        resp = requests.get(f"{url}/health", timeout=5)
        assert resp.json()["queue_depth"] == 3
