from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.router.admin import cleanup_stale_runtime_state, find_stale_runtime_state
from src.router.db import RouterDB
from src.router.models import (
    CLIType,
    Session,
    Task,
    TaskPhase,
    TaskStatus,
    Thread,
    ThreadStatus,
    Worker,
)


@pytest.fixture
def db(tmp_path: Path) -> RouterDB:
    db = RouterDB(str(tmp_path / "router.db"), check_same_thread=False)
    db.init_schema()
    yield db
    db.close()


def _worker() -> Worker:
    return Worker(
        worker_id="w1",
        machine="ws",
        cli_type=CLIType.claude,
        account_profile="work",
    )


def _task(task_id: str, *, status: TaskStatus, thread_id: str | None = None, step_index: int | None = None) -> Task:
    return Task(
        task_id=task_id,
        title=task_id,
        phase=TaskPhase.implement,
        target_cli=CLIType.claude,
        target_account="work",
        status=status,
        thread_id=thread_id,
        step_index=step_index,
        idempotency_key=f"idem-{task_id}",
    )


def test_find_stale_runtime_state_collects_only_safe_candidates(db: RouterDB) -> None:
    db.insert_worker(_worker())
    db.insert_task(_task("task-running", status=TaskStatus.running))
    db.insert_task(_task("task-complete", status=TaskStatus.completed))
    db.insert_task(_task("task-failed", status=TaskStatus.failed))

    db.insert_session(Session(session_id="sess-running", worker_id="w1", task_id="task-running"))
    db.insert_session(Session(session_id="sess-complete", worker_id="w1", task_id="task-complete"))
    db.insert_session(Session(session_id="sess-failed", worker_id="w1", task_id="task-failed"))
    db.insert_session(Session(session_id="sess-missing", worker_id="w1", task_id="task-missing"))
    db.insert_session(Session(session_id="sess-taskless", worker_id="w1", task_id=None))
    db.insert_session(
        Session(session_id="sess-closed", worker_id="w1", task_id="task-failed", state="closed")
    )

    db.insert_thread(Thread(thread_id="thread-failed", name="thread-failed", status=ThreadStatus.active))
    db.insert_task(_task("thread-failed-step", status=TaskStatus.failed, thread_id="thread-failed", step_index=0))

    db.insert_thread(
        Thread(thread_id="thread-complete", name="thread-complete", status=ThreadStatus.active)
    )
    db.insert_task(
        _task("thread-complete-step", status=TaskStatus.completed, thread_id="thread-complete", step_index=0)
    )

    db.insert_thread(Thread(thread_id="thread-pending", name="thread-pending", status=ThreadStatus.pending))

    sessions, threads, skipped_taskless = find_stale_runtime_state(db)

    assert sorted((item.session_id, item.to_state, item.reason) for item in sessions) == [
        ("sess-complete", "closed", "task_completed"),
        ("sess-failed", "errored", "task_failed"),
        ("sess-missing", "closed", "missing_task"),
    ]
    assert sorted((item.thread_id, item.to_status) for item in threads) == [
        ("thread-complete", "completed"),
        ("thread-failed", "failed"),
    ]
    assert skipped_taskless == 1


def test_find_stale_runtime_state_can_include_taskless_sessions(db: RouterDB) -> None:
    db.insert_worker(_worker())
    db.insert_session(Session(session_id="sess-taskless", worker_id="w1", task_id=None))

    sessions, threads, skipped_taskless = find_stale_runtime_state(
        db,
        include_taskless_sessions=True,
    )

    assert [(item.session_id, item.to_state, item.reason) for item in sessions] == [
        ("sess-taskless", "closed", "taskless_session")
    ]
    assert threads == []
    assert skipped_taskless == 0


def test_cleanup_stale_runtime_state_apply_updates_rows_and_creates_backup(db: RouterDB, tmp_path: Path) -> None:
    db.insert_worker(_worker())
    db.insert_task(_task("task-failed", status=TaskStatus.failed))
    db.insert_session(Session(session_id="sess-failed", worker_id="w1", task_id="task-failed"))
    db.insert_thread(Thread(thread_id="thread-failed", name="thread-failed", status=ThreadStatus.active))
    db.insert_task(_task("thread-step", status=TaskStatus.failed, thread_id="thread-failed", step_index=0))

    report = cleanup_stale_runtime_state(db, apply=True, create_backup=True)

    assert report.status == "applied"
    assert report.updated_sessions == 1
    assert report.updated_threads == 1
    assert report.backup_path is not None
    assert Path(report.backup_path).is_file()

    session = db.get_session("sess-failed")
    thread = db.get_thread("thread-failed")
    assert session is not None
    assert thread is not None
    assert session.state.value == "errored"
    assert thread.status.value == "failed"

    backup_conn = sqlite3.connect(report.backup_path)
    try:
        state = backup_conn.execute(
            "select state from sessions where session_id = ?",
            ("sess-failed",),
        ).fetchone()
        assert state is not None
        assert state[0] == "open"
    finally:
        backup_conn.close()


def test_cleanup_stale_runtime_state_dry_run_makes_no_changes(db: RouterDB) -> None:
    db.insert_worker(_worker())
    db.insert_task(_task("task-failed", status=TaskStatus.failed))
    db.insert_session(Session(session_id="sess-failed", worker_id="w1", task_id="task-failed"))

    report = cleanup_stale_runtime_state(db, apply=False)

    assert report.status == "dry_run"
    assert report.updated_sessions == 0
    assert report.backup_path is None
    session = db.get_session("sess-failed")
    assert session is not None
    assert session.state.value == "open"


def test_cleanup_stale_runtime_state_reports_skipped_taskless_sessions(db: RouterDB) -> None:
    db.insert_worker(_worker())
    db.insert_session(Session(session_id="sess-taskless", worker_id="w1", task_id=None))

    report = cleanup_stale_runtime_state(db, apply=False)

    assert report.skipped_taskless_sessions == 1


def test_find_stale_runtime_state_rejects_invalid_limits(db: RouterDB) -> None:
    with pytest.raises(ValueError):
        find_stale_runtime_state(db, session_limit=0)
    with pytest.raises(ValueError):
        find_stale_runtime_state(db, thread_limit=10001)
