"""Tests for the Matrix notification bridge (Phase 19)."""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock

import pytest

# Import bridge components
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from importlib import import_module

bridge_mod = import_module("mesh-matrix-bridge")

BridgeConfig = bridge_mod.BridgeConfig
BridgeState = bridge_mod.BridgeState
RouterClient = bridge_mod.RouterClient
MatrixClient = bridge_mod.MatrixClient
TriggerDetector = bridge_mod.TriggerDetector
MatrixBridge = bridge_mod.MatrixBridge
render_attach_command = bridge_mod.render_attach_command
render_notification = bridge_mod.render_notification
load_repo_rooms = bridge_mod.load_repo_rooms


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_config(**overrides: Any) -> BridgeConfig:
    defaults = dict(
        router_url="http://localhost:8080",
        auth_token="test-token",
        matrix_homeserver="https://matrix.example.com",
        matrix_access_token="syt_test",
        matrix_default_room="!default:matrix.example",
        poll_interval_s=1.0,
        matrix_boss_room="!boss:matrix.example",
        input_patterns=[re.compile(r"approve|continue|press enter|y/n|select", re.IGNORECASE)],
        request_timeout_s=5.0,
    )
    defaults.update(overrides)
    return BridgeConfig(**defaults)


def make_session(
    session_id: str = "sess-001",
    state: str = "open",
    task_id: str | None = "task-001",
    attach_kind: str | None = "upterm",
    attach_target: str | None = "ssh://tok123@upterm.example:22",
    tmux_session: str | None = "mesh-claude-work-abc",
) -> dict:
    meta: dict[str, Any] = {}
    if tmux_session:
        meta["tmux_session"] = tmux_session
    if attach_kind:
        meta["attach_kind"] = attach_kind
    if attach_target:
        meta["attach_target"] = attach_target
    return {
        "session_id": session_id,
        "worker_id": "worker-001",
        "state": state,
        "task_id": task_id,
        "metadata": meta,
        "created_at": "2026-03-05T10:00:00Z",
        "updated_at": "2026-03-05T10:00:00Z",
    }


def make_message(
    seq: int,
    content: str,
    direction: str = "out",
    role: str = "cli",
) -> dict:
    return {
        "session_id": "sess-001",
        "direction": direction,
        "role": role,
        "content": content,
        "seq": seq,
        "ts": "2026-03-05T10:01:00Z",
    }


def make_task(
    task_id: str = "task-001",
    status: str = "review",
    title: str = "Review this change",
    repo: str | None = "rektslug",
    session_id: str | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "status": status,
        "title": title,
        "repo": repo,
        "session_id": session_id,
        "created_at": "2026-03-05T10:00:00Z",
        "updated_at": "2026-03-05T10:00:00Z",
    }


def make_thread(
    thread_id: str = "thread-001",
    name: str = "deploy-pipeline",
    status: str = "active",
) -> dict:
    return {
        "thread_id": thread_id,
        "name": name,
        "status": status,
        "created_at": "2026-03-05T10:00:00Z",
        "updated_at": "2026-03-05T10:00:00Z",
    }


# ===========================================================================
# Unit: render_attach_command
# ===========================================================================


class TestRenderAttachCommand:
    def test_upterm_target(self):
        session = make_session(attach_kind="upterm", attach_target="ssh://tok123@host:22")
        result = render_attach_command(session)
        assert result == "ssh tok123@host:22"

    def test_ssh_tmux_target(self):
        session = make_session(
            attach_kind="ssh_tmux",
            attach_target="ssh://user@host:22?tmux_session=mesh-work",
        )
        result = render_attach_command(session)
        assert "ssh -t" in result
        assert "tmux attach" in result

    def test_no_attach_kind(self):
        session = make_session(attach_kind=None, attach_target=None)
        result = render_attach_command(session)
        assert "tmux attach -t mesh-claude-work-abc" in result

    def test_no_metadata(self):
        session = {"session_id": "x", "metadata": {}}
        result = render_attach_command(session)
        assert "no attach handle" in result

    def test_missing_metadata_key(self):
        session = {"session_id": "x"}
        result = render_attach_command(session)
        assert "no attach handle" in result


# ===========================================================================
# Unit: render_notification
# ===========================================================================


class TestRenderNotification:
    def test_input_requested(self):
        session = make_session()
        plain, html = render_notification(
            "input_requested",
            repo="rektslug",
            session=session,
            excerpt="Do you want to approve? (y/n)",
        )
        assert "Input Requested" in plain
        assert "rektslug" in plain
        assert "ssh tok123@upterm.example:22" in plain
        assert "quick text reply" in plain.lower()
        assert "<b>Input Requested</b>" in html

    def test_approval_needed(self):
        task = make_task()
        plain, _html = render_notification("approval_needed", task=task)
        assert "Approval Needed" in plain
        assert task["task_id"][:12] in plain

    def test_thread_failed(self):
        thread = make_thread(status="failed")
        plain, _html = render_notification("thread_failed", thread=thread)
        assert "Thread Failed" in plain
        assert "deploy-pipeline" in plain

    def test_thread_completed(self):
        thread = make_thread(status="completed")
        plain, _html = render_notification("thread_completed", thread=thread)
        assert "Thread Completed" in plain


# ===========================================================================
# Unit: trigger detection (input_requested)
# ===========================================================================


class TestTriggerDetectorInputRequested:
    def setup_method(self):
        self.config = make_config()
        self.router = MagicMock(spec=RouterClient)
        self.state = BridgeState()
        self.detector = TriggerDetector(self.config, self.router, self.state)

    def test_detects_input_pattern_in_outbound_message(self):
        session = make_session()
        self.router.get_sessions.return_value = [session]
        self.router.get_session_messages.return_value = [
            make_message(1, "Please approve the deployment (y/n)"),
        ]
        self.router.get_tasks.return_value = []
        self.router.get_threads.return_value = []

        notifications = self.detector.poll()
        input_notifs = [n for n in notifications if n["trigger"] == "input_requested"]
        assert len(input_notifs) == 1
        assert "approve" in input_notifs[0]["excerpt"].lower()

    def test_ignores_inbound_messages(self):
        session = make_session()
        self.router.get_sessions.return_value = [session]
        self.router.get_session_messages.return_value = [
            make_message(1, "approve this", direction="in"),
        ]
        self.router.get_tasks.return_value = []
        self.router.get_threads.return_value = []

        notifications = self.detector.poll()
        input_notifs = [n for n in notifications if n["trigger"] == "input_requested"]
        assert len(input_notifs) == 0

    def test_respects_after_seq_cursor(self):
        session = make_session()
        self.state.session_seqs["sess-001"] = 5
        self.router.get_sessions.return_value = [session]
        self.router.get_session_messages.return_value = [
            make_message(6, "continue?"),
        ]
        self.router.get_tasks.return_value = []
        self.router.get_threads.return_value = []

        notifications = self.detector.poll()
        # Verify after_seq was passed
        self.router.get_session_messages.assert_called_once_with("sess-001", after_seq=5)
        assert self.state.session_seqs["sess-001"] == 6

    def test_no_duplicate_for_same_message(self):
        session = make_session()
        self.router.get_sessions.return_value = [session]
        self.router.get_session_messages.return_value = [
            make_message(1, "approve this?"),
        ]
        self.router.get_tasks.return_value = []
        self.router.get_threads.return_value = []

        self.detector.poll()
        # Second poll: no new messages
        self.router.get_session_messages.return_value = []
        notifications = self.detector.poll()
        input_notifs = [n for n in notifications if n["trigger"] == "input_requested"]
        assert len(input_notifs) == 0

    def test_no_match_for_normal_output(self):
        session = make_session()
        self.router.get_sessions.return_value = [session]
        self.router.get_session_messages.return_value = [
            make_message(1, "Compiling src/main.rs... done."),
        ]
        self.router.get_tasks.return_value = []
        self.router.get_threads.return_value = []

        notifications = self.detector.poll()
        input_notifs = [n for n in notifications if n["trigger"] == "input_requested"]
        assert len(input_notifs) == 0


# ===========================================================================
# Unit: trigger detection (approval_needed)
# ===========================================================================


class TestTriggerDetectorApprovalNeeded:
    def setup_method(self):
        self.config = make_config()
        self.router = MagicMock(spec=RouterClient)
        self.state = BridgeState()
        self.detector = TriggerDetector(self.config, self.router, self.state)

    def test_detects_new_review_task(self):
        self.router.get_sessions.return_value = []
        self.router.get_session_messages.return_value = []
        self.router.get_tasks.return_value = [make_task(task_id="task-new", status="review")]
        self.router.get_threads.return_value = []

        notifications = self.detector.poll()
        approval_notifs = [n for n in notifications if n["trigger"] == "approval_needed"]
        assert len(approval_notifs) == 1
        assert approval_notifs[0]["task"]["task_id"] == "task-new"

    def test_no_duplicate_for_already_known_review(self):
        self.state.review_task_ids = {"task-old"}
        self.router.get_sessions.return_value = []
        self.router.get_session_messages.return_value = []
        self.router.get_tasks.return_value = [make_task(task_id="task-old", status="review")]
        self.router.get_threads.return_value = []

        notifications = self.detector.poll()
        approval_notifs = [n for n in notifications if n["trigger"] == "approval_needed"]
        assert len(approval_notifs) == 0

    def test_clears_resolved_reviews(self):
        self.state.review_task_ids = {"task-resolved"}
        self.router.get_sessions.return_value = []
        self.router.get_session_messages.return_value = []
        self.router.get_tasks.return_value = []  # task no longer in review
        self.router.get_threads.return_value = []

        self.detector.poll()
        assert "task-resolved" not in self.state.review_task_ids


# ===========================================================================
# Unit: trigger detection (thread transitions)
# ===========================================================================


class TestTriggerDetectorThreadTransitions:
    def setup_method(self):
        self.config = make_config()
        self.router = MagicMock(spec=RouterClient)
        self.state = BridgeState()
        self.detector = TriggerDetector(self.config, self.router, self.state)

    def test_detects_thread_failed(self):
        self.state.thread_statuses["t1"] = "active"
        self.router.get_sessions.return_value = []
        self.router.get_session_messages.return_value = []
        self.router.get_tasks.return_value = []
        self.router.get_threads.return_value = [make_thread(thread_id="t1", status="failed")]

        notifications = self.detector.poll()
        thread_notifs = [n for n in notifications if n["trigger"] == "thread_failed"]
        assert len(thread_notifs) == 1

    def test_detects_thread_completed(self):
        self.state.thread_statuses["t1"] = "active"
        self.router.get_sessions.return_value = []
        self.router.get_session_messages.return_value = []
        self.router.get_tasks.return_value = []
        self.router.get_threads.return_value = [make_thread(thread_id="t1", status="completed")]

        notifications = self.detector.poll()
        thread_notifs = [n for n in notifications if n["trigger"] == "thread_completed"]
        assert len(thread_notifs) == 1

    def test_no_notification_for_same_status(self):
        self.state.thread_statuses["t1"] = "active"
        self.router.get_sessions.return_value = []
        self.router.get_session_messages.return_value = []
        self.router.get_tasks.return_value = []
        self.router.get_threads.return_value = [make_thread(thread_id="t1", status="active")]

        notifications = self.detector.poll()
        thread_notifs = [n for n in notifications if "thread_" in n["trigger"]]
        assert len(thread_notifs) == 0

    def test_ignores_non_terminal_transitions(self):
        """pending -> active should NOT trigger a notification."""
        self.state.thread_statuses["t1"] = "pending"
        self.router.get_sessions.return_value = []
        self.router.get_session_messages.return_value = []
        self.router.get_tasks.return_value = []
        self.router.get_threads.return_value = [make_thread(thread_id="t1", status="active")]

        notifications = self.detector.poll()
        assert len(notifications) == 0

    def test_detects_thread_blocked(self):
        self.state.thread_statuses["t1"] = "active"
        self.router.get_sessions.return_value = []
        self.router.get_session_messages.return_value = []
        self.router.get_tasks.return_value = []
        self.router.get_threads.return_value = [make_thread(thread_id="t1", status="blocked")]

        notifications = self.detector.poll()
        blocked = [n for n in notifications if n["trigger"] == "thread_blocked"]
        assert len(blocked) == 1


# ===========================================================================
# Unit: de-duplication
# ===========================================================================


class TestDeduplication:
    def setup_method(self):
        self.config = make_config()
        self.router = MagicMock(spec=RouterClient)
        self.state = BridgeState()
        self.detector = TriggerDetector(self.config, self.router, self.state)

    def test_thread_status_not_retriggered(self):
        """Once notified for failed, same status should not re-trigger."""
        self.state.thread_statuses["t1"] = "active"
        self.router.get_sessions.return_value = []
        self.router.get_session_messages.return_value = []
        self.router.get_tasks.return_value = []
        self.router.get_threads.return_value = [make_thread(thread_id="t1", status="failed")]

        # First poll triggers
        notifs1 = self.detector.poll()
        assert len([n for n in notifs1 if n["trigger"] == "thread_failed"]) == 1

        # Second poll does NOT re-trigger
        notifs2 = self.detector.poll()
        assert len([n for n in notifs2 if n["trigger"] == "thread_failed"]) == 0


# ===========================================================================
# Integration: MatrixBridge.run_once
# ===========================================================================


class TestBridgeRunOnce:
    def _make_bridge(self):
        config = make_config()
        bridge = MatrixBridge(config)
        mock_router = MagicMock(spec=RouterClient)
        bridge.router = mock_router
        bridge.detector = TriggerDetector(config, mock_router, bridge.state)
        bridge.matrix = MagicMock(spec=MatrixClient)
        return bridge

    def test_full_cycle_with_input_requested(self):
        bridge = self._make_bridge()
        bridge.matrix.send_message.return_value = True

        session = make_session()
        bridge.router.get_sessions.return_value = [session]
        bridge.router.get_session_messages.return_value = [
            make_message(1, "Do you approve? (y/n)"),
        ]
        bridge.router.get_tasks.return_value = []
        bridge.router.get_threads.return_value = []

        sent = bridge.run_once()
        assert sent == 1
        bridge.matrix.send_message.assert_called_once()
        call_args = bridge.matrix.send_message.call_args
        assert call_args[0][0] == "!default:matrix.example"  # default room
        assert "Input Requested" in call_args[0][1]

    def test_boss_room_receives_critical_notifications(self):
        bridge = self._make_bridge()
        bridge.matrix.send_message.return_value = True

        bridge.router.get_sessions.return_value = []
        bridge.router.get_session_messages.return_value = []
        bridge.router.get_tasks.return_value = [make_task(task_id="t-new")]
        bridge.router.get_threads.return_value = []

        sent = bridge.run_once()
        assert sent == 1
        # Default room + boss room
        assert bridge.matrix.send_message.call_count == 2
        rooms_called = [c[0][0] for c in bridge.matrix.send_message.call_args_list]
        assert "!boss:matrix.example" in rooms_called

    def test_seed_state_prevents_startup_spam(self):
        bridge = self._make_bridge()

        # Existing state on router
        bridge.router.get_sessions.return_value = [make_session()]
        bridge.router.get_session_messages.return_value = [make_message(10, "old stuff")]
        bridge.router.get_tasks.return_value = [make_task(task_id="existing-review")]
        bridge.router.get_threads.return_value = [make_thread(thread_id="t1", status="active")]

        bridge._seed_state()

        # Now poll: same state -> no notifications
        bridge.router.get_session_messages.return_value = []  # no new messages after seed
        bridge.matrix.send_message.return_value = True
        sent = bridge.run_once()
        assert sent == 0


# ===========================================================================
# Unit: load_repo_rooms
# ===========================================================================


class TestLoadRepoRooms:
    def test_returns_empty_for_missing_file(self):
        assert load_repo_rooms(None) == {}
        assert load_repo_rooms("/nonexistent") == {}

    def test_loads_rooms_from_topology(self, tmp_path):
        topo = tmp_path / "topology.yml"
        topo.write_text(
            "version: 1\n"
            "global:\n"
            "  boss_notify_room: '!boss:m'\n"
            "repos:\n"
            "  myrepo:\n"
            "    notify_room: '!myrepo:m'\n"
        )
        rooms = load_repo_rooms(str(topo))
        assert rooms["myrepo"] == "!myrepo:m"
        assert rooms["__boss__"] == "!boss:m"


# ===========================================================================
# Unit: BridgeConfig.from_env
# ===========================================================================


class TestBridgeConfigFromEnv:
    def test_missing_required_var(self):
        with pytest.raises(SystemExit, match="MESH_ROUTER_URL"):
            BridgeConfig.from_env()

    def test_valid_config(self, monkeypatch):
        env = {
            "MESH_ROUTER_URL": "http://router:8080",
            "MESH_AUTH_TOKEN": "tok",
            "MESH_MATRIX_HOMESERVER": "https://matrix.example.com",
            "MESH_MATRIX_ACCESS_TOKEN": "syt_test",
            "MESH_MATRIX_DEFAULT_ROOM": "!room:example",
            "MESH_MATRIX_POLL_INTERVAL_S": "5",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        cfg = BridgeConfig.from_env()
        assert cfg.router_url == "http://router:8080"
        assert cfg.poll_interval_s == 5.0
        assert cfg.matrix_boss_room is None


# ===========================================================================
# Smoke: bridge import
# ===========================================================================


class TestSmoke:
    def test_bridge_module_imports(self):
        assert hasattr(bridge_mod, "MatrixBridge")
        assert hasattr(bridge_mod, "main")
