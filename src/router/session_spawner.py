"""Tmux session spawner for mesh thread steps.

Spawns isolated tmux sessions for each thread step. The router process
and workers share the same machine (single-VPS topology).
"""

from __future__ import annotations

import re
import shlex
import subprocess


_VALID_SESSION_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


def _sanitize_session_name(name: str) -> str:
    """Sanitize a tmux session name to contain only [a-zA-Z0-9_-]."""
    if _VALID_SESSION_NAME.match(name):
        return name
    return re.sub(r"[^a-zA-Z0-9_-]", "", name)


def spawn_tmux_session(
    thread_id: str,
    step_index: int,
    cli_command: str,
    work_dir: str = "",
) -> str:
    """Spawn a tmux session for a thread step.

    Session name: mesh-{thread_id[:8]}-s{step_index}

    Args:
        thread_id: Thread UUID.
        step_index: Step index within the thread.
        cli_command: CLI command to run in the session.
        work_dir: Working directory for the session.

    Returns:
        The sanitized session name.

    Raises:
        RuntimeError: If tmux fails to create the session.
    """
    session_name = _sanitize_session_name(f"mesh-{thread_id[:8]}-s{step_index}")

    cmd: list[str] = ["tmux", "new-session", "-d", "-s", session_name]

    if work_dir:
        cmd.extend(["-c", work_dir])

    if cli_command:
        cmd.extend(shlex.split(cli_command))

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=10)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Failed to spawn tmux session {session_name}: {e.stderr.decode()}"
        ) from e

    return session_name


def kill_tmux_session(session_name: str) -> bool:
    """Kill a tmux session by name.

    Returns True if killed, False if session was not found.
    """
    result = subprocess.run(
        ["tmux", "kill-session", "-t", session_name],
        capture_output=True,
        timeout=5,
    )
    return result.returncode == 0


def is_session_alive(session_name: str) -> bool:
    """Check if a tmux session is alive.

    Returns True if the session exists, False otherwise.
    """
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
        timeout=5,
    )
    return result.returncode == 0
