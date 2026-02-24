"""Unit tests for the interactive session worker."""

from __future__ import annotations

import os
from unittest.mock import patch

from src.router.session_worker import SessionWorkerConfig, _sanitize_session_name


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
