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
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from src.router.db import RouterDB
from src.router.heartbeat import HeartbeatManager
from src.router.models import Worker
from src.router.recovery import recover_on_startup
from src.router.scheduler import Scheduler

logger = logging.getLogger("mesh.server")


class MeshRouterHandler(BaseHTTPRequestHandler):
    """HTTP request handler for mesh router endpoints."""

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._handle_health()
        elif path == "/tasks/next":
            self._handle_task_poll()
        else:
            self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/events":
            self._handle_events()
        elif path == "/heartbeat":
            self._handle_heartbeat()
        elif path == "/register":
            self._handle_register()
        elif path == "/tasks/complete":
            self._handle_task_complete()
        elif path == "/tasks/fail":
            self._handle_task_fail()
        else:
            self._send_json(404, {"error": "not_found"})

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

    def _handle_task_poll(self) -> None:
        """GET /tasks/next?worker_id=X — short-poll for next assigned task.

        Returns 200 + task JSON if a task is assigned, or 204 if none.
        MVP uses short-polling (worker polls every 2s).
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

        tasks = db.get_tasks_by_worker(worker_id, status="assigned")
        if tasks:
            task = tasks[0]
            self._send_json(200, task.model_dump(mode="json"))
        else:
            self._send_json(204, None)

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
        """POST /register — worker registration."""
        if not self._check_auth():
            return
        body = self._read_body()
        if body is None:
            return
        try:
            data = json.loads(body)
            db: RouterDB = self.server.router_state["db"]  # type: ignore[attr-defined]
            worker = Worker(**data)
            db.upsert_worker(worker)
            self._send_json(201, {"status": "registered", "worker_id": worker.worker_id})
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid_json"})
        except (ValueError, TypeError, KeyError) as e:
            self._send_json(400, {"error": f"invalid_worker_data: {type(e).__name__}"})

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
            scheduler: Scheduler = self.server.router_state["scheduler"]  # type: ignore[attr-defined]
            ok = scheduler.complete_task(task_id, worker_id)
            if ok:
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

    heartbeat = HeartbeatManager(db)
    scheduler = Scheduler(db)
    transport = InProcessTransport(db)
    start_time = datetime.now(timezone.utc)

    server = ThreadingHTTPServer((host, port), MeshRouterHandler)
    server.router_state = {  # type: ignore[attr-defined]
        "db": db,
        "heartbeat": heartbeat,
        "scheduler": scheduler,
        "transport": transport,
        "auth_token": auth_token,
        "start_time": start_time,
    }

    def handle_shutdown(signum: int, frame: object) -> None:
        logger.info("Shutting down mesh router (signal %d)...", signum)
        threading.Thread(target=server.shutdown).start()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    # Notify systemd we're ready + start watchdog thread
    try:
        import sdnotify

        n = sdnotify.SystemdNotifier()
        n.notify("READY=1")
        logger.info("sd_notify: READY=1")

        def watchdog_loop() -> None:
            while True:
                n.notify("WATCHDOG=1")
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
