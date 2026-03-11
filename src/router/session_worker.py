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
import threading
import time
from dataclasses import dataclass, field

import requests

from src.router.failure_classifier import classify_cli_failure
from src.router.provider_runtime import resolve_cli_command
from src.router.workdir_guard import parse_allowed_work_dirs, resolve_work_dir

logger = logging.getLogger("mesh.session_worker")
_CLAUDE_CODE_READY_MARKERS = ("❯",)
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
    for line in reversed((captured or "").splitlines()):
        normalized = line.replace("\xa0", " ").lstrip()
        if normalized.startswith("❯"):
            return bool(normalized[1:].strip())
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
    return any(
        line.lstrip().startswith("● ")
        for line in body.splitlines()
    )


def _looks_like_start_screen(captured: str) -> bool:
    body = str(captured or "")
    lowered = body.lower()
    if "welcome back" not in lowered:
        return False
    if _capture_shows_activity(body):
        return False
    return (
        "tips for getting started" in lowered
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


@dataclass
class SessionWorkerConfig:
    """Configuration for a tmux-backed interactive session worker."""

    worker_id: str = "ws-unknown-session-01"
    router_url: str = "http://localhost:8780"
    cli_type: str = "claude"
    account_profile: str = "work"
    auth_token: str | None = None
    heartbeat_interval: float = 5.0
    heartbeat_timeout: float = 5.0
    control_plane_timeout: float = 30.0
    longpoll_timeout: float = 25.0
    capabilities: list[str] = field(default_factory=lambda: ["code", "tests", "refactor", "interactive"])
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
            else ["code", "tests", "refactor", "interactive"]
        )
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
            heartbeat_timeout=float(os.environ.get("MESH_HEARTBEAT_TIMEOUT_S", "5")),
            control_plane_timeout=float(os.environ.get("MESH_CONTROL_PLANE_TIMEOUT_S", "30")),
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
        self._register()
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
        if resp.status_code == 409:
            logger.info("Session worker %s already registered, continuing", self.config.worker_id)
            return
        resp.raise_for_status()
        logger.info("Registered session worker %s", self.config.worker_id)

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
                resp = self._http.get(url, timeout=self.config.longpoll_timeout + 5)
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
            if not prompt:
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
            self._prepare_cli_runtime(work_dir, target_account)
            tmux_session_name = self._tmux_session_name(task_id, target_account)
            if self._tmux_has_session(tmux_session_name):
                logger.warning(
                    "Killing stale tmux session before retry: %s",
                    tmux_session_name,
                )
                self._tmux_kill_session(tmux_session_name)
            self._tmux_new_session(tmux_session_name, work_dir, cmd_base)
            time.sleep(max(0.0, float(self.config.startup_post_launch_settle_s)))

            attach_meta, upterm_proc = self._create_attach_handle(tmux_session_name)

            session_id = self._open_session(task, tmux_session_name, work_dir, target_account, attach_meta)
            self._send_session_message(
                session_id,
                direction="system",
                role="system",
                content=f"tmux session created: {tmux_session_name}",
                metadata={"tmux_session": tmux_session_name, "working_dir": work_dir},
            )
            self._send_session_message(
                session_id,
                direction="in",
                role="president",
                content=prompt,
                metadata={"source": "task.payload.prompt", "task_id": task_id},
            )
            if not self._wait_for_cli_ready(tmux_session_name):
                logger.warning(
                    "CLI prompt readiness timeout for session %s; sending prompt anyway",
                    tmux_session_name,
                )
            pre_prompt_capture = self._tmux_capture_pane(tmux_session_name)
            self._tmux_send_text(tmux_session_name, prompt)
            self._ensure_prompt_submitted(tmux_session_name)
            self._ensure_prompt_delivered(tmux_session_name, prompt, pre_prompt_capture)

            start = time.monotonic()
            after_seq = 0
            # Mark any messages already present so we don't replay our own initial prompt.
            try:
                msgs = self._list_session_messages(session_id, after_seq=0, limit=1000)
            except SessionNotFoundError:
                logger.warning(
                    "Session %s disappeared before initial sync; continuing with after_seq=0",
                    session_id,
                )
                msgs = []
            except requests.RequestException as e:
                logger.warning(
                    "Initial session message sync failed for %s: %s; continuing with after_seq=0",
                    session_id,
                    e,
                )
                msgs = []
            if msgs:
                after_seq = max(int(m.get("seq") or 0) for m in msgs)
            last_capture = ""
            last_emitted_capture = ""
            auto_exit_sent = False
            auto_exit_baseline_capture = ""
            prompt_delivery_confirmed = False
            prompt_delivery_attempts = 0
            if auto_exit_on_success and not success_markers and not success_file_path:
                logger.warning(
                    "Task %s requested auto_exit_on_success without success markers or success file; session will stay open",
                    task_id,
                )

            while self._running:
                # Safety cap for abandoned sessions
                if (time.monotonic() - start) > self.config.task_timeout:
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
                    new_after_seq = self._deliver_inbound_messages(session_id, tmux_session_name, after_seq)
                except SessionNotFoundError:
                    logger.info(
                        "Router no longer has session %s; stopping interactive loop for task %s",
                        session_id,
                        task_id,
                    )
                    break
                if new_after_seq > after_seq:
                    auto_exit_baseline_capture = ""
                    prompt_delivery_confirmed = False
                    prompt_delivery_attempts = 0
                after_seq = max(after_seq, new_after_seq)
                captured = self._tmux_capture_pane(tmux_session_name)
                if captured:
                    prior_capture = last_capture
                    capture_emit = _compute_output_emit(prior_capture, captured)
                    delta_text = capture_emit[0] if capture_emit else ""
                    if not prompt_delivery_confirmed:
                        if captured.strip() and (
                            not _looks_like_start_screen(captured)
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
                    live_failure_kind = _detect_interactive_failure_screen(
                        self.config.cli_type, captured
                    )
                    if live_failure_kind:
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
            if session_id:
                failure_kind = classify_cli_failure(self.config.cli_type, final_snapshot)
                if final_snapshot and final_snapshot.strip() != (last_emitted_capture or "").strip():
                    self._send_session_message(
                        session_id,
                        direction="out",
                        role="cli",
                        content=final_snapshot[-self.config.output_emit_max_chars:],
                        metadata={"snapshot": True, "final": True},
                    )
                self._send_session_message(
                    session_id,
                    direction="system",
                    role="system",
                    content="tmux session exited",
                    metadata={"tmux_session": tmux_session_name},
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
                self._report_complete(task_id, {
                    "interactive_session": True,
                    "session_id": session_id,
                    "tmux_session": tmux_session_name,
                    "final_snapshot": final_snapshot[-4000:] if final_snapshot else "",
                })

        except Exception as e:
            logger.exception("Interactive task %s failed", task_id)
            if session_id:
                try:
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
        if self.config.cli_type != "claude":
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
            if os.path.isdir(instance_dir):
                state_paths.append(os.path.join(instance_dir, ".claude.json"))
            else:
                logger.info(
                    "Skipping Claude instance preseed; CCS profile dir missing: %s",
                    instance_dir,
                )
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

    def _tmux_new_session(self, session_name: str, work_dir: str, cli_command: str) -> None:
        # Launch command directly inside a non-interactive bash wrapper so tmux session ends when CLI exits.
        subprocess.run(
            [self.config.tmux_bin, "new-session", "-d", "-s", session_name, "-c", work_dir, "bash", "-lc", cli_command],
            check=True,
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
            if captured.strip() == baseline or _capture_contains_prompt_text(captured, prompt):
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

    def _open_session(
        self,
        task: dict,
        tmux_session: str,
        work_dir: str,
        target_account: str,
        attach_meta: dict | None = None,
    ) -> str:
        metadata: dict = {
            "tmux_session": tmux_session,
            "working_dir": work_dir,
            "task_title": task.get("title", ""),
        }
        if attach_meta:
            metadata.update(attach_meta)
        body = {
            "worker_id": self.config.worker_id,
            "cli_type": self.config.cli_type,
            "account_profile": target_account,
            "task_id": task["task_id"],
            "metadata": metadata,
        }
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

    def _deliver_inbound_messages(self, session_id: str, tmux_session: str, after_seq: int) -> int:
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
            content = str(msg.get("content", ""))
            metadata = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
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
