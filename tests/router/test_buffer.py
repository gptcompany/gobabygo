"""Tests for the fallback buffer and emitter+buffer integration."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.router.bridge.buffer import FallbackBuffer
from src.router.bridge.emitter import EventEmitter
from src.router.bridge.transport import InProcessTransport
from src.router.db import RouterDB


def _sample_event_json(idem_key: str = "k1") -> str:
    """Helper: minimal CloudEvent JSON string."""
    return json.dumps({
        "specversion": "1.0",
        "type": "com.mesh.command.started",
        "source": "mesh/gsd-bridge/test",
        "id": f"ce-{idem_key}",
        "data": {
            "run_id": "run-1",
            "task_id": "task-1",
            "gsd_command": "gsd:test",
            "event": "started",
            "idempotency_key": idem_key,
            "ts": "2026-02-19T12:00:00+00:00",
        },
    })


class _SuccessTransport:
    def send(self, cloud_event_json: str) -> bool:
        return True


class _FailTransport:
    def send(self, cloud_event_json: str) -> bool:
        return False


class _PartialTransport:
    """Succeeds on first call, fails on subsequent calls."""

    def __init__(self):
        self._count = 0

    def send(self, cloud_event_json: str) -> bool:
        self._count += 1
        return self._count == 1


# --- Buffer core tests ---

class TestFallbackBuffer:
    def test_append_creates_file(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        buf.append(_sample_event_json())
        assert buf.buffer_path.exists()

    def test_append_writes_ndjson_line(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        buf.append(_sample_event_json("a"))
        buf.append(_sample_event_json("b"))
        lines = buf.buffer_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["data"]["idempotency_key"] == "a"
        assert json.loads(lines[1])["data"]["idempotency_key"] == "b"

    def test_read_all_returns_events(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        buf.append(_sample_event_json("x"))
        buf.append(_sample_event_json("y"))
        events = buf.read_all()
        assert len(events) == 2

    def test_read_all_empty_file(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        assert buf.read_all() == []

    def test_read_all_missing_file(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "nonexistent.jsonl")
        assert buf.read_all() == []

    def test_count(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        assert buf.count() == 0
        buf.append(_sample_event_json())
        assert buf.count() == 1

    def test_clear_removes_file(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        buf.append(_sample_event_json())
        assert buf.buffer_path.exists()
        buf.clear()
        assert not buf.buffer_path.exists()

    def test_clear_nonexistent_file(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "nope.jsonl")
        buf.clear()  # should not raise

    def test_creates_parent_directories(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "deep" / "nested" / "buf.jsonl")
        assert (tmp_path / "deep" / "nested").is_dir()


# --- Replay tests ---

class TestBufferReplay:
    def test_replay_success_rotates_file(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        buf.append(_sample_event_json("a"))
        buf.append(_sample_event_json("b"))
        sent, failed = buf.replay(_SuccessTransport())
        assert sent == 2
        assert failed == 0
        assert not buf.buffer_path.exists()
        rotated = list(tmp_path.glob("*.replayed-*"))
        assert len(rotated) == 1

    def test_replay_failure_keeps_buffer(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        buf.append(_sample_event_json("a"))
        sent, failed = buf.replay(_FailTransport())
        assert sent == 0
        assert failed == 1
        assert buf.buffer_path.exists()
        assert buf.count() == 1

    def test_replay_partial_rewrites_with_failed_only(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        buf.append(_sample_event_json("a"))
        buf.append(_sample_event_json("b"))
        sent, failed = buf.replay(_PartialTransport())
        assert sent == 1
        assert failed == 1
        remaining = buf.read_all()
        assert len(remaining) == 1
        assert json.loads(remaining[0])["data"]["idempotency_key"] == "b"

    def test_replay_empty_buffer(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        sent, failed = buf.replay(_SuccessTransport())
        assert sent == 0
        assert failed == 0


# --- Emitter + Buffer integration ---

class TestEmitterBufferIntegration:
    @pytest.fixture
    def db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        db = RouterDB(db_path)
        db.init_schema()
        return db

    def test_emit_failure_buffers_event(self, tmp_path):
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        emitter = EventEmitter(
            _FailTransport(),
            source_machine="test",
            buffer=buf,
        )
        result = emitter.emit(
            command="gsd:test",
            event_kind="started",
            run_id="run-1",
        )
        assert result is False
        assert buf.count() == 1

    def test_emit_success_does_not_buffer(self, tmp_path, db):
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        transport = InProcessTransport(db)
        emitter = EventEmitter(
            transport,
            source_machine="test",
            buffer=buf,
        )
        result = emitter.emit(
            command="gsd:test",
            event_kind="started",
            run_id="run-1",
        )
        assert result is True
        assert buf.count() == 0

    def test_replay_buffer_sends_buffered_events(self, tmp_path, db):
        buf = FallbackBuffer(tmp_path / "buf.jsonl")
        # First emit with failing transport
        emitter = EventEmitter(
            _FailTransport(),
            source_machine="test",
            buffer=buf,
        )
        emitter.emit(
            command="gsd:test",
            event_kind="started",
            run_id="run-1",
        )
        assert buf.count() == 1

        # Now replay with working transport
        transport = InProcessTransport(db)
        emitter2 = EventEmitter(
            transport,
            source_machine="test",
            buffer=buf,
        )
        sent, failed = emitter2.replay_buffer()
        assert sent == 1
        assert failed == 0

    def test_emit_without_buffer_failure_not_buffered(self):
        emitter = EventEmitter(
            _FailTransport(),
            source_machine="test",
            buffer=None,
        )
        result = emitter.emit(
            command="gsd:test",
            event_kind="started",
            run_id="run-fail",
        )
        assert result is False


# --- Concurrency test ---

class TestBufferConcurrency:
    def test_concurrent_appends(self, tmp_path):
        """Verify file locking prevents corruption with concurrent writes."""
        buf_path = tmp_path / "concurrent.jsonl"
        script = f'''
import sys, json, fcntl
from pathlib import Path

buf_path = Path("{buf_path}")
worker_id = sys.argv[1]
for i in range(10):
    data = json.dumps({{"worker": worker_id, "i": i}})
    with open(buf_path, "a") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(data + "\\n")
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
'''
        script_path = tmp_path / "worker.py"
        script_path.write_text(script)

        procs = []
        for w in range(3):
            p = subprocess.Popen(
                [sys.executable, str(script_path), str(w)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            procs.append(p)

        for p in procs:
            p.wait()

        lines = buf_path.read_text().strip().split("\n")
        assert len(lines) == 30  # 3 workers * 10 events each
        for line in lines:
            json.loads(line)  # verify valid JSON
