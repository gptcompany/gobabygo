"""Mesh router HTTP server.

Exposes router endpoints over HTTP for worker communication.
Uses stdlib ThreadingHTTPServer — zero external dependencies for serving.
Integrates with systemd via sd_notify for Type=notify watchdog support.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pydantic import ValidationError

from src.router.db import RouterDB
from src.router.heartbeat import HeartbeatManager
from src.router.longpoll import LongPollRegistry
from src.router.metrics import MeshMetrics
from src.router.models import (
    HandoffRepoError,
    HandoffRoleError,
    NotificationLedgerEntry,
    NotificationLedgerWriteRequest,
    Session,
    SessionMessage,
    Task,
    TaskCreateRequest,
    TaskStatus,
    ThreadCreateRequest,
    ThreadStepRequest,
    Worker,
)
from src.router.recovery import recover_on_startup
from src.router.scheduler import Scheduler
from src.router.topology import TopologyError, load_topology
from src.router.thread import add_step, compute_thread_status, create_thread, get_thread_context
from src.router.verifier import VerifierGate
from src.router.worker_manager import WorkerManager

logger = logging.getLogger("mesh.server")
_CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError)


class MeshRouterHandler(BaseHTTPRequestHandler):
    """HTTP request handler for mesh router endpoints."""
    _verifier_gate_lock = threading.Lock()

    def do_GET(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/health":
                self._handle_health()
            elif path == "/metrics":
                self._handle_metrics()
            elif path == "/tasks/next":
                self._handle_task_poll()
            elif path == "/tasks":
                self._handle_list_tasks()
            elif path.startswith("/tasks/"):
                suffix = path[len("/tasks/"):]
                parts = suffix.split("/")
                task_id = parts[0]
                if not task_id:
                    self._send_json(404, {"error": "not_found"})
                elif len(parts) == 1 and task_id != "next":
                    self._handle_get_task(task_id)
                elif len(parts) == 2 and parts[1] == "pending-fixes":
                    self._handle_task_pending_fixes(task_id)
                else:
                    self._send_json(404, {"error": "not_found"})
            elif path == "/workers":
                self._handle_list_workers()
            elif path == "/sessions":
                self._handle_list_sessions()
            elif path == "/sessions/messages":
                self._handle_list_session_messages()
            elif path.startswith("/sessions/"):
                session_id = path[len("/sessions/"):]
                if session_id:
                    self._handle_get_session(session_id)
                else:
                    self._send_json(404, {"error": "not_found"})
            elif path.startswith("/workers/"):
                worker_id = path[len("/workers/"):]
                if worker_id:
                    self._handle_get_worker(worker_id)
                else:
                    self._send_json(404, {"error": "not_found"})
            elif path == "/threads":
                self._handle_list_threads()
            elif path == "/notifications":
                self._handle_list_notifications()
            elif path.startswith("/threads/"):
                parts = path[len("/threads/"):].split("/")
                thread_id = parts[0]
                if len(parts) == 1:
                    self._handle_get_thread(thread_id)
                elif len(parts) == 2 and parts[1] == "status":
                    self._handle_thread_status(thread_id)
                elif len(parts) == 2 and parts[1] == "context":
                    self._handle_thread_context(thread_id)
                else:
                    self._send_json(404, {"error": "not_found"})
            else:
                self._send_json(404, {"error": "not_found"})

        except _CLIENT_DISCONNECT_ERRORS:
            logger.info("Client disconnected during GET %s", self.path)
        except Exception as e:
            self._send_json(500, {"error": "internal_error", "details": str(e)})

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            if path == "/tasks":
                self._handle_create_task()
            elif path == "/events":
                self._handle_events()
            elif path == "/heartbeat":
                self._handle_heartbeat()
            elif path == "/register":
                self._handle_register()
            elif path == "/sessions/open":
                self._handle_open_session()
            elif path == "/sessions/send":
                self._handle_send_session_message()
            elif path == "/sessions/send-key":
                self._handle_send_session_key()
            elif path == "/sessions/resize":
                self._handle_resize_session()
            elif path == "/sessions/signal":
                self._handle_signal_session()
            elif path == "/sessions/close":
                self._handle_close_session()
            elif path == "/tasks/ack":
                self._handle_task_ack()
            elif path == "/tasks/complete":
                self._handle_task_complete()
            elif path == "/tasks/fail":
                self._handle_task_fail()
            elif path == "/tasks/cancel":
                self._handle_task_cancel()
            elif path == "/tasks/admin-fail":
                self._handle_task_admin_fail()
            elif path == "/tasks/review/approve":
                self._handle_task_review_approve()
            elif path == "/tasks/review/reject":
                self._handle_task_review_reject()
            elif path == "/threads":
                self._handle_create_thread()
            elif path == "/notifications":
                self._handle_create_notification()
            elif path.startswith("/threads/") and path.endswith("/steps"):
                thread_id = path[len("/threads/"):-len("/steps")]
                if thread_id:
                    self._handle_add_step(thread_id)
                else:
                    self._send_json(404, {"error": "not_found"})
            elif path.endswith("/drain") and path.startswith("/workers/"):
                # Extract worker_id from /workers/<id>/drain
                worker_id = path[len("/workers/"):-len("/drain")]
                if worker_id:
                    self._handle_drain_worker(worker_id)
                else:
                    self._send_json(404, {"error": "not_found"})
            elif path.endswith("/deregister") and path.startswith("/workers/"):
                worker_id = path[len("/workers/"):-len("/deregister")]
                if worker_id:
                    self._handle_deregister_worker(worker_id)
                else:
                    self._send_json(404, {"error": "not_found"})
            else:
                self._send_json(404, {"error": "not_found"})
        except _CLIENT_DISCONNECT_ERRORS:
            logger.info("Client disconnected during POST %s", self.path)
        except Exception as e:
            self._send_json(500, {"error": "internal_error", "details": str(e)})

    # --- Endpoint implementations ---

    def _handle_health(self) -> None:
        """GET /health — liveness check. No auth (internal wg0 network only)."""
        state = self.server.router_state  # type: ignore[attr-defined]
        db: RouterDB = state["db"]
        worker_count = len(db.list_workers())
        queue_depth = db.count_tasks_by_status("queued")
        self._send_json(200, {
            "status": "healthy",
            "workers": worker_count,
            "queue_depth": queue_depth,
            "uptime_s": round(
                (datetime.now(timezone.utc) - state["start_time"]).total_seconds(), 1
            ),
        })

    def _handle_metrics(self) -> None:
        """GET /metrics — Prometheus metrics endpoint. No auth (internal wg0 network only)."""
        state = self.server.router_state  # type: ignore[attr-defined]
        db: RouterDB = state["db"]
        metrics: MeshMetrics = state["metrics"]
        uptime_s = (datetime.now(timezone.utc) - state["start_time"]).total_seconds()
        metrics.collect_from_db(db, uptime_s)
        body = metrics.generate()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_task_poll(self) -> None:
        """GET /tasks/next?worker_id=X -- long-poll for next assigned task.

        Blocks until a task is dispatched to this worker or timeout expires.
        Returns 200 + task JSON on task, 204 on timeout, 409 on duplicate poll.
        """
        if not self._check_auth():
            return
        query = parse_qs(urlparse(self.path).query)
        worker_id = query.get("worker_id", [None])[0]
        if not worker_id:
            self._send_json(400, {"error": "missing worker_id"})
            return

        state = self.server.router_state  # type: ignore[attr-defined]
        db: RouterDB = state["db"]
        registry: LongPollRegistry = state["longpoll_registry"]
        timeout_s: float = state["longpoll_timeout"]
        metrics: MeshMetrics = state["metrics"]

        logger.info("poll_start worker_id=%s", worker_id)
        start = time.monotonic()

        result = registry.wait_for_task(worker_id, timeout_s, db)
        duration = time.monotonic() - start

        metrics.longpoll_wait_seconds.observe(duration)
        metrics.longpoll_waiting_workers.set(registry.waiting_count())

        if result.conflict:
            metrics.longpoll_total.labels(result="conflict").inc()
            logger.info(
                "poll_complete worker_id=%s result=conflict duration=%.1fs",
                worker_id, duration,
            )
            self._send_json(409, {"error": "duplicate_poll"})
        elif result.task is not None:
            metrics.longpoll_total.labels(result="task").inc()
            logger.info(
                "poll_complete worker_id=%s result=task duration=%.1fs",
                worker_id, duration,
            )
            task_dict = result.task.model_dump(mode="json")
            # Runtime thread context enrichment
            if result.task.thread_id and result.task.step_index and result.task.step_index > 0:

                thread_ctx = get_thread_context(db, result.task.thread_id, result.task.step_index)
                task_dict["thread_context"] = thread_ctx
            self._send_json(200, task_dict)
        else:
            metrics.longpoll_total.labels(result="timeout").inc()
            logger.info(
                "poll_complete worker_id=%s result=timeout duration=%.1fs",
                worker_id, duration,
            )
            self._send_json(204, None)

    def _handle_create_task(self) -> None:
        """POST /tasks — create a new task for dispatch."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        state = self.server.router_state  # type: ignore[attr-defined]
        metrics: MeshMetrics = state["metrics"]
        default_mode = str(state.get("default_execution_mode", "batch")).strip()
        if "execution_mode" not in data and default_mode in {"batch", "session"}:
            data["execution_mode"] = default_mode
        if self._enforce_session_only_and_reject_if_needed(data):
            metrics.tasks_create_errors.labels(reason="session_only").inc()
            return

        try:
            request = TaskCreateRequest(**data)
        except (ValidationError, ValueError, TypeError) as e:
            metrics.tasks_create_errors.labels(reason="invalid").inc()
            self._send_json(400, {"error": "invalid_task", "detail": str(e)})
            return

        task = Task(**request.model_dump())

        db: RouterDB = state["db"]
        try:
            db.insert_task(task)
        except sqlite3.IntegrityError:
            metrics.tasks_create_errors.labels(reason="duplicate").inc()
            self._send_json(409, {"error": "duplicate_task", "detail": "idempotency_key already exists"})
            return

        # Eager dispatch (best-effort, periodic loop is backup)
        scheduler: Scheduler = state["scheduler"]
        try:
            scheduler.dispatch()
        except Exception as e:
            logger.warning("Eager dispatch failed: %s", e)

        logger.info("task_created id=%s title=%s", task.task_id, task.title)
        metrics.tasks_created.inc()
        self._send_json(201, {"status": "created", "task_id": task.task_id})

    def _handle_events(self) -> None:
        """POST /events — receive CloudEvent JSON from bridge.

        Uses InProcessTransport to parse CloudEvent and write TaskEvent to DB.
        """
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            json.loads(body)  # Validate JSON
            transport = self.server.router_state["transport"]  # type: ignore[attr-defined]
            success = transport.send(body)
            if success:
                self._send_json(202, {"status": "accepted"})
            else:
                self._send_json(409, {"status": "duplicate_or_error"})
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})

    def _handle_heartbeat(self) -> None:
        """POST /heartbeat — receive worker heartbeat."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
            worker_id = data.get("worker_id")
            if not worker_id:
                self._send_json(400, {"error": "missing worker_id"})
                return
            heartbeat_mgr: HeartbeatManager = self.server.router_state["heartbeat"]  # type: ignore[attr-defined]
            result = heartbeat_mgr.receive_heartbeat(worker_id)
            self._send_json(200, result)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})

    def _handle_register(self) -> None:
        """POST /register — worker registration via WorkerManager."""
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return
        try:
            worker = Worker(**data)
        except (ValidationError, ValueError, TypeError, KeyError) as e:
            self._send_json(400, {"error": f"invalid_worker_data: {type(e).__name__}"})
            return

        # Extract bearer token (case-insensitive scheme)
        auth_header = self.headers.get("Authorization", "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()

        wm: WorkerManager = self.server.router_state["worker_manager"]  # type: ignore[attr-defined]
        success, message = wm.register_worker(worker, token)

        if not success:
            if message == "invalid_token":
                self._send_json(401, {"error": "invalid_token"})
            elif message == "account_in_use":
                self._send_json(409, {"error": "account_in_use"})
            else:
                self._send_json(400, {"error": message})
            return

        status_code = 200 if message == "re-registered" else 201
        self._send_json(status_code, {"status": "registered", "worker_id": worker.worker_id})

    def _handle_task_ack(self) -> None:
        """POST /tasks/ack — worker acknowledges assigned task (assigned -> running)."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
            task_id = data["task_id"]
            worker_id = data["worker_id"]
            scheduler: Scheduler = self.server.router_state["scheduler"]  # type: ignore[attr-defined]
            ok = scheduler.ack_task(task_id, worker_id)
            if ok:
                self._send_json(200, {"status": "acknowledged"})
            else:
                self._send_json(409, {"error": "transition_failed"})
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
        except KeyError as e:
            self._send_json(400, {"error": f"missing_field: {e}"})

    def _handle_task_complete(self) -> None:
        """POST /tasks/complete — worker reports task completion."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
            task_id = data["task_id"]
            worker_id = data["worker_id"]
            result = data.get("result")  # None if not present (backward compat)
            if result is not None and not isinstance(result, dict):
                self._send_json(400, {"error": "result_must_be_object"})
                return
            state = self.server.router_state  # type: ignore[attr-defined]
            db: RouterDB = state["db"]
            scheduler: Scheduler = state["scheduler"]
            # Get task before completion to calculate duration
            task = db.get_task(task_id)
            ok = scheduler.complete_task(task_id, worker_id, result=result)
            if ok:
                # Observe task duration for Prometheus Summary
                if task and task.created_at:
                    try:
                        created = datetime.fromisoformat(task.created_at)
                        duration_s = (datetime.now(timezone.utc) - created).total_seconds()
                        metrics: MeshMetrics = state["metrics"]
                        metrics.observe_task_duration(duration_s)
                    except (ValueError, TypeError):
                        pass  # Skip duration if timestamp parse fails
                self._send_json(200, {"status": "completed"})
            else:
                self._send_json(409, {"error": "transition_failed"})
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
        except KeyError as e:
            self._send_json(400, {"error": f"missing_field: {e}"})

    def _handle_task_fail(self) -> None:
        """POST /tasks/fail — worker reports task failure."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
            task_id = data["task_id"]
            worker_id = data["worker_id"]
            error = data.get("error", "unknown")
            scheduler: Scheduler = self.server.router_state["scheduler"]  # type: ignore[attr-defined]
            ok = scheduler.report_failure(task_id, worker_id, reason=error)
            if ok:
                self._send_json(200, {"status": "failed_recorded"})
            else:
                self._send_json(409, {"error": "transition_failed"})
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
        except KeyError as e:
            self._send_json(400, {"error": f"missing_field: {e}"})

    def _handle_task_cancel(self) -> None:
        """POST /tasks/cancel — admin cancel a non-running task."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
            task_id = data["task_id"]
            reason = str(data.get("reason") or "admin_cancel").strip()
            scheduler: Scheduler = self.server.router_state["scheduler"]  # type: ignore[attr-defined]
            ok, detail = scheduler.admin_cancel_task(task_id, reason=reason)
            if ok:
                self._send_json(200, {"status": "canceled", "task_id": task_id})
            elif detail == "not_found":
                self._send_json(404, {"error": "not_found"})
            else:
                self._send_json(409, {"error": "cancel_failed", "detail": detail})
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
        except KeyError as e:
            self._send_json(400, {"error": f"missing_field: {e}"})

    def _handle_task_admin_fail(self) -> None:
        """POST /tasks/admin-fail — admin fail a non-running task."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
            task_id = data["task_id"]
            reason = str(data.get("reason") or "admin_fail").strip()
            scheduler: Scheduler = self.server.router_state["scheduler"]  # type: ignore[attr-defined]
            ok, detail = scheduler.admin_fail_task(task_id, reason=reason)
            if ok:
                self._send_json(200, {"status": "failed", "task_id": task_id})
            elif detail == "not_found":
                self._send_json(404, {"error": "not_found"})
            else:
                self._send_json(409, {"error": "admin_fail_failed", "detail": detail})
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
        except KeyError as e:
            self._send_json(400, {"error": f"missing_field: {e}"})

    def _get_verifier_gate(self) -> VerifierGate:
        """Return verifier gate from router_state, lazily initializing when absent."""
        state = self.server.router_state  # type: ignore[attr-defined]
        with self._verifier_gate_lock:
            gate = state.get("verifier_gate")
            if gate is None:
                gate = VerifierGate()
                state["verifier_gate"] = gate
            return gate

    def _handle_task_review_approve(self) -> None:
        """POST /tasks/review/approve — approve task in review state."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
            task_id = data["task_id"]
            verifier_id = data["verifier_id"]
            if not isinstance(verifier_id, str) or not verifier_id.strip():
                self._send_json(400, {"error": "invalid_verifier_id"})
                return
            state = self.server.router_state  # type: ignore[attr-defined]
            db: RouterDB = state["db"]
            if db.get_task(task_id) is None:
                self._send_json(404, {"error": "not_found"})
                return
            gate = self._get_verifier_gate()
            result = gate.approve_task(db, task_id, verifier_id.strip())
            if result.success:
                self._send_json(200, {
                    "status": "approved",
                    "task_id": task_id,
                    "event_id": result.event_id,
                })
            else:
                self._send_json(
                    409,
                    {
                        "error": "review_approval_failed",
                        "detail": result.reason or "transition_failed",
                    },
                )
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
        except KeyError as e:
            self._send_json(400, {"error": f"missing_field: {e}"})

    def _handle_task_review_reject(self) -> None:
        """POST /tasks/review/reject — reject task in review, create fix/escalate."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
            task_id = data["task_id"]
            verifier_id = data["verifier_id"]
            reason = data["reason"]
            if not isinstance(verifier_id, str) or not verifier_id.strip():
                self._send_json(400, {"error": "invalid_verifier_id"})
                return
            if not isinstance(reason, str) or not reason.strip():
                self._send_json(400, {"error": "invalid_reason"})
                return
            state = self.server.router_state  # type: ignore[attr-defined]
            db: RouterDB = state["db"]
            task = db.get_task(task_id)
            if task is None:
                self._send_json(404, {"error": "not_found"})
                return
            if task.status != TaskStatus.review:
                self._send_json(
                    409,
                    {
                        "error": "review_rejection_failed",
                        "detail": f"task not in review (status={task.status.value})",
                    },
                )
                return
            gate = self._get_verifier_gate()
            fix_task = gate.reject_task(
                db,
                task_id,
                verifier_id=verifier_id.strip(),
                reason=reason.strip(),
            )
            task = db.get_task(task_id)
            if fix_task is not None:
                self._send_json(200, {
                    "status": "rejected",
                    "task_id": task_id,
                    "fix_task_id": fix_task.task_id,
                    "rejection_count": task.rejection_count if task else None,
                })
                return

            if task is not None and task.status == TaskStatus.failed:
                self._send_json(200, {
                    "status": "rejected_escalated",
                    "task_id": task_id,
                    "rejection_count": task.rejection_count,
                })
                return

            self._send_json(200, {
                "status": "rejected_no_action",
                "task_id": task_id,
                "rejection_count": task.rejection_count if task else None,
            })
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
        except KeyError as e:
            self._send_json(400, {"error": f"missing_field: {e}"})

    def _handle_task_pending_fixes(self, task_id: str) -> None:
        """GET /tasks/<id>/pending-fixes — check if non-terminal fix tasks exist."""
        if not self._check_auth():
            return
        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        task = db.get_task(task_id)
        if task is None:
            self._send_json(404, {"error": "not_found"})
            return
        gate = self._get_verifier_gate()
        pending = gate.has_pending_fixes(db, task_id)
        self._send_json(200, {"task_id": task_id, "has_pending_fixes": pending})

    # --- Task read endpoints ---

    def _handle_get_task(self, task_id: str) -> None:
        """GET /tasks/<id> — fetch a single task by ID."""
        # TODO: enforce CommunicationPolicy.can_view_all_tasks() when per-token roles exist
        if not self._check_auth():
            return
        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        task = db.get_task(task_id)
        if task is None:
            self._send_json(404, {"error": "not_found"})
            return
        self._send_json(200, task.model_dump(mode="json"))

    def _handle_list_tasks(self) -> None:
        """GET /tasks?status=...&limit=N — list tasks with optional filters."""
        # TODO: enforce CommunicationPolicy.can_view_all_tasks() when per-token roles exist
        if not self._check_auth():
            return
        query = parse_qs(urlparse(self.path).query)
        status = query.get("status", [None])[0]
        limit_raw = query.get("limit", ["100"])[0]
        try:
            limit = max(1, min(1000, int(limit_raw)))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "invalid_limit"})
            return

        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        tasks = db.list_tasks(status=status, limit=limit)
        self._send_json(200, {"tasks": [t.model_dump(mode="json") for t in tasks]})

    # --- Session bus endpoints ---

    def _handle_open_session(self) -> None:
        """POST /sessions/open — create a persisted interactive session record."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        try:
            session = Session(**data)
        except (ValidationError, ValueError, TypeError) as e:
            self._send_json(400, {"error": "invalid_session", "detail": str(e)})
            return

        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        try:
            db.insert_session(session)
        except sqlite3.IntegrityError:
            self._send_json(409, {"error": "duplicate_session"})
            return
        if session.task_id:
            # Link task -> session for auditability / later operator attach workflows.
            db.update_task_fields(session.task_id, {"session_id": session.session_id})

        self._send_json(201, {"status": "opened", "session": session.model_dump(mode="json")})

    def _handle_send_session_message(self) -> None:
        """POST /sessions/send — append a persisted message to a session."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        try:
            message = SessionMessage(**data)
        except (ValidationError, ValueError, TypeError) as e:
            self._send_json(400, {"error": "invalid_session_message", "detail": str(e)})
            return

        state = self.server.router_state  # type: ignore[attr-defined]
        db: RouterDB = state["db"]
        session = db.get_session(message.session_id)
        if session is None:
            self._send_json(404, {"error": "session_not_found"})
            return
        session_state = session.state.value if hasattr(session.state, "value") else str(session.state)
        if session_state in {"closed", "errored"}:
            self._send_json(409, {"error": "session_closed"})
            return

        try:
            seq = db.append_session_message(message)
        except sqlite3.IntegrityError:
            self._send_json(404, {"error": "session_not_found"})
            return

        self._send_json(201, {"status": "accepted", "seq": seq, "session_id": message.session_id})

    def _append_session_control_message(
        self,
        *,
        session_id: str,
        control: str,
        metadata: dict,
        content: str = "",
    ) -> bool:
        """Append a validated control message to an open session."""
        state = self.server.router_state  # type: ignore[attr-defined]
        db: RouterDB = state["db"]
        session = db.get_session(session_id)
        if session is None:
            self._send_json(404, {"error": "session_not_found"})
            return False
        session_state = session.state.value if hasattr(session.state, "value") else str(session.state)
        if session_state in {"closed", "errored"}:
            self._send_json(409, {"error": "session_closed"})
            return False

        message = SessionMessage(
            session_id=session_id,
            direction="in",
            role="operator",
            content=content,
            metadata={"control": control, **metadata},
        )
        try:
            seq = db.append_session_message(message)
        except sqlite3.IntegrityError:
            self._send_json(404, {"error": "session_not_found"})
            return False
        self._send_json(
            201,
            {
                "status": "accepted",
                "seq": seq,
                "session_id": session_id,
                "control": control,
            },
        )
        return True

    def _handle_send_session_key(self) -> None:
        """POST /sessions/send-key — send a tmux key event via session bus."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        session_id = str(data.get("session_id", "")).strip()
        key = str(data.get("key", "")).strip()
        repeat_raw = data.get("repeat", 1)
        if not session_id:
            self._send_json(400, {"error": "missing_session_id"})
            return
        if not key:
            self._send_json(400, {"error": "missing_key"})
            return
        try:
            repeat = int(repeat_raw)
        except (TypeError, ValueError):
            self._send_json(400, {"error": "invalid_repeat"})
            return
        if repeat < 1 or repeat > 50:
            self._send_json(400, {"error": "invalid_repeat"})
            return

        self._append_session_control_message(
            session_id=session_id,
            control="send_key",
            metadata={"key": key, "repeat": repeat},
        )

    def _handle_resize_session(self) -> None:
        """POST /sessions/resize — request tmux window resize via session bus."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        session_id = str(data.get("session_id", "")).strip()
        if not session_id:
            self._send_json(400, {"error": "missing_session_id"})
            return
        try:
            cols = int(data.get("cols"))
            rows = int(data.get("rows"))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "invalid_resize"})
            return
        if cols < 20 or cols > 500 or rows < 5 or rows > 200:
            self._send_json(400, {"error": "invalid_resize"})
            return

        self._append_session_control_message(
            session_id=session_id,
            control="resize",
            metadata={"cols": cols, "rows": rows},
        )

    def _handle_signal_session(self) -> None:
        """POST /sessions/signal — request control signal for active session."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        session_id = str(data.get("session_id", "")).strip()
        signal_name = str(data.get("signal", "")).strip().lower()
        if not session_id:
            self._send_json(400, {"error": "missing_session_id"})
            return
        if signal_name not in {"interrupt", "terminate"}:
            self._send_json(400, {"error": "invalid_signal"})
            return

        self._append_session_control_message(
            session_id=session_id,
            control="signal",
            metadata={"signal": signal_name},
        )

    def _handle_close_session(self) -> None:
        """POST /sessions/close — mark a session as closed/errored."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        session_id = data.get("session_id")
        if not session_id:
            self._send_json(400, {"error": "missing_session_id"})
            return
        requested_state = str(data.get("state", "closed"))
        if requested_state not in {"closed", "errored"}:
            self._send_json(400, {"error": "invalid_state"})
            return

        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        ok = db.update_session(session_id, {"state": requested_state})
        if not ok:
            self._send_json(404, {"error": "session_not_found"})
            return

        self._send_json(200, {"status": requested_state, "session_id": session_id})

    def _handle_list_sessions(self) -> None:
        """GET /sessions — list persisted sessions."""
        if not self._check_auth():
            return
        query = parse_qs(urlparse(self.path).query)
        state_q = query.get("state", [None])[0]
        worker_id = query.get("worker_id", [None])[0]
        limit_raw = query.get("limit", ["200"])[0]
        try:
            limit = max(1, min(1000, int(limit_raw)))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "invalid_limit"})
            return

        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        sessions = db.list_sessions(state=state_q, worker_id=worker_id, limit=limit)
        self._send_json(200, {"sessions": [s.model_dump(mode="json") for s in sessions]})

    def _handle_get_session(self, session_id: str) -> None:
        """GET /sessions/<id> — fetch a single session."""
        if not self._check_auth():
            return
        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        session = db.get_session(session_id)
        if session is None:
            self._send_json(404, {"error": "not_found"})
            return
        self._send_json(200, session.model_dump(mode="json"))

    def _handle_list_session_messages(self) -> None:
        """GET /sessions/messages?session_id=...&after_seq=N&limit=M."""
        if not self._check_auth():
            return
        query = parse_qs(urlparse(self.path).query)
        session_id = query.get("session_id", [None])[0]
        if not session_id:
            self._send_json(400, {"error": "missing_session_id"})
            return
        try:
            after_seq = int(query.get("after_seq", ["0"])[0])
            limit = max(1, min(1000, int(query.get("limit", ["200"])[0])))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "invalid_pagination"})
            return

        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        if db.get_session(session_id) is None:
            self._send_json(404, {"error": "session_not_found"})
            return

        messages = db.list_session_messages(session_id, after_seq=after_seq, limit=limit)
        self._send_json(200, {"messages": [m.model_dump(mode="json") for m in messages]})

    def _handle_create_notification(self) -> None:
        """POST /notifications — persist notification delivery attempt."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        try:
            # Use NotificationLedgerWriteRequest for strict input validation
            write_req = NotificationLedgerWriteRequest(**data)
            # Convert to storage/read model
            entry = NotificationLedgerEntry(**write_req.model_dump())
        except (ValidationError, ValueError, TypeError) as e:
            self._send_json(400, {"error": "invalid_notification", "detail": str(e)})
            return

        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        created, notification_id = db.insert_notification_ledger_once(entry)
        if not created:
            self._send_json(
                200,
                {"status": "duplicate", "trace_id": entry.trace_id, "room_id": entry.room_id},
            )
            return

        self._send_json(
            201,
            {"status": "created", "notification_id": notification_id, "trace_id": entry.trace_id},
        )

    def _handle_list_notifications(self) -> None:
        """GET /notifications?trace_id=...&status=...&limit=N."""
        if not self._check_auth():
            return
        query = parse_qs(urlparse(self.path).query)
        trace_id = query.get("trace_id", [None])[0]
        status = query.get("status", [None])[0]
        limit_raw = query.get("limit", ["200"])[0]
        try:
            limit = max(1, min(1000, int(limit_raw)))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "invalid_limit"})
            return

        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        entries = db.list_notification_ledger(trace_id=trace_id, status=status, limit=limit)
        self._send_json(200, {"notifications": [e.model_dump(mode="json") for e in entries]})

    # --- Worker management endpoints ---

    def _handle_list_workers(self) -> None:
        """GET /workers -- list all workers with embedded running tasks."""
        if not self._check_auth():
            return
        state = self.server.router_state  # type: ignore[attr-defined]
        db: RouterDB = state["db"]
        now = datetime.now(timezone.utc)

        workers = db.list_workers()
        result = []
        for w in workers:
            running = db.get_tasks_by_worker(w.worker_id, status="running")
            assigned = db.get_tasks_by_worker(w.worker_id, status="assigned")
            tasks = running + assigned
            task_list = []
            for t in tasks:
                age_s = 0.0
                if t.created_at:
                    try:
                        created = datetime.fromisoformat(t.created_at)
                        age_s = round((now - created).total_seconds(), 1)
                    except (ValueError, TypeError):
                        pass
                task_list.append({
                    "task_id": t.task_id,
                    "status": t.status.value,
                    "created_at": t.created_at,
                    "age_s": age_s,
                })
            result.append({
                "worker_id": w.worker_id,
                "machine": w.machine,
                "cli_type": w.cli_type.value if hasattr(w.cli_type, "value") else w.cli_type,
                "status": w.status,
                "last_heartbeat": w.last_heartbeat,
                "idle_since": w.idle_since,
                "stale_since": w.stale_since,
                "running_tasks": task_list,
            })
        self._send_json(200, {"workers": result})

    def _handle_get_worker(self, worker_id: str) -> None:
        """GET /workers/<id> -- single worker detail with running tasks."""
        if not self._check_auth():
            return
        state = self.server.router_state  # type: ignore[attr-defined]
        db: RouterDB = state["db"]
        now = datetime.now(timezone.utc)

        w = db.get_worker(worker_id)
        if w is None:
            self._send_json(404, {"error": "not_found"})
            return

        running = db.get_tasks_by_worker(worker_id, status="running")
        assigned = db.get_tasks_by_worker(worker_id, status="assigned")
        tasks = running + assigned
        task_list = []
        for t in tasks:
            age_s = 0.0
            if t.created_at:
                try:
                    created = datetime.fromisoformat(t.created_at)
                    age_s = round((now - created).total_seconds(), 1)
                except (ValueError, TypeError):
                    pass
            task_list.append({
                "task_id": t.task_id,
                "status": t.status.value,
                "created_at": t.created_at,
                "age_s": age_s,
            })
        result = {
            "worker_id": w.worker_id,
            "machine": w.machine,
            "cli_type": w.cli_type.value if hasattr(w.cli_type, "value") else w.cli_type,
            "status": w.status,
            "last_heartbeat": w.last_heartbeat,
            "idle_since": w.idle_since,
            "stale_since": w.stale_since,
            "running_tasks": task_list,
        }
        self._send_json(200, result)

    def _handle_drain_worker(self, worker_id: str) -> None:
        """POST /workers/<id>/drain -- initiate graceful drain."""
        if not self._check_auth():
            return
        state = self.server.router_state  # type: ignore[attr-defined]
        wm: WorkerManager = state["worker_manager"]

        ok, message = wm.drain_worker(worker_id)
        if not ok:
            if message == "not_found":
                self._send_json(404, {"error": "not_found"})
            elif message == "invalid_state":
                self._send_json(409, {"error": "invalid_state", "detail": "Worker is stale or offline, cannot drain"})
            else:
                self._send_json(409, {"error": message})
            return

        # 202 Accepted: drain initiated (or completed immediately for idle workers)
        self._send_json(202, {"status": message, "worker_id": worker_id})

    def _handle_deregister_worker(self, worker_id: str) -> None:
        """POST /workers/<id>/deregister -- immediately retire a worker."""
        if not self._check_auth():
            return
        state = self.server.router_state  # type: ignore[attr-defined]
        wm: WorkerManager = state["worker_manager"]

        ok, message = wm.deregister_worker(worker_id)
        if not ok:
            if message == "not_found":
                self._send_json(404, {"error": "not_found"})
            else:
                self._send_json(409, {"error": message})
            return

        self._send_json(200, {"status": message, "worker_id": worker_id})

    # --- Thread endpoints ---

    def _handle_list_threads(self) -> None:
        """GET /threads — list threads with optional filters."""
        if not self._check_auth():
            return
        query = parse_qs(urlparse(self.path).query)
        status = query.get("status", [None])[0]
        name = query.get("name", [None])[0]
        limit_raw = query.get("limit", ["50"])[0]
        try:
            limit = max(1, min(1000, int(limit_raw)))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "invalid_limit"})
            return

        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        threads = db.list_threads(status=status, name=name, limit=limit)
        self._send_json(200, {"threads": [t.model_dump(mode="json") for t in threads]})

    def _handle_get_thread(self, thread_id: str) -> None:
        """GET /threads/<id> — fetch a single thread."""
        if not self._check_auth():
            return
        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        thread = db.get_thread(thread_id)
        if thread is None:
            self._send_json(404, {"error": "not_found"})
            return
        self._send_json(200, thread.model_dump(mode="json"))

    def _handle_thread_status(self, thread_id: str) -> None:
        """GET /threads/<id>/status — thread + all steps with status."""
        if not self._check_auth():
            return
        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        thread = db.get_thread(thread_id)
        if thread is None:
            self._send_json(404, {"error": "not_found"})
            return

        computed_status = compute_thread_status(db, thread_id)
        thread_dict = thread.model_dump(mode="json")
        thread_dict["status"] = computed_status.value

        steps = db.list_thread_steps(thread_id)
        steps_list = []
        for s in steps:
            result_summary = None
            if s.result:
                raw = str(s.result)
                result_summary = raw[:100] + "..." if len(raw) > 100 else raw
            has_handoff = bool(s.payload and s.payload.get("handoff"))
            steps_list.append({
                "step_index": s.step_index,
                "task_id": s.task_id,
                "status": s.status.value if hasattr(s.status, "value") else str(s.status),
                "repo": s.repo or "",
                "title": s.title,
                "assigned_worker": s.assigned_worker or "",
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "attempt": s.attempt,
                "on_failure": s.on_failure,
                "result_summary": result_summary,
                "has_handoff": has_handoff,
            })

        self._send_json(200, {"thread": thread_dict, "steps": steps_list})

    def _handle_thread_context(self, thread_id: str) -> None:
        """GET /threads/<id>/context — aggregated results from completed steps."""
        if not self._check_auth():
            return
        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        thread = db.get_thread(thread_id)
        if thread is None:
            self._send_json(404, {"error": "not_found"})
            return

        context = get_thread_context(db, thread_id, up_to_step_index=999)
        self._send_json(200, {"thread_id": thread_id, "context": context})

    def _handle_create_thread(self) -> None:
        """POST /threads — create a new thread."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        try:
            request = ThreadCreateRequest(**data)
        except (ValidationError, ValueError, TypeError) as e:
            self._send_json(400, {"error": "invalid_thread", "detail": str(e)})
            return

        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        try:
            thread = create_thread(db, request.name)
        except ValueError as e:
            self._send_json(409, {"error": "duplicate_thread_name", "detail": str(e)})
            return
        logger.info("thread_created id=%s name=%s", thread.thread_id, thread.name)
        self._send_json(201, {
            "status": "created",
            "thread_id": thread.thread_id,
            "name": thread.name,
        })

    def _handle_add_step(self, thread_id: str) -> None:
        """POST /threads/<id>/steps — add a step to a thread."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
            return

        state = self.server.router_state  # type: ignore[attr-defined]
        default_mode = str(state.get("default_execution_mode", "batch")).strip()
        if "execution_mode" not in data and default_mode in {"batch", "session"}:
            data["execution_mode"] = default_mode
        if self._enforce_session_only_and_reject_if_needed(data):
            return

        try:
            step_request = ThreadStepRequest(**data)
        except (ValidationError, ValueError, TypeError) as e:
            self._send_json(400, {"error": "invalid_step", "detail": str(e)})
            return

        db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
        topology = self.server.router_state.get("topology")  # type: ignore[attr-defined]
        try:
            task = add_step(db, thread_id, step_request, topology=topology)
        except HandoffRoleError as e:
            self._send_json(403, {"error": "handoff_role_required", "detail": str(e)})
            return
        except HandoffRepoError as e:
            self._send_json(400, {"error": "invalid_handoff_repo", "detail": str(e)})
            return
        except ValidationError as e:
            self._send_json(400, {"error": "invalid_handoff", "detail": str(e)})
            return
        except ValueError as e:
            detail = str(e)
            if detail.endswith("not found"):
                self._send_json(404, {"error": "not_found", "detail": detail})
            else:
                self._send_json(409, {"error": "invalid_step_order", "detail": detail})
            return
        except sqlite3.IntegrityError:
            self._send_json(409, {"error": "duplicate_step_index"})
            return

        logger.info(
            "thread_step_added thread=%s step=%d task=%s",
            thread_id, step_request.step_index, task.task_id,
        )
        self._send_json(201, {
            "status": "created",
            "task_id": task.task_id,
            "step_index": step_request.step_index,
        })

    # --- Helpers ---

    def _check_auth(self) -> bool:
        """Validate bearer token if configured."""
        expected = self.server.router_state.get("auth_token")  # type: ignore[attr-defined]
        if not expected:
            return True
        auth_header = self.headers.get("Authorization", "")
        if auth_header == f"Bearer {expected}":
            return True
        self._send_json(401, {"error": "unauthorized"})
        return False

    def _enforce_session_only_and_reject_if_needed(self, data: dict) -> bool:
        """Reject non-session tasks/steps when session-only mode is enabled."""
        state = self.server.router_state  # type: ignore[attr-defined]
        enforce = bool(state.get("enforce_session_only", False))
        if not enforce:
            return False
        mode = str(data.get("execution_mode", "batch")).strip() or "batch"
        if mode == "session":
            return False
        self._send_json(
            400,
            {
                "error": "session_only_mode",
                "detail": "execution_mode must be 'session' when MESH_ENFORCE_SESSION_ONLY=1",
                "execution_mode": mode,
            },
        )
        return True

    def _read_body(self) -> str | None:
        """Read request body. Returns None on error (already sent response)."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json(400, {"error": "empty_body"})
            return None
        if content_length > 1_000_000:  # 1MB max
            self._send_json(413, {"error": "body_too_large"})
            return None
        return self.rfile.read(content_length).decode("utf-8")

    def _send_json(self, status: int, data: dict | None) -> None:
        """Send JSON response."""
        try:
            self.send_response(status)
            if data is not None:
                body = json.dumps(data).encode("utf-8")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_header("Content-Length", "0")
                self.end_headers()
        except _CLIENT_DISCONNECT_ERRORS:
            logger.info("Client disconnected before response flush for %s", self.path)

    def log_message(self, format: str, *args: object) -> None:
        """Route access logs to logger instead of stderr."""
        logger.info(format, *args)


def run_server(
    host: str = "0.0.0.0",
    port: int = 8780,
    db_path: str = "/var/lib/mesh-router/router.db",
    auth_token: str | None = None,
) -> None:
    """Start the mesh router HTTP server.

    Initializes DB, recovery, scheduler, and heartbeat manager,
    then serves HTTP requests. Sends sd_notify for systemd integration.
    """
    from src.router.bridge.buffer import FallbackBuffer
    from src.router.bridge.emitter import EventEmitter
    from src.router.bridge.transport import InProcessTransport

    db = RouterDB(db_path, check_same_thread=False)
    db.init_schema()

    recovery_result = recover_on_startup(db)
    if recovery_result.tasks_requeued or recovery_result.leases_expired:
        logger.info(
            "Recovery: %d tasks requeued, %d leases expired",
            recovery_result.tasks_requeued,
            recovery_result.leases_expired,
        )

    # Build WorkerManager token list from auth_token
    wm_tokens: list[dict[str, str | None]] = []
    if auth_token:
        wm_tokens.append({"token": auth_token, "expires_at": None})
    dev_mode = os.environ.get("MESH_DEV_MODE", "").strip() == "1"
    longpoll_registry = LongPollRegistry()

    worker_manager = WorkerManager(db, tokens=wm_tokens, dev_mode=dev_mode, longpoll_registry=longpoll_registry)

    heartbeat = HeartbeatManager(db, longpoll_registry=longpoll_registry)
    topology_path = os.environ.get("MESH_TOPOLOGY_PATH")
    topology = load_topology(topology_path)
    default_execution_mode = os.environ.get("MESH_DEFAULT_EXECUTION_MODE", "batch").strip()
    if default_execution_mode not in {"batch", "session"}:
        logger.warning("Invalid MESH_DEFAULT_EXECUTION_MODE=%r, using 'batch'", default_execution_mode)
        default_execution_mode = "batch"
    session_fallback_to_batch = os.environ.get("MESH_SESSION_FALLBACK_TO_BATCH", "").strip() == "1"
    enforce_session_only = os.environ.get("MESH_ENFORCE_SESSION_ONLY", "").strip().lower() in {"1", "true", "yes"}
    scheduler = Scheduler(
        db,
        longpoll_registry=longpoll_registry,
        topology=topology,
        session_fallback_to_batch=session_fallback_to_batch,
    )
    transport = InProcessTransport(db)
    buffer_path = os.environ.get("MESH_BUFFER_PATH", "~/.mesh/events-buffer.jsonl")
    buffer = FallbackBuffer(buffer_path=buffer_path)
    replay_interval = float(os.environ.get("MESH_BUFFER_REPLAY_INTERVAL_S", "60"))
    emitter = EventEmitter(
        transport=transport,
        source_machine=os.environ.get("MESH_SOURCE_MACHINE", "router"),
        buffer=buffer,
        replay_interval_s=replay_interval,
    )
    metrics = MeshMetrics()
    review_check_interval = float(os.environ.get("MESH_REVIEW_CHECK_INTERVAL_S", "60"))
    recovery_check_interval = float(os.environ.get("MESH_RECOVERY_CHECK_INTERVAL_S", "30"))
    verifier_gate = VerifierGate()
    longpoll_timeout = float(os.environ.get("MESH_LONGPOLL_TIMEOUT_S", "25"))
    wal_size_threshold = int(os.environ.get("MESH_DB_WAL_SIZE_THRESHOLD_BYTES", str(50 * 1024 * 1024)))  # 50MB
    disk_free_threshold = int(os.environ.get("MESH_DB_DISK_FREE_THRESHOLD_BYTES", str(100 * 1024 * 1024)))  # 100MB
    integrity_check_interval = int(os.environ.get("MESH_DB_INTEGRITY_CHECK_INTERVAL", "10"))  # every 10 cycles
    start_time = datetime.now(timezone.utc)

    server = ThreadingHTTPServer((host, port), MeshRouterHandler)
    server.router_state = {  # type: ignore[attr-defined]
        "db": db,
        "worker_manager": worker_manager,
        "heartbeat": heartbeat,
        "scheduler": scheduler,
        "transport": transport,
        "emitter": emitter,
        "buffer": buffer,
        "metrics": metrics,
        "longpoll_registry": longpoll_registry,
        "longpoll_timeout": longpoll_timeout,
        "default_execution_mode": default_execution_mode,
        "session_fallback_to_batch": session_fallback_to_batch,
        "enforce_session_only": enforce_session_only,
        "auth_token": auth_token,
        "start_time": start_time,
        "topology": topology,
        "verifier_gate": verifier_gate,
        "review_check_interval": review_check_interval,
        "recovery_check_interval": recovery_check_interval,
        "db_health_config": {
            "wal_size_threshold": wal_size_threshold,
            "disk_free_threshold": disk_free_threshold,
            "integrity_check_interval": integrity_check_interval,
        },
    }

    def handle_shutdown(signum: int, frame: object) -> None:
        logger.info("Shutting down mesh router (signal %d)...", signum)
        threading.Thread(target=server.shutdown).start()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    # Start dispatch loop thread
    dispatch_interval = float(os.environ.get("MESH_DISPATCH_INTERVAL_S", "5"))

    def dispatch_loop() -> None:
        """Periodically drain all dispatchable tasks to idle workers."""
        while True:
            time.sleep(dispatch_interval)
            try:
                dispatched = 0
                while True:
                    result = scheduler.dispatch()
                    if result is None:
                        break
                    dispatched += 1
                    logger.info(
                        "dispatch task=%s -> worker=%s",
                        result.task.task_id,
                        result.worker.worker_id,
                    )
                    metrics.tasks_dispatched.inc()
                if dispatched:
                    metrics.dispatch_cycles_total.labels(result="dispatched").inc()
                else:
                    metrics.dispatch_cycles_total.labels(result="empty").inc()
            except Exception as e:
                logger.error("Dispatch loop error: %s", e)
                metrics.dispatch_cycles_total.labels(result="error").inc()

    dispatch_thread = threading.Thread(target=dispatch_loop, daemon=True, name="dispatch")
    dispatch_thread.start()

    # Start periodic review timeout check thread
    def review_check_loop() -> None:
        """Periodically check for stale reviews and transition them to timeout."""
        while True:
            time.sleep(review_check_interval)
            try:
                timed_out = verifier_gate.check_review_timeout(db)
                if timed_out:
                    logger.info("Review timeout: %d tasks timed out: %s", len(timed_out), timed_out)
            except Exception as e:
                logger.error("Review timeout check failed: %s", e)

    review_thread = threading.Thread(target=review_check_loop, daemon=True, name="review-check")
    review_thread.start()

    def recovery_check_loop() -> None:
        """Periodically recover orphaned assigned/running tasks after worker loss."""
        while True:
            time.sleep(recovery_check_interval)
            try:
                result = recover_on_startup(db)
                if result.tasks_requeued or result.leases_expired:
                    logger.info(
                        "Periodic recovery: %d tasks requeued, %d leases expired",
                        result.tasks_requeued,
                        result.leases_expired,
                    )
                for error in result.errors:
                    logger.warning("Periodic recovery warning: %s", error)
            except Exception as e:
                logger.error("Periodic recovery check failed: %s", e)

    recovery_thread = threading.Thread(target=recovery_check_loop, daemon=True, name="recovery-check")
    recovery_thread.start()

    # Start buffer replay timer
    emitter.start_replay_timer()

    # Notify systemd we're ready + start watchdog thread
    try:
        import sdnotify

        n = sdnotify.SystemdNotifier()
        n.notify("READY=1")
        logger.info("sd_notify: READY=1")

        def watchdog_loop() -> None:
            cycle = 0
            while True:
                n.notify("WATCHDOG=1")

                # DB health checks
                try:
                    # WAL size check (every cycle)
                    wal_size = db.check_wal_size()
                    metrics.db_wal_size_bytes.set(wal_size)
                    if wal_size > wal_size_threshold:
                        logger.warning(
                            "WAL size %.1fMB exceeds threshold %.1fMB",
                            wal_size / (1024 * 1024),
                            wal_size_threshold / (1024 * 1024),
                        )

                    # Disk space check (every cycle)
                    disk_free = db.check_disk_space()
                    metrics.db_disk_free_bytes.set(disk_free)
                    if disk_free < disk_free_threshold:
                        logger.error(
                            "Low disk space: %.1fMB free (threshold %.1fMB)",
                            disk_free / (1024 * 1024),
                            disk_free_threshold / (1024 * 1024),
                        )

                    # Integrity check (every N cycles -- expensive)
                    if cycle > 0 and cycle % integrity_check_interval == 0:
                        integrity_ok = db.check_integrity()
                        metrics.db_integrity_ok.set(1 if integrity_ok else 0)
                        if not integrity_ok:
                            logger.error("PRAGMA integrity_check FAILED")

                except Exception as e:
                    logger.error("DB health check error: %s", e)
                    metrics.db_health_check_errors_total.inc()

                cycle += 1
                time.sleep(10)

        wd_thread = threading.Thread(target=watchdog_loop, daemon=True)
        wd_thread.start()
    except ImportError:
        logger.debug("sdnotify not available, skipping sd_notify")

    logger.info("Mesh router listening on %s:%d", host, port)
    server.serve_forever()
    db.close()
    logger.info("Mesh router stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    run_server(
        port=int(os.environ.get("MESH_ROUTER_PORT", "8780")),
        db_path=os.environ.get("MESH_DB_PATH", "/var/lib/mesh-router/router.db"),
        auth_token=os.environ.get("MESH_AUTH_TOKEN"),
    )
