"""Interactive session worker (tmux-backed) for Claude/Codex/Gemini CLIs.

Unlike the batch worker (`worker_client.py`), this worker launches a long-lived
interactive CLI session inside tmux, persists a session record in the router DB
via `/sessions/*`, and allows operator/orchestrator messages to be delivered via
the session message bus.

Human approval gates remain native to each CLI (manual/yolo/etc. config).
This worker focuses on orchestration + persistence + attachability.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

from src.router.failure_classifier import classify_cli_failure
from src.router.models import CrossRoleMessageType, MessageEnvelope, RoleState
from src.router.provider_runtime import resolve_cli_command
from src.router.workdir_guard import parse_allowed_work_dirs, resolve_work_dir

logger = logging.getLogger("mesh.session_worker")
_CLAUDE_CODE_READY_MARKERS = ("❯",)
_INBOUND_PROXY_PREFIX = "__mesh_inbound__:"
_CLAUDE_RATE_LIMIT_SCREEN_MARKERS = (
    "/rate-limit-options",
    "what do you want to do?",
    "stop and wait for limit to reset",
    "upgrade your plan",
)


class SessionNotFoundError(RuntimeError):
    """Raised when the router no longer has a record for a session."""


def _sanitize_session_name(value: str) -> str:
    """Return tmux-safe session name (ASCII-ish, bounded length)."""
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return (s or "mesh-session")[:64]


def _default_mesh_home() -> str:
    """Return the control-repo root used to expose the mesh CLI to sessions."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _default_operator_ui_config_path(mesh_home: str | None = None) -> str:
    base = (mesh_home or "").strip() or os.environ.get("MESH_HOME", "").strip() or _default_mesh_home()
    return os.path.join(base, "mapping", "operator_ui.yaml")


def _mesh_script_path(mesh_home: str | None = None) -> str:
    base = (mesh_home or "").strip() or os.environ.get("MESH_HOME", "").strip() or _default_mesh_home()
    return os.path.join(base, "scripts", "mesh")


def _relay_proxy_script_path(mesh_home: str | None = None) -> str:
    base = (mesh_home or "").strip() or os.environ.get("MESH_HOME", "").strip() or _default_mesh_home()
    return os.path.join(base, "scripts", "mesh_prompt_relay_proxy.py")


def _claude_router_hook_script_path(mesh_home: str | None = None) -> str:
    base = (mesh_home or "").strip() or os.environ.get("MESH_HOME", "").strip() or _default_mesh_home()
    return os.path.join(base, "scripts", "mesh_claude_router_hook.py")


def _claude_settings_local_path(work_dir: str) -> str:
    return os.path.join(work_dir, ".claude", "settings.local.json")


def _claude_command_path(work_dir: str, command_name: str) -> str:
    return os.path.join(work_dir, ".claude", "commands", f"{command_name}.md")


def _build_claude_gbg_command() -> str:
    return (
        "---\n"
        "description: Route a Gobabygo handoff. Usage: /gbg [role] [text]\n"
        "argument-hint: [role] [text]\n"
        "disable-model-invocation: true\n"
        "---\n\n"
        "# /gbg\n\n"
        "Use `/gbg` to route a handoff through the mesh runtime.\n\n"
        "Forms:\n"
        "- `/gbg`\n"
        "  Route your last useful assistant response to the default target.\n"
        "- `/gbg <text>`\n"
        "  Route `<text>` to the default target.\n"
        "- `/gbg <role> <text>`\n"
        "  Route `<text>` to an explicit role when you have multiple peers.\n\n"
        "Respond with at most one short acknowledgement. Do not explain the protocol.\n"
        "If you print a machine-readable trailer, use only this final line format:\n\n"
        "`GBG: {\"message\":\"...\"}`\n\n"
        "Rules:\n"
        "1. If the command already contains explicit text, the runtime will use that text directly.\n"
        "2. If the command has no text, the runtime will use your previous useful assistant response.\n"
        "3. Do not invent extra routing metadata in natural language.\n"
        "4. Do not output anything after a `GBG: {...}` trailer.\n"
    )


def _ensure_claude_project_command(
    work_dir: str,
    *,
    command_name: str,
    content: str,
) -> str | None:
    command_path = Path(_claude_command_path(work_dir, command_name))
    try:
        command_path.parent.mkdir(parents=True, exist_ok=True)
        command_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to install Claude command %s in %s: %s", command_name, work_dir, exc)
        return None
    return str(command_path)


def _build_claude_mesh_hook_settings(mesh_home: str | None = None) -> dict[str, object]:
    hook_script = _claude_router_hook_script_path(mesh_home)
    hook_python = shlex.quote(sys.executable)
    hook_path = shlex.quote(hook_script)

    def _hook_entry(event_name: str, timeout: int) -> dict[str, object]:
        return {
            "matcher": "*",
            "hooks": [
                {
                    "type": "command",
                    "command": f"{hook_python} {hook_path} {shlex.quote(event_name)}",
                    "timeout": timeout,
                }
            ],
        }

    return {
        "hooks": {
            "UserPromptSubmit": [_hook_entry("UserPromptSubmit", 3)],
            "Stop": [_hook_entry("Stop", 5)],
            "Notification": [_hook_entry("Notification", 3)],
        }
    }


def _merge_claude_mesh_hook_settings(
    existing: dict[str, object] | None,
    *,
    mesh_home: str | None = None,
) -> dict[str, object]:
    merged = dict(existing or {})
    hooks = merged.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        merged["hooks"] = hooks

    desired = _build_claude_mesh_hook_settings(mesh_home).get("hooks", {})
    if not isinstance(desired, dict):
        return merged

    for event_name, desired_entries in desired.items():
        if not isinstance(desired_entries, list):
            continue
        current_entries = hooks.get(event_name)
        if not isinstance(current_entries, list):
            current_entries = []
            hooks[event_name] = current_entries
        existing_commands = {
            str(hook.get("command") or "").strip()
            for entry in current_entries
            if isinstance(entry, dict)
            for hook in entry.get("hooks", [])
            if isinstance(hook, dict)
        }
        for entry in desired_entries:
            if not isinstance(entry, dict):
                continue
            hook_defs = entry.get("hooks", [])
            if not isinstance(hook_defs, list):
                continue
            entry_commands = [
                str(hook.get("command") or "").strip()
                for hook in hook_defs
                if isinstance(hook, dict)
            ]
            if any(command and command in existing_commands for command in entry_commands):
                continue
            current_entries.append(entry)
            existing_commands.update(command for command in entry_commands if command)
    return merged


def _ensure_claude_mesh_hook_settings(work_dir: str, *, mesh_home: str | None = None) -> str | None:
    settings_path = Path(_claude_settings_local_path(work_dir))
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, object] = {}
        if settings_path.is_file():
            with settings_path.open(encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                existing = loaded
        merged = _merge_claude_mesh_hook_settings(existing, mesh_home=mesh_home)
        with settings_path.open("w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2, ensure_ascii=True)
            fh.write("\n")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Failed to install Claude mesh hook settings in %s: %s", work_dir, exc)
        return None
    return str(settings_path)


def _parse_upterm_ssh_url(output: str) -> str | None:
    """Extract ssh:// URL from ``upterm session current`` output."""
    for line in output.splitlines():
        m = re.search(r"ssh://\S+", line)
        if m:
            return m.group(0)
    return None


def _compute_output_emit(
    previous_capture: str,
    current_capture: str,
    *,
    max_chars: int = 8000,
) -> tuple[str, dict] | None:
    """Compute an output message payload from tmux pane snapshots.

    Returns `(content, metadata)` or `None` if nothing should be emitted.
    Heuristic:
    - unchanged/empty => no emit
    - prefix-growth => emit delta only
    - otherwise => emit bounded snapshot (screen redraw / scroll / reflow)
    """
    prev = (previous_capture or "").strip()
    cur = (current_capture or "").strip()
    if not cur or cur == prev:
        return None

    if prev and cur.startswith(prev):
        delta = cur[len(prev):].lstrip("\n")
        if not delta:
            return None
        return delta[-max_chars:], {
            "snapshot": False,
            "kind": "delta",
            "chars": len(delta),
        }

    return cur[-max_chars:], {
        "snapshot": True,
        "kind": "snapshot",
        "chars": len(cur),
    }


def _last_prompt_line_has_content(captured: str) -> bool:
    """Return True when the bottom-most Claude Code composer still holds text."""
    lowered_capture = str(captured or "").lower()
    for line in reversed((captured or "").splitlines()):
        normalized = line.replace("\xa0", " ").lstrip()
        if normalized.startswith("❯"):
            prompt_text = normalized[1:].strip()
            if not prompt_text:
                return False
            # Gemini home can render a suggestion row like `❯ Try "..."` above the
            # real empty composer; that row should not be treated as pending input.
            if (
                prompt_text.lower().startswith("try ")
                and (
                    "/model to try" in lowered_capture
                    or "bypass permissions on" in lowered_capture
                )
            ):
                return False
            return True
    return False


def _coerce_bool(value: object, *, default: bool = False) -> bool:
    """Parse common JSON/env-ish truthy values."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return default


def _coerce_string_list(value: object) -> list[str]:
    """Normalize a string or list payload field into non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for raw in value:
            item = str(raw).strip()
            if item:
                items.append(item)
        return items
    item = str(value).strip()
    return [item] if item else []


def _coerce_relay_config(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    enabled = _coerce_bool(value.get("enabled"), default=False)
    if not enabled:
        return {}
    target_role = str(value.get("target_role", "")).strip()
    if not target_role:
        return {}
    config: dict[str, object] = {
        "enabled": True,
        "mode": str(value.get("mode", "prompt_submit")).strip() or "prompt_submit",
        "target_role": target_role,
    }
    if _coerce_bool(value.get("ignore_slash_commands"), default=False):
        config["ignore_slash_commands"] = True
    message_prefix = str(value.get("message_prefix", "")).strip()
    if message_prefix:
        config["message_prefix"] = message_prefix
    passthrough_to_child = value.get("passthrough_to_child")
    if passthrough_to_child is not None and not _coerce_bool(passthrough_to_child, default=True):
        config["passthrough_to_child"] = False
    local_ack = str(value.get("local_ack", "")).strip()
    if local_ack:
        config["local_ack"] = local_ack
    return config


def _load_role_relay_from_mapping(role: str, *, mesh_home: str | None = None) -> dict[str, object]:
    role_name = str(role or "").strip()
    if not role_name:
        return {}
    path = _default_operator_ui_config_path(mesh_home)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}
    roles = raw.get("roles")
    if not isinstance(roles, dict):
        return {}
    entry = roles.get(role_name)
    if not isinstance(entry, dict):
        return {}
    return _coerce_relay_config(entry.get("relay"))


def _wrap_cli_command_with_relay_proxy(
    child_command: str,
    *,
    relay: dict[str, object],
    ui_group_id: str,
    ui_role: str,
    mesh_home: str | None = None,
) -> str:
    if not child_command.strip() or not ui_group_id.strip():
        return child_command
    mode = str(relay.get("mode", "prompt_submit")).strip() or "prompt_submit"
    if mode not in {"prompt_submit", "response_summary", "router_relay"}:
        return child_command

    command = [
        shlex.quote(sys.executable),
        shlex.quote(_relay_proxy_script_path(mesh_home)),
        "--mode",
        shlex.quote(mode),
        "--target-role",
        shlex.quote(str(relay.get("target_role", "")).strip()),
        "--ui-group-id",
        shlex.quote(ui_group_id),
        "--mesh-script",
        shlex.quote(_mesh_script_path(mesh_home)),
        "--source-role",
        shlex.quote(ui_role),
    ]
    if _coerce_bool(relay.get("ignore_slash_commands"), default=False):
        command.append("--ignore-slash-commands")
    message_prefix = str(relay.get("message_prefix", "")).strip()
    if message_prefix:
        command.extend(["--message-prefix", shlex.quote(message_prefix)])
    if not _coerce_bool(relay.get("passthrough_to_child"), default=True):
        command.append("--no-child-passthrough")
    local_ack = str(relay.get("local_ack", "")).strip()
    if local_ack:
        command.extend(["--local-ack", shlex.quote(local_ack)])
    command.extend(["--child-command", shlex.quote(child_command)])
    return " ".join(command)


def _prompt_is_idle(captured: str) -> bool:
    """Return True when Claude Code is back at an empty ready prompt."""
    body = str(captured or "")
    return "❯" in body and not _last_prompt_line_has_content(body)


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _prompt_snippet(prompt: str, *, max_chars: int = 48) -> str:
    for line in str(prompt or "").splitlines():
        normalized = _normalize_ws(line)
        if normalized:
            return normalized[:max_chars]
    return ""


def _capture_contains_prompt_text(captured: str, prompt: str) -> bool:
    snippet = _prompt_snippet(prompt)
    if not snippet:
        return False
    return snippet in _normalize_ws(captured)


def _capture_shows_activity(captured: str) -> bool:
    body = str(captured or "")
    lowered = body.lower()
    if "press up to edit queued messages" in lowered:
        return True
    if "· flowing" in lowered or "✻ " in body or "⎿" in body:
        return True
    return any(_line_shows_activity(line) for line in body.splitlines())


def _line_shows_activity(line: str) -> bool:
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


def _looks_like_start_screen(captured: str) -> bool:
    body = str(captured or "")
    lowered = body.lower()
    if _capture_shows_activity(body):
        return False
    return (
        "welcome back" in lowered
        or "tips for getting started" in lowered
        or "/model to try opus" in lowered
        or "run /init to create" in lowered
        or "❯ try " in body.lower()
    )


def _should_auto_exit_on_success(
    captured: str,
    success_markers: list[str],
    *,
    baseline_capture: str = "",
    delta_text: str = "",
) -> bool:
    """Return True when the requested success markers are visible at an idle prompt."""
    if not success_markers:
        return False
    if not _prompt_is_idle(captured):
        return False
    baseline = str(baseline_capture or "")
    delta = str(delta_text or "")
    for marker in success_markers:
        if _count_marker_lines(delta, marker) > 0:
            return True
        if _count_marker_lines(captured, marker) > _count_marker_lines(baseline, marker):
            return True
    return False


def _count_marker_lines(text: str, marker: str) -> int:
    normalized_marker = str(marker or "").strip()
    if not normalized_marker:
        return 0
    accepted = {
        normalized_marker,
        f"● {normalized_marker}",
        f"• {normalized_marker}",
        f"- {normalized_marker}",
        f"* {normalized_marker}",
    }
    count = 0
    for line in str(text or "").splitlines():
        if line.strip() in accepted:
            count += 1
    return count


def _success_file_matches(
    work_dir: str,
    success_file_path: str,
    success_file_contains: str = "",
    *,
    min_mtime_ns: int | None = None,
) -> bool:
    path = str(success_file_path or "").strip()
    if not path:
        return False
    resolved = path if os.path.isabs(path) else os.path.join(work_dir, path)
    if not os.path.isfile(resolved):
        return False
    try:
        stat = os.stat(resolved)
    except OSError:
        return False
    if min_mtime_ns is not None and stat.st_mtime_ns <= min_mtime_ns:
        return False
    marker = str(success_file_contains or "")
    if not marker:
        return True
    try:
        with open(resolved, encoding="utf-8") as fh:
            return marker in fh.read()
    except OSError:
        return False


def _detect_interactive_failure_screen(cli_type: str, captured: str) -> str:
    """Return a failure kind when the live TUI is stuck on a terminal error screen."""
    failure_kind = classify_cli_failure(cli_type, captured)
    if failure_kind != "account_exhausted":
        return ""
    body = str(captured or "").lower()
    if any(marker in body for marker in _CLAUDE_RATE_LIMIT_SCREEN_MARKERS):
        return failure_kind
    return ""


def _discover_project_mcp_servers(work_dir: str) -> list[str]:
    """Return MCP server names declared by ``work_dir/.mcp.json``."""
    mcp_path = os.path.join(work_dir, ".mcp.json")
    if not os.path.isfile(mcp_path):
        return []
    try:
        with open(mcp_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    return [str(name).strip() for name in servers.keys() if str(name).strip()]


def _default_completion_summary_text(role: str, status: str) -> str:
    role_name = role or "role"
    if status == "completed":
        return f"{role_name} completed."
    return f"{role_name} failed."


def _default_completion_summary_targets(role: str) -> list[str]:
    if role in {"worker-gemini", "worker-claude", "worker-codex", "verifier"}:
        return ["lead"]
    if role == "lead":
        return ["president", "boss"]
    if role == "president":
        return ["boss"]
    return []


def _encode_inbound_proxy_message(content: str, *, source_role: str) -> str:
    role = str(source_role or "").strip() or "peer"
    return f"{_INBOUND_PROXY_PREFIX}{role}:{content}"


def _format_inbound_notice(content: str, *, source_role: str, max_chars: int = 240) -> str:
    role = str(source_role or "").strip() or "peer"
    clean = " ".join(str(content or "").replace("\xa0", " ").split())
    clean = clean[: max(32, int(max_chars))].strip()
    return f"[mesh][{role}] {clean}" if clean else f"[mesh][{role}]"


def _detect_role_state(captured: str) -> RoleState:
    body = str(captured or "")
    if not body.strip():
        return RoleState.awaiting_input
    if _capture_shows_activity(body):
        return RoleState.responding
    if _prompt_is_idle(body):
        return RoleState.idle
    if _looks_like_start_screen(body) or _last_prompt_line_has_content(body):
        return RoleState.awaiting_input
    return RoleState.responding


def _extract_clean_response(text: str, *, max_chars: int = 1200) -> str:
    lines: list[str] = []
    for raw in str(text or "").splitlines():
        line = raw.replace("\xa0", " ").strip()
        if not line:
            continue
        if line.startswith("❯"):
            continue
        if line.startswith("✻"):
            continue
        if line.startswith("⎿"):
            continue
        if line.startswith("Stop says:"):
            continue
        if line.startswith(_INBOUND_PROXY_PREFIX):
            continue
        lines.append(line)
    summary = " ".join(lines).strip()
    if not summary:
        return ""
    return summary[-max(1, int(max_chars)) :]


def _relay_mode_uses_claude_hooks(relay: dict[str, object], cli_type: str) -> bool:
    mode = str(relay.get("mode", "")).strip()
    if mode != "claude_hooks":
        return False
    return str(cli_type or "").strip() in {"claude", "gemini"}


@dataclass
class SessionWorkerConfig:
    """Configuration for a tmux-backed interactive session worker."""

    worker_id: str = "ws-unknown-session-01"
    router_url: str = "http://localhost:8780"
    cli_type: str = "claude"
    account_profile: str = "work"
    auth_token: str | None = None
    heartbeat_interval: float = 5.0
    heartbeat_timeout: float = 10.0
    control_plane_timeout: float = 60.0
    longpoll_timeout: float = 25.0
    capabilities: list[str] = field(
        default_factory=lambda: ["code", "tests", "refactor", "interactive", "ui_role"]
    )
    allowed_accounts: list[str] = field(default_factory=list)  # MESH_ALLOWED_ACCOUNTS=foo,bar,*
    allowed_work_dirs: list[str] = field(default_factory=list)  # MESH_ALLOWED_WORK_DIRS=/repo/root,/tmp/mesh-tasks
    execution_modes: list[str] = field(default_factory=lambda: ["session"])
    cli_command: str = "claude"  # supports {target_account}, {account_profile}, {worker_account_profile}
    provider_runtime_config: str | None = None  # None=repo default, ""=disabled
    work_dir: str = "/tmp/mesh-tasks"
    session_poll_interval_s: float = 1.0
    startup_ready_timeout_s: float = 10.0
    startup_ready_poll_interval_s: float = 0.25
    startup_post_launch_settle_s: float = 0.35
    tmux_send_settle_s: float = 0.1
    prompt_submit_retry_count: int = 3
    prompt_submit_retry_poll_s: float = 1.0
    tmux_bin: str = "tmux"
    tmux_capture_lines: int = 200
    output_emit_max_chars: int = 8000
    tmux_session_prefix: str = "mesh"
    task_timeout: int = 7200  # Hard ceiling for interactive sessions (2h)
    auto_complete_on_exit: bool = True
    runtime_state_dir: str = field(
        default_factory=lambda: os.path.join(os.path.expanduser("~"), ".cache", "gobabygo")
    )
    upterm_bin: str = "upterm"
    upterm_server: str = ""
    upterm_ready_timeout: float = 10.0
    upterm_accept: bool = True
    upterm_skip_host_key_check: bool = True
    ssh_tmux_user: str = ""
    ssh_tmux_host: str = ""

    @classmethod
    def from_env(cls) -> SessionWorkerConfig:
        raw_caps = os.environ.get("MESH_CAPABILITIES", "").strip()
        capabilities = (
            [c.strip() for c in raw_caps.split(",") if c.strip()]
            if raw_caps
            else ["code", "tests", "refactor", "interactive", "ui_role"]
        )
        if "ui_role" not in capabilities:
            capabilities.append("ui_role")
        raw_allowed = os.environ.get("MESH_ALLOWED_ACCOUNTS", "").strip()
        allowed_accounts = [a.strip() for a in raw_allowed.split(",") if a.strip()]
        allowed_work_dirs = parse_allowed_work_dirs(
            os.environ.get("MESH_ALLOWED_WORK_DIRS", "").strip(),
            default_work_dir=os.environ.get("MESH_WORK_DIR", "/tmp/mesh-tasks"),
        )
        return cls(
            worker_id=os.environ.get("MESH_WORKER_ID", "ws-unknown-session-01"),
            router_url=os.environ.get("MESH_ROUTER_URL", "http://localhost:8780"),
            cli_type=os.environ.get("MESH_CLI_TYPE", "claude"),
            account_profile=os.environ.get("MESH_ACCOUNT_PROFILE", "work"),
            auth_token=os.environ.get("MESH_AUTH_TOKEN"),
            capabilities=capabilities,
            allowed_accounts=allowed_accounts,
            allowed_work_dirs=allowed_work_dirs,
            heartbeat_timeout=float(os.environ.get("MESH_HEARTBEAT_TIMEOUT_S", "10")),
            control_plane_timeout=float(os.environ.get("MESH_CONTROL_PLANE_TIMEOUT_S", "60")),
            longpoll_timeout=float(os.environ.get("MESH_LONGPOLL_TIMEOUT_S", "25")),
            cli_command=os.environ.get("MESH_CLI_COMMAND", "claude"),
            provider_runtime_config=os.environ.get("MESH_PROVIDER_RUNTIME_CONFIG"),
            execution_modes=[
                m.strip() for m in os.environ.get("MESH_EXECUTION_MODES", "session").split(",")
                if m.strip()
            ] or ["session"],
            work_dir=os.environ.get("MESH_WORK_DIR", "/tmp/mesh-tasks"),
            session_poll_interval_s=float(os.environ.get("MESH_SESSION_POLL_INTERVAL_S", "1.0")),
            startup_ready_timeout_s=float(os.environ.get("MESH_SESSION_READY_TIMEOUT_S", "10.0")),
            startup_ready_poll_interval_s=float(
                os.environ.get("MESH_SESSION_READY_POLL_INTERVAL_S", "0.25")
            ),
            startup_post_launch_settle_s=float(
                os.environ.get("MESH_SESSION_POST_LAUNCH_SETTLE_S", "0.35")
            ),
            tmux_send_settle_s=float(os.environ.get("MESH_TMUX_SEND_SETTLE_S", "0.1")),
            prompt_submit_retry_count=int(
                os.environ.get("MESH_PROMPT_SUBMIT_RETRY_COUNT", "3")
            ),
            prompt_submit_retry_poll_s=float(
                os.environ.get("MESH_PROMPT_SUBMIT_RETRY_POLL_S", "1.0")
            ),
            tmux_bin=os.environ.get("MESH_TMUX_BIN", "tmux"),
            tmux_capture_lines=int(os.environ.get("MESH_TMUX_CAPTURE_LINES", "200")),
            output_emit_max_chars=int(os.environ.get("MESH_OUTPUT_EMIT_MAX_CHARS", "8000")),
            tmux_session_prefix=os.environ.get("MESH_TMUX_SESSION_PREFIX", "mesh"),
            task_timeout=int(os.environ.get("MESH_TASK_TIMEOUT_S", "7200")),
            auto_complete_on_exit=os.environ.get("MESH_AUTO_COMPLETE_ON_EXIT", "1").strip() != "0",
            runtime_state_dir=os.environ.get(
                "MESH_RUNTIME_STATE_DIR",
                os.path.join(os.path.expanduser("~"), ".cache", "gobabygo"),
            ),
            upterm_bin=os.environ.get("MESH_UPTERM_BIN", "upterm"),
            upterm_server=os.environ.get("MESH_UPTERM_SERVER", ""),
            upterm_ready_timeout=float(os.environ.get("MESH_UPTERM_READY_TIMEOUT", "10.0")),
            upterm_accept=os.environ.get("MESH_UPTERM_ACCEPT", "1").strip() != "0",
            upterm_skip_host_key_check=os.environ.get("MESH_UPTERM_SKIP_HOST_KEY_CHECK", "1").strip() != "0",
            ssh_tmux_user=os.environ.get("MESH_SSH_TMUX_USER", ""),
            ssh_tmux_host=os.environ.get("MESH_SSH_TMUX_HOST", ""),
        )

    def registration_capabilities(self) -> list[str]:
        """Capabilities sent to router during register (with optional account allowlist)."""
        caps = list(self.capabilities)
        if self.allowed_accounts:
            for account in self.allowed_accounts:
                if account == "*":
                    caps.append("account:*")
                else:
                    caps.append(f"account:{account}")
        return list(dict.fromkeys(caps))


class MeshSessionWorker:
    """Worker that runs interactive CLI tasks in tmux and persists session bus state."""

    def __init__(self, config: SessionWorkerConfig) -> None:
        self.config = config
        self._running = False
        self._heartbeat_thread: threading.Thread | None = None
        self._http = requests.Session()
        if config.auth_token:
            self._http.headers["Authorization"] = f"Bearer {config.auth_token}"
        self._http.headers["Content-Type"] = "application/json"

    def start(self) -> None:
        self._running = True
        if not self._register_until_available():
            return
        self._start_heartbeat()
        self._poll_loop()

    def stop(self) -> None:
        logger.info("Stopping session worker %s...", self.config.worker_id)
        self._running = False
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=10)
        self._deregister()

    def _register(self) -> None:
        payload = {
            "worker_id": self.config.worker_id,
            "machine": os.environ.get("HOSTNAME", "unknown"),
            "cli_type": self.config.cli_type,
            "account_profile": self.config.account_profile,
            "capabilities": self.config.registration_capabilities(),
            "execution_modes": self.config.execution_modes,
            "status": "idle",
            "concurrency": 1,
        }
        resp = self._http.post(
            f"{self.config.router_url}/register",
            json=payload,
            timeout=self.config.control_plane_timeout,
        )
        resp.raise_for_status()
        if resp.status_code == 200:
            logger.info("Re-registered session worker %s", self.config.worker_id)
        else:
            logger.info("Registered session worker %s", self.config.worker_id)

    def _register_until_available(self) -> bool:
        """Keep retrying registration while the worker should remain running."""
        backoff = 1.0
        while self._running:
            try:
                self._register()
                return True
            except requests.RequestException as exc:
                logger.warning(
                    "Initial session worker registration failed for %s: %s; retrying in %.1fs",
                    self.config.worker_id,
                    exc,
                    backoff,
                )
                time.sleep(backoff + random.uniform(0.1, 0.5))
                backoff = min(backoff * 2.0, 30.0)
        return False

    def _deregister(self) -> None:
        """Best-effort router retirement during worker shutdown."""
        try:
            resp = self._http.post(
                f"{self.config.router_url}/workers/{self.config.worker_id}/deregister",
                timeout=self.config.control_plane_timeout,
            )
            if resp.status_code not in (200, 404):
                logger.warning(
                    "Session worker %s deregister returned %d",
                    self.config.worker_id,
                    resp.status_code,
                )
        except requests.RequestException as e:
            logger.warning("Session worker %s deregister failed: %s", self.config.worker_id, e)

    def _start_heartbeat(self) -> None:
        def heartbeat_loop() -> None:
            url = f"{self.config.router_url}/heartbeat"
            while self._running:
                try:
                    self._http.post(
                        url,
                        json={"worker_id": self.config.worker_id},
                        timeout=self.config.heartbeat_timeout,
                    )
                except requests.RequestException as e:
                    logger.warning("Heartbeat failed: %s", e)
                time.sleep(self.config.heartbeat_interval)

        self._heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _poll_loop(self) -> None:
        url = f"{self.config.router_url}/tasks/next?worker_id={self.config.worker_id}"
        backoff = 0.0
        while self._running:
            if backoff > 0:
                time.sleep(backoff + random.uniform(0.1, 0.5))
            try:
                resp = self._http.get(
                    url,
                    timeout=max(self.config.longpoll_timeout + 5, self.config.control_plane_timeout),
                )
                if resp.status_code == 200:
                    backoff = 0.0
                    try:
                        task = resp.json()
                    except ValueError:
                        logger.warning("Poll returned non-JSON response")
                        continue
                    self._execute_task(task)
                    continue
                if resp.status_code == 204:
                    backoff = 0.0
                    time.sleep(random.uniform(0.1, 0.5))
                    continue
                if resp.status_code == 409:
                    logger.warning("Duplicate poll detected, backing off")
                    backoff = min(backoff * 2 or 1.0, 30.0)
                    continue
                logger.warning("Poll returned %d", resp.status_code)
                backoff = min(backoff * 2 or 1.0, 30.0)
            except requests.RequestException as e:
                logger.warning("Poll failed: %s", e)
                backoff = min(backoff * 2 or 1.0, 30.0)

    def _execute_task(self, task: dict) -> None:
        task_id = task["task_id"]
        payload = task.get("payload", {})
        prompt = str(payload.get("prompt", ""))
        ui_role_session = _coerce_bool(payload.get("ui_role_session"), default=False)
        ui_role = str(payload.get("ui_role") or task.get("role") or "").strip()
        role_for_relay = ui_role or str(task.get("role") or "").strip()
        ui_group_id = str(payload.get("ui_group_id") or "").strip()
        relay = _coerce_relay_config(payload.get("relay"))
        if not relay and role_for_relay:
            relay = _load_role_relay_from_mapping(role_for_relay)
        if not relay and role_for_relay == "boss":
            relay = {
                "enabled": True,
                "mode": "router_relay",
                "target_role": "president",
                "ignore_slash_commands": True,
                "passthrough_to_child": True,
            }
        execution_mode = str(task.get("execution_mode", "batch")).strip() or "batch"
        target_account = str(task.get("target_account") or self.config.account_profile).strip() or self.config.account_profile
        requested_work_dir = payload.get("working_dir", self.config.work_dir)
        auto_exit_on_success = _coerce_bool(payload.get("auto_exit_on_success"), default=False)
        success_markers = _coerce_string_list(
            payload.get("success_markers", payload.get("success_marker"))
        )
        allow_text_success_markers = _coerce_bool(
            payload.get("allow_text_success_markers"), default=False
        )
        success_file_path = str(payload.get("success_file_path") or "").strip()
        success_file_contains = str(payload.get("success_file_contains") or "")
        success_file_min_mtime_ns = time.time_ns() if success_file_path else None
        exit_command = str(payload.get("exit_command") or "/exit").strip() or "/exit"
        cli_args = _coerce_string_list(payload.get("cli_args"))
        relay_uses_claude_hooks = _relay_mode_uses_claude_hooks(relay, self.config.cli_type)
        preallocated_session_id = str(uuid.uuid4())

        logger.info("Starting interactive task %s (%s)", task_id, task.get("title", "untitled"))

        if not self._ack_task(task_id):
            return

        session_id: str | None = None
        tmux_session_name: str | None = None
        upterm_proc: subprocess.Popen | None = None
        final_snapshot = ""

        try:
            if execution_mode != "session":
                self._report_failure(task_id, f"unsupported execution_mode={execution_mode} for session worker")
                return
            if not prompt and not ui_role_session:
                self._report_failure(task_id, "missing payload.prompt")
                return
            if success_markers and not success_file_path and not allow_text_success_markers:
                logger.warning(
                    "Task %s requested marker-based auto-exit without success_file_path; disabling unstructured marker checks",
                    task_id,
                )
                success_markers = []

            work_dir = resolve_work_dir(
                requested_work_dir,
                default_work_dir=self.config.work_dir,
                allowed_roots=self.config.allowed_work_dirs,
            )
            if os.path.isdir(work_dir):
                pass
            else:
                os.makedirs(work_dir, exist_ok=True)
            cmd_base = resolve_cli_command(
                cli_type=self.config.cli_type,
                target_account=target_account,
                worker_account_profile=self.config.account_profile,
                fallback_command=self.config.cli_command,
                config_path=self.config.provider_runtime_config,
            )
            if prompt and self.config.cli_type != "codex":
                cli_args = [*cli_args, "--append-system-prompt", prompt]
            if cli_args:
                cmd_base = " ".join([cmd_base, *[shlex.quote(arg) for arg in cli_args]])
            if relay_uses_claude_hooks:
                hook_settings_path = _ensure_claude_mesh_hook_settings(
                    work_dir,
                    mesh_home=_default_mesh_home(),
                )
                if not hook_settings_path or not os.path.isfile(hook_settings_path):
                    self._report_failure(
                        task_id,
                        f"failed to install Claude mesh hook settings in {work_dir}",
                    )
                    return
                gbg_command_path = _ensure_claude_project_command(
                    work_dir,
                    command_name="gbg",
                    content=_build_claude_gbg_command(),
                )
                if not gbg_command_path or not os.path.isfile(gbg_command_path):
                    self._report_failure(
                        task_id,
                        f"failed to install Claude command /gbg in {work_dir}",
                    )
                    return
            elif relay:
                cmd_base = _wrap_cli_command_with_relay_proxy(
                    cmd_base,
                    relay=relay,
                    ui_group_id=ui_group_id,
                    ui_role=role_for_relay,
                )
            self._prepare_cli_runtime(work_dir, target_account)
            tmux_session_name = self._tmux_session_name(task_id, target_account)
            bootstrap_prompt_via_stdin = bool(prompt) and self.config.cli_type == "codex"
            prompt_delivery_requires_composer = bootstrap_prompt_via_stdin
            if self._tmux_has_session(tmux_session_name):
                logger.warning(
                    "Killing stale tmux session before retry: %s",
                    tmux_session_name,
                )
                self._tmux_kill_session(tmux_session_name)
            self._tmux_new_session(
                tmux_session_name,
                work_dir,
                cmd_base,
                initial_stdin=prompt if bootstrap_prompt_via_stdin else None,
                extra_env={
                    "MESH_UI_GROUP_ID": ui_group_id,
                    "MESH_UI_ROLE": ui_role,
                    "MESH_UI_REPO_NAME": os.path.basename(str(task.get("repo") or work_dir).rstrip("/")),
                    "MESH_RELAY_MODE": str(relay.get("mode") or "").strip(),
                    "MESH_RELAY_TARGET_ROLE": str(relay.get("target_role") or "").strip(),
                    "MESH_ROUTER_URL": self.config.router_url,
                    "MESH_AUTH_TOKEN": self.config.auth_token or "",
                    "MESH_ROUTER_SESSION_ID": preallocated_session_id,
                    "MESH_TMUX_SESSION": tmux_session_name,
                },
            )
            time.sleep(max(0.0, float(self.config.startup_post_launch_settle_s)))

            attach_meta, upterm_proc = self._create_attach_handle(tmux_session_name)

            session_id = self._open_session(
                task,
                tmux_session_name,
                work_dir,
                target_account,
                attach_meta,
                session_id=preallocated_session_id,
            )
            self._send_session_message(
                session_id,
                direction="system",
                role="system",
                content=f"tmux session created: {tmux_session_name}",
                metadata={"tmux_session": tmux_session_name, "working_dir": work_dir},
            )
            if prompt:
                self._send_session_message(
                    session_id,
                    direction="system",
                    role="bootstrap",
                    content=prompt,
                    metadata={"source": "task.payload.prompt", "task_id": task_id},
                )
            if prompt and not bootstrap_prompt_via_stdin and self.config.cli_type == "codex":
                if not self._wait_for_cli_ready(tmux_session_name):
                    logger.warning(
                        "CLI prompt readiness timeout for session %s; sending prompt anyway",
                        tmux_session_name,
                    )
                if prompt:
                    pre_prompt_capture = self._tmux_capture_pane(tmux_session_name)
                    self._tmux_send_text(tmux_session_name, prompt)
                    self._ensure_prompt_submitted(tmux_session_name)
                    self._ensure_prompt_delivered(tmux_session_name, prompt, pre_prompt_capture)

            start = time.monotonic()
            after_seq = 0
            group_after_seq = 0
            last_capture = ""
            last_emitted_capture = ""
            last_role_state: RoleState | None = None
            relay_baseline_capture = ""
            turn_claimed = False
            auto_exit_sent = False
            auto_exit_baseline_capture = ""
            prompt_delivery_confirmed = not prompt_delivery_requires_composer
            prompt_delivery_attempts = 0
            if auto_exit_on_success and not success_markers and not success_file_path:
                logger.warning(
                    "Task %s requested auto_exit_on_success without success markers or success file; session will stay open",
                    task_id,
                )

            while self._running:
                # Safety cap for abandoned sessions
                if (time.monotonic() - start) > self.config.task_timeout:
                    self._emit_completion_summary(
                        session_id,
                        task,
                        status="failed",
                    )
                    self._send_session_message(
                        session_id,
                        direction="system",
                        role="system",
                        content=f"session timeout after {self.config.task_timeout}s",
                        metadata={"timeout_s": self.config.task_timeout},
                    )
                    self._report_failure(task_id, f"session timeout after {self.config.task_timeout}s")
                    self._close_session(session_id, state="errored")
                    if tmux_session_name and self._tmux_has_session(tmux_session_name):
                        self._tmux_kill_session(tmux_session_name)
                    return

                if not self._tmux_has_session(tmux_session_name):
                    break

                try:
                    new_after_seq = self._deliver_inbound_messages(
                        session_id,
                        tmux_session_name,
                        after_seq,
                        ui_role=ui_role,
                    )
                except SessionNotFoundError:
                    logger.info(
                        "Router no longer has session %s; stopping interactive loop for task %s",
                        session_id,
                        task_id,
                    )
                    break
                if new_after_seq > after_seq:
                    auto_exit_baseline_capture = ""
                after_seq = max(after_seq, new_after_seq)
                if ui_group_id and ui_role:
                    group_after_seq = max(
                        group_after_seq,
                        self._deliver_group_messages(
                            session_id=session_id,
                            tmux_session=tmux_session_name,
                            ui_group_id=ui_group_id,
                            after_seq=group_after_seq,
                            ui_role=ui_role,
                        ),
                    )
                captured = self._tmux_capture_pane(tmux_session_name)
                if captured:
                    prior_capture = last_capture
                    capture_emit = _compute_output_emit(prior_capture, captured)
                    delta_text = capture_emit[0] if capture_emit else ""
                    if prompt_delivery_requires_composer and not prompt_delivery_confirmed:
                        if captured.strip() and (
                            _capture_contains_prompt_text(captured, prompt)
                            or not _looks_like_start_screen(captured)
                            or _capture_shows_activity(captured)
                        ):
                            prompt_delivery_confirmed = True
                        elif prompt_delivery_attempts < self.config.prompt_submit_retry_count:
                            prompt_delivery_attempts += 1
                            logger.info(
                                "Prompt still stuck on start screen for %s; resending prompt attempt %d/%d",
                                tmux_session_name,
                                prompt_delivery_attempts,
                                self.config.prompt_submit_retry_count,
                            )
                            self._tmux_send_text(tmux_session_name, prompt)
                            self._ensure_prompt_submitted(tmux_session_name)
                            continue
                    last_capture = captured
                    last_emitted_capture = self._emit_cli_output_if_changed(
                        session_id, captured, last_emitted_capture
                    )
                    if ui_group_id and ui_role and not relay_uses_claude_hooks:
                        new_state = _detect_role_state(captured)
                        if new_state != last_role_state:
                            self._emit_state_change(
                                session_id=session_id,
                                ui_role=ui_role,
                                ui_group_id=ui_group_id,
                                state=new_state,
                            )
                            if new_state == RoleState.responding:
                                turn_claimed = self._claim_turn_via_bus(ui_group_id, ui_role)
                                if turn_claimed:
                                    relay_baseline_capture = prior_capture
                            elif (
                                new_state == RoleState.idle
                                and last_role_state == RoleState.responding
                            ):
                                target_role = str(relay.get("target_role") or "").strip()
                                if target_role and turn_claimed:
                                    self._emit_response_relay(
                                        session_id=session_id,
                                        ui_role=ui_role,
                                        ui_group_id=ui_group_id,
                                        target_role=target_role,
                                        baseline_capture=relay_baseline_capture,
                                        current_capture=captured,
                                    )
                                if turn_claimed:
                                    self._release_turn_via_bus(ui_group_id, ui_role)
                                turn_claimed = False
                                relay_baseline_capture = ""
                            elif (
                                new_state == RoleState.awaiting_input
                                and last_role_state == RoleState.responding
                            ):
                                if turn_claimed:
                                    self._release_turn_via_bus(ui_group_id, ui_role)
                                turn_claimed = False
                                relay_baseline_capture = ""
                            last_role_state = new_state
                    live_failure_kind = _detect_interactive_failure_screen(
                        self.config.cli_type, captured
                    )
                    if live_failure_kind:
                        self._emit_completion_summary(
                            session_id,
                            task,
                            status="failed",
                            final_snapshot=captured[-4000:],
                        )
                        self._send_session_message(
                            session_id,
                            direction="system",
                            role="system",
                            content=(
                                "detected terminal CLI blocker; closing session so router can retry "
                                "with another account"
                            ),
                            metadata={"error_kind": live_failure_kind},
                        )
                        self._report_failure(
                            task_id,
                            captured[-4000:],
                            error_kind=live_failure_kind,
                        )
                        self._close_session(session_id, state="errored")
                        if self._tmux_has_session(tmux_session_name):
                            self._tmux_kill_session(tmux_session_name)
                        return
                    if auto_exit_on_success and success_markers and not auto_exit_baseline_capture:
                        auto_exit_baseline_capture = captured
                    if (
                        auto_exit_on_success
                        and not auto_exit_sent
                        and auto_exit_baseline_capture
                        and _should_auto_exit_on_success(
                            captured,
                            success_markers,
                            baseline_capture=auto_exit_baseline_capture,
                            delta_text=delta_text,
                        )
                    ):
                        logger.info(
                            "Auto-exit on success triggered for task %s using markers %s",
                            task_id,
                            success_markers,
                        )
                        self._send_session_message(
                            session_id,
                            direction="system",
                            role="system",
                            content="auto_exit_on_success triggered; sending exit command",
                            metadata={
                                "exit_command": exit_command,
                                "success_markers": success_markers,
                            },
                        )
                        self._tmux_send_text(tmux_session_name, exit_command)
                        self._ensure_prompt_submitted(tmux_session_name)
                        auto_exit_sent = True
                    elif (
                        auto_exit_on_success
                        and not auto_exit_sent
                        and success_file_path
                        and _success_file_matches(
                            work_dir,
                            success_file_path,
                            success_file_contains=success_file_contains,
                            min_mtime_ns=success_file_min_mtime_ns,
                        )
                    ):
                        logger.info(
                            "Auto-exit on artifact success triggered for task %s using file %s",
                            task_id,
                            success_file_path,
                        )
                        self._send_session_message(
                            session_id,
                            direction="system",
                            role="system",
                            content="auto_exit_on_success triggered by success_file_path; sending exit command",
                            metadata={
                                "exit_command": exit_command,
                                "success_file_path": success_file_path,
                                "success_file_contains": success_file_contains,
                            },
                        )
                        self._tmux_send_text(tmux_session_name, exit_command)
                        self._ensure_prompt_submitted(tmux_session_name)
                        auto_exit_sent = True
                time.sleep(self.config.session_poll_interval_s)

            final_snapshot = last_capture
            completion_summary: dict[str, Any] | None = None
            if session_id:
                failure_kind = classify_cli_failure(self.config.cli_type, final_snapshot)
                completion_summary = self._emit_completion_summary(
                    session_id,
                    task,
                    status="failed" if failure_kind else "completed",
                    final_snapshot=final_snapshot[-4000:] if final_snapshot else "",
                )
                if final_snapshot and final_snapshot.strip() != (last_emitted_capture or "").strip():
                    self._send_session_message_nonfatal(
                        session_id,
                        direction="out",
                        role="cli",
                        content=final_snapshot[-self.config.output_emit_max_chars:],
                        metadata={"snapshot": True, "final": True},
                        context="final_snapshot",
                    )
                self._send_session_message_nonfatal(
                    session_id,
                    direction="system",
                    role="system",
                    content="tmux session exited",
                    metadata={"tmux_session": tmux_session_name},
                    context="session_exit",
                )
                self._close_session(
                    session_id,
                    state="errored" if failure_kind else "closed",
                )

            if failure_kind:
                self._report_failure(
                    task_id,
                    final_snapshot[-4000:] if final_snapshot else failure_kind,
                    error_kind=failure_kind,
                )
            elif self.config.auto_complete_on_exit:
                result = {
                    "interactive_session": True,
                    "session_id": session_id,
                    "tmux_session": tmux_session_name,
                    "final_snapshot": final_snapshot[-4000:] if final_snapshot else "",
                }
                if completion_summary:
                    result["completion_summary"] = completion_summary
                self._report_complete(task_id, result)

        except Exception as e:
            logger.exception("Interactive task %s failed", task_id)
            if session_id:
                try:
                    self._emit_completion_summary(
                        session_id,
                        task,
                        status="failed",
                        final_snapshot=str(e),
                    )
                    self._send_session_message(
                        session_id,
                        direction="system",
                        role="system",
                        content=f"session worker exception: {e}",
                        metadata={"exception": type(e).__name__},
                    )
                    self._close_session(session_id, state="errored")
                except Exception:  # pragma: no cover
                    pass
            self._report_failure(task_id, f"unexpected: {e}")
        finally:
            if upterm_proc is not None:
                log_path = self._upterm_log_path(tmux_session_name) if tmux_session_name else None
                self._stop_upterm(upterm_proc, log_path=log_path)

    def _prepare_cli_runtime(self, work_dir: str, target_account: str) -> None:
        """Preseed provider runtime metadata needed for unattended session startup."""
        if self.config.cli_type not in {"claude", "gemini", "codex"}:
            return
        self._preseed_claude_runtime(work_dir, target_account)

    def _preseed_claude_runtime(self, work_dir: str, target_account: str) -> None:
        """Mark onboarding/trust/MCP state as accepted for the current project.

        Claude persists most first-run state in ``.claude.json`` files, both
        globally and per-CCS instance. Preseeding these files avoids blocking
        tmux sessions on theme/onboarding/trust/MCP prompts.
        """
        home_dir = os.path.expanduser("~")
        enabled_servers = _discover_project_mcp_servers(work_dir)
        state_paths = [os.path.join(home_dir, ".claude.json")]
        if target_account:
            instance_dir = os.path.join(home_dir, ".ccs", "instances", target_account)
            # Create instance dir if missing to ensure we can write the state file
            os.makedirs(instance_dir, exist_ok=True)
            state_paths.append(os.path.join(instance_dir, ".claude.json"))
        for state_path in state_paths:
            self._preseed_claude_state_file(state_path, work_dir, enabled_servers)

    @staticmethod
    def _preseed_claude_state_file(
        state_path: str, work_dir: str, enabled_servers: list[str]
    ) -> None:
        data: dict = {}
        if os.path.exists(state_path):
            try:
                with open(state_path, encoding="utf-8") as fh:
                    raw = json.load(fh)
                if isinstance(raw, dict):
                    data = raw
            except (OSError, json.JSONDecodeError):
                logger.warning("Failed to read Claude state file %s; recreating", state_path)

        projects = data.get("projects")
        if not isinstance(projects, dict):
            projects = {}
            data["projects"] = projects

        project = projects.get(work_dir)
        if not isinstance(project, dict):
            project = {}
            projects[work_dir] = project

        data["hasCompletedOnboarding"] = True
        data["numStartups"] = max(int(data.get("numStartups", 0) or 0), 1)

        project["allowedTools"] = list(project.get("allowedTools") or [])
        project["mcpContextUris"] = list(project.get("mcpContextUris") or [])
        project["mcpServers"] = dict(project.get("mcpServers") or {})
        project["enabledMcpjsonServers"] = enabled_servers
        project["disabledMcpjsonServers"] = []
        project["hasTrustDialogAccepted"] = True
        project["projectOnboardingSeenCount"] = max(
            int(project.get("projectOnboardingSeenCount", 0) or 0), 1
        )
        project["hasClaudeMdExternalIncludesApproved"] = bool(
            project.get("hasClaudeMdExternalIncludesApproved", False)
        )
        project["hasClaudeMdExternalIncludesWarningShown"] = bool(
            project.get("hasClaudeMdExternalIncludesWarningShown", False)
        )

        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        tmp_path = f"{state_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path, state_path)

    def _tmux_session_name(self, task_id: str, target_account: str | None = None) -> str:
        account = (target_account or self.config.account_profile or "work").strip() or "work"
        task_fragment = re.sub(r"[^A-Za-z0-9]+", "", str(task_id))[:16] or "task"
        base = f"{self.config.tmux_session_prefix}-{self.config.cli_type}-{account}-{task_fragment}"
        return _sanitize_session_name(base)

    def _tmux_new_session(
        self,
        session_name: str,
        work_dir: str,
        cli_command: str,
        *,
        initial_stdin: str | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        # Launch command directly inside a non-interactive bash wrapper so tmux session ends when CLI exits.
        mesh_home = os.environ.get("MESH_HOME", "").strip() or _default_mesh_home()
        mesh_scripts = os.path.join(mesh_home, "scripts")
        exports = [
            f"export MESH_HOME={shlex.quote(mesh_home)};",
            f"export PATH={shlex.quote(mesh_scripts)}:$PATH;",
        ]
        for key, value in sorted((extra_env or {}).items()):
            if not key or value is None:
                continue
            exports.append(f"export {key}={shlex.quote(str(value))};")
        launch_command = " ".join([*exports, cli_command])
        if initial_stdin:
            launch_command = " ".join([
                shlex.quote(sys.executable),
                "-c",
                shlex.quote(
                    "import subprocess, sys; raise SystemExit(subprocess.run(sys.argv[1], input=sys.argv[2], text=True, shell=True).returncode)"
                ),
                shlex.quote(cli_command),
                shlex.quote(initial_stdin),
            ])
        subprocess.run(
            [self.config.tmux_bin, "new-session", "-d", "-s", session_name, "-c", work_dir, "bash", "-lc", launch_command],
            check=True,
            capture_output=True,
            text=True,
        )
        for key, value in sorted((extra_env or {}).items()):
            if not key or value is None:
                continue
            subprocess.run(
                [self.config.tmux_bin, "set-environment", "-t", session_name, key, str(value)],
                check=False,
                capture_output=True,
                text=True,
            )

    def _tmux_has_session(self, session_name: str) -> bool:
        proc = subprocess.run(
            [self.config.tmux_bin, "has-session", "-t", session_name],
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0

    def _tmux_kill_session(self, session_name: str) -> None:
        subprocess.run(
            [self.config.tmux_bin, "kill-session", "-t", session_name],
            capture_output=True,
            text=True,
        )

    def _tmux_send_text(self, session_name: str, text: str) -> None:
        target = f"{session_name}:0.0"
        lines = text.splitlines() or [text]
        for idx, line in enumerate(lines):
            if line:
                subprocess.run(
                    [self.config.tmux_bin, "send-keys", "-t", target, line],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                time.sleep(max(0.0, float(self.config.tmux_send_settle_s)))
            # Submit each line (interactive CLI prompt style).
            subprocess.run(
                [self.config.tmux_bin, "send-keys", "-t", target, "Enter"],
                check=True,
                capture_output=True,
                text=True,
            )

    def _tmux_send_key(self, session_name: str, key: str, repeat: int = 1) -> None:
        target = f"{session_name}:0.0"
        n = max(1, min(50, int(repeat)))
        subprocess.run(
            [self.config.tmux_bin, "send-keys", "-t", target, *([key] * n)],
            check=True,
            capture_output=True,
            text=True,
        )

    def _tmux_display_message(self, session_name: str, message: str, *, duration_ms: int = 12000) -> None:
        target = f"{session_name}:0.0"
        subprocess.run(
            [
                self.config.tmux_bin,
                "display-message",
                "-d",
                str(max(1000, int(duration_ms))),
                "-t",
                target,
                message,
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def _tmux_pane_tty(self, session_name: str) -> str:
        target = f"{session_name}:0.0"
        proc = subprocess.run(
            [
                self.config.tmux_bin,
                "display-message",
                "-p",
                "-t",
                target,
                "#{pane_tty}",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return str(proc.stdout or "").strip()

    def _tmux_render_notice(self, session_name: str, message: str) -> None:
        tty_path = self._tmux_pane_tty(session_name)
        if not tty_path:
            raise subprocess.SubprocessError(f"missing pane tty for {session_name}")
        rendered = str(message or "").strip()
        if not rendered:
            return
        with open(tty_path, "w", encoding="utf-8", errors="ignore") as fh:
            fh.write(f"\r\n{rendered}\r\n")

    def _tmux_resize(self, session_name: str, cols: int, rows: int) -> None:
        subprocess.run(
            [
                self.config.tmux_bin,
                "resize-window",
                "-t",
                session_name,
                "-x",
                str(int(cols)),
                "-y",
                str(int(rows)),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def _tmux_capture_pane(self, session_name: str) -> str:
        target = f"{session_name}:0.0"
        proc = subprocess.run(
            [
                self.config.tmux_bin,
                "capture-pane",
                "-p",
                "-t",
                target,
                "-S",
                f"-{self.config.tmux_capture_lines}",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            return ""
        return proc.stdout.strip()

    def _wait_for_cli_ready(self, session_name: str) -> bool:
        timeout_s = max(0.0, float(self.config.startup_ready_timeout_s))
        poll_s = max(0.05, float(self.config.startup_ready_poll_interval_s))
        attempts = max(1, int(timeout_s / poll_s)) if timeout_s > 0 else 1
        for _ in range(attempts):
            captured = self._tmux_capture_pane(session_name)
            if any(marker in captured for marker in _CLAUDE_CODE_READY_MARKERS):
                return True
            time.sleep(poll_s)
        return False

    def _ensure_prompt_submitted(self, session_name: str) -> None:
        retries = max(0, int(self.config.prompt_submit_retry_count))
        poll_s = max(0.05, float(self.config.prompt_submit_retry_poll_s))
        for attempt in range(retries):
            time.sleep(poll_s)
            if not _last_prompt_line_has_content(self._tmux_capture_pane(session_name)):
                return
            logger.info(
                "Composer still has pending text for %s; sending Enter retry %d/%d",
                session_name,
                attempt + 1,
                retries,
            )
            self._tmux_send_key(session_name, "Enter", repeat=1)

    def _ensure_prompt_delivered(self, session_name: str, prompt: str, baseline_capture: str) -> None:
        retries = max(0, int(self.config.prompt_submit_retry_count))
        poll_s = max(0.05, float(self.config.prompt_submit_retry_poll_s))
        baseline = str(baseline_capture or "").strip()
        for attempt in range(retries):
            time.sleep(poll_s)
            captured = self._tmux_capture_pane(session_name)
            if not captured.strip():
                continue
            if not _looks_like_start_screen(captured):
                return
            if _capture_contains_prompt_text(captured, prompt):
                return
            if captured.strip() == baseline:
                logger.info(
                    "Prompt not visible and pane unchanged for %s; resending prompt attempt %d/%d",
                    session_name,
                    attempt + 1,
                    retries,
                )
                self._tmux_send_text(session_name, prompt)
                self._ensure_prompt_submitted(session_name)

    def _emit_cli_output_if_changed(
        self,
        session_id: str,
        current_capture: str,
        previous_emitted_capture: str,
    ) -> str:
        payload = _compute_output_emit(
            previous_emitted_capture,
            current_capture,
            max_chars=self.config.output_emit_max_chars,
        )
        if payload is None:
            return previous_emitted_capture
        content, metadata = payload
        try:
            self._send_session_message(
                session_id,
                direction="out",
                role="cli",
                content=content,
                metadata=metadata,
            )
        except requests.RequestException as e:
            logger.warning("Failed to emit CLI output for session %s: %s", session_id, e)
            return previous_emitted_capture
        return current_capture

    # ------------------------------------------------------------------
    # Attach handle lifecycle
    # ------------------------------------------------------------------

    def _create_attach_handle(
        self, tmux_session: str
    ) -> tuple[dict | None, subprocess.Popen | None]:
        """Try to create an attach handle for *tmux_session*.

        Returns ``(metadata_dict, upterm_process)`` on success or
        ``(None, None)`` when no attach is available.
        """
        proc, target = self._start_upterm(tmux_session)
        if proc is not None and target is not None:
            return {"attach_kind": "upterm", "attach_target": target}, proc

        # Fallback: ssh_tmux (static pointer to the tmux session).
        if self.config.ssh_tmux_user and self.config.ssh_tmux_host:
            target = (
                f"ssh://{self.config.ssh_tmux_user}@{self.config.ssh_tmux_host}:22"
                f"?tmux_session={tmux_session}"
            )
            logger.info("Attach fallback ssh_tmux for %s", tmux_session)
            return {"attach_kind": "ssh_tmux", "attach_target": target}, None

        logger.info("No attach handle available for %s, continuing without", tmux_session)
        return None, None

    def _upterm_log_path(self, tmux_session: str) -> str:
        log_dir = os.path.join(self.config.runtime_state_dir, "upterm")
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, f"upterm-{tmux_session}.log")

    def _start_upterm(
        self, tmux_session: str
    ) -> tuple[subprocess.Popen | None, str | None]:
        """Launch ``upterm host`` for *tmux_session*.

        Returns ``(process, ssh_url)`` or ``(None, None)`` on failure.
        """
        log_path = self._upterm_log_path(tmux_session)
        if os.path.exists(log_path):
            try:
                os.remove(log_path)
                logger.info("Removed stale upterm log before start: %s", log_path)
            except OSError as e:
                logger.warning("Failed to remove stale upterm log %s: %s", log_path, e)
        cmd: list[str] = [
            self.config.upterm_bin,
            "host",
        ]
        if self.config.upterm_accept:
            cmd.append("--accept")
        if self.config.upterm_skip_host_key_check:
            cmd.append("--skip-host-key-check")
        cmd.extend([
            "--force-command",
            f"{self.config.tmux_bin} attach -t {tmux_session}",
        ])
        if self.config.upterm_server:
            cmd.extend(["--server", self.config.upterm_server])
        cmd.extend(["--", "bash"])

        log_handle = None
        try:
            log_handle = open(log_path, "w", encoding="utf-8")
            proc = subprocess.Popen(
                cmd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError:
            logger.warning("upterm binary not found at %s", self.config.upterm_bin)
            return None, None
        except OSError as e:
            logger.warning("upterm launch failed at %s: %s", self.config.upterm_bin, e)
            return None, None
        finally:
            if log_handle is not None:
                log_handle.close()

        target = self._poll_upterm_target(log_path, proc)
        if target:
            return proc, target

        logger.warning("upterm started but failed to provide session URL")
        self._stop_upterm(proc, log_path=log_path)
        return None, None

    def _poll_upterm_target(self, log_path: str, proc: subprocess.Popen | None = None) -> str | None:
        """Poll upterm host output until an SSH URL appears."""
        deadline = time.monotonic() + self.config.upterm_ready_timeout
        while time.monotonic() < deadline:
            try:
                if os.path.exists(log_path):
                    with open(log_path, encoding="utf-8") as fh:
                        target = _parse_upterm_ssh_url(fh.read())
                    if target:
                        return target
                if proc is not None and proc.poll() is not None:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        return None

    @staticmethod
    def _stop_upterm(proc: subprocess.Popen, log_path: str | None = None) -> None:
        """Terminate an upterm child process (SIGTERM then SIGKILL) and cleanup temp log."""
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

        if log_path and os.path.exists(log_path):
            try:
                os.remove(log_path)
                logger.info("Cleaned up upterm log: %s", log_path)
            except OSError as e:
                logger.warning("Failed to remove upterm log %s: %s", log_path, e)

    def _ack_task(self, task_id: str) -> bool:
        try:
            resp = self._http.post(
                f"{self.config.router_url}/tasks/ack",
                json={"task_id": task_id, "worker_id": self.config.worker_id},
                timeout=self.config.control_plane_timeout,
            )
            if resp.status_code != 200:
                logger.warning("Task %s ack failed (%d)", task_id, resp.status_code)
                return False
            return True
        except requests.RequestException as e:
            logger.warning("Task %s ack error: %s", task_id, e)
            return False

    def _report_complete(self, task_id: str, result: dict) -> None:
        try:
            self._http.post(
                f"{self.config.router_url}/tasks/complete",
                json={"task_id": task_id, "worker_id": self.config.worker_id, "result": result},
                timeout=self.config.control_plane_timeout,
            )
            logger.info("Task %s completed", task_id)
        except requests.RequestException as e:
            logger.error("Failed to report completion for task %s: %s", task_id, e)

    def _report_failure(self, task_id: str, error: str, *, error_kind: str = "") -> None:
        logger.error("Task %s failed: %s", task_id, error)
        try:
            body = {
                "task_id": task_id,
                "worker_id": self.config.worker_id,
                "error": error,
            }
            if error_kind:
                body["error_kind"] = error_kind
            self._http.post(
                f"{self.config.router_url}/tasks/fail",
                json=body,
                timeout=self.config.control_plane_timeout,
            )
        except requests.RequestException as e:
            logger.error("Failed to report failure for task %s: %s", task_id, e)

    def _build_completion_summary(
        self,
        task: dict,
        *,
        status: str,
        final_snapshot: str = "",
    ) -> dict[str, Any] | None:
        payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        if not _coerce_bool(payload.get("ui_role_session"), default=False):
            return None

        role = str(payload.get("ui_role") or task.get("role") or "").strip() or "role"
        target_roles = _coerce_string_list(payload.get("completion_summary_targets"))
        if not target_roles:
            target_roles = _default_completion_summary_targets(role)
        summary_text = str(payload.get("completion_summary_text") or "").strip()
        if not summary_text:
            summary_text = _default_completion_summary_text(role, status)
            if final_snapshot.strip():
                summary_text = f"{summary_text} Final snapshot captured."
        return {
            "type": "completion_summary",
            "role": role,
            "ui_group_id": str(payload.get("ui_group_id") or "").strip(),
            "status": status,
            "summary_text": summary_text,
            "artifacts": _coerce_string_list(
                payload.get("completion_summary_artifacts", payload.get("artifacts"))
            ),
            "target_roles": target_roles,
        }

    def _list_open_ui_group_sessions(self, ui_group_id: str) -> list[dict[str, Any]]:
        if not ui_group_id:
            return []
        try:
            resp = self._http.get(
                f"{self.config.router_url}/sessions",
                params={"state": "open", "limit": 200},
                timeout=self.config.control_plane_timeout,
            )
        except requests.RequestException as e:
            logger.warning("Failed to list sessions for completion summary routing: %s", e)
            return []
        if getattr(resp, "status_code", None) != 200:
            return []
        try:
            payload = resp.json()
        except ValueError:
            return []
        sessions = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(sessions, list):
            return []
        matched: list[dict[str, Any]] = []
        for session in sessions:
            if not isinstance(session, dict):
                continue
            metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
            if str(metadata.get("ui_group_id") or "").strip() != ui_group_id:
                continue
            matched.append(session)
        return matched

    def _route_completion_summary(
        self,
        source_session_id: str | None,
        summary: dict[str, Any],
    ) -> None:
        ui_group_id = str(summary.get("ui_group_id") or "").strip()
        target_roles = [role for role in _coerce_string_list(summary.get("target_roles")) if role]
        if not source_session_id or not ui_group_id or not target_roles:
            return

        sessions = self._list_open_ui_group_sessions(ui_group_id)
        source_role = str(summary.get("role") or "").strip()
        summary_text = str(summary.get("summary_text") or "")
        for target_role in target_roles:
            candidates = []
            for session in sessions:
                if str(session.get("session_id") or "").strip() == source_session_id:
                    continue
                metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
                peer_role = str(metadata.get("ui_role") or metadata.get("role") or "").strip()
                if peer_role == target_role:
                    candidates.append(session)
            if len(candidates) != 1:
                if len(candidates) > 1:
                    logger.warning(
                        "Skipping completion summary route for %s in ui_group %s; ambiguous peers=%d",
                        target_role,
                        ui_group_id,
                        len(candidates),
                    )
                continue
            peer_session_id = str(candidates[0].get("session_id") or "").strip()
            if not peer_session_id:
                continue
            routed_metadata = dict(summary)
            routed_metadata.update(
                {
                    "source_session_id": source_session_id,
                    "source_role": source_role,
                    "target_role": target_role,
                }
            )
            try:
                self._send_session_message(
                    peer_session_id,
                    direction="in",
                    role="summary",
                    content=summary_text,
                    metadata=routed_metadata,
                )
            except requests.RequestException as e:
                logger.warning(
                    "Skipping completion summary route to %s for ui_group %s: %s",
                    target_role,
                    ui_group_id,
                    e,
                )

    def _emit_completion_summary(
        self,
        session_id: str | None,
        task: dict,
        *,
        status: str,
        final_snapshot: str = "",
    ) -> dict[str, Any] | None:
        summary = self._build_completion_summary(task, status=status, final_snapshot=final_snapshot)
        if not summary:
            return None
        if session_id:
            self._send_session_message_nonfatal(
                session_id,
                direction="system",
                role="summary",
                content=str(summary.get("summary_text") or ""),
                metadata=summary,
                context="completion_summary",
            )
            self._route_completion_summary(session_id, summary)
        return summary

    def _open_session(
        self,
        task: dict,
        tmux_session: str,
        work_dir: str,
        target_account: str,
        attach_meta: dict | None = None,
        session_id: str | None = None,
    ) -> str:
        payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        metadata: dict = {
            "tmux_session": tmux_session,
            "working_dir": work_dir,
            "repo": str(task.get("repo") or work_dir).strip(),
            "role": str(payload.get("ui_role") or task.get("role") or "").strip(),
            "task_title": task.get("title", ""),
        }
        ui_group_id = str(payload.get("ui_group_id") or "").strip()
        if ui_group_id:
            metadata["ui_group_id"] = ui_group_id
        ui_role = str(payload.get("ui_role") or "").strip()
        if ui_role:
            metadata["ui_role"] = ui_role
        if attach_meta:
            metadata.update(attach_meta)
        body = {
            "worker_id": self.config.worker_id,
            "cli_type": self.config.cli_type,
            "account_profile": target_account,
            "task_id": task["task_id"],
            "metadata": metadata,
        }
        if session_id:
            body["session_id"] = session_id
        resp = self._http.post(
            f"{self.config.router_url}/sessions/open",
            json=body,
            timeout=self.config.control_plane_timeout,
        )
        resp.raise_for_status()
        return resp.json()["session"]["session_id"]

    def _send_session_message(
        self,
        session_id: str,
        *,
        direction: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        resp = self._http.post(
            f"{self.config.router_url}/sessions/send",
            json={
                "session_id": session_id,
                "direction": direction,
                "role": role,
                "content": content,
                "metadata": metadata or {},
            },
            timeout=self.config.control_plane_timeout,
        )
        resp.raise_for_status()

    def _send_session_message_nonfatal(
        self,
        session_id: str,
        *,
        direction: str,
        role: str,
        content: str,
        metadata: dict | None = None,
        context: str = "",
    ) -> bool:
        try:
            self._send_session_message(
                session_id,
                direction=direction,
                role=role,
                content=content,
                metadata=metadata,
            )
            return True
        except requests.HTTPError as e:
            response = getattr(e, "response", None)
            status_code = getattr(response, "status_code", None)
            error_code = ""
            if response is not None:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                if isinstance(payload, dict):
                    error_code = str(payload.get("error") or "").strip()
            if status_code in {404, 409} or error_code in {"session_not_found", "session_closed"}:
                logger.warning(
                    "Skipping non-fatal session send for %s (%s): status=%s error=%s",
                    session_id,
                    context or role or direction,
                    status_code,
                    error_code or type(e).__name__,
                )
                return False
            raise
        except requests.RequestException as e:
            logger.warning(
                "Skipping non-fatal session send for %s (%s): %s",
                session_id,
                context or role or direction,
                e,
            )
            return False

    def _close_session(self, session_id: str, *, state: str = "closed") -> None:
        resp = self._http.post(
            f"{self.config.router_url}/sessions/close",
            json={"session_id": session_id, "state": state},
            timeout=self.config.control_plane_timeout,
        )
        if resp.status_code == 404:
            logger.warning(
                "Session close returned 404 for session %s (state=%s); treating as already closed",
                session_id,
                state,
            )
            return
        resp.raise_for_status()

    def _list_session_messages(self, session_id: str, *, after_seq: int, limit: int = 200) -> list[dict]:
        resp = self._http.get(
            f"{self.config.router_url}/sessions/messages",
            params={"session_id": session_id, "after_seq": after_seq, "limit": limit},
            timeout=self.config.control_plane_timeout,
        )
        if resp.status_code == 404:
            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            if isinstance(payload, dict) and payload.get("error") == "session_not_found":
                raise SessionNotFoundError(session_id)
        resp.raise_for_status()
        return resp.json().get("messages", [])

    def _list_group_messages(
        self,
        ui_group_id: str,
        *,
        after_seq: int,
        target_role: str,
        limit: int = 200,
    ) -> list[dict]:
        resp = self._http.get(
            f"{self.config.router_url}/sessions/group-messages",
            params={
                "ui_group_id": ui_group_id,
                "after_seq": after_seq,
                "target_role": target_role,
                "limit": limit,
            },
            timeout=self.config.control_plane_timeout,
        )
        resp.raise_for_status()
        return resp.json().get("messages", [])

    def _claim_turn_via_bus(self, ui_group_id: str, role: str) -> bool:
        try:
            resp = self._http.post(
                f"{self.config.router_url}/sessions/turn/claim",
                json={"ui_group_id": ui_group_id, "role": role},
                timeout=self.config.control_plane_timeout,
            )
        except requests.RequestException as e:
            logger.warning("Failed to claim turn for %s/%s: %s", ui_group_id, role, e)
            return False
        return resp.status_code == 200

    def _release_turn_via_bus(self, ui_group_id: str, role: str) -> bool:
        try:
            resp = self._http.post(
                f"{self.config.router_url}/sessions/turn/release",
                json={"ui_group_id": ui_group_id, "role": role},
                timeout=self.config.control_plane_timeout,
            )
        except requests.RequestException as e:
            logger.warning("Failed to release turn for %s/%s: %s", ui_group_id, role, e)
            return False
        return resp.status_code == 200

    def _send_cross_role_message(
        self,
        *,
        session_id: str,
        sender_role: str,
        target_role: str,
        ui_group_id: str,
        msg_type: CrossRoleMessageType,
        content: str,
        turn_id: str | None = None,
        reply_to_msg_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not session_id or not sender_role or not target_role or not ui_group_id or not content.strip():
            return None
        envelope = MessageEnvelope(
            sender_role=sender_role,
            sender_session_id=session_id,
            target_role=target_role,
            msg_type=msg_type,
            turn_id=turn_id,
            reply_to_msg_id=reply_to_msg_id,
            ui_group_id=ui_group_id,
        )
        merged_metadata = dict(metadata or {})
        merged_metadata.setdefault("ui_group_id", ui_group_id)
        merged_metadata["envelope"] = envelope.model_dump(mode="json")
        self._send_session_message(
            session_id,
            direction="out",
            role=sender_role,
            content=content,
            metadata=merged_metadata,
        )
        return envelope.model_dump(mode="json")

    def _emit_state_change(
        self,
        *,
        session_id: str,
        ui_role: str,
        ui_group_id: str,
        state: RoleState,
    ) -> None:
        self._send_cross_role_message(
            session_id=session_id,
            sender_role=ui_role,
            target_role="*",
            ui_group_id=ui_group_id,
            msg_type=CrossRoleMessageType.state_change,
            content=state.value,
            metadata={"state": state.value},
        )

    def _emit_response_relay(
        self,
        *,
        session_id: str,
        ui_role: str,
        ui_group_id: str,
        target_role: str,
        baseline_capture: str,
        current_capture: str,
    ) -> None:
        emit = _compute_output_emit(baseline_capture, current_capture, max_chars=4000)
        if not emit:
            return
        clean = _extract_clean_response(emit[0])
        if not clean:
            return
        self._send_cross_role_message(
            session_id=session_id,
            sender_role=ui_role,
            target_role=target_role,
            ui_group_id=ui_group_id,
            msg_type=CrossRoleMessageType.relay,
            content=clean,
            metadata={"source_role": ui_role},
        )

    def _deliver_group_messages(
        self,
        *,
        session_id: str,
        tmux_session: str,
        ui_group_id: str,
        after_seq: int,
        ui_role: str,
    ) -> int:
        if not ui_group_id or not ui_role:
            return after_seq
        try:
            messages = self._list_group_messages(
                ui_group_id,
                after_seq=after_seq,
                target_role=ui_role,
                limit=200,
            )
        except requests.RequestException as e:
            logger.warning("Failed to fetch group messages for %s/%s: %s", ui_group_id, ui_role, e)
            return after_seq

        max_seq = after_seq
        for msg in messages:
            seq = int(msg.get("seq") or 0)
            max_seq = max(max_seq, seq)
            if str(msg.get("session_id") or "").strip() == session_id:
                continue
            metadata = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
            envelope = metadata.get("envelope") if isinstance(metadata.get("envelope"), dict) else {}
            msg_type = str(envelope.get("msg_type") or "")
            if msg_type != CrossRoleMessageType.relay.value:
                continue
            content = str(msg.get("content") or "").strip()
            if not content:
                continue
            source_role = str(envelope.get("sender_role") or metadata.get("source_role") or msg.get("role") or "").strip()
            try:
                if ui_role == "boss":
                    self._tmux_display_message(
                        tmux_session,
                        _format_inbound_notice(content, source_role=source_role),
                        duration_ms=30000,
                    )
                else:
                    self._tmux_send_text(
                        tmux_session,
                        content,
                    )
            except subprocess.SubprocessError as e:
                logger.warning("Failed to deliver group message seq=%s to %s: %s", seq, tmux_session, e)
        return max_seq

    def _deliver_inbound_messages(
        self,
        session_id: str,
        tmux_session: str,
        after_seq: int,
        *,
        ui_role: str = "",
    ) -> int:
        try:
            messages = self._list_session_messages(session_id, after_seq=after_seq, limit=200)
        except SessionNotFoundError:
            raise
        except requests.RequestException as e:
            logger.warning("Failed to fetch session messages for %s: %s", session_id, e)
            return after_seq

        max_seq = after_seq
        for msg in messages:
            seq = int(msg.get("seq") or 0)
            max_seq = max(max_seq, seq)
            if msg.get("direction") != "in":
                continue
            metadata = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
            envelope = metadata.get("envelope") if isinstance(metadata.get("envelope"), dict) else {}
            msg_type = str(envelope.get("msg_type") or "").strip()
            # Do not replay the initial prompt already sent during bootstrap
            if (metadata or {}).get("source") == "task.payload.prompt":
                continue
            # Cross-role relay envelopes are delivered via the ui_group polling path.
            if msg_type == CrossRoleMessageType.relay.value:
                continue
            content = str(msg.get("content", ""))
            control = str((metadata or {}).get("control", "")).strip().lower()
            # Skip empty inputs to avoid accidental extra Enter spam.
            try:
                if control == "send_key":
                    key = str((metadata or {}).get("key", "")).strip()
                    if key:
                        repeat = int((metadata or {}).get("repeat", 1))
                        self._tmux_send_key(tmux_session, key, repeat=repeat)
                    continue
                if control == "resize":
                    cols = int((metadata or {}).get("cols"))
                    rows = int((metadata or {}).get("rows"))
                    self._tmux_resize(tmux_session, cols=cols, rows=rows)
                    continue
                if control == "signal":
                    signal_name = str((metadata or {}).get("signal", "")).strip().lower()
                    if signal_name == "interrupt":
                        self._tmux_send_key(tmux_session, "C-c", repeat=1)
                    elif signal_name == "terminate":
                        self._tmux_kill_session(tmux_session)
                    continue
                if not content:
                    continue
                current_role = str(ui_role or "").strip()
                source_role = str((metadata or {}).get("source_role") or msg.get("role") or "").strip()
                if current_role == "boss" and source_role and source_role != "boss":
                    self._tmux_display_message(
                        tmux_session,
                        _format_inbound_notice(content, source_role=source_role),
                        duration_ms=30000,
                    )
                else:
                    self._tmux_send_text(tmux_session, content)
            except subprocess.SubprocessError as e:
                logger.warning("Failed to deliver message seq=%s to tmux session %s: %s", seq, tmux_session, e)
            except (TypeError, ValueError) as e:
                logger.warning("Invalid control payload seq=%s for tmux session %s: %s", seq, tmux_session, e)
        return max_seq


def run_session_worker() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config = SessionWorkerConfig.from_env()
    worker = MeshSessionWorker(config)

    def handle_shutdown(signum: int, frame: object) -> None:
        logger.info("Shutting down session worker %s (signal %d)...", config.worker_id, signum)
        worker.stop()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    worker.start()


if __name__ == "__main__":
    run_session_worker()
