"""SQLite persistence layer for the AI Mesh Router.

Provides RouterDB class with schema management, CRUD operations,
compare-and-set state transitions, and event deduplication.

WAL mode is enabled on init for concurrent read/write support.
All write operations use BEGIN IMMEDIATE for correctness.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from src.router.models import CommunicationRole, Lease, Task, TaskEvent, TaskStatus, Worker

_BUSY_RETRIES = 3
_BUSY_BACKOFFS_MS = [50, 100, 200]

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id         TEXT PRIMARY KEY,
    parent_task_id  TEXT,
    phase           TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    payload         TEXT NOT NULL DEFAULT '{}',
    target_cli      TEXT NOT NULL DEFAULT 'claude',
    target_account  TEXT NOT NULL DEFAULT 'default',
    priority        INTEGER NOT NULL DEFAULT 1,
    deadline_ts     TEXT,
    depends_on      TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'queued',
    assigned_worker TEXT,
    lease_expires_at TEXT,
    attempt         INTEGER NOT NULL DEFAULT 1,
    not_before      TEXT,
    created_by      TEXT,
    idempotency_key TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_idempotency ON tasks(idempotency_key);

CREATE TABLE IF NOT EXISTS task_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    payload         TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL,
    ts              TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_events_idempotency ON task_events(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_events_task_id ON task_events(task_id);

CREATE TABLE IF NOT EXISTS workers (
    worker_id       TEXT PRIMARY KEY,
    machine         TEXT NOT NULL DEFAULT '',
    cli_type        TEXT NOT NULL DEFAULT 'claude',
    account_profile TEXT NOT NULL DEFAULT 'default',
    capabilities    TEXT NOT NULL DEFAULT '[]',
    role            TEXT NOT NULL DEFAULT 'worker',
    status          TEXT NOT NULL DEFAULT 'idle',
    last_heartbeat  TEXT NOT NULL,
    idle_since      TEXT,
    stale_since     TEXT,
    concurrency     INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status);

CREATE TABLE IF NOT EXISTS leases (
    lease_id        TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL UNIQUE,
    worker_id       TEXT NOT NULL,
    granted_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id),
    FOREIGN KEY (worker_id) REFERENCES workers(worker_id)
);

CREATE INDEX IF NOT EXISTS idx_leases_expires ON leases(expires_at);

CREATE TABLE IF NOT EXISTS dead_letter_events (
    dl_id           TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    attempted_from  TEXT NOT NULL,
    attempted_to    TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    original_payload TEXT NOT NULL DEFAULT '{}',
    ts              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dead_letter_task_id ON dead_letter_events(task_id);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retry_on_busy(func):
    """Decorator to retry on SQLITE_BUSY with exponential backoff."""
    def wrapper(*args, **kwargs):
        last_err = None
        for attempt in range(_BUSY_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e) or "SQLITE_BUSY" in str(e):
                    last_err = e
                    time.sleep(_BUSY_BACKOFFS_MS[attempt] / 1000.0)
                else:
                    raise
        raise last_err  # type: ignore[misc]
    return wrapper


class RouterDB:
    """SQLite-backed persistence for the mesh router."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        self._conn.close()

    def init_schema(self) -> None:
        """Create all tables and indexes if they don't exist."""
        self._conn.executescript(_SCHEMA_SQL)

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for atomic compound operations.

        Uses BEGIN IMMEDIATE to acquire a write lock immediately,
        preventing concurrent writers from interleaving.
        Commits on success, rolls back on exception.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    # -- Tasks --

    def _task_from_row(self, row: sqlite3.Row) -> Task:
        return Task(
            task_id=row["task_id"],
            parent_task_id=row["parent_task_id"],
            phase=row["phase"],
            title=row["title"],
            payload=json.loads(row["payload"]),
            target_cli=row["target_cli"],
            target_account=row["target_account"],
            priority=row["priority"],
            deadline_ts=row["deadline_ts"],
            depends_on=json.loads(row["depends_on"]),
            status=row["status"],
            assigned_worker=row["assigned_worker"],
            lease_expires_at=row["lease_expires_at"],
            attempt=row["attempt"],
            not_before=row["not_before"],
            created_by=row["created_by"],
            idempotency_key=row["idempotency_key"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @_retry_on_busy
    def insert_task(self, task: Task, conn: sqlite3.Connection | None = None) -> Task:
        """Insert a new task. Returns the task on success."""
        c = conn or self._conn
        c.execute(
            """INSERT INTO tasks (
                task_id, parent_task_id, phase, title, payload, target_cli,
                target_account, priority, deadline_ts, depends_on, status,
                assigned_worker, lease_expires_at, attempt, not_before,
                created_by, idempotency_key, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.task_id,
                task.parent_task_id,
                task.phase.value,
                task.title,
                json.dumps(task.payload),
                task.target_cli.value,
                task.target_account,
                task.priority,
                task.deadline_ts,
                json.dumps(task.depends_on),
                task.status.value,
                task.assigned_worker,
                task.lease_expires_at,
                task.attempt,
                task.not_before,
                task.created_by,
                task.idempotency_key,
                task.created_at,
                task.updated_at,
            ),
        )
        if conn is None:
            self._conn.commit()
        return task

    def get_task(self, task_id: str) -> Task | None:
        """Get a task by ID, or None if not found."""
        cur = self._conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        )
        row = cur.fetchone()
        return self._task_from_row(row) if row else None

    @_retry_on_busy
    def update_task_status(
        self,
        task_id: str,
        old_status: TaskStatus,
        new_status: TaskStatus,
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        """Compare-and-set status update.

        Returns True if the update succeeded (old_status matched),
        False if old_status did not match (concurrent modification or wrong state).
        """
        c = conn or self._conn
        now = _utc_now()
        cur = c.execute(
            """UPDATE tasks SET status = ?, updated_at = ?
            WHERE task_id = ? AND status = ?""",
            (new_status.value, now, task_id, old_status.value),
        )
        if conn is None:
            self._conn.commit()
        return cur.rowcount > 0

    # -- Task Events --

    def _event_from_row(self, row: sqlite3.Row) -> TaskEvent:
        return TaskEvent(
            event_id=str(row["event_id"]),
            task_id=row["task_id"],
            event_type=row["event_type"],
            payload=json.loads(row["payload"]),
            idempotency_key=row["idempotency_key"],
            ts=row["ts"],
        )

    @_retry_on_busy
    def insert_event(self, event: TaskEvent, conn: sqlite3.Connection | None = None) -> bool:
        """Insert an event. Returns True on success, False on duplicate idempotency_key."""
        c = conn or self._conn
        try:
            c.execute(
                """INSERT INTO task_events (task_id, event_type, payload, idempotency_key, ts)
                VALUES (?, ?, ?, ?, ?)""",
                (
                    event.task_id,
                    event.event_type,
                    json.dumps(event.payload),
                    event.idempotency_key,
                    event.ts,
                ),
            )
            if conn is None:
                self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            if conn is None:
                self._conn.rollback()
            return False

    def get_events(self, task_id: str) -> list[TaskEvent]:
        """Get all events for a task, ordered chronologically (by event_id ASC)."""
        cur = self._conn.execute(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY event_id ASC",
            (task_id,),
        )
        return [self._event_from_row(row) for row in cur.fetchall()]

    # -- Workers --

    def _worker_from_row(self, row: sqlite3.Row) -> Worker:
        return Worker(
            worker_id=row["worker_id"],
            machine=row["machine"],
            cli_type=row["cli_type"],
            account_profile=row["account_profile"],
            capabilities=json.loads(row["capabilities"]),
            role=row["role"],
            status=row["status"],
            last_heartbeat=row["last_heartbeat"],
            idle_since=row["idle_since"],
            stale_since=row["stale_since"],
            concurrency=row["concurrency"],
        )

    @_retry_on_busy
    def insert_worker(self, worker: Worker, conn: sqlite3.Connection | None = None) -> Worker:
        """Insert a new worker. Returns the worker on success."""
        c = conn or self._conn
        c.execute(
            """INSERT INTO workers (
                worker_id, machine, cli_type, account_profile,
                capabilities, role, status, last_heartbeat, idle_since,
                stale_since, concurrency
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                worker.worker_id,
                worker.machine,
                worker.cli_type.value,
                worker.account_profile,
                json.dumps(worker.capabilities),
                worker.role,
                worker.status,
                worker.last_heartbeat,
                worker.idle_since,
                worker.stale_since,
                worker.concurrency,
            ),
        )
        if conn is None:
            self._conn.commit()
        return worker

    def get_worker(self, worker_id: str) -> Worker | None:
        """Get a worker by ID, or None if not found."""
        cur = self._conn.execute(
            "SELECT * FROM workers WHERE worker_id = ?", (worker_id,)
        )
        row = cur.fetchone()
        return self._worker_from_row(row) if row else None

    def list_workers(self, status: str | None = None) -> list[Worker]:
        """List workers, optionally filtered by status."""
        if status is not None:
            cur = self._conn.execute(
                "SELECT * FROM workers WHERE status = ?", (status,)
            )
        else:
            cur = self._conn.execute("SELECT * FROM workers")
        return [self._worker_from_row(row) for row in cur.fetchall()]

    @_retry_on_busy
    def update_worker(
        self,
        worker_id: str,
        updates: dict[str, str | int | None],
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        """Update worker fields by worker_id. Returns True if row was updated."""
        if not updates:
            return False
        c = conn or self._conn
        set_clauses = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [worker_id]
        cur = c.execute(
            f"UPDATE workers SET {set_clauses} WHERE worker_id = ?",
            values,
        )
        if conn is None:
            self._conn.commit()
        return cur.rowcount > 0

    def list_stale_candidates(self, threshold_iso: str) -> list[Worker]:
        """Find workers whose last_heartbeat is older than threshold."""
        cur = self._conn.execute(
            "SELECT * FROM workers WHERE status IN ('idle', 'busy') AND last_heartbeat < ?",
            (threshold_iso,),
        )
        return [self._worker_from_row(row) for row in cur.fetchall()]

    def find_worker_by_account(
        self,
        account_profile: str,
        exclude_statuses: list[str] | None = None,
    ) -> Worker | None:
        """Find an active worker with the given account_profile."""
        excluded = exclude_statuses or ["offline"]
        placeholders = ", ".join("?" for _ in excluded)
        cur = self._conn.execute(
            f"SELECT * FROM workers WHERE account_profile = ? AND status NOT IN ({placeholders}) LIMIT 1",
            [account_profile] + excluded,
        )
        row = cur.fetchone()
        return self._worker_from_row(row) if row else None

    def list_worker_leases(self, worker_id: str) -> list[Lease]:
        """Get all active leases for a worker."""
        cur = self._conn.execute(
            "SELECT * FROM leases WHERE worker_id = ?", (worker_id,)
        )
        return [self._lease_from_row(row) for row in cur.fetchall()]

    @_retry_on_busy
    def update_task_fields(
        self,
        task_id: str,
        updates: dict[str, str | int | None],
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        """Update arbitrary task fields by task_id. Returns True if row was updated."""
        if not updates:
            return False
        c = conn or self._conn
        updates["updated_at"] = _utc_now()
        set_clauses = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        cur = c.execute(
            f"UPDATE tasks SET {set_clauses} WHERE task_id = ?",
            values,
        )
        if conn is None:
            self._conn.commit()
        return cur.rowcount > 0

    def list_queued_tasks(
        self,
        before_iso: str | None = None,
    ) -> list[Task]:
        """List queued tasks, optionally only those created before a timestamp."""
        if before_iso:
            cur = self._conn.execute(
                "SELECT * FROM tasks WHERE status = 'queued' AND created_at < ? ORDER BY priority DESC, created_at ASC",
                (before_iso,),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM tasks WHERE status = 'queued' ORDER BY priority DESC, created_at ASC"
            )
        return [self._task_from_row(row) for row in cur.fetchall()]

    # -- Leases --

    def _lease_from_row(self, row: sqlite3.Row) -> Lease:
        return Lease(
            lease_id=row["lease_id"],
            task_id=row["task_id"],
            worker_id=row["worker_id"],
            granted_at=row["granted_at"],
            expires_at=row["expires_at"],
        )

    @_retry_on_busy
    def create_lease(self, lease: Lease, conn: sqlite3.Connection | None = None) -> Lease:
        """Create a new lease. Raises on duplicate task_id (one active lease per task)."""
        c = conn or self._conn
        c.execute(
            """INSERT INTO leases (lease_id, task_id, worker_id, granted_at, expires_at)
            VALUES (?, ?, ?, ?, ?)""",
            (
                lease.lease_id,
                lease.task_id,
                lease.worker_id,
                lease.granted_at,
                lease.expires_at,
            ),
        )
        if conn is None:
            self._conn.commit()
        return lease

    def get_active_lease(self, task_id: str) -> Lease | None:
        """Get the active lease for a task, or None if no lease exists."""
        cur = self._conn.execute(
            "SELECT * FROM leases WHERE task_id = ?", (task_id,)
        )
        row = cur.fetchone()
        return self._lease_from_row(row) if row else None

    @_retry_on_busy
    def expire_lease(self, lease_id: str, conn: sqlite3.Connection | None = None) -> bool:
        """Delete a lease by ID. Returns True if a lease was deleted."""
        c = conn or self._conn
        cur = c.execute("DELETE FROM leases WHERE lease_id = ?", (lease_id,))
        if conn is None:
            self._conn.commit()
        return cur.rowcount > 0
