"""Tests for meshctl thread commands.

Mocks all HTTP calls to the router.
"""

from __future__ import annotations

import argparse
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from src.meshctl import (
    cmd_pipeline_create,
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


def _pipeline_create_args(
    template: str = "gsd",
    thread_name: str = "pipeline-demo",
    repo: str = "/tmp/repo",
    phase: str = "17",
    project: str = "Demo Project",
    feature: str = "Feature X",
    template_file: str = "/tmp/pipeline_templates.yaml",
    account_claude: str = "work-claude",
    account_codex: str = "work-codex",
    account_gemini: str = "work-gemini",
    dry_run: bool = False,
    json_output: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        command="pipeline",
        pipeline_command="create",
        template=template,
        thread_name=thread_name,
        repo=repo,
        phase=phase,
        project=project,
        feature=feature,
        template_file=template_file,
        account_claude=account_claude,
        account_codex=account_codex,
        account_gemini=account_gemini,
        dry_run=dry_run,
        json_output=json_output,
    )


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


class TestPipelineCommands:
    @patch("src.meshctl.requests.post")
    def test_pipeline_create_dry_run(
        self, mock_post: MagicMock, tmp_path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        template_file = tmp_path / "pipeline.yaml"
        template_file.write_text(
            """version: 1
templates:
  gsd:
    steps:
      - name: gsd:plan-phase
        title: "Plan {phase}"
        target_cli: claude
        target_account: "{claude_account}"
        execution_mode: session
        critical: true
        on_failure: abort
        review_policy: codex_review
        prompt: "Run /gsd:plan-phase {phase} in {repo}"
""",
            encoding="utf-8",
        )
        args = _pipeline_create_args(
            dry_run=True,
            template_file=str(template_file),
            phase="5",
            repo="/repo/demo",
        )
        cmd_pipeline_create(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["template"] == "gsd"
        assert data["steps"][0]["execution_mode"] == "session"
        assert data["steps"][0]["critical"] is True
        assert "Run /gsd:plan-phase 5 in /repo/demo" in data["steps"][0]["prompt"]
        mock_post.assert_not_called()

    @patch("src.meshctl.requests.post")
    def test_pipeline_create_success_maps_dependency_to_task_id(
        self, mock_post: MagicMock, tmp_path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        template_file = tmp_path / "pipeline.yaml"
        template_file.write_text(
            """version: 1
templates:
  gsd:
    steps:
      - name: step-a
        title: "A"
        target_cli: claude
        target_account: "{claude_account}"
        execution_mode: session
        critical: true
        on_failure: abort
        review_policy: codex_review
        prompt: "Prompt A {repo}"
      - name: step-b
        title: "B"
        target_cli: codex
        target_account: "{codex_account}"
        execution_mode: batch
        critical: false
        on_failure: retry
        depends_on_steps: [0]
        review_policy: none
        prompt: "Prompt B {repo}"
""",
            encoding="utf-8",
        )

        mock_post.side_effect = [
            _mock_response(201, {"thread_id": "t-1"}),
            _mock_response(201, {"task_id": "task-1"}),
            _mock_response(201, {"task_id": "task-2"}),
        ]
        args = _pipeline_create_args(
            template_file=str(template_file),
            repo="/repo/demo",
            thread_name="pipeline-1",
        )
        cmd_pipeline_create(args)
        out = capsys.readouterr().out
        assert "Pipeline thread created: t-1 (pipeline-1)" in out

        # 1) create thread
        create_thread_call = mock_post.call_args_list[0]
        assert create_thread_call.kwargs["json"] == {"name": "pipeline-1"}

        # 2) first step carries execution_mode/critical
        step_a_call = mock_post.call_args_list[1]
        step_a_body = step_a_call.kwargs["json"]
        assert step_a_body["step_index"] == 0
        assert step_a_body["execution_mode"] == "session"
        assert step_a_body["critical"] is True
        assert step_a_body["payload"]["working_dir"] == "/repo/demo"

        # 3) second step depends on first created task id
        step_b_call = mock_post.call_args_list[2]
        step_b_body = step_b_call.kwargs["json"]
        assert step_b_body["step_index"] == 1
        assert step_b_body["depends_on"] == ["task-1"]
        assert step_b_body["on_failure"] == "retry"
        assert step_b_body["payload"]["working_dir"] == "/repo/demo"

    def test_pipeline_create_unknown_template_exits(
        self, tmp_path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        template_file = tmp_path / "pipeline.yaml"
        template_file.write_text("version: 1\ntemplates:\n  gsd: {steps: []}\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            cmd_pipeline_create(_pipeline_create_args(template="speckit", template_file=str(template_file)))
        err = capsys.readouterr().err
        assert "unknown template" in err

    @patch.dict(os.environ, {"MESH_ENFORCE_SESSION_ONLY": "1"}, clear=False)
    @patch("src.meshctl.requests.post")
    def test_pipeline_create_respects_session_only_env(
        self, mock_post: MagicMock, tmp_path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        template_file = tmp_path / "pipeline.yaml"
        template_file.write_text(
            """version: 1
templates:
  gsd:
    steps:
      - name: step-batch
        title: "B"
        target_cli: codex
        target_account: "{codex_account}"
        execution_mode: batch
        critical: false
        on_failure: abort
        review_policy: none
        prompt: "Prompt {repo}"
""",
            encoding="utf-8",
        )
        args = _pipeline_create_args(dry_run=True, template_file=str(template_file))
        cmd_pipeline_create(args)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["policy"]["enforce_session_only"] is True
        assert data["steps"][0]["execution_mode"] == "session"


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
