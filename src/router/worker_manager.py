"""Worker lifecycle manager: registration, deregistration, and status transitions.

Handles token-based authentication, account uniqueness enforcement,
and worker status FSM transitions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from src.router.db import RouterDB
from src.router.heartbeat import requeue_task
from src.router.longpoll import LongPollRegistry
from src.router.models import TaskEvent, Worker

logger = logging.getLogger(__name__)

WORKER_TRANSITIONS: dict[str, set[str]] = {
    "offline": {"idle"},
    "idle": {"busy", "stale", "offline", "draining"},
    "busy": {"idle", "stale", "offline", "draining"},
    "stale": {"idle", "offline"},
    "draining": {"offline", "stale"},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid4() -> str:
    import uuid
    return str(uuid.uuid4())


class WorkerManager:
    """Manages worker registration, deregistration, and status transitions."""

    def __init__(
        self,
        db: RouterDB,
        tokens: list[dict[str, str | None]],
        max_attempts: int = 3,
        dev_mode: bool = False,
        longpoll_registry: LongPollRegistry | None = None,
    ) -> None:
        self._db = db
        self._tokens = tokens  # [{"token": str, "expires_at": str | None}]
        self._max_attempts = max_attempts
        self._dev_mode = dev_mode
        self._longpoll_registry = longpoll_registry

    def validate_token(self, token: str) -> bool:
        """Check if token is valid and not expired."""
        now = _utc_now()
        for t in self._tokens:
            if t["token"] == token:
                expires = t.get("expires_at")
                if expires is None or expires > now:
                    return True
        return False

    def register_worker(
        self, worker: Worker, token: str
    ) -> tuple[bool, str]:
        """Register a worker with token auth and account uniqueness.

        Returns (success, message).
        """
        # Dev mode: skip token validation if no tokens configured
        if not self._dev_mode or self._tokens:
            if not self.validate_token(token):
                return False, "invalid_token"

        with self._db.transaction() as conn:
            # Check account uniqueness (atomic within transaction)
            existing = self._db.find_worker_by_account(
                worker.account_profile, exclude_statuses=["offline"]
            )
            if existing and existing.worker_id != worker.worker_id:
                return False, "account_in_use"

            # Check if re-registering same worker_id
            current = self._db.get_worker(worker.worker_id)
            now = _utc_now()

            if current is not None:
                # Fast re-registration from stale/offline
                self._db.update_worker(
                    worker.worker_id,
                    {
                        "status": "idle",
                        "last_heartbeat": now,
                        "idle_since": now,
                        "stale_since": None,
                        "machine": worker.machine,
                        "cli_type": worker.cli_type.value,
                        "capabilities": json.dumps(
                            worker.capabilities
                        ),
                    },
                    conn=conn,
                )
                is_reregister = True
                # Reset long-poll Condition on re-registration
                if self._longpoll_registry is not None:
                    self._longpoll_registry.register(worker.worker_id)
            else:
                # New registration
                worker.status = "idle"
                worker.last_heartbeat = now
                worker.idle_since = now
                worker.stale_since = None
                self._db.insert_worker(worker, conn=conn)
                is_reregister = False

            # Emit registration event
            self._db.insert_event(
                TaskEvent(
                    task_id=worker.worker_id,
                    event_type="worker_registered",
                    payload={
                        "machine": worker.machine,
                        "cli_type": worker.cli_type.value,
                        "account_profile": worker.account_profile,
                    },
                    idempotency_key=_uuid4(),
                ),
                conn=conn,
            )

        return True, "re-registered" if is_reregister else "registered"

    def deregister_worker(
        self, worker_id: str
    ) -> tuple[bool, str]:
        """Deregister a worker, cleaning up active tasks.

        Returns (success, message).
        """
        worker = self._db.get_worker(worker_id)
        if worker is None:
            return False, "not_found"

        with self._db.transaction() as conn:
            # If worker is busy, clean up its tasks
            if worker.status == "busy":
                leases = self._db.list_worker_leases(worker_id)
                for lease in leases:
                    task = self._db.get_task(lease.task_id)
                    if task and task.status.value in ("assigned", "running"):
                        self._db.expire_lease(lease.lease_id, conn=conn)
                        requeue_task(
                            self._db,
                            task.task_id,
                            "deregister",
                            self._max_attempts,
                            conn=conn,
                        )

            # Set worker offline
            self._db.update_worker(
                worker_id,
                {"status": "offline"},
                conn=conn,
            )

            # Clean up long-poll Condition on deregistration
            if self._longpoll_registry is not None:
                self._longpoll_registry.unregister(worker_id)

            self._db.insert_event(
                TaskEvent(
                    task_id=worker_id,
                    event_type="worker_deregistered",
                    payload={"worker_id": worker_id},
                    idempotency_key=_uuid4(),
                ),
                conn=conn,
            )

        return True, "deregistered"

    def drain_worker(self, worker_id: str) -> tuple[bool, str]:
        """Initiate graceful drain: mark worker as draining, no new tasks assigned.

        Returns (success, message). Possible messages:
        - "draining" -- successfully initiated
        - "already_draining" -- worker already in draining state
        - "not_found" -- worker_id not in DB
        - "invalid_state" -- worker is stale or offline (can't drain)
        - "drained_immediately" -- worker was idle with no tasks, set offline
        """
        worker = self._db.get_worker(worker_id)
        if worker is None:
            return False, "not_found"

        if worker.status == "draining":
            return True, "already_draining"

        if worker.status in ("stale", "offline"):
            return False, "invalid_state"

        # Use FSM transition (idle -> draining or busy -> draining)
        ok = self.transition_worker_status(
            worker_id, worker.status, "draining"
        )
        if not ok:
            return False, "transition_failed"

        # If worker is idle (no running tasks), auto-retire immediately
        running_tasks = self._db.get_tasks_by_worker(worker_id, status="running")
        assigned_tasks = self._db.get_tasks_by_worker(worker_id, status="assigned")
        if not running_tasks and not assigned_tasks:
            self.deregister_worker(worker_id)
            return True, "drained_immediately"

        return True, "draining"

    def transition_worker_status(
        self, worker_id: str, old_status: str, new_status: str
    ) -> bool:
        """Validate and apply a worker status transition."""
        allowed = WORKER_TRANSITIONS.get(old_status, set())
        if new_status not in allowed:
            logger.warning(
                "Invalid worker transition: %s -> %s for %s",
                old_status,
                new_status,
                worker_id,
            )
            return False

        worker = self._db.get_worker(worker_id)
        if worker is None or worker.status != old_status:
            return False

        now = _utc_now()
        updates: dict[str, str | int | None] = {"status": new_status}

        if new_status == "idle":
            updates["idle_since"] = now
            updates["stale_since"] = None
        elif new_status == "stale":
            updates["stale_since"] = now

        return self._db.update_worker(worker_id, updates)
