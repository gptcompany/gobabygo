from src.router.cli_screen import (
    LiveScreenState,
    claude_session_limit_reset,
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
SESSION_LIMIT = """⎿  You've hit your session limit · resets 12am (Asia/Bangkok)
   /upgrade to increase your usage limit.

✻ Crunched for 1m 33s

❯
"""


def test_classifies_live_cli_states() -> None:
    assert classify_live_screen("claude", "header\n❯ ") == LiveScreenState.idle
    assert classify_live_screen("claude", "● Reading file\n❯ ") == LiveScreenState.busy
    assert classify_live_screen("claude", "header\n❯ pending") == LiveScreenState.awaiting_input
    assert classify_live_screen("claude", RATE_LIMIT_SELECTED) == LiveScreenState.rate_limit
    assert classify_live_screen("claude", SESSION_LIMIT) == LiveScreenState.session_limit


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
    assert claude_wait_option_selected(f"{RATE_LIMIT_SELECTED}\n❯ pending task") is False
    assert claude_wait_option_selected(f"{RATE_LIMIT_SELECTED}\n✻ Working") is False


def test_session_limit_requires_exact_current_banner_and_empty_prompt() -> None:
    assert claude_session_limit_reset(SESSION_LIMIT) == ("12am", "Asia/Bangkok")
    assert claude_session_limit_reset(SESSION_LIMIT.replace("12am", "12:30 am")) == (
        "12:30am",
        "Asia/Bangkok",
    )
    assert claude_session_limit_reset(SESSION_LIMIT.replace("/upgrade", "/other")) is None
    assert claude_session_limit_reset(f"{SESSION_LIMIT.rstrip()} pending") is None
    assert claude_session_limit_reset(
        f"{SESSION_LIMIT}\n❯ later request\nanswer\n❯ "
    ) is None
    assert claude_session_limit_reset(f"{SESSION_LIMIT}\n" + "history\n" * 31) is None
    assert classify_live_screen("codex", SESSION_LIMIT) != LiveScreenState.session_limit
