from src.router.cli_screen import (
    LiveScreenState,
    claude_wait_option_selected,
    classify_live_screen,
    detect_interactive_failure_screen,
)


RATE_LIMIT_SELECTED = """You've hit your limit
❯ /rate-limit-options
What do you want to do?
❯ 1. Stop and wait for limit to reset
  2. Upgrade your plan
"""


def test_classifies_live_cli_states() -> None:
    assert classify_live_screen("claude", "header\n❯ ") == LiveScreenState.idle
    assert classify_live_screen("claude", "● Reading file\n❯ ") == LiveScreenState.busy
    assert classify_live_screen("claude", "header\n❯ pending") == LiveScreenState.awaiting_input
    assert classify_live_screen("claude", RATE_LIMIT_SELECTED) == LiveScreenState.rate_limit


def test_rate_limit_detector_reuses_account_exhausted_classification() -> None:
    assert detect_interactive_failure_screen("claude", RATE_LIMIT_SELECTED) == "account_exhausted"
    assert detect_interactive_failure_screen("claude", "API Error: 429") == ""


def test_wait_action_requires_visible_selected_option() -> None:
    assert claude_wait_option_selected(RATE_LIMIT_SELECTED) is True
    assert (
        claude_wait_option_selected(
            RATE_LIMIT_SELECTED.replace("❯ 1. Stop", "  1. Stop")
        )
        is False
    )
