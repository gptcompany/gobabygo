#!/usr/bin/env python3
"""Transactional review ledger for bound Spec Kit tasks."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterator, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.mesh_speckit_cli import (  # noqa: E402
    SpeckitRuntimeError,
    normalize_immutable_review_scope,
)
from scripts.mesh_speckit_github import (  # noqa: E402
    LedgerError,
    LoadedFeature,
    load_feature,
    task_key,
)
from src.pipeline_templates import (  # noqa: E402
    default_pipeline_template_file,
    load_pipeline_templates,
    normalized_review_convergence,
)


SCHEMA = "mesh.speckit.review-ledger.v1"
LEDGER_FILE = "review-ledger.json"
MAX_LEDGER_BYTES = 1024 * 1024
MAX_EVENTS_PER_TASK = 128
MAX_INVARIANTS = 32
MAX_MUTATION_BUDGET = 32
LEVELS = {"DELTA", "INVARIANT", "RELEASE"}
VERDICTS = {"PASS", "CHANGES_REQUIRED"}
DECISIONS = {"REPLAN", "ESCALATE", "BACKLOG"}
TERMINAL_STATUSES = {"RELEASE_PASSED", "ESCALATED", "BACKLOGGED"}
STATUSES = {
    "READY_FOR_REVIEW",
    "REVIEW_OPEN",
    "CHANGES_REQUIRED",
    "CORRECTION_OPEN",
    "CANDIDATE_UPDATE_REQUIRED",
    "REVIEW_BUDGET_EXHAUSTED",
    "REPLAN_REQUIRED",
    *TERMINAL_STATUSES,
}
_SAFE_TASK = re.compile(r"^T[0-9]{3,}$")
_SAFE_SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_DELEGATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_ROOT_FIELDS = {"schema", "feature_key", "revision", "tasks"}
_TASK_FIELDS = {
    "task_key",
    "cycle",
    "frozen_scope",
    "writer_session",
    "invariants",
    "mutation_budget",
    "status",
    "correction_round",
    "active_review",
    "last_findings",
    "events",
}
_ACTIVE_REVIEW_FIELDS = {
    "level",
    "scope",
    "round",
    "reviewer",
    "delegation_id",
    "invariant",
}
_REVIEW_POLICY = normalized_review_convergence(
    load_pipeline_templates(default_pipeline_template_file())
)["reviewer_timeout"]
REVIEW_TIMEOUT_MINUTES = int(_REVIEW_POLICY["minutes"])
MAX_REVIEWER_FALLBACKS = int(_REVIEW_POLICY["max_fallbacks"])


class ReviewLedgerError(ValueError):
    """Raised when a review transaction is invalid or unsafe."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewLedgerError(f"invalid {label}") from exc
    if parsed.tzinfo is None:
        raise ReviewLedgerError(f"invalid {label}")
    return parsed.astimezone(timezone.utc)


def _matching_review_timeouts(
    record: dict[str, Any], active: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        event
        for event in record["events"]
        if event["type"] == "review_timed_out"
        and all(
            event["data"].get(key) == active[key]
            for key in ("level", "scope", "round", "invariant")
        )
    ]


def _active_review_timing(record: dict[str, Any]) -> tuple[str, str, int]:
    active = record.get("active_review")
    if not isinstance(active, dict):
        raise ReviewLedgerError("review state is missing active review metadata")
    opened = next(
        (
            event
            for event in reversed(record["events"])
            if event["type"] == "review_opened"
            and event["data"].get("delegation_id") == active["delegation_id"]
        ),
        None,
    )
    if opened is None:
        raise ReviewLedgerError("review state is missing its opening event")
    opened_at = _timestamp(opened["at"], "review opening timestamp")
    deadline = opened_at + timedelta(minutes=REVIEW_TIMEOUT_MINUTES)
    fallback_attempt = len(_matching_review_timeouts(record, active))
    return opened_at.isoformat(), deadline.isoformat(), fallback_attempt


def _bounded_text(value: str, label: str, *, maximum: int = 512) -> str:
    text = " ".join(str(value or "").split())
    if not text or len(text) > maximum:
        raise ReviewLedgerError(f"{label} must contain 1-{maximum} characters")
    return text


def _normalize_task_id(value: str) -> str:
    task_id = str(value or "").strip().upper()
    if not _SAFE_TASK.fullmatch(task_id):
        raise ReviewLedgerError("task must use canonical Tnnn identity")
    return task_id


def _normalize_session(value: str, label: str) -> str:
    session = str(value or "").strip()
    if not _SAFE_SESSION.fullmatch(session):
        raise ReviewLedgerError(f"invalid {label}")
    return session


def _normalize_delegation(value: str) -> str:
    delegation = str(value or "").strip()
    if not _SAFE_DELEGATION.fullmatch(delegation):
        raise ReviewLedgerError("invalid delegation ID")
    return delegation


def _normalize_invariants(values: Sequence[str]) -> list[str]:
    if len(values) > MAX_INVARIANTS:
        raise ReviewLedgerError(f"at most {MAX_INVARIANTS} invariants are allowed")
    result: list[str] = []
    for value in values:
        invariant = _bounded_text(value, "invariant", maximum=256)
        if invariant in result:
            raise ReviewLedgerError(f"duplicate invariant: {invariant}")
        result.append(invariant)
    return result


def _load_bound_task(
    repo: Path, feature_dir: Path, task_id: str
) -> tuple[LoadedFeature, Any, str]:
    try:
        feature = load_feature(repo, feature_dir)
    except LedgerError as exc:
        raise ReviewLedgerError(str(exc)) from exc
    normalized = _normalize_task_id(task_id)
    task = next((item for item in feature.tasks if item.task_id == normalized), None)
    if task is None:
        raise ReviewLedgerError(f"task not found in tasks.md: {normalized}")
    if task.completed:
        raise ReviewLedgerError(f"task is already completed in tasks.md: {normalized}")
    return feature, task, task_key(feature.binding, task)


def _feature_key(feature: LoadedFeature) -> str:
    return f"{feature.binding.repository}:{feature.binding.feature_id}"


def _ledger_path(feature: LoadedFeature) -> Path:
    return feature.feature_dir / LEDGER_FILE


def _git_internal_path(repo: Path, name: str) -> Path:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-path", name],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReviewLedgerError(f"cannot resolve Git lock path: {exc}") from exc
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ReviewLedgerError("cannot resolve Git lock path")
    path = Path(proc.stdout.strip())
    return path if path.is_absolute() else (repo / path).resolve()


@contextmanager
def _ledger_lock(feature: LoadedFeature) -> Iterator[None]:
    digest = hashlib.sha256(
        feature.feature_dir.relative_to(feature.repo_root).as_posix().encode("utf-8")
    ).hexdigest()[:24]
    path = _git_internal_path(
        feature.repo_root, f"mesh-speckit-review/{digest}.lock"
    )
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ReviewLedgerError(f"cannot open review ledger lock: {exc}") from exc
    locked = False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError as exc:
            raise ReviewLedgerError("another review ledger transaction is active") from exc
        yield
    finally:
        if locked:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_bytes(path: Path) -> bytes | None:
    if not path.exists():
        if path.is_symlink():
            raise ReviewLedgerError(f"review ledger must not be a symlink: {path}")
        return None
    if path.is_symlink() or not path.is_file():
        raise ReviewLedgerError(f"review ledger must be a regular file: {path}")
    try:
        if path.stat().st_size > MAX_LEDGER_BYTES:
            raise ReviewLedgerError("review ledger exceeds 1 MiB")
        return path.read_bytes()
    except OSError as exc:
        raise ReviewLedgerError(f"cannot read review ledger: {exc}") from exc


def _validate_event(event: Any, task_id: str, ledger_revision: int) -> int:
    if not isinstance(event, dict) or set(event) != {"revision", "at", "type", "data"}:
        raise ReviewLedgerError(f"invalid event in task {task_id}")
    if (
        isinstance(event["revision"], bool)
        or not isinstance(event["revision"], int)
        or not 1 <= event["revision"] <= ledger_revision
    ):
        raise ReviewLedgerError(f"invalid event revision in task {task_id}")
    if not isinstance(event["at"], str) or len(event["at"]) > 64:
        raise ReviewLedgerError(f"invalid event timestamp in task {task_id}")
    if not isinstance(event["type"], str) or len(event["type"]) > 64:
        raise ReviewLedgerError(f"invalid event type in task {task_id}")
    if not isinstance(event["data"], dict):
        raise ReviewLedgerError(f"invalid event data in task {task_id}")
    return event["revision"]


def _validate_task_record(
    task_id: str, record: Any, feature_key: str, ledger_revision: int
) -> None:
    if not isinstance(record, dict) or set(record) != _TASK_FIELDS:
        raise ReviewLedgerError(f"invalid task record fields: {task_id}")
    if record["task_key"] != f"{feature_key}:{task_id}":
        raise ReviewLedgerError(f"task key mismatch: {task_id}")
    for key in ("cycle", "mutation_budget", "correction_round"):
        value = record[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ReviewLedgerError(f"invalid {key} in task {task_id}")
    if record["cycle"] < 1 or record["correction_round"] > 2:
        raise ReviewLedgerError(f"invalid cycle or correction round in task {task_id}")
    normalize_immutable_review_scope(record["frozen_scope"])
    _normalize_session(record["writer_session"], "writer session")
    if _normalize_invariants(record["invariants"]) != record["invariants"]:
        raise ReviewLedgerError(f"non-canonical invariants in task {task_id}")
    if record["mutation_budget"] > MAX_MUTATION_BUDGET:
        raise ReviewLedgerError(f"mutation budget is too large in task {task_id}")
    if record["status"] not in STATUSES:
        raise ReviewLedgerError(f"invalid status in task {task_id}")
    active = record["active_review"]
    if active is not None:
        if not isinstance(active, dict) or set(active) != _ACTIVE_REVIEW_FIELDS:
            raise ReviewLedgerError(f"invalid active review in task {task_id}")
        if active["level"] not in LEVELS:
            raise ReviewLedgerError(f"invalid active review level in task {task_id}")
        normalize_immutable_review_scope(active["scope"])
        _normalize_session(active["reviewer"], "reviewer session")
        _normalize_delegation(active["delegation_id"])
        if active["reviewer"] == record["writer_session"]:
            raise ReviewLedgerError(f"self-review recorded in task {task_id}")
        if active["round"] != record["correction_round"]:
            raise ReviewLedgerError(f"active review round mismatch in task {task_id}")
        if not isinstance(active["invariant"], str):
            raise ReviewLedgerError(f"invalid active invariant in task {task_id}")
        if active["level"] == "INVARIANT":
            if active["invariant"] not in record["invariants"]:
                raise ReviewLedgerError(f"undeclared active invariant in task {task_id}")
        elif active["invariant"]:
            raise ReviewLedgerError(f"unexpected active invariant in task {task_id}")
        if active["level"] in {"INVARIANT", "RELEASE"} and (
            active["scope"] != record["frozen_scope"]
        ):
            raise ReviewLedgerError(f"active candidate scope mismatch in task {task_id}")
        if active["level"] == "DELTA" and record["correction_round"] == 0:
            raise ReviewLedgerError(f"DELTA review without correction in task {task_id}")
    if (record["status"] == "REVIEW_OPEN") != (active is not None):
        raise ReviewLedgerError(f"active review status mismatch in task {task_id}")
    findings = record["last_findings"]
    if findings is not None and (
        not isinstance(findings, dict)
        or set(findings) != {"blocking_high", "blocking_medium", "invalidates_safety"}
    ):
        raise ReviewLedgerError(f"invalid last findings in task {task_id}")
    if findings is not None:
        for key in ("blocking_high", "blocking_medium"):
            if (
                isinstance(findings[key], bool)
                or not isinstance(findings[key], int)
                or findings[key] < 0
            ):
                raise ReviewLedgerError(f"invalid {key} in task {task_id}")
        if not isinstance(findings["invalidates_safety"], bool):
            raise ReviewLedgerError(f"invalid safety flag in task {task_id}")
    if record["status"] in {"CHANGES_REQUIRED", "REVIEW_BUDGET_EXHAUSTED"} and (
        findings is None
    ):
        raise ReviewLedgerError(f"missing blocking findings in task {task_id}")
    events = record["events"]
    if not isinstance(events, list) or len(events) > MAX_EVENTS_PER_TASK:
        raise ReviewLedgerError(f"invalid events in task {task_id}")
    revisions = [_validate_event(event, task_id, ledger_revision) for event in events]
    aliases: set[str] = set()
    for event in events:
        if event["type"] != "alias_registered":
            continue
        data = event["data"]
        if set(data) != {"alias", "task_key", "evidence"}:
            raise ReviewLedgerError("invalid alias event fields")
        alias = _normalize_alias(data["alias"])
        if alias != data["alias"] or alias in aliases or data["task_key"] != record["task_key"]:
            raise ReviewLedgerError("invalid alias identity or duplicate mapping")
        aliases.add(alias)
    if revisions != sorted(revisions) or len(set(revisions)) != len(revisions):
        raise ReviewLedgerError(f"non-monotonic event revisions in task {task_id}")


def _load_ledger(feature: LoadedFeature) -> tuple[dict[str, Any], str | None]:
    path = _ledger_path(feature)
    raw = _read_bytes(path)
    if raw is None:
        return {
            "schema": SCHEMA,
            "feature_key": _feature_key(feature),
            "revision": 0,
            "tasks": {},
        }, None
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewLedgerError(f"invalid review ledger JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != _ROOT_FIELDS:
        raise ReviewLedgerError("invalid review ledger root fields")
    if payload["schema"] != SCHEMA:
        raise ReviewLedgerError(f"unsupported review ledger schema: {payload['schema']!r}")
    if payload["feature_key"] != _feature_key(feature):
        raise ReviewLedgerError("review ledger feature identity mismatch")
    revision = payload["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ReviewLedgerError("invalid review ledger revision")
    tasks = payload["tasks"]
    if not isinstance(tasks, dict) or len(tasks) > 512:
        raise ReviewLedgerError("invalid review ledger tasks")
    for task_id, record in tasks.items():
        if _normalize_task_id(task_id) != task_id:
            raise ReviewLedgerError(f"non-canonical task identity: {task_id}")
        _validate_task_record(task_id, record, payload["feature_key"], revision)
    return payload, digest


def _evidence_record(feature: LoadedFeature, evidence_file: Path) -> dict[str, str]:
    candidate = evidence_file.expanduser()
    if not candidate.is_absolute():
        candidate = feature.feature_dir / candidate
    try:
        lexical_relative = candidate.absolute().relative_to(feature.feature_dir)
    except ValueError as exc:
        raise ReviewLedgerError("review evidence must be inside the feature directory") from exc
    cursor = feature.feature_dir
    for part in lexical_relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ReviewLedgerError("review evidence path must not contain symlinks")
    try:
        path = candidate.resolve(strict=True)
        relative = path.relative_to(feature.feature_dir).as_posix()
    except (OSError, ValueError) as exc:
        raise ReviewLedgerError("review evidence must be inside the feature directory") from exc
    if not path.is_file() or path.name == LEDGER_FILE:
        raise ReviewLedgerError("review evidence must be a regular report file")
    try:
        if path.stat().st_size > MAX_LEDGER_BYTES:
            raise ReviewLedgerError("review evidence exceeds 1 MiB")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ReviewLedgerError(f"cannot read review evidence: {exc}") from exc
    return {"path": relative, "sha256": digest}


def _atomic_write(
    feature: LoadedFeature,
    payload: dict[str, Any],
    expected_digest: str | None,
) -> None:
    path = _ledger_path(feature)
    current = _read_bytes(path)
    current_digest = hashlib.sha256(current).hexdigest() if current is not None else None
    if current_digest != expected_digest:
        raise ReviewLedgerError("review ledger changed during transaction")
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_LEDGER_BYTES:
        raise ReviewLedgerError("review ledger would exceed 1 MiB")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if path.is_symlink():
            raise ReviewLedgerError(f"review ledger must not be a symlink: {path}")
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _require_revision(ledger: dict[str, Any], expected: int) -> None:
    if isinstance(expected, bool) or expected < 0:
        raise ReviewLedgerError("expected revision must be a non-negative integer")
    if ledger["revision"] != expected:
        raise ReviewLedgerError(
            f"review ledger revision mismatch: expected {expected}, "
            f"actual {ledger['revision']}"
        )


def _append_event(
    record: dict[str, Any], revision: int, event_type: str, data: dict[str, Any]
) -> None:
    if len(record["events"]) >= MAX_EVENTS_PER_TASK:
        raise ReviewLedgerError("review event limit reached; close or replan the task")
    record["events"].append(
        {"revision": revision, "at": _now(), "type": event_type, "data": data}
    )


def _transact(
    feature: LoadedFeature,
    expected_revision: int,
    mutate: Callable[[dict[str, Any], int], dict[str, Any]],
) -> dict[str, Any]:
    with _ledger_lock(feature):
        ledger, digest = _load_ledger(feature)
        _require_revision(ledger, expected_revision)
        next_revision = ledger["revision"] + 1
        output = mutate(ledger, next_revision)
        ledger["revision"] = next_revision
        _atomic_write(feature, ledger, digest)
        return {**output, "revision": next_revision, "ledger_file": str(_ledger_path(feature))}


def initialize_task(
    repo: Path,
    feature_dir: Path,
    task_id: str,
    *,
    scope: str,
    writer_session: str,
    invariants: Sequence[str],
    mutation_budget: int,
    expected_revision: int,
    replan_file: Path | None = None,
) -> dict[str, Any]:
    feature, _task, key = _load_bound_task(repo, feature_dir, task_id)
    normalized_task = _normalize_task_id(task_id)
    normalized_scope = normalize_immutable_review_scope(scope)
    writer = _normalize_session(writer_session, "writer session")
    normalized_invariants = _normalize_invariants(invariants)
    if (
        isinstance(mutation_budget, bool)
        or not isinstance(mutation_budget, int)
        or not 0 <= mutation_budget <= MAX_MUTATION_BUDGET
    ):
        raise ReviewLedgerError(
            f"mutation budget must be between 0 and {MAX_MUTATION_BUDGET}"
        )

    def mutate(ledger: dict[str, Any], revision: int) -> dict[str, Any]:
        existing = ledger["tasks"].get(normalized_task)
        if existing is not None and existing["status"] != "REPLAN_REQUIRED":
            raise ReviewLedgerError(
                f"task review cycle already exists with status {existing['status']}"
            )
        replan = None
        if existing is not None:
            if replan_file is None:
                raise ReviewLedgerError(
                    "restarting a cycle requires a replan artifact (--replan-file)"
                )
            evidence = _evidence_record(feature, replan_file)
            raw = (feature.feature_dir / evidence["path"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != evidence["sha256"]:
                raise ReviewLedgerError("replan artifact changed during transaction")
            try:
                plan = json.loads(raw)
            except ValueError as exc:
                raise ReviewLedgerError("replan artifact must be valid JSON") from exc
            fields = {
                "task_key", "previous_cycle", "failure_evidence", "approach_change",
                "acceptance_criteria", "finding_disposition",
            }
            if not isinstance(plan, dict) or set(plan) != fields:
                raise ReviewLedgerError("replan artifact has invalid fields")
            if plan["task_key"] != key:
                raise ReviewLedgerError("replan artifact task key mismatch")
            if type(plan["previous_cycle"]) is not int:
                raise ReviewLedgerError("replan artifact previous cycle must be an integer")
            if plan["previous_cycle"] != existing["cycle"]:
                raise ReviewLedgerError("replan artifact previous cycle mismatch")
            for field in sorted(fields - {"task_key", "previous_cycle"}):
                if not isinstance(plan[field], str):
                    raise ReviewLedgerError(f"replan artifact {field} must be text")
                if len(plan[field]) > 4096:
                    raise ReviewLedgerError(f"replan artifact {field} exceeds 4096 characters")
                if not plan[field].strip():
                    raise ReviewLedgerError(f"replan artifact {field} must not be empty")
            replan = {
                **evidence, "plan": plan, "carried_findings": existing["last_findings"],
            }
        elif replan_file is not None:
            raise ReviewLedgerError("replan artifact requires an existing cycle")
        cycle = 1 if existing is None else existing["cycle"] + 1
        events = [] if existing is None else list(existing["events"])
        record = {
            "task_key": key,
            "cycle": cycle,
            "frozen_scope": normalized_scope,
            "writer_session": writer,
            "invariants": normalized_invariants,
            "mutation_budget": mutation_budget,
            "status": "READY_FOR_REVIEW",
            "correction_round": 0,
            "active_review": None,
            "last_findings": None if existing is None else existing["last_findings"],
            "events": events,
        }
        _append_event(
            record,
            revision,
            "cycle_initialized",
            {
                "cycle": cycle,
                "scope": normalized_scope,
                "writer_session": writer,
                "mutation_budget": mutation_budget,
                "invariants": normalized_invariants,
                **({"replan": replan} if replan is not None else {}),
            },
        )
        ledger["tasks"][normalized_task] = record
        return {"task": normalized_task, "task_key": key, "status": record["status"]}

    return _transact(feature, expected_revision, mutate)


def open_review(
    repo: Path,
    feature_dir: Path,
    task_id: str,
    *,
    level: str,
    scope: str,
    reviewer_session: str,
    delegation_id: str,
    invariant: str,
    expected_revision: int,
) -> dict[str, Any]:
    feature, _task, _key = _load_bound_task(repo, feature_dir, task_id)
    normalized_task = _normalize_task_id(task_id)
    normalized_level = str(level or "").strip().upper()
    if normalized_level not in LEVELS:
        raise ReviewLedgerError("review level must be DELTA, INVARIANT, or RELEASE")
    normalized_scope = normalize_immutable_review_scope(scope)
    reviewer = _normalize_session(reviewer_session, "reviewer session")
    delegation = _normalize_delegation(delegation_id)
    normalized_invariant = ""

    def mutate(ledger: dict[str, Any], revision: int) -> dict[str, Any]:
        nonlocal normalized_invariant
        record = ledger["tasks"].get(normalized_task)
        if record is None:
            raise ReviewLedgerError(f"task review cycle is not initialized: {normalized_task}")
        status = record["status"]
        if reviewer == record["writer_session"]:
            raise ReviewLedgerError("reviewer session must differ from writer session")
        if status == "CORRECTION_OPEN":
            if normalized_level != "DELTA":
                raise ReviewLedgerError("a correction must be reviewed at DELTA level")
        elif status == "READY_FOR_REVIEW":
            if normalized_level == "DELTA":
                raise ReviewLedgerError("DELTA review requires an open correction")
            if normalized_scope != record["frozen_scope"]:
                raise ReviewLedgerError("INVARIANT/RELEASE review must use the frozen candidate scope")
        else:
            raise ReviewLedgerError(f"cannot open review while task status is {status}")
        if normalized_level == "INVARIANT":
            normalized_invariant = _bounded_text(invariant, "invariant", maximum=256)
            if normalized_invariant not in record["invariants"]:
                raise ReviewLedgerError("INVARIANT review must name one declared invariant")
        elif str(invariant or "").strip():
            raise ReviewLedgerError("--invariant is valid only for INVARIANT review")
        duplicate = any(
            event["type"] == "review_recorded"
            and event["data"].get("level") == normalized_level
            and event["data"].get("scope") == normalized_scope
            and event["data"].get("invariant", "") == normalized_invariant
            for event in record["events"]
        )
        if duplicate:
            raise ReviewLedgerError("this review level and immutable scope were already recorded")
        active = {
            "level": normalized_level,
            "scope": normalized_scope,
            "round": record["correction_round"],
            "reviewer": reviewer,
            "delegation_id": delegation,
            "invariant": normalized_invariant,
        }
        prior_timeouts = _matching_review_timeouts(record, active)
        if len(prior_timeouts) > MAX_REVIEWER_FALLBACKS:
            raise ReviewLedgerError("reviewer fallback budget is exhausted")
        if prior_timeouts and prior_timeouts[-1]["data"].get("reviewer") == reviewer:
            raise ReviewLedgerError("reviewer fallback must use a different session")
        record["active_review"] = active
        record["status"] = "REVIEW_OPEN"
        _append_event(record, revision, "review_opened", active)
        opened_at, deadline_at, fallback_attempt = _active_review_timing(record)
        return {
            "task": normalized_task,
            "status": record["status"],
            **active,
            "opened_at": opened_at,
            "deadline_at": deadline_at,
            "fallback_attempt": fallback_attempt,
        }

    return _transact(feature, expected_revision, mutate)


def record_review(
    repo: Path,
    feature_dir: Path,
    task_id: str,
    *,
    verdict: str,
    evidence_file: Path,
    blocking_high: int,
    blocking_medium: int,
    invalidates_safety: bool,
    mutations_run: int,
    expected_revision: int,
) -> dict[str, Any]:
    feature, _task, _key = _load_bound_task(repo, feature_dir, task_id)
    normalized_task = _normalize_task_id(task_id)
    normalized_verdict = str(verdict or "").strip().upper()
    if normalized_verdict not in VERDICTS:
        raise ReviewLedgerError("review verdict must be PASS or CHANGES_REQUIRED")
    for value, label in (
        (blocking_high, "blocking high findings"),
        (blocking_medium, "blocking medium findings"),
        (mutations_run, "mutations run"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ReviewLedgerError(f"{label} must be a non-negative integer")

    def mutate(ledger: dict[str, Any], revision: int) -> dict[str, Any]:
        evidence = _evidence_record(feature, evidence_file)
        record = ledger["tasks"].get(normalized_task)
        if record is None or record["status"] != "REVIEW_OPEN":
            status = "missing" if record is None else record["status"]
            raise ReviewLedgerError(f"cannot record review while task status is {status}")
        active = record["active_review"]
        if active is None:
            raise ReviewLedgerError("review state is missing active review metadata")
        if mutations_run > record["mutation_budget"]:
            raise ReviewLedgerError(
                f"mutations run exceed frozen budget {record['mutation_budget']}"
            )
        blocking = blocking_high > 0 or blocking_medium > 0 or invalidates_safety
        if normalized_verdict == "PASS" and blocking:
            raise ReviewLedgerError("PASS is forbidden with blocking or safety findings")
        findings = {
            "blocking_high": blocking_high,
            "blocking_medium": blocking_medium,
            "invalidates_safety": bool(invalidates_safety),
        }
        event_data = {
            **active,
            "verdict": normalized_verdict,
            "evidence": evidence,
            "findings": findings,
            "mutations_run": mutations_run,
        }
        _append_event(record, revision, "review_recorded", event_data)
        record["active_review"] = None
        record["last_findings"] = findings
        if normalized_verdict == "CHANGES_REQUIRED":
            record["status"] = (
                "REVIEW_BUDGET_EXHAUSTED"
                if record["correction_round"] >= 2
                else "CHANGES_REQUIRED"
            )
        elif active["level"] == "RELEASE":
            record["status"] = "RELEASE_PASSED"
        elif active["level"] == "DELTA":
            record["status"] = "CANDIDATE_UPDATE_REQUIRED"
        else:
            record["status"] = "READY_FOR_REVIEW"
        return {
            "task": normalized_task,
            "status": record["status"],
            "level": active["level"],
            "scope": active["scope"],
            "round": active["round"],
            "verdict": normalized_verdict,
        }

    return _transact(feature, expected_revision, mutate)


def open_correction(
    repo: Path,
    feature_dir: Path,
    task_id: str,
    *,
    delegation_id: str,
    expected_revision: int,
) -> dict[str, Any]:
    feature, _task, _key = _load_bound_task(repo, feature_dir, task_id)
    normalized_task = _normalize_task_id(task_id)
    delegation = _normalize_delegation(delegation_id)

    def mutate(ledger: dict[str, Any], revision: int) -> dict[str, Any]:
        record = ledger["tasks"].get(normalized_task)
        if record is None or record["status"] != "CHANGES_REQUIRED":
            status = "missing" if record is None else record["status"]
            raise ReviewLedgerError(f"cannot open correction while task status is {status}")
        if record["correction_round"] >= 2:
            raise ReviewLedgerError("correction round budget is exhausted")
        record["correction_round"] += 1
        record["status"] = "CORRECTION_OPEN"
        data = {
            "round": record["correction_round"],
            "delegation_id": delegation,
            "writer_session": record["writer_session"],
        }
        _append_event(record, revision, "correction_opened", data)
        return {"task": normalized_task, "status": record["status"], **data}

    return _transact(feature, expected_revision, mutate)


def timeout_review(
    repo: Path,
    feature_dir: Path,
    task_id: str,
    *,
    expected_revision: int,
) -> dict[str, Any]:
    feature, _task, _key = _load_bound_task(repo, feature_dir, task_id)
    normalized_task = _normalize_task_id(task_id)

    def mutate(ledger: dict[str, Any], revision: int) -> dict[str, Any]:
        record = ledger["tasks"].get(normalized_task)
        if record is None or record["status"] != "REVIEW_OPEN":
            status = "missing" if record is None else record["status"]
            raise ReviewLedgerError(f"cannot timeout review while task status is {status}")
        active = record["active_review"]
        if active is None:
            raise ReviewLedgerError("review state is missing active review metadata")
        opened_at, deadline_at, fallback_attempt = _active_review_timing(record)
        if _timestamp(_now(), "current timestamp") < _timestamp(
            deadline_at, "review deadline"
        ):
            raise ReviewLedgerError(f"review deadline has not elapsed: {deadline_at}")
        fallback_allowed = fallback_attempt < MAX_REVIEWER_FALLBACKS
        _append_event(
            record,
            revision,
            "review_timed_out",
            {
                **active,
                "opened_at": opened_at,
                "deadline_at": deadline_at,
                "fallback_attempt": fallback_attempt,
            },
        )
        record["active_review"] = None
        if fallback_allowed:
            record["status"] = (
                "CORRECTION_OPEN" if active["level"] == "DELTA" else "READY_FOR_REVIEW"
            )
        else:
            record["status"] = "ESCALATED"
        return {
            "task": normalized_task,
            "status": record["status"],
            "level": active["level"],
            "scope": active["scope"],
            "round": active["round"],
            "timed_out_reviewer": active["reviewer"],
            "fallback_allowed": fallback_allowed,
            "fallback_attempt": fallback_attempt,
        }

    return _transact(feature, expected_revision, mutate)


def update_candidate(
    repo: Path,
    feature_dir: Path,
    task_id: str,
    *,
    scope: str,
    expected_revision: int,
) -> dict[str, Any]:
    feature, _task, _key = _load_bound_task(repo, feature_dir, task_id)
    normalized_task = _normalize_task_id(task_id)
    normalized_scope = normalize_immutable_review_scope(scope)

    def mutate(ledger: dict[str, Any], revision: int) -> dict[str, Any]:
        record = ledger["tasks"].get(normalized_task)
        if record is None or record["status"] != "CANDIDATE_UPDATE_REQUIRED":
            status = "missing" if record is None else record["status"]
            raise ReviewLedgerError(f"cannot update candidate while task status is {status}")
        if normalized_scope == record["frozen_scope"]:
            raise ReviewLedgerError("corrected candidate scope must differ from prior scope")
        previous = record["frozen_scope"]
        record["frozen_scope"] = normalized_scope
        record["status"] = "READY_FOR_REVIEW"
        _append_event(
            record,
            revision,
            "candidate_updated",
            {"previous_scope": previous, "scope": normalized_scope},
        )
        return {
            "task": normalized_task,
            "status": record["status"],
            "scope": normalized_scope,
            "round": record["correction_round"],
        }

    return _transact(feature, expected_revision, mutate)


def expand_mutation_budget(
    repo: Path,
    feature_dir: Path,
    task_id: str,
    *,
    new_budget: int,
    reason: str,
    expected_revision: int,
) -> dict[str, Any]:
    feature, _task, _key = _load_bound_task(repo, feature_dir, task_id)
    normalized_task = _normalize_task_id(task_id)
    normalized_reason = _bounded_text(reason, "budget expansion reason")
    if isinstance(new_budget, bool) or not isinstance(new_budget, int):
        raise ReviewLedgerError("new mutation budget must be an integer")

    def mutate(ledger: dict[str, Any], revision: int) -> dict[str, Any]:
        record = ledger["tasks"].get(normalized_task)
        if record is None:
            raise ReviewLedgerError(f"task review cycle is not initialized: {normalized_task}")
        if record["status"] in TERMINAL_STATUSES | {
            "REVIEW_OPEN",
            "REPLAN_REQUIRED",
            "REVIEW_BUDGET_EXHAUSTED",
        }:
            raise ReviewLedgerError(
                f"cannot expand mutation budget while task status is {record['status']}"
            )
        old_budget = record["mutation_budget"]
        if not old_budget < new_budget <= MAX_MUTATION_BUDGET:
            raise ReviewLedgerError(
                f"new mutation budget must be {old_budget + 1}-{MAX_MUTATION_BUDGET}"
            )
        record["mutation_budget"] = new_budget
        _append_event(
            record,
            revision,
            "mutation_budget_expanded",
            {"previous": old_budget, "current": new_budget, "reason": normalized_reason},
        )
        return {
            "task": normalized_task,
            "status": record["status"],
            "mutation_budget": new_budget,
        }

    return _transact(feature, expected_revision, mutate)


def decide_exhausted(
    repo: Path,
    feature_dir: Path,
    task_id: str,
    *,
    decision: str,
    reason: str,
    expected_revision: int,
) -> dict[str, Any]:
    feature, _task, _key = _load_bound_task(repo, feature_dir, task_id)
    normalized_task = _normalize_task_id(task_id)
    normalized_decision = str(decision or "").strip().upper()
    if normalized_decision not in DECISIONS:
        raise ReviewLedgerError("decision must be REPLAN, ESCALATE, or BACKLOG")
    normalized_reason = _bounded_text(reason, "loop decision reason")

    def mutate(ledger: dict[str, Any], revision: int) -> dict[str, Any]:
        record = ledger["tasks"].get(normalized_task)
        if record is None or record["status"] not in {
            "CHANGES_REQUIRED",
            "REVIEW_BUDGET_EXHAUSTED",
        }:
            status = "missing" if record is None else record["status"]
            raise ReviewLedgerError(f"cannot decide loop while task status is {status}")
        findings = record["last_findings"] or {}
        blocking = (
            findings.get("blocking_high", 0) > 0
            or findings.get("blocking_medium", 0) > 0
            or findings.get("invalidates_safety") is True
        )
        if normalized_decision == "BACKLOG" and blocking:
            raise ReviewLedgerError("BACKLOG is forbidden for blocking or safety findings")
        status_by_decision = {
            "REPLAN": "REPLAN_REQUIRED",
            "ESCALATE": "ESCALATED",
            "BACKLOG": "BACKLOGGED",
        }
        record["status"] = status_by_decision[normalized_decision]
        _append_event(
            record,
            revision,
            "loop_decided",
            {"decision": normalized_decision, "reason": normalized_reason},
        )
        return {
            "task": normalized_task,
            "status": record["status"],
            "decision": normalized_decision,
        }

    return _transact(feature, expected_revision, mutate)


def review_status(
    repo: Path, feature_dir: Path, task_id: str | None = None
) -> dict[str, Any]:
    feature = load_feature(repo, feature_dir)
    with _ledger_lock(feature):
        ledger, _digest = _load_ledger(feature)
    if task_id is None:
        return {
            "schema": SCHEMA,
            "feature_key": ledger["feature_key"],
            "revision": ledger["revision"],
            "ledger_file": str(_ledger_path(feature)),
            "tasks": ledger["tasks"],
        }
    normalized_task = _normalize_task_id(task_id)
    record = ledger["tasks"].get(normalized_task)
    if record is None:
        raise ReviewLedgerError(f"task review cycle is not initialized: {normalized_task}")
    output = {
        "schema": SCHEMA,
        "feature_key": ledger["feature_key"],
        "revision": ledger["revision"],
        "ledger_file": str(_ledger_path(feature)),
        "task": normalized_task,
        **record,
        "completed": next((task.completed for task in feature.tasks
                           if task.task_id == normalized_task), None),
        "aliases": sorted(
            event["data"]["alias"] for event in record["events"]
            if event["type"] == "alias_registered"
        ),
        "total_correction_rounds": sum(
            event["type"] == "correction_opened" for event in record["events"]
        ),
    }
    attempted = next((event for event in reversed(record["events"])
                      if event["type"] == "correction_dispatch_attempted"), None)
    if attempted is not None:
        result = next((event for event in reversed(record["events"])
                       if event["type"] == "correction_dispatch_result"
                       and event["revision"] > attempted["revision"]
                       and event["data"].get("delegation_id") == attempted["data"]["delegation_id"]), None)
        output["last_dispatch"] = {
            **attempted["data"], "attempted_at": attempted["at"],
            "submission": "unknown", "receipt_recorded": result is not None,
            **(result["data"] if result else {}),
        }
    if record["status"] == "REVIEW_OPEN":
        opened_at, deadline_at, fallback_attempt = _active_review_timing(record)
        output.update(
            {
                "review_opened_at": opened_at,
                "review_deadline_at": deadline_at,
                "review_fallback_attempt": fallback_attempt,
            }
        )
    return output


def _normalize_alias(value: str) -> str:
    alias = str(value or "").strip().upper()
    if len(alias) > 128:
        raise ReviewLedgerError("legacy alias exceeds 128 characters")
    if _SAFE_TASK.fullmatch(alias):
        raise ReviewLedgerError("a canonical task ID cannot be used as an alias")
    if not re.fullmatch(r"T[0-9]{3,}[A-Z0-9_.:-]{1,96}", alias):
        raise ReviewLedgerError("legacy alias must start with Tnnn and have a bounded suffix")
    return alias


def _alias_owner(ledger: dict[str, Any], alias: str) -> str | None:
    owners = {
        task for task, record in ledger["tasks"].items()
        for event in record["events"]
        if event["type"] == "alias_registered" and event["data"].get("alias") == alias
    }
    if len(owners) > 1:
        raise ReviewLedgerError("ambiguous legacy alias in ledger")
    return next(iter(owners), None)


def register_alias(
    repo: Path, feature_dir: Path, task_id: str, *, alias: str,
    evidence_file: Path, expected_revision: int,
) -> dict[str, Any]:
    feature, _task, key = _load_bound_task(repo, feature_dir, task_id)
    task = _normalize_task_id(task_id)
    normalized_alias = _normalize_alias(alias)

    def mutate(ledger: dict[str, Any], revision: int) -> dict[str, Any]:
        record = ledger["tasks"].get(task)
        if record is None:
            raise ReviewLedgerError("initialize the canonical review task before mapping aliases")
        if _alias_owner(ledger, normalized_alias) is not None:
            raise ReviewLedgerError("legacy alias is already mapped; mappings cannot be reassigned")
        evidence = _evidence_record(feature, evidence_file)
        _append_event(record, revision, "alias_registered", {
            "alias": normalized_alias, "task_key": key, "evidence": evidence,
        })
        return {"task": task, "task_key": key, "alias": normalized_alias,
                "status": record["status"]}

    return _transact(feature, expected_revision, mutate)


def resolve_task(repo: Path, feature_dir: Path, task_id: str) -> dict[str, Any]:
    feature = load_feature(repo, feature_dir)
    normalized = str(task_id or "").strip().upper()
    with _ledger_lock(feature):
        ledger, _digest = _load_ledger(feature)
        if _SAFE_TASK.fullmatch(normalized):
            canonical = normalized
        else:
            canonical = _alias_owner(ledger, _normalize_alias(normalized))
            if canonical is None:
                raise ReviewLedgerError("legacy task is not mapped to a canonical review task")
        if not any(task.task_id == canonical for task in feature.tasks):
            raise ReviewLedgerError("canonical task is absent from tasks.md")
        return {"task": canonical, "task_key": f"{_feature_key(feature)}:{canonical}",
                "revision": ledger["revision"]}


def review_check(
    repo: Path, feature_dir: Path, task_id: str, *, scope: str
) -> dict[str, Any]:
    _feature, _task, current_task_key = _load_bound_task(repo, feature_dir, task_id)
    expected_scope = normalize_immutable_review_scope(scope)
    status = review_status(repo, feature_dir, task_id)
    if status["task_key"] != current_task_key:
        raise ReviewLedgerError("review ledger task identity no longer matches tasks.md")
    if status["frozen_scope"] != expected_scope:
        raise ReviewLedgerError(
            "review gate scope mismatch: supplied candidate is not the frozen candidate"
        )
    return {
        "schema": SCHEMA,
        "feature_key": status["feature_key"],
        "revision": status["revision"],
        "ledger_file": status["ledger_file"],
        "task": status["task"],
        "task_key": status["task_key"],
        "scope": expected_scope,
        "status": status["status"],
        "release_passed": status["status"] == "RELEASE_PASSED",
    }


def _prepare_local_delivery(writer: str, worker_repo: Path) -> tuple[dict[str, Any], Callable]:
    from scripts.mesh_live_cli import LiveClient, LiveEndpoint, LiveReadError

    root = worker_repo.expanduser().resolve(strict=True)
    if not (root / ".git").exists():
        raise ReviewLedgerError("worker repo must be an exact Git checkout")
    client = LiveClient(LiveEndpoint(host="", local=True, users=(getpass.getuser(),)))
    try:
        sessions, _warnings = client.discover()
    except LiveReadError as exc:
        raise ReviewLedgerError("cannot discover the local writer; inspect mesh live board") from exc
    matches = [session for session in sessions if session.name == writer]
    if len(matches) != 1:
        raise ReviewLedgerError("canonical writer session is missing or ambiguous on this host")
    session = matches[0]
    if Path(session.pane_path).resolve() != root or session.pane_pid <= 0:
        raise ReviewLedgerError("writer pane repository or process identity does not match")
    target = {"owner": session.owner, "name": session.name,
              "pane_id": session.pane_id, "pane_pid": session.pane_pid}
    return target, lambda message, delegation: client.send(
        session, message, enter=True, delegation_id=delegation,
    )


def dispatch_correction(
    repo: Path, feature_dir: Path, task_id: str, *, delegation_id: str,
    message: str, worker_repo: Path, expected_revision: int,
) -> dict[str, Any]:
    from scripts.mesh_live_cli import validate_send_text

    feature, _task, key = _load_bound_task(repo, feature_dir, task_id)
    task = _normalize_task_id(task_id)
    delegation = _normalize_delegation(delegation_id)
    text = validate_send_text(message, enter=True)
    for value, label in ((delegation, "delegation"), (key, "canonical task key")):
        if not re.search(r"(?<![A-Za-z0-9_.:-])" + re.escape(value) + r"(?![A-Za-z0-9_.:-])", text):
            raise ReviewLedgerError(f"dispatch text must contain the exact {label}")
    with _ledger_lock(feature):
        ledger, digest = _load_ledger(feature)
        _require_revision(ledger, expected_revision)
        record = ledger["tasks"].get(task)
        if record is None or record["status"] != "CORRECTION_OPEN":
            raise ReviewLedgerError("managed correction dispatch requires CORRECTION_OPEN")
        opened = next((event for event in reversed(record["events"])
                       if event["type"] == "correction_opened"), None)
        if opened is None or opened["data"].get("delegation_id") != delegation:
            raise ReviewLedgerError("dispatch delegation does not match the open correction")
        if any(event["type"] == "correction_dispatch_attempted"
               and event["data"].get("delegation_id") == delegation
               for entry in ledger["tasks"].values() for event in entry["events"]):
            raise ReviewLedgerError("dispatch was already attempted; inspect receipt/pane, never resend")
        if len(record["events"]) > MAX_EVENTS_PER_TASK - 2:
            raise ReviewLedgerError("insufficient event capacity for dispatch and receipt")
        target, deliver = _prepare_local_delivery(record["writer_session"], worker_repo)
        revision = ledger["revision"] + 1
        data = {"delegation_id": delegation, "target": target,
                "worker_repo": str(worker_repo.expanduser().resolve()),
                "message_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "cycle": record["cycle"], "round": record["correction_round"]}
        _append_event(record, revision, "correction_dispatch_attempted", data)
        ledger["revision"] = revision
        # Commit intent before input. A crash or lost response must never authorize replay.
        _atomic_write(feature, ledger, digest)
        persisted = _read_bytes(_ledger_path(feature))
        digest = hashlib.sha256(persisted).hexdigest()
        try:
            sent = deliver(text, delegation)
            receipt = {
                "submission": "verified" if sent.get("submission") == "verified" else "unknown",
                "text_sent": sent.get("text_sent") is True,
                "enter_sent": sent.get("enter_sent") is True,
            }
        except Exception:
            # Input may already have happened, including when transport code itself fails.
            # Signals/process death still leave the durable attempt visible as unknown.
            receipt = {"submission": "unknown", "text_sent": None, "enter_sent": None}
        revision += 1
        _append_event(record, revision, "correction_dispatch_result",
                      {"delegation_id": delegation, **receipt})
        ledger["revision"] = revision
        _atomic_write(feature, ledger, digest)
        return {"task": task, "task_key": key, "revision": revision,
                "status": record["status"], "delegation_id": delegation, **receipt}


def complete_task(
    repo: Path, feature_dir: Path, task_id: str, *, scope: str, expected_revision: int,
) -> dict[str, Any]:
    feature = load_feature(repo, feature_dir)
    task = _normalize_task_id(task_id)
    expected_scope = normalize_immutable_review_scope(scope)
    with _ledger_lock(feature):
        ledger, _digest = _load_ledger(feature)
        _require_revision(ledger, expected_revision)
        record = ledger["tasks"].get(task)
        if record is None or record["status"] != "RELEASE_PASSED":
            raise ReviewLedgerError("task completion requires RELEASE_PASSED")
        if record["frozen_scope"] != expected_scope:
            raise ReviewLedgerError("completion scope differs from the reviewed candidate")
        passed = next((event for event in reversed(record["events"])
                       if event["type"] == "review_recorded"
                       and event["data"].get("level") == "RELEASE"
                       and event["data"].get("verdict") == "PASS"
                       and event["data"].get("scope") == expected_scope), None)
        if passed is None:
            raise ReviewLedgerError("release evidence is missing")
        evidence = passed["data"]["evidence"]
        if _evidence_record(feature, Path(evidence["path"])) != evidence:
            raise ReviewLedgerError("release evidence changed since review")
        current = load_feature(repo, feature_dir)
        if current.binding != feature.binding:
            raise ReviewLedgerError("feature binding changed during completion")
        selected = next((item for item in current.tasks if item.task_id == task), None)
        if selected is None:
            raise ReviewLedgerError("canonical task is absent from tasks.md")
        if not selected.completed:
            path = current.tasks_file
            original = path.read_bytes()
            lines = original.decode("utf-8").splitlines(keepends=True)
            index = selected.line_number - 1
            pattern = r"^(\s*-\s*\[) (\]\s*" + re.escape(task) + r"(?=\s|$))"
            replacement, count = re.subn(pattern, r"\g<1>x\g<2>", lines[index], count=1)
            if count != 1:
                raise ReviewLedgerError("task line changed during completion")
            lines[index] = replacement
            descriptor, temporary_name = tempfile.mkstemp(prefix=".mesh-tasks-", dir=path.parent)
            temporary = Path(temporary_name)
            try:
                os.fchmod(descriptor, path.stat().st_mode & 0o777)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write("".join(lines).encode("utf-8"))
                    stream.flush()
                    os.fsync(stream.fileno())
                if path.is_symlink() or path.read_bytes() != original:
                    raise ReviewLedgerError("tasks.md changed during completion")
                os.replace(temporary, path)
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                temporary.unlink(missing_ok=True)
        return {"task": task, "status": record["status"], "revision": ledger["revision"],
                "completed": True, "task_key": record["task_key"]}


def _common(command: argparse.ArgumentParser, *, revision: bool = True) -> None:
    command.add_argument("repo", type=Path)
    command.add_argument("feature_dir", type=Path)
    command.add_argument("task")
    if revision:
        command.add_argument("--expect-revision", type=int, required=True)
    command.add_argument("--json", action="store_true")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    dispatch = sub.add_parser("dispatch")
    _common(dispatch)
    dispatch.add_argument("--delegation-id", required=True)
    dispatch.add_argument("--message", required=True)
    dispatch.add_argument("--worker-repo", type=Path, required=True)
    complete = sub.add_parser("complete")
    _common(complete)
    complete.add_argument("--scope", required=True)
    mapped = sub.add_parser("map")
    _common(mapped)
    mapped.add_argument("--alias", required=True)
    mapped.add_argument("--evidence-file", type=Path, required=True)
    resolved = sub.add_parser("resolve")
    _common(resolved, revision=False)
    status = sub.add_parser("status")
    status.add_argument("repo", type=Path)
    status.add_argument("feature_dir", type=Path)
    status.add_argument("task", nargs="?")
    status.add_argument("--json", action="store_true")
    check = sub.add_parser("check")
    check.add_argument("repo", type=Path)
    check.add_argument("feature_dir", type=Path)
    check.add_argument("task")
    check.add_argument("--scope", required=True)
    check.add_argument("--json", action="store_true")
    init = sub.add_parser("init")
    _common(init)
    init.add_argument("--scope", required=True)
    init.add_argument("--writer-session", required=True)
    init.add_argument("--invariant", action="append", default=[])
    init.add_argument("--mutation-budget", type=int, default=1)
    init.add_argument("--replan-file", type=Path)
    opened = sub.add_parser("open")
    _common(opened)
    opened.add_argument("--level", required=True)
    opened.add_argument("--scope", required=True)
    opened.add_argument("--reviewer-session", required=True)
    opened.add_argument("--delegation-id", required=True)
    opened.add_argument("--invariant", default="")
    record = sub.add_parser("record")
    _common(record)
    record.add_argument("--verdict", required=True)
    record.add_argument("--evidence-file", type=Path, required=True)
    record.add_argument("--blocking-high", type=int, default=0)
    record.add_argument("--blocking-medium", type=int, default=0)
    record.add_argument("--invalidates-safety", action="store_true")
    record.add_argument("--mutations-run", type=int, default=0)
    correction = sub.add_parser("correction")
    _common(correction)
    correction.add_argument("--delegation-id", required=True)
    timeout = sub.add_parser("timeout")
    _common(timeout)
    candidate = sub.add_parser("candidate")
    _common(candidate)
    candidate.add_argument("--scope", required=True)
    budget = sub.add_parser("budget")
    _common(budget)
    budget.add_argument("--new-budget", type=int, required=True)
    budget.add_argument("--reason", required=True)
    decide = sub.add_parser("decide")
    _common(decide)
    decide.add_argument("--decision", required=True)
    decide.add_argument("--reason", required=True)
    return parser.parse_args(argv)


def _render(output: dict[str, Any]) -> str:
    fields = [
        f"revision={output.get('revision', '-')}",
        f"task={output.get('task', '-')}",
        f"status={output.get('status', '-')}",
    ]
    for key in ("level", "round", "verdict", "decision", "scope", "completed", "submission"):
        if key in output:
            fields.append(f"{key}={output[key]}")
    return "Review ledger " + " ".join(fields)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "dispatch":
            output = dispatch_correction(
                args.repo, args.feature_dir, args.task, delegation_id=args.delegation_id,
                message=args.message, worker_repo=args.worker_repo,
                expected_revision=args.expect_revision,
            )
        elif args.command == "complete":
            output = complete_task(args.repo, args.feature_dir, args.task,
                                   scope=args.scope, expected_revision=args.expect_revision)
        elif args.command == "map":
            output = register_alias(
                args.repo, args.feature_dir, args.task, alias=args.alias,
                evidence_file=args.evidence_file, expected_revision=args.expect_revision,
            )
        elif args.command == "resolve":
            output = resolve_task(args.repo, args.feature_dir, args.task)
        elif args.command == "status":
            output = review_status(args.repo, args.feature_dir, args.task)
        elif args.command == "check":
            output = review_check(
                args.repo, args.feature_dir, args.task, scope=args.scope
            )
        elif args.command == "init":
            output = initialize_task(
                args.repo,
                args.feature_dir,
                args.task,
                scope=args.scope,
                writer_session=args.writer_session,
                invariants=args.invariant,
                mutation_budget=args.mutation_budget,
                expected_revision=args.expect_revision,
                replan_file=args.replan_file,
            )
        elif args.command == "open":
            output = open_review(
                args.repo,
                args.feature_dir,
                args.task,
                level=args.level,
                scope=args.scope,
                reviewer_session=args.reviewer_session,
                delegation_id=args.delegation_id,
                invariant=args.invariant,
                expected_revision=args.expect_revision,
            )
        elif args.command == "record":
            output = record_review(
                args.repo,
                args.feature_dir,
                args.task,
                verdict=args.verdict,
                evidence_file=args.evidence_file,
                blocking_high=args.blocking_high,
                blocking_medium=args.blocking_medium,
                invalidates_safety=args.invalidates_safety,
                mutations_run=args.mutations_run,
                expected_revision=args.expect_revision,
            )
        elif args.command == "correction":
            output = open_correction(
                args.repo,
                args.feature_dir,
                args.task,
                delegation_id=args.delegation_id,
                expected_revision=args.expect_revision,
            )
        elif args.command == "timeout":
            output = timeout_review(
                args.repo,
                args.feature_dir,
                args.task,
                expected_revision=args.expect_revision,
            )
        elif args.command == "candidate":
            output = update_candidate(
                args.repo,
                args.feature_dir,
                args.task,
                scope=args.scope,
                expected_revision=args.expect_revision,
            )
        elif args.command == "budget":
            output = expand_mutation_budget(
                args.repo,
                args.feature_dir,
                args.task,
                new_budget=args.new_budget,
                reason=args.reason,
                expected_revision=args.expect_revision,
            )
        else:
            output = decide_exhausted(
                args.repo,
                args.feature_dir,
                args.task,
                decision=args.decision,
                reason=args.reason,
                expected_revision=args.expect_revision,
            )
    except (ReviewLedgerError, LedgerError, SpeckitRuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Error: operating system failure: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(output, indent=2, sort_keys=True) if args.json else _render(output))
    if args.command == "check" and not output["release_passed"]:
        return 1
    if args.command == "dispatch" and output["submission"] != "verified":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
