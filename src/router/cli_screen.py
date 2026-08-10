"""Pure terminal-screen classifiers shared by managed and live tmux sessions."""

from __future__ import annotations

from enum import Enum
import re

from src.router.failure_classifier import classify_cli_failure


_CLAUDE_RATE_LIMIT_SCREEN_MARKERS = (
    "/rate-limit-options",
    "what do you want to do?",
    "stop and wait for limit to reset",
    "upgrade your plan",
)
_CLAUDE_WAIT_SELECTED = re.compile(
    r"(?im)^[ \t]*(?:❯|>)[ \t]*(?:1[.)]?[ \t]*)?stop and wait for limit to reset[ \t]*$"
)


class LiveScreenState(str, Enum):
    idle = "idle"
    busy = "busy"
    awaiting_input = "awaiting_input"
    rate_limit = "rate_limit"
    unknown = "unknown"


def last_prompt_line_has_content(captured: str) -> bool:
    """Return True when the bottom-most CLI composer still holds text."""
    body = str(captured or "")
    lowered = body.lower()
    for line in reversed(body.splitlines()):
        normalized = line.replace("\xa0", " ").lstrip()
        if not normalized.startswith("❯"):
            continue
        prompt_text = normalized[1:].strip()
        if not prompt_text:
            return False
        if prompt_text.lower().startswith("try ") and (
            "/model to try" in lowered or "bypass permissions on" in lowered
        ):
            return False
        return True
    return False


def line_shows_activity(line: str) -> bool:
    stripped = str(line or "").replace("\xa0", " ").strip()
    if not stripped.startswith("● "):
        return False
    content = stripped[2:].strip()
    if not content:
        return False
    if re.match(r"^[A-Z][A-Za-z0-9_-]*\(", content):
        return True
    lowered = content.lower()
    return lowered.startswith(
        (
            "running ",
            "executing ",
            "reading ",
            "writing ",
            "editing ",
            "searching ",
            "updating ",
            "creating ",
            "calling ",
            "using tool",
        )
    )


def capture_shows_activity(captured: str) -> bool:
    body = str(captured or "")
    lowered = body.lower()
    if "press up to edit queued messages" in lowered:
        return True
    if "· flowing" in lowered or "✻ " in body or "⎿" in body:
        return True
    return any(line_shows_activity(line) for line in body.splitlines())


def prompt_is_idle(captured: str) -> bool:
    body = str(captured or "")
    return "❯" in body and not capture_shows_activity(body) and not last_prompt_line_has_content(body)


def looks_like_start_screen(captured: str) -> bool:
    body = str(captured or "")
    lowered = body.lower()
    if capture_shows_activity(body):
        return False
    return (
        "welcome back" in lowered
        or "tips for getting started" in lowered
        or "/model to try opus" in lowered
        or "run /init to create" in lowered
        or "❯ try " in lowered
    )


def detect_interactive_failure_screen(cli_type: str, captured: str) -> str:
    """Return a terminal failure kind only for an interactive blocker screen."""
    failure_kind = classify_cli_failure(cli_type, captured)
    if failure_kind != "account_exhausted":
        return ""
    body = str(captured or "").lower()
    if any(marker in body for marker in _CLAUDE_RATE_LIMIT_SCREEN_MARKERS):
        return failure_kind
    return ""


def claude_wait_option_selected(captured: str) -> bool:
    """Require a visible selection cursor on Claude's WAIT option."""
    body = str(captured or "")
    lowered = body.lower()
    if "/rate-limit-options" not in lowered or "what do you want to do?" not in lowered:
        return False
    return _CLAUDE_WAIT_SELECTED.search(body) is not None


def classify_live_screen(cli_type: str, captured: str) -> LiveScreenState:
    body = str(captured or "")
    if detect_interactive_failure_screen(cli_type, body):
        return LiveScreenState.rate_limit
    if capture_shows_activity(body):
        return LiveScreenState.busy
    if prompt_is_idle(body):
        return LiveScreenState.idle
    if looks_like_start_screen(body) or last_prompt_line_has_content(body):
        return LiveScreenState.awaiting_input
    if body.strip():
        return LiveScreenState.unknown
    return LiveScreenState.awaiting_input
