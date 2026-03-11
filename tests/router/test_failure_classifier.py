from __future__ import annotations

from src.router.failure_classifier import classify_cli_failure


def test_classify_claude_account_exhausted_patterns() -> None:
    assert classify_cli_failure("claude", "You've hit your limit") == "account_exhausted"
    assert classify_cli_failure("claude", "You're out of extra usage") == "account_exhausted"
    assert classify_cli_failure("claude", "rate limit error") == "account_exhausted"


def test_classify_cli_failure_ignores_non_claude() -> None:
    assert classify_cli_failure("codex", "rate_limit_exceeded") == "account_exhausted"
    assert classify_cli_failure("gemini", "RESOURCE_EXHAUSTED: quota exceeded") == "account_exhausted"
    assert classify_cli_failure("codex", "plain failure") == ""
