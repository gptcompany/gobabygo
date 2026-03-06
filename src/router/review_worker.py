"""External verifier worker for review-state tasks.

Polls tasks in `review` state, runs an external CLI to produce an
approve/reject decision, then calls router review endpoints:
- POST /tasks/review/approve
- POST /tasks/review/reject
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger("mesh.review_worker")

_TERMINAL = {"completed", "failed", "timeout", "canceled"}
_PENDING_FIX_STATUSES = ("queued", "assigned", "running", "blocked", "review")


@dataclass
class ReviewDecision:
    decision: str  # approve|reject
    reason: str


@dataclass
class ReviewWorkerConfig:
    """Configuration for verifier worker."""

    router_url: str = "http://localhost:8780"
    auth_token: str | None = None
    poll_interval: float = 8.0
    reviewer_id: str = "verifier-codex"
    cli_command: str = "ccs codex --effort xhigh"
    account_profile: str = "review-codex"
    work_dir: str = "/tmp/mesh-tasks"
    task_timeout: int = 1200
    max_review_tasks: int = 100
    dry_run: bool = False
    target_cli_filter: str = ""  # optional exact match (claude|codex|gemini)
    target_account_filter: str = ""  # optional exact match

    @classmethod
    def from_env(cls) -> ReviewWorkerConfig:
        return cls(
            router_url=os.environ.get("MESH_ROUTER_URL", "http://localhost:8780"),
            auth_token=os.environ.get("MESH_AUTH_TOKEN"),
            poll_interval=float(os.environ.get("MESH_REVIEW_POLL_INTERVAL_S", "8")),
            reviewer_id=os.environ.get("MESH_REVIEWER_ID", "verifier-codex"),
            cli_command=os.environ.get("MESH_REVIEW_CLI_COMMAND", "ccs codex --effort xhigh"),
            account_profile=os.environ.get("MESH_ACCOUNT_PROFILE", "review-codex"),
            work_dir=os.environ.get("MESH_WORK_DIR", "/tmp/mesh-tasks"),
            task_timeout=int(os.environ.get("MESH_TASK_TIMEOUT_S", "1200")),
            max_review_tasks=int(os.environ.get("MESH_REVIEW_MAX_TASKS", "100")),
            dry_run=os.environ.get("MESH_DRY_RUN", "").strip() == "1",
            target_cli_filter=os.environ.get("MESH_REVIEW_TARGET_CLI", "").strip(),
            target_account_filter=os.environ.get("MESH_REVIEW_TARGET_ACCOUNT", "").strip(),
        )


def _parse_review_decision(output: str) -> ReviewDecision:
    """Parse review decision from CLI output.

    Expected format is JSON object with keys:
    - decision: "approve" | "reject"
    - reason: free text
    """
    text = (output or "").strip()
    candidates: list[str] = []
    if text:
        candidates.append(text)
        for line in reversed(text.splitlines()):
            line = line.strip().strip("`")
            if line.startswith("{") and line.endswith("}"):
                candidates.append(line)
                break
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(text[start:end + 1])

    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        decision = str(obj.get("decision", "")).strip().lower()
        reason = str(obj.get("reason", "")).strip()
        if decision in {"approve", "reject"}:
            if not reason:
                reason = "no reason provided"
            return ReviewDecision(decision=decision, reason=reason)

    return ReviewDecision(
        decision="reject",
        reason="review output was not parseable JSON decision",
    )


def _has_pending_fix_tasks(task_id: str, all_tasks: list[dict]) -> bool:
    """Return True when the reviewed task has non-terminal child fix tasks."""
    for task in all_tasks:
        if str(task.get("parent_task_id") or "") != task_id:
            continue
        status = str(task.get("status") or "")
        if status and status not in _TERMINAL:
            return True
    return False


def _safe_json_preview(value: object, max_chars: int) -> str:
    """Render JSON text safely; truncate with explicit marker if too long."""
    text = json.dumps(value, ensure_ascii=True)
    if len(text) <= max_chars:
        return text
    kept = max(0, max_chars - 32)
    omitted = len(text) - kept
    return f"{text[:kept]}... [truncated {omitted} chars]"


class ReviewWorker:
    """Polls review tasks and applies verifier decisions through router API."""

    def __init__(self, config: ReviewWorkerConfig) -> None:
        self.config = config
        self._running = False
        self._http = requests.Session()
        if config.auth_token:
            self._http.headers["Authorization"] = f"Bearer {config.auth_token}"
        self._http.headers["Content-Type"] = "application/json"

    def start(self) -> None:
        self._running = True
        logger.info(
            "starting review worker reviewer_id=%s cli=%s",
            self.config.reviewer_id,
            self.config.cli_command,
        )
        while self._running:
            try:
                self._review_cycle()
            except Exception as e:  # pragma: no cover - defensive loop guard
                logger.exception("review cycle error: %s", e)
            time.sleep(self.config.poll_interval)

    def stop(self) -> None:
        logger.info("stopping review worker %s", self.config.reviewer_id)
        self._running = False

    def _review_cycle(self) -> None:
        review_tasks = self._list_tasks(status="review", limit=self.config.max_review_tasks)
        if not review_tasks:
            return
        for task in review_tasks:
            if not self._running:
                return
            if not self._matches_filters(task):
                continue
            task_id = str(task.get("task_id") or "")
            if not task_id:
                continue
            if self._has_pending_fix_tasks_remote(task_id):
                logger.info("skip task=%s (pending fix tasks)", task_id)
                continue
            self._review_task(task)

    def _matches_filters(self, task: dict) -> bool:
        if self.config.target_cli_filter:
            if str(task.get("target_cli") or "") != self.config.target_cli_filter:
                return False
        if self.config.target_account_filter:
            if str(task.get("target_account") or "") != self.config.target_account_filter:
                return False
        return True

    def _list_tasks(self, *, status: str | None, limit: int) -> list[dict]:
        params: dict[str, str | int] = {"limit": max(1, min(1000, limit))}
        if status:
            params["status"] = status
        resp = self._http.get(f"{self.config.router_url}/tasks", params=params, timeout=10)
        if resp.status_code == 401:
            raise RuntimeError("unauthorized: set MESH_AUTH_TOKEN for review worker")
        resp.raise_for_status()
        data = resp.json()
        tasks = data.get("tasks", [])
        return tasks if isinstance(tasks, list) else []

    def _has_pending_fix_tasks_remote(self, task_id: str) -> bool:
        """Check pending fix tasks by scanning non-terminal statuses in bounded pages."""
        for status in _PENDING_FIX_STATUSES:
            tasks = self._list_tasks(status=status, limit=1000)
            if _has_pending_fix_tasks(task_id, tasks):
                return True
        return False

    def _review_task(self, task: dict) -> None:
        task_id = str(task.get("task_id") or "")
        prompt = self._build_review_prompt(task)
        decision = self._run_cli_review(prompt)
        if self.config.dry_run:
            logger.info(
                "DRY_RUN task=%s decision=%s reason=%s",
                task_id,
                decision.decision,
                decision.reason,
            )
            return
        if decision.decision == "approve":
            self._approve(task_id)
            return
        self._reject(task_id, decision.reason)

    def _build_review_prompt(self, task: dict) -> str:
        task_id = str(task.get("task_id") or "")
        title = str(task.get("title") or "")
        phase = str(task.get("phase") or "")
        target_cli = str(task.get("target_cli") or "")
        target_account = str(task.get("target_account") or "")
        payload = task.get("payload") or {}
        result = task.get("result")
        payload_json = _safe_json_preview(payload, 4000)
        result_json = _safe_json_preview(result, 8000) if result is not None else "null"
        return (
            "You are the mesh verifier.\n"
            "Decide if this task can be approved or must be rejected.\n"
            "Reply with JSON only: {\"decision\":\"approve|reject\",\"reason\":\"...\"}\n\n"
            f"task_id: {task_id}\n"
            f"title: {title}\n"
            f"phase: {phase}\n"
            f"target_cli: {target_cli}\n"
            f"target_account: {target_account}\n"
            f"payload: {payload_json}\n"
            f"result: {result_json}\n"
        )

    def _run_cli_review(self, prompt: str) -> ReviewDecision:
        cmd_base = self.config.cli_command.replace("{account_profile}", self.config.account_profile)
        cmd_parts = shlex.split(cmd_base)
        full_cmd = cmd_parts + ["--print", "-p", prompt]
        logger.info("review command: %s", full_cmd)
        try:
            proc = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=self.config.task_timeout,
                cwd=self.config.work_dir,
            )
        except subprocess.TimeoutExpired:
            logger.warning("review command timeout after %ss", self.config.task_timeout)
            return ReviewDecision("reject", f"review command timeout after {self.config.task_timeout}s")
        if proc.returncode != 0:
            stderr = (proc.stderr or "")[-1024:]
            logger.warning("review command failed exit=%d stderr=%s", proc.returncode, stderr)
            return ReviewDecision("reject", f"review command failed: exit={proc.returncode}")
        return _parse_review_decision(proc.stdout)

    def _approve(self, task_id: str) -> None:
        resp = self._http.post(
            f"{self.config.router_url}/tasks/review/approve",
            json={"task_id": task_id, "verifier_id": self.config.reviewer_id},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("approved task=%s", task_id)
            return
        logger.info("approve skipped task=%s status=%d body=%s", task_id, resp.status_code, resp.text[:300])

    def _reject(self, task_id: str, reason: str) -> None:
        resp = self._http.post(
            f"{self.config.router_url}/tasks/review/reject",
            json={
                "task_id": task_id,
                "verifier_id": self.config.reviewer_id,
                "reason": reason[:500],
            },
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info("rejected task=%s reason=%s", task_id, reason)
            return
        logger.info("reject skipped task=%s status=%d body=%s", task_id, resp.status_code, resp.text[:300])


def run_review_worker() -> None:
    """Entrypoint for review worker service."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    config = ReviewWorkerConfig.from_env()
    worker = ReviewWorker(config)

    def handle_shutdown(signum: int, frame: object) -> None:
        logger.info("shutting down review worker (signal %d)", signum)
        worker.stop()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
    worker.start()


if __name__ == "__main__":
    run_review_worker()
