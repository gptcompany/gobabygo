"""Mesh worker client.

Entry point for worker processes. Handles:
- Registration with the router
- Periodic heartbeat (5s interval)
- Task polling (GET /tasks/next)
- Task execution delegation (invoke CLI)
- Result reporting (complete/fail)
"""

from __future__ import annotations

import logging
import os
import random
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

import requests

logger = logging.getLogger("mesh.worker")


@dataclass
class WorkerConfig:
    """Configuration for a mesh worker instance."""

    worker_id: str = "ws-unknown-01"
    router_url: str = "http://localhost:8780"
    cli_type: str = "claude"
    account_profile: str = "work"
    auth_token: str | None = None
    heartbeat_interval: float = 5.0
    poll_interval: float = 2.0
    longpoll_timeout: float = 25.0  # Must match server MESH_LONGPOLL_TIMEOUT_S
    capabilities: list[str] = field(default_factory=lambda: ["code", "tests", "refactor"])
    allowed_accounts: list[str] = field(default_factory=list)  # MESH_ALLOWED_ACCOUNTS=foo,bar,*
    execution_modes: list[str] = field(default_factory=lambda: ["batch"])
    cli_command: str = "claude"  # Template supports {target_account}, {account_profile}, {worker_account_profile}
    dry_run: bool = False  # MESH_DRY_RUN=1 logs without executing
    work_dir: str = "/tmp/mesh-tasks"  # MESH_WORK_DIR
    task_timeout: int = 1800  # MESH_TASK_TIMEOUT_S (30 min)

    @classmethod
    def from_env(cls) -> WorkerConfig:
        """Create config from environment variables."""
        raw_caps = os.environ.get("MESH_CAPABILITIES", "").strip()
        capabilities = (
            [c.strip() for c in raw_caps.split(",") if c.strip()]
            if raw_caps
            else ["code", "tests", "refactor"]
        )
        raw_allowed = os.environ.get("MESH_ALLOWED_ACCOUNTS", "").strip()
        allowed_accounts = [a.strip() for a in raw_allowed.split(",") if a.strip()]
        return cls(
            worker_id=os.environ.get("MESH_WORKER_ID", "ws-unknown-01"),
            router_url=os.environ.get("MESH_ROUTER_URL", "http://localhost:8780"),
            cli_type=os.environ.get("MESH_CLI_TYPE", "claude"),
            account_profile=os.environ.get("MESH_ACCOUNT_PROFILE", "work"),
            auth_token=os.environ.get("MESH_AUTH_TOKEN"),
            capabilities=capabilities,
            allowed_accounts=allowed_accounts,
            longpoll_timeout=float(os.environ.get("MESH_LONGPOLL_TIMEOUT_S", "25")),
            cli_command=os.environ.get("MESH_CLI_COMMAND", "claude"),
            execution_modes=[
                m.strip() for m in os.environ.get("MESH_EXECUTION_MODES", "batch").split(",")
                if m.strip()
            ] or ["batch"],
            dry_run=os.environ.get("MESH_DRY_RUN", "").strip() == "1",
            work_dir=os.environ.get("MESH_WORK_DIR", "/tmp/mesh-tasks"),
            task_timeout=int(os.environ.get("MESH_TASK_TIMEOUT_S", "1800")),
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
        # Preserve order, drop duplicates
        return list(dict.fromkeys(caps))


class MeshWorker:
    """Worker client that communicates with the mesh router."""

    def __init__(self, config: WorkerConfig) -> None:
        self.config = config
        self._running = False
        self._heartbeat_thread: threading.Thread | None = None
        self._session = requests.Session()
        if config.auth_token:
            self._session.headers["Authorization"] = f"Bearer {config.auth_token}"
        self._session.headers["Content-Type"] = "application/json"

    def start(self) -> None:
        """Register with router, start heartbeat, begin polling."""
        self._running = True
        self._register()
        self._start_heartbeat()
        self._poll_loop()

    def stop(self) -> None:
        """Graceful shutdown."""
        logger.info("Stopping worker %s...", self.config.worker_id)
        self._running = False
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=10)

    def _register(self) -> None:
        """POST /register with worker metadata."""
        url = f"{self.config.router_url}/register"
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
        resp = self._session.post(url, json=payload, timeout=5)
        if resp.status_code == 409:
            logger.info("Worker %s already registered, continuing", self.config.worker_id)
        else:
            resp.raise_for_status()
            logger.info("Registered as %s", self.config.worker_id)

    def _start_heartbeat(self) -> None:
        """Start background heartbeat thread."""

        def heartbeat_loop() -> None:
            url = f"{self.config.router_url}/heartbeat"
            while self._running:
                try:
                    resp = self._session.post(
                        url,
                        json={"worker_id": self.config.worker_id},
                        timeout=3,
                    )
                    try:
                        data = resp.json()
                        if data.get("status") == "unknown_worker":
                            logger.warning(
                                "Router reports unknown_worker for %s, re-registering",
                                self.config.worker_id,
                            )
                            try:
                                self._register()
                            except requests.RequestException as re_err:
                                logger.error("Re-registration failed: %s", re_err)
                    except (ValueError, KeyError):
                        pass  # Old server or non-JSON response -- ignore
                except requests.RequestException as e:
                    logger.warning("Heartbeat failed: %s", e)
                time.sleep(self.config.heartbeat_interval)

        self._heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _poll_loop(self) -> None:
        """Main loop: long-poll for tasks, execute, report.

        On 204 (timeout): reconnect immediately with small random jitter (100-500ms).
        On 200 (task): execute task, then reconnect when idle.
        On error/unreachable: exponential backoff 1s-30s with random jitter.
        """
        url = f"{self.config.router_url}/tasks/next?worker_id={self.config.worker_id}"
        backoff = 0.0  # No backoff on first connect
        while self._running:
            if backoff > 0:
                # Jitter applies to ALL reconnect scenarios
                jitter = random.uniform(0.1, 0.5)
                time.sleep(backoff + jitter)
            try:
                # Timeout slightly longer than server's longpoll timeout
                # to avoid client-side timeout before server responds
                resp = self._session.get(
                    url, timeout=self.config.longpoll_timeout + 5
                )
                if resp.status_code == 200:
                    backoff = 0.0  # Reset on success
                    try:
                        task = resp.json()
                    except ValueError:
                        logger.warning("Poll returned non-JSON response")
                        continue
                    self._execute_task(task)
                    # After task execution, reconnect immediately (no backoff)
                    continue
                elif resp.status_code == 204:
                    # Timeout, reconnect with small jitter only
                    backoff = 0.0
                    jitter = random.uniform(0.1, 0.5)
                    time.sleep(jitter)
                    continue
                elif resp.status_code == 409:
                    # Duplicate poll -- should not happen in normal operation
                    logger.warning("Duplicate poll detected, backing off")
                    backoff = min(backoff * 2 or 1.0, 30.0)
                else:
                    logger.warning("Poll returned %d", resp.status_code)
                    backoff = min(backoff * 2 or 1.0, 30.0)
            except requests.RequestException as e:
                logger.warning("Poll failed: %s", e)
                backoff = min(backoff * 2 or 1.0, 30.0)

    def _execute_task(self, task: dict) -> None:
        """Execute a task via CLI invocation.

        Validates payload.prompt, builds CLI command, runs subprocess
        (or dry-run), and reports result. Every error path reports failure
        so no task is ever stuck in 'running'.
        """
        task_id = task["task_id"]
        payload = task.get("payload", {})
        prompt = payload.get("prompt", "")
        execution_mode = str(task.get("execution_mode", "batch")).strip() or "batch"
        target_account = str(task.get("target_account") or self.config.account_profile).strip() or self.config.account_profile

        logger.info("Executing task %s: %s", task_id, task.get("title", "untitled"))

        # Ack task: assigned -> running
        if not self._ack_task(task_id):
            return

        try:
            # Validate payload contract
            if execution_mode != "batch":
                self._report_failure(
                    task_id,
                    f"unsupported execution_mode={execution_mode} for batch worker",
                )
                return
            if not prompt:
                self._report_failure(task_id, "missing payload.prompt")
                return

            # Build command — shlex.split tokenizes multi-word commands.
            # Placeholder resolution:
            # - {target_account}: account requested by current task
            # - {account_profile}: alias of task account (for backward compatibility)
            # - {worker_account_profile}: static worker registration profile
            cmd_base = (
                self.config.cli_command
                .replace("{target_account}", target_account)
                .replace("{account_profile}", target_account)
                .replace("{worker_account_profile}", self.config.account_profile)
            )
            cmd_parts = shlex.split(cmd_base)
            full_cmd = cmd_parts + ["--print", "-p", prompt]
            work_dir = payload.get("working_dir", self.config.work_dir)

            # Dry-run path
            if self.config.dry_run:
                logger.info("DRY_RUN task=%s cmd=%s cwd=%s", task_id, full_cmd, work_dir)
                self._report_complete(task_id, {
                    "output": f"[dry-run] {' '.join(full_cmd)}",
                    "dry_run": True,
                })
                return

            # Real execution
            start = time.monotonic()
            proc = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=self.config.task_timeout,
                cwd=work_dir,
            )
            duration = time.monotonic() - start
            logger.info("CLI finished task=%s exit=%d duration=%.1fs", task_id, proc.returncode, duration)

            if proc.returncode == 0:
                output = proc.stdout[-4096:] if len(proc.stdout) > 4096 else proc.stdout
                self._report_complete(task_id, {"output": output, "exit_code": 0})
            else:
                stderr = proc.stderr[-2048:] if len(proc.stderr) > 2048 else proc.stderr
                self._report_failure(task_id, f"exit_code={proc.returncode}: {stderr}")

        except subprocess.TimeoutExpired:
            self._report_failure(task_id, f"timeout after {self.config.task_timeout}s")
        except FileNotFoundError:
            cmd_name = shlex.split(
                self.config.cli_command
                .replace("{target_account}", target_account)
                .replace("{account_profile}", target_account)
                .replace("{worker_account_profile}", self.config.account_profile)
            )[0]
            self._report_failure(task_id, f"command not found: {cmd_name}")
        except Exception as e:
            self._report_failure(task_id, f"unexpected: {e}")

    def _ack_task(self, task_id: str) -> bool:
        """ACK task (assigned -> running). Returns True on success."""
        try:
            ack_resp = self._session.post(
                f"{self.config.router_url}/tasks/ack",
                json={"task_id": task_id, "worker_id": self.config.worker_id},
                timeout=5,
            )
            if ack_resp.status_code != 200:
                logger.warning("Task %s ack failed (%d), skipping", task_id, ack_resp.status_code)
                return False
            return True
        except requests.RequestException as e:
            logger.warning("Task %s ack error: %s, skipping", task_id, e)
            return False

    def _report_complete(self, task_id: str, result: dict) -> None:
        """Report task completion to router."""
        try:
            self._session.post(
                f"{self.config.router_url}/tasks/complete",
                json={
                    "task_id": task_id,
                    "worker_id": self.config.worker_id,
                    "result": result,
                },
                timeout=5,
            )
            logger.info("Task %s completed", task_id)
        except requests.RequestException as e:
            logger.error("Failed to report completion for task %s: %s", task_id, e)

    def _report_failure(self, task_id: str, error: str) -> None:
        """Report task failure to router."""
        logger.error("Task %s failed: %s", task_id, error)
        try:
            self._session.post(
                f"{self.config.router_url}/tasks/fail",
                json={
                    "task_id": task_id,
                    "worker_id": self.config.worker_id,
                    "error": error,
                },
                timeout=5,
            )
        except requests.RequestException as e:
            logger.error("Failed to report failure for task %s: %s", task_id, e)


def run_worker() -> None:
    """Entry point for mesh-worker service."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    config = WorkerConfig.from_env()
    worker = MeshWorker(config)

    def handle_shutdown(signum: int, frame: object) -> None:
        logger.info("Shutting down worker %s (signal %d)...", config.worker_id, signum)
        worker.stop()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    worker.start()


if __name__ == "__main__":
    run_worker()
