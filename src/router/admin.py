"""Administrative maintenance helpers for router state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from time import gmtime, strftime

from src.router.db import RouterDB
from src.router.models import SessionState, TaskStatus, ThreadStatus
from src.router.thread import compute_thread_status

_TERMINAL_TASK_SESSION_STATES = {
    TaskStatus.completed: SessionState.closed.value,
    TaskStatus.failed: SessionState.errored.value,
    TaskStatus.timeout: SessionState.errored.value,
    TaskStatus.canceled: SessionState.errored.value,
}
_NON_TERMINAL_THREAD_STATES = {ThreadStatus.pending.value, ThreadStatus.active.value}


@dataclass(frozen=True)
class SessionCleanupCandidate:
    session_id: str
    task_id: str | None
    from_state: str
    to_state: str
    reason: str
    task_status: str | None = None


@dataclass(frozen=True)
class ThreadCleanupCandidate:
    thread_id: str
    from_status: str
    to_status: str
    reason: str


@dataclass(frozen=True)
class RuntimeCleanupReport:
    status: str
    backup_path: str | None
    sessions: list[SessionCleanupCandidate]
    threads: list[ThreadCleanupCandidate]
    updated_sessions: int
    updated_threads: int
    generated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "backup_path": self.backup_path,
            "sessions": [asdict(item) for item in self.sessions],
            "threads": [asdict(item) for item in self.threads],
            "updated_sessions": self.updated_sessions,
            "updated_threads": self.updated_threads,
            "generated_at": self.generated_at,
        }


def _validate_limit(name: str, value: int) -> int:
    if value < 1 or value > 10_000:
        raise ValueError(f"{name} must be between 1 and 10000")
    return value


def find_stale_runtime_state(
    db: RouterDB,
    *,
    session_limit: int = 1000,
    thread_limit: int = 1000,
) -> tuple[list[SessionCleanupCandidate], list[ThreadCleanupCandidate]]:
    """Collect conservative cleanup candidates for stale sessions/threads."""
    session_limit = _validate_limit("session_limit", session_limit)
    thread_limit = _validate_limit("thread_limit", thread_limit)

    session_candidates: list[SessionCleanupCandidate] = []
    for session in db.list_sessions(state=SessionState.open.value, limit=session_limit):
        if not session.task_id:
            continue
        task = db.get_task(session.task_id)
        if task is None:
            session_candidates.append(
                SessionCleanupCandidate(
                    session_id=session.session_id,
                    task_id=session.task_id,
                    from_state=SessionState.open.value,
                    to_state=SessionState.closed.value,
                    reason="missing_task",
                )
            )
            continue

        next_state = _TERMINAL_TASK_SESSION_STATES.get(task.status)
        if not next_state:
            continue
        session_candidates.append(
            SessionCleanupCandidate(
                session_id=session.session_id,
                task_id=session.task_id,
                from_state=SessionState.open.value,
                to_state=next_state,
                reason=f"task_{task.status.value}",
                task_status=task.status.value,
            )
        )

    thread_candidates: list[ThreadCleanupCandidate] = []
    seen_threads: set[str] = set()
    for status in sorted(_NON_TERMINAL_THREAD_STATES):
        for thread in db.list_threads(status=status, limit=thread_limit):
            if thread.thread_id in seen_threads:
                continue
            seen_threads.add(thread.thread_id)

            computed = compute_thread_status(db, thread.thread_id)
            if computed.value == thread.status.value:
                continue
            if computed.value not in {ThreadStatus.completed.value, ThreadStatus.failed.value}:
                continue

            thread_candidates.append(
                ThreadCleanupCandidate(
                    thread_id=thread.thread_id,
                    from_status=thread.status.value,
                    to_status=computed.value,
                    reason="computed_terminal_status",
                )
            )

    return session_candidates, thread_candidates


def cleanup_stale_runtime_state(
    db: RouterDB,
    *,
    apply: bool = False,
    create_backup: bool = True,
    session_limit: int = 1000,
    thread_limit: int = 1000,
) -> RuntimeCleanupReport:
    """Close stale open sessions and reconcile stale thread statuses.

    Dry-run by default. Only sessions linked to missing or terminal tasks are touched.
    Task-less open sessions are ignored deliberately because they can be operator-owned.
    """
    sessions, threads = find_stale_runtime_state(
        db,
        session_limit=session_limit,
        thread_limit=thread_limit,
    )

    backup_path: str | None = None
    updated_sessions = 0
    updated_threads = 0
    if apply and (sessions or threads):
        if create_backup:
            backup_path = db.create_backup()
        now = strftime("%Y-%m-%dT%H:%M:%SZ", gmtime())
        with db.transaction() as conn:
            for session in sessions:
                cur = conn.execute(
                    """UPDATE sessions
                       SET state = ?, updated_at = ?
                       WHERE session_id = ? AND state = ?""",
                    (session.to_state, now, session.session_id, SessionState.open.value),
                )
                updated_sessions += cur.rowcount
            for thread in threads:
                cur = conn.execute(
                    """UPDATE threads
                       SET status = ?, updated_at = ?
                       WHERE thread_id = ? AND status IN (?, ?)""",
                    (
                        thread.to_status,
                        now,
                        thread.thread_id,
                        ThreadStatus.pending.value,
                        ThreadStatus.active.value,
                    ),
                )
                updated_threads += cur.rowcount

    return RuntimeCleanupReport(
        status="applied" if apply else "dry_run",
        backup_path=backup_path,
        sessions=sessions,
        threads=threads,
        updated_sessions=updated_sessions,
        updated_threads=updated_threads,
        generated_at=strftime("%Y-%m-%dT%H:%M:%SZ", gmtime()),
    )
