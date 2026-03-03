"""Tests for session_spawner: tmux session lifecycle management."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from src.router.session_spawner import (
    _sanitize_session_name,
    is_session_alive,
    kill_tmux_session,
    spawn_tmux_session,
)


def test_sanitize_session_name() -> None:
    assert _sanitize_session_name("valid-name_123") == "valid-name_123"
    assert _sanitize_session_name("has spaces!@#") == "hasspaces"
    assert _sanitize_session_name("a.b.c") == "abc"
    assert _sanitize_session_name("ok") == "ok"


@patch("src.router.session_spawner.subprocess.run")
def test_spawn_tmux_session(mock_run) -> None:
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    name = spawn_tmux_session("abcd1234-5678", 0, "claude --task test", work_dir="/tmp")
    assert name == "mesh-abcd1234-s0"
    call_args = mock_run.call_args
    cmd = call_args[0][0]
    assert cmd[0] == "tmux"
    assert cmd[1] == "new-session"
    assert "-d" in cmd
    assert "-s" in cmd
    assert "mesh-abcd1234-s0" in cmd
    assert "-c" in cmd
    assert "/tmp" in cmd
    assert "claude" in cmd
    assert mock_run.call_args.kwargs["check"] is True
    assert mock_run.call_args.kwargs["capture_output"] is True


@patch("src.router.session_spawner.subprocess.run")
def test_kill_tmux_session(mock_run) -> None:
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    assert kill_tmux_session("mesh-abc-s0") is True
    cmd = mock_run.call_args[0][0]
    assert cmd == ["tmux", "kill-session", "-t", "mesh-abc-s0"]

    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1)
    assert kill_tmux_session("nonexistent") is False


@patch("src.router.session_spawner.subprocess.run")
def test_is_session_alive(mock_run) -> None:
    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
    assert is_session_alive("mesh-abc-s0") is True

    mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1)
    assert is_session_alive("mesh-abc-s0") is False
