"""Tests for meshctl CLI (status and drain commands).

All HTTP calls are mocked via unittest.mock.patch -- no real server needed.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.meshctl import (
    _format_age,
    _format_duration,
    _router_timeout,
    cmd_drain,
    cmd_status,
    cmd_submit,
    cmd_worker_prune,
    cmd_task_cancel,
    cmd_task_fail,
)


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int, json_data: dict | None = None, text: str = "") -> MagicMock:
    """Create a mock requests response object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text or json.dumps(json_data or {})
    return resp


def _status_args(
    json_output: bool = False,
    *,
    show_all: bool = False,
    recent_seconds: int = 6 * 3600,
) -> argparse.Namespace:
    return argparse.Namespace(
        command="status",
        json_output=json_output,
        all=show_all,
        recent_seconds=recent_seconds,
    )


def _drain_args(worker_id: str = "w1", timeout: int = 300) -> argparse.Namespace:
    return argparse.Namespace(command="drain", worker_id=worker_id, timeout=timeout)


def _worker_prune_args(
    *,
    older_than: int = 12 * 3600,
    statuses: list[str] | None = None,
    json_output: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        command="worker",
        worker_command="prune",
        older_than=older_than,
        statuses=statuses or ["offline"],
        json_output=json_output,
    )


# ---------------------------------------------------------------------------
# Time formatting tests
# ---------------------------------------------------------------------------


class TestFormatAge:
    def test_format_age_seconds(self) -> None:
        ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        result = _format_age(ts)
        assert result == "5s ago"

    def test_format_age_minutes(self) -> None:
        ts = (datetime.now(timezone.utc) - timedelta(seconds=180)).isoformat()
        result = _format_age(ts)
        assert result == "3m ago"

    def test_format_age_hours(self) -> None:
        ts = (datetime.now(timezone.utc) - timedelta(seconds=7500)).isoformat()
        result = _format_age(ts)
        assert result == "2h 5m ago"

    def test_format_age_none(self) -> None:
        assert _format_age(None) == "n/a"
        assert _format_age("") == "n/a"

    def test_format_age_invalid(self) -> None:
        assert _format_age("not-a-date") == "n/a"


class TestFormatDuration:
    def test_format_duration_seconds(self) -> None:
        assert _format_duration(45) == "45s"

    def test_format_duration_minutes(self) -> None:
        assert _format_duration(192) == "3m12s"

    def test_format_duration_hours(self) -> None:
        assert _format_duration(8100) == "2h15m"

    def test_format_duration_exact_minutes(self) -> None:
        assert _format_duration(120) == "2m"

    def test_format_duration_exact_hours(self) -> None:
        assert _format_duration(3600) == "1h"


class TestRouterTimeout:
    def test_default_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MESH_ROUTER_TIMEOUT_S", raising=False)
        assert _router_timeout() == 30.0

    def test_env_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MESH_ROUTER_TIMEOUT_S", "45")
        assert _router_timeout() == 45.0

    def test_invalid_timeout_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MESH_ROUTER_TIMEOUT_S", "wat")
        assert _router_timeout() == 30.0


# ---------------------------------------------------------------------------
# Status command tests
# ---------------------------------------------------------------------------


SAMPLE_WORKERS = {
    "workers": [
        {
            "worker_id": "abc12345-6789-0000-0000-000000000001",
            "machine": "worker-1",
            "cli_type": "claude",
            "status": "idle",
            "last_heartbeat": (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
            "running_tasks": [],
        },
        {
            "worker_id": "def45678-9012-0000-0000-000000000002",
            "machine": "worker-2",
            "cli_type": "codex",
            "status": "busy",
            "last_heartbeat": (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
            "running_tasks": [
                {"task_id": "t1", "status": "running", "age_s": 192.0},
            ],
        },
    ]
}

SAMPLE_HEALTH = {
    "status": "healthy",
    "workers": 2,
    "queue_depth": 3,
    "uptime_s": 8100.0,
}


class TestStatusCommand:
    @patch("src.meshctl.requests.get")
    def test_status_table_output(self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_get.side_effect = [
            _mock_response(200, SAMPLE_WORKERS),
            _mock_response(200, SAMPLE_HEALTH),
        ]

        cmd_status(_status_args())
        out = capsys.readouterr().out

        assert "WORKERS" in out
        assert "abc12345" in out
        assert "def45678" in out
        assert "idle" in out
        assert "busy" in out
        assert "QUEUE" in out
        assert "Queued: 3" in out
        assert "2h15m" in out

    @patch("src.meshctl.requests.get")
    def test_status_json_output(self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_get.side_effect = [
            _mock_response(200, SAMPLE_WORKERS),
            _mock_response(200, SAMPLE_HEALTH),
        ]

        cmd_status(_status_args(json_output=True))
        out = capsys.readouterr().out

        data = json.loads(out)
        assert "workers" in data
        assert "health" in data
        assert len(data["workers"]) == 2

    @patch("src.meshctl.requests.get")
    def test_status_no_workers(self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_get.side_effect = [
            _mock_response(200, {"workers": []}),
            _mock_response(200, SAMPLE_HEALTH),
        ]

        cmd_status(_status_args())
        out = capsys.readouterr().out
        assert "No workers registered" in out

    @patch("src.meshctl.requests.get")
    def test_status_connection_error(self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        import requests as req
        mock_get.side_effect = req.ConnectionError("refused")

        with pytest.raises(SystemExit) as exc:
            cmd_status(_status_args())
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Cannot connect" in err

    @patch("src.meshctl.requests.get")
    def test_status_auth_header_sent(self, mock_get: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MESH_AUTH_TOKEN", "mytoken")
        mock_get.side_effect = [
            _mock_response(200, SAMPLE_WORKERS),
            _mock_response(200, SAMPLE_HEALTH),
        ]

        cmd_status(_status_args())

        # Both calls should include auth header
        for call in mock_get.call_args_list:
            headers = call.kwargs.get("headers", {})
            assert headers.get("Authorization") == "Bearer mytoken"

    @patch("src.meshctl.requests.get")
    def test_status_no_auth_when_unset(self, mock_get: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_get.side_effect = [
            _mock_response(200, SAMPLE_WORKERS),
            _mock_response(200, SAMPLE_HEALTH),
        ]

        cmd_status(_status_args())

        for call in mock_get.call_args_list:
            headers = call.kwargs.get("headers", {})
            assert "Authorization" not in headers

    @patch("src.meshctl.requests.get")
    def test_status_hides_historical_workers_by_default(
        self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        stale_hb = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        fresh_hb = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        mock_get.side_effect = [
            _mock_response(
                200,
                {
                    "workers": [
                        {
                            "worker_id": "fresh-worker",
                            "machine": "worker-fresh",
                            "cli_type": "claude",
                            "status": "idle",
                            "last_heartbeat": fresh_hb,
                            "running_tasks": [],
                        },
                        {
                            "worker_id": "stale-worker",
                            "machine": "worker-stale",
                            "cli_type": "codex",
                            "status": "offline",
                            "last_heartbeat": stale_hb,
                            "running_tasks": [],
                        },
                    ]
                },
            ),
            _mock_response(200, SAMPLE_HEALTH),
        ]

        cmd_status(_status_args())
        out = capsys.readouterr().out

        assert "fresh-wo" in out
        assert "stale-wo" not in out
        assert "hidden historical workers: 1" in out


# ---------------------------------------------------------------------------
# Drain command tests
# ---------------------------------------------------------------------------


class TestDrainCommand:
    @patch("src.meshctl.requests.get")
    @patch("src.meshctl.requests.post")
    def test_drain_immediate(
        self,
        mock_post: MagicMock,
        mock_get: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(
            202, {"status": "drained_immediately", "worker_id": "w1"}
        )

        cmd_drain(_drain_args("w1"))
        out = capsys.readouterr().out

        assert "drained and retired" in out
        assert "idle, no tasks" in out
        # No polling should occur
        mock_get.assert_not_called()

    @patch("src.meshctl.time.sleep")
    @patch("src.meshctl.requests.get")
    @patch("src.meshctl.requests.post")
    def test_drain_with_polling(
        self,
        mock_post: MagicMock,
        mock_get: MagicMock,
        mock_sleep: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(
            202, {"status": "draining", "worker_id": "w1"}
        )
        # First poll: still draining with 1 task. Second poll: offline.
        mock_get.side_effect = [
            _mock_response(200, {"status": "draining", "running_tasks": [{"task_id": "t1"}]}),
            _mock_response(200, {"status": "offline", "running_tasks": []}),
        ]

        cmd_drain(_drain_args("w1"))
        out = capsys.readouterr().out

        assert "Draining worker w1" in out
        assert "Waiting for 1 task(s)" in out
        assert "drained and retired" in out

    @patch("src.meshctl.requests.post")
    def test_drain_not_found(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(404)

        with pytest.raises(SystemExit) as exc:
            cmd_drain(_drain_args("w1"))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "not found" in err

    @patch("src.meshctl.requests.post")
    def test_drain_conflict_409(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(
            409, {"error": "invalid_state", "detail": "Worker is stale"}
        )

        with pytest.raises(SystemExit) as exc:
            cmd_drain(_drain_args("w1"))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Cannot drain" in err
        assert "stale" in err.lower()

    @patch("src.meshctl.requests.post")
    def test_drain_auth_failure(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(401)

        with pytest.raises(SystemExit) as exc:
            cmd_drain(_drain_args("w1"))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Authentication failed" in err

    @patch("src.meshctl.time.monotonic")
    @patch("src.meshctl.time.sleep")
    @patch("src.meshctl.requests.get")
    @patch("src.meshctl.requests.post")
    def test_drain_timeout(
        self,
        mock_post: MagicMock,
        mock_get: MagicMock,
        mock_sleep: MagicMock,
        mock_monotonic: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(
            202, {"status": "draining", "worker_id": "w1"}
        )
        # Simulate time passing past the timeout
        # First monotonic() call is start, second is in the loop check
        mock_monotonic.side_effect = [0.0, 301.0]

        with pytest.raises(SystemExit) as exc:
            cmd_drain(_drain_args("w1", timeout=300))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "timed out" in err

    @patch("src.meshctl.time.sleep")
    @patch("src.meshctl.requests.get")
    @patch("src.meshctl.requests.post")
    def test_drain_worker_disappears_during_poll(
        self,
        mock_post: MagicMock,
        mock_get: MagicMock,
        mock_sleep: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(
            202, {"status": "draining", "worker_id": "w1"}
        )
        # Worker already deregistered (404)
        mock_get.return_value = _mock_response(404)

        cmd_drain(_drain_args("w1"))
        out = capsys.readouterr().out
        assert "drained and retired" in out


class TestWorkerPruneCommand:
    @patch("src.meshctl.requests.get")
    def test_worker_prune_json_output(
        self, mock_get: MagicMock, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        old_hb = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        recent_hb = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        mock_get.return_value = _mock_response(
            200,
            {
                "workers": [
                    {
                        "worker_id": "stale-offline",
                        "status": "offline",
                        "last_heartbeat": old_hb,
                        "running_tasks": [],
                    },
                    {
                        "worker_id": "fresh-offline",
                        "status": "offline",
                        "last_heartbeat": recent_hb,
                        "running_tasks": [],
                    },
                ]
            },
        )

        cmd_worker_prune(_worker_prune_args(json_output=True))
        data = json.loads(capsys.readouterr().out)
        assert [item["worker_id"] for item in data["selected"]] == ["stale-offline"]

    @patch("src.meshctl.requests.post")
    @patch("src.meshctl.requests.get")
    def test_worker_prune_success(
        self,
        mock_get: MagicMock,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        old_hb = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        mock_get.return_value = _mock_response(
            200,
            {
                "workers": [
                    {
                        "worker_id": "offline-old",
                        "status": "offline",
                        "last_heartbeat": old_hb,
                        "running_tasks": [],
                    },
                    {
                        "worker_id": "idle-old",
                        "status": "idle",
                        "last_heartbeat": old_hb,
                        "running_tasks": [],
                    },
                ]
            },
        )
        mock_post.return_value = _mock_response(200, {"status": "deregistered"})

        cmd_worker_prune(_worker_prune_args(statuses=["offline", "idle"]))
        out = capsys.readouterr().out

        assert "Pruned 2 worker(s)." in out
        assert "offline-old" in out
        assert "idle-old" in out
        assert mock_post.call_count == 2


# ---------------------------------------------------------------------------
# Submit command tests
# ---------------------------------------------------------------------------


def _submit_args(
    title: str = "Test task",
    cli: str | None = None,
    account: str | None = None,
    phase: str | None = None,
    priority: int | None = None,
    payload: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        command="submit",
        title=title,
        cli=cli,
        account=account,
        phase=phase,
        priority=priority,
        payload=payload,
    )


def _task_admin_args(
    task_id: str = "t-123",
    reason: str = "admin_test",
) -> argparse.Namespace:
    return argparse.Namespace(
        command="task",
        task_command="cancel",
        task_id=task_id,
        reason=reason,
    )


class TestSubmitCommand:
    @patch("src.meshctl.requests.post")
    def test_submit_success(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(
            201, {"status": "created", "task_id": "t-123"}
        )

        cmd_submit(_submit_args("My Task"))
        out = capsys.readouterr().out
        assert "Task created: t-123" in out

        # Verify POST body
        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["json"]["title"] == "My Task"

    @patch("src.meshctl.requests.post")
    def test_submit_with_payload(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(
            201, {"status": "created", "task_id": "t-456"}
        )

        cmd_submit(_submit_args("Task", payload='{"prompt": "Do X"}'))
        call_kwargs = mock_post.call_args
        assert call_kwargs.kwargs["json"]["payload"] == {"prompt": "Do X"}

    @patch("src.meshctl.requests.post")
    def test_submit_invalid_payload_json(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        with pytest.raises(SystemExit) as exc:
            cmd_submit(_submit_args("Task", payload="not json"))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "valid JSON" in err

    @patch("src.meshctl.requests.post")
    def test_submit_auth_failure(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(401)
        with pytest.raises(SystemExit) as exc:
            cmd_submit(_submit_args("Task"))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Authentication failed" in err

    @patch("src.meshctl.requests.post")
    def test_submit_duplicate_409(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(
            409, {"error": "duplicate_task", "detail": "idempotency_key already exists"}
        )
        with pytest.raises(SystemExit) as exc:
            cmd_submit(_submit_args("Task"))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Duplicate" in err

    @patch("src.meshctl.requests.post")
    def test_submit_connection_error(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        import requests as req
        mock_post.side_effect = req.ConnectionError("refused")
        with pytest.raises(SystemExit) as exc:
            cmd_submit(_submit_args("Task"))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Cannot connect" in err

    @patch("src.meshctl.requests.post")
    def test_submit_with_all_options(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(
            201, {"status": "created", "task_id": "t-789"}
        )

        cmd_submit(_submit_args(
            "Full task",
            cli="codex",
            account="clientA",
            phase="test",
            priority=5,
            payload='{"prompt": "Run tests"}',
        ))

        call_kwargs = mock_post.call_args
        body = call_kwargs.kwargs["json"]
        assert body["title"] == "Full task"
        assert body["target_cli"] == "codex"
        assert body["target_account"] == "clientA"
        assert body["phase"] == "test"
        assert body["priority"] == 5
        assert body["payload"] == {"prompt": "Run tests"}


class TestTaskAdminCommands:
    @patch("src.meshctl.requests.post")
    def test_task_cancel_success(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(200, {"status": "canceled"})

        cmd_task_cancel(_task_admin_args("t-1", "cleanup queue"))
        out = capsys.readouterr().out
        assert "Task canceled: t-1" in out
        call_kwargs = mock_post.call_args
        assert call_kwargs.args[0].endswith("/tasks/cancel")
        assert call_kwargs.kwargs["json"] == {"task_id": "t-1", "reason": "cleanup queue"}

    @patch("src.meshctl.requests.post")
    def test_task_fail_success(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(200, {"status": "failed"})

        args = _task_admin_args("t-2", "stuck review")
        args.task_command = "fail"
        cmd_task_fail(args)
        out = capsys.readouterr().out
        assert "Task failed: t-2" in out
        call_kwargs = mock_post.call_args
        assert call_kwargs.args[0].endswith("/tasks/admin-fail")
        assert call_kwargs.kwargs["json"] == {"task_id": "t-2", "reason": "stuck review"}

    @patch("src.meshctl.requests.post")
    def test_task_cancel_conflict(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(
            409, {"error": "cancel_failed", "detail": "running_not_supported"}
        )
        with pytest.raises(SystemExit) as exc:
            cmd_task_cancel(_task_admin_args("t-3"))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "running_not_supported" in err

    @patch("src.meshctl.requests.post")
    def test_task_fail_not_found(
        self,
        mock_post: MagicMock,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MESH_AUTH_TOKEN", raising=False)
        mock_post.return_value = _mock_response(404)
        args = _task_admin_args("missing")
        args.task_command = "fail"
        with pytest.raises(SystemExit) as exc:
            cmd_task_fail(args)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "task not found" in err
