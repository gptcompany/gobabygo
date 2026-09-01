from src.router.cli_screen import (
    LiveScreenState,
    antigravity_screen_state,
    claude_screen_state,
    claude_session_limit_reset,
    claude_wait_option_selected,
    classify_live_screen,
    detect_interactive_failure_screen,
)


ANTIGRAVITY_IDLE = """Antigravity CLI 1.1.13
Gemini 3.7 Flash (High)
/tmp/mesh-antigravity-smoke
────────────────────────────────────────────────────────────────────────────────
>
────────────────────────────────────────────────────────────────────────────────
? for shortcuts                                          Gemini 3.7 Flash · high
"""
ANTIGRAVITY_BUSY = """Antigravity CLI 1.1.13
> Run the read-only command sleep 8.
⡿  Generating...
────────────────────────────────────────────────────────────────────────────────
>
────────────────────────────────────────────────────────────────────────────────
esc to cancel                                            Gemini 3.7 Flash · high
"""
ANTIGRAVITY_APPROVAL = """● Bash(pwd) (ctrl+o to expand)
Command
Requesting permission for:
   pwd
Do you want to proceed?
> 1. Yes
  4. No
esc to cancel
"""


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
SESSION_LIMIT_MONITOR_EVENT = """● Monitor event: worker quiet check
  ⎿ \xa0You've hit your session limit · resets 1:20pm (Asia/Bangkok)
     /upgrade to increase your usage limit.

✻ Worked for 0s · 1 shell, 2 monitors still running

47 tasks (28 done, 4 in progress, 15 open)
  ◼ Continue the active work
  ◻ Review the remaining tasks

────────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────────
  bypass permissions on · 1 shell, 2 monitors · ← for agents
"""


def test_classifies_live_cli_states() -> None:
    assert classify_live_screen("claude", "header\n❯ ") == LiveScreenState.idle
    assert classify_live_screen("claude", "● Reading file\n❯ ") == LiveScreenState.busy
    assert classify_live_screen("claude", "header\n❯ pending") == LiveScreenState.awaiting_input
    assert classify_live_screen("claude", RATE_LIMIT_SELECTED) == LiveScreenState.rate_limit
    assert classify_live_screen("claude", SESSION_LIMIT) == LiveScreenState.session_limit
    assert (
        classify_live_screen("claude", SESSION_LIMIT.replace("❯\n", "❯ continue task\n"))
        == LiveScreenState.session_limit
    )


def test_claude_state_ignores_monitor_transcript_before_current_status_bar() -> None:
    idle_with_historical_monitor = """● Monitor event: worker quiet check
  ⎿ Waiting for agents appeared in worker prose

✻ Worked for 23s · 1 shell, 2 monitors still running

────────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────────
  bypass permissions on · 1 shell, 2 monitors · ← for agents
"""
    active_main_turn = """old transcript
────────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────────
✻ Working… esc to interrupt
"""

    assert claude_screen_state(idle_with_historical_monitor) == LiveScreenState.idle
    assert claude_screen_state(active_main_turn) == LiveScreenState.busy


def test_claude_dynamic_active_turn_above_composer_is_busy() -> None:
    active = """old transcript
● Concocting… (9m 29s · ↓ 22.0k tokens · thinking with xhigh effort)
  ⎿ Tip: Use /btw for a side question
────────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────────
  bypass permissions on
"""
    completed = """● Concocting… (9m 29s · ↓ 22.0k tokens · thinking with xhigh effort)
✻ Cogitated for 9m 30s
────────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────────
  bypass permissions on
"""
    interrupted = """● Befuddling… (10s · ↓ 613 tokens)
  ⎿ Interrupted · What should Claude do instead?
────────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────────
  bypass permissions on
"""

    assert claude_screen_state(active) == LiveScreenState.busy
    assert claude_screen_state(completed) == LiveScreenState.idle
    assert claude_screen_state(interrupted) == LiveScreenState.idle


def test_classifies_observed_antigravity_tui_states() -> None:
    assert antigravity_screen_state(ANTIGRAVITY_IDLE) == LiveScreenState.idle
    assert classify_live_screen("antigravity", ANTIGRAVITY_BUSY) == LiveScreenState.busy
    assert (
        classify_live_screen("antigravity", ANTIGRAVITY_APPROVAL)
        == LiveScreenState.awaiting_input
    )
    assert classify_live_screen("antigravity", "booting") == LiveScreenState.unknown


def test_antigravity_history_does_not_make_idle_screen_busy() -> None:
    capture = "● Bash(sleep 8) (ctrl+o to expand)\ncompleted\n" + ANTIGRAVITY_IDLE
    assert classify_live_screen("antigravity", capture) == LiveScreenState.idle


def test_antigravity_old_idle_footer_does_not_hide_current_activity() -> None:
    capture = (
        ANTIGRAVITY_IDLE
        + "\n> Run a task.\n⡿  Generating...\n"
        + "─" * 80
        + "\n>\n"
        + "─" * 80
        + "\nesc to cancel\n"
    )
    assert classify_live_screen("antigravity", capture) == LiveScreenState.busy


def test_antigravity_login_and_quota_screens_fail_closed() -> None:
    assert (
        classify_live_screen("antigravity", "Select login method:\n> 1. Google OAuth")
        == LiveScreenState.awaiting_input
    )
    assert (
        classify_live_screen("antigravity", "RESOURCE_EXHAUSTED: quota exceeded")
        == LiveScreenState.rate_limit
    )


def test_antigravity_rate_limit_text_in_history_does_not_override_idle() -> None:
    capture = (
        "Investigate quota exceeded without changing files.\n"
        "Requesting permission for:\nDo you want to proceed?\n"
        + ANTIGRAVITY_IDLE
    )
    assert classify_live_screen("antigravity", capture) == LiveScreenState.idle


def test_rate_limit_detector_reuses_account_exhausted_classification() -> None:
    assert detect_interactive_failure_screen("claude", RATE_LIMIT_SELECTED) == "account_exhausted"
    assert detect_interactive_failure_screen("claude", "API Error: 429") == ""
    assert (
        detect_interactive_failure_screen(
            "codex", "You've hit your usage limit. Upgrade to Pro or try again later."
        )
        == "account_exhausted"
    )


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
    pending = SESSION_LIMIT.replace("❯\n", "❯ continue task\n")
    assert claude_session_limit_reset(pending) is None
    assert claude_session_limit_reset(pending, allow_pending_prompt=True) == (
        "12am",
        "Asia/Bangkok",
    )
    assert claude_session_limit_reset(
        f"{SESSION_LIMIT}\n❯ later request\nanswer\n❯ "
    ) is None
    assert claude_session_limit_reset(f"{SESSION_LIMIT}\n" + "history\n" * 31) is None
    assert classify_live_screen("codex", SESSION_LIMIT) != LiveScreenState.session_limit


def test_session_limit_accepts_nbsp_in_monitor_event_banner() -> None:
    assert claude_session_limit_reset(SESSION_LIMIT_MONITOR_EVENT) == (
        "1:20pm",
        "Asia/Bangkok",
    )
    assert (
        classify_live_screen("claude", SESSION_LIMIT_MONITOR_EVENT)
        == LiveScreenState.session_limit
    )
