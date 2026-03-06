"""Tests for review worker helper logic."""

from __future__ import annotations

from unittest.mock import patch

from src.router.review_worker import (
    ReviewDecision,
    ReviewWorker,
    ReviewWorkerConfig,
    _has_pending_fix_tasks,
    _parse_review_decision,
)


class TestParseReviewDecision:
    def test_parses_plain_json(self):
        out = '{"decision":"approve","reason":"looks good"}'
        decision = _parse_review_decision(out)
        assert decision == ReviewDecision(decision="approve", reason="looks good")

    def test_parses_json_inside_text(self):
        out = "some text\n```json\n{\"decision\":\"reject\",\"reason\":\"missing tests\"}\n```"
        decision = _parse_review_decision(out)
        assert decision.decision == "reject"
        assert "missing tests" in decision.reason

    def test_rejects_unparseable_output(self):
        decision = _parse_review_decision("not json")
        assert decision.decision == "reject"
        assert "not parseable" in decision.reason

    def test_rejects_non_object_json(self):
        decision = _parse_review_decision("true")
        assert decision.decision == "reject"


class TestPendingFixTasks:
    def test_true_when_child_non_terminal_exists(self):
        tasks = [
            {"task_id": "t-main", "status": "review"},
            {"task_id": "t-fix", "parent_task_id": "t-main", "status": "queued"},
        ]
        assert _has_pending_fix_tasks("t-main", tasks) is True

    def test_false_when_child_terminal(self):
        tasks = [
            {"task_id": "t-main", "status": "review"},
            {"task_id": "t-fix", "parent_task_id": "t-main", "status": "completed"},
        ]
        assert _has_pending_fix_tasks("t-main", tasks) is False


class TestReviewCycle:
    @patch.object(ReviewWorker, "_has_pending_fix_tasks_remote", return_value=True)
    @patch.object(ReviewWorker, "_review_task")
    @patch.object(ReviewWorker, "_list_tasks")
    def test_skips_review_task_with_pending_fixes(
        self, mock_list, mock_review_task, mock_pending
    ):
        cfg = ReviewWorkerConfig(poll_interval=0.01)
        worker = ReviewWorker(cfg)
        worker._running = True
        mock_list.return_value = [
            {"task_id": "t1", "status": "review", "target_cli": "claude", "target_account": "work"}
        ]

        worker._review_cycle()
        mock_review_task.assert_not_called()

    @patch.object(ReviewWorker, "_has_pending_fix_tasks_remote", return_value=False)
    @patch.object(ReviewWorker, "_review_task")
    @patch.object(ReviewWorker, "_list_tasks")
    def test_reviews_task_without_pending_fixes(
        self, mock_list, mock_review_task, mock_pending
    ):
        cfg = ReviewWorkerConfig(poll_interval=0.01)
        worker = ReviewWorker(cfg)
        worker._running = True
        review_task = {"task_id": "t1", "status": "review", "target_cli": "claude", "target_account": "work"}
        mock_list.return_value = [review_task]

        worker._review_cycle()
        mock_review_task.assert_called_once_with(review_task)


class TestRunCliReview:
    @patch("src.router.review_worker.subprocess.run")
    def test_timeout_returns_reject(self, mock_run):
        from subprocess import TimeoutExpired

        cfg = ReviewWorkerConfig(task_timeout=1)
        worker = ReviewWorker(cfg)
        mock_run.side_effect = TimeoutExpired(cmd=["ccs"], timeout=1)

        decision = worker._run_cli_review("review prompt")
        assert decision.decision == "reject"
        assert "timeout" in decision.reason
