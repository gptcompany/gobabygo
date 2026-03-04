"""Pydantic models for the AI Mesh Router.

All timestamps are UTC ISO-8601. All IDs are UUID4 strings.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utc_now() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _uuid4() -> str:
    """Return a new UUID4 string."""
    return str(uuid.uuid4())


class ThreadStatus(str, Enum):
    pending = "pending"
    active = "active"
    completed = "completed"
    failed = "failed"


class TaskStatus(str, Enum):
    queued = "queued"
    assigned = "assigned"
    blocked = "blocked"
    running = "running"
    review = "review"
    completed = "completed"
    failed = "failed"
    timeout = "timeout"
    canceled = "canceled"


class TaskPhase(str, Enum):
    plan = "plan"
    implement = "implement"
    test = "test"
    integrate = "integrate"
    release = "release"


class CommunicationRole(str, Enum):
    boss = "boss"
    president = "president"
    worker = "worker"


class CLIType(str, Enum):
    claude = "claude"
    codex = "codex"
    gemini = "gemini"


class ExecutionMode(str, Enum):
    batch = "batch"
    session = "session"


class OnFailurePolicy(str, Enum):
    abort = "abort"
    skip = "skip"
    retry = "retry"


class Task(BaseModel):
    task_id: str = Field(default_factory=_uuid4)
    parent_task_id: str | None = None
    phase: TaskPhase = TaskPhase.implement
    title: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    target_cli: CLIType = CLIType.claude
    target_account: str = "default"
    execution_mode: ExecutionMode = ExecutionMode.batch
    priority: int = 1
    deadline_ts: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.queued
    assigned_worker: str | None = None
    session_id: str | None = None
    lease_expires_at: str | None = None
    attempt: int = 1
    not_before: str | None = None
    created_by: str | None = None
    critical: bool = False
    rejection_count: int = 0
    review_timeout_at: str | None = None
    idempotency_key: str = Field(default_factory=_uuid4)
    result: dict[str, Any] | None = None
    # Thread fields (nullable -- non-thread tasks have these as None)
    thread_id: str | None = None
    step_index: int | None = None
    repo: str | None = None
    role: str | None = None
    on_failure: OnFailurePolicy = OnFailurePolicy.abort
    created_at: str = Field(default_factory=_utc_now)
    updated_at: str = Field(default_factory=_utc_now)


class Thread(BaseModel):
    thread_id: str = Field(default_factory=_uuid4)
    name: str
    status: ThreadStatus = ThreadStatus.pending
    created_at: str = Field(default_factory=_utc_now)
    updated_at: str = Field(default_factory=_utc_now)


class ThreadCreateRequest(BaseModel):
    name: str


class ThreadStepRequest(BaseModel):
    """Add a step to a thread. Maps to a Task with thread fields set."""

    title: str
    step_index: int
    repo: str = ""
    target_cli: CLIType = CLIType.claude
    target_account: str = "work"
    execution_mode: ExecutionMode = ExecutionMode.batch
    payload: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    priority: int = 1
    critical: bool = False
    on_failure: OnFailurePolicy = OnFailurePolicy.abort


class TaskCreateRequest(BaseModel):
    """Public API schema for task submission. Subset of Task fields."""

    title: str
    phase: TaskPhase = TaskPhase.implement
    payload: dict[str, Any] = Field(default_factory=dict)
    target_cli: CLIType = CLIType.claude
    target_account: str = "work"
    execution_mode: ExecutionMode = ExecutionMode.batch
    priority: int = 1
    depends_on: list[str] = Field(default_factory=list)
    deadline_ts: str | None = None
    not_before: str | None = None
    critical: bool = False
    idempotency_key: str = Field(default_factory=_uuid4)


class TaskEvent(BaseModel):
    event_id: str = Field(default_factory=_uuid4)
    task_id: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(default_factory=_uuid4)
    ts: str = Field(default_factory=_utc_now)


class Worker(BaseModel):
    worker_id: str = Field(default_factory=_uuid4)
    machine: str = ""
    cli_type: CLIType = CLIType.claude
    account_profile: str = "default"
    capabilities: list[str] = Field(default_factory=list)
    execution_modes: list[str] = Field(default_factory=lambda: [ExecutionMode.batch.value])
    role: str = CommunicationRole.worker.value
    status: str = "idle"
    last_heartbeat: str = Field(default_factory=_utc_now)
    idle_since: str = Field(default_factory=_utc_now)
    stale_since: str | None = None
    concurrency: int = 1


class Lease(BaseModel):
    lease_id: str = Field(default_factory=_uuid4)
    task_id: str
    worker_id: str
    granted_at: str = Field(default_factory=_utc_now)
    expires_at: str


class SessionState(str, Enum):
    open = "open"
    closed = "closed"
    errored = "errored"


class Session(BaseModel):
    session_id: str = Field(default_factory=_uuid4)
    worker_id: str
    cli_type: CLIType = CLIType.claude
    account_profile: str = "default"
    task_id: str | None = None
    state: SessionState = SessionState.open
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utc_now)
    updated_at: str = Field(default_factory=_utc_now)


class SessionMessage(BaseModel):
    session_id: str
    direction: str = "in"  # in|out|system (relative to session process)
    role: str = "operator"  # operator|worker|cli|system
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    seq: int | None = None
    ts: str = Field(default_factory=_utc_now)
