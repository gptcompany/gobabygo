"""Mesh worker client.

Entry point for worker processes. Handles:
- Registration with the router
- Periodic heartbeat (5s interval)
- Task polling (GET /tasks/next)
- Task execution delegation (invoke CLI)
- Result reporting (complete/fail)
"""

from __future__ import annotations

import json
import logging
import os
import random
import signal
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

    @classmethod
    def from_env(cls) -> WorkerConfig:
        """Create config from environment variables."""
        return cls(
            worker_id=os.environ.get("MESH_WORKER_ID", "ws-unknown-01"),
            router_url=os.environ.get("MESH_ROUTER_URL", "http://localhost:8780"),
            cli_type=os.environ.get("MESH_CLI_TYPE", "claude"),
            account_profile=os.environ.get("MESH_ACCOUNT_PROFILE", "work"),
            auth_token=os.environ.get("MESH_AUTH_TOKEN"),
            longpoll_timeout=float(os.environ.get("MESH_LONGPOLL_TIMEOUT_S", "25")),
        )


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
            "capabilities": self.config.capabilities,
            "status": "idle",
            "concurrency": 1,
        }
        resp = self._session.post(url, json=payload, timeout=5)
        resp.raise_for_status()
        logger.info("Registered as %s", self.config.worker_id)

    def _start_heartbeat(self) -> None:
        """Start background heartbeat thread."""

        def heartbeat_loop() -> None:
            url = f"{self.config.router_url}/heartbeat"
            while self._running:
                try:
                    self._session.post(
                        url,
                        json={"worker_id": self.config.worker_id},
                        timeout=3,
                    )
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

        For v1: logs task receipt and reports success.
        Full CLI integration (CCS profile + command dispatch) to be wired
        in production deployment.
        """
        task_id = task["task_id"]
        logger.info("Executing task %s: %s", task_id, task.get("title", "untitled"))

        # Ack task: assigned -> running
        try:
            ack_resp = self._session.post(
                f"{self.config.router_url}/tasks/ack",
                json={"task_id": task_id, "worker_id": self.config.worker_id},
                timeout=5,
            )
            if ack_resp.status_code != 200:
                logger.warning("Task %s ack failed (%d), skipping", task_id, ack_resp.status_code)
                return
        except requests.RequestException as e:
            logger.warning("Task %s ack error: %s, skipping", task_id, e)
            return

        try:
            # TODO: Wire CLI invocation (CCS profile + command dispatch)
            result = {"output": f"Task {task_id} executed by {self.config.worker_id}"}

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
        except (requests.RequestException, ValueError, RuntimeError, OSError) as e:
            logger.error("Task %s failed: %s", task_id, e)
            try:
                self._session.post(
                    f"{self.config.router_url}/tasks/fail",
                    json={
                        "task_id": task_id,
                        "worker_id": self.config.worker_id,
                        "error": str(e),
                    },
                    timeout=5,
                )
            except requests.RequestException:
                logger.error("Failed to report failure for task %s", task_id)


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
