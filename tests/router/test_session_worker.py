"""Unit tests for the interactive session worker."""

from __future__ import annotations

import os
import subprocess
import requests
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from src.router.session_worker import (
    MeshSessionWorker,
    SessionWorkerConfig,
    _compute_output_emit,
    _parse_upterm_ssh_url,
    _sanitize_session_name,
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
    assert cfg.capabilities == ["interactive", "code"]
    assert cfg.allowed_accounts == ["work-claude", "review-codex", "*"]
    assert cfg.session_poll_interval_s == 0.5
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


# ---------------------------------------------------------------------------
# Config env for attach fields
# ---------------------------------------------------------------------------


def test_config_from_env_attach_fields() -> None:
    env = {
        "MESH_UPTERM_BIN": "/usr/local/bin/upterm",
        "MESH_UPTERM_SERVER": "ssh://uptermd.example.com:22",
        "MESH_UPTERM_READY_TIMEOUT": "5.0",
        "MESH_SSH_TMUX_USER": "operator",
        "MESH_SSH_TMUX_HOST": "10.0.0.5",
    }
    with patch.dict(os.environ, env, clear=False):
        cfg = SessionWorkerConfig.from_env()
    assert cfg.upterm_bin == "/usr/local/bin/upterm"
    assert cfg.upterm_server == "ssh://uptermd.example.com:22"
    assert cfg.upterm_ready_timeout == 5.0
    assert cfg.ssh_tmux_user == "operator"
    assert cfg.ssh_tmux_host == "10.0.0.5"


def test_config_defaults_attach_fields() -> None:
    cfg = SessionWorkerConfig()
    assert cfg.upterm_bin == "upterm"
    assert cfg.upterm_server == ""
    assert cfg.upterm_ready_timeout == 10.0
    assert cfg.ssh_tmux_user == ""
    assert cfg.ssh_tmux_host == ""


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_worker(**overrides) -> MeshSessionWorker:
    cfg = SessionWorkerConfig(
        worker_id="ws-test-01",
        router_url="http://localhost:8780",
        upterm_bin="/usr/bin/upterm",
        upterm_ready_timeout=0.6,
        **overrides,
    )
    return MeshSessionWorker(cfg)


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
    @patch("src.router.session_worker.os.remove")
    @patch.object(MeshSessionWorker, "_poll_upterm_target")
    @patch("src.router.session_worker.subprocess.Popen")
    def test_success(
        self,
        mock_popen: Mock,
        mock_poll: Mock,
        mock_remove: Mock,
        mock_exists: Mock,
    ) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        mock_popen.return_value = proc
        mock_poll.return_value = "ssh://tok@host:22"
        worker = _make_worker()

        p, target = worker._start_upterm("mesh-sess")

        assert p is proc
        assert target == "ssh://tok@host:22"
        # Verify forced command
        popen_args = mock_popen.call_args[0][0]
        assert "--force-command" in popen_args
        fc_idx = popen_args.index("--force-command")
        assert "tmux attach -t mesh-sess" in popen_args[fc_idx + 1]
        mock_exists.assert_called_once_with("/tmp/upterm-mesh-sess.sock")
        mock_remove.assert_called_once_with("/tmp/upterm-mesh-sess.sock")

    @patch("src.router.session_worker.os.path.exists", return_value=False)
    @patch("src.router.session_worker.os.remove")
    @patch.object(MeshSessionWorker, "_poll_upterm_target")
    @patch("src.router.session_worker.subprocess.Popen")
    def test_success_without_stale_socket(
        self,
        mock_popen: Mock,
        mock_poll: Mock,
        mock_remove: Mock,
        mock_exists: Mock,
    ) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        mock_popen.return_value = proc
        mock_poll.return_value = "ssh://tok@host:22"
        worker = _make_worker()

        p, target = worker._start_upterm("mesh-sess")

        assert p is proc
        assert target == "ssh://tok@host:22"
        mock_exists.assert_called_once_with("/tmp/upterm-mesh-sess.sock")
        mock_remove.assert_not_called()

    @patch.object(MeshSessionWorker, "_poll_upterm_target")
    @patch("src.router.session_worker.subprocess.Popen")
    def test_with_server_flag(self, mock_popen: Mock, mock_poll: Mock) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        mock_popen.return_value = proc
        mock_poll.return_value = "ssh://tok@host:22"
        worker = _make_worker(upterm_server="ssh://custom:22")

        worker._start_upterm("mesh-sess")

        popen_args = mock_popen.call_args[0][0]
        assert "--server" in popen_args
        assert "ssh://custom:22" in popen_args

    @patch("src.router.session_worker.subprocess.Popen", side_effect=FileNotFoundError)
    def test_binary_not_found(self, mock_popen: Mock) -> None:
        worker = _make_worker()
        p, target = worker._start_upterm("mesh-sess")
        assert p is None
        assert target is None

    @patch.object(MeshSessionWorker, "_stop_upterm")
    @patch.object(MeshSessionWorker, "_poll_upterm_target", return_value=None)
    @patch("src.router.session_worker.subprocess.Popen")
    def test_poll_timeout_kills_process(
        self, mock_popen: Mock, mock_poll: Mock, mock_stop: Mock
    ) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        mock_popen.return_value = proc
        worker = _make_worker()

        p, target = worker._start_upterm("mesh-sess")

        assert p is None
        assert target is None
        mock_stop.assert_called_once_with(proc, "/tmp/upterm-mesh-sess.sock")


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
    def test_socket_cleanup(self, mock_remove: Mock, mock_exists: Mock) -> None:
        proc = MagicMock(spec=subprocess.Popen)
        proc.poll.return_value = 0
        MeshSessionWorker._stop_upterm(proc, "/tmp/test.sock")
        mock_exists.assert_called_once_with("/tmp/test.sock")
        mock_remove.assert_called_once_with("/tmp/test.sock")


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
    @patch("src.router.session_worker.subprocess.run")
    @patch("src.router.session_worker.time.monotonic")
    def test_success_on_second_poll(
        self, mock_mono: Mock, mock_run: Mock, mock_sleep: Mock
    ) -> None:
        # First call: deadline check -> True
        # Second call: returns before deadline
        # Third call: deadline exceeded (shouldn't reach)
        mock_mono.side_effect = [0.0, 0.1, 0.5, 99.0]
        fail_result = MagicMock(returncode=1, stdout="")
        ok_result = MagicMock(
            returncode=0,
            stdout="Host:                   ssh://tok@uptermd:22\n",
        )
        mock_run.side_effect = [fail_result, ok_result]
        worker = _make_worker()

        target = worker._poll_upterm_target("/tmp/upterm-sess.sock")

        assert target == "ssh://tok@uptermd:22"

    @patch("src.router.session_worker.time.sleep")
    @patch("src.router.session_worker.subprocess.run")
    @patch("src.router.session_worker.time.monotonic")
    def test_timeout(
        self, mock_mono: Mock, mock_run: Mock, mock_sleep: Mock
    ) -> None:
        mock_mono.side_effect = [0.0, 99.0]
        worker = _make_worker()

        target = worker._poll_upterm_target("/tmp/upterm-sess.sock")

        assert target is None


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
        mock_stop.assert_called_once_with(upterm_proc, "/tmp/upterm-mesh-claude-work-t-001.sock")

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
        mock_stop.assert_called_once_with(upterm_proc, "/tmp/upterm-mesh-claude-work-t-003.sock")

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

    @patch("src.router.session_worker.subprocess.run")
    def test_send_text_multiline(self, mock_run: Mock) -> None:
        worker = _make_worker()
        worker._tmux_send_text("mysess", "line1\nline2")
        
        # line1, Enter, line2, Enter = 4 calls
        assert mock_run.call_count == 4
        args = [c[0][0] for c in mock_run.call_args_list]
        assert args[0][4] == "line1"
        assert args[1][4] == "Enter"
        assert args[2][4] == "line2"
        assert args[3][4] == "Enter"

    @patch("src.router.session_worker.subprocess.run")
    def test_send_text_empty(self, mock_run: Mock) -> None:
        worker = _make_worker()
        worker._tmux_send_text("mysess", "")
        # splitlines() or [text] -> [""] -> 1 iteration -> if line: skip, then Enter
        assert mock_run.call_count == 1
        assert mock_run.call_args[0][0][4] == "Enter"

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
