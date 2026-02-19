"""Tests for the event bridge transport layer."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.router.bridge.transport import (
    EventTransport,
    HttpTransport,
    InProcessTransport,
)
from src.router.db import RouterDB


@pytest.fixture
def db(tmp_path):
    """Create a fresh RouterDB with schema."""
    db_path = str(tmp_path / "test.db")
    db = RouterDB(db_path)
    db.init_schema()
    return db


def _make_cloud_event_json(
    task_id: str = "task-1",
    run_id: str = "run-1",
    event: str = "started",
    idempotency_key: str = "idem-1",
) -> str:
    """Helper to build a minimal CloudEvent JSON string."""
    return json.dumps({
        "specversion": "1.0",
        "type": f"com.mesh.command.{event}",
        "source": "mesh/gsd-bridge/test",
        "id": "ce-1",
        "data": {
            "run_id": run_id,
            "task_id": task_id,
            "gsd_command": "gsd:plan-phase",
            "event": event,
            "idempotency_key": idempotency_key,
            "ts": "2026-02-19T12:00:00+00:00",
        },
    })


class TestInProcessTransport:
    def test_send_writes_task_event(self, db):
        transport = InProcessTransport(db)
        ce_json = _make_cloud_event_json()
        result = transport.send(ce_json)
        assert result is True
        events = db.get_events("task-1")
        assert len(events) == 1
        assert events[0].event_type == "command.started"

    def test_send_idempotency_duplicate_returns_false(self, db):
        transport = InProcessTransport(db)
        ce_json = _make_cloud_event_json()
        assert transport.send(ce_json) is True
        assert transport.send(ce_json) is False

    def test_send_invalid_json_returns_false(self, db):
        transport = InProcessTransport(db)
        assert transport.send("not json{{{") is False

    def test_send_without_task_id_uses_run_id(self, db):
        transport = InProcessTransport(db)
        ce_json = json.dumps({
            "specversion": "1.0",
            "type": "com.mesh.command.started",
            "source": "mesh/gsd-bridge/test",
            "id": "ce-2",
            "data": {
                "run_id": "run-99",
                "gsd_command": "pipeline:gsd",
                "event": "started",
                "idempotency_key": "idem-run-level",
                "ts": "2026-02-19T12:00:00+00:00",
            },
        })
        assert transport.send(ce_json) is True
        events = db.get_events("run-99")
        assert len(events) == 1

    def test_send_preserves_data_in_payload(self, db):
        transport = InProcessTransport(db)
        ce_json = _make_cloud_event_json()
        transport.send(ce_json)
        events = db.get_events("task-1")
        payload = events[0].payload
        assert payload["gsd_command"] == "gsd:plan-phase"
        assert payload["run_id"] == "run-1"


class TestHttpTransport:
    def test_send_posts_to_events_endpoint(self):
        transport = HttpTransport("http://10.0.0.1:8080")
        ce_json = _make_cloud_event_json()
        with patch("src.router.bridge.transport.requests") as mock_requests:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_requests.post.return_value = mock_resp
            result = transport.send(ce_json)
        assert result is True
        mock_requests.post.assert_called_once()
        call_args = mock_requests.post.call_args
        assert call_args[0][0] == "http://10.0.0.1:8080/events"

    def test_send_includes_auth_header(self):
        transport = HttpTransport("http://10.0.0.1:8080", auth_token="secret-tok")
        ce_json = _make_cloud_event_json()
        with patch("src.router.bridge.transport.requests") as mock_requests:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_requests.post.return_value = mock_resp
            transport.send(ce_json)
        headers = mock_requests.post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer secret-tok"

    def test_send_returns_false_on_500(self):
        transport = HttpTransport("http://10.0.0.1:8080")
        ce_json = _make_cloud_event_json()
        with patch("src.router.bridge.transport.requests") as mock_requests:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_requests.post.return_value = mock_resp
            result = transport.send(ce_json)
        assert result is False

    def test_send_returns_false_on_connection_error(self):
        transport = HttpTransport("http://10.0.0.1:8080")
        ce_json = _make_cloud_event_json()
        import requests as real_requests
        with patch("src.router.bridge.transport.requests") as mock_requests:
            mock_requests.post.side_effect = real_requests.ConnectionError("refused")
            mock_requests.RequestException = real_requests.RequestException
            result = transport.send(ce_json)
        assert result is False

    def test_send_strips_trailing_slash_from_url(self):
        transport = HttpTransport("http://10.0.0.1:8080/")
        assert transport.router_url == "http://10.0.0.1:8080"


class TestProtocol:
    def test_in_process_is_event_transport(self, db):
        transport = InProcessTransport(db)
        assert isinstance(transport, EventTransport)

    def test_http_is_event_transport(self):
        transport = HttpTransport("http://localhost")
        assert isinstance(transport, EventTransport)
