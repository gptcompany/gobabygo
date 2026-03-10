"""Unit tests for the interactive session worker."""

from __future__ import annotations

import json
import os
import subprocess
import requests
from unittest.mock import MagicMock, Mock, call, mock_open, patch

import pytest

from src.router.session_worker import (
    MeshSessionWorker,
    SessionNotFoundError,
    SessionWorkerConfig,
    _capture_shows_activity,
    _capture_contains_prompt_text,
    _coerce_bool,
    _coerce_string_list,
    _compute_output_emit,
    _detect_interactive_failure_screen,
    _discover_project_mcp_servers,
    _last_prompt_line_has_content,
    _looks_like_start_screen,
    _parse_upterm_ssh_url,
    _prompt_is_idle,
    _sanitize_session_name,
    _success_file_matches,
    _should_auto_exit_on_success,
)


# ---------------------------------------------------------------------------
# Existing tests (unchanged)
# ---------------------------------------------------------------------------


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
        "MESH_CAPABILITIES": "interactive,code",
        "MESH_ALLOWED_ACCOUNTS": "work-claude,review-codex,*",
        "MESH_HEARTBEAT_TIMEOUT_S": "4.5",
        "MESH_CONTROL_PLANE_TIMEOUT_S": "22",
        "MESH_SESSION_POLL_INTERVAL_S": "0.5",
        "MESH_SESSION_READY_TIMEOUT_S": "7",
        "MESH_SESSION_READY_POLL_INTERVAL_S": "0.2",
        "MESH_TMUX_SEND_SETTLE_S": "0.15",
        "MESH_PROMPT_SUBMIT_RETRY_COUNT": "4",
        "MESH_PROMPT_SUBMIT_RETRY_POLL_S": "0.4",
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
    assert cfg.capabilities == ["interactive", "code"]
    assert cfg.allowed_accounts == ["work-claude", "review-codex", "*"]
    assert cfg.heartbeat_timeout == 4.5
    assert cfg.control_plane_timeout == 22.0
    assert cfg.session_poll_interval_s == 0.5
    assert cfg.startup_ready_timeout_s == 7.0
    assert cfg.startup_ready_poll_interval_s == 0.2
    assert cfg.tmux_send_settle_s == 0.15
    assert cfg.prompt_submit_retry_count == 4
    assert cfg.prompt_submit_retry_poll_s == 0.4
    assert cfg.tmux_session_prefix == "meshx"


def test_session_worker_registration_capabilities_with_allowed_accounts() -> None:
    cfg = SessionWorkerConfig(
        capabilities=["interactive"],
        allowed_accounts=["work-claude", "*", "work-claude"],
    )
    caps = cfg.registration_capabilities()
    assert "interactive" in caps
    assert "account:work-claude" in caps
    assert "account:*" in caps
    assert caps.count("account:work-claude") == 1


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


def test_last_prompt_line_has_content_detects_pending_composer() -> None:
    assert _last_prompt_line_has_content("header\n❯ Execute spec") is True
    assert _last_prompt_line_has_content("header\n❯ Execute spec\n✻ Herding…\n❯ ") is False
    assert _last_prompt_line_has_content("no prompt here") is False


def test_detect_interactive_failure_screen_detects_rate_limit_menu() -> None:
    captured = (
        "You've hit your limit\n"
        "❯ /rate-limit-options\n"
        "What do you want to do?\n"
        "1. Stop and wait for limit to reset\n"
    )
    assert _detect_interactive_failure_screen("claude", captured) == "account_exhausted"
    assert _detect_interactive_failure_screen("codex", captured) == ""


def test_coerce_bool() -> None:
    assert _coerce_bool(True) is True
    assert _coerce_bool("1") is True
    assert _coerce_bool("yes") is True
    assert _coerce_bool("0") is False
    assert _coerce_bool(None, default=True) is True


def test_coerce_string_list() -> None:
    assert _coerce_string_list("GEMINI_OK") == ["GEMINI_OK"]
    assert _coerce_string_list(["A", " ", "B"]) == ["A", "B"]
    assert _coerce_string_list(None) == []


def test_prompt_is_idle_and_auto_exit_success_detection() -> None:
    captured = "❯ Reply\n\n● GEMINI_OK\n\n❯ "
    assert _prompt_is_idle(captured) is True
    assert _should_auto_exit_on_success(captured, ["GEMINI_OK"], delta_text="● GEMINI_OK") is True
    assert _should_auto_exit_on_success(captured, ["OTHER"], delta_text="● GEMINI_OK") is False


def test_auto_exit_success_detection_ignores_marker_present_only_in_prompt() -> None:
    baseline = "❯ Reply with exactly GEMINI_E2E_OK.\n\n❯ "
    captured = baseline
    assert (
        _should_auto_exit_on_success(
            captured,
            ["GEMINI_E2E_OK"],
            baseline_capture=baseline,
            delta_text="",
        )
        is False
    )
    assert (
        _should_auto_exit_on_success(
            "❯ Reply with exactly GEMINI_E2E_OK.\n\n● GEMINI_E2E_OK\n\n❯ ",
            ["GEMINI_E2E_OK"],
            baseline_capture=baseline,
            delta_text="● GEMINI_E2E_OK",
        )
        is True
    )


def test_capture_contains_prompt_text_normalizes_whitespace() -> None:
    captured = "❯  Inside this repository, create a file named GEMINI_E2E_OK.md\n\n❯ "
    prompt = (
        "Inside this repository, create a file named GEMINI_E2E_OK.md containing exactly one line:\n"
        "GEMINI_FILE_OK."
    )
    assert _capture_contains_prompt_text(captured, prompt) is True


def test_looks_like_start_screen_accepts_partial_claude_home_capture() -> None:
    captured = "Welcome back gpt!\n\n  /model to try Opus 4.6\n\n❯ Try \"fix typecheck errors\""
    assert _looks_like_start_screen(captured) is True


def test_capture_shows_activity_for_tool_and_flowing_output() -> None:
    captured = (
        "Welcome back gpt!\n"
        "● Write(GEMINI_E2E_OK.md)\n"
        "⎿ \n"
        "· Flowing…\n"
        "❯ Press up to edit queued messages"
    )
    assert _capture_shows_activity(captured) is True
    assert _looks_like_start_screen(captured) is False


def test_success_file_matches_relative_path_and_contents(tmp_path) -> None:
    work_dir = str(tmp_path)
    file_path = tmp_path / "GEMINI_E2E_OK.md"
    file_path.write_text("GEMINI_FILE_OK\n", encoding="utf-8")
    assert _success_file_matches(work_dir, "GEMINI_E2E_OK.md") is True
    assert _success_file_matches(work_dir, "GEMINI_E2E_OK.md", "GEMINI_FILE_OK") is True
    assert _success_file_matches(work_dir, "GEMINI_E2E_OK.md", "OTHER") is False


# ---------------------------------------------------------------------------
# Config env for attach fields
# ---------------------------------------------------------------------------


def test_config_from_env_attach_fields() -> None:
    env = {
        "MESH_UPTERM_BIN": "/usr/local/bin/upterm",
        "MESH_UPTERM_SERVER": "ssh://uptermd.example.com:22",
        "MESH_UPTERM_READY_TIMEOUT": "5.0",
        "MESH_UPTERM_ACCEPT": "0",
        "MESH_UPTERM_SKIP_HOST_KEY_CHECK": "0",
        "MESH_RUNTIME_STATE_DIR": "/var/lib/mesh-runtime",
        "MESH_SSH_TMUX_USER": "operator",
        "MESH_SSH_TMUX_HOST": "10.0.0.5",
    }
    with patch.dict(os.environ, env, clear=False):
        cfg = SessionWorkerConfig.from_env()
    assert cfg.upterm_bin == "/usr/local/bin/upterm"
    assert cfg.upterm_server == "ssh://uptermd.example.com:22"
    assert cfg.upterm_ready_timeout == 5.0
    assert cfg.upterm_accept is False
    assert cfg.upterm_skip_host_key_check is False
    assert cfg.runtime_state_dir == "/var/lib/mesh-runtime"
    assert cfg.ssh_tmux_user == "operator"
    assert cfg.ssh_tmux_host == "10.0.0.5"


def test_config_defaults_attach_fields() -> None:
    cfg = SessionWorkerConfig()
    assert cfg.upterm_bin == "upterm"
    assert cfg.upterm_server == ""
    assert cfg.upterm_ready_timeout == 10.0
    assert cfg.upterm_accept is True
    assert cfg.upterm_skip_host_key_check is True
    assert cfg.runtime_state_dir.endswith(".cache/gobabygo")
    assert cfg.ssh_tmux_user == ""
    assert cfg.ssh_tmux_host == ""


def test_wait_for_cli_ready_detects_prompt() -> None:
    worker = _make_worker(startup_ready_timeout_s=0.5, startup_ready_poll_interval_s=0.1)
    with (
        patch.object(worker, "_tmux_capture_pane", side_effect=["booting", "╭─── Claude Code", "❯ "]),
        patch("src.router.session_worker.time.sleep"),
    ):
        assert worker._wait_for_cli_ready("mysess") is True


def test_wait_for_cli_ready_times_out_without_prompt() -> None:
    worker = _make_worker(startup_ready_timeout_s=0.3, startup_ready_poll_interval_s=0.1)
    with (
        patch.object(worker, "_tmux_capture_pane", return_value="booting"),
        patch("src.router.session_worker.time.sleep"),
    ):
        assert worker._wait_for_cli_ready("mysess") is False


def test_control_plane_operations_use_configured_timeout() -> None:
    worker = _make_worker(control_plane_timeout=17.5)
    worker._http = MagicMock()
    ok_resp = MagicMock(status_code=200)
    ok_resp.json.return_value = {"session": {"session_id": "sid-timeout"}}
    worker._http.post.return_value = ok_resp
    worker._http.get.return_value = MagicMock(status_code=200, json=Mock(return_value={"messages": []}))

    worker._register()
    worker._deregister()
    assert worker._ack_task("task-1") is True
    worker._report_complete("task-1", {"ok": True})
    worker._report_failure("task-1", "boom")
    assert worker._open_session({"task_id": "task-1", "title": "t"}, "mesh-sess", "/tmp/work", "claude-rektslug") == "sid-timeout"
    worker._send_session_message("sid-timeout", direction="system", role="system", content="hello")
    worker._close_session("sid-timeout")
    assert worker._list_session_messages("sid-timeout", after_seq=0, limit=50) == []

    post_timeouts = [call.kwargs["timeout"] for call in worker._http.post.call_args_list]
    get_timeouts = [call.kwargs["timeout"] for call in worker._http.get.call_args_list]
    assert post_timeouts == [17.5] * len(post_timeouts)
    assert get_timeouts == [17.5]


def test_report_failure_includes_error_kind() -> None:
    worker = _make_worker(control_plane_timeout=17.5)
    worker._http = MagicMock()
    worker._http.post.return_value = MagicMock(status_code=200)

    worker._report_failure("task-1", "boom", error_kind="account_exhausted")

    body = worker._http.post.call_args.kwargs["json"]
    assert body["task_id"] == "task-1"
    assert body["error_kind"] == "account_exhausted"


# ---------------------------------------------------------------------------
# _parse_upterm_ssh_url
# ---------------------------------------------------------------------------


def test_parse_upterm_ssh_url_standard() -> None:
    output = (
        "=== abc123 ===\n"
        "Command:                bash\n"
        "Force Command:          tmux attach -t mesh-xxx\n"
        "Host:                   ssh://TOKEN@uptermd.upterm.dev:22\n"
    )
    assert _parse_upterm_ssh_url(output) == "ssh://TOKEN@uptermd.upterm.dev:22"


def test_parse_upterm_ssh_url_no_match() -> None:
    assert _parse_upterm_ssh_url("nothing here") is None


def test_parse_upterm_ssh_url_empty() -> None:
    assert _parse_upterm_ssh_url("") is None


def test_parse_upterm_ssh_url_inline() -> None:
    output = "SSH: ssh://abc@host:2222 (some trailing text)"
    assert _parse_upterm_ssh_url(output) == "ssh://abc@host:2222"


def test_discover_project_mcp_servers(tmp_path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"playwright": {}, "serena": {}}}),
        encoding="utf-8",
    )
    assert _discover_project_mcp_servers(str(tmp_path)) == ["playwright", "serena"]


def test_discover_project_mcp_servers_invalid_json(tmp_path) -> None:
    (tmp_path / ".mcp.json").write_text("{not json", encoding="utf-8")
    assert _discover_project_mcp_servers(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worker(**overrides) -> MeshSessionWorker:
    cfg = SessionWorkerConfig(
        worker_id="ws-test-01",
        router_url="http://localhost:8780",
        runtime_state_dir="/tmp/mesh-runtime-tests",
        upterm_bin="/usr/bin/upterm",
        upterm_ready_timeout=0.6,
        provider_runtime_config="",
        **overrides,
    )
    return MeshSessionWorker(cfg)


def test_preseed_claude_state_file(tmp_path) -> None:
    state_path = tmp_path / ".claude.json"
    MeshSessionWorker._preseed_claude_state_file(
        str(state_path),
        "/media/sam/1TB/rektslug",
        ["playwright"],
    )
    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["hasCompletedOnboarding"] is True
    assert data["numStartups"] == 1
    project = data["projects"]["/media/sam/1TB/rektslug"]
    assert project["hasTrustDialogAccepted"] is True
    assert project["projectOnboardingSeenCount"] == 1
    assert project["enabledMcpjsonServers"] == ["playwright"]
    assert project["disabledMcpjsonServers"] == []


def test_preseed_claude_runtime_targets_global_and_instance(tmp_path) -> None:
    home_dir = tmp_path / "home"
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    (home_dir / ".ccs" / "instances" / "claude-rektslug").mkdir(parents=True)
    (work_dir / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"playwright": {}}}),
        encoding="utf-8",
    )
    worker = _make_worker(cli_type="claude")

    with patch("src.router.session_worker.os.path.expanduser", return_value=str(home_dir)):
        worker._preseed_claude_runtime(str(work_dir), "claude-rektslug")

    global_state = json.loads((home_dir / ".claude.json").read_text(encoding="utf-8"))
    instance_state = json.loads(
        (home_dir / ".ccs" / "instances" / "claude-rektslug" / ".claude.json").read_text(
            encoding="utf-8"
        )
    )
    assert global_state["projects"][str(work_dir)]["enabledMcpjsonServers"] == ["playwright"]
    assert instance_state["projects"][str(work_dir)]["hasTrustDialogAccepted"] is True


def test_preseed_claude_runtime_skips_missing_instance_dir(tmp_path) -> None:
    home_dir = tmp_path / "home"
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    worker = _make_worker(cli_type="claude")

    with patch("src.router.session_worker.os.path.expanduser", return_value=str(home_dir)):
        worker._preseed_claude_runtime(str(work_dir), "claude-missing")

    assert (home_dir / ".claude.json").exists()
    assert not (home_dir / ".ccs" / "instances" / "claude-missing" / ".claude.json").exists()


# ---------------------------------------------------------------------------
# _create_attach_handle
# ---------------------------------------------------------------------------


class TestCreateAttachHandle:
    """Test attach handle creation with upterm + fallback paths."""

    @patch.object(MeshSessionWorker, "_start_upterm")
    def test_upterm_success(self, mock_start: Mock) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        mock_start.return_value = (proc, "ssh://tok@host:22")
        worker = _make_worker()

        meta, p = worker._create_attach_handle("mesh-sess")

        assert meta == {"attach_kind": "upterm", "attach_target": "ssh://tok@host:22"}
        assert p is proc
        mock_start.assert_called_once_with("mesh-sess")

    @patch.object(MeshSessionWorker, "_start_upterm")
    def test_upterm_fail_fallback_ssh_tmux(self, mock_start: Mock) -> None:
        mock_start.return_value = (None, None)
        worker = _make_worker(ssh_tmux_user="op", ssh_tmux_host="10.0.0.5")

        meta, p = worker._create_attach_handle("mesh-sess")

        assert meta == {
            "attach_kind": "ssh_tmux",
            "attach_target": "ssh://op@10.0.0.5:22?tmux_session=mesh-sess",
        }
        assert p is None

    @patch.object(MeshSessionWorker, "_start_upterm")
    def test_no_attach_available(self, mock_start: Mock) -> None:
        mock_start.return_value = (None, None)
        worker = _make_worker()  # no ssh_tmux_user/host

        meta, p = worker._create_attach_handle("mesh-sess")

        assert meta is None
        assert p is None


# ---------------------------------------------------------------------------
# _start_upterm
# ---------------------------------------------------------------------------


class TestStartUpterm:

    @patch("src.router.session_worker.os.path.exists", return_value=True)
    @patch("src.router.session_worker.os.makedirs")
    @patch("src.router.session_worker.os.remove")
    @patch.object(MeshSessionWorker, "_poll_upterm_target")
    @patch("builtins.open", new_callable=mock_open)
    @patch("src.router.session_worker.subprocess.Popen")
    def test_success(
        self,
        mock_popen: Mock,
        mock_open_file: Mock,
        mock_poll: Mock,
        mock_remove: Mock,
        mock_makedirs: Mock,
        mock_exists: Mock,
    ) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        mock_popen.return_value = proc
        mock_poll.return_value = "ssh://tok@host:22"
        worker = _make_worker()
        expected_log_path = os.path.join(worker.config.runtime_state_dir, "upterm", "upterm-mesh-sess.log")

        p, target = worker._start_upterm("mesh-sess")

        assert p is proc
        assert target == "ssh://tok@host:22"
        # Verify forced command
        popen_args = mock_popen.call_args[0][0]
        assert "--accept" in popen_args
        assert "--skip-host-key-check" in popen_args
        assert "--force-command" in popen_args
        fc_idx = popen_args.index("--force-command")
        assert "tmux attach -t mesh-sess" in popen_args[fc_idx + 1]
        mock_exists.assert_called_once_with(expected_log_path)
        mock_remove.assert_called_once_with(expected_log_path)
        mock_poll.assert_called_once_with(expected_log_path, proc)

    @patch("src.router.session_worker.os.path.exists", return_value=False)
    @patch("src.router.session_worker.os.makedirs")
    @patch("src.router.session_worker.os.remove")
    @patch.object(MeshSessionWorker, "_poll_upterm_target")
    @patch("builtins.open", new_callable=mock_open)
    @patch("src.router.session_worker.subprocess.Popen")
    def test_success_without_stale_socket(
        self,
        mock_popen: Mock,
        mock_open_file: Mock,
        mock_poll: Mock,
        mock_remove: Mock,
        mock_makedirs: Mock,
        mock_exists: Mock,
    ) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        mock_popen.return_value = proc
        mock_poll.return_value = "ssh://tok@host:22"
        worker = _make_worker()
        expected_log_path = os.path.join(worker.config.runtime_state_dir, "upterm", "upterm-mesh-sess.log")

        p, target = worker._start_upterm("mesh-sess")

        assert p is proc
        assert target == "ssh://tok@host:22"
        mock_exists.assert_called_once_with(expected_log_path)
        mock_remove.assert_not_called()
        mock_poll.assert_called_once_with(expected_log_path, proc)

    @patch.object(MeshSessionWorker, "_poll_upterm_target")
    @patch("src.router.session_worker.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    @patch("src.router.session_worker.subprocess.Popen")
    def test_with_server_flag(
        self, mock_popen: Mock, mock_open_file: Mock, mock_makedirs: Mock, mock_poll: Mock
    ) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        mock_popen.return_value = proc
        mock_poll.return_value = "ssh://tok@host:22"
        worker = _make_worker(upterm_server="ssh://custom:22")

        worker._start_upterm("mesh-sess")

        popen_args = mock_popen.call_args[0][0]
        assert "--server" in popen_args
        assert "ssh://custom:22" in popen_args

    @patch.object(MeshSessionWorker, "_poll_upterm_target")
    @patch("src.router.session_worker.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    @patch("src.router.session_worker.subprocess.Popen")
    def test_can_disable_accept_and_host_key_skip(
        self, mock_popen: Mock, mock_open_file: Mock, mock_makedirs: Mock, mock_poll: Mock
    ) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        mock_popen.return_value = proc
        mock_poll.return_value = "ssh://tok@host:22"
        worker = _make_worker(upterm_accept=False, upterm_skip_host_key_check=False)

        worker._start_upterm("mesh-sess")

        popen_args = mock_popen.call_args[0][0]
        assert "--accept" not in popen_args
        assert "--skip-host-key-check" not in popen_args

    @patch("src.router.session_worker.os.makedirs")
    @patch("src.router.session_worker.subprocess.Popen", side_effect=FileNotFoundError)
    def test_binary_not_found(self, mock_popen: Mock, mock_makedirs: Mock) -> None:
        worker = _make_worker()
        p, target = worker._start_upterm("mesh-sess")
        assert p is None
        assert target is None

    @patch("src.router.session_worker.os.makedirs")
    @patch("src.router.session_worker.subprocess.Popen", side_effect=PermissionError("denied"))
    def test_launch_oserror_logs_actual_error(
        self, mock_popen: Mock, mock_makedirs: Mock, caplog: pytest.LogCaptureFixture
    ) -> None:
        worker = _make_worker()
        with caplog.at_level("WARNING"):
            p, target = worker._start_upterm("mesh-sess")
        assert p is None
        assert target is None
        assert "upterm launch failed" in caplog.text
        assert "denied" in caplog.text

    @patch.object(MeshSessionWorker, "_stop_upterm")
    @patch.object(MeshSessionWorker, "_poll_upterm_target", return_value=None)
    @patch("src.router.session_worker.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    @patch("src.router.session_worker.subprocess.Popen")
    def test_poll_timeout_kills_process(
        self,
        mock_popen: Mock,
        mock_open_file: Mock,
        mock_makedirs: Mock,
        mock_poll: Mock,
        mock_stop: Mock,
    ) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        mock_popen.return_value = proc
        worker = _make_worker()
        expected_log_path = os.path.join(worker.config.runtime_state_dir, "upterm", "upterm-mesh-sess.log")

        p, target = worker._start_upterm("mesh-sess")

        assert p is None
        assert target is None
        mock_stop.assert_called_once_with(proc, log_path=expected_log_path)


# ---------------------------------------------------------------------------
# _stop_upterm
# ---------------------------------------------------------------------------


class TestStopUpterm:

    def test_already_exited(self) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 0  # already exited
        MeshSessionWorker._stop_upterm(proc)
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()

    def test_terminate_success(self) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None  # still running
        proc.wait.return_value = 0
        MeshSessionWorker._stop_upterm(proc)
        proc.terminate.assert_called_once()
        proc.kill.assert_not_called()

    def test_terminate_timeout_then_kill(self) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = None
        proc.wait.side_effect = [subprocess.TimeoutExpired("upterm", 3), None]
        MeshSessionWorker._stop_upterm(proc)
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    @patch("src.router.session_worker.os.path.exists", return_value=True)
    @patch("src.router.session_worker.os.remove")
    def test_log_cleanup(self, mock_remove: Mock, mock_exists: Mock) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 0
        MeshSessionWorker._stop_upterm(proc, "/tmp/test.log")
        mock_exists.assert_called_once_with("/tmp/test.log")
        mock_remove.assert_called_once_with("/tmp/test.log")


# ---------------------------------------------------------------------------
# _open_session metadata merging
# ---------------------------------------------------------------------------


class TestOpenSessionMetadata:

    def test_metadata_includes_attach_info(self) -> None:
        worker = _make_worker()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"session": {"session_id": "s-123"}}
        worker._http = MagicMock()
        worker._http.post.return_value = mock_resp

        task = {"task_id": "t-abc", "title": "test task"}
        attach_meta = {"attach_kind": "upterm", "attach_target": "ssh://tok@host:22"}

        sid = worker._open_session(task, "mesh-sess", "/tmp/work", "review-codex", attach_meta)

        assert sid == "s-123"
        posted = worker._http.post.call_args
        body = posted[1]["json"] if "json" in posted[1] else posted[0][1]
        meta = body["metadata"]
        assert meta["tmux_session"] == "mesh-sess"
        assert meta["working_dir"] == "/tmp/work"
        assert meta["task_title"] == "test task"
        assert meta["attach_kind"] == "upterm"
        assert meta["attach_target"] == "ssh://tok@host:22"
        assert body["account_profile"] == "review-codex"

    def test_metadata_without_attach(self) -> None:
        worker = _make_worker()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session": {"session_id": "s-456"}}
        worker._http = MagicMock()
        worker._http.post.return_value = mock_resp

        task = {"task_id": "t-def", "title": "no attach"}

        sid = worker._open_session(task, "mesh-sess", "/tmp/work", "work-claude", None)

        assert sid == "s-456"
        posted = worker._http.post.call_args
        body = posted[1]["json"] if "json" in posted[1] else posted[0][1]
        meta = body["metadata"]
        assert "attach_kind" not in meta
        assert "attach_target" not in meta
        # Core fields still present
        assert meta["tmux_session"] == "mesh-sess"
        assert meta["working_dir"] == "/tmp/work"
        assert body["account_profile"] == "work-claude"


# ---------------------------------------------------------------------------
# _poll_upterm_target
# ---------------------------------------------------------------------------


class TestPollUptermTarget:

    @patch("src.router.session_worker.time.sleep")
    @patch("src.router.session_worker.time.monotonic")
    def test_success_on_second_poll(
        self, mock_mono: Mock, mock_sleep: Mock, tmp_path
    ) -> None:
        mock_mono.side_effect = [0.0, 0.1, 0.5, 99.0]
        worker = _make_worker()
        log_path = tmp_path / "upterm.log"
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.side_effect = [None, None, None]

        def write_target(*args, **kwargs):
            log_path.write_text("Host: ssh://tok@uptermd:22\n", encoding="utf-8")

        mock_sleep.side_effect = write_target

        target = worker._poll_upterm_target(str(log_path), proc)

        assert target == "ssh://tok@uptermd:22"

    @patch("src.router.session_worker.time.sleep")
    @patch("src.router.session_worker.time.monotonic")
    def test_timeout(
        self, mock_mono: Mock, mock_sleep: Mock, tmp_path
    ) -> None:
        mock_mono.side_effect = [0.0, 99.0]
        worker = _make_worker()

        target = worker._poll_upterm_target(str(tmp_path / "missing.log"))

        assert target is None

    @patch("src.router.session_worker.time.sleep")
    @patch("src.router.session_worker.time.monotonic")
    def test_returns_none_if_process_exits_without_url(
        self, mock_mono: Mock, mock_sleep: Mock, tmp_path
    ) -> None:
        mock_mono.side_effect = [0.0, 0.1]
        worker = _make_worker()
        log_path = tmp_path / "upterm.log"
        log_path.write_text("starting...\n", encoding="utf-8")
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 1

        assert worker._poll_upterm_target(str(log_path), proc) is None


# ---------------------------------------------------------------------------
# Integration: upterm cleanup in _execute_task finally block
# ---------------------------------------------------------------------------


class TestAttachCleanupInExecuteTask:
    """Verify upterm process is always cleaned up."""

    def _setup_worker(self) -> tuple[MeshSessionWorker, MagicMock]:
        """Return a worker with mocked HTTP and tmux."""
        worker = _make_worker()
        http = MagicMock()
        worker._http = http

        # ack succeeds
        ack_resp = MagicMock(status_code=200)
        # open succeeds
        open_resp = MagicMock()
        open_resp.json.return_value = {"session": {"session_id": "s-99"}}
        # send, close, list, complete, fail succeed
        ok_resp = MagicMock(status_code=200)
        ok_resp.json.return_value = {"messages": []}

        http.post.return_value = ok_resp
        http.get.return_value = ok_resp

        # Override specific endpoints
        def post_dispatch(url, **kw):
            if "/tasks/ack" in url:
                return ack_resp
            if "/sessions/open" in url:
                return open_resp
            return ok_resp

        http.post.side_effect = post_dispatch
        return worker, http

    @patch.object(MeshSessionWorker, "_stop_upterm")
    @patch.object(MeshSessionWorker, "_create_attach_handle")
    @patch.object(MeshSessionWorker, "_tmux_has_session", return_value=False)
    @patch.object(MeshSessionWorker, "_tmux_new_session")
    def test_upterm_cleaned_up_on_normal_exit(
        self,
        mock_tmux_new: Mock,
        mock_has: Mock,
        mock_attach: Mock,
        mock_stop: Mock,
    ) -> None:
        worker, http = self._setup_worker()
        upterm_proc = MagicMock(spec=subprocess.Popen)
        mock_attach.return_value = (
            {"attach_kind": "upterm", "attach_target": "ssh://t@h:22"},
            upterm_proc,
        )

        task = {
            "task_id": "t-001",
            "execution_mode": "session",
            "payload": {"prompt": "hello"},
        }
        worker._execute_task(task)

        # tmux_session_name: mesh-claude-work-t-001
        mock_stop.assert_called_once_with(
            upterm_proc,
            log_path=worker._upterm_log_path("mesh-claude-work-t-001"),
        )

    @patch.object(MeshSessionWorker, "_stop_upterm")
    @patch.object(MeshSessionWorker, "_create_attach_handle")
    @patch.object(MeshSessionWorker, "_tmux_new_session", side_effect=RuntimeError("boom"))
    def test_upterm_not_started_on_early_error(
        self,
        mock_tmux_new: Mock,
        mock_attach: Mock,
        mock_stop: Mock,
    ) -> None:
        """If tmux creation fails before attach, upterm_proc stays None."""
        worker, http = self._setup_worker()

        task = {
            "task_id": "t-002",
            "execution_mode": "session",
            "payload": {"prompt": "hello"},
        }
        worker._execute_task(task)

        # _create_attach_handle was never called
        mock_attach.assert_not_called()
        # _stop_upterm should not be called (proc is None)
        mock_stop.assert_not_called()

    @patch.object(MeshSessionWorker, "_stop_upterm")
    @patch.object(MeshSessionWorker, "_create_attach_handle")
    @patch.object(MeshSessionWorker, "_tmux_new_session")
    def test_upterm_cleaned_up_on_open_session_error(
        self,
        mock_tmux_new: Mock,
        mock_attach: Mock,
        mock_stop: Mock,
    ) -> None:
        """If _open_session raises, upterm is still cleaned up via finally."""
        worker, http = self._setup_worker()
        upterm_proc = MagicMock(spec=subprocess.Popen)
        mock_attach.return_value = (
            {"attach_kind": "upterm", "attach_target": "ssh://t@h:22"},
            upterm_proc,
        )
        # Make _only_ /sessions/open fail (ack must succeed first)
        ack_resp = MagicMock(status_code=200)

        ok_resp = MagicMock(status_code=200)

        def post_open_fails(url, **kw):
            if "/tasks/ack" in url:
                return ack_resp
            if "/tasks/fail" in url:
                return ok_resp
            raise RuntimeError("open failed")

        http.post.side_effect = post_open_fails

        task = {
            "task_id": "t-003",
            "execution_mode": "session",
            "payload": {"prompt": "hello"},
        }
        worker._execute_task(task)

        # tmux_session_name: mesh-claude-work-t-003
        mock_stop.assert_called_once_with(
            upterm_proc,
            log_path=worker._upterm_log_path("mesh-claude-work-t-003"),
        )

    def test_no_cleanup_when_no_attach(self) -> None:
        """Early returns (bad mode) skip attach entirely; no cleanup needed."""
        worker = _make_worker()
        http = MagicMock()
        worker._http = http
        ack_resp = MagicMock(status_code=200)
        http.post.return_value = ack_resp

        task = {
            "task_id": "t-004",
            "execution_mode": "batch",  # unsupported for session worker
            "payload": {"prompt": "hello"},
        }
        # Should not raise, and no upterm involvement
        worker._execute_task(task)

    @patch("src.router.session_worker.os.makedirs")
    @patch("src.router.session_worker.os.path.isdir", return_value=True)
    @patch.object(MeshSessionWorker, "_tmux_has_session", return_value=False)
    @patch.object(MeshSessionWorker, "_tmux_capture_pane", return_value="")
    @patch.object(MeshSessionWorker, "_deliver_inbound_messages", return_value=0)
    @patch.object(MeshSessionWorker, "_create_attach_handle", return_value=(None, None))
    @patch.object(MeshSessionWorker, "_prepare_cli_runtime")
    @patch.object(MeshSessionWorker, "_ensure_prompt_delivered")
    @patch.object(MeshSessionWorker, "_wait_for_cli_ready", return_value=True)
    @patch.object(MeshSessionWorker, "_tmux_new_session")
    @patch.object(MeshSessionWorker, "_tmux_send_text")
    @patch.object(MeshSessionWorker, "_open_session", return_value="sid-1")
    @patch.object(MeshSessionWorker, "_close_session")
    @patch.object(MeshSessionWorker, "_report_complete")
    @patch("src.router.session_worker.time.sleep")
    def test_existing_work_dir_skips_makedirs(
        self,
        mock_sleep: Mock,
        mock_complete: Mock,
        mock_close: Mock,
        mock_open: Mock,
        mock_send_text: Mock,
        mock_tmux_new: Mock,
        mock_wait_ready: Mock,
        mock_ensure_prompt_delivered: Mock,
        mock_prepare_runtime: Mock,
        mock_attach: Mock,
        mock_deliver: Mock,
        mock_capture: Mock,
        mock_has: Mock,
        mock_isdir: Mock,
        mock_makedirs: Mock,
    ) -> None:
        worker, http = self._setup_worker()
        task = {
            "task_id": "t-005",
            "execution_mode": "session",
            "target_account": "claude-alt",
            "payload": {
                "prompt": "hello",
                "working_dir": "/media/sam/1TB/rektaslug",
            },
        }

        worker._execute_task(task)

        assert call("/media/sam/1TB/rektaslug") in mock_isdir.mock_calls
        assert call("/media/sam/1TB/rektaslug", exist_ok=True) not in mock_makedirs.mock_calls
        mock_prepare_runtime.assert_called_once_with("/media/sam/1TB/rektaslug", "claude-alt")
        mock_tmux_new.assert_called_once()
        mock_wait_ready.assert_called_once()
        mock_ensure_prompt_delivered.assert_called_once()
        mock_send_text.assert_called_once_with("mesh-claude-claude-alt-t-005", "hello")
        mock_complete.assert_called_once()

    @patch.object(MeshSessionWorker, "_tmux_has_session", side_effect=[True, False])
    @patch.object(MeshSessionWorker, "_tmux_capture_pane", return_value="")
    @patch.object(MeshSessionWorker, "_deliver_inbound_messages", return_value=0)
    @patch.object(MeshSessionWorker, "_create_attach_handle", return_value=(None, None))
    @patch.object(MeshSessionWorker, "_tmux_new_session")
    @patch.object(MeshSessionWorker, "_tmux_kill_session")
    @patch.object(MeshSessionWorker, "_tmux_send_text")
    @patch.object(MeshSessionWorker, "_open_session", return_value="sid-retry")
    @patch.object(MeshSessionWorker, "_close_session")
    @patch.object(MeshSessionWorker, "_report_complete")
    @patch("src.router.session_worker.time.sleep")
    def test_retry_kills_stale_tmux_session_before_new_session(
        self,
        mock_sleep: Mock,
        mock_complete: Mock,
        mock_close: Mock,
        mock_open: Mock,
        mock_send_text: Mock,
        mock_kill_session: Mock,
        mock_tmux_new: Mock,
        mock_attach: Mock,
        mock_deliver: Mock,
        mock_capture: Mock,
        mock_has: Mock,
    ) -> None:
        worker, http = self._setup_worker()
        worker.config.cli_type = "gemini"
        task = {
            "task_id": "t-retry",
            "execution_mode": "session",
            "target_account": "gemini",
            "payload": {
                "prompt": "hello",
                "working_dir": "/tmp/mesh-tasks",
            },
        }

        worker._execute_task(task)

        mock_kill_session.assert_called_once_with("mesh-gemini-gemini-t-retry")
        mock_tmux_new.assert_called_once()
        mock_complete.assert_called_once()

    @patch.object(MeshSessionWorker, "_tmux_has_session", return_value=False)
    @patch.object(MeshSessionWorker, "_tmux_capture_pane", return_value="")
    @patch.object(MeshSessionWorker, "_deliver_inbound_messages", return_value=0)
    @patch.object(MeshSessionWorker, "_create_attach_handle", return_value=(None, None))
    @patch.object(MeshSessionWorker, "_tmux_new_session")
    @patch.object(MeshSessionWorker, "_tmux_send_text")
    @patch.object(MeshSessionWorker, "_open_session", return_value="sid-404")
    @patch.object(MeshSessionWorker, "_report_complete")
    @patch.object(MeshSessionWorker, "_report_failure")
    @patch("src.router.session_worker.time.sleep")
    def test_missing_session_on_close_is_tolerated(
        self,
        mock_sleep: Mock,
        mock_report_failure: Mock,
        mock_report_complete: Mock,
        mock_open: Mock,
        mock_send_text: Mock,
        mock_tmux_new: Mock,
        mock_attach: Mock,
        mock_deliver: Mock,
        mock_capture: Mock,
        mock_has: Mock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        worker, http = self._setup_worker()
        close_404 = MagicMock(status_code=404)
        close_404.raise_for_status.side_effect = requests.HTTPError("session_not_found")

        def post_dispatch(url, **kw):
            if "/tasks/ack" in url:
                return MagicMock(status_code=200)
            if "/sessions/send" in url:
                return MagicMock(status_code=200)
            if "/sessions/close" in url:
                return close_404
            if "/tasks/complete" in url:
                return MagicMock(status_code=200)
            return MagicMock(status_code=200)

        http.post.side_effect = post_dispatch

        task = {
            "task_id": "t-006",
            "execution_mode": "session",
            "target_account": "claude-rektslug",
            "payload": {
                "prompt": "/exit",
                "working_dir": "/media/sam/1TB/rektslug",
            },
        }

        worker._execute_task(task)

        mock_report_complete.assert_called_once()
        mock_report_failure.assert_not_called()
        assert "Session close returned 404 for session sid-404" in caplog.text

    @patch.object(MeshSessionWorker, "_tmux_has_session", return_value=False)
    @patch.object(MeshSessionWorker, "_tmux_capture_pane", return_value="")
    @patch.object(MeshSessionWorker, "_deliver_inbound_messages", return_value=0)
    @patch.object(MeshSessionWorker, "_create_attach_handle", return_value=(None, None))
    @patch.object(MeshSessionWorker, "_tmux_new_session")
    @patch.object(MeshSessionWorker, "_tmux_send_text")
    @patch.object(MeshSessionWorker, "_open_session", return_value="sid-sync")
    @patch.object(MeshSessionWorker, "_close_session")
    @patch.object(MeshSessionWorker, "_report_complete")
    @patch.object(MeshSessionWorker, "_report_failure")
    @patch("src.router.session_worker.time.sleep")
    def test_initial_session_message_sync_failure_is_non_fatal(
        self,
        mock_sleep: Mock,
        mock_report_failure: Mock,
        mock_report_complete: Mock,
        mock_close: Mock,
        mock_open: Mock,
        mock_send_text: Mock,
        mock_tmux_new: Mock,
        mock_attach: Mock,
        mock_deliver: Mock,
        mock_capture: Mock,
        mock_has: Mock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        worker, http = self._setup_worker()
        worker._list_session_messages = Mock(side_effect=requests.HTTPError("boom"))  # type: ignore[method-assign]

        task = {
            "task_id": "t-007",
            "execution_mode": "session",
            "target_account": "claude-rektslug",
            "payload": {
                "prompt": "/exit",
                "working_dir": "/media/sam/1TB/rektslug",
            },
        }

        worker._execute_task(task)

        mock_report_complete.assert_called_once()
        mock_report_failure.assert_not_called()
        assert "Initial session message sync failed for sid-sync" in caplog.text

    @patch.object(MeshSessionWorker, "_tmux_has_session", side_effect=[False, True, True, False])
    @patch.object(
        MeshSessionWorker,
        "_tmux_capture_pane",
        side_effect=[
            "❯ Reply with exactly GEMINI_OK.\n\n❯ ",
            "❯ Reply with exactly GEMINI_OK.\n\n❯ ",
            "❯ Reply with exactly GEMINI_OK.\n\n● GEMINI_OK\n\n❯ ",
            "❯ Reply with exactly GEMINI_OK.\n\n● GEMINI_OK\n\n❯ ",
        ],
    )
    @patch.object(MeshSessionWorker, "_deliver_inbound_messages", return_value=0)
    @patch.object(MeshSessionWorker, "_create_attach_handle", return_value=(None, None))
    @patch.object(MeshSessionWorker, "_tmux_new_session")
    @patch.object(MeshSessionWorker, "_wait_for_cli_ready", return_value=True)
    @patch.object(MeshSessionWorker, "_ensure_prompt_submitted")
    @patch.object(MeshSessionWorker, "_tmux_send_text")
    @patch.object(MeshSessionWorker, "_open_session", return_value="sid-auto-exit")
    @patch.object(MeshSessionWorker, "_close_session")
    @patch.object(MeshSessionWorker, "_report_complete")
    @patch("src.router.session_worker.time.sleep")
    def test_auto_exit_on_success_sends_exit_command_and_completes(
        self,
        mock_sleep: Mock,
        mock_report_complete: Mock,
        mock_close: Mock,
        mock_open: Mock,
        mock_send_text: Mock,
        mock_ensure_prompt_submitted: Mock,
        mock_wait_ready: Mock,
        mock_tmux_new: Mock,
        mock_attach: Mock,
        mock_deliver: Mock,
        mock_capture: Mock,
        mock_has: Mock,
    ) -> None:
        worker, http = self._setup_worker()
        worker.config.cli_type = "gemini"
        worker._running = True
        expected_session = worker._tmux_session_name("t-auto-exit", "gemini")

        task = {
            "task_id": "t-auto-exit",
            "execution_mode": "session",
            "target_account": "gemini",
            "payload": {
                "prompt": "Reply with exactly GEMINI_OK.",
                "working_dir": "/media/sam/1TB/gobabygo",
                "auto_exit_on_success": True,
                "success_marker": "GEMINI_OK",
            },
        }

        worker._execute_task(task)

        mock_send_text.assert_has_calls([
            call(expected_session, "Reply with exactly GEMINI_OK."),
            call(expected_session, "/exit"),
        ])
        assert mock_ensure_prompt_submitted.call_count == 2
        assert mock_capture.call_count == 4
        mock_close.assert_called_once_with("sid-auto-exit", state="closed")
        mock_report_complete.assert_called_once()

    @patch.object(MeshSessionWorker, "_tmux_has_session", side_effect=[False, True, True, True, True, False])
    @patch.object(
        MeshSessionWorker,
        "_tmux_capture_pane",
        side_effect=[
            "❯ Create file and finish.\n\n❯ ",
            "● Write(GEMINI_E2E_OK.md)\n· Flowing…",
            "● Write(GEMINI_E2E_OK.md)\n· Flowing…",
            "● Write(GEMINI_E2E_OK.md)\n· Flowing…",
            "● Write(GEMINI_E2E_OK.md)\n· Flowing…",
            "● Write(GEMINI_E2E_OK.md)\n· Flowing…",
        ],
    )
    @patch.object(MeshSessionWorker, "_deliver_inbound_messages", return_value=0)
    @patch.object(MeshSessionWorker, "_create_attach_handle", return_value=(None, None))
    @patch.object(MeshSessionWorker, "_tmux_new_session")
    @patch.object(MeshSessionWorker, "_wait_for_cli_ready", return_value=True)
    @patch.object(MeshSessionWorker, "_ensure_prompt_submitted")
    @patch.object(MeshSessionWorker, "_tmux_send_text")
    @patch.object(MeshSessionWorker, "_open_session", return_value="sid-auto-exit-file")
    @patch.object(MeshSessionWorker, "_close_session")
    @patch.object(MeshSessionWorker, "_report_complete")
    @patch("src.router.session_worker._success_file_matches", return_value=True)
    @patch("src.router.session_worker.time.sleep")
    def test_auto_exit_on_success_sends_exit_command_when_success_file_matches(
        self,
        mock_sleep: Mock,
        mock_success_file_matches: Mock,
        mock_report_complete: Mock,
        mock_close: Mock,
        mock_open: Mock,
        mock_send_text: Mock,
        mock_ensure_prompt_submitted: Mock,
        mock_wait_ready: Mock,
        mock_tmux_new: Mock,
        mock_attach: Mock,
        mock_deliver: Mock,
        mock_capture: Mock,
        mock_has: Mock,
    ) -> None:
        worker, http = self._setup_worker()
        worker.config.cli_type = "gemini"
        worker._running = True
        expected_session = worker._tmux_session_name("t-auto-exit-file", "gemini")

        task = {
            "task_id": "t-auto-exit-file",
            "execution_mode": "session",
            "target_account": "gemini",
            "payload": {
                "prompt": "Create file and finish.",
                "working_dir": "/tmp/mesh-gemini-e2e",
                "auto_exit_on_success": True,
                "success_file_path": "GEMINI_E2E_OK.md",
                "success_file_contains": "GEMINI_FILE_OK",
            },
        }

        worker._execute_task(task)

        mock_success_file_matches.assert_called()
        mock_send_text.assert_has_calls([
            call(expected_session, "Create file and finish."),
            call(expected_session, "/exit"),
        ])
        assert mock_ensure_prompt_submitted.call_count == 2
        mock_close.assert_called_once_with("sid-auto-exit-file", state="closed")
        mock_report_complete.assert_called_once()

    @patch.object(MeshSessionWorker, "_tmux_has_session", side_effect=[False, True, True, False])
    @patch.object(
        MeshSessionWorker,
        "_tmux_capture_pane",
        side_effect=[
            "Welcome back gpt!\nTips for getting started\n❯ Try \"edit <filepath> to...\"",
            "Welcome back gpt!\nTips for getting started\n❯ Try \"edit <filepath> to...\"",
            "❯ Reply with exactly GEMINI_OK.\n\n● GEMINI_OK\n\n❯ ",
        ],
    )
    @patch.object(MeshSessionWorker, "_deliver_inbound_messages", return_value=0)
    @patch.object(MeshSessionWorker, "_create_attach_handle", return_value=(None, None))
    @patch.object(MeshSessionWorker, "_ensure_prompt_delivered")
    @patch.object(MeshSessionWorker, "_tmux_new_session")
    @patch.object(MeshSessionWorker, "_wait_for_cli_ready", return_value=True)
    @patch.object(MeshSessionWorker, "_ensure_prompt_submitted")
    @patch.object(MeshSessionWorker, "_tmux_send_text")
    @patch.object(MeshSessionWorker, "_open_session", return_value="sid-loop-resend")
    @patch.object(MeshSessionWorker, "_close_session")
    @patch.object(MeshSessionWorker, "_report_complete")
    @patch("src.router.session_worker.time.sleep")
    def test_execute_task_resends_prompt_when_loop_stays_on_start_screen(
        self,
        mock_sleep: Mock,
        mock_report_complete: Mock,
        mock_close: Mock,
        mock_open: Mock,
        mock_send_text: Mock,
        mock_ensure_prompt_submitted: Mock,
        mock_wait_ready: Mock,
        mock_tmux_new: Mock,
        mock_ensure_prompt_delivered: Mock,
        mock_attach: Mock,
        mock_deliver: Mock,
        mock_capture: Mock,
        mock_has: Mock,
    ) -> None:
        worker, http = self._setup_worker()
        worker.config.cli_type = "gemini"
        worker._running = True
        expected_session = worker._tmux_session_name("t-loop-resend", "gemini")

        task = {
            "task_id": "t-loop-resend",
            "execution_mode": "session",
            "target_account": "gemini",
            "payload": {
                "prompt": "Reply with exactly GEMINI_OK.",
                "working_dir": "/media/sam/1TB/gobabygo",
                "auto_exit_on_success": True,
                "success_marker": "GEMINI_OK",
            },
        }

        worker._execute_task(task)

        mock_send_text.assert_has_calls([
            call(expected_session, "Reply with exactly GEMINI_OK."),
            call(expected_session, "Reply with exactly GEMINI_OK."),
            call(expected_session, "/exit"),
        ])
        assert mock_ensure_prompt_submitted.call_count == 3
        mock_report_complete.assert_called_once()

    @patch.object(MeshSessionWorker, "_tmux_has_session", side_effect=[False, True, False])
    @patch.object(
        MeshSessionWorker,
        "_tmux_capture_pane",
        return_value="API Error: 429 rate_limit_error\nYou've hit your limit",
    )
    @patch.object(MeshSessionWorker, "_deliver_inbound_messages", return_value=0)
    @patch.object(MeshSessionWorker, "_create_attach_handle", return_value=(None, None))
    @patch.object(MeshSessionWorker, "_tmux_new_session")
    @patch.object(MeshSessionWorker, "_tmux_send_text")
    @patch.object(MeshSessionWorker, "_open_session", return_value="sid-limit")
    @patch.object(MeshSessionWorker, "_close_session")
    @patch.object(MeshSessionWorker, "_report_complete")
    @patch.object(MeshSessionWorker, "_report_failure")
    @patch("src.router.session_worker.time.sleep")
    def test_final_snapshot_rate_limit_reports_failure(
        self,
        mock_sleep: Mock,
        mock_report_failure: Mock,
        mock_report_complete: Mock,
        mock_close: Mock,
        mock_open: Mock,
        mock_send_text: Mock,
        mock_tmux_new: Mock,
        mock_attach: Mock,
        mock_deliver: Mock,
        mock_capture: Mock,
        mock_has: Mock,
    ) -> None:
        worker, http = self._setup_worker()

        task = {
            "task_id": "t-limit",
            "execution_mode": "session",
            "target_account": "claude-samuele",
            "payload": {
                "prompt": "/continue",
                "working_dir": "/media/sam/1TB/rektslug",
            },
        }

        worker._running = True
        worker._execute_task(task)

        mock_report_complete.assert_not_called()
        mock_report_failure.assert_called_once()
        assert mock_report_failure.call_args.kwargs["error_kind"] == "account_exhausted"
        mock_close.assert_called_once_with("sid-limit", state="errored")

    @patch.object(MeshSessionWorker, "_tmux_has_session", side_effect=[False, True, True])
    @patch.object(
        MeshSessionWorker,
        "_tmux_capture_pane",
        return_value=(
            "You've hit your limit\n"
            "❯ /rate-limit-options\n"
            "What do you want to do?\n"
            "1. Stop and wait for limit to reset\n"
        ),
    )
    @patch.object(MeshSessionWorker, "_deliver_inbound_messages", return_value=0)
    @patch.object(MeshSessionWorker, "_create_attach_handle", return_value=(None, None))
    @patch.object(MeshSessionWorker, "_tmux_new_session")
    @patch.object(MeshSessionWorker, "_tmux_send_text")
    @patch.object(MeshSessionWorker, "_ensure_prompt_submitted")
    @patch.object(MeshSessionWorker, "_tmux_kill_session")
    @patch.object(MeshSessionWorker, "_open_session", return_value="sid-rate-menu")
    @patch.object(MeshSessionWorker, "_close_session")
    @patch.object(MeshSessionWorker, "_report_complete")
    @patch.object(MeshSessionWorker, "_report_failure")
    @patch("src.router.session_worker.time.sleep")
    def test_live_rate_limit_menu_reports_failure_without_waiting_for_exit(
        self,
        mock_sleep: Mock,
        mock_report_failure: Mock,
        mock_report_complete: Mock,
        mock_close: Mock,
        mock_open: Mock,
        mock_kill: Mock,
        mock_ensure_prompt_submitted: Mock,
        mock_send_text: Mock,
        mock_tmux_new: Mock,
        mock_attach: Mock,
        mock_deliver: Mock,
        mock_capture: Mock,
        mock_has: Mock,
    ) -> None:
        worker, http = self._setup_worker()

        task = {
            "task_id": "t-limit-menu",
            "execution_mode": "session",
            "target_account": "claude-samuele",
            "payload": {
                "prompt": "/continue",
                "working_dir": "/media/sam/1TB/rektslug",
            },
        }

        worker._running = True
        worker._execute_task(task)

        mock_report_complete.assert_not_called()
        mock_report_failure.assert_called_once()
        assert mock_report_failure.call_args.kwargs["error_kind"] == "account_exhausted"
        mock_close.assert_called_once_with("sid-rate-menu", state="errored")
        mock_kill.assert_called_once_with(worker._tmux_session_name("t-limit-menu", "claude-samuele"))


# ---------------------------------------------------------------------------
# _deliver_inbound_messages
# ---------------------------------------------------------------------------


class TestDeliverInboundMessages:

    def test_deliver_success(self) -> None:
        worker = _make_worker()
        worker._http = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "messages": [
                {"seq": 10, "direction": "in", "content": "hello"},
                {"seq": 11, "direction": "out", "content": "ignored"},
                {"seq": 12, "direction": "in", "content": ""}, # empty ignored
                {"seq": 13, "direction": "in", "content": "world"},
            ]
        }
        worker._http.get.return_value = mock_resp
        
        with patch.object(worker, "_tmux_send_text") as mock_send:
            new_seq = worker._deliver_inbound_messages("sid", "tsess", 9)
            
            assert new_seq == 13
            assert mock_send.call_count == 2
            mock_send.assert_has_calls([
                call("tsess", "hello"),
                call("tsess", "world"),
            ])

    def test_fetch_error_returns_old_seq(self) -> None:
        worker = _make_worker()
        worker._http = MagicMock()
        worker._http.get.side_effect = requests.RequestException("boom")
        
        new_seq = worker._deliver_inbound_messages("sid", "tsess", 5)
        assert new_seq == 5

    def test_list_session_messages_raises_session_not_found(self) -> None:
        worker = _make_worker()
        worker._http = MagicMock()
        resp = MagicMock(status_code=404)
        resp.json.return_value = {"error": "session_not_found"}
        worker._http.get.return_value = resp

        with pytest.raises(SessionNotFoundError):
            worker._list_session_messages("sid", after_seq=0, limit=50)

    def test_session_not_found_propagates(self) -> None:
        worker = _make_worker()
        worker._list_session_messages = Mock(side_effect=SessionNotFoundError("sid"))  # type: ignore[method-assign]

        with pytest.raises(SessionNotFoundError):
            worker._deliver_inbound_messages("sid", "tsess", 5)

    def test_tmux_send_error_continues(self) -> None:
        worker = _make_worker()
        worker._http = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "messages": [{"seq": 1, "direction": "in", "content": "msg"}]
        }
        worker._http.get.return_value = mock_resp

        with patch.object(worker, "_tmux_send_text", side_effect=subprocess.SubprocessError("fail")):
            new_seq = worker._deliver_inbound_messages("sid", "tsess", 0)
            assert new_seq == 1 # still advanced

    def test_deliver_send_key_control(self) -> None:
        worker = _make_worker()
        worker._http = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "messages": [
                {
                    "seq": 2,
                    "direction": "in",
                    "content": "",
                    "metadata": {"control": "send_key", "key": "Up", "repeat": 3},
                }
            ]
        }
        worker._http.get.return_value = mock_resp

        with patch.object(worker, "_tmux_send_key") as mock_send_key:
            new_seq = worker._deliver_inbound_messages("sid", "tsess", 1)
            assert new_seq == 2
            mock_send_key.assert_called_once_with("tsess", "Up", repeat=3)

    def test_deliver_resize_control(self) -> None:
        worker = _make_worker()
        worker._http = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "messages": [
                {
                    "seq": 3,
                    "direction": "in",
                    "content": "",
                    "metadata": {"control": "resize", "cols": 120, "rows": 40},
                }
            ]
        }
        worker._http.get.return_value = mock_resp

        with patch.object(worker, "_tmux_resize") as mock_resize:
            new_seq = worker._deliver_inbound_messages("sid", "tsess", 2)
            assert new_seq == 3
            mock_resize.assert_called_once_with("tsess", cols=120, rows=40)

    def test_deliver_signal_controls(self) -> None:
        worker = _make_worker()
        worker._http = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "messages": [
                {
                    "seq": 4,
                    "direction": "in",
                    "content": "",
                    "metadata": {"control": "signal", "signal": "interrupt"},
                },
                {
                    "seq": 5,
                    "direction": "in",
                    "content": "",
                    "metadata": {"control": "signal", "signal": "terminate"},
                },
            ]
        }
        worker._http.get.return_value = mock_resp

        with (
            patch.object(worker, "_tmux_send_key") as mock_send_key,
            patch.object(worker, "_tmux_kill_session") as mock_kill,
        ):
            new_seq = worker._deliver_inbound_messages("sid", "tsess", 3)
            assert new_seq == 5
            mock_send_key.assert_called_once_with("tsess", "C-c", repeat=1)
            mock_kill.assert_called_once_with("tsess")


# ---------------------------------------------------------------------------
# Tmux operations
# ---------------------------------------------------------------------------


class TestTmuxOperations:

    @patch("src.router.session_worker.time.sleep")
    @patch("src.router.session_worker.subprocess.run")
    def test_send_text_multiline(self, mock_run: Mock, mock_sleep: Mock) -> None:
        worker = _make_worker()
        worker._tmux_send_text("mysess", "line1\nline2")
        
        # line1, Enter, line2, Enter = 4 calls
        assert mock_run.call_count == 4
        args = [c[0][0] for c in mock_run.call_args_list]
        assert args[0][4] == "line1"
        assert args[1][4] == "Enter"
        assert args[2][4] == "line2"
        assert args[3][4] == "Enter"
        assert mock_sleep.call_count == 2

    @patch("src.router.session_worker.time.sleep")
    @patch("src.router.session_worker.subprocess.run")
    def test_send_text_empty(self, mock_run: Mock, mock_sleep: Mock) -> None:
        worker = _make_worker()
        worker._tmux_send_text("mysess", "")
        # splitlines() or [text] -> [""] -> 1 iteration -> if line: skip, then Enter
        assert mock_run.call_count == 1
        assert mock_run.call_args[0][0][4] == "Enter"
        mock_sleep.assert_not_called()

    @patch("src.router.session_worker.time.sleep")
    def test_ensure_prompt_submitted_retries_until_prompt_clears(self, mock_sleep: Mock) -> None:
        worker = _make_worker(prompt_submit_retry_count=3, prompt_submit_retry_poll_s=0.2)
        with (
            patch.object(worker, "_tmux_capture_pane", side_effect=["header\n❯ pending", "header\n✻ Herding…\n❯ "]),
            patch.object(worker, "_tmux_send_key") as mock_send_key,
        ):
            worker._ensure_prompt_submitted("mysess")
        mock_send_key.assert_called_once_with("mysess", "Enter", repeat=1)

    @patch("src.router.session_worker.time.sleep")
    def test_ensure_prompt_submitted_skips_when_composer_empty(self, mock_sleep: Mock) -> None:
        worker = _make_worker(prompt_submit_retry_count=2, prompt_submit_retry_poll_s=0.2)
        with (
            patch.object(worker, "_tmux_capture_pane", return_value="header\n✻ Herding…\n❯ "),
            patch.object(worker, "_tmux_send_key") as mock_send_key,
        ):
            worker._ensure_prompt_submitted("mysess")
        mock_send_key.assert_not_called()

    @patch("src.router.session_worker.time.sleep")
    def test_ensure_prompt_delivered_resends_when_welcome_screen_unchanged(self, mock_sleep: Mock) -> None:
        worker = _make_worker(prompt_submit_retry_count=2, prompt_submit_retry_poll_s=0.2)
        prompt = "Create GEMINI_E2E_OK.md and reply GEMINI_E2E_OK."
        baseline = 'Welcome back gpt!\nTips for getting started\n❯ Try "write a test for <filepath>"'
        with (
            patch.object(
                worker,
                "_tmux_capture_pane",
                side_effect=[
                    baseline,
                    "Create GEMINI_E2E_OK.md and reply GEMINI_E2E_OK.\n\n❯ ",
                ],
            ),
            patch.object(worker, "_tmux_send_text") as mock_send_text,
            patch.object(worker, "_ensure_prompt_submitted") as mock_ensure_submitted,
        ):
            worker._ensure_prompt_delivered("mysess", prompt, baseline)
        mock_send_text.assert_called_once_with("mysess", prompt)
        mock_ensure_submitted.assert_called_once_with("mysess")

    @patch("src.router.session_worker.time.sleep")
    def test_ensure_prompt_delivered_defers_start_screen_variant_change_to_main_loop(self, mock_sleep: Mock) -> None:
        worker = _make_worker(prompt_submit_retry_count=2, prompt_submit_retry_poll_s=0.2)
        prompt = "Create GEMINI_E2E_OK.md and reply GEMINI_E2E_OK."
        baseline = (
            "Welcome back gpt!\nTips for getting started\n❯ Try \"write a test for <filepath>\""
        )
        with (
            patch.object(
                worker,
                "_tmux_capture_pane",
                side_effect=[
                    "Welcome back gpt!\nTips for getting started\n❯ edit <filepath> to...",
                    "Create GEMINI_E2E_OK.md and reply GEMINI_E2E_OK.\n\n❯ ",
                ],
            ),
            patch.object(worker, "_tmux_send_text") as mock_send_text,
            patch.object(worker, "_ensure_prompt_submitted") as mock_ensure_submitted,
        ):
            worker._ensure_prompt_delivered("mysess", prompt, baseline)
        mock_send_text.assert_not_called()
        mock_ensure_submitted.assert_not_called()

    @patch("src.router.session_worker.time.sleep")
    def test_ensure_prompt_delivered_resends_when_start_screen_contains_prompt_text(self, mock_sleep: Mock) -> None:
        worker = _make_worker(prompt_submit_retry_count=2, prompt_submit_retry_poll_s=0.2)
        prompt = "Create GEMINI_E2E_OK.md and reply GEMINI_E2E_OK."
        baseline = (
            "Welcome back gpt!\nTips for getting started\n❯ Try \"write a test for <filepath>\""
        )
        with (
            patch.object(
                worker,
                "_tmux_capture_pane",
                side_effect=[
                    f"Welcome back gpt!\nTips for getting started\n❯ {prompt}",
                    "Create GEMINI_E2E_OK.md and reply GEMINI_E2E_OK.\n\n● GEMINI_E2E_OK\n\n❯ ",
                ],
            ),
            patch.object(worker, "_tmux_send_text") as mock_send_text,
            patch.object(worker, "_ensure_prompt_submitted") as mock_ensure_submitted,
        ):
            worker._ensure_prompt_delivered("mysess", prompt, baseline)
        mock_send_text.assert_called_once_with("mysess", prompt)
        mock_ensure_submitted.assert_called_once_with("mysess")

    @patch("src.router.session_worker.time.sleep")
    def test_ensure_prompt_delivered_does_not_resend_when_activity_is_visible(self, mock_sleep: Mock) -> None:
        worker = _make_worker(prompt_submit_retry_count=2, prompt_submit_retry_poll_s=0.2)
        prompt = "Create GEMINI_E2E_OK.md and reply GEMINI_E2E_OK."
        baseline = (
            "Welcome back gpt!\nTips for getting started\n❯ Try \"write a test for <filepath>\""
        )
        with (
            patch.object(
                worker,
                "_tmux_capture_pane",
                return_value=(
                    "Welcome back gpt!\n"
                    "● Write(GEMINI_E2E_OK.md)\n"
                    "⎿ \n"
                    "· Flowing…\n"
                    "❯ Press up to edit queued messages"
                ),
            ),
            patch.object(worker, "_tmux_send_text") as mock_send_text,
            patch.object(worker, "_ensure_prompt_submitted") as mock_ensure_submitted,
        ):
            worker._ensure_prompt_delivered("mysess", prompt, baseline)
        mock_send_text.assert_not_called()
        mock_ensure_submitted.assert_not_called()

    @patch("src.router.session_worker.subprocess.run")
    def test_send_key_repeat(self, mock_run: Mock) -> None:
        worker = _make_worker()
        worker._tmux_send_key("mysess", "Up", repeat=3)
        args = mock_run.call_args[0][0]
        assert args[:4] == [worker.config.tmux_bin, "send-keys", "-t", "mysess:0.0"]
        assert args[4:] == ["Up", "Up", "Up"]

    @patch("src.router.session_worker.subprocess.run")
    def test_resize(self, mock_run: Mock) -> None:
        worker = _make_worker()
        worker._tmux_resize("mysess", cols=140, rows=50)
        args = mock_run.call_args[0][0]
        assert args == [worker.config.tmux_bin, "resize-window", "-t", "mysess", "-x", "140", "-y", "50"]

    @patch("src.router.session_worker.subprocess.run")
    def test_capture_pane_success(self, mock_run: Mock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="hello world\n")
        worker = _make_worker()
        out = worker._tmux_capture_pane("mysess")
        assert out == "hello world"

    @patch("src.router.session_worker.subprocess.run")
    def test_capture_pane_error(self, mock_run: Mock) -> None:
        mock_run.return_value = MagicMock(returncode=1)
        worker = _make_worker()
        assert worker._tmux_capture_pane("mysess") == ""


def test_stop_deregisters_session_worker() -> None:
    worker = _make_worker()
    worker._http = MagicMock()
    worker._http.post.return_value = MagicMock(status_code=200)

    worker.stop()

    worker._http.post.assert_called_once_with(
        "http://localhost:8780/workers/ws-test-01/deregister",
        timeout=worker.config.control_plane_timeout,
    )
