"""Tests for the event emitter and schema validation."""

from __future__ import annotations


import pytest

from src.router.bridge.emitter import EventEmitter
from src.router.bridge.schema import load_schema, validate_event_data
from src.router.bridge.transport import InProcessTransport
from src.router.comms import CommunicationPolicy
from src.router.db import RouterDB


@pytest.fixture
def db(tmp_path):
    """Create a fresh RouterDB with schema."""
    db_path = str(tmp_path / "test.db")
    db = RouterDB(db_path)
    db.init_schema()
    return db


@pytest.fixture
def transport(db):
    return InProcessTransport(db)


@pytest.fixture
def emitter(transport):
    return EventEmitter(transport, source_machine="test-mac")


@pytest.fixture
def emitter_with_policy(transport):
    return EventEmitter(
        transport,
        source_machine="test-mac",
        comm_policy=CommunicationPolicy(),
    )


# --- Schema validation tests ---

class TestSchemaValidation:
    def test_load_schema_returns_dict(self):
        schema = load_schema()
        assert isinstance(schema, dict)
        assert schema["type"] == "object"

    def test_validate_valid_data(self):
        data = {
            "run_id": "abc-123",
            "gsd_command": "gsd:plan-phase",
            "event": "started",
            "idempotency_key": "key-1",
            "ts": "2026-02-19T12:00:00+00:00",
            "attempt": 1,
            "artifact_paths": [],
            "sender_role": "president",
        }
        errors = validate_event_data(data)
        assert errors == []

    def test_validate_missing_required_field(self):
        data = {
            "gsd_command": "test",
            "event": "started",
            "idempotency_key": "k",
            "ts": "2026-02-19T12:00:00",
            # missing run_id
        }
        errors = validate_event_data(data)
        assert len(errors) > 0
        assert any("run_id" in e for e in errors)

    def test_validate_invalid_event_enum(self):
        data = {
            "run_id": "r1",
            "gsd_command": "test",
            "event": "invalid_event",
            "idempotency_key": "k",
            "ts": "now",
        }
        errors = validate_event_data(data)
        assert len(errors) > 0

    def test_validate_invalid_step_enum(self):
        data = {
            "run_id": "r1",
            "gsd_command": "test",
            "event": "started",
            "idempotency_key": "k",
            "ts": "now",
            "step": "invalid_step",
        }
        errors = validate_event_data(data)
        assert len(errors) > 0

    def test_validate_additional_properties_rejected(self):
        data = {
            "run_id": "r1",
            "gsd_command": "test",
            "event": "started",
            "idempotency_key": "k",
            "ts": "now",
            "unknown_field": "surprise",
        }
        errors = validate_event_data(data)
        assert len(errors) > 0


# --- Emitter tests ---

class TestEventEmitter:
    def test_emit_creates_cloud_event(self, emitter, db):
        result = emitter.emit(
            command="gsd:plan-phase",
            event_kind="started",
            run_id="run-1",
            task_id="task-1",
            phase="04",
            step="plan",
        )
        assert result is True
        events = db.get_events("task-1")
        assert len(events) == 1
        assert events[0].event_type == "command.started"

    def test_emit_with_minimal_fields(self, emitter):
        result = emitter.emit(
            command="gsd:research-phase",
            event_kind="completed",
            run_id="run-2",
        )
        assert result is True

    def test_emit_with_all_optional_fields(self, emitter, db):
        result = emitter.emit(
            command="gsd:execute-phase",
            event_kind="completed",
            run_id="run-3",
            task_id="task-3",
            phase="05",
            step="implement",
            target_cli="claude",
            target_account="work",
            status="running",
            attempt=2,
            artifact_paths=["PLAN.md"],
            duration_ms=5000,
        )
        assert result is True
        events = db.get_events("task-3")
        assert events[0].payload["duration_ms"] == 5000
        assert events[0].payload["target_cli"] == "claude"

    def test_emit_generates_deterministic_idempotency_key(self, emitter, db):
        emitter.emit(
            command="cmd1",
            event_kind="started",
            run_id="run-a",
            step="plan",
        )
        # Same parameters should produce same key
        key1 = EventEmitter._make_idempotency_key("run-a", "cmd1", "plan", "started", 1)
        key2 = EventEmitter._make_idempotency_key("run-a", "cmd1", "plan", "started", 1)
        assert key1 == key2
        assert len(key1) == 64  # SHA-256 hex digest

    def test_emit_different_params_different_key(self):
        key1 = EventEmitter._make_idempotency_key("run-a", "cmd1", "plan", "started", 1)
        key2 = EventEmitter._make_idempotency_key("run-a", "cmd1", "plan", "started", 2)
        assert key1 != key2

    def test_emit_schema_validation_failure_raises(self, emitter):
        with pytest.raises(ValueError, match="schema validation failed"):
            emitter.emit(
                command="",  # empty string violates minLength
                event_kind="started",
                run_id="run-bad",
            )

    def test_emit_invalid_event_kind_raises(self, emitter):
        with pytest.raises(ValueError, match="schema validation failed"):
            emitter.emit(
                command="gsd:test",
                event_kind="invalid",
                run_id="run-bad",
            )

    def test_emit_returns_false_on_transport_failure(self):
        class FailTransport:
            def send(self, cloud_event_json: str) -> bool:
                return False

        emitter = EventEmitter(FailTransport(), source_machine="test")
        result = emitter.emit(
            command="gsd:test",
            event_kind="started",
            run_id="run-fail",
        )
        assert result is False


# --- CommunicationPolicy integration ---

class TestEmitterCommPolicy:
    def test_emit_blocks_worker_role(self, emitter_with_policy):
        with pytest.raises(ValueError, match="not authorized"):
            emitter_with_policy.emit(
                command="gsd:test",
                event_kind="started",
                run_id="run-1",
                sender_role="worker",
            )

    def test_emit_allows_president_role(self, emitter_with_policy, db):
        result = emitter_with_policy.emit(
            command="gsd:test",
            event_kind="started",
            run_id="run-1",
            sender_role="president",
        )
        assert result is True

    def test_emit_allows_boss_role(self, emitter_with_policy, db):
        result = emitter_with_policy.emit(
            command="gsd:test",
            event_kind="started",
            run_id="run-2",
            sender_role="boss",
        )
        assert result is True

    def test_emit_without_policy_allows_any_role(self, emitter, db):
        result = emitter.emit(
            command="gsd:test",
            event_kind="started",
            run_id="run-3",
            sender_role="worker",
        )
        assert result is True


# --- Replay ---

class TestEmitterReplay:
    def test_replay_buffer_without_buffer_returns_zero(self, emitter):
        assert emitter.replay_buffer() == (0, 0)
