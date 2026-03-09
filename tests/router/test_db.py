"""Unit tests for the RouterDB persistence layer."""

from __future__ import annotations

import os
import tempfile
import uuid

import pytest

from src.router.db import RouterDB, _decode_json_blob
from src.router.models import (
    CLIType,
    ExecutionMode,
    Lease,
    NotificationLedgerEntry,
    Session,
    SessionMessage,
    Task,
    TaskEvent,
    TaskPhase,
    TaskStatus,
    Worker,
)


@pytest.fixture
def db() -> RouterDB:
    """Create an in-memory RouterDB with schema initialized."""
    rdb = RouterDB(":memory:")
    rdb.init_schema()
    return rdb


@pytest.fixture
def sample_task() -> Task:
    return Task(
        title="Implement feature X",
        phase=TaskPhase.implement,
        target_cli=CLIType.claude,
        target_account="work",
        priority=2,
    )


@pytest.fixture
def sample_worker() -> Worker:
    return Worker(
        machine="vps-01",
        cli_type=CLIType.claude,
        account_profile="work",
        capabilities=["python", "typescript"],
        status="idle",
        concurrency=2,
    )


# -- Schema --


def test_schema_creation(db: RouterDB) -> None:
    """init_schema creates all 4 tables."""
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cur.fetchall()]
    assert "leases" in tables
    assert "task_events" in tables
    assert "tasks" in tables
    assert "workers" in tables


def test_wal_mode_enabled() -> None:
    """WAL mode is enabled for file-based databases."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        rdb = RouterDB(db_path)
        rdb.init_schema()
        mode = rdb._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal", f"Expected WAL mode, got {mode}"
        rdb.close()


# -- Tasks CRUD --


def test_insert_and_get_task(db: RouterDB, sample_task: Task) -> None:
    """Round-trip task insert and get."""
    db.insert_task(sample_task)
    retrieved = db.get_task(sample_task.task_id)
    assert retrieved is not None
    assert retrieved.task_id == sample_task.task_id
    assert retrieved.title == "Implement feature X"
    assert retrieved.status == TaskStatus.queued
    assert retrieved.phase == TaskPhase.implement
    assert retrieved.target_cli == CLIType.claude
    assert retrieved.execution_mode == ExecutionMode.batch
    assert retrieved.priority == 2
    assert retrieved.attempt == 1


def test_get_task_not_found(db: RouterDB) -> None:
    """get_task returns None for nonexistent task_id."""
    assert db.get_task("nonexistent-id") is None


def test_update_task_status_cas(db: RouterDB, sample_task: Task) -> None:
    """CAS update succeeds with correct old_status, fails with wrong old_status."""
    db.insert_task(sample_task)

    # Correct old_status -> success
    result = db.update_task_status(
        sample_task.task_id, TaskStatus.queued, TaskStatus.assigned
    )
    assert result is True

    # Verify status actually changed
    task = db.get_task(sample_task.task_id)
    assert task is not None
    assert task.status == TaskStatus.assigned

    # Wrong old_status -> failure
    result = db.update_task_status(
        sample_task.task_id, TaskStatus.queued, TaskStatus.running
    )
    assert result is False

    # Status unchanged after failed CAS
    task = db.get_task(sample_task.task_id)
    assert task is not None
    assert task.status == TaskStatus.assigned


def test_concurrent_update_rejected(db: RouterDB, sample_task: Task) -> None:
    """Two CAS updates on same task: one succeeds, one fails."""
    db.insert_task(sample_task)

    # Both try to transition from queued -> assigned
    result1 = db.update_task_status(
        sample_task.task_id, TaskStatus.queued, TaskStatus.assigned
    )
    result2 = db.update_task_status(
        sample_task.task_id, TaskStatus.queued, TaskStatus.running
    )

    assert result1 is True
    assert result2 is False

    task = db.get_task(sample_task.task_id)
    assert task is not None
    assert task.status == TaskStatus.assigned


# -- Events --


def test_insert_event_idempotent(db: RouterDB, sample_task: Task) -> None:
    """First insert succeeds, duplicate idempotency_key returns False."""
    db.insert_task(sample_task)

    idem_key = str(uuid.uuid4())
    event = TaskEvent(
        task_id=sample_task.task_id,
        event_type="task_created",
        idempotency_key=idem_key,
    )

    # First insert succeeds
    assert db.insert_event(event) is True

    # Duplicate idempotency_key rejected
    event2 = TaskEvent(
        task_id=sample_task.task_id,
        event_type="task_created",
        idempotency_key=idem_key,
    )
    assert db.insert_event(event2) is False


def test_get_events_ordered(db: RouterDB, sample_task: Task) -> None:
    """Events returned in chronological order (by event_id ASC)."""
    db.insert_task(sample_task)

    events = []
    for i in range(5):
        ev = TaskEvent(
            task_id=sample_task.task_id,
            event_type=f"event_{i}",
        )
        db.insert_event(ev)
        events.append(ev)

    retrieved = db.get_events(sample_task.task_id)
    assert len(retrieved) == 5
    for i, ev in enumerate(retrieved):
        assert ev.event_type == f"event_{i}"


# -- Workers --


def test_worker_crud(db: RouterDB, sample_worker: Worker) -> None:
    """Insert, get, and list workers."""
    db.insert_worker(sample_worker)

    # Get by ID
    retrieved = db.get_worker(sample_worker.worker_id)
    assert retrieved is not None
    assert retrieved.worker_id == sample_worker.worker_id
    assert retrieved.machine == "vps-01"
    assert retrieved.cli_type == CLIType.claude
    assert retrieved.capabilities == ["python", "typescript"]
    assert retrieved.execution_modes == ["batch"]
    assert retrieved.concurrency == 2

    # List all
    all_workers = db.list_workers()
    assert len(all_workers) == 1

    # List by status
    idle_workers = db.list_workers(status="idle")
    assert len(idle_workers) == 1
    busy_workers = db.list_workers(status="busy")
    assert len(busy_workers) == 0

    # Get nonexistent
    assert db.get_worker("nonexistent") is None


# -- Leases --


def test_lease_crud(db: RouterDB, sample_task: Task, sample_worker: Worker) -> None:
    """Create, get_active, and expire a lease."""
    db.insert_task(sample_task)
    db.insert_worker(sample_worker)

    lease = Lease(
        task_id=sample_task.task_id,
        worker_id=sample_worker.worker_id,
        expires_at="2026-12-31T23:59:59+00:00",
    )
    db.create_lease(lease)

    # Get active lease
    active = db.get_active_lease(sample_task.task_id)
    assert active is not None
    assert active.lease_id == lease.lease_id
    assert active.task_id == sample_task.task_id
    assert active.worker_id == sample_worker.worker_id

    # Renew lease
    renewed_expiry = "2027-01-01T00:00:00+00:00"
    result = db.renew_lease(lease.lease_id, renewed_expiry)
    assert result is True
    renewed = db.get_active_lease(sample_task.task_id)
    assert renewed is not None
    assert renewed.expires_at == renewed_expiry

    # Expire lease
    result = db.expire_lease(lease.lease_id)
    assert result is True

    # No longer active
    assert db.get_active_lease(sample_task.task_id) is None

    # Double expire returns False
    result = db.expire_lease(lease.lease_id)
    assert result is False

    # Renewing a missing lease returns False
    result = db.renew_lease(lease.lease_id, renewed_expiry)
    assert result is False


# -- Sessions --


def test_session_crud_and_messages(db: RouterDB, sample_worker: Worker) -> None:
    db.insert_worker(sample_worker)
    session = Session(
        worker_id=sample_worker.worker_id,
        cli_type=sample_worker.cli_type,
        account_profile=sample_worker.account_profile,
        metadata={"pane": "claude-1"},
    )
    db.insert_session(session)

    retrieved = db.get_session(session.session_id)
    assert retrieved is not None
    assert retrieved.worker_id == sample_worker.worker_id
    assert retrieved.metadata["pane"] == "claude-1"

    seq1 = db.append_session_message(SessionMessage(
        session_id=session.session_id,
        direction="in",
        role="operator",
        content="hello",
    ))
    seq2 = db.append_session_message(SessionMessage(
        session_id=session.session_id,
        direction="out",
        role="cli",
        content="hi",
    ))
    assert seq2 > seq1

    msgs = db.list_session_messages(session.session_id, after_seq=0)
    assert [m.content for m in msgs] == ["hello", "hi"]

    ok = db.update_session(session.session_id, {"state": "closed"})
    assert ok is True
    closed = db.get_session(session.session_id)
    assert closed is not None
    assert str(getattr(closed.state, "value", closed.state)) == "closed"


def test_notification_ledger_roundtrip(db: RouterDB) -> None:
    entry = NotificationLedgerEntry(
        trace_id="ntf_1234567890abcdef0123",
        trigger="approval_needed",
        room_id="!room:matrix.example",
        status="sent",
        repo="rektslug",
        task_id="task-1",
        metadata={"source": "bridge"},
    )
    notification_id = db.insert_notification_ledger(entry)
    assert notification_id >= 1

    rows = db.list_notification_ledger(trace_id="ntf_1234567890abcdef0123")
    assert len(rows) == 1
    assert rows[0].trigger == "approval_needed"
    assert rows[0].room_id == "!room:matrix.example"
    assert rows[0].status.value == "sent"
    assert rows[0].metadata["source"] == "bridge"


def test_notification_ledger_duplicate_dedup(db: RouterDB) -> None:
    trace = "ntf_abc1234567890abcdef0123"
    entry1 = NotificationLedgerEntry(
        trace_id=trace, trigger="approval_needed", room_id="!r1", status="sent"
    )
    # Different trigger or metadata but same trace+room
    entry2 = NotificationLedgerEntry(
        trace_id=trace, trigger="thread_failed", room_id="!r1", status="failed"
    )
    # Same trace, different room
    entry3 = NotificationLedgerEntry(
        trace_id=trace, trigger="approval_needed", room_id="!r2", status="sent"
    )

    ok1, id1 = db.insert_notification_ledger_once(entry1)
    assert ok1 is True
    assert id1 is not None

    ok2, id2 = db.insert_notification_ledger_once(entry2)
    assert ok2 is False
    assert id2 is None

    ok3, id3 = db.insert_notification_ledger_once(entry3)
    assert ok3 is True
    assert id3 is not None

    rows = db.list_notification_ledger(trace_id=trace)
    assert len(rows) == 2
    room_ids = {r.room_id for r in rows}
    assert room_ids == {"!r1", "!r2"}


def test_decode_json_blob_tolerates_null_metadata() -> None:
    assert _decode_json_blob(None, default={}) == {}
    assert _decode_json_blob("", default={}) == {}


# -- Transaction context manager --


def test_transaction_commit(db: RouterDB, sample_task: Task, sample_worker: Worker) -> None:
    """Transaction commits on success, making all changes visible."""
    db.insert_task(sample_task)
    db.insert_worker(sample_worker)

    with db.transaction() as conn:
        db.update_task_status(
            sample_task.task_id, TaskStatus.queued, TaskStatus.assigned, conn=conn
        )
        lease = Lease(
            task_id=sample_task.task_id,
            worker_id=sample_worker.worker_id,
            expires_at="2026-12-31T23:59:59+00:00",
        )
        db.create_lease(lease, conn=conn)
        event = TaskEvent(
            task_id=sample_task.task_id,
            event_type="task_assigned",
        )
        db.insert_event(event, conn=conn)

    # All changes visible after commit
    task = db.get_task(sample_task.task_id)
    assert task is not None
    assert task.status == TaskStatus.assigned
    assert db.get_active_lease(sample_task.task_id) is not None
    assert len(db.get_events(sample_task.task_id)) == 1


def test_transaction_rollback(db: RouterDB, sample_task: Task, sample_worker: Worker) -> None:
    """Transaction rolls back on exception, no partial changes."""
    db.insert_task(sample_task)
    db.insert_worker(sample_worker)

    with pytest.raises(ValueError, match="deliberate"):
        with db.transaction() as conn:
            db.update_task_status(
                sample_task.task_id, TaskStatus.queued, TaskStatus.assigned, conn=conn
            )
            raise ValueError("deliberate error")

    # Status unchanged after rollback
    task = db.get_task(sample_task.task_id)
    assert task is not None
    assert task.status == TaskStatus.queued
    assert db.get_active_lease(sample_task.task_id) is None


def test_insert_notification_ledger_once_duplicate_does_not_leave_open_transaction(db: RouterDB) -> None:
    trace = "ntf_abc1234567890abcdef0124"
    # Use NotificationLedgerEntry directly for test (lenient)
    entry = NotificationLedgerEntry(
        trace_id=trace, trigger="approval_needed", room_id="!r1", status="sent"
    )
    # First insert
    ok, _ = db.insert_notification_ledger_once(entry)
    assert ok is True
    assert db._conn.in_transaction is False

    # Second insert (duplicate)
    ok, _ = db.insert_notification_ledger_once(entry)
    assert ok is False
    # This check confirms BLOCKER-1 fix: no open transaction left after duplicate return
    assert db._conn.in_transaction is False


def test_list_notification_ledger_lenient_read_legacy_data(db: RouterDB) -> None:
    # Directly insert legacy data that would fail strict validation
    legacy_trace = "legacy_bad_trace"
    legacy_trigger = "old_unknown_trigger"
    db._conn.execute(
        "INSERT INTO notification_ledger (trace_id, trigger, room_id, status, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?)",
        (legacy_trace, legacy_trigger, "!r1", "sent", "2026-01-01T00:00:00Z", "{}")
    )
    db._conn.commit()

    # Reading should NOT raise ValidationError (BLOCKER-2 fix)
    rows = db.list_notification_ledger(trace_id=legacy_trace)
    assert len(rows) == 1
    assert rows[0].trace_id == legacy_trace
    assert rows[0].trigger == legacy_trigger
