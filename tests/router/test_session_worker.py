"""Unit tests for the interactive session worker."""

from __future__ import annotations

import os
from unittest.mock import patch

from src.router.session_worker import (
    SessionWorkerConfig,
    _compute_output_emit,
    _sanitize_session_name,
)


def test_sanitize_session_name() -> None:
    name = _sanitize_session_name("mesh/claude work:task#1")
    assert name == "mesh-claude-work-task-1"


def test_session_worker_config_from_env() -> None:
    env = {
        "MESH_WORKER_ID": "ws-claude-session-01",
        "MESH_ROUTER_URL": "http://10.0.0.1:8780",
        "MESH_CLI_TYPE": "claude",
        "MESH_ACCOUNT_PROFILE": "work-claude",
        "MESH_AUTH_TOKEN": "tok-123",
        "MESH_EXECUTION_MODES": "session",
        "MESH_CLI_COMMAND": "claude",
        "MESH_SESSION_POLL_INTERVAL_S": "0.5",
        "MESH_TMUX_SESSION_PREFIX": "meshx",
    }
    with patch.dict(os.environ, env):
        cfg = SessionWorkerConfig.from_env()
    assert cfg.worker_id == "ws-claude-session-01"
    assert cfg.router_url == "http://10.0.0.1:8780"
    assert cfg.cli_type == "claude"
    assert cfg.account_profile == "work-claude"
    assert cfg.auth_token == "tok-123"
    assert cfg.execution_modes == ["session"]
    assert cfg.cli_command == "claude"
    assert cfg.session_poll_interval_s == 0.5
    assert cfg.tmux_session_prefix == "meshx"


def test_compute_output_emit_delta() -> None:
    prev = "line1\nline2"
    cur = "line1\nline2\nline3"
    result = _compute_output_emit(prev, cur, max_chars=100)
    assert result is not None
    content, meta = result
    assert content == "line3"
    assert meta["snapshot"] is False
    assert meta["kind"] == "delta"


def test_compute_output_emit_snapshot_on_reflow() -> None:
    prev = "abcd"
    cur = "ab\ncd"
    result = _compute_output_emit(prev, cur, max_chars=100)
    assert result is not None
    content, meta = result
    assert content == "ab\ncd"
    assert meta["snapshot"] is True
    assert meta["kind"] == "snapshot"


def test_compute_output_emit_none_on_unchanged() -> None:
    assert _compute_output_emit("same", "same") is None
