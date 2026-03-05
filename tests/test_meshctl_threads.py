"""Tests for meshctl thread commands.

Mocks all HTTP calls to the router.
"""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from src.meshctl import (
    cmd_thread_add_step,
    cmd_thread_context,
    cmd_thread_create,
    cmd_thread_handoff,
    cmd_thread_status,
)


def _mock_response(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text or json.dumps(json_data or {})
    return resp


def _thread_create_args(name: str = "Test Thread") -> argparse.Namespace:
    return argparse.Namespace(command="thread", thread_command="create", name=name)


def _thread_add_step_args(
    thread: str = "thread-123",
    title: str = "Step 1",
    step_index: int = 0,
    repo: str = "my-repo",
    role: str = "",
    cli: str | None = None,
    account: str | None = None,
    payload: str | None = None,
    on_failure: str = "abort",
) -> argparse.Namespace:
    return argparse.Namespace(
        command="thread",
        thread_command="add-step",
        thread=thread,
        title=title,
        step_index=step_index,
        repo=repo,
        role=role,
        cli=cli,
        account=account,
        payload=payload,
        on_failure=on_failure,
    )


def _thread_status_args(thread: str = "thread-123", json_output: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        command="thread", thread_command="status", thread=thread, json_output=json_output
    )


def _thread_context_args(thread: str = "thread-123") -> argparse.Namespace:
    return argparse.Namespace(command="thread", thread_command="context", thread=thread)


class TestThreadCommands:
    @patch("src.meshctl.requests.post")
    def test_thread_create_success(
        self, mock_post: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_post.return_value = _mock_response(
            201, {"thread_id": "t-1", "name": "My Thread"}
        )
        cmd_thread_create(_thread_create_args("My Thread"))
        out = capsys.readouterr().out
        assert "Thread created: t-1 (My Thread)" in out

    @patch("src.meshctl.requests.get")
    @patch("src.meshctl.requests.post")
    def test_thread_add_step_success(
        self, mock_post: MagicMock, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Resolve thread name "my-thread" -> "t-1"
        mock_get.return_value = _mock_response(200, {"threads": [{"thread_id": "t-1"}]})
        mock_post.return_value = _mock_response(201, {"task_id": "task-1"})

        cmd_thread_add_step(_thread_add_step_args(thread="my-thread", title="Step 1"))
        out = capsys.readouterr().out
        assert "Step 0 added: task_id=task-1" in out

    @patch("src.meshctl.requests.get")
    @patch("src.meshctl.requests.post")
    def test_thread_add_step_invalid_payload(
        self, mock_post: MagicMock, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_get.return_value = _mock_response(200, {"threads": [{"thread_id": "t-1"}]})
        with pytest.raises(SystemExit):
            cmd_thread_add_step(_thread_add_step_args(payload="invalid"))

    @patch("src.meshctl.requests.get")
    def test_thread_status_table(
        self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # 1. Resolve thread name "t1" -> "t-1" (already a UUID-like string passes directly)
        # 2. GET /threads/t-1/status
        mock_get.return_value = _mock_response(
            200,
            {
                "thread": {"name": "My Thread", "status": "running"},
                "steps": [
                    {
                        "step_index": 0,
                        "status": "completed",
                        "repo": "r1",
                        "assigned_worker": "w1",
                        "attempt": 1,
                        "on_failure": "abort",
                        "title": "First step",
                    }
                ],
            },
        )

        # Use 36-char string to skip _resolve_thread_id's GET call
        thread_id = "a" * 8 + "-" + "b" * 4 + "-" + "c" * 4 + "-" + "d" * 4 + "-" + "e" * 12
        cmd_thread_status(_thread_status_args(thread=thread_id))
        out = capsys.readouterr().out
        assert "THREAD: My Thread [running]" in out
        assert "First step" in out

    @patch("src.meshctl.requests.get")
    def test_thread_status_json(
        self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_get.return_value = _mock_response(
            200, {"thread": {"name": "T"}, "steps": []}
        )
        thread_id = "a" * 8 + "-" + "b" * 4 + "-" + "c" * 4 + "-" + "d" * 4 + "-" + "e" * 12
        cmd_thread_status(_thread_status_args(thread=thread_id, json_output=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["thread"]["name"] == "T"

    @patch("src.meshctl.requests.get")
    def test_thread_context(
        self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_get.return_value = _mock_response(200, {"step_0": {"result": "ok"}})
        thread_id = "a" * 8 + "-" + "b" * 4 + "-" + "c" * 4 + "-" + "d" * 4 + "-" + "e" * 12
        cmd_thread_context(_thread_context_args(thread=thread_id))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["step_0"]["result"] == "ok"

    @patch("src.meshctl.requests.get")
    def test_resolve_thread_id_not_found(
        self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_get.return_value = _mock_response(200, {"threads": []})
        with pytest.raises(SystemExit):
            cmd_thread_context(_thread_context_args(thread="missing"))
        err = capsys.readouterr().err
        assert "Thread not found" in err

    @patch("src.meshctl.requests.get")
    def test_resolve_thread_id_ambiguous(
        self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_get.return_value = _mock_response(200, {"threads": [{"thread_id": "1"}, {"thread_id": "2"}]})
        with pytest.raises(SystemExit):
            cmd_thread_context(_thread_context_args(thread="ambiguous"))
        err = capsys.readouterr().err
        assert "Ambiguous" in err


# -- Handoff tests (Phase 20) --

_UUID = "a" * 8 + "-" + "b" * 4 + "-" + "c" * 4 + "-" + "d" * 4 + "-" + "e" * 12


def _thread_handoff_args(
    thread: str = _UUID,
    step_index: int = 0,
    json_output: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        command="thread",
        thread_command="handoff",
        thread=thread,
        step_index=step_index,
        json_output=json_output,
    )


class TestHandoffVisibility:
    """Tests for meshctl handoff display and [HANDOFF] marker."""

    @patch("src.meshctl.requests.get")
    def test_thread_status_shows_handoff_marker(
        self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_get.return_value = _mock_response(
            200,
            {
                "thread": {"name": "cross-repo", "status": "active"},
                "steps": [
                    {
                        "step_index": 0,
                        "status": "completed",
                        "repo": "backend",
                        "assigned_worker": "w1",
                        "attempt": 1,
                        "on_failure": "abort",
                        "title": "Backend work",
                        "has_handoff": False,
                    },
                    {
                        "step_index": 1,
                        "status": "queued",
                        "repo": "platform",
                        "assigned_worker": "",
                        "attempt": 1,
                        "on_failure": "abort",
                        "title": "Platform handoff",
                        "has_handoff": True,
                    },
                ],
            },
        )
        cmd_thread_status(_thread_status_args(thread=_UUID))
        out = capsys.readouterr().out
        assert "[HANDOFF]" in out
        assert "Backend work" in out  # no marker on non-handoff step

    @patch("src.meshctl.requests.get")
    def test_thread_handoff_display(
        self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # First call: GET /threads/<id>/status
        status_resp = _mock_response(200, {
            "thread": {"name": "xrepo", "status": "active"},
            "steps": [{
                "step_index": 0,
                "task_id": "task-42",
                "status": "queued",
                "repo": "platform",
                "title": "Handoff step",
                "has_handoff": True,
                "assigned_worker": "",
                "attempt": 1,
                "on_failure": "abort",
            }],
        })
        # Second call: GET /tasks/task-42
        task_resp = _mock_response(200, {
            "task_id": "task-42",
            "payload": {
                "handoff": {
                    "source_repo": "backend",
                    "target_repo": "platform",
                    "summary": "Auth module needs migration",
                    "question": "Which auth provider?",
                    "decisions": ["Use OAuth2", "Keep backward compat"],
                    "artifacts": ["auth.py"],
                    "open_risks": ["Token rotation"],
                    "related_session_ids": ["sess-abc"],
                }
            },
        })
        mock_get.side_effect = [status_resp, task_resp]
        cmd_thread_handoff(_thread_handoff_args(step_index=0))
        out = capsys.readouterr().out
        assert "HANDOFF: backend -> platform" in out
        assert "Auth module needs migration" in out
        assert "Which auth provider?" in out
        assert "Use OAuth2" in out
        assert "Token rotation" in out
        assert "sess-abc" in out

    @patch("src.meshctl.requests.get")
    def test_thread_handoff_json_output(
        self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        status_resp = _mock_response(200, {
            "thread": {"name": "xr", "status": "active"},
            "steps": [{
                "step_index": 0, "task_id": "t-1", "status": "queued",
                "repo": "p", "title": "h", "has_handoff": True,
                "assigned_worker": "", "attempt": 1, "on_failure": "abort",
            }],
        })
        task_resp = _mock_response(200, {
            "task_id": "t-1",
            "payload": {"handoff": {"source_repo": "a", "target_repo": "b", "summary": "s"}},
        })
        mock_get.side_effect = [status_resp, task_resp]
        cmd_thread_handoff(_thread_handoff_args(json_output=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["source_repo"] == "a"
        assert data["target_repo"] == "b"

    @patch("src.meshctl.requests.get")
    def test_thread_handoff_no_handoff_on_step(
        self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        mock_get.return_value = _mock_response(200, {
            "thread": {"name": "t", "status": "active"},
            "steps": [{
                "step_index": 0, "task_id": "t-1", "status": "queued",
                "repo": "r", "title": "No handoff", "has_handoff": False,
                "assigned_worker": "", "attempt": 1, "on_failure": "abort",
            }],
        })
        with pytest.raises(SystemExit):
            cmd_thread_handoff(_thread_handoff_args(step_index=0))

    @patch("src.meshctl.requests.get")
    @patch("src.meshctl.requests.post")
    def test_add_step_sends_role(
        self, mock_post: MagicMock, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Verify --role is included in the POST body."""
        mock_get.return_value = _mock_response(200, {"threads": [{"thread_id": "t-1"}]})
        mock_post.return_value = _mock_response(201, {"task_id": "task-2"})
        cmd_thread_add_step(_thread_add_step_args(
            thread="my-thread", title="Handoff", role="PRESIDENT_GLOBAL",
            payload='{"handoff": {"source_repo": "a", "target_repo": "b", "summary": "s"}}',
        ))
        # Verify the role was sent in the POST body
        call_kwargs = mock_post.call_args
        sent_body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert sent_body["role"] == "PRESIDENT_GLOBAL"
        assert "handoff" in sent_body["payload"]

    @patch("src.meshctl.requests.get")
    @patch("src.meshctl.requests.post")
    def test_add_step_server_403_handoff_role(
        self, mock_post: MagicMock, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Server returns 403 for cross-repo handoff without role."""
        mock_get.return_value = _mock_response(200, {"threads": [{"thread_id": "t-1"}]})
        mock_post.return_value = _mock_response(
            403, {"error": "handoff_role_required", "detail": "requires PRESIDENT_GLOBAL"}
        )
        with pytest.raises(SystemExit):
            cmd_thread_add_step(_thread_add_step_args(
                thread="my-thread", title="Bad handoff",
                payload='{"handoff": {"source_repo": "a", "target_repo": "b", "summary": "s"}}',
            ))
        err = capsys.readouterr().err
        assert "403" in err

    @patch("src.meshctl.requests.get")
    @patch("src.meshctl.requests.post")
    def test_add_step_server_400_invalid_handoff(
        self, mock_post: MagicMock, mock_get: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Server returns 400 for malformed handoff."""
        mock_get.return_value = _mock_response(200, {"threads": [{"thread_id": "t-1"}]})
        mock_post.return_value = _mock_response(
            400, {"error": "invalid_handoff", "detail": "missing summary"}
        )
        with pytest.raises(SystemExit):
            cmd_thread_add_step(_thread_add_step_args(
                thread="my-thread", title="Bad",
                payload='{"handoff": {"source_repo": "a"}}',
            ))
        err = capsys.readouterr().err
        assert "400" in err
