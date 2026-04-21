#!/usr/bin/env python3
"""Minimal iTerm2 control helper for mesh-marked panes.

This script is intentionally mechanical. It does not infer workflow or routing;
it only finds panes marked by ``mesh_iterm_ui.py`` and performs direct actions:

- list mesh panes
- focus a pane
- send text
- send a key escape
- dump recent screen contents
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


@dataclass
class MeshPane:
    window_index: int
    tab_index: int
    session_index: int
    repo: str
    role: str
    ui_group_id: str
    tab: Any
    session: Any


NO_AUTO_EDIT_PATHS = ("__mesh_no_auto_edit_prompts__",)
SEND_TEXT_CHUNK_CHARS = 200
CLAUDE_CONFIG_MARKERS = ("commands", "scripts")
DEFAULT_CLAUDE_CONFIG_CANDIDATES = (
    "/Users/sam/claude-config",
    "/media/sam/1TB/claude-config",
)
CLAUDE_CONFIG_CONTRACTS = {
    "pipeline.speckit": ("commands/pipeline.speckit.md", "command"),
    "speckit.analyze": ("commands/speckit.analyze.md", "command"),
    "speckit.implement": ("commands/speckit.implement.md", "command"),
    "verify.quick": ("commands/verify/quick.md", "command"),
    "validate": ("skills/validate/SKILL.md", "skill"),
    "confidence-gate": ("scripts/confidence_gate.py", "script"),
}
DEFAULT_ROLE_CONTRACTS = {
    "boss": "pipeline.speckit",
    "president": "speckit.analyze",
    "worker": "speckit.implement",
    "reviewer": "verify.quick",
}
DEFAULT_CONTRACT_EXCERPT_MAX_CHARS = 240
CONTRACT_FRONTMATTER_KEYS = ("name", "description", "argument-hint")
CONTRACT_TAG_SECTIONS = ("objective", "purpose", "role", "process")
NON_WORKER_OUTPUT_POLICY_RULES = (
    ("mesh-speckit-run", r"(?:(?:^|[`$;&|])\s*|\b(?:run|launch|execute|start|call|invoke|spawn|use)\s+)(?:\./)?(?:scripts/mesh|mesh)\s+speckit\s+run\b", "nested Speckit run"),
    ("scripts-mesh", r"(?:(?:^|[`$;&|])\s*|\b(?:run|launch|execute|start|call|invoke|spawn|use)\s+)(?:\./)?scripts/mesh\b", "direct mesh script execution"),
    ("gemini-cli", r"(?:(?:^|[`$;&|])\s*|\b(?:run|launch|execute|start|call|invoke|spawn|use)\s+)gemini\s+", "hidden Gemini CLI launch"),
    ("codex-cli", r"(?:(?:^|[`$;&|])\s*|\b(?:run|launch|execute|start|call|invoke|spawn|use)\s+)codex\s+", "hidden Codex CLI launch"),
    ("claude-cli", r"(?:(?:^|[`$;&|])\s*|\b(?:run|launch|execute|start|call|invoke|spawn|use)\s+)claude\s+", "hidden Claude CLI launch"),
    ("confidence-gate", r"\bconfidence_gate\.py\b", "headless confidence gate execution"),
    ("validation-orchestrator", r"\bvalidation/orchestrator\.py\b", "headless validation orchestrator execution"),
    ("task-tool", r"Task\(\{", "nested Task tool invocation"),
)
WORKER_OUTPUT_POLICY_RULES = (
    ("mesh-speckit-run", r"(?:(?:^|[`$;&|])\s*|\b(?:run|launch|execute|start|call|invoke|spawn|use)\s+)(?:\./)?(?:scripts/mesh|mesh)\s+speckit\s+run\b", "recursive Speckit run"),
    ("gemini-cli", r"(?:(?:^|[`$;&|])\s*|\b(?:run|launch|execute|start|call|invoke|spawn|use)\s+)gemini\s+", "nested Gemini CLI launch"),
    ("codex-cli", r"(?:(?:^|[`$;&|])\s*|\b(?:run|launch|execute|start|call|invoke|spawn|use)\s+)codex\s+", "nested Codex CLI launch"),
    ("claude-cli", r"(?:(?:^|[`$;&|])\s*|\b(?:run|launch|execute|start|call|invoke|spawn|use)\s+)claude\s+", "nested Claude CLI launch"),
    ("git-commit", r"\bgit\s+commit\b", "git commit attempt"),
    ("git-push", r"\bgit\s+push\b", "git push attempt"),
)


@dataclass(frozen=True)
class ClaudeConfigResolution:
    root: str
    source: str
    available: bool
    reason: str = ""
    markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProductReview:
    parsed: bool
    status: str = "missing"
    score: int | None = None
    visual: int | None = None
    interaction: int | None = None
    clarity: int | None = None
    technical: int | None = None
    feedback: str = ""
    raw_line: str = ""


@dataclass(frozen=True)
class SupervisorAssessment:
    failure_class: str
    remediation: str


@dataclass(frozen=True)
class SupervisorRemediation:
    action: str
    retryable: bool
    max_attempts: int


@dataclass(frozen=True)
class TimeoutTelemetry:
    timeout_s: float
    elapsed_s: float
    poll_interval_s: float
    poll_count: int
    last_progress_s_ago: float
    screen_changed_recently: bool
    marker_seen_without_ack: bool = False


SUPERVISOR_REMEDIATION_REGISTRY = {
    "marker_format_issue": SupervisorRemediation(
        action="normalize_marker_variant",
        retryable=True,
        max_attempts=1,
    ),
    "delivery_ack_issue": SupervisorRemediation(
        action="normalize_delivery_ack_variant",
        retryable=True,
        max_attempts=1,
    ),
    "approval_pattern_missing": SupervisorRemediation(
        action="extend_approval_profile",
        retryable=True,
        max_attempts=1,
    ),
    "model_fallback_needed": SupervisorRemediation(
        action="switch_fallback_model",
        retryable=True,
        max_attempts=1,
    ),
    "review_context_missing": SupervisorRemediation(
        action="re_prompt_reviewer_with_artifact_context",
        retryable=True,
        max_attempts=1,
    ),
    "queued_prompt_issue": SupervisorRemediation(
        action="resume_queued_prompt",
        retryable=True,
        max_attempts=2,
    ),
    "provider_not_ready": SupervisorRemediation(
        action="stop_run",
        retryable=False,
        max_attempts=0,
    ),
    "stalled_run": SupervisorRemediation(
        action="stop_run",
        retryable=False,
        max_attempts=0,
    ),
    "unknown_controller_failure": SupervisorRemediation(
        action="stop_run",
        retryable=False,
        max_attempts=0,
    ),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Control mesh-marked iTerm2 panes.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_parser = sub.add_parser("list", help="List mesh-marked panes.")
    list_parser.add_argument("--repo", default="", help="Filter by repo path.")
    list_parser.add_argument("--ui-group-id", default="", help="Filter by mesh UI group id.")
    list_parser.add_argument("--output", default="", help="Write output to this file instead of stdout.")

    close_parser = sub.add_parser("close", help="Close mesh-marked tabs for a repo.")
    close_parser.add_argument("--repo", required=True, help="Exact mesh repo path.")
    close_parser.add_argument("--ui-group-id", default="", help="Close only tabs in this mesh UI group id.")

    smoke_parser = sub.add_parser("two-cli-smoke", help="Run a bidirectional live smoke between boss and president panes.")
    smoke_parser.add_argument("--repo", required=True, help="Exact mesh repo path.")
    smoke_parser.add_argument("--ui-group-id", default="", help="Target a specific mesh UI group id.")
    smoke_parser.add_argument("--boss-role", default="boss", help="Source Gemini role.")
    smoke_parser.add_argument("--president-role", default="president", help="Source Codex role.")
    smoke_parser.add_argument("--gemini-model", default="", help="Optional model command sent to the boss pane before testing.")
    smoke_parser.add_argument("--run-id", default="", help="Optional marker suffix.")
    smoke_parser.add_argument("--response-timeout", type=float, default=120.0, help="Seconds to wait for each marker.")
    smoke_parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between screen polls.")

    e2e_parser = sub.add_parser("two-cli-e2e", help="Open, verify, and optionally close a local Gemini/Codex layout.")
    e2e_parser.add_argument("--repo", required=True, help="Exact repo path.")
    e2e_parser.add_argument("--boss-cmd", default=os.environ.get("MESH_TWO_CLI_BOSS_CMD", "gemini"))
    e2e_parser.add_argument("--president-cmd", default=os.environ.get("MESH_TWO_CLI_PRESIDENT_CMD", "codex"))
    e2e_parser.add_argument("--boss-role", default="boss")
    e2e_parser.add_argument("--president-role", default="president")
    e2e_parser.add_argument("--gemini-model", default="", help="Optional model command sent to the boss pane before testing.")
    e2e_parser.add_argument("--ui-group-id", default="", help="Optional mesh UI group id.")
    e2e_parser.add_argument("--startup-wait", type=float, default=12.0, help="Seconds to wait for CLIs after launch.")
    e2e_parser.add_argument("--startup-timeout", type=float, default=90.0, help="Seconds to wait for CLI prompts.")
    e2e_parser.add_argument("--response-timeout", type=float, default=120.0, help="Seconds to wait for each marker.")
    e2e_parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between screen polls.")
    e2e_parser.add_argument("--keep-open", action="store_true", help="Leave the test layout open after completion.")

    team_parser = sub.add_parser("team-e2e", help="Open and verify a local boss/president/worker CLI chain.")
    team_parser.add_argument("--repo", required=True, help="Exact repo path.")
    team_parser.add_argument("--boss-cmd", default=os.environ.get("MESH_TEAM_BOSS_CMD", "claude"))
    team_parser.add_argument("--president-cmd", default=os.environ.get("MESH_TEAM_PRESIDENT_CMD", "codex"))
    team_parser.add_argument("--worker-cmd", default=os.environ.get("MESH_TEAM_WORKER_CMD", "gemini"))
    team_parser.add_argument("--boss-role", default="boss")
    team_parser.add_argument("--president-role", default="president")
    team_parser.add_argument("--worker-role", default="worker-gemini")
    team_parser.add_argument("--ui-group-id", default="", help="Optional mesh UI group id.")
    team_parser.add_argument("--startup-wait", type=float, default=12.0, help="Seconds to wait after launching panes.")
    team_parser.add_argument("--startup-timeout", type=float, default=120.0, help="Seconds to wait for CLI prompts.")
    team_parser.add_argument("--response-timeout", type=float, default=120.0, help="Seconds to wait for each marker.")
    team_parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between screen polls.")
    team_parser.add_argument("--keep-open", action="store_true", help="Leave the test layout open after completion.")

    speckit_parser = sub.add_parser("speckit-team-e2e", help="Open and verify a dry-run Speckit role-routing chain.")
    speckit_parser.add_argument("--repo", required=True, help="Exact repo path.")
    speckit_parser.add_argument("--feature", default=os.environ.get("MESH_SPECKIT_FEATURE", "snake-game-demo"))
    speckit_parser.add_argument("--boss-cmd", default=os.environ.get("MESH_TEAM_BOSS_CMD", "claude"))
    speckit_parser.add_argument("--president-cmd", default=os.environ.get("MESH_TEAM_PRESIDENT_CMD", "codex"))
    speckit_parser.add_argument("--worker-cmd", default=os.environ.get("MESH_TEAM_WORKER_CMD", "gemini"))
    speckit_parser.add_argument("--boss-role", default="boss")
    speckit_parser.add_argument("--president-role", default="president")
    speckit_parser.add_argument("--worker-role", default="worker-gemini")
    speckit_parser.add_argument("--ui-group-id", default="", help="Optional mesh UI group id.")
    speckit_parser.add_argument("--startup-wait", type=float, default=12.0, help="Seconds to wait after launching panes.")
    speckit_parser.add_argument("--startup-timeout", type=float, default=120.0, help="Seconds to wait for CLI prompts.")
    speckit_parser.add_argument("--response-timeout", type=float, default=120.0, help="Seconds to wait for each marker.")
    speckit_parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between screen polls.")
    speckit_parser.add_argument("--keep-open", action="store_true", help="Leave the test layout open after completion.")

    speckit_run_parser = sub.add_parser("speckit-team-run", help="Run one controlled Speckit team cycle.")
    speckit_run_parser.add_argument("--repo", required=True, help="Exact repo path.")
    speckit_run_parser.add_argument("--feature", required=True, help="Feature or change request.")
    speckit_run_parser.add_argument("--task", default="", help="Optional narrower implementation task.")
    speckit_run_parser.add_argument("--boss-cmd", default=os.environ.get("MESH_TEAM_BOSS_CMD", "claude"))
    speckit_run_parser.add_argument("--president-cmd", default=os.environ.get("MESH_TEAM_PRESIDENT_CMD", "codex"))
    speckit_run_parser.add_argument("--worker-cmd", default=os.environ.get("MESH_TEAM_WORKER_CMD", "gemini"))
    speckit_run_parser.add_argument("--reviewer-cmd", default=os.environ.get("MESH_TEAM_REVIEWER_CMD", "gemini"))
    speckit_run_parser.add_argument("--boss-role", default="boss")
    speckit_run_parser.add_argument("--president-role", default="president")
    speckit_run_parser.add_argument("--worker-role", default="worker-gemini")
    speckit_run_parser.add_argument("--reviewer-role", default="reviewer")
    speckit_run_parser.add_argument("--with-reviewer", action="store_true", help="Add a reviewer role before the final boss report.")
    speckit_run_parser.add_argument("--ui-group-id", default="", help="Optional mesh UI group id.")
    speckit_run_parser.add_argument(
        "--claude-config",
        default="",
        help="Optional claude-config root used as the command/gate policy source.",
    )
    speckit_run_parser.add_argument("--boss-contract", default="", help="Optional claude-config contract name for boss prompts.")
    speckit_run_parser.add_argument("--president-contract", default="", help="Optional claude-config contract name for president prompts.")
    speckit_run_parser.add_argument("--worker-contract", default="", help="Optional claude-config contract name for worker prompts.")
    speckit_run_parser.add_argument("--reviewer-contract", default="", help="Optional claude-config contract name for reviewer prompts.")
    speckit_run_parser.add_argument("--allow-write", action="store_true", help="Allow the worker to edit files.")
    speckit_run_parser.add_argument("--allow-dirty", action="store_true", help="Run even if the repo is already dirty.")
    speckit_run_parser.add_argument(
        "--handoff-dir",
        default=".mesh/runs",
        help="Repo-relative directory for persistent Speckit handoff JSON files.",
    )
    speckit_run_parser.add_argument("--no-handoff", action="store_true", help="Do not write persistent handoff JSON files.")
    speckit_run_parser.add_argument(
        "--auto-approve-prompts",
        action="store_true",
        help="Auto-answer known CLI trust/write prompts during this run; requires --allow-write.",
    )
    speckit_run_parser.add_argument(
        "--auto-approve-edit-path",
        action="append",
        default=[],
        help="When auto-approving edit prompts, allow only this repo-relative path. Repeatable.",
    )
    speckit_run_parser.add_argument("--test-command", default="", help="Optional local test command to run after worker returns.")
    speckit_run_parser.add_argument("--test-timeout", type=float, default=180.0, help="Seconds for the optional test command.")
    speckit_run_parser.add_argument("--allow-test-failure", action="store_true", help="Return success even if --test-command fails.")
    speckit_run_parser.add_argument("--quality", choices=("off", "quick"), default="off", help="Optional deterministic quality evidence mode.")
    speckit_run_parser.add_argument("--product-quality", action="store_true", help="Require a machine-readable product review score.")
    speckit_run_parser.add_argument("--min-product-score", type=int, default=7, help="Minimum PRODUCT_REVIEW score for pass.")
    speckit_run_parser.add_argument("--max-quality-retries", type=int, default=0, help="Maximum worker retries after product review failure.")
    speckit_run_parser.add_argument("--max-turns", type=int, default=1, help="Maximum response turns per role in this controlled cycle.")
    speckit_run_parser.add_argument("--startup-wait", type=float, default=12.0, help="Seconds to wait after launching panes.")
    speckit_run_parser.add_argument("--startup-timeout", type=float, default=120.0, help="Seconds to wait for CLI prompts.")
    speckit_run_parser.add_argument("--response-timeout", type=float, default=300.0, help="Seconds to wait for each role response.")
    speckit_run_parser.add_argument("--poll-interval", type=float, default=3.0, help="Seconds between screen polls.")
    speckit_run_parser.add_argument("--keep-open", action="store_true", help="Leave the run layout open after completion.")

    for name in ("focus", "dump", "send-text", "send-line", "send-key"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--repo", required=True, help="Exact mesh repo path.")
        cmd.add_argument("--role", required=True, help="Exact mesh role.")
        cmd.add_argument("--ui-group-id", default="", help="Target a specific mesh UI group id.")
        if name == "dump":
            cmd.add_argument("--lines", type=int, default=20, help="Trailing non-empty lines to print.")
            cmd.add_argument("--output", default="", help="Write output to this file instead of stdout.")
        elif name in {"send-text", "send-line"}:
            cmd.add_argument("text", help="Text to send verbatim.")
        elif name == "send-key":
            cmd.add_argument("key", help="Logical key: enter/up/down/left/right/esc/tab/backspace/ctrl-c.")

    return parser.parse_args()


async def _mesh_sessions(app, repo_filter: str = "", ui_group_filter: str = "") -> list[MeshPane]:
    panes: list[MeshPane] = []
    repo_filter = str(repo_filter or "").strip()
    ui_group_filter = str(ui_group_filter or "").strip()
    for wi, window in enumerate(getattr(app, "windows", []), 1):
        for ti, tab in enumerate(getattr(window, "tabs", []), 1):
            for si, session in enumerate(_tab_sessions(tab), 1):
                try:
                    marker = await session.async_get_variable("user.mesh_ui_tab")
                    repo = str(await session.async_get_variable("user.mesh_repo") or "").strip()
                    role = str(await session.async_get_variable("user.mesh_role") or "").strip()
                    ui_group_id = str(await session.async_get_variable("user.mesh_ui_group_id") or "").strip()
                except Exception:
                    continue
                if str(marker) != "1" or not repo or not role:
                    continue
                if repo_filter and repo != repo_filter:
                    continue
                if ui_group_filter and ui_group_id != ui_group_filter:
                    continue
                panes.append(
                    MeshPane(
                        window_index=wi,
                        tab_index=ti,
                        session_index=si,
                        repo=repo,
                        role=role,
                        ui_group_id=ui_group_id,
                        tab=tab,
                        session=session,
                    )
                )
    return panes


async def _find_mesh_pane(app, repo: str, role: str, ui_group_id: str = "") -> MeshPane:
    repo = str(repo or "").strip()
    role = str(role or "").strip()
    ui_group_id = str(ui_group_id or "").strip()
    matches = [pane for pane in await _mesh_sessions(app, repo, ui_group_id) if pane.role == role]
    if not matches:
        raise RuntimeError(f"no pane matched repo={repo!r} role={role!r} ui_group_id={ui_group_id!r}")
    if len(matches) > 1:
        raise RuntimeError(f"multiple panes matched repo={repo!r} role={role!r} ui_group_id={ui_group_id!r}")
    return matches[0]


async def _find_mesh_panes_ready(
    app: Any,
    repo: str,
    roles: Sequence[str],
    ui_group_id: str = "",
    *,
    timeout: float = 12.0,
    poll_interval: float = 1.0,
) -> dict[str, MeshPane]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, float(timeout))
    wait_s = max(0.2, float(poll_interval))
    last_error = ""
    while True:
        panes: dict[str, MeshPane] = {}
        try:
            for role in roles:
                panes[str(role)] = await _find_mesh_pane(app, repo, str(role), ui_group_id)
            return panes
        except RuntimeError as exc:
            last_error = str(exc)
            if loop.time() >= deadline:
                raise RuntimeError(last_error) from exc
            await asyncio.sleep(wait_s)


async def _close_or_retire_mesh_role_panes(
    app: Any,
    repo: str,
    role: str,
    ui_group_id: str = "",
) -> int:
    retired = 0
    for pane in [item for item in await _mesh_sessions(app, repo, ui_group_id) if item.role == str(role or "").strip()]:
        tab_sessions = getattr(pane.tab, "sessions", None)
        if isinstance(tab_sessions, list) and len(tab_sessions) == 1:
            close_fn = getattr(pane.tab, "async_close", None)
            if close_fn is not None:
                try:
                    await close_fn(force=True)
                except TypeError:
                    await close_fn()
                retired += 1
                continue
        try:
            await pane.session.async_set_variable("user.mesh_ui_tab", "")
            await pane.session.async_set_variable("user.mesh_role", f"{pane.role}-retired")
        except Exception:
            pass
        retired += 1
    return retired


def _tab_sessions(tab: Any) -> list[Any]:
    sessions = getattr(tab, "sessions", None)
    if isinstance(sessions, list) and sessions:
        return sessions
    current = getattr(tab, "current_session", None)
    return [current] if current is not None else []


async def _mark_mesh_session(session: Any, *, repo: str, role: str, ui_group_id: str) -> None:
    await session.async_set_variable("user.mesh_ui_tab", "1")
    await session.async_set_variable("user.mesh_repo", repo)
    await session.async_set_variable("user.mesh_role", role)
    await session.async_set_variable("user.mesh_ui_group_id", ui_group_id)


async def _launch_single_role_tab_in_group(
    app: Any,
    *,
    repo: str,
    role: str,
    command_text: str,
    ui_group_id: str,
) -> bool:
    panes = await _mesh_sessions(app, repo, ui_group_id)
    if not panes:
        return False
    windows = list(getattr(app, "windows", []) or [])
    anchor_index = max(0, int(panes[0].window_index) - 1)
    if anchor_index >= len(windows):
        return False
    window = windows[anchor_index]
    create_tab = getattr(window, "async_create_tab", None)
    if create_tab is None:
        return False
    tab = await create_tab()
    sessions = _tab_sessions(tab)
    if not sessions:
        return False
    session = sessions[0]
    await _mark_mesh_session(session, repo=repo, role=role, ui_group_id=ui_group_id)
    banner = f"printf \"\\033[3J\\033[H\\033[2J\"; clear; echo '[mesh:{role}] repo={_repo_name(repo)}'; "
    try:
        await session.async_activate()
        await asyncio.sleep(0.25)
        await session.async_send_text("\x03")
        await asyncio.sleep(0.05)
    except Exception:
        pass
    await session.async_send_text(f"{banner}{_role_launch_command(repo, command_text)}\r")
    return True


def _key_text(key: str) -> str:
    normalized = str(key or "").strip().lower()
    mapping = {
        "enter": "\r",
        "return": "\r",
        "up": "\x1b[A",
        "down": "\x1b[B",
        "right": "\x1b[C",
        "left": "\x1b[D",
        "esc": "\x1b",
        "escape": "\x1b",
        "tab": "\t",
        "backspace": "\x7f",
        "ctrl-c": "\x03",
        "interrupt": "\x03",
    }
    text = mapping.get(normalized)
    if text is None:
        raise ValueError(f"unsupported key: {key}")
    return text


def _iterm_retry_enabled() -> bool:
    return str(os.environ.get("MESH_ITERM_RETRY", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _emit(text: str, output_path: str = "") -> None:
    if output_path:
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
        return
    print(text)


def _repo_name(repo: str) -> str:
    return os.path.basename(str(repo or "").rstrip("/")) or "repo"


def _ui_command_env_key(role: str) -> str:
    return "MESH_UI_CMD_" + str(role or "").upper().replace("-", "_")


def _command_name(command_text: str) -> str:
    return str(command_text or "").strip().split(" ", 1)[0]


def _role_launch_command(repo: str, command_text: str) -> str:
    zsh = shutil.which("zsh") or "/bin/zsh"
    inner = (
        f"source ~/.zshrc >/dev/null 2>&1; cd {shlex.quote(str(repo or ''))} || exit $?; "
        f"{command_text}; status=$?; "
        "printf '\\n[mesh] role command exited with status %s; leaving shell open for diagnostics.\\n' \"$status\"; "
        "exec zsh -l"
    )
    return f"exec {shlex.quote(zsh)} -lc {shlex.quote(inner)}"


def _role_restart_command(repo: str, command_text: str) -> str:
    return (
        f"source ~/.zshrc >/dev/null 2>&1; "
        f"cd {shlex.quote(str(repo or ''))} || return $?; "
        f"{command_text}"
    )


def _clean_one_line(value: object) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())


def _format_mesh_msg(**fields: object) -> str:
    parts = ["MESH_MSG"]
    for key, value in fields.items():
        parts.append(f"{key}={shlex.quote(_clean_one_line(value))}")
    parts.append("END_MESH_MSG")
    return " ".join(parts)


def _worker_prompt_text(*, task: str, allow_write: bool, allowed_edit_paths: Sequence[str]) -> str:
    clean_task = _clean_one_line(task)
    if allow_write:
        if allowed_edit_paths:
            edit_scope = "Edit only these path(s): " + ", ".join(allowed_edit_paths) + "."
        else:
            edit_scope = "Edits are allowed by the operator with no controller path allowlist."
        return (
            f"Role worker. {edit_scope} Implement: {clean_task}. Save changes. "
            "Summarize files/tests/risks. No nested CLI, commit, push, or questions."
        )
    return (
        f"Role worker. No edits. Draft implementation plan for: {clean_task}. "
        "Summarize plan/tests/risks. No nested CLI, commit, push, or questions."
    )


def _product_retry_worker_prompt_text(
    *,
    task: str,
    feedback: str,
    allowed_edit_paths: Sequence[str],
) -> str:
    clean_feedback = _clean_one_line(feedback) or "Improve product quality based on the reviewer score."
    clean_task = _clean_one_line(task)
    if allowed_edit_paths:
        edit_scope = "Edit only these path(s): " + ", ".join(allowed_edit_paths) + "."
    else:
        edit_scope = "Edits are allowed by the operator with no controller path allowlist."
    return (
        f"Role worker-quality-retry. {edit_scope} Improve: {clean_task}. "
        f"Reviewer feedback: {clean_feedback}. Save changes. "
        "Summarize files/tests/risks. No nested CLI, commit, push, or questions."
    )


def _reviewer_product_prompt_text(
    *,
    test_status: str,
    quality_status: str,
    min_score: int,
    artifact_paths: Sequence[str],
) -> str:
    artifact_text = ", ".join(artifact_paths) if artifact_paths else "the changed artifact path from git status"
    return (
        f"Role reviewer. Tests {test_status}; quality {quality_status}. "
        f"Artifact path(s): {artifact_text}. Read-only inspection is allowed; no edits/questions. "
        "Playtest the artifact as a product. "
        "If you cannot render it visually, inspect the source and infer the likely product quality; do not score 0 solely for lack of browser access. "
        f"Pass only if score >= {max(0, min(10, int(min_score)))}. "
        "Score visual, interaction, clarity, and technical from 0..10."
    )


def _parse_product_review(text: str) -> ProductReview:
    review_line = ""
    feedback = ""
    lines = str(text or "").splitlines()
    for index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if line.startswith("✦"):
            line = line[1:].strip()
        if line.upper().startswith("PRODUCT_REVIEW "):
            review_line = line
            for follow in lines[index + 1 : index + 4]:
                follow_line = _screen_protocol_line(follow)
                upper_follow = follow_line.upper()
                if (
                    upper_follow.startswith("FEEDBACK:")
                    or upper_follow.startswith("SPECKIT")
                    or upper_follow.startswith("GBG_")
                    or upper_follow.startswith("DELIVERY_ACK")
                    or upper_follow.startswith("PRODUCT_REVIEW ")
                ):
                    break
                if re.search(r"\b(?:status|score|visual|interaction|clarity|technical)=", follow_line):
                    review_line += " " + follow_line
        elif line.upper().startswith("FEEDBACK:"):
            feedback = line.split(":", 1)[1].strip()
    if not review_line:
        return ProductReview(parsed=False, feedback=feedback)

    values = {
        key.lower(): value
        for key, value in re.findall(r"([A-Za-z_]+)=([^\s]+)", review_line)
    }

    def _score(name: str) -> int | None:
        raw = values.get(name)
        if raw is None:
            return None
        try:
            return max(0, min(10, int(raw)))
        except ValueError:
            return None

    return ProductReview(
        parsed=True,
        status=str(values.get("status") or "missing").strip().lower(),
        score=_score("score"),
        visual=_score("visual"),
        interaction=_score("interaction"),
        clarity=_score("clarity"),
        technical=_score("technical"),
        feedback=feedback,
        raw_line=review_line,
    )


def _product_review_payload(review: ProductReview, *, min_score: int, retry_count: int) -> dict[str, object]:
    passed = _product_review_passed(review, min_score=min_score)
    return {
        "parsed": review.parsed,
        "status": review.status,
        "controller_status": "passed" if passed else "retry",
        "score": review.score,
        "visual": review.visual,
        "interaction": review.interaction,
        "clarity": review.clarity,
        "technical": review.technical,
        "feedback": review.feedback,
        "raw_line": review.raw_line,
        "min_score": max(0, min(10, int(min_score))),
        "retry_count": max(0, int(retry_count)),
    }


def _product_review_passed(review: ProductReview, *, min_score: int) -> bool:
    if not review.parsed:
        return False
    if review.status != "pass":
        return False
    if review.score is None:
        return False
    return review.score >= max(0, min(10, int(min_score)))


def _repo_relative_path(repo: str, path: Path) -> str:
    repo_path = Path(repo).resolve()
    try:
        return str(path.resolve().relative_to(repo_path))
    except ValueError:
        return str(path)


def _valid_claude_config_root(path: Path) -> bool:
    return path.is_dir() and all((path / marker).exists() for marker in CLAUDE_CONFIG_MARKERS)


def _resolve_claude_config_root(
    explicit_root: str = "",
    *,
    env: Mapping[str, str] | None = None,
    candidates: Sequence[str] = DEFAULT_CLAUDE_CONFIG_CANDIDATES,
    require: bool = False,
) -> ClaudeConfigResolution:
    env_values = env if env is not None else os.environ
    explicit = str(explicit_root or "").strip()
    env_root = str(env_values.get("MESH_CLAUDE_CONFIG", "") or "").strip()
    checked: list[str] = []

    requested: tuple[tuple[str, str], ...] = ()
    if explicit:
        requested = (("explicit", explicit),)
    elif env_root:
        requested = (("env", env_root),)

    for source, raw_path in requested:
        path = Path(raw_path).expanduser()
        checked.append(str(path))
        if _valid_claude_config_root(path):
            return ClaudeConfigResolution(
                root=str(path.resolve()),
                source=source,
                available=True,
                markers=CLAUDE_CONFIG_MARKERS,
            )
        raise RuntimeError(
            "invalid claude-config root from "
            f"{source}: {path}; expected directory with {', '.join(CLAUDE_CONFIG_MARKERS)}"
        )

    for raw_path in candidates:
        path = Path(raw_path).expanduser()
        checked.append(str(path))
        if _valid_claude_config_root(path):
            return ClaudeConfigResolution(
                root=str(path.resolve()),
                source="default",
                available=True,
                markers=CLAUDE_CONFIG_MARKERS,
            )

    reason = "no claude-config root found"
    if checked:
        reason = f"{reason}; checked: {', '.join(checked)}"
    if require:
        raise RuntimeError(reason)
    return ClaudeConfigResolution(root="", source="none", available=False, reason=reason)


def _claude_config_contract_inventory(
    resolution: ClaudeConfigResolution,
    names: Sequence[str] | None = None,
) -> dict[str, dict[str, object]]:
    if not resolution.available or not resolution.root:
        return {}
    selected = tuple(names) if names is not None else tuple(CLAUDE_CONFIG_CONTRACTS)
    root = Path(resolution.root)
    inventory: dict[str, dict[str, object]] = {}
    for name in selected:
        if name not in CLAUDE_CONFIG_CONTRACTS:
            inventory[name] = {
                "name": name,
                "kind": "unknown",
                "relative_path": "",
                "path": "",
                "exists": False,
                "error": "unknown contract",
            }
            continue
        relative_path, kind = CLAUDE_CONFIG_CONTRACTS[name]
        path = root / relative_path
        exists = path.is_file()
        inventory[name] = {
            "name": name,
            "kind": kind,
            "relative_path": relative_path,
            "path": str(path.resolve()) if exists else str(path),
            "exists": exists,
        }
    return inventory


def _missing_claude_config_contracts(inventory: Mapping[str, Mapping[str, object]]) -> list[str]:
    return [name for name, item in inventory.items() if not bool(item.get("exists"))]


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    limit = max(1, int(max_chars or 1))
    value = str(text or "").strip()
    if len(value) <= limit:
        return value, False
    marker = "\n...[truncated]"
    if limit <= len(marker):
        return value[:limit], True
    return value[: limit - len(marker)].rstrip() + marker, True


def _split_markdown_frontmatter(text: str) -> tuple[dict[str, str], str]:
    value = str(text or "")
    if not value.startswith("---\n"):
        return {}, value
    end = value.find("\n---", 4)
    if end < 0:
        return {}, value
    frontmatter_text = value[4:end]
    body = value[value.find("\n", end + 4) + 1 :] if "\n" in value[end + 4 :] else ""
    parsed: dict[str, str] = {}
    for raw_line in frontmatter_text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line or line.startswith("-"):
            continue
        key, raw_value = line.split(":", 1)
        normalized_key = key.strip()
        value_text = raw_value.strip().strip("'\"")
        if normalized_key in CONTRACT_FRONTMATTER_KEYS and value_text:
            parsed[normalized_key] = value_text
    return parsed, body


def _first_markdown_heading(text: str) -> str:
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            return line
    return ""


def _first_tagged_section(text: str) -> str:
    body = str(text or "")
    lowered = body.lower()
    for tag in CONTRACT_TAG_SECTIONS:
        start_token = f"<{tag}>"
        end_token = f"</{tag}>"
        start = lowered.find(start_token)
        end = lowered.find(end_token, start + len(start_token)) if start >= 0 else -1
        if start >= 0 and end >= 0:
            return body[start : end + len(end_token)].strip()
    return ""


def _fallback_markdown_intro(text: str, max_lines: int = 16) -> str:
    lines: list[str] = []
    seen_heading = False
    for raw_line in str(text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if stripped.startswith("#"):
            if seen_heading:
                break
            seen_heading = True
            continue
        lines.append(line)
        if len([item for item in lines if item.strip()]) >= max(1, int(max_lines)):
            break
    return "\n".join(lines).strip()


def _compact_contract_excerpt_from_text(text: str, max_chars: int = DEFAULT_CONTRACT_EXCERPT_MAX_CHARS) -> tuple[str, bool]:
    frontmatter, body = _split_markdown_frontmatter(text)
    parts: list[str] = []
    if frontmatter:
        parts.append("\n".join(f"{key}: {frontmatter[key]}" for key in CONTRACT_FRONTMATTER_KEYS if key in frontmatter))
    heading = _first_markdown_heading(body)
    if heading:
        parts.append(heading)
    tagged = _first_tagged_section(body)
    if tagged:
        parts.append(tagged)
    else:
        intro = _fallback_markdown_intro(body)
        if intro:
            parts.append(intro)
    excerpt = "\n\n".join(part for part in parts if part.strip()).strip()
    if not excerpt:
        excerpt = str(text or "").strip()
    return _truncate_text(excerpt, max_chars)


def _claude_config_contract_excerpt(
    resolution: ClaudeConfigResolution,
    name: str,
    *,
    max_chars: int = DEFAULT_CONTRACT_EXCERPT_MAX_CHARS,
) -> dict[str, object]:
    inventory = _claude_config_contract_inventory(resolution, names=(name,))
    item = dict(inventory.get(name) or {})
    if not item:
        item = {
            "name": name,
            "kind": "unknown",
            "relative_path": "",
            "path": "",
            "exists": False,
            "error": "unknown contract",
        }
    item["excerpt"] = ""
    item["excerpt_truncated"] = False
    item["excerpt_max_chars"] = max(1, int(max_chars or 1))
    if not bool(item.get("exists")):
        return item
    path = Path(str(item.get("path") or ""))
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        item["exists"] = False
        item["error"] = str(exc)
        return item
    excerpt, truncated = _compact_contract_excerpt_from_text(text, max_chars=max_chars)
    item["excerpt"] = excerpt
    item["excerpt_truncated"] = truncated
    return item


def _claude_config_payload(
    resolution: ClaudeConfigResolution,
    *,
    include_contracts: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "available": resolution.available,
        "root": resolution.root,
        "source": resolution.source,
        "markers": list(resolution.markers),
        "reason": resolution.reason,
    }
    if include_contracts:
        contracts = _claude_config_contract_inventory(resolution)
        payload["contracts"] = contracts
        payload["missing_contracts"] = _missing_claude_config_contracts(contracts)
    return payload


def _role_contract_names(args: argparse.Namespace) -> dict[str, str]:
    explicit = {
        "boss": str(getattr(args, "boss_contract", "") or "").strip(),
        "president": str(getattr(args, "president_contract", "") or "").strip(),
        "worker": str(getattr(args, "worker_contract", "") or "").strip(),
        "reviewer": str(getattr(args, "reviewer_contract", "") or "").strip(),
    }
    if str(getattr(args, "claude_config", "") or "").strip():
        return {
            role: explicit.get(role) or DEFAULT_ROLE_CONTRACTS[role]
            for role in DEFAULT_ROLE_CONTRACTS
        }
    return explicit


def _role_contract_context(
    resolution: ClaudeConfigResolution,
    role: str,
    contract_name: str,
    *,
    max_chars: int = DEFAULT_CONTRACT_EXCERPT_MAX_CHARS,
) -> str:
    name = str(contract_name or "").strip()
    if not name:
        return ""
    inventory = _claude_config_contract_inventory(resolution, names=(name,))
    metadata = inventory.get(name) or {}
    if metadata and bool(metadata.get("exists")):
        return _clean_one_line(
            "CLAUDE_CONFIG_CONTRACT "
            f"role={role} name={name} kind={metadata.get('kind')} source={metadata.get('relative_path')}. "
        )
    payload = _claude_config_contract_excerpt(resolution, name, max_chars=max_chars)
    if not bool(payload.get("exists")):
        reason = str(payload.get("error") or "missing contract file").strip()
        return _clean_one_line(
            f"CLAUDE_CONFIG_CONTRACT role={role} name={name} unavailable: {reason}."
        )
    return ""


def _role_contract_contexts(
    resolution: ClaudeConfigResolution,
    role_contract_names: Mapping[str, str],
) -> dict[str, str]:
    return {
        role: _role_contract_context(resolution, role, role_contract_names.get(role, ""))
        for role in ("boss", "president", "worker", "reviewer")
    }


def _prompt_contract_context(context: str) -> str:
    value = _clean_one_line(context)
    return f"{value} " if value else ""


def _role_output_policy_rules(role_class: str) -> tuple[tuple[str, str, str], ...]:
    return WORKER_OUTPUT_POLICY_RULES if str(role_class or "").strip() == "worker" else NON_WORKER_OUTPUT_POLICY_RULES


def _strip_controller_prompt_artifacts(text: str) -> str:
    value = str(text or "")
    value = re.sub(
        r"CLAUDE_CONFIG_CONTRACT\b.*?END_CLAUDE_CONFIG_CONTRACT\.",
        "",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    value = re.sub(
        r"MESH_MSG\b.*?END_MESH_MSG",
        "",
        value,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return value


def _scan_role_output_policy(
    *,
    role: str,
    role_class: str,
    phase: str,
    text: str,
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    value = _strip_controller_prompt_artifacts(str(text or ""))
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        for rule_id, pattern, reason in _role_output_policy_rules(role_class):
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if not match:
                continue
            findings.append(
                {
                    "severity": "block",
                    "role": str(role or ""),
                    "role_class": str(role_class or ""),
                    "phase": str(phase or ""),
                    "rule_id": rule_id,
                    "reason": reason,
                    "line": line_number,
                    "match": _clean_one_line(match.group(0))[:160],
                    "excerpt": _clean_one_line(line)[:240],
                }
            )
    return findings


def _format_policy_findings(findings: Sequence[Mapping[str, object]]) -> str:
    lines = ["role output policy violation"]
    for item in findings:
        lines.append(
            " - "
            f"role={item.get('role')} phase={item.get('phase')} "
            f"rule={item.get('rule_id')} reason={item.get('reason')} "
            f"line={item.get('line')} match={item.get('match')}"
        )
    return "\n".join(lines)


def _policy_violations_payload(
    *,
    run_id: str,
    status: str,
    findings: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    blocking_count = sum(1 for item in findings if item.get("severity") == "block")
    return {
        "schema": "mesh.speckit.policy_violations.v1",
        "run_id": str(run_id or ""),
        "status": str(status or "blocked"),
        "blocking_count": blocking_count,
        "finding_count": len(findings),
        "findings": [dict(item) for item in findings],
    }


def _write_policy_violations_json(
    repo: str,
    handoff_dir: str,
    run_id: str,
    findings: Sequence[Mapping[str, object]],
    *,
    status: str = "blocked",
    enabled: bool = True,
) -> str:
    if not enabled or not findings:
        return ""
    run_dir = _handoff_run_dir(repo, handoff_dir, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / "policy-violations.json"
    payload = _policy_violations_payload(run_id=run_id, status=status, findings=findings)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _repo_relative_path(repo, target)


def _quality_quick_payload(
    *,
    run_id: str,
    git_status: str,
    diff_stat: str,
    test_status: str,
    allow_test_failure: bool,
    operator_allowed_edit_paths: Sequence[str],
    president_allowed_edit_paths: Sequence[str],
    effective_allowed_edit_paths: Sequence[str],
    policy_findings: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    reasons: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []
    if test_status == "failed":
        item = {
            "code": "test_failed",
            "message": "test command failed",
            "allowed": bool(allow_test_failure),
        }
        if allow_test_failure:
            warnings.append(item)
        else:
            reasons.append(item)
    if policy_findings:
        reasons.append(
            {
                "code": "policy_findings",
                "message": "policy scanner returned findings",
                "count": len(policy_findings),
            }
        )
    status = "failed" if reasons else "passed"
    return {
        "schema": "mesh.speckit.quality_quick.v1",
        "run_id": str(run_id or ""),
        "mode": "quick",
        "status": status,
        "reasons": reasons,
        "warnings": warnings,
        "evidence": {
            "git_status": git_status or "clean",
            "diff_stat": diff_stat or "empty",
            "test_status": test_status or "skipped",
            "allow_test_failure": bool(allow_test_failure),
            "operator_allowed_edit_paths": list(operator_allowed_edit_paths),
            "president_allowed_edit_paths": list(president_allowed_edit_paths),
            "effective_allowed_edit_paths": list(effective_allowed_edit_paths),
            "policy_status": "findings" if policy_findings else "clean",
            "policy_finding_count": len(policy_findings),
        },
    }


def _write_quality_quick_json(
    repo: str,
    handoff_dir: str,
    run_id: str,
    payload: Mapping[str, object],
    *,
    enabled: bool = True,
) -> str:
    if not enabled:
        return ""
    run_dir = _handoff_run_dir(repo, handoff_dir, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / "quality-quick.json"
    target.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _repo_relative_path(repo, target)


def _enforce_role_output_policy(
    *,
    role: str,
    role_class: str,
    phase: str,
    text: str,
    repo: str = "",
    handoff_dir: str = ".mesh/runs",
    run_id: str = "",
    write_sidecar: bool = False,
) -> list[dict[str, object]]:
    findings = _scan_role_output_policy(role=role, role_class=role_class, phase=phase, text=text)
    blocking = [item for item in findings if item.get("severity") == "block"]
    if blocking:
        sidecar = _write_policy_violations_json(
            repo,
            handoff_dir,
            run_id,
            blocking,
            status="blocked",
            enabled=write_sidecar,
        )
        message = _format_policy_findings(blocking)
        if sidecar:
            message = f"{message}\npolicy_violations: {sidecar}"
        raise RuntimeError(message)
    return findings


def _handoff_run_dir(repo: str, handoff_dir: str, run_id: str) -> Path:
    base = Path(str(handoff_dir or ".mesh/runs"))
    if not base.is_absolute():
        base = Path(repo) / base
    return base / run_id


def _write_handoff_json(
    repo: str,
    handoff_dir: str,
    run_id: str,
    filename: str,
    payload: dict[str, object],
    *,
    enabled: bool = True,
) -> str:
    if not enabled:
        return ""
    run_dir = _handoff_run_dir(repo, handoff_dir, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / filename
    data = {
        "schema": "mesh.speckit.handoff.v1",
        "run_id": run_id,
        **payload,
    }
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _repo_relative_path(repo, target)


def _turn_limit_text(max_turns: int) -> str:
    turns = max(1, int(max_turns or 1))
    return (
        f"Turn budget: maximum {turns} response(s) for this role in this cycle. "
        "Do not ask questions, open clarification flows, or start sub-dialogs. "
        "Answer once and close with the required marker."
    )


def _marker_instruction(marker: str) -> str:
    return f"REQUIRED_FINAL_MARKER: {marker}. End with that exact marker on its own final line."


def _delivery_tokens(run_id: str, phase: str, role: str) -> dict[str, str]:
    base = f"{_clean_one_line(run_id)}:{_clean_one_line(phase)}:{_clean_one_line(role)}"
    suffix = hashlib.sha256(base.encode("utf-8")).hexdigest()[:6].upper()
    return {
        "id": suffix,
        "start": f"S{suffix}",
        "end": f"E{suffix}",
    }


def _delivery_prompt(text: str, delivery: Mapping[str, str], marker: str = "") -> str:
    start = str(delivery.get("start") or "")
    end = str(delivery.get("end") or "")
    required_marker = str(marker or "").strip()
    return f"Reply exactly 2 lines: line1 DELIVERY_ACK {start} {end}; line2 {required_marker}. {text}"


def _screen_has_marker(text: str, marker: str) -> bool:
    expected = str(marker or "").strip()
    if not expected:
        return False
    previous_normalized = ""
    for line in str(text or "").splitlines():
        normalized = _screen_protocol_line(line)
        if normalized == expected:
            return True
        parts = [part.strip(".,;:") for part in normalized.split()]
        if parts[:1] == ["DELIVERY_ACK"] and expected in parts:
            return True
        if parts and parts[0] == expected and previous_normalized.startswith("DELIVERY_ACK "):
            return True
        previous_normalized = normalized
    return False


def _screen_protocol_line(raw_line: str) -> str:
    line = str(raw_line or "").strip()
    while line.startswith(("✦", ">", "›", "•")):
        line = line[1:].strip()
    lowered = line.lower()
    for prefix in ("line1 ", "line2 ", "line3 ", "line4 "):
        if lowered.startswith(prefix):
            return line[len(prefix) :].strip()
    return line


def _screen_has_delivery_ack(text: str, delivery: Mapping[str, str] | None) -> bool:
    if not delivery:
        return True
    start = str(delivery.get("start") or "").strip()
    end = str(delivery.get("end") or "").strip()
    if not start or not end:
        return True
    for raw_line in str(text or "").splitlines():
        line = _screen_protocol_line(raw_line)
        parts = [part.strip(".,;:") for part in line.split()]
        if len(parts) >= 3 and parts[0] == "DELIVERY_ACK" and parts[1] == start and parts[2] == end:
            return True
    return False


def _classify_controller_failure(
    *,
    screen_text: str,
    marker: str,
    delivery_ack: Mapping[str, str] | None = None,
    timeout_telemetry: TimeoutTelemetry | None = None,
) -> SupervisorAssessment:
    text = str(screen_text or "")
    lower = text.lower()
    expected_marker = str(marker or "").strip()

    if "currently experiencing high demand" in lower and "switch to gemini-2.5-flash" in lower:
        return SupervisorAssessment(
            failure_class="model_fallback_needed",
            remediation="select fallback model once and resume waiting",
        )
    if _gemini_screen_has_queued_prompt(text):
        return SupervisorAssessment(
            failure_class="queued_prompt_issue",
            remediation="resume the queued Gemini composer and continue waiting",
        )
    if "waiting for mcp servers to initialize" in lower or "prompts will be queued" in lower:
        return SupervisorAssessment(
            failure_class="provider_not_ready",
            remediation="wait for Gemini provider bootstrap before entering the controlled cycle",
        )
    if "apply this change?" in lower or "writing to " in lower or "allow execution of" in lower:
        return SupervisorAssessment(
            failure_class="approval_pattern_missing",
            remediation="extend provider approval profile or allowlist-safe auto-approval",
        )
    if "product_review" in lower and (
        "no artifact was provided" in lower
        or "unable to playtest" in lower
        or "lack of browser access" in lower
    ):
        return SupervisorAssessment(
            failure_class="review_context_missing",
            remediation="re-prompt reviewer with artifact path and source-inspection guidance",
        )
    if expected_marker and expected_marker in text and not _screen_has_marker(text, expected_marker):
        return SupervisorAssessment(
            failure_class="marker_format_issue",
            remediation="normalize marker formatting variants or shorten live markers",
        )
    if expected_marker and _screen_has_marker(text, expected_marker) and not _screen_has_delivery_ack(text, delivery_ack):
        return SupervisorAssessment(
            failure_class="delivery_ack_issue",
            remediation="normalize delivery-ack formatting variants for this provider",
        )
    if "delivery_ack" in lower and expected_marker and expected_marker in lower:
        return SupervisorAssessment(
            failure_class="delivery_ack_issue",
            remediation="accept combined ack+marker lines for this provider",
        )
    if timeout_telemetry is not None and not timeout_telemetry.screen_changed_recently:
        return SupervisorAssessment(
            failure_class="stalled_run",
            remediation="stop the run after timeout budget is exhausted without fresh pane output",
        )
    return SupervisorAssessment(
        failure_class="unknown_controller_failure",
        remediation="capture pane tail and stop the run explicitly",
    )


def _supervisor_remediation_for(failure_class: str) -> SupervisorRemediation:
    return SUPERVISOR_REMEDIATION_REGISTRY.get(
        str(failure_class or "").strip(),
        SUPERVISOR_REMEDIATION_REGISTRY["unknown_controller_failure"],
    )


def _supervisor_report_payload(
    *,
    role: str,
    marker: str,
    assessment: SupervisorAssessment,
    attempts: int = 0,
    timeout_telemetry: TimeoutTelemetry | None = None,
) -> dict[str, object]:
    remediation = _supervisor_remediation_for(assessment.failure_class)
    payload = {
        "schema": "mesh.controller.supervisor.v1",
        "role": str(role or ""),
        "marker": str(marker or ""),
        "failure_class": assessment.failure_class,
        "failure_summary": assessment.remediation,
        "action": remediation.action,
        "retryable": remediation.retryable,
        "max_attempts": remediation.max_attempts,
        "attempts": max(0, int(attempts)),
    }
    if timeout_telemetry is not None:
        payload["timeout"] = {
            "timeout_s": round(float(timeout_telemetry.timeout_s), 3),
            "elapsed_s": round(float(timeout_telemetry.elapsed_s), 3),
            "poll_interval_s": round(float(timeout_telemetry.poll_interval_s), 3),
            "poll_count": int(timeout_telemetry.poll_count),
            "last_progress_s_ago": round(float(timeout_telemetry.last_progress_s_ago), 3),
            "screen_changed_recently": bool(timeout_telemetry.screen_changed_recently),
            "marker_seen_without_ack": bool(timeout_telemetry.marker_seen_without_ack),
        }
    return payload


def _supervisor_finalize_assessment(
    *,
    screen_text: str,
    assessment: SupervisorAssessment,
    attempts: int = 0,
) -> SupervisorAssessment:
    current = assessment
    lower = str(screen_text or "").lower()
    if current.failure_class == "queued_prompt_issue":
        remediation = _supervisor_remediation_for(current.failure_class)
        if max(0, int(attempts)) >= remediation.max_attempts:
            if "waiting for mcp servers to initialize" in lower or "prompts will be queued" in lower:
                return SupervisorAssessment(
                    failure_class="provider_not_ready",
                    remediation="Gemini provider remained in bootstrap/queued state after queued-prompt recovery budget was exhausted",
                )
    if current.failure_class == "model_fallback_needed":
        remediation = _supervisor_remediation_for(current.failure_class)
        if max(0, int(attempts)) >= remediation.max_attempts:
            if "currently experiencing high demand" in lower or "switch to gemini-2.5-flash" in lower:
                return SupervisorAssessment(
                    failure_class="provider_not_ready",
                    remediation="Gemini provider remained in high-demand fallback state after model-switch recovery budget was exhausted",
                )
    return current


def _supervisor_outcome_fields(
    *,
    status: str,
    assessment: SupervisorAssessment | None = None,
    attempts: int = 0,
) -> dict[str, object]:
    item = assessment or SupervisorAssessment(failure_class="", remediation="")
    return {
        "supervisor_status": str(status or "").strip() or "unknown",
        "supervisor_failure_class": str(item.failure_class or "").strip(),
        "supervisor_remediation": str(item.remediation or "").strip(),
        "supervisor_attempts": max(0, int(attempts)),
    }


def _supervisor_error_field(error_text: str, key: str) -> str:
    prefix = f"{str(key or '').strip()}="
    if not prefix:
        return ""
    for raw_line in str(error_text or "").splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    return ""


def _supervisor_error_role(error_text: str) -> str:
    match = re.search(r"\brole=([A-Za-z0-9._-]+)", str(error_text or ""))
    return str(match.group(1) if match else "").strip()


def _provider_fallback_command(
    *,
    role: str,
    commands: Mapping[str, str],
    error_text: str,
    used_roles: set[str],
) -> str:
    failed_role = _supervisor_error_role(error_text)
    failure_class = _supervisor_error_field(error_text, "supervisor_failure_class")
    if failed_role != str(role or "").strip():
        return ""
    if failure_class != "provider_not_ready":
        return ""
    if failed_role in used_roles:
        return ""
    if _command_name(str(commands.get(failed_role, "") or "")) != "gemini":
        return ""
    return "codex"


def _supervisor_failure_handoff_payload(
    *,
    feature: str,
    task: str,
    error_text: str,
) -> dict[str, object]:
    failure_class = _supervisor_error_field(error_text, "supervisor_failure_class")
    remediation = _supervisor_error_field(error_text, "supervisor_remediation")
    attempts_text = _supervisor_error_field(error_text, "supervisor_attempts")
    try:
        attempts = int(attempts_text) if attempts_text else 0
    except ValueError:
        attempts = 0
    payload = {
        "phase": "speckit.report",
        "from_role": "controller",
        "to_role": "operator",
        "feature": str(feature or ""),
        "task": str(task or ""),
        "run_status": "failed",
        "error": str(error_text or "").strip(),
        **_supervisor_outcome_fields(
            status="failed",
            assessment=SupervisorAssessment(
                failure_class=failure_class,
                remediation=remediation,
            ),
            attempts=attempts,
        ),
    }
    action = _supervisor_error_field(error_text, "supervisor_action")
    if action:
        payload["supervisor_action"] = action
    retryable = _supervisor_error_field(error_text, "supervisor_retryable")
    if retryable:
        payload["supervisor_retryable"] = retryable.lower() == "true"
    max_attempts = _supervisor_error_field(error_text, "supervisor_max_attempts")
    if max_attempts:
        try:
            payload["supervisor_max_attempts"] = int(max_attempts)
        except ValueError:
            payload["supervisor_max_attempts"] = max_attempts
    timeout_elapsed = _supervisor_error_field(error_text, "timeout_elapsed_s")
    timeout_poll_count = _supervisor_error_field(error_text, "timeout_poll_count")
    timeout_last_progress = _supervisor_error_field(error_text, "timeout_last_progress_s_ago")
    if timeout_elapsed or timeout_poll_count or timeout_last_progress:
        timeout_payload: dict[str, object] = {}
        if timeout_elapsed:
            try:
                timeout_payload["elapsed_s"] = float(timeout_elapsed)
            except ValueError:
                timeout_payload["elapsed_s"] = timeout_elapsed
        if timeout_poll_count:
            try:
                timeout_payload["poll_count"] = int(timeout_poll_count)
            except ValueError:
                timeout_payload["poll_count"] = timeout_poll_count
        if timeout_last_progress:
            try:
                timeout_payload["last_progress_s_ago"] = float(timeout_last_progress)
            except ValueError:
                timeout_payload["last_progress_s_ago"] = timeout_last_progress
        payload["timeout"] = timeout_payload
    return payload


def _timeout_telemetry(
    *,
    timeout: float,
    poll_interval: float,
    poll_count: int,
    start_time: float,
    now: float,
    last_progress_time: float,
    marker_seen_without_ack: bool = False,
) -> TimeoutTelemetry:
    elapsed = max(0.0, float(now) - float(start_time))
    last_progress_ago = max(0.0, float(now) - float(last_progress_time))
    recent_threshold = max(float(poll_interval) * 2.0, 2.0)
    return TimeoutTelemetry(
        timeout_s=max(1.0, float(timeout)),
        elapsed_s=elapsed,
        poll_interval_s=max(0.1, float(poll_interval)),
        poll_count=max(0, int(poll_count)),
        last_progress_s_ago=last_progress_ago,
        screen_changed_recently=last_progress_ago <= recent_threshold,
        marker_seen_without_ack=bool(marker_seen_without_ack),
    )


def _run_local_capture(args: list[str], *, cwd: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        timeout=max(1.0, float(timeout)),
    )


def _git_status_short(repo: str) -> str:
    proc = _run_local_capture(["git", "status", "--short"], cwd=repo)
    if proc.returncode != 0:
        return f"[git status failed]\n{proc.stderr.strip() or proc.stdout.strip()}"
    return proc.stdout.strip()


def _git_diff_stat(repo: str) -> str:
    proc = _run_local_capture(["git", "diff", "--stat"], cwd=repo)
    if proc.returncode != 0:
        return f"[git diff --stat failed]\n{proc.stderr.strip() or proc.stdout.strip()}"
    return proc.stdout.strip()


def _run_optional_test_command(repo: str, command_text: str, timeout: float) -> tuple[str, str]:
    command = str(command_text or "").strip()
    if not command:
        return "skipped", ""
    try:
        proc = _run_local_capture(shlex.split(command), cwd=repo, timeout=timeout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return "failed", str(exc)
    output = "\n".join(part for part in (proc.stdout.strip(), proc.stderr.strip()) if part)
    return ("passed" if proc.returncode == 0 else "failed"), output[-4000:]


def _load_mesh_iterm_ui():
    script_path = Path(__file__).resolve().with_name("mesh_iterm_ui.py")
    spec = importlib.util.spec_from_file_location("mesh_iterm_ui_for_control", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load mesh UI module at {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def _refresh_iterm_app(connection: Any, current_app: Any) -> Any:
    try:
        import iterm2

        refreshed = await iterm2.async_get_app(connection)
        if refreshed is not None:
            return refreshed
    except Exception:
        pass
    return current_app


def _ensure_command(command_text: str) -> None:
    executable = str(command_text or "").strip().split(" ", 1)[0]
    if not executable:
        raise RuntimeError("empty CLI command")
    if shutil.which(executable) is None:
        raise RuntimeError(f"required command not found in PATH: {executable}")


def _set_env_temporarily(values: dict[str, str]) -> dict[str, str | None]:
    previous: dict[str, str | None] = {}
    for key, value in values.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    return previous


def _restore_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


async def _launch_role_layout(
    connection: Any,
    *,
    repo: str,
    roles: list[str],
    commands: dict[str, str],
    ui_group_id: str,
) -> None:
    ui = _load_mesh_iterm_ui()
    repo_name = _repo_name(repo)
    env_values = {
        "MESH_UI_ROLES": ",".join(roles),
        "MESH_UI_MAX_PANES_PER_TAB": str(max(1, len(roles))),
    }
    for role in roles:
        env_values[_ui_command_env_key(role)] = _role_launch_command(repo, commands[role])
    env_previous = _set_env_temporarily(env_values)
    try:
        cfg = ui.UiConfig(
            repo=repo,
            repo_name=repo_name,
            roles=roles,
            max_panes_per_tab=max(1, len(roles)),
            single_tab=False,
            replace_tabs=False,
            preset="auto",
            attach_live=False,
            ui_group_id=ui_group_id,
        )
        await ui._launch_layout(connection, cfg)
    finally:
        _restore_env(env_previous)


async def _screen_tail(session: Any, lines: int = 20) -> str:
    screen = await session.async_get_screen_contents()
    collected: list[str] = []
    for idx in range(getattr(screen, "number_of_lines", 0)):
        raw = str(screen.line(idx).string or "")
        line = raw.replace("\x00", "").rstrip()
        if line.strip():
            collected.append(line)
    return "\n".join(collected[-max(1, int(lines)) :])


async def _send_line(session: Any, text: str) -> None:
    await session.async_activate()
    await asyncio.sleep(0.25)
    value = str(text or "")
    chunk_size = max(1, int(SEND_TEXT_CHUNK_CHARS))
    for offset in range(0, len(value), chunk_size):
        await session.async_send_text(value[offset : offset + chunk_size])
        await asyncio.sleep(0.015)
    await asyncio.sleep(0.08)
    await session.async_send_text("\r")


async def _restart_mesh_role_pane(pane: MeshPane, repo: str, command_text: str) -> None:
    await pane.session.async_activate()
    await asyncio.sleep(0.2)
    await pane.session.async_send_text(_key_text("ctrl-c"))
    await asyncio.sleep(0.3)
    await _send_line(pane.session, _role_restart_command(repo, command_text))


def _auto_approval_choice(screen_text: str) -> tuple[str, str]:
    lower = screen_text.lower()
    compact = "".join(ch for ch in lower if ch.isalnum())
    if "apply this change?" in lower:
        return "1", "apply change once"
    if "currently experiencing high demand" in lower and "switch to gemini-2.5-flash" in lower:
        return "2", "switch gemini flash"
    if "allow execution of" in lower:
        return "2", "allow command for session"
    if (
        (
            "do you trust" in lower
            or "trust this folder" in lower
            or "trust the files" in lower
            or "doyoutrust" in compact
            or "trustthecontentsofthisdirectory" in compact
        )
        and ("1." in screen_text or "1 " in screen_text)
    ):
        return "1", "trust folder"
    return "", ""


def _auto_approval_edit_path(screen_text: str) -> str:
    for line in screen_text.splitlines():
        lowered = line.lower()
        idx = lowered.find("edit ")
        if idx < 0 or ":" not in line[idx:]:
            write_idx = lowered.find("writing to ")
            if write_idx < 0:
                continue
            value = line[write_idx + len("writing to ") :]
            return value.strip().strip("'\"`│")
        value = line[idx + len("edit ") :].split(":", 1)[0]
        return value.strip().strip("'\"`│")
    return ""


def _normalize_edit_path(path: str) -> str:
    value = str(path or "").strip().strip("'\"`")
    while value.startswith("./"):
        value = value[2:]
    return value


def _edit_path_allowed(path: str, allowed_paths: tuple[str, ...]) -> bool:
    if not allowed_paths:
        return True
    normalized = _normalize_edit_path(path)
    for allowed in allowed_paths:
        item = _normalize_edit_path(allowed)
        if not item:
            continue
        if normalized == item:
            return True
        if item.endswith("/") and normalized.startswith(item):
            return True
    return False


def _parse_allowed_edit_paths(text: str) -> tuple[str, ...]:
    for raw_line in reversed(text.splitlines()):
        line = raw_line.strip()
        lowered = line.lower()
        if "allowed_edit_paths" not in lowered and "allowed edit paths" not in lowered:
            continue
        if "path1" in lowered or "path2" in lowered or "for repo-relative files" in lowered:
            continue
        if ":" not in line:
            continue
        value = line.split(":", 1)[1].strip()
        if not value:
            return ()
        if value.lower().startswith("any"):
            return ()
        if value.startswith("["):
            try:
                loaded = json.loads(value)
            except json.JSONDecodeError:
                loaded = None
            if isinstance(loaded, list):
                return tuple(
                    _normalize_edit_path(str(item))
                    for item in loaded
                    if _normalize_edit_path(str(item)).lower() not in {"", "any", "none", "n/a", "unknown"}
                )
        paths: list[str] = []
        for item in value.replace(";", ",").split(","):
            normalized = _normalize_edit_path(item)
            if normalized.lower() in {"", "any", "none", "n/a", "unknown"}:
                continue
            if normalized.lower() in {"path1", "path2"} or "repo-relative" in normalized.lower():
                continue
            paths.append(normalized)
        return tuple(paths)
    return ()


def _effective_edit_allowlist(
    operator_paths: tuple[str, ...],
    president_paths: tuple[str, ...],
) -> tuple[str, ...]:
    if operator_paths and president_paths:
        return tuple(path for path in president_paths if _edit_path_allowed(path, operator_paths))
    if president_paths:
        return president_paths
    return operator_paths


def _edit_prompt_allowed_paths_for_role(role: str, worker_role: str, allowed_paths: tuple[str, ...]) -> tuple[str, ...]:
    return allowed_paths if role == worker_role else NO_AUTO_EDIT_PATHS


def _auto_approval_signature(screen_text: str, choice: str, reason: str) -> str:
    if reason == "trust folder":
        return f"{choice}:{reason}"
    if reason.startswith("reject edit"):
        return f"{choice}:{reason}"
    if reason == "apply change once":
        edit_path = _auto_approval_edit_path(screen_text)
        if edit_path:
            prompt_tail = "\n".join(screen_text.splitlines()[-80:])
            return f"{choice}:{reason}:{_normalize_edit_path(edit_path).lower()}:{prompt_tail}"
    if reason == "allow command for session":
        for line in reversed(screen_text.splitlines()):
            if "allow execution of" in line.lower():
                return f"{choice}:{reason}:{line.strip().lower()}"
        return f"{choice}:{reason}"
    return f"{choice}:{reason}:{chr(10).join(screen_text.splitlines()[-12:])}"


async def _maybe_auto_approve_prompt(
    session: Any,
    screen_text: str,
    *,
    role: str,
    enabled: bool,
    seen: set[str],
    allowed_edit_paths: tuple[str, ...] = (),
) -> bool:
    if not enabled:
        return False
    choice, reason = _auto_approval_choice(screen_text)
    if not choice:
        return False
    if reason == "apply change once":
        edit_path = _auto_approval_edit_path(screen_text)
        if allowed_edit_paths == NO_AUTO_EDIT_PATHS:
            choice = "4"
            reason = f"reject edit in non-worker role: {_normalize_edit_path(edit_path) or 'unknown'}"
        elif not edit_path:
            return False
        elif not _edit_path_allowed(edit_path, allowed_edit_paths):
            choice = "4"
            reason = f"reject edit outside allowlist: {_normalize_edit_path(edit_path)}"
    signature = _auto_approval_signature(screen_text, choice, reason)
    if signature in seen:
        return False
    seen.add(signature)
    print(f"auto-approve {role}: {reason} -> {choice}")
    await _send_line(session, choice)
    await asyncio.sleep(1.0)
    return True


def _supervisor_can_auto_remediate_reason(reason: str, *, auto_approve_prompts: bool) -> bool:
    normalized = str(reason or "").strip().lower()
    if normalized in {"switch gemini flash", "allow command for session", "trust folder"}:
        return True
    if normalized == "apply change once":
        return bool(auto_approve_prompts)
    return False


async def _maybe_supervisor_remediate(
    session: Any,
    screen_text: str,
    *,
    role: str,
    assessment: SupervisorAssessment,
    auto_approve_prompts: bool,
    seen: set[str],
    attempts: dict[str, int],
    allowed_edit_paths: tuple[str, ...] = (),
) -> bool:
    remediation = _supervisor_remediation_for(assessment.failure_class)
    if not remediation.retryable or remediation.max_attempts <= 0:
        return False
    current_attempts = attempts.get(assessment.failure_class, 0)
    if current_attempts >= remediation.max_attempts:
        return False
    if assessment.failure_class == "queued_prompt_issue":
        changed = await _maybe_resume_gemini_queued_prompt(
            session,
            screen_text,
            role=role,
            seen=seen,
            attempt_index=current_attempts + 1,
        )
        if not changed:
            return False
        attempts[assessment.failure_class] = current_attempts + 1
        print(
            f"supervisor remediate {role}: {assessment.failure_class} "
            f"{attempts[assessment.failure_class]}/{remediation.max_attempts}"
        )
        return True
    choice, reason = _auto_approval_choice(screen_text)
    if not choice or not _supervisor_can_auto_remediate_reason(reason, auto_approve_prompts=auto_approve_prompts):
        return False
    if assessment.failure_class == "model_fallback_needed" and reason != "switch gemini flash":
        return False
    if assessment.failure_class == "approval_pattern_missing" and reason == "switch gemini flash":
        return False
    changed = await _maybe_auto_approve_prompt(
        session,
        screen_text,
        role=role,
        enabled=True,
        seen=seen,
        allowed_edit_paths=allowed_edit_paths,
    )
    if not changed:
        return False
    attempts[assessment.failure_class] = current_attempts + 1
    print(
        f"supervisor remediate {role}: {assessment.failure_class} "
        f"{attempts[assessment.failure_class]}/{remediation.max_attempts}"
    )
    return True


async def _wait_for_screen_marker(
    session: Any,
    *,
    role: str,
    marker: str,
    timeout: float,
    poll_interval: float,
    auto_approve_prompts: bool = False,
    allowed_edit_paths: tuple[str, ...] = (),
    delivery_ack: Mapping[str, str] | None = None,
) -> None:
    loop = asyncio.get_running_loop()
    timeout_s = max(1.0, float(timeout))
    poll_interval_s = max(0.5, float(poll_interval))
    start_time = loop.time()
    deadline = start_time + timeout_s
    last_dump = ""
    last_dump_hash = ""
    last_progress_time = start_time
    poll_count = 0
    seen_auto_approvals: set[str] = set()
    supervisor_attempts: dict[str, int] = {}
    marker_seen_without_ack = False
    while loop.time() < deadline:
        last_dump = await _screen_tail(session, lines=360)
        poll_count += 1
        dump_hash = hashlib.sha256(last_dump.encode("utf-8")).hexdigest()
        if dump_hash != last_dump_hash:
            last_dump_hash = dump_hash
            last_progress_time = loop.time()
        if _screen_has_marker(last_dump, marker):
            if _screen_has_delivery_ack(last_dump, delivery_ack):
                return
            marker_seen_without_ack = True
        assessment = _classify_controller_failure(
            screen_text=last_dump,
            marker=marker,
            delivery_ack=delivery_ack,
        )
        if await _maybe_supervisor_remediate(
            session,
            last_dump,
            role=role,
            assessment=assessment,
            auto_approve_prompts=auto_approve_prompts,
            seen=seen_auto_approvals,
            attempts=supervisor_attempts,
            allowed_edit_paths=allowed_edit_paths,
        ):
            continue
        if await _maybe_auto_approve_prompt(
            session,
            last_dump,
            role=role,
            enabled=auto_approve_prompts,
            seen=seen_auto_approvals,
            allowed_edit_paths=allowed_edit_paths,
        ):
            continue
        await asyncio.sleep(poll_interval_s)
    print(f"--- last dump for {role} ---")
    print(last_dump)
    telemetry = _timeout_telemetry(
        timeout=timeout_s,
        poll_interval=poll_interval_s,
        poll_count=poll_count,
        start_time=start_time,
        now=loop.time(),
        last_progress_time=last_progress_time,
        marker_seen_without_ack=marker_seen_without_ack,
    )
    assessment = _classify_controller_failure(
        screen_text=last_dump,
        marker=marker,
        delivery_ack=delivery_ack,
        timeout_telemetry=telemetry,
    )
    attempt_count = sum(supervisor_attempts.values())
    assessment = _supervisor_finalize_assessment(
        screen_text=last_dump,
        assessment=assessment,
        attempts=attempt_count,
    )
    report = _supervisor_report_payload(
        role=role,
        marker=marker,
        assessment=assessment,
        attempts=attempt_count,
        timeout_telemetry=telemetry,
    )
    if marker_seen_without_ack:
        raise RuntimeError(
            f"timed out waiting for delivery ack for marker {marker!r} in role={role}\n"
            f"supervisor_failure_class={assessment.failure_class}\n"
            f"supervisor_remediation={assessment.remediation}\n"
            f"supervisor_action={report['action']}\n"
            f"supervisor_retryable={report['retryable']}\n"
            f"supervisor_max_attempts={report['max_attempts']}\n"
            f"supervisor_attempts={attempt_count}\n"
            f"timeout_elapsed_s={report['timeout']['elapsed_s']}\n"
            f"timeout_poll_count={report['timeout']['poll_count']}\n"
            f"timeout_last_progress_s_ago={report['timeout']['last_progress_s_ago']}"
        )
    raise RuntimeError(
        f"timed out waiting for marker {marker!r} in role={role}\n"
        f"supervisor_failure_class={assessment.failure_class}\n"
        f"supervisor_remediation={assessment.remediation}\n"
        f"supervisor_action={report['action']}\n"
        f"supervisor_retryable={report['retryable']}\n"
        f"supervisor_max_attempts={report['max_attempts']}\n"
        f"supervisor_attempts={attempt_count}\n"
        f"timeout_elapsed_s={report['timeout']['elapsed_s']}\n"
        f"timeout_poll_count={report['timeout']['poll_count']}\n"
        f"timeout_last_progress_s_ago={report['timeout']['last_progress_s_ago']}"
    )


async def _wait_for_screen_any(
    session: Any,
    *,
    role: str,
    markers: tuple[str, ...],
    timeout: float,
    poll_interval: float,
    description: str,
    auto_approve_prompts: bool = False,
    allowed_edit_paths: tuple[str, ...] = (),
) -> str:
    loop = asyncio.get_running_loop()
    timeout_s = max(1.0, float(timeout))
    poll_interval_s = max(0.5, float(poll_interval))
    start_time = loop.time()
    deadline = start_time + timeout_s
    last_dump = ""
    last_dump_hash = ""
    last_progress_time = start_time
    poll_count = 0
    seen_auto_approvals: set[str] = set()
    supervisor_attempts: dict[str, int] = {}
    while loop.time() < deadline:
        last_dump = await _screen_tail(session, lines=360)
        poll_count += 1
        dump_hash = hashlib.sha256(last_dump.encode("utf-8")).hexdigest()
        if dump_hash != last_dump_hash:
            last_dump_hash = dump_hash
            last_progress_time = loop.time()
        assessment = _classify_controller_failure(
            screen_text=last_dump,
            marker="",
            delivery_ack=None,
        )
        if await _maybe_supervisor_remediate(
            session,
            last_dump,
            role=role,
            assessment=assessment,
            auto_approve_prompts=auto_approve_prompts,
            seen=seen_auto_approvals,
            attempts=supervisor_attempts,
            allowed_edit_paths=allowed_edit_paths,
        ):
            continue
        if auto_approve_prompts and _auto_approval_choice(last_dump)[0]:
            await _maybe_auto_approve_prompt(
                session,
                last_dump,
                role=role,
                enabled=True,
                seen=seen_auto_approvals,
                allowed_edit_paths=allowed_edit_paths,
            )
            await asyncio.sleep(poll_interval_s)
            continue
        for marker in markers:
            if marker and marker in last_dump:
                return marker
        await asyncio.sleep(poll_interval_s)
    print(f"--- last dump for {role} ---")
    print(last_dump)
    telemetry = _timeout_telemetry(
        timeout=timeout_s,
        poll_interval=poll_interval_s,
        poll_count=poll_count,
        start_time=start_time,
        now=loop.time(),
        last_progress_time=last_progress_time,
        marker_seen_without_ack=False,
    )
    assessment = _classify_controller_failure(
        screen_text=last_dump,
        marker="",
        delivery_ack=None,
        timeout_telemetry=telemetry,
    )
    attempt_count = sum(supervisor_attempts.values())
    assessment = _supervisor_finalize_assessment(
        screen_text=last_dump,
        assessment=assessment,
        attempts=attempt_count,
    )
    report = _supervisor_report_payload(
        role=role,
        marker="",
        assessment=assessment,
        attempts=attempt_count,
        timeout_telemetry=telemetry,
    )
    raise RuntimeError(
        f"timed out waiting for {description} in role={role}\n"
        f"supervisor_failure_class={assessment.failure_class}\n"
        f"supervisor_remediation={assessment.remediation}\n"
        f"supervisor_action={report['action']}\n"
        f"supervisor_retryable={report['retryable']}\n"
        f"supervisor_max_attempts={report['max_attempts']}\n"
        f"supervisor_attempts={attempt_count}\n"
        f"timeout_elapsed_s={report['timeout']['elapsed_s']}\n"
        f"timeout_poll_count={report['timeout']['poll_count']}\n"
        f"timeout_last_progress_s_ago={report['timeout']['last_progress_s_ago']}"
    )


def _gemini_screen_ready(screen_text: str) -> bool:
    lower = str(screen_text or "").lower()
    if "type your message" not in lower:
        return False
    if "waiting for mcp servers to initialize" in lower:
        return False
    if "prompts will be queued" in lower:
        return False
    if "queued (press" in lower:
        return False
    return True


def _gemini_screen_has_queued_prompt(screen_text: str) -> bool:
    lower = str(screen_text or "").lower()
    return "queued (press" in lower and "type your message" in lower


def _gemini_queued_prompt_signature(screen_text: str) -> str:
    lines = [line.strip() for line in str(screen_text or "").splitlines() if line.strip()]
    queued_lines = [line for line in lines if "Queued (press" in line or line.startswith("Reply exactly")]
    if queued_lines:
        return "\n".join(queued_lines[-4:])
    return "\n".join(lines[-8:])


async def _maybe_resume_gemini_queued_prompt(
    session: Any,
    screen_text: str,
    *,
    role: str,
    seen: set[str],
    attempt_index: int = 1,
) -> bool:
    if not _gemini_screen_has_queued_prompt(screen_text):
        return False
    signature = f"{max(1, int(attempt_index))}:{_gemini_queued_prompt_signature(screen_text)}"
    if signature in seen:
        return False
    seen.add(signature)
    print(f"resume queued Gemini prompt for {role}")
    await session.async_activate()
    await asyncio.sleep(0.2)
    await session.async_send_text(_key_text("up"))
    await asyncio.sleep(0.2)
    await session.async_send_text("\r")
    await asyncio.sleep(1.0)
    return True


async def _wait_for_gemini_ready(
    session: Any,
    *,
    role: str,
    timeout: float,
    poll_interval: float,
    auto_approve_prompts: bool = False,
    allowed_edit_paths: tuple[str, ...] = (),
) -> None:
    loop = asyncio.get_running_loop()
    timeout_s = max(1.0, float(timeout))
    poll_interval_s = max(0.5, float(poll_interval))
    start_time = loop.time()
    deadline = start_time + timeout_s
    last_dump = ""
    last_dump_hash = ""
    last_progress_time = start_time
    poll_count = 0
    seen_auto_approvals: set[str] = set()
    supervisor_attempts: dict[str, int] = {}
    while loop.time() < deadline:
        last_dump = await _screen_tail(session, lines=360)
        poll_count += 1
        dump_hash = hashlib.sha256(last_dump.encode("utf-8")).hexdigest()
        if dump_hash != last_dump_hash:
            last_dump_hash = dump_hash
            last_progress_time = loop.time()
        if _gemini_screen_ready(last_dump):
            return
        assessment = _classify_controller_failure(
            screen_text=last_dump,
            marker="",
            delivery_ack=None,
        )
        if await _maybe_supervisor_remediate(
            session,
            last_dump,
            role=role,
            assessment=assessment,
            auto_approve_prompts=auto_approve_prompts,
            seen=seen_auto_approvals,
            attempts=supervisor_attempts,
            allowed_edit_paths=allowed_edit_paths,
        ):
            continue
        if auto_approve_prompts and _auto_approval_choice(last_dump)[0]:
            await _maybe_auto_approve_prompt(
                session,
                last_dump,
                role=role,
                enabled=True,
                seen=seen_auto_approvals,
                allowed_edit_paths=allowed_edit_paths,
            )
            await asyncio.sleep(poll_interval_s)
            continue
        await asyncio.sleep(poll_interval_s)
    print(f"--- last dump for {role} ---")
    print(last_dump)
    telemetry = _timeout_telemetry(
        timeout=timeout_s,
        poll_interval=poll_interval_s,
        poll_count=poll_count,
        start_time=start_time,
        now=loop.time(),
        last_progress_time=last_progress_time,
        marker_seen_without_ack=False,
    )
    assessment = _classify_controller_failure(
        screen_text=last_dump,
        marker="",
        delivery_ack=None,
        timeout_telemetry=telemetry,
    )
    report = _supervisor_report_payload(
        role=role,
        marker="",
        assessment=assessment,
        attempts=sum(supervisor_attempts.values()),
        timeout_telemetry=telemetry,
    )
    attempt_count = sum(supervisor_attempts.values())
    raise RuntimeError(
        f"timed out waiting for Gemini prompt in role={role}\n"
        f"supervisor_failure_class={assessment.failure_class}\n"
        f"supervisor_remediation={assessment.remediation}\n"
        f"supervisor_action={report['action']}\n"
        f"supervisor_retryable={report['retryable']}\n"
        f"supervisor_max_attempts={report['max_attempts']}\n"
        f"supervisor_attempts={attempt_count}\n"
        f"timeout_elapsed_s={report['timeout']['elapsed_s']}\n"
        f"timeout_poll_count={report['timeout']['poll_count']}\n"
        f"timeout_last_progress_s_ago={report['timeout']['last_progress_s_ago']}"
    )


async def _wait_for_cli_ready(
    session: Any,
    *,
    role: str,
    command_text: str,
    timeout: float,
    poll_interval: float,
    auto_approve_prompts: bool = False,
    allowed_edit_paths: tuple[str, ...] = (),
) -> None:
    command_name = _command_name(command_text)
    if command_name == "gemini":
        await _wait_for_gemini_ready(
            session,
            role=role,
            timeout=timeout,
            poll_interval=poll_interval,
            auto_approve_prompts=auto_approve_prompts,
            allowed_edit_paths=allowed_edit_paths,
        )
    elif command_name == "codex":
        await _wait_for_screen_any(
            session,
            role=role,
            markers=("Write tests for", "›"),
            timeout=timeout,
            poll_interval=poll_interval,
            description="Codex prompt",
            auto_approve_prompts=auto_approve_prompts,
            allowed_edit_paths=allowed_edit_paths,
        )
    elif command_name == "claude":
        await _wait_for_screen_any(
            session,
            role=role,
            markers=("cwd:", "cwd", "Claude", ">"),
            timeout=timeout,
            poll_interval=poll_interval,
            description="Claude prompt",
            auto_approve_prompts=auto_approve_prompts,
            allowed_edit_paths=allowed_edit_paths,
        )


async def _close_mesh_tabs(app: Any, repo: str, ui_group_id: str = "") -> int:
    tabs: dict[int, Any] = {}
    for pane in await _mesh_sessions(app, repo, ui_group_id):
        tabs[id(pane.tab)] = pane.tab
    for tab in tabs.values():
        close_fn = getattr(tab, "async_close", None)
        if close_fn is None:
            continue
        try:
            await close_fn(force=True)
        except TypeError:
            await close_fn()
    return len(tabs)


async def _run_two_cli_smoke(app: Any, args: argparse.Namespace) -> int:
    ui_group_id = str(getattr(args, "ui_group_id", "") or "").strip()
    boss = await _find_mesh_pane(app, args.repo, args.boss_role, ui_group_id)
    president = await _find_mesh_pane(app, args.repo, args.president_role, ui_group_id)

    run_id = str(args.run_id or uuid.uuid4().hex[:8]).upper().replace("-", "_")
    gemini_marker = f"GEMINI_TO_CODEX_{run_id}"
    codex_ack = f"CODEX_SAW_GEMINI_{run_id}"
    codex_marker = f"CODEX_TO_GEMINI_{run_id}"
    gemini_ack = f"GEMINI_SAW_CODEX_{run_id}"

    print(
        f"panes: boss=W{boss.window_index} T{boss.tab_index} S{boss.session_index} "
        f"president=W{president.window_index} T{president.tab_index} S{president.session_index}"
    )
    print(f"run_id: {run_id}")

    if str(args.gemini_model or "").strip():
        print(f"selecting boss model: {args.gemini_model}")
        await _send_line(boss.session, f"/model {args.gemini_model}")
        await asyncio.sleep(2)

    print("1. Gemini emits a marker")
    await _send_line(
        boss.session,
        f'Rispondi solo con la concatenazione esatta di "GEMINI_TO_CODEX_" e "{run_id}".',
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=gemini_marker,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("2. Relay Gemini marker to Codex and verify Codex acknowledgement")
    await _send_line(
        president.session,
        (
            f"Messaggio ricevuto da boss: {gemini_marker}. "
            f'Rispondi solo con la concatenazione esatta di "CODEX_SAW_GEMINI_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=codex_ack,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("3. Codex emits a marker")
    await _send_line(
        president.session,
        f'Rispondi solo con la concatenazione esatta di "CODEX_TO_GEMINI_" e "{run_id}".',
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=codex_marker,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("4. Relay Codex marker to Gemini and verify Gemini acknowledgement")
    await _send_line(
        boss.session,
        (
            f"Messaggio ricevuto da president: {codex_marker}. "
            f'Rispondi solo con la concatenazione esatta di "GEMINI_SAW_CODEX_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=gemini_ack,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("success:")
    print(f"  {gemini_marker}")
    print(f"  {codex_ack}")
    print(f"  {codex_marker}")
    print(f"  {gemini_ack}")
    return 0


async def _run_two_cli_e2e(connection: Any, app: Any, args: argparse.Namespace) -> int:
    _ensure_command(args.boss_cmd)
    _ensure_command(args.president_cmd)

    repo = str(args.repo or "").strip()
    repo_name = _repo_name(repo)
    ui_group_id = str(args.ui_group_id or f"{repo_name}-two-cli-{uuid.uuid4().hex[:8]}").strip()
    roles = [args.boss_role, args.president_role]
    commands = {
        args.boss_role: args.boss_cmd,
        args.president_role: args.president_cmd,
    }
    print(f"opening two-cli test layout group={ui_group_id}")
    try:
        await _launch_role_layout(connection, repo=repo, roles=roles, commands=commands, ui_group_id=ui_group_id)
        await asyncio.sleep(max(0.0, float(args.startup_wait)))
        boss = await _find_mesh_pane(app, repo, args.boss_role, ui_group_id)
        president = await _find_mesh_pane(app, repo, args.president_role, ui_group_id)
        print("waiting for CLI prompts")
        await _wait_for_cli_ready(
            boss.session,
            role=args.boss_role,
            command_text=args.boss_cmd,
            timeout=args.startup_timeout,
            poll_interval=args.poll_interval,
        )
        await _wait_for_cli_ready(
            president.session,
            role=args.president_role,
            command_text=args.president_cmd,
            timeout=args.startup_timeout,
            poll_interval=args.poll_interval,
        )
        smoke_args = argparse.Namespace(
            repo=repo,
            ui_group_id=ui_group_id,
            boss_role=args.boss_role,
            president_role=args.president_role,
            gemini_model=args.gemini_model,
            run_id="",
            response_timeout=args.response_timeout,
            poll_interval=args.poll_interval,
        )
        return await _run_two_cli_smoke(app, smoke_args)
    finally:
        if not args.keep_open:
            closed = await _close_mesh_tabs(app, repo, ui_group_id)
            print(f"closed {closed} test tab(s) group={ui_group_id}")


async def _run_team_smoke(app: Any, args: argparse.Namespace) -> int:
    ui_group_id = str(getattr(args, "ui_group_id", "") or "").strip()
    boss = await _find_mesh_pane(app, args.repo, args.boss_role, ui_group_id)
    president = await _find_mesh_pane(app, args.repo, args.president_role, ui_group_id)
    worker = await _find_mesh_pane(app, args.repo, args.worker_role, ui_group_id)

    run_id = str(args.run_id or uuid.uuid4().hex[:8]).upper().replace("-", "_")
    boss_task = f"BOSS_TASK_{run_id}"
    president_to_worker = f"PRESIDENT_TO_WORKER_{run_id}"
    worker_result = f"WORKER_RESULT_{run_id}"
    president_ack = f"PRESIDENT_SAW_WORKER_{run_id}"
    boss_done = f"BOSS_SAW_PRESIDENT_{run_id}"

    print(
        f"panes: boss=W{boss.window_index} T{boss.tab_index} S{boss.session_index} "
        f"president=W{president.window_index} T{president.tab_index} S{president.session_index} "
        f"worker=W{worker.window_index} T{worker.tab_index} S{worker.session_index}"
    )
    print(f"run_id: {run_id}")

    print("1. Boss emits a task marker")
    await _send_line(
        boss.session,
        f'Rispondi solo con la concatenazione esatta di "BOSS_TASK_" e "{run_id}".',
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=boss_task,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("2. President receives boss task and emits worker handoff")
    await _send_line(
        president.session,
        (
            f"Task ricevuto da boss: {boss_task}. "
            f'Rispondi solo con la concatenazione esatta di "PRESIDENT_TO_WORKER_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=president_to_worker,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("3. Worker receives president handoff and emits result")
    await _send_line(
        worker.session,
        (
            f"Task ricevuto da president: {president_to_worker}. "
            f'Rispondi solo con la concatenazione esatta di "WORKER_RESULT_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        worker.session,
        role=args.worker_role,
        marker=worker_result,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("4. President receives worker result and acknowledges")
    await _send_line(
        president.session,
        (
            f"Risultato ricevuto da worker: {worker_result}. "
            f'Rispondi solo con la concatenazione esatta di "PRESIDENT_SAW_WORKER_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=president_ack,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("5. Boss receives president acknowledgement and closes loop")
    await _send_line(
        boss.session,
        (
            f"Ack ricevuto da president: {president_ack}. "
            f'Rispondi solo con la concatenazione esatta di "BOSS_SAW_PRESIDENT_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=boss_done,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("success:")
    print(f"  {boss_task}")
    print(f"  {president_to_worker}")
    print(f"  {worker_result}")
    print(f"  {president_ack}")
    print(f"  {boss_done}")
    return 0


async def _run_team_e2e(connection: Any, app: Any, args: argparse.Namespace) -> int:
    _ensure_command(args.boss_cmd)
    _ensure_command(args.president_cmd)
    _ensure_command(args.worker_cmd)

    repo = str(args.repo or "").strip()
    repo_name = _repo_name(repo)
    ui_group_id = str(args.ui_group_id or f"{repo_name}-team-{uuid.uuid4().hex[:8]}").strip()
    roles = [args.boss_role, args.president_role, args.worker_role]
    commands = {
        args.boss_role: args.boss_cmd,
        args.president_role: args.president_cmd,
        args.worker_role: args.worker_cmd,
    }
    print(f"opening team test layout group={ui_group_id}")
    try:
        await _launch_role_layout(connection, repo=repo, roles=roles, commands=commands, ui_group_id=ui_group_id)
        await asyncio.sleep(max(0.0, float(args.startup_wait)))
        panes = {
            role: await _find_mesh_pane(app, repo, role, ui_group_id)
            for role in roles
        }
        print("waiting for CLI prompts")
        for role, command in commands.items():
            await _wait_for_cli_ready(
                panes[role].session,
                role=role,
                command_text=command,
                timeout=args.startup_timeout,
                poll_interval=args.poll_interval,
            )
        smoke_args = argparse.Namespace(
            repo=repo,
            ui_group_id=ui_group_id,
            boss_role=args.boss_role,
            president_role=args.president_role,
            worker_role=args.worker_role,
            run_id="",
            response_timeout=args.response_timeout,
            poll_interval=args.poll_interval,
        )
        return await _run_team_smoke(app, smoke_args)
    finally:
        if not args.keep_open:
            closed = await _close_mesh_tabs(app, repo, ui_group_id)
            print(f"closed {closed} test tab(s) group={ui_group_id}")


async def _run_speckit_team_smoke(app: Any, args: argparse.Namespace) -> int:
    ui_group_id = str(getattr(args, "ui_group_id", "") or "").strip()
    boss = await _find_mesh_pane(app, args.repo, args.boss_role, ui_group_id)
    president = await _find_mesh_pane(app, args.repo, args.president_role, ui_group_id)
    worker = await _find_mesh_pane(app, args.repo, args.worker_role, ui_group_id)

    run_id = str(args.run_id or uuid.uuid4().hex[:8]).upper().replace("-", "_")
    feature = str(getattr(args, "feature", "") or "feature").strip()
    discuss = f"SPECKIT_DISCUSS_TO_PRESIDENT_{run_id}"
    analyze = f"SPECKIT_ANALYZE_TO_WORKER_{run_id}"
    implement = f"SPECKIT_IMPLEMENT_RESULT_{run_id}"
    adjudicate = f"SPECKIT_PRESIDENT_READY_{run_id}"
    done = f"SPECKIT_BOSS_DONE_{run_id}"

    print(
        f"panes: boss=W{boss.window_index} T{boss.tab_index} S{boss.session_index} "
        f"president=W{president.window_index} T{president.tab_index} S{president.session_index} "
        f"worker=W{worker.window_index} T{worker.tab_index} S{worker.session_index}"
    )
    print(f"feature: {feature}")
    print(f"run_id: {run_id}")

    print("1. Boss maps /speckit.discuss to president handoff")
    await _send_line(
        boss.session,
        (
            "Dry-run routing smoke only: do not inspect files and do not edit files. "
            f"For feature '{feature}', treat this as /speckit.discuss. "
            "Your role is boss. If the next role should be president for analysis coordination, "
            f'rispondi solo con la concatenazione esatta di "SPECKIT_DISCUSS_TO_PRESIDENT_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=discuss,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("2. President maps speckit.analyze to worker handoff")
    await _send_line(
        president.session,
        (
            "Dry-run routing smoke only: do not inspect files and do not edit files. "
            f"Boss handoff received for feature '{feature}': {discuss}. "
            "Your role is president. Treat this as speckit.analyze and hand work to worker-gemini. "
            f'Rispondi solo con la concatenazione esatta di "SPECKIT_ANALYZE_TO_WORKER_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=analyze,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("3. Worker maps speckit.implement to implementation result")
    await _send_line(
        worker.session,
        (
            "Dry-run routing smoke only: do not inspect files and do not edit files. "
            f"President handoff received for feature '{feature}': {analyze}. "
            "Your role is worker-gemini. Treat this as speckit.implement dry-run execution. "
            f'Rispondi solo con la concatenazione esatta di "SPECKIT_IMPLEMENT_RESULT_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        worker.session,
        role=args.worker_role,
        marker=implement,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("4. President adjudicates worker result")
    await _send_line(
        president.session,
        (
            "Dry-run routing smoke only. "
            f"Worker result received for feature '{feature}': {implement}. "
            "Your role is president. Treat this as Speckit readiness adjudication. "
            f'Rispondi solo con la concatenazione esatta di "SPECKIT_PRESIDENT_READY_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        president.session,
        role=args.president_role,
        marker=adjudicate,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("5. Boss closes Speckit routing loop")
    await _send_line(
        boss.session,
        (
            "Dry-run routing smoke only. "
            f"President readiness received for feature '{feature}': {adjudicate}. "
            "Your role is boss. Close the Speckit routing loop. "
            f'Rispondi solo con la concatenazione esatta di "SPECKIT_BOSS_DONE_" e "{run_id}".'
        ),
    )
    await _wait_for_screen_marker(
        boss.session,
        role=args.boss_role,
        marker=done,
        timeout=args.response_timeout,
        poll_interval=args.poll_interval,
    )

    print("success:")
    print(f"  {discuss}")
    print(f"  {analyze}")
    print(f"  {implement}")
    print(f"  {adjudicate}")
    print(f"  {done}")
    return 0


async def _run_speckit_team_e2e(connection: Any, app: Any, args: argparse.Namespace) -> int:
    _ensure_command(args.boss_cmd)
    _ensure_command(args.president_cmd)
    _ensure_command(args.worker_cmd)

    repo = str(args.repo or "").strip()
    repo_name = _repo_name(repo)
    ui_group_id = str(args.ui_group_id or f"{repo_name}-speckit-{uuid.uuid4().hex[:8]}").strip()
    roles = [args.boss_role, args.president_role, args.worker_role]
    commands = {
        args.boss_role: args.boss_cmd,
        args.president_role: args.president_cmd,
        args.worker_role: args.worker_cmd,
    }
    print(f"opening speckit team test layout group={ui_group_id}")
    try:
        await _launch_role_layout(connection, repo=repo, roles=roles, commands=commands, ui_group_id=ui_group_id)
        await asyncio.sleep(max(0.0, float(args.startup_wait)))
        panes = {
            role: await _find_mesh_pane(app, repo, role, ui_group_id)
            for role in roles
        }
        print("waiting for CLI prompts")
        for role, command in commands.items():
            await _wait_for_cli_ready(
                panes[role].session,
                role=role,
                command_text=command,
                timeout=args.startup_timeout,
                poll_interval=args.poll_interval,
            )
        smoke_args = argparse.Namespace(
            repo=repo,
            ui_group_id=ui_group_id,
            boss_role=args.boss_role,
            president_role=args.president_role,
            worker_role=args.worker_role,
            feature=args.feature,
            run_id="",
            response_timeout=args.response_timeout,
            poll_interval=args.poll_interval,
        )
        return await _run_speckit_team_smoke(app, smoke_args)
    finally:
        if not args.keep_open:
            closed = await _close_mesh_tabs(app, repo, ui_group_id)
            print(f"closed {closed} test tab(s) group={ui_group_id}")


async def _run_speckit_team_cycle(app: Any, args: argparse.Namespace) -> int:
    ui_group_id = str(getattr(args, "ui_group_id", "") or "").strip()
    boss = await _find_mesh_pane(app, args.repo, args.boss_role, ui_group_id)
    president = await _find_mesh_pane(app, args.repo, args.president_role, ui_group_id)
    worker = await _find_mesh_pane(app, args.repo, args.worker_role, ui_group_id)
    reviewer_enabled = bool(getattr(args, "with_reviewer", False))
    reviewer = (
        await _find_mesh_pane(app, args.repo, args.reviewer_role, ui_group_id)
        if reviewer_enabled
        else None
    )

    run_id = str(args.run_id or uuid.uuid4().hex[:8]).upper().replace("-", "_")
    feature = _clean_one_line(args.feature)
    task = _clean_one_line(args.task) or feature
    turn_limit = _turn_limit_text(args.max_turns)
    write_allowed = "true" if bool(args.allow_write) else "false"
    auto_approve_prompts = bool(getattr(args, "auto_approve_prompts", False))
    operator_allowed_edit_paths = tuple(
        _normalize_edit_path(str(item)) for item in (getattr(args, "auto_approve_edit_path", None) or [])
    )
    effective_allowed_edit_paths = operator_allowed_edit_paths
    claude_config_resolution = getattr(args, "claude_config_resolution", None)
    if not isinstance(claude_config_resolution, ClaudeConfigResolution):
        claude_config_resolution = _resolve_claude_config_root(str(getattr(args, "claude_config", "") or ""))
    role_contract_names = _role_contract_names(args)
    role_contract_contexts = _role_contract_contexts(claude_config_resolution, role_contract_names)
    handoff_enabled = not bool(getattr(args, "no_handoff", False))
    handoff_dir = str(getattr(args, "handoff_dir", ".mesh/runs") or ".mesh/runs")
    quality_mode = str(getattr(args, "quality", "off") or "off").strip()
    product_quality_enabled = bool(getattr(args, "product_quality", False))
    min_product_score = max(0, min(10, int(getattr(args, "min_product_score", 7) or 7)))
    max_quality_retries = max(0, int(getattr(args, "max_quality_retries", 0) or 0))
    product_quality_status = "off"
    product_review_payload: dict[str, object] = {}
    product_review = ProductReview(parsed=False)
    product_retry_count = 0
    product_retry_markers: list[str] = []

    boss_delegated = f"GBG_BOSS_{run_id}"
    president_assigned = f"GBG_PRES_{run_id}"
    worker_done = f"GBG_WORKER_{run_id}"
    president_reviewed = f"GBG_VERIFY_{run_id}"
    reviewer_done = f"GBG_REVIEW_{run_id}"
    boss_reported = f"GBG_REPORT_{run_id}"
    handoff_files = {
        "operator": "00-operator.json",
        "discuss": "01-discuss.json",
        "analyze": "02-analyze.json",
        "implement": "03-implement.json",
        "verify": "04-verify.json",
        "reviewer": "05-reviewer.json",
        "report": "06-report.json" if reviewer_enabled else "05-report.json",
    }

    print(
        f"panes: boss=W{boss.window_index} T{boss.tab_index} S{boss.session_index} "
        f"president=W{president.window_index} T{president.tab_index} S{president.session_index} "
        f"worker=W{worker.window_index} T{worker.tab_index} S{worker.session_index}"
        + (
            f" reviewer=W{reviewer.window_index} T{reviewer.tab_index} S{reviewer.session_index}"
            if reviewer is not None
            else ""
        )
    )
    print(f"feature: {feature}")
    print(f"task: {task}")
    print(f"run_id: {run_id}")
    print(f"write_allowed: {write_allowed}")
    if operator_allowed_edit_paths:
        print(f"operator_allowed_edit_paths: {', '.join(operator_allowed_edit_paths)}")
    if claude_config_resolution.available:
        print(f"claude_config: {claude_config_resolution.root} ({claude_config_resolution.source})")
    print(f"quality: {quality_mode}")
    if product_quality_enabled:
        print(f"product_quality: min_score={min_product_score} max_retries={max_quality_retries}")
    if handoff_enabled:
        print(f"handoff_dir: {_repo_relative_path(args.repo, _handoff_run_dir(args.repo, handoff_dir, run_id))}")

    operator_handoff = _write_handoff_json(
        args.repo,
        handoff_dir,
        run_id,
        handoff_files["operator"],
        {
            "phase": "speckit.request",
            "from_role": "operator",
            "to_role": args.boss_role,
            "feature": feature,
            "task": task,
            "write_allowed": bool(args.allow_write),
            "operator_allowed_edit_paths": list(operator_allowed_edit_paths),
            "claude_config": _claude_config_payload(claude_config_resolution, include_contracts=True),
            "contract_names": role_contract_names,
            "test_command": args.test_command,
            "quality": quality_mode,
            "product_quality": {
                "enabled": product_quality_enabled,
                "min_score": min_product_score,
                "max_retries": max_quality_retries,
            },
            "max_turns": max(1, int(args.max_turns or 1)),
            "roles": {
                "boss": args.boss_role,
                "president": args.president_role,
                "worker": args.worker_role,
                "reviewer": args.reviewer_role if reviewer_enabled else "",
            },
            "next_handoff": str(Path(handoff_dir) / run_id / handoff_files["discuss"]),
        },
        enabled=handoff_enabled,
    )

    operator_msg = _format_mesh_msg(
        id=f"operator-{run_id}",
        from_role="operator",
        to_role=args.boss_role,
        phase="speckit.discuss",
        feature=feature,
        task=task,
        write_allowed=write_allowed,
        handoff_in=operator_handoff,
        handoff_out=str(Path(handoff_dir) / run_id / handoff_files["discuss"]) if handoff_enabled else "",
        done_criteria="one controlled Speckit cycle; no commit",
    )

    async def _send_role_prompt(
        session: Any,
        *,
        role: str,
        phase: str,
        marker: str,
        text: str,
        allowed_paths: tuple[str, ...],
        response_mode: str = "marker_only",
    ) -> dict[str, str]:
        role_commands = dict(getattr(args, "role_commands", {}) or {})
        if _command_name(str(role_commands.get(role, "") or "")) == "gemini":
            await _wait_for_gemini_ready(
                session,
                role=role,
                timeout=min(max(6.0, float(args.poll_interval) * 4.0), max(6.0, float(args.response_timeout) / 2.0)),
                poll_interval=args.poll_interval,
                auto_approve_prompts=auto_approve_prompts,
                allowed_edit_paths=allowed_paths,
            )
        delivery = _delivery_tokens(run_id, phase, role)
        if response_mode == "product_review":
            start = delivery["start"]
            end = delivery["end"]
            prompt = (
                f"Reply exactly 4 lines: line1 DELIVERY_ACK {start} {end}; "
                "line2 PRODUCT_REVIEW status=pass|retry score=0..10 visual=0..10 "
                "interaction=0..10 clarity=0..10 technical=0..10; "
                "line3 FEEDBACK: concise actionable feedback; "
                f"line4 {marker}. {text}"
            )
        else:
            prompt = _delivery_prompt(text, delivery, marker)
        await _send_line(session, prompt)
        send_tail = await _screen_tail(session, lines=120)
        if _gemini_screen_has_queued_prompt(send_tail):
            print(f"queued prompt detected for {role}; resuming queued Gemini composer once")
            await _maybe_resume_gemini_queued_prompt(session, send_tail, role=role, seen=set(), attempt_index=1)
        await _wait_for_screen_marker(
            session,
            role=role,
            marker=marker,
            timeout=args.response_timeout,
            poll_interval=args.poll_interval,
            auto_approve_prompts=auto_approve_prompts,
            allowed_edit_paths=allowed_paths,
            delivery_ack=delivery,
        )
        return delivery

    print("1. Boss discusses and delegates to president")
    boss_delivery = await _send_role_prompt(
        boss.session,
        role=args.boss_role,
        phase="speckit.discuss",
        marker=boss_delegated,
        allowed_paths=_edit_prompt_allowed_paths_for_role(args.boss_role, args.worker_role, effective_allowed_edit_paths),
        text=(
            "Role boss. Delegate to president. No edits, tools, nested CLI, or questions."
        ),
    )
    boss_tail = await _screen_tail(boss.session, lines=120)
    _enforce_role_output_policy(
        role=args.boss_role,
        role_class="non_worker",
        phase="speckit.discuss",
        text=boss_tail,
        repo=args.repo,
        handoff_dir=handoff_dir,
        run_id=run_id,
        write_sidecar=handoff_enabled,
    )
    discuss_handoff = _write_handoff_json(
        args.repo,
        handoff_dir,
        run_id,
        handoff_files["discuss"],
        {
            "phase": "speckit.discuss",
            "from_role": args.boss_role,
            "to_role": args.president_role,
            "feature": feature,
            "task": task,
            "marker": boss_delegated,
            "delivery": boss_delivery,
            "handoff_in": operator_handoff,
            "next_handoff": str(Path(handoff_dir) / run_id / handoff_files["analyze"]),
            "screen_tail": boss_tail,
        },
        enabled=handoff_enabled,
    )

    president_msg = _format_mesh_msg(
        id=f"boss-{run_id}",
        from_role=args.boss_role,
        to_role=args.president_role,
        phase="speckit.analyze",
        feature=feature,
        task=task,
        write_allowed=write_allowed,
        upstream_marker=boss_delegated,
        handoff_in=discuss_handoff,
        handoff_out=str(Path(handoff_dir) / run_id / handoff_files["analyze"]) if handoff_enabled else "",
        done_criteria="assign exactly one worker task",
    )

    print("2. President analyzes and assigns worker")
    president_delivery = await _send_role_prompt(
        president.session,
        role=args.president_role,
        phase="speckit.analyze",
        marker=president_assigned,
        allowed_paths=_edit_prompt_allowed_paths_for_role(args.president_role, args.worker_role, effective_allowed_edit_paths),
        text=(
            "Role president. Assign worker one task. Include ALLOWED_EDIT_PATHS: ANY. No edits/tools/questions."
        ),
    )
    president_tail = await _screen_tail(president.session, lines=120)
    _enforce_role_output_policy(
        role=args.president_role,
        role_class="non_worker",
        phase="speckit.analyze",
        text=president_tail,
        repo=args.repo,
        handoff_dir=handoff_dir,
        run_id=run_id,
        write_sidecar=handoff_enabled,
    )
    president_allowed_edit_paths = _parse_allowed_edit_paths(president_tail)
    effective_allowed_edit_paths = _effective_edit_allowlist(
        operator_allowed_edit_paths,
        president_allowed_edit_paths,
    )
    if president_allowed_edit_paths:
        print(f"president_allowed_edit_paths: {', '.join(president_allowed_edit_paths)}")
    if effective_allowed_edit_paths:
        print(f"effective_allowed_edit_paths: {', '.join(effective_allowed_edit_paths)}")
    analyze_handoff = _write_handoff_json(
        args.repo,
        handoff_dir,
        run_id,
        handoff_files["analyze"],
        {
            "phase": "speckit.analyze",
            "from_role": args.president_role,
            "to_role": args.worker_role,
            "feature": feature,
            "task": task,
            "marker": president_assigned,
            "delivery": president_delivery,
            "upstream_marker": boss_delegated,
            "handoff_in": discuss_handoff,
            "next_handoff": str(Path(handoff_dir) / run_id / handoff_files["implement"]),
            "president_allowed_edit_paths": list(president_allowed_edit_paths),
            "effective_allowed_edit_paths": list(effective_allowed_edit_paths),
            "screen_tail": president_tail,
        },
        enabled=handoff_enabled,
    )

    worker_msg = _format_mesh_msg(
        id=f"president-{run_id}",
        from_role=args.president_role,
        to_role=args.worker_role,
        phase="speckit.implement",
        feature=feature,
        task=task,
        write_allowed=write_allowed,
        upstream_marker=president_assigned,
        handoff_in=analyze_handoff,
        handoff_out=str(Path(handoff_dir) / run_id / handoff_files["implement"]) if handoff_enabled else "",
        allowed_edit_paths=", ".join(effective_allowed_edit_paths) if effective_allowed_edit_paths else "unrestricted",
        done_criteria="single implementation pass; summarize files/tests/risks; no commit",
    )
    write_policy = (
        "File edits are allowed for this one task. Do not commit."
        if args.allow_write
        else "Do not edit files. Produce an implementation plan and risk note only."
    )

    print("3. Worker executes one bounded implementation pass")
    worker_delivery = await _send_role_prompt(
        worker.session,
        role=args.worker_role,
        phase="speckit.implement",
        marker=worker_done,
        allowed_paths=_edit_prompt_allowed_paths_for_role(args.worker_role, args.worker_role, effective_allowed_edit_paths),
        text=_worker_prompt_text(
            task=task,
            allow_write=bool(args.allow_write),
            allowed_edit_paths=effective_allowed_edit_paths,
        ),
    )

    status_after_worker = _git_status_short(args.repo)
    diff_stat_after_worker = _git_diff_stat(args.repo)
    test_status, test_output = _run_optional_test_command(args.repo, args.test_command, args.test_timeout)
    worker_tail = await _screen_tail(worker.session, lines=120)
    worker_policy_findings = _enforce_role_output_policy(
        role=args.worker_role,
        role_class="worker",
        phase="speckit.implement",
        text=worker_tail,
        repo=args.repo,
        handoff_dir=handoff_dir,
        run_id=run_id,
        write_sidecar=handoff_enabled,
    )
    quality_quick: dict[str, object] = {}
    quality_quick_path = ""
    quality_status = "off"
    if quality_mode == "quick":
        quality_quick = _quality_quick_payload(
            run_id=run_id,
            git_status=status_after_worker,
            diff_stat=diff_stat_after_worker,
            test_status=test_status,
            allow_test_failure=bool(getattr(args, "allow_test_failure", False)),
            operator_allowed_edit_paths=operator_allowed_edit_paths,
            president_allowed_edit_paths=president_allowed_edit_paths,
            effective_allowed_edit_paths=effective_allowed_edit_paths,
            policy_findings=worker_policy_findings,
        )
        quality_status = str(quality_quick.get("status") or "failed")
        quality_quick_path = _write_quality_quick_json(
            args.repo,
            handoff_dir,
            run_id,
            quality_quick,
            enabled=handoff_enabled,
        )
    implement_handoff = _write_handoff_json(
        args.repo,
        handoff_dir,
        run_id,
        handoff_files["implement"],
        {
            "phase": "speckit.implement",
            "from_role": args.worker_role,
            "to_role": args.president_role,
            "feature": feature,
            "task": task,
            "write_allowed": bool(args.allow_write),
            "allowed_edit_paths": list(effective_allowed_edit_paths),
            "marker": worker_done,
            "delivery": worker_delivery,
            "upstream_marker": president_assigned,
            "handoff_in": analyze_handoff,
            "next_handoff": str(Path(handoff_dir) / run_id / handoff_files["verify"]),
            "git_status": status_after_worker or "clean",
            "diff_stat": diff_stat_after_worker or "empty",
            "test_status": test_status,
            "test_output_tail": test_output,
            "quality_mode": quality_mode,
            "quality_status": quality_status,
            "quality_quick": quality_quick_path,
            "screen_tail": worker_tail,
        },
        enabled=handoff_enabled,
    )

    review_msg = _format_mesh_msg(
        id=f"worker-{run_id}",
        from_role=args.worker_role,
        to_role=args.president_role,
        phase="speckit.verify-work",
        feature=feature,
        task=task,
        write_allowed=write_allowed,
        upstream_marker=worker_done,
        git_status=status_after_worker or "clean",
        diff_stat=diff_stat_after_worker or "empty",
        test_status=test_status,
        quality_status=quality_status,
        quality_quick=quality_quick_path or "none",
        policy_status="clean",
        policy_violations="none",
        handoff_in=implement_handoff,
        handoff_out=str(Path(handoff_dir) / run_id / handoff_files["verify"]) if handoff_enabled else "",
        done_criteria="adjudicate ready_or_blocked",
    )

    print("4. President reviews worker result")
    president_review_delivery = await _send_role_prompt(
        president.session,
        role=args.president_role,
        phase="speckit.verify-work",
        marker=president_reviewed,
        allowed_paths=_edit_prompt_allowed_paths_for_role(args.president_role, args.worker_role, effective_allowed_edit_paths),
        text=(
            f"Role president-review. Tests {test_status}; quality {quality_status}. Say ready_or_blocked. No edits/tools/questions."
        ),
    )
    review_tail = await _screen_tail(president.session, lines=120)
    _enforce_role_output_policy(
        role=args.president_role,
        role_class="non_worker",
        phase="speckit.verify-work",
        text=review_tail,
        repo=args.repo,
        handoff_dir=handoff_dir,
        run_id=run_id,
        write_sidecar=handoff_enabled,
    )
    verify_handoff = _write_handoff_json(
        args.repo,
        handoff_dir,
        run_id,
        handoff_files["verify"],
        {
            "phase": "speckit.verify-work",
            "from_role": args.president_role,
            "to_role": args.boss_role,
            "feature": feature,
            "task": task,
            "marker": president_reviewed,
            "delivery": president_review_delivery,
            "upstream_marker": worker_done,
            "handoff_in": implement_handoff,
            "next_handoff": str(Path(handoff_dir) / run_id / handoff_files["report"]),
            "git_status": status_after_worker or "clean",
            "diff_stat": diff_stat_after_worker or "empty",
            "test_status": test_status,
            "screen_tail": review_tail,
        },
        enabled=handoff_enabled,
    )

    upstream_for_final = president_reviewed
    handoff_for_final = verify_handoff
    if reviewer is not None:
        reviewer_msg = _format_mesh_msg(
            id=f"president-reviewer-{run_id}",
            from_role=args.president_role,
            to_role=args.reviewer_role,
            phase="speckit.review",
            feature=feature,
            task=task,
            write_allowed=write_allowed,
            upstream_marker=president_reviewed,
            handoff_in=verify_handoff,
            handoff_out=str(Path(handoff_dir) / run_id / handoff_files["reviewer"]) if handoff_enabled else "",
            git_status=status_after_worker or "clean",
            diff_stat=diff_stat_after_worker or "empty",
            test_status=test_status,
            quality_status=quality_status,
            quality_quick=quality_quick_path or "none",
            policy_status="clean",
            policy_violations="none",
            allowed_edit_paths=", ".join(effective_allowed_edit_paths) if effective_allowed_edit_paths else "unrestricted",
            done_criteria="independent reviewer verdict on work and controller policy",
        )

        print("5. Reviewer validates work and controller policy")
        reviewer_prompt = (
            _reviewer_product_prompt_text(
                test_status=test_status,
                quality_status=quality_status,
                min_score=min_product_score,
                artifact_paths=effective_allowed_edit_paths,
            )
            if product_quality_enabled
            else f"Role reviewer. Tests {test_status}; quality {quality_status}. Validate ready_or_blocked. No edits/tools/questions."
        )
        reviewer_delivery = await _send_role_prompt(
            reviewer.session,
            role=args.reviewer_role,
            phase="speckit.review",
            marker=reviewer_done,
            allowed_paths=_edit_prompt_allowed_paths_for_role(
                args.reviewer_role,
                args.worker_role,
                effective_allowed_edit_paths,
            ),
            text=reviewer_prompt,
            response_mode="product_review" if product_quality_enabled else "marker_only",
        )
        reviewer_tail = await _screen_tail(reviewer.session, lines=120)
        _enforce_role_output_policy(
            role=args.reviewer_role,
            role_class="non_worker",
            phase="speckit.review",
            text=reviewer_tail,
            repo=args.repo,
            handoff_dir=handoff_dir,
            run_id=run_id,
            write_sidecar=handoff_enabled,
        )
        if product_quality_enabled:
            product_review = _parse_product_review(reviewer_tail)
            product_review_payload = _product_review_payload(
                product_review,
                min_score=min_product_score,
                retry_count=product_retry_count,
            )
            product_quality_status = (
                "passed" if _product_review_passed(product_review, min_score=min_product_score) else "retry"
            )
        handoff_for_final = _write_handoff_json(
            args.repo,
            handoff_dir,
            run_id,
            handoff_files["reviewer"],
            {
                "phase": "speckit.review",
                "from_role": args.reviewer_role,
                "to_role": args.boss_role,
                "feature": feature,
                "task": task,
                "marker": reviewer_done,
                "delivery": reviewer_delivery,
                "upstream_marker": president_reviewed,
                "handoff_in": verify_handoff,
                "next_handoff": str(Path(handoff_dir) / run_id / handoff_files["report"]),
                "git_status": status_after_worker or "clean",
                "diff_stat": diff_stat_after_worker or "empty",
                "test_status": test_status,
                "quality_status": quality_status,
                "quality_quick": quality_quick_path,
                "product_quality_status": product_quality_status,
                "product_review": product_review_payload,
                "allowed_edit_paths": list(effective_allowed_edit_paths),
                "screen_tail": reviewer_tail,
            },
            enabled=handoff_enabled,
        )
        upstream_for_final = reviewer_done

    while (
        reviewer is not None
        and product_quality_enabled
        and not _product_review_passed(product_review, min_score=min_product_score)
        and product_retry_count < max_quality_retries
    ):
        product_retry_count += 1
        retry_worker_done = f"GBG_RW{product_retry_count}_{run_id}"
        retry_reviewer_done = f"GBG_RV{product_retry_count}_{run_id}"
        product_retry_markers.extend([retry_worker_done, retry_reviewer_done])

        print(f"6. Product quality retry {product_retry_count}/{max_quality_retries}")
        retry_worker_delivery = await _send_role_prompt(
            worker.session,
            role=args.worker_role,
            phase=f"speckit.implement-product-retry-{product_retry_count}",
            marker=retry_worker_done,
            allowed_paths=_edit_prompt_allowed_paths_for_role(
                args.worker_role,
                args.worker_role,
                effective_allowed_edit_paths,
            ),
            text=_product_retry_worker_prompt_text(
                task=task,
                feedback=product_review.feedback,
                allowed_edit_paths=effective_allowed_edit_paths,
            ),
        )

        status_after_worker = _git_status_short(args.repo)
        diff_stat_after_worker = _git_diff_stat(args.repo)
        test_status, test_output = _run_optional_test_command(args.repo, args.test_command, args.test_timeout)
        worker_tail = await _screen_tail(worker.session, lines=120)
        worker_policy_findings = _enforce_role_output_policy(
            role=args.worker_role,
            role_class="worker",
            phase=f"speckit.implement-product-retry-{product_retry_count}",
            text=worker_tail,
            repo=args.repo,
            handoff_dir=handoff_dir,
            run_id=run_id,
            write_sidecar=handoff_enabled,
        )
        if quality_mode == "quick":
            quality_quick = _quality_quick_payload(
                run_id=run_id,
                git_status=status_after_worker,
                diff_stat=diff_stat_after_worker,
                test_status=test_status,
                allow_test_failure=bool(getattr(args, "allow_test_failure", False)),
                operator_allowed_edit_paths=operator_allowed_edit_paths,
                president_allowed_edit_paths=president_allowed_edit_paths,
                effective_allowed_edit_paths=effective_allowed_edit_paths,
                policy_findings=worker_policy_findings,
            )
            quality_status = str(quality_quick.get("status") or "failed")
            quality_quick_path = _write_quality_quick_json(
                args.repo,
                handoff_dir,
                run_id,
                quality_quick,
                enabled=handoff_enabled,
            )

        implement_handoff = _write_handoff_json(
            args.repo,
            handoff_dir,
            run_id,
            f"03-implement-product-retry-{product_retry_count}.json",
            {
                "phase": f"speckit.implement-product-retry-{product_retry_count}",
                "from_role": args.worker_role,
                "to_role": args.reviewer_role,
                "feature": feature,
                "task": task,
                "write_allowed": bool(args.allow_write),
                "allowed_edit_paths": list(effective_allowed_edit_paths),
                "marker": retry_worker_done,
                "delivery": retry_worker_delivery,
                "upstream_marker": upstream_for_final,
                "handoff_in": handoff_for_final,
                "git_status": status_after_worker or "clean",
                "diff_stat": diff_stat_after_worker or "empty",
                "test_status": test_status,
                "test_output_tail": test_output,
                "quality_mode": quality_mode,
                "quality_status": quality_status,
                "quality_quick": quality_quick_path,
                "product_retry_count": product_retry_count,
                "product_review_previous": product_review_payload,
                "screen_tail": worker_tail,
            },
            enabled=handoff_enabled,
        )

        print(f"7. Reviewer validates product retry {product_retry_count}/{max_quality_retries}")
        retry_reviewer_delivery = await _send_role_prompt(
            reviewer.session,
            role=args.reviewer_role,
            phase=f"speckit.product-review-retry-{product_retry_count}",
            marker=retry_reviewer_done,
            allowed_paths=_edit_prompt_allowed_paths_for_role(
                args.reviewer_role,
                args.worker_role,
                effective_allowed_edit_paths,
            ),
            text=_reviewer_product_prompt_text(
                test_status=test_status,
                quality_status=quality_status,
                min_score=min_product_score,
                artifact_paths=effective_allowed_edit_paths,
            ),
            response_mode="product_review",
        )
        reviewer_tail = await _screen_tail(reviewer.session, lines=120)
        _enforce_role_output_policy(
            role=args.reviewer_role,
            role_class="non_worker",
            phase=f"speckit.product-review-retry-{product_retry_count}",
            text=reviewer_tail,
            repo=args.repo,
            handoff_dir=handoff_dir,
            run_id=run_id,
            write_sidecar=handoff_enabled,
        )
        product_review = _parse_product_review(reviewer_tail)
        product_review_payload = _product_review_payload(
            product_review,
            min_score=min_product_score,
            retry_count=product_retry_count,
        )
        product_quality_status = (
            "passed" if _product_review_passed(product_review, min_score=min_product_score) else "retry"
        )
        handoff_for_final = _write_handoff_json(
            args.repo,
            handoff_dir,
            run_id,
            f"05-reviewer-product-retry-{product_retry_count}.json",
            {
                "phase": f"speckit.product-review-retry-{product_retry_count}",
                "from_role": args.reviewer_role,
                "to_role": args.boss_role,
                "feature": feature,
                "task": task,
                "marker": retry_reviewer_done,
                "delivery": retry_reviewer_delivery,
                "upstream_marker": retry_worker_done,
                "handoff_in": implement_handoff,
                "next_handoff": str(Path(handoff_dir) / run_id / handoff_files["report"]),
                "git_status": status_after_worker or "clean",
                "diff_stat": diff_stat_after_worker or "empty",
                "test_status": test_status,
                "quality_status": quality_status,
                "quality_quick": quality_quick_path,
                "product_quality_status": product_quality_status,
                "product_review": product_review_payload,
                "allowed_edit_paths": list(effective_allowed_edit_paths),
                "screen_tail": reviewer_tail,
            },
            enabled=handoff_enabled,
        )
        upstream_for_final = retry_reviewer_done

    if product_quality_enabled:
        product_quality_status = (
            "passed" if _product_review_passed(product_review, min_score=min_product_score) else "failed"
        )

    final_msg = _format_mesh_msg(
        id=f"final-review-{run_id}",
        from_role=args.reviewer_role if reviewer is not None else args.president_role,
        to_role=args.boss_role,
        phase="speckit.report",
        feature=feature,
        task=task,
        write_allowed=write_allowed,
        upstream_marker=upstream_for_final,
        git_status=status_after_worker or "clean",
        diff_stat=diff_stat_after_worker or "empty",
        test_status=test_status,
        quality_status=quality_status,
        quality_quick=quality_quick_path or "none",
        product_quality_status=product_quality_status,
        product_score=product_review.score if product_quality_enabled else "none",
        product_retry_count=product_retry_count,
        policy_status="clean",
        policy_violations="none",
        handoff_in=handoff_for_final,
        handoff_out=str(Path(handoff_dir) / run_id / handoff_files["report"]) if handoff_enabled else "",
        done_criteria="operator-facing summary",
    )

    print(f"{'6' if reviewer is not None else '5'}. Boss reports final controlled-cycle status")
    boss_report_delivery = await _send_role_prompt(
        boss.session,
        role=args.boss_role,
        phase="speckit.report",
        marker=boss_reported,
        allowed_paths=_edit_prompt_allowed_paths_for_role(args.boss_role, args.worker_role, effective_allowed_edit_paths),
        text=(
            f"Role boss-final. Tests {test_status}; quality {quality_status}; "
            f"product_quality {product_quality_status}. Summarize. No commit claim, edits/tools/questions."
        ),
    )
    report_tail = await _screen_tail(boss.session, lines=120)
    _enforce_role_output_policy(
        role=args.boss_role,
        role_class="non_worker",
        phase="speckit.report",
        text=report_tail,
        repo=args.repo,
        handoff_dir=handoff_dir,
        run_id=run_id,
        write_sidecar=handoff_enabled,
    )
    report_handoff = _write_handoff_json(
        args.repo,
        handoff_dir,
        run_id,
        handoff_files["report"],
        {
            "phase": "speckit.report",
            "from_role": args.boss_role,
            "to_role": "operator",
            "feature": feature,
            "task": task,
            "marker": boss_reported,
            "delivery": boss_report_delivery,
            "upstream_marker": upstream_for_final,
            "handoff_in": handoff_for_final,
            "git_status": status_after_worker or "clean",
            "diff_stat": diff_stat_after_worker or "empty",
            "test_status": test_status,
            "quality_status": quality_status,
            "quality_quick": quality_quick_path,
            "product_quality_status": product_quality_status,
            "product_review": product_review_payload,
            "product_retry_count": product_retry_count,
            "screen_tail": report_tail,
            **_supervisor_outcome_fields(status="passed"),
        },
        enabled=handoff_enabled,
    )

    print("success:")
    print(f"  {boss_delegated}")
    print(f"  {president_assigned}")
    print(f"  {worker_done}")
    print(f"  {president_reviewed}")
    if reviewer is not None:
        print(f"  {reviewer_done}")
    for marker in product_retry_markers:
        print(f"  {marker}")
    print(f"  {boss_reported}")
    print("git_status_after:")
    print(status_after_worker or "clean")
    print("diff_stat_after:")
    print(diff_stat_after_worker or "empty")
    print(f"test_status: {test_status}")
    print(f"quality_status: {quality_status}")
    print("supervisor_status: passed")
    if quality_quick_path:
        print(f"quality_quick: {quality_quick_path}")
    if product_quality_enabled:
        print(f"product_quality_status: {product_quality_status}")
        print(f"product_score: {product_review.score if product_review.score is not None else 'missing'}")
        print(f"product_retry_count: {product_retry_count}")
    print("policy_status: clean")
    if report_handoff:
        print(f"handoff_report: {report_handoff}")
    if test_output:
        print("test_output_tail:")
        print(test_output)
    if quality_mode == "quick" and quality_status == "failed":
        print("run_status: failed")
        return 1
    if product_quality_enabled and product_quality_status != "passed":
        print("run_status: failed")
        return 1
    if test_status == "failed" and not bool(getattr(args, "allow_test_failure", False)):
        print("run_status: failed")
        return 1
    return 0


async def _run_speckit_team_run(connection: Any, app: Any, args: argparse.Namespace) -> int:
    _ensure_command(args.boss_cmd)
    _ensure_command(args.president_cmd)
    _ensure_command(args.worker_cmd)
    if args.with_reviewer:
        _ensure_command(args.reviewer_cmd)
    if args.auto_approve_prompts and not args.allow_write:
        raise RuntimeError("--auto-approve-prompts requires --allow-write")
    if args.product_quality and not args.with_reviewer:
        raise RuntimeError("--product-quality requires --with-reviewer")

    repo = str(args.repo or "").strip()
    repo_path = Path(repo)
    if not repo_path.is_dir():
        raise RuntimeError(f"repo path does not exist or is not a directory: {repo}")
    status_before = _git_status_short(repo)
    if status_before and not args.allow_dirty:
        raise RuntimeError(
            "target repo is dirty; commit/stash changes or pass --allow-dirty\n"
            f"{status_before}"
        )
    claude_config_resolution = _resolve_claude_config_root(str(getattr(args, "claude_config", "") or ""))

    repo_name = _repo_name(repo)
    ui_group_id = str(args.ui_group_id or f"{repo_name}-speckit-run-{uuid.uuid4().hex[:8]}").strip()
    roles = [args.boss_role, args.president_role, args.worker_role]
    if args.with_reviewer:
        roles.append(args.reviewer_role)
    commands = {
        args.boss_role: args.boss_cmd,
        args.president_role: args.president_cmd,
        args.worker_role: args.worker_cmd,
    }
    if args.with_reviewer:
        commands[args.reviewer_role] = args.reviewer_cmd
    run_id = str(getattr(args, "run_id", "") or uuid.uuid4().hex[:8]).upper().replace("-", "_")
    handoff_dir = str(getattr(args, "handoff_dir", ".mesh/runs") or ".mesh/runs")
    handoff_enabled = not bool(getattr(args, "no_handoff", False))
    report_filename = "06-report.json" if args.with_reviewer else "05-report.json"
    fallback_used_roles: set[str] = set()
    launch_repair_attempts = 0
    attempt_index = 0
    launch_roles = list(roles)
    while True:
        attempt_ui_group_id = ui_group_id
        startup_wait_s = max(0.0, float(args.startup_wait)) + (max(2.0, float(args.poll_interval) * 2.0) if attempt_index > 0 else 0.0)
        print(
            f"opening speckit team run layout group={attempt_ui_group_id}"
            + (f" roles={','.join(launch_roles)}" if launch_roles != roles else "")
        )
        print("git_status_before:")
        print(status_before or "clean")
        retry_same_group = False
        try:
            if launch_roles:
                await _launch_role_layout(
                    connection,
                    repo=repo,
                    roles=launch_roles,
                    commands={role: commands[role] for role in launch_roles},
                    ui_group_id=attempt_ui_group_id,
                )
            await asyncio.sleep(startup_wait_s)
            app = await _refresh_iterm_app(connection, app)
            panes = await _find_mesh_panes_ready(
                app,
                repo,
                roles,
                attempt_ui_group_id,
                timeout=max(4.0, min(float(args.startup_timeout), 20.0)),
                poll_interval=max(0.5, float(args.poll_interval)),
            )
            print("waiting for CLI prompts")
            for role, command in commands.items():
                await _wait_for_cli_ready(
                    panes[role].session,
                    role=role,
                    command_text=command,
                    timeout=args.startup_timeout,
                    poll_interval=args.poll_interval,
                    auto_approve_prompts=args.auto_approve_prompts,
                    allowed_edit_paths=_edit_prompt_allowed_paths_for_role(
                        role,
                        args.worker_role,
                        tuple(str(item) for item in (args.auto_approve_edit_path or [])),
                    ),
                )
            cycle_args = argparse.Namespace(
                repo=repo,
                ui_group_id=attempt_ui_group_id,
                role_commands=dict(commands),
                boss_role=args.boss_role,
                president_role=args.president_role,
                worker_role=args.worker_role,
                reviewer_role=args.reviewer_role,
                with_reviewer=args.with_reviewer,
                feature=args.feature,
                task=args.task,
                allow_write=args.allow_write,
                test_command=args.test_command,
                test_timeout=args.test_timeout,
                allow_test_failure=args.allow_test_failure,
                quality=args.quality,
                product_quality=args.product_quality,
                min_product_score=args.min_product_score,
                max_quality_retries=args.max_quality_retries,
                max_turns=args.max_turns,
                handoff_dir=args.handoff_dir,
                no_handoff=args.no_handoff,
                claude_config=args.claude_config,
                claude_config_resolution=claude_config_resolution,
                boss_contract=args.boss_contract,
                president_contract=args.president_contract,
                worker_contract=args.worker_contract,
                reviewer_contract=args.reviewer_contract,
                run_id=run_id,
                response_timeout=args.response_timeout,
                poll_interval=args.poll_interval,
                auto_approve_prompts=args.auto_approve_prompts,
                auto_approve_edit_path=args.auto_approve_edit_path,
            )
            return await _run_speckit_team_cycle(app, cycle_args)
        except RuntimeError as exc:
            error_text = str(exc)
            missing_roles: list[str] = []
            if "no pane matched" in error_text and launch_repair_attempts < 1:
                app = await _refresh_iterm_app(connection, app)
                try:
                    existing_roles = {
                        pane.role
                        for pane in await _mesh_sessions(app, repo, attempt_ui_group_id)
                    }
                except Exception:
                    existing_roles = set()
                missing_roles = [role for role in roles if role not in existing_roles]
                if missing_roles:
                    launch_repair_attempts += 1
                    attempt_index += 1
                    retry_same_group = True
                    repaired_roles: list[str] = []
                    for missing_role in missing_roles:
                        launched = await _launch_single_role_tab_in_group(
                            app,
                            repo=repo,
                            role=missing_role,
                            command_text=commands[missing_role],
                            ui_group_id=attempt_ui_group_id,
                        )
                        if launched:
                            repaired_roles.append(missing_role)
                    launch_roles = [role for role in missing_roles if role not in repaired_roles]
                    if repaired_roles:
                        print(
                            "launch repair: reopened missing role(s) "
                            f"{','.join(repaired_roles)} in existing window group={attempt_ui_group_id}"
                        )
                    elif launch_roles:
                        print(
                            "launch repair: relaunching missing role(s) "
                            f"{','.join(launch_roles)} in group={attempt_ui_group_id}"
                        )
                    await asyncio.sleep(max(1.0, float(args.poll_interval)))
                    continue
            fallback_applied = False
            for role in roles:
                fallback_command = _provider_fallback_command(
                    role=role,
                    commands=commands,
                    error_text=error_text,
                    used_roles=fallback_used_roles,
                )
                if not fallback_command:
                    continue
                previous_command = str(commands.get(role, "") or "")
                commands[role] = fallback_command
                fallback_used_roles.add(role)
                attempt_index += 1
                fallback_applied = True
                retry_same_group = True
                print(
                    f"provider fallback: role={role} command={previous_command} -> {fallback_command} "
                    f"(reason=provider_not_ready)"
                )
                app = await _refresh_iterm_app(connection, app)
                retired = await _close_or_retire_mesh_role_panes(app, repo, role, attempt_ui_group_id)
                print(f"provider fallback: retired {retired} pane(s) for role={role} group={attempt_ui_group_id}")
                relaunched = await _launch_single_role_tab_in_group(
                    app,
                    repo=repo,
                    role=role,
                    command_text=fallback_command,
                    ui_group_id=attempt_ui_group_id,
                )
                if relaunched:
                    launch_roles = []
                    print(f"provider fallback: reopened role={role} with fallback command in group={attempt_ui_group_id}")
                else:
                    launch_roles = [role]
                await asyncio.sleep(max(1.0, float(args.poll_interval)))
                break
            if fallback_applied:
                continue
            launch_roles = list(roles)
            report_handoff = _write_handoff_json(
                repo,
                handoff_dir,
                run_id,
                report_filename,
                _supervisor_failure_handoff_payload(
                    feature=args.feature,
                    task=args.task,
                    error_text=error_text,
                ),
                enabled=handoff_enabled,
            )
            if report_handoff:
                print(f"handoff_report: {report_handoff}")
            raise
        finally:
            if not args.keep_open and not retry_same_group:
                closed = await _close_mesh_tabs(app, repo, attempt_ui_group_id)
                print(f"closed {closed} run tab(s) group={attempt_ui_group_id}")


async def _run(connection, args: argparse.Namespace) -> int:
    import iterm2

    app = await iterm2.async_get_app(connection)
    if app is None:
        raise RuntimeError("iTerm2 app not available")

    if args.cmd == "list":
        panes = await _mesh_sessions(app, args.repo, getattr(args, "ui_group_id", ""))
        lines = []
        for pane in panes:
            lines.append(
                f"W{pane.window_index} T{pane.tab_index} S{pane.session_index} "
                f"role={pane.role} repo={pane.repo}"
            )
        _emit("\n".join(lines), args.output)
        return 0

    if args.cmd == "close":
        ui_group_id = getattr(args, "ui_group_id", "")
        closed = await _close_mesh_tabs(app, args.repo, ui_group_id)
        suffix = f" ui_group_id={ui_group_id}" if ui_group_id else ""
        print(f"closed {closed} mesh tab(s) for repo={args.repo}{suffix}")
        return 0

    if args.cmd == "two-cli-smoke":
        return await _run_two_cli_smoke(app, args)

    if args.cmd == "two-cli-e2e":
        return await _run_two_cli_e2e(connection, app, args)

    if args.cmd == "team-e2e":
        return await _run_team_e2e(connection, app, args)

    if args.cmd == "speckit-team-e2e":
        return await _run_speckit_team_e2e(connection, app, args)

    if args.cmd == "speckit-team-run":
        return await _run_speckit_team_run(connection, app, args)

    pane = await _find_mesh_pane(app, args.repo, args.role, getattr(args, "ui_group_id", ""))

    if args.cmd == "focus":
        await pane.session.async_activate()
        print(
            f"focused W{pane.window_index} T{pane.tab_index} S{pane.session_index} "
            f"role={pane.role} repo={pane.repo}"
        )
        return 0
    if args.cmd == "send-text":
        await pane.session.async_activate()
        await pane.session.async_send_text(args.text)
        print(f"sent text to role={pane.role} repo={pane.repo}")
        return 0
    if args.cmd == "send-line":
        await pane.session.async_activate()
        await pane.session.async_send_text(args.text)
        await asyncio.sleep(0.08)
        await pane.session.async_send_text("\r")
        print(f"sent line to role={pane.role} repo={pane.repo}")
        return 0
    if args.cmd == "send-key":
        await pane.session.async_activate()
        await pane.session.async_send_text(_key_text(args.key))
        print(f"sent key {args.key} to role={pane.role} repo={pane.repo}")
        return 0
    if args.cmd == "dump":
        _emit(await _screen_tail(pane.session, lines=args.lines), args.output)
        return 0
    raise RuntimeError(f"unsupported command: {args.cmd}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    args = _parse_args()
    try:
        import iterm2
    except ImportError:
        raise SystemExit(
            "Error: Python package 'iterm2' not found. Use: uv run --with iterm2 -- python scripts/mesh_iterm_control.py ..."
        )

    try:
        iterm2.run_until_complete(lambda conn: _run(conn, args), retry=_iterm_retry_enabled())
    except Exception as exc:
        raise SystemExit(f"Error: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
