"""Tests for Prometheus metrics collector and /metrics endpoint."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

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
    """Fresh DB for each test."""
    db_path = str(tmp_path / "test_metrics.db")
    db = RouterDB(db_path, check_same_thread=False)
    db.init_schema()
    yield db
    db.close()


@pytest.fixture
def metrics():
    """Fresh MeshMetrics with isolated registry."""
    return MeshMetrics()


@pytest.fixture
def server_url(db):
    """Start a test HTTP server for metrics endpoint testing."""
    heartbeat = HeartbeatManager(db)
    scheduler = Scheduler(db)
    transport = InProcessTransport(db)
    test_metrics = MeshMetrics()

    server = ThreadingHTTPServer(("127.0.0.1", 0), MeshRouterHandler)
    server.router_state = {
        "db": db,
        "heartbeat": heartbeat,
        "scheduler": scheduler,
        "transport": transport,
        "metrics": test_metrics,
        "auth_token": "test-token",
        "start_time": datetime.now(timezone.utc),
    }

    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"
    yield url
    server.shutdown()


# --- MeshMetrics unit tests ---


class TestMeshMetricsUnit:
    """Unit tests for MeshMetrics class."""

    def test_initial_state_generates_valid_output(self, metrics):
        """Fresh metrics should produce valid Prometheus text format."""
        output = metrics.generate().decode("utf-8")
        assert "mesh_router_up" in output
        assert "mesh_tasks_queued" in output
        assert "mesh_workers_total" in output
        assert "mesh_task_duration_seconds" in output

    def test_collect_from_db_empty(self, db, metrics):
        """collect_from_db on empty DB should set all gauges to 0."""
        metrics.collect_from_db(db, uptime_s=42.0)
        output = metrics.generate().decode("utf-8")
        assert "mesh_router_up 1.0" in output
        assert "mesh_uptime_seconds 42.0" in output
        assert "mesh_tasks_queued 0.0" in output
        assert "mesh_workers_total 0.0" in output
        assert "mesh_dead_letters_total 0.0" in output

    def test_collect_from_db_with_tasks(self, db, metrics):
        """collect_from_db should reflect task counts by status."""
        now = datetime.now(timezone.utc).isoformat()
        for i in range(3):
            db.insert_task(Task(
                task_id=f"queued-{i}", status=TaskStatus.queued, phase="test",
                created_at=now, updated_at=now,
            ))
        for i in range(2):
            db.insert_task(Task(
                task_id=f"running-{i}", status=TaskStatus.running, phase="test",
                created_at=now, updated_at=now,
            ))
        db.insert_task(Task(
            task_id="completed-1", status=TaskStatus.completed, phase="test",
            created_at=now, updated_at=now,
        ))

        metrics.collect_from_db(db, uptime_s=10.0)
        output = metrics.generate().decode("utf-8")
        assert "mesh_tasks_queued 3.0" in output
        assert "mesh_tasks_running 2.0" in output
        assert "mesh_tasks_completed_total 1.0" in output

    def test_collect_from_db_queue_depth(self, db, metrics):
        """queue_depth should sum queued + assigned + blocked."""
        now = datetime.now(timezone.utc).isoformat()
        db.insert_task(Task(
            task_id="q1", status=TaskStatus.queued, phase="test",
            created_at=now, updated_at=now,
        ))
        db.insert_task(Task(
            task_id="a1", status=TaskStatus.assigned, phase="test",
            created_at=now, updated_at=now,
        ))
        db.insert_task(Task(
            task_id="b1", status=TaskStatus.blocked, phase="test",
            created_at=now, updated_at=now,
        ))
        db.insert_task(Task(
            task_id="r1", status=TaskStatus.running, phase="test",
            created_at=now, updated_at=now,
        ))

        metrics.collect_from_db(db, uptime_s=1.0)
        output = metrics.generate().decode("utf-8")
        assert "mesh_queue_depth 3.0" in output

    def test_collect_from_db_workers(self, db, metrics):
        """Worker gauges should reflect status correctly."""
        now = datetime.now(timezone.utc).isoformat()
        db.insert_worker(Worker(
            worker_id="w1", status="idle", last_heartbeat=now,
        ))
        db.insert_worker(Worker(
            worker_id="w2", status="busy", last_heartbeat=now,
        ))
        db.insert_worker(Worker(
            worker_id="w3", status="idle", last_heartbeat=now,
            stale_since=now,
        ))

        metrics.collect_from_db(db, uptime_s=1.0)
        output = metrics.generate().decode("utf-8")
        assert "mesh_workers_total 3.0" in output
        assert "mesh_workers_idle 2.0" in output
        assert "mesh_workers_busy 1.0" in output
        assert "mesh_workers_stale 1.0" in output

    def test_collect_from_db_dead_letters(self, db, metrics):
        """Dead letter count should be reflected."""
        now = datetime.now(timezone.utc).isoformat()
        # Insert a dead letter via the DB
        db._conn.execute(
            "INSERT INTO dead_letter_events (dl_id, task_id, attempted_from, attempted_to, reason, ts) VALUES (?, ?, ?, ?, ?, ?)",
            ("dl1", "task1", "queued", "invalid_state", "bad transition", now),
        )
        db._conn.commit()

        metrics.collect_from_db(db, uptime_s=1.0)
        output = metrics.generate().decode("utf-8")
        assert "mesh_dead_letters_total 1.0" in output

    def test_observe_task_duration(self, metrics):
        """Summary should record observations."""
        metrics.observe_task_duration(1.5)
        metrics.observe_task_duration(3.0)
        output = metrics.generate().decode("utf-8")
        assert "mesh_task_duration_seconds_count 2.0" in output
        assert "mesh_task_duration_seconds_sum 4.5" in output


# --- RouterDB helper tests ---


class TestDBMetricsHelpers:
    """Tests for count_all_task_statuses and count_dead_letters."""

    def test_count_all_task_statuses_empty(self, db):
        result = db.count_all_task_statuses()
        assert result == {}

    def test_count_all_task_statuses_with_tasks(self, db):
        now = datetime.now(timezone.utc).isoformat()
        db.insert_task(Task(
            task_id="t1", status=TaskStatus.queued, phase="plan",
            created_at=now, updated_at=now,
        ))
        db.insert_task(Task(
            task_id="t2", status=TaskStatus.queued, phase="plan",
            created_at=now, updated_at=now,
        ))
        db.insert_task(Task(
            task_id="t3", status=TaskStatus.completed, phase="plan",
            created_at=now, updated_at=now,
        ))

        result = db.count_all_task_statuses()
        assert result["queued"] == 2
        assert result["completed"] == 1
        assert "running" not in result

    def test_count_dead_letters_empty(self, db):
        assert db.count_dead_letters() == 0

    def test_count_dead_letters_with_entries(self, db):
        now = datetime.now(timezone.utc).isoformat()
        for i in range(3):
            db._conn.execute(
                "INSERT INTO dead_letter_events (dl_id, task_id, attempted_from, attempted_to, reason, ts) VALUES (?, ?, ?, ?, ?, ?)",
                (f"dl{i}", f"task{i}", "queued", "bad", "reason", now),
            )
        db._conn.commit()
        assert db.count_dead_letters() == 3


# --- HTTP endpoint tests ---


class TestMetricsEndpoint:
    """Tests for GET /metrics HTTP endpoint."""

    def test_metrics_returns_prometheus_format(self, server_url):
        resp = requests.get(f"{server_url}/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["Content-Type"]
        assert "mesh_router_up" in resp.text
        assert "mesh_tasks_queued" in resp.text
        assert "mesh_workers_total" in resp.text

    def test_metrics_no_auth_required(self, server_url):
        """Metrics should be accessible without Bearer token."""
        resp = requests.get(f"{server_url}/metrics")
        assert resp.status_code == 200

    def test_metrics_contains_all_metric_families(self, server_url):
        resp = requests.get(f"{server_url}/metrics")
        text = resp.text
        expected_metrics = [
            "mesh_router_up",
            "mesh_tasks_queued",
            "mesh_tasks_running",
            "mesh_tasks_review",
            "mesh_queue_depth",
            "mesh_workers_total",
            "mesh_workers_idle",
            "mesh_workers_busy",
            "mesh_workers_stale",
            "mesh_uptime_seconds",
            "mesh_tasks_completed_total",
            "mesh_tasks_failed_total",
            "mesh_tasks_timeout_total",
            "mesh_dead_letters_total",
            "mesh_task_duration_seconds",
        ]
        for metric in expected_metrics:
            assert metric in text, f"Missing metric: {metric}"
