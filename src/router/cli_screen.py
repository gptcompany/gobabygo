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
_CLAUDE_SESSION_LIMIT = re.compile(
    r"(?im)^[ \t]*(?:⎿[ \t]*)?you've hit your session limit[ \t]*·[ \t]*"
    r"resets[ \t]+(?P<time>(?:1[0-2]|[1-9])(?::[0-5][0-9])?[ \t]*(?:am|pm))"
    r"[ \t]*\((?P<timezone>[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)+)\)[ \t]*$"
    r"\r?\n[ \t]*/upgrade to increase your usage limit\.[ \t]*$"
)
_CLAUDE_COMPLETED_ACTIVITY = re.compile(
    r"(?im)^[ \t]*✻[ \t]+[A-Za-z][A-Za-z -]*[ \t]+for[ \t]+\d[^\n]*$"
)
_CLAUDE_ACTIVE_TURN = re.compile(
    r"(?im)^[ \t]*[●✻][ \t]+[^\n]*…[ \t]*\([^\n)]*"
    r"(?:tokens?|thinking|esc to interrupt)[^\n)]*\)[ \t]*$"
)
_CLAUDE_INTERRUPTED_TURN = re.compile(
    r"(?im)^[ \t]*(?:⎿[ \t]*)?Interrupted[ \t]*·[^\n]*$"
)
_CLAUDE_TRANSIENT_FAILURE = re.compile(
    r"(?im)^[ \t]*(?:[●⎿][ \t]*)?API Error:[ \t]*529\b[^\n]*"
    r"(?:\n[ \t]*(?:Retrying\b[^\n]*|(?:[⎿][ \t]*)?Server overloaded[ \t]*))*$"
)
_CLAUDE_SEPARATOR = re.compile(r"^[ \t]*[─━-]{20,}.*$")
_ANTIGRAVITY_AWAITING_INPUT_MARKERS = (
    "requesting permission for:",
    "do you want to proceed?",
    "select login method:",
    "press any key to go back.",
    "token exchange failed:",
)
_ANTIGRAVITY_BUSY_MARKERS = (
    "generating...",
    "esc to cancel",
)
_ANTIGRAVITY_IDLE_FOOTER = re.compile(
    r"(?m)^>[ \t]*\r?\n[^\n]*\r?\n\?[ \t]+for shortcuts[^\n]*\Z"
)


class LiveScreenState(str, Enum):
    idle = "idle"
    busy = "busy"
    awaiting_input = "awaiting_input"
    rate_limit = "rate_limit"
    session_limit = "session_limit"
    transient_failure = "transient_failure"
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


def claude_current_region(captured: str) -> str:
    """Return the composer/status region, excluding completed transcript history."""
    body = str(captured or "").replace("\xa0", " ")
    lines = body.splitlines()
    prompt_indexes = [
        index for index, line in enumerate(lines) if line.lstrip().startswith("❯")
    ]
    if not prompt_indexes:
        return body
    prompt_index = prompt_indexes[-1]
    separator_indexes = [
        index
        for index, line in enumerate(lines[:prompt_index])
        if _CLAUDE_SEPARATOR.match(line)
    ]
    start = separator_indexes[-1] + 1 if separator_indexes else 0
    prelude = "\n".join(lines[max(0, start - 6) : start])
    active = list(_CLAUDE_ACTIVE_TURN.finditer(prelude))
    ended = [
        *list(_CLAUDE_COMPLETED_ACTIVITY.finditer(prelude)),
        *list(_CLAUDE_INTERRUPTED_TURN.finditer(prelude)),
    ]
    if active and active[-1].start() > max((item.start() for item in ended), default=-1):
        return "\n".join([prelude, *lines[start:]])
    return "\n".join(lines[start:])


def claude_terminal_outcome(captured: str) -> str:
    """Return a terminal provider failure immediately above the empty composer."""
    body = str(captured or "").replace("\xa0", " ")
    lines = body.splitlines()
    prompt_indexes = [
        index for index, line in enumerate(lines) if line.lstrip().startswith("❯")
    ]
    if not prompt_indexes:
        return ""
    prompt_index = prompt_indexes[-1]
    if lines[prompt_index].lstrip()[1:].strip():
        return ""
    separators = [
        index
        for index, line in enumerate(lines[:prompt_index])
        if _CLAUDE_SEPARATOR.match(line)
    ]
    end = separators[-1] if separators else prompt_index
    previous_separator = max(
        (index for index in separators[:-1] if index < end), default=-1
    )
    candidate = "\n".join(lines[max(previous_separator + 1, end - 8) : end]).strip()
    match = _CLAUDE_TRANSIENT_FAILURE.search(candidate)
    if match is None:
        return ""
    return match.group(0) if not candidate[match.end() :].strip() else ""


def claude_screen_state(captured: str) -> LiveScreenState:
    """Classify Claude from its current composer/status region, not transcript words."""
    body = str(captured or "")
    if claude_session_limit_reset(body, allow_pending_prompt=True):
        return LiveScreenState.session_limit
    if detect_interactive_failure_screen("claude", body):
        return LiveScreenState.rate_limit
    if claude_terminal_outcome(body):
        return LiveScreenState.transient_failure
    current = claude_current_region(body)
    if _CLAUDE_ACTIVE_TURN.search(current):
        return LiveScreenState.busy
    if capture_shows_activity(current):
        return LiveScreenState.busy
    if prompt_is_idle(current):
        return LiveScreenState.idle
    if looks_like_start_screen(current) or last_prompt_line_has_content(current):
        return LiveScreenState.awaiting_input
    return LiveScreenState.unknown if body.strip() else LiveScreenState.awaiting_input


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
    if str(cli_type or "").strip().lower() in {"antigravity", "codex"}:
        return failure_kind
    if any(marker in body for marker in _CLAUDE_RATE_LIMIT_SCREEN_MARKERS):
        return failure_kind
    return ""


def antigravity_screen_state(captured: str) -> LiveScreenState:
    """Classify a current Antigravity TUI capture using observed 1.1.x markers."""
    body = str(captured or "")
    if not body.strip():
        return LiveScreenState.awaiting_input
    lowered = body.lower()
    if _ANTIGRAVITY_IDLE_FOOTER.search(body.rstrip()):
        return LiveScreenState.idle
    if any(marker in lowered for marker in _ANTIGRAVITY_AWAITING_INPUT_MARKERS):
        return LiveScreenState.awaiting_input
    if detect_interactive_failure_screen("antigravity", body):
        return LiveScreenState.rate_limit
    if any(marker in lowered for marker in _ANTIGRAVITY_BUSY_MARKERS):
        return LiveScreenState.busy
    return LiveScreenState.unknown


def claude_wait_option_selected(captured: str) -> bool:
    """Require a visible selection cursor on Claude's WAIT option."""
    body = str(captured or "")
    lines = body.splitlines()
    menu_indexes = [
        index for index, line in enumerate(lines) if "/rate-limit-options" in line.lower()
    ]
    if not menu_indexes:
        return False
    menu_index = menu_indexes[-1]
    if len(lines) - menu_index > 40:
        return False
    question_index = next(
        (
            index
            for index in range(menu_index + 1, len(lines))
            if "what do you want to do?" in lines[index].lower()
        ),
        -1,
    )
    if question_index < 0:
        return False
    selected_index = next(
        (
            index
            for index in range(question_index + 1, len(lines))
            if _CLAUDE_WAIT_SELECTED.search(lines[index]) is not None
        ),
        -1,
    )
    if selected_index < 0:
        return False
    trailing = lines[selected_index + 1 :]
    if any(line.replace("\xa0", " ").lstrip().startswith("❯") for line in trailing):
        return False
    return not capture_shows_activity("\n".join(trailing))


def claude_session_limit_reset(
    captured: str, *, allow_pending_prompt: bool = False
) -> tuple[str, str] | None:
    """Return the current session-limit reset label and IANA timezone."""
    body = str(captured or "").replace("\xa0", " ")
    matches = list(_CLAUDE_SESSION_LIMIT.finditer(body))
    if not matches:
        return None
    match = matches[-1]
    trailing = _CLAUDE_COMPLETED_ACTIVITY.sub("", body[match.end() :])
    if len(trailing.splitlines()) > 30:
        return None
    prompts = [
        line.replace("\xa0", " ").lstrip()
        for line in trailing.splitlines()
        if line.replace("\xa0", " ").lstrip().startswith("❯")
    ]
    if len(prompts) != 1:
        return None
    pending_prompt = bool(prompts[0][1:].strip())
    if pending_prompt and not allow_pending_prompt:
        return None
    if pending_prompt:
        if capture_shows_activity(trailing):
            return None
    elif not prompt_is_idle(trailing):
        return None
    return match.group("time").lower().replace(" ", ""), match.group("timezone")


def classify_live_screen(cli_type: str, captured: str) -> LiveScreenState:
    body = str(captured or "")
    provider = str(cli_type or "").strip().lower()
    if provider == "antigravity":
        return antigravity_screen_state(body)
    if provider == "claude":
        return claude_screen_state(body)
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
