"""SQLite persistence layer for the AI Mesh Router.

Provides RouterDB class with schema management, CRUD operations,
compare-and-set state transitions, and event deduplication.

WAL mode is enabled on init for concurrent read/write support.
All write operations use BEGIN IMMEDIATE for correctness.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from src.router.models import Lease, Session, SessionMessage, Task, TaskEvent, TaskStatus, Thread, Worker

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
    execution_mode  TEXT NOT NULL DEFAULT 'batch',
    priority        INTEGER NOT NULL DEFAULT 1,
    deadline_ts     TEXT,
    depends_on      TEXT NOT NULL DEFAULT '[]',
    status          TEXT NOT NULL DEFAULT 'queued',
    assigned_worker TEXT,
    session_id      TEXT,
    lease_expires_at TEXT,
    attempt         INTEGER NOT NULL DEFAULT 1,
    not_before      TEXT,
    created_by      TEXT,
    critical        INTEGER NOT NULL DEFAULT 0,
    rejection_count INTEGER NOT NULL DEFAULT 0,
    review_timeout_at TEXT,
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
    execution_modes TEXT NOT NULL DEFAULT '["batch"]',
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

CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    worker_id       TEXT NOT NULL,
    cli_type        TEXT NOT NULL DEFAULT 'claude',
    account_profile TEXT NOT NULL DEFAULT 'default',
    task_id         TEXT,
    state           TEXT NOT NULL DEFAULT 'open',
    metadata        TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_worker_id ON sessions(worker_id);
CREATE INDEX IF NOT EXISTS idx_sessions_state ON sessions(state);
CREATE INDEX IF NOT EXISTS idx_sessions_task_id ON sessions(task_id);

CREATE TABLE IF NOT EXISTS session_messages (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    direction       TEXT NOT NULL DEFAULT 'in',
    role            TEXT NOT NULL DEFAULT 'operator',
    content         TEXT NOT NULL,
    metadata        TEXT NOT NULL DEFAULT '{}',
    ts              TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_session_messages_session_seq
ON session_messages(session_id, seq);

CREATE TABLE IF NOT EXISTS threads (
    thread_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);
CREATE INDEX IF NOT EXISTS idx_threads_name ON threads(name);

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

    def __init__(self, db_path: str, check_same_thread: bool = True) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
        self._db_path = db_path
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    @property
    def db_path(self) -> str:
        return self._db_path

    def close(self) -> None:
        self._conn.close()

    # -- DB Health Checks --

    def check_wal_size(self) -> int:
        """Return WAL file size in bytes. Returns 0 if WAL file does not exist."""
        wal_path = Path(self._db_path + "-wal")
        if not wal_path.exists():
            return 0
        return wal_path.stat().st_size

    def check_integrity(self) -> bool:
        """Run PRAGMA integrity_check. Returns True if database is intact.

        WARNING: This is expensive on large databases. Do NOT call every cycle.
        Recommended: every 10 cycles (~100s).
        """
        try:
            cur = self._conn.execute("PRAGMA integrity_check")
            result = cur.fetchone()
            return result is not None and result[0] == "ok"
        except sqlite3.Error:
            return False

    def check_disk_space(self) -> int:
        """Return free disk space in bytes on the partition containing the DB file."""
        db_dir = os.path.dirname(os.path.abspath(self._db_path))
        usage = shutil.disk_usage(db_dir)
        return usage.free

    def init_schema(self) -> None:
        """Create all tables and indexes if they don't exist."""
        self._conn.executescript(_SCHEMA_SQL)
        # Lightweight additive migrations for existing DBs (safe to re-run)
        self._ensure_column("tasks", "execution_mode", "TEXT NOT NULL DEFAULT 'batch'")
        self._ensure_column("tasks", "session_id", "TEXT")
        self._ensure_column("tasks", "result_json", "TEXT DEFAULT NULL")
        self._ensure_column("workers", "execution_modes", "TEXT NOT NULL DEFAULT '[\"batch\"]'")
        self._ensure_column("tasks", "thread_id", "TEXT DEFAULT NULL")
        self._ensure_column("tasks", "step_index", "INTEGER DEFAULT NULL")
        self._ensure_column("tasks", "repo", "TEXT DEFAULT NULL")
        self._ensure_column("tasks", "role", "TEXT DEFAULT NULL")
        self._conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_thread_step
            ON tasks(thread_id, step_index) WHERE thread_id IS NOT NULL"""
        )
        self._conn.commit()

    def _ensure_column(self, table: str, column: str, sql_type_clause: str) -> None:
        """Add a column if it does not exist (best-effort additive migration)."""
        cur = self._conn.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in cur.fetchall()}
        if column in existing:
            return
        self._conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {sql_type_clause}"
        )

    # -- Result sanitization --

    _SECRET_PATTERNS = re.compile(
        r"sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36,}|xoxb-[a-zA-Z0-9\-]{50,}"
    )
    _MAX_RESULT_BYTES = 32768  # 32KB
    _MAX_STRING_VALUE = 1000

    def _sanitize_result(self, result: dict[str, Any] | None) -> str | None:
        """Sanitize and serialize a task result dict to JSON string.

        - Replaces secret patterns (sk-, ghp_, xoxb-) with [REDACTED]
        - Truncates if JSON > 32KB (trims long string values, adds _truncated flag)
        - Returns JSON string or None
        """
        if result is None:
            return None
        # Deep-copy via JSON round-trip and sanitize secrets in the string form
        raw = json.dumps(result)
        sanitized_str = self._SECRET_PATTERNS.sub("[REDACTED]", raw)
        # Check size and truncate iteratively until under limit
        if len(sanitized_str.encode("utf-8")) > self._MAX_RESULT_BYTES:
            data = json.loads(sanitized_str)
            original_key_count = len(data)
            max_len = self._MAX_STRING_VALUE
            for _ in range(3):
                self._truncate_strings(data, max_len)
                data["_truncated"] = True
                sanitized_str = json.dumps(data)
                if len(sanitized_str.encode("utf-8")) <= self._MAX_RESULT_BYTES:
                    break
                max_len //= 2
            else:
                # Hard cap: return a small, bounded summary object. Do not echo
                # original keys here, because oversized key names can defeat the cap.
                sanitized_str = json.dumps({
                    "_hard_truncated": True,
                    "_key_count": original_key_count,
                    "_reason": "result exceeded size limit",
                })
        return sanitized_str

    @staticmethod
    def _truncate_strings(obj: Any, max_len: int = 1000) -> None:
        """Recursively truncate string values longer than max_len in-place."""
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, str) and len(v) > max_len:
                    obj[k] = v[:max_len] + "...[truncated]"
                elif isinstance(v, (dict, list)):
                    RouterDB._truncate_strings(v, max_len)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                if isinstance(v, str) and len(v) > max_len:
                    obj[i] = v[:max_len] + "...[truncated]"
                elif isinstance(v, (dict, list)):
                    RouterDB._truncate_strings(v, max_len)

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
        keys = row.keys()
        result_json = row["result_json"] if "result_json" in keys else None
        result = json.loads(result_json) if result_json else None
        return Task(
            task_id=row["task_id"],
            parent_task_id=row["parent_task_id"],
            phase=row["phase"],
            title=row["title"],
            payload=json.loads(row["payload"]),
            target_cli=row["target_cli"],
            target_account=row["target_account"],
            execution_mode=row["execution_mode"] if "execution_mode" in keys else "batch",
            priority=row["priority"],
            deadline_ts=row["deadline_ts"],
            depends_on=json.loads(row["depends_on"]),
            status=row["status"],
            assigned_worker=row["assigned_worker"],
            session_id=row["session_id"] if "session_id" in keys else None,
            lease_expires_at=row["lease_expires_at"],
            attempt=row["attempt"],
            not_before=row["not_before"],
            created_by=row["created_by"],
            critical=bool(row["critical"]),
            rejection_count=row["rejection_count"],
            review_timeout_at=row["review_timeout_at"],
            idempotency_key=row["idempotency_key"],
            result=result,
            thread_id=row["thread_id"] if "thread_id" in keys else None,
            step_index=row["step_index"] if "step_index" in keys else None,
            repo=row["repo"] if "repo" in keys else None,
            role=row["role"] if "role" in keys else None,
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
                target_account, execution_mode, priority, deadline_ts, depends_on, status,
                assigned_worker, session_id, lease_expires_at, attempt, not_before,
                created_by, critical, rejection_count, review_timeout_at,
                idempotency_key, thread_id, step_index, repo, role,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.task_id,
                task.parent_task_id,
                task.phase.value,
                task.title,
                json.dumps(task.payload),
                task.target_cli.value,
                task.target_account,
                task.execution_mode.value if hasattr(task.execution_mode, "value") else str(task.execution_mode),
                task.priority,
                task.deadline_ts,
                json.dumps(task.depends_on),
                task.status.value,
                task.assigned_worker,
                task.session_id,
                task.lease_expires_at,
                task.attempt,
                task.not_before,
                task.created_by,
                int(task.critical),
                task.rejection_count,
                task.review_timeout_at,
                task.idempotency_key,
                task.thread_id,
                task.step_index,
                task.repo,
                task.role,
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

    def list_tasks(
        self,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Task]:
        """List tasks, optionally filtered by status. Ordered by created_at DESC."""
        if status is not None:
            cur = self._conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [self._task_from_row(row) for row in cur.fetchall()]

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

    # -- Sessions --

    def _session_from_row(self, row: sqlite3.Row) -> Session:
        return Session(
            session_id=row["session_id"],
            worker_id=row["worker_id"],
            cli_type=row["cli_type"],
            account_profile=row["account_profile"],
            task_id=row["task_id"],
            state=row["state"],
            metadata=json.loads(row["metadata"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _session_message_from_row(self, row: sqlite3.Row) -> SessionMessage:
        return SessionMessage(
            seq=row["seq"],
            session_id=row["session_id"],
            direction=row["direction"],
            role=row["role"],
            content=row["content"],
            metadata=json.loads(row["metadata"]),
            ts=row["ts"],
        )

    @_retry_on_busy
    def insert_session(self, session: Session, conn: sqlite3.Connection | None = None) -> Session:
        c = conn or self._conn
        c.execute(
            """INSERT INTO sessions (
                session_id, worker_id, cli_type, account_profile, task_id,
                state, metadata, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.session_id,
                session.worker_id,
                session.cli_type.value if hasattr(session.cli_type, "value") else str(session.cli_type),
                session.account_profile,
                session.task_id,
                session.state.value if hasattr(session.state, "value") else str(session.state),
                json.dumps(session.metadata),
                session.created_at,
                session.updated_at,
            ),
        )
        if conn is None:
            self._conn.commit()
        return session

    def get_session(self, session_id: str) -> Session | None:
        cur = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        return self._session_from_row(row) if row else None

    def list_sessions(
        self,
        *,
        state: str | None = None,
        worker_id: str | None = None,
        limit: int = 200,
    ) -> list[Session]:
        sql = "SELECT * FROM sessions"
        params: list[object] = []
        clauses: list[str] = []
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        if worker_id is not None:
            clauses.append("worker_id = ?")
            params.append(worker_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        cur = self._conn.execute(sql, params)
        return [self._session_from_row(row) for row in cur.fetchall()]

    @_retry_on_busy
    def update_session(
        self,
        session_id: str,
        updates: dict[str, str | None],
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        if not updates:
            return False
        c = conn or self._conn
        updates = dict(updates)
        updates["updated_at"] = _utc_now()
        if "metadata" in updates and isinstance(updates["metadata"], dict):  # type: ignore[unreachable]
            updates["metadata"] = json.dumps(updates["metadata"])  # pragma: no cover
        set_clauses = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [session_id]
        cur = c.execute(
            f"UPDATE sessions SET {set_clauses} WHERE session_id = ?",
            values,
        )
        if conn is None:
            self._conn.commit()
        return cur.rowcount > 0

    @_retry_on_busy
    def append_session_message(
        self,
        message: SessionMessage,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        c = conn or self._conn
        cur = c.execute(
            """INSERT INTO session_messages (
                session_id, direction, role, content, metadata, ts
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                message.session_id,
                message.direction,
                message.role,
                message.content,
                json.dumps(message.metadata),
                message.ts,
            ),
        )
        if conn is None:
            self._conn.commit()
        return int(cur.lastrowid)

    def list_session_messages(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
        limit: int = 200,
    ) -> list[SessionMessage]:
        cur = self._conn.execute(
            """SELECT * FROM session_messages
            WHERE session_id = ? AND seq > ?
            ORDER BY seq ASC
            LIMIT ?""",
            (session_id, after_seq, limit),
        )
        return [self._session_message_from_row(row) for row in cur.fetchall()]

    # -- Workers --

    def _worker_from_row(self, row: sqlite3.Row) -> Worker:
        return Worker(
            worker_id=row["worker_id"],
            machine=row["machine"],
            cli_type=row["cli_type"],
            account_profile=row["account_profile"],
            capabilities=json.loads(row["capabilities"]),
            execution_modes=json.loads(row["execution_modes"]) if "execution_modes" in row.keys() else ["batch"],
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
                capabilities, execution_modes, role, status, last_heartbeat, idle_since,
                stale_since, concurrency
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                worker.worker_id,
                worker.machine,
                worker.cli_type.value,
                worker.account_profile,
                json.dumps(worker.capabilities),
                json.dumps(worker.execution_modes),
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

    @_retry_on_busy
    def upsert_worker(self, worker: Worker, conn: sqlite3.Connection | None = None) -> Worker:
        """Insert or update a worker. Used for registration/re-registration."""
        c = conn or self._conn
        c.execute(
            """INSERT INTO workers (
                worker_id, machine, cli_type, account_profile,
                capabilities, execution_modes, role, status, last_heartbeat, idle_since,
                stale_since, concurrency
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                machine=excluded.machine,
                cli_type=excluded.cli_type,
                account_profile=excluded.account_profile,
                capabilities=excluded.capabilities,
                execution_modes=excluded.execution_modes,
                status=excluded.status,
                last_heartbeat=excluded.last_heartbeat,
                idle_since=excluded.idle_since,
                stale_since=NULL,
                concurrency=excluded.concurrency""",
            (
                worker.worker_id,
                worker.machine,
                worker.cli_type.value,
                worker.account_profile,
                json.dumps(worker.capabilities),
                json.dumps(worker.execution_modes),
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
            "SELECT * FROM workers WHERE status IN ('idle', 'busy', 'draining') AND last_heartbeat < ?",
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

    def get_tasks_by_worker(self, worker_id: str, status: str | None = None) -> list[Task]:
        """Get tasks assigned to a worker, optionally filtered by status."""
        if status is not None:
            cur = self._conn.execute(
                "SELECT * FROM tasks WHERE assigned_worker = ? AND status = ? ORDER BY created_at ASC",
                (worker_id, status),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM tasks WHERE assigned_worker = ? ORDER BY created_at ASC",
                (worker_id,),
            )
        return [self._task_from_row(row) for row in cur.fetchall()]

    def count_tasks_by_status(self, status: str) -> int:
        """Count tasks with a given status."""
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = ?", (status,)
        )
        return cur.fetchone()[0]

    def count_all_task_statuses(self) -> dict[str, int]:
        """Count tasks grouped by status in a single query."""
        cur = self._conn.execute(
            "SELECT status, COUNT(*) FROM tasks GROUP BY status"
        )
        return {row[0]: row[1] for row in cur.fetchall()}

    def count_dead_letters(self) -> int:
        """Count dead letter events."""
        cur = self._conn.execute("SELECT COUNT(*) FROM dead_letter_events")
        return cur.fetchone()[0]

    # -- Threads --

    def _thread_from_row(self, row: sqlite3.Row) -> Thread:
        return Thread(
            thread_id=row["thread_id"],
            name=row["name"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @_retry_on_busy
    def insert_thread(self, thread: Thread, conn: sqlite3.Connection | None = None) -> Thread:
        c = conn or self._conn
        c.execute(
            """INSERT INTO threads (thread_id, name, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)""",
            (
                thread.thread_id,
                thread.name,
                thread.status.value if hasattr(thread.status, "value") else str(thread.status),
                thread.created_at,
                thread.updated_at,
            ),
        )
        if conn is None:
            self._conn.commit()
        return thread

    def get_thread(self, thread_id: str) -> Thread | None:
        cur = self._conn.execute(
            "SELECT * FROM threads WHERE thread_id = ?", (thread_id,)
        )
        row = cur.fetchone()
        return self._thread_from_row(row) if row else None

    def get_thread_by_name(self, name: str) -> Thread | None:
        cur = self._conn.execute(
            "SELECT * FROM threads WHERE name = ? LIMIT 1", (name,)
        )
        row = cur.fetchone()
        return self._thread_from_row(row) if row else None

    def list_threads(
        self,
        status: str | None = None,
        name: str | None = None,
        limit: int = 50,
    ) -> list[Thread]:
        sql = "SELECT * FROM threads"
        params: list[object] = []
        clauses: list[str] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if name is not None:
            clauses.append("name = ?")
            params.append(name)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        cur = self._conn.execute(sql, params)
        return [self._thread_from_row(row) for row in cur.fetchall()]

    @_retry_on_busy
    def update_thread(
        self,
        thread_id: str,
        updates: dict[str, str | int | None],
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        if not updates:
            return False
        c = conn or self._conn
        set_clauses = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [thread_id]
        cur = c.execute(
            f"UPDATE threads SET {set_clauses} WHERE thread_id = ?",
            values,
        )
        if conn is None:
            self._conn.commit()
        return cur.rowcount > 0

    def list_thread_steps(
        self, thread_id: str, status: str | None = None
    ) -> list[Task]:
        if status is not None:
            cur = self._conn.execute(
                "SELECT * FROM tasks WHERE thread_id = ? AND status = ? ORDER BY step_index ASC",
                (thread_id, status),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM tasks WHERE thread_id = ? ORDER BY step_index ASC",
                (thread_id,),
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
