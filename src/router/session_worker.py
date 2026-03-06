"""Interactive session worker (tmux-backed) for Claude/Codex/Gemini CLIs.

Unlike the batch worker (`worker_client.py`), this worker launches a long-lived
interactive CLI session inside tmux, persists a session record in the router DB
via `/sessions/*`, and allows operator/orchestrator messages to be delivered via
the session message bus.

Human approval gates remain native to each CLI (manual/yolo/etc. config).
This worker focuses on orchestration + persistence + attachability.
"""

from __future__ import annotations

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

logger = logging.getLogger("mesh.session_worker")


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


@dataclass
class SessionWorkerConfig:
    """Configuration for a tmux-backed interactive session worker."""

    worker_id: str = "ws-unknown-session-01"
    router_url: str = "http://localhost:8780"
    cli_type: str = "claude"
    account_profile: str = "work"
    auth_token: str | None = None
    heartbeat_interval: float = 5.0
    longpoll_timeout: float = 25.0
    capabilities: list[str] = field(default_factory=lambda: ["code", "tests", "refactor", "interactive"])
    execution_modes: list[str] = field(default_factory=lambda: ["session"])
    cli_command: str = "claude"
    work_dir: str = "/tmp/mesh-tasks"
    session_poll_interval_s: float = 1.0
    tmux_bin: str = "tmux"
    tmux_capture_lines: int = 200
    output_emit_max_chars: int = 8000
    tmux_session_prefix: str = "mesh"
    task_timeout: int = 7200  # Hard ceiling for interactive sessions (2h)
    auto_complete_on_exit: bool = True
    upterm_bin: str = "upterm"
    upterm_server: str = ""
    upterm_ready_timeout: float = 10.0
    ssh_tmux_user: str = ""
    ssh_tmux_host: str = ""

    @classmethod
    def from_env(cls) -> SessionWorkerConfig:
        return cls(
            worker_id=os.environ.get("MESH_WORKER_ID", "ws-unknown-session-01"),
            router_url=os.environ.get("MESH_ROUTER_URL", "http://localhost:8780"),
            cli_type=os.environ.get("MESH_CLI_TYPE", "claude"),
            account_profile=os.environ.get("MESH_ACCOUNT_PROFILE", "work"),
            auth_token=os.environ.get("MESH_AUTH_TOKEN"),
            longpoll_timeout=float(os.environ.get("MESH_LONGPOLL_TIMEOUT_S", "25")),
            cli_command=os.environ.get("MESH_CLI_COMMAND", "claude"),
            execution_modes=[
                m.strip() for m in os.environ.get("MESH_EXECUTION_MODES", "session").split(",")
                if m.strip()
            ] or ["session"],
            work_dir=os.environ.get("MESH_WORK_DIR", "/tmp/mesh-tasks"),
            session_poll_interval_s=float(os.environ.get("MESH_SESSION_POLL_INTERVAL_S", "1.0")),
            tmux_bin=os.environ.get("MESH_TMUX_BIN", "tmux"),
            tmux_capture_lines=int(os.environ.get("MESH_TMUX_CAPTURE_LINES", "200")),
            output_emit_max_chars=int(os.environ.get("MESH_OUTPUT_EMIT_MAX_CHARS", "8000")),
            tmux_session_prefix=os.environ.get("MESH_TMUX_SESSION_PREFIX", "mesh"),
            task_timeout=int(os.environ.get("MESH_TASK_TIMEOUT_S", "7200")),
            auto_complete_on_exit=os.environ.get("MESH_AUTO_COMPLETE_ON_EXIT", "1").strip() != "0",
            upterm_bin=os.environ.get("MESH_UPTERM_BIN", "upterm"),
            upterm_server=os.environ.get("MESH_UPTERM_SERVER", ""),
            upterm_ready_timeout=float(os.environ.get("MESH_UPTERM_READY_TIMEOUT", "10.0")),
            ssh_tmux_user=os.environ.get("MESH_SSH_TMUX_USER", ""),
            ssh_tmux_host=os.environ.get("MESH_SSH_TMUX_HOST", ""),
        )


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

    def _register(self) -> None:
        payload = {
            "worker_id": self.config.worker_id,
            "machine": os.environ.get("HOSTNAME", "unknown"),
            "cli_type": self.config.cli_type,
            "account_profile": self.config.account_profile,
            "capabilities": self.config.capabilities,
            "execution_modes": self.config.execution_modes,
            "status": "idle",
            "concurrency": 1,
        }
        resp = self._http.post(f"{self.config.router_url}/register", json=payload, timeout=5)
        if resp.status_code == 409:
            logger.info("Session worker %s already registered, continuing", self.config.worker_id)
            return
        resp.raise_for_status()
        logger.info("Registered session worker %s", self.config.worker_id)

    def _start_heartbeat(self) -> None:
        def heartbeat_loop() -> None:
            url = f"{self.config.router_url}/heartbeat"
            while self._running:
                try:
                    self._http.post(url, json={"worker_id": self.config.worker_id}, timeout=3)
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
        work_dir = payload.get("working_dir", self.config.work_dir)

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

            os.makedirs(work_dir, exist_ok=True)
            cmd_base = self.config.cli_command.replace("{account_profile}", self.config.account_profile)
            tmux_session_name = self._tmux_session_name(task_id)
            self._tmux_new_session(tmux_session_name, work_dir, cmd_base)

            attach_meta, upterm_proc = self._create_attach_handle(tmux_session_name)

            session_id = self._open_session(task, tmux_session_name, work_dir, attach_meta)
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
            self._tmux_send_text(tmux_session_name, prompt)

            start = time.monotonic()
            after_seq = 0
            # Mark any messages already present so we don't replay our own initial prompt.
            msgs = self._list_session_messages(session_id, after_seq=0, limit=1000)
            if msgs:
                after_seq = max(int(m.get("seq") or 0) for m in msgs)
            last_capture = ""
            last_emitted_capture = ""

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

                new_after_seq = self._deliver_inbound_messages(session_id, tmux_session_name, after_seq)
                after_seq = max(after_seq, new_after_seq)
                captured = self._tmux_capture_pane(tmux_session_name)
                if captured:
                    last_capture = captured
                    last_emitted_capture = self._emit_cli_output_if_changed(
                        session_id, captured, last_emitted_capture
                    )
                time.sleep(self.config.session_poll_interval_s)

            final_snapshot = last_capture
            if session_id:
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
                self._close_session(session_id, state="closed")

            if self.config.auto_complete_on_exit:
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
                socket_path = f"/tmp/upterm-{tmux_session_name}.sock" if tmux_session_name else None
                self._stop_upterm(upterm_proc, socket_path)

    def _tmux_session_name(self, task_id: str) -> str:
        base = f"{self.config.tmux_session_prefix}-{self.config.cli_type}-{self.config.account_profile}-{task_id[:8]}"
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

    def _start_upterm(
        self, tmux_session: str
    ) -> tuple[subprocess.Popen | None, str | None]:
        """Launch ``upterm host`` for *tmux_session*.

        Returns ``(process, ssh_url)`` or ``(None, None)`` on failure.
        """
        socket_path = f"/tmp/upterm-{tmux_session}.sock"
        if os.path.exists(socket_path):
            try:
                os.remove(socket_path)
                logger.info("Removed stale upterm admin socket before start: %s", socket_path)
            except OSError as e:
                logger.warning("Failed to remove stale upterm admin socket %s: %s", socket_path, e)
        cmd: list[str] = [
            self.config.upterm_bin,
            "host",
            "--force-command",
            f"{self.config.tmux_bin} attach -t {tmux_session}",
            "--admin-socket",
            socket_path,
        ]
        if self.config.upterm_server:
            cmd.extend(["--server", self.config.upterm_server])
        cmd.extend(["--", "bash"])

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except (OSError, FileNotFoundError):
            logger.warning("upterm binary not found at %s", self.config.upterm_bin)
            return None, None

        target = self._poll_upterm_target(socket_path)
        if target:
            return proc, target

        logger.warning("upterm started but failed to provide session URL")
        self._stop_upterm(proc, socket_path)
        return None, None

    def _poll_upterm_target(self, socket_path: str) -> str | None:
        """Poll ``upterm session current`` until an SSH URL appears."""
        deadline = time.monotonic() + self.config.upterm_ready_timeout
        while time.monotonic() < deadline:
            try:
                result = subprocess.run(
                    [
                        self.config.upterm_bin,
                        "session",
                        "current",
                        "--admin-socket",
                        socket_path,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                if result.returncode == 0:
                    target = _parse_upterm_ssh_url(result.stdout)
                    if target:
                        return target
            except (subprocess.SubprocessError, OSError):
                pass
            time.sleep(0.5)
        return None

    @staticmethod
    def _stop_upterm(proc: subprocess.Popen, socket_path: str | None = None) -> None:
        """Terminate an upterm child process (SIGTERM then SIGKILL) and cleanup socket."""
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

        if socket_path and os.path.exists(socket_path):
            try:
                os.remove(socket_path)
                logger.info("Cleaned up upterm admin socket: %s", socket_path)
            except OSError as e:
                logger.warning("Failed to remove upterm admin socket %s: %s", socket_path, e)

    def _ack_task(self, task_id: str) -> bool:
        try:
            resp = self._http.post(
                f"{self.config.router_url}/tasks/ack",
                json={"task_id": task_id, "worker_id": self.config.worker_id},
                timeout=5,
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
                timeout=5,
            )
            logger.info("Task %s completed", task_id)
        except requests.RequestException as e:
            logger.error("Failed to report completion for task %s: %s", task_id, e)

    def _report_failure(self, task_id: str, error: str) -> None:
        logger.error("Task %s failed: %s", task_id, error)
        try:
            self._http.post(
                f"{self.config.router_url}/tasks/fail",
                json={"task_id": task_id, "worker_id": self.config.worker_id, "error": error},
                timeout=5,
            )
        except requests.RequestException as e:
            logger.error("Failed to report failure for task %s: %s", task_id, e)

    def _open_session(
        self,
        task: dict,
        tmux_session: str,
        work_dir: str,
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
            "account_profile": self.config.account_profile,
            "task_id": task["task_id"],
            "metadata": metadata,
        }
        resp = self._http.post(f"{self.config.router_url}/sessions/open", json=body, timeout=5)
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
            timeout=5,
        )
        resp.raise_for_status()

    def _close_session(self, session_id: str, *, state: str = "closed") -> None:
        resp = self._http.post(
            f"{self.config.router_url}/sessions/close",
            json={"session_id": session_id, "state": state},
            timeout=5,
        )
        resp.raise_for_status()

    def _list_session_messages(self, session_id: str, *, after_seq: int, limit: int = 200) -> list[dict]:
        resp = self._http.get(
            f"{self.config.router_url}/sessions/messages",
            params={"session_id": session_id, "after_seq": after_seq, "limit": limit},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("messages", [])

    def _deliver_inbound_messages(self, session_id: str, tmux_session: str, after_seq: int) -> int:
        try:
            messages = self._list_session_messages(session_id, after_seq=after_seq, limit=200)
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
