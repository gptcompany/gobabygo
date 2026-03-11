"""Extended tests for review worker to increase coverage."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.router.review_worker import (
    ReviewDecision,
    ReviewWorker,
    ReviewWorkerConfig,
    _parse_review_decision,
    _safe_json_preview,
    run_review_worker,
)


class TestReviewWorkerConfig:
    def test_from_env_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            cfg = ReviewWorkerConfig.from_env()
            assert cfg.router_url == "http://localhost:8780"
            assert cfg.reviewer_id == "verifier-codex"
            assert cfg.poll_interval == 8.0
            assert cfg.dry_run is False

    def test_from_env_overrides(self):
        env = {
            "MESH_ROUTER_URL": "http://mesh:9000",
            "MESH_AUTH_TOKEN": "secret",
            "MESH_REVIEW_POLL_INTERVAL_S": "1.5",
            "MESH_REVIEWER_ID": "custom-reviewer",
            "MESH_REVIEW_CLI_COMMAND": "custom-cmd",
            "MESH_ACCOUNT_PROFILE": "custom-profile",
            "MESH_WORK_DIR": "/tmp/custom",
            "MESH_TASK_TIMEOUT_S": "60",
            "MESH_REVIEW_MAX_TASKS": "10",
            "MESH_DRY_RUN": "1",
            "MESH_REVIEW_TARGET_CLI": "claude",
            "MESH_REVIEW_TARGET_ACCOUNT": "work",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = ReviewWorkerConfig.from_env()
            assert cfg.router_url == "http://mesh:9000"
            assert cfg.auth_token == "secret"
            assert cfg.poll_interval == 1.5
            assert cfg.reviewer_id == "custom-reviewer"
            assert cfg.cli_command == "custom-cmd"
            assert cfg.account_profile == "custom-profile"
            assert cfg.work_dir == "/tmp/custom"
            assert cfg.task_timeout == 60
            assert cfg.max_review_tasks == 10
            assert cfg.dry_run is True
            assert cfg.target_cli_filter == "claude"
            assert cfg.target_account_filter == "work"


class TestParseReviewDecisionExtended:
    def test_parses_json_with_unusual_spacing(self):
        out = '  {  "decision"  :  "approve"  ,  "reason"  :  "ok"  }  '
        decision = _parse_review_decision(out)
        assert decision.decision == "approve"
        assert decision.reason == "ok"

    def test_parses_json_with_missing_reason(self):
        out = '{"decision":"approve"}'
        decision = _parse_review_decision(out)
        assert decision.decision == "approve"
        assert decision.reason == "no reason provided"

    def test_handles_empty_output(self):
        decision = _parse_review_decision("")
        assert decision.decision == "reject"
        assert "not parseable" in decision.reason

    def test_handles_malformed_json_substring(self):
        out = "Here is some text with { malformed : json } in it"
        decision = _parse_review_decision(out)
        assert decision.decision == "reject"


class TestSafeJsonPreview:
    def test_safe_json_preview_no_truncation(self):
        val = {"key": "value"}
        res = _safe_json_preview(val, 100)
        assert res == json.dumps(val)

    def test_safe_json_preview_truncation(self):
        val = {"key": "very long value" * 100}
        res = _safe_json_preview(val, 50)
        assert len(res) <= 50
        assert "... [truncated" in res


class TestReviewWorkerMethods:
    @pytest.fixture
    def worker(self):
        cfg = ReviewWorkerConfig(router_url="http://localhost:8780", auth_token="test-token")
        return ReviewWorker(cfg)

    def test_matches_filters_none(self, worker):
        task = {"target_cli": "claude", "target_account": "work"}
        assert worker._matches_filters(task) is True

    def test_matches_filters_cli_match(self, worker):
        worker.config.target_cli_filter = "claude"
        task = {"target_cli": "claude"}
        assert worker._matches_filters(task) is True

    def test_matches_filters_cli_mismatch(self, worker):
        worker.config.target_cli_filter = "claude"
        task = {"target_cli": "codex"}
        assert worker._matches_filters(task) is False

    def test_matches_filters_account_match(self, worker):
        worker.config.target_account_filter = "work"
        task = {"target_account": "work"}
        assert worker._matches_filters(task) is True

    def test_matches_filters_account_mismatch(self, worker):
        worker.config.target_account_filter = "work"
        task = {"target_account": "personal"}
        assert worker._matches_filters(task) is False

    @patch.object(requests.Session, "get")
    def test_list_tasks_unauthorized(self, mock_get, worker):
        mock_get.return_value = MagicMock(status_code=401)
        with pytest.raises(RuntimeError, match="unauthorized"):
            worker._list_tasks(status="review", limit=10)

    @patch.object(requests.Session, "get")
    def test_has_pending_fix_tasks_remote_404(self, mock_get, worker):
        mock_get.return_value = MagicMock(status_code=404)
        assert worker._has_pending_fix_tasks_remote("t1") is False

    @patch.object(requests.Session, "get")
    def test_has_pending_fix_tasks_remote_401(self, mock_get, worker):
        mock_get.return_value = MagicMock(status_code=401)
        with pytest.raises(RuntimeError, match="unauthorized"):
            worker._has_pending_fix_tasks_remote("t1")

    def test_build_review_prompt_contains_fields(self, worker):
        task = {
            "task_id": "t1",
            "title": "My Task",
            "phase": "implement",
            "target_cli": "claude",
            "target_account": "work",
            "payload": {"p": 1},
            "result": {"r": 2}
        }
        prompt = worker._build_review_prompt(task)
        assert "t1" in prompt
        assert "My Task" in prompt
        assert "implement" in prompt
        assert "claude" in prompt
        assert "work" in prompt
        assert '"p": 1' in prompt
        assert '"r": 2' in prompt

    @patch("src.router.review_worker.subprocess.run")
    def test_run_cli_review_error_exit(self, mock_run, worker):
        mock_run.return_value = MagicMock(returncode=1, stderr="Error message", stdout="")
        decision = worker._run_cli_review("prompt")
        assert decision.decision == "reject"
        assert "exit=1" in decision.reason

    @patch.object(ReviewWorker, "_approve")
    @patch.object(ReviewWorker, "_run_cli_review")
    def test_review_task_dry_run(self, mock_run, mock_approve, worker):
        worker.config.dry_run = True
        mock_run.return_value = ReviewDecision("approve", "ok")
        worker._review_task({"task_id": "t1"})
        mock_approve.assert_not_called()

    @patch.object(requests.Session, "post")
    def test_approve_failure_logs(self, mock_post, worker):
        mock_post.return_value = MagicMock(status_code=409, text="Conflicted")
        # Should not raise, just log
        worker._approve("t1")
        mock_post.assert_called_once()

    @patch.object(requests.Session, "post")
    def test_reject_failure_logs(self, mock_post, worker):
        mock_post.return_value = MagicMock(status_code=409, text="Conflicted")
        # Should not raise, just log
        worker._reject("t1", "reason")
        mock_post.assert_called_once()

    @patch.object(ReviewWorker, "_review_cycle")
    @patch("src.router.review_worker.signal.signal")
    def test_run_review_worker_entrypoint(self, mock_signal, mock_cycle):
        # We need to mock the while loop or make it run once
        with patch("src.router.review_worker.ReviewWorker.start", side_effect=lambda: None) as mock_start:
            run_review_worker()
            mock_start.assert_called_once()
