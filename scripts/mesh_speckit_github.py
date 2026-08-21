#!/usr/bin/env python3
"""Deterministic Spec Kit task ledger primitives.

The module keeps Git and ``tasks.md`` authoritative. GitHub transport and
mutation are added as a separate layer so parsing and planning stay offline and
fully testable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


BINDING_SCHEMA = "mesh.speckit.github-ledger.v1"
ISSUE_MARKER_VERSION = "v1"
BINDING_FILE = "github-ledger.json"
TASKS_FILE = "tasks.md"
MAX_BINDING_BYTES = 16 * 1024
MAX_TASKS_BYTES = 1024 * 1024
MAX_DESCRIPTION_CHARS = 4_000
MAX_ISSUE_TITLE_CHARS = 256

_BINDING_FIELDS = {"schema", "feature_id", "repository", "enabled"}
_FEATURE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{6,38}[a-z0-9]$")
_REPOSITORY_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?/[a-z0-9][a-z0-9._-]{0,99}$"
)
_CHECKBOX_RE = re.compile(r"^\s*-\s*\[([^\]])\]\s*(.*)$")
_TASK_RE = re.compile(r"^(T\d{3,})(?:\s+(.+))?$")
_TASK_LIKE_RE = re.compile(r"^T\d+\b")
_MARKER_RE = re.compile(r"^\[([^\]]+)\](?:\s+|$)")
_STORY_RE = re.compile(r"^US\d+$")
_MARKER_PREFIX = "<!-- mesh-speckit-task:"
_MANAGED_MARKER_RE = re.compile(
    r"<!--\s*mesh-speckit-task:(?P<version>[^\s]+)\s+"
    r"repo=(?P<repository>[^\s]+)\s+"
    r"feature=(?P<feature>[^\s]+)\s+"
    r"task=(?P<task>T\d{3,})\s*-->"
)
_LEGACY_TITLE_RE = re.compile(r"^(?:\[[^\]]+\]\s+)?(?P<task>T\d{3,})\s*:")


class LedgerError(ValueError):
    """Raised when local ledger input is unsafe or ambiguous."""


@dataclass(frozen=True)
class FeatureBinding:
    schema: str
    feature_id: str
    repository: str
    enabled: bool


@dataclass(frozen=True)
class SpecTask:
    task_id: str
    description: str
    completed: bool
    parallel: bool
    story: str | None
    line_number: int


@dataclass(frozen=True)
class LoadedFeature:
    repo_root: Path
    feature_dir: Path
    tasks_file: Path
    source_path: str
    display_name: str
    binding: FeatureBinding
    tasks: tuple[SpecTask, ...]


@dataclass(frozen=True)
class RenderedIssue:
    title: str
    body: str
    labels: tuple[str, ...]
    desired_state: str


@dataclass(frozen=True)
class RemoteIssue:
    number: int
    title: str
    body: str
    state: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class BlockingDrift:
    code: str
    message: str
    issue_number: int | None = None
    task_id: str | None = None


@dataclass(frozen=True)
class ReconcileAction:
    task_id: str
    operation: str
    issue_number: int | None
    title: str
    body: str
    add_labels: tuple[str, ...]
    state: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ReconciliationPlan:
    feature: LoadedFeature
    aligned: bool
    blocking: tuple[BlockingDrift, ...]
    actions: tuple[ReconcileAction, ...]


def _read_bounded(path: Path, limit: int, label: str) -> str:
    if path.is_symlink():
        raise LedgerError(f"{label} must not be a symlink: {path}")
    if not path.is_file():
        raise LedgerError(f"{label} not found: {path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise LedgerError(f"cannot inspect {label}: {path}: {exc}") from exc
    if size > limit:
        raise LedgerError(f"{label} exceeds {limit} bytes: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LedgerError(f"cannot read {label}: {path}: {exc}") from exc


def _resolve_feature_dir(repo_root: Path, feature_dir: Path) -> tuple[Path, Path]:
    try:
        root = repo_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise LedgerError(f"repository root does not exist: {repo_root}") from exc
    if not (root / ".git").exists():
        raise LedgerError(f"repository root is not an exact Git checkout: {root}")

    candidate = feature_dir.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise LedgerError(
            f"feature directory must be inside the exact repository root: {candidate}"
        ) from exc
    if not resolved.is_dir():
        raise LedgerError(f"feature directory not found: {resolved}")
    return root, resolved


def load_binding(path: Path) -> FeatureBinding:
    raw = _read_bounded(path, MAX_BINDING_BYTES, "ledger binding")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"invalid ledger binding JSON: {path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise LedgerError("ledger binding must be a JSON object")
    unknown = sorted(set(payload) - _BINDING_FIELDS)
    missing = sorted(_BINDING_FIELDS - set(payload))
    if unknown:
        raise LedgerError(f"unknown binding fields: {', '.join(unknown)}")
    if missing:
        raise LedgerError(f"missing binding fields: {', '.join(missing)}")

    schema = payload.get("schema")
    if schema != BINDING_SCHEMA:
        raise LedgerError(f"unsupported ledger schema: {schema!r}")
    feature_id = payload.get("feature_id")
    if not isinstance(feature_id, str) or not _FEATURE_ID_RE.fullmatch(feature_id):
        raise LedgerError("invalid feature_id; use 8-40 lowercase letters, digits, or hyphens")
    repository = payload.get("repository")
    if not isinstance(repository, str) or not _REPOSITORY_RE.fullmatch(repository):
        if isinstance(repository, str) and repository.lower() != repository:
            raise LedgerError("repository must use canonical lowercase owner/repo")
        raise LedgerError("invalid repository; expected canonical lowercase owner/repo")
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise LedgerError("binding enabled must be a boolean")
    if not enabled:
        raise LedgerError("feature ledger is not enabled")
    return FeatureBinding(
        schema=schema,
        feature_id=feature_id,
        repository=repository,
        enabled=enabled,
    )


def _normalize_description(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_tasks(text: str) -> tuple[SpecTask, ...]:
    tasks: list[SpecTask] = []
    seen: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), 1):
        checkbox = _CHECKBOX_RE.match(line)
        if not checkbox:
            continue
        state, remainder = checkbox.groups()
        if not _TASK_LIKE_RE.match(remainder):
            continue
        if state not in {" ", "x", "X"}:
            raise LedgerError(f"malformed task line {line_number}: {line.strip()}")
        match = _TASK_RE.fullmatch(remainder)
        if not match or not match.group(2):
            raise LedgerError(f"malformed task line {line_number}: {line.strip()}")
        task_id, tail = match.groups()

        parallel = False
        story: str | None = None
        while tail.startswith("["):
            marker_match = _MARKER_RE.match(tail)
            if not marker_match:
                break
            marker = marker_match.group(1)
            if marker == "P" and not parallel:
                parallel = True
            elif _STORY_RE.fullmatch(marker) and story is None:
                story = marker
            else:
                raise LedgerError(
                    f"unsupported task marker [{marker}] on line {line_number}"
                )
            tail = tail[marker_match.end() :]

        description = _normalize_description(tail)
        if not description:
            raise LedgerError(f"malformed task line {line_number}: missing description")
        if len(description) > MAX_DESCRIPTION_CHARS:
            raise LedgerError(
                f"task {task_id} description exceeds {MAX_DESCRIPTION_CHARS} characters"
            )
        if task_id in seen:
            raise LedgerError(f"duplicate task ID {task_id}")
        seen.add(task_id)
        tasks.append(
            SpecTask(
                task_id=task_id,
                description=description,
                completed=state in {"x", "X"},
                parallel=parallel,
                story=story,
                line_number=line_number,
            )
        )
    if not tasks:
        raise LedgerError("tasks.md contains no Spec Kit tasks")
    return tuple(tasks)


def load_feature(repo_root: Path, feature_dir: Path) -> LoadedFeature:
    root, feature = _resolve_feature_dir(repo_root, feature_dir)
    binding = load_binding(feature / BINDING_FILE)
    tasks_path = feature / TASKS_FILE
    task_text = _read_bounded(tasks_path, MAX_TASKS_BYTES, "tasks file")
    try:
        source_path = tasks_path.resolve(strict=True).relative_to(root).as_posix()
    except (OSError, ValueError) as exc:
        raise LedgerError("tasks file must remain inside the exact repository root") from exc
    return LoadedFeature(
        repo_root=root,
        feature_dir=feature,
        tasks_file=tasks_path,
        source_path=source_path,
        display_name=feature.name,
        binding=binding,
        tasks=parse_tasks(task_text),
    )


def task_key(binding: FeatureBinding, task: SpecTask) -> str:
    return f"{binding.repository}:{binding.feature_id}:{task.task_id}"


def issue_marker(binding: FeatureBinding, task: SpecTask) -> str:
    return (
        f"<!-- mesh-speckit-task:{ISSUE_MARKER_VERSION} "
        f"repo={binding.repository} feature={binding.feature_id} task={task.task_id} -->"
    )


def _bounded_title(prefix: str, description: str) -> str:
    title = f"{prefix}{description}"
    if len(title) <= MAX_ISSUE_TITLE_CHARS:
        return title
    available = MAX_ISSUE_TITLE_CHARS - len(prefix) - 3
    return f"{prefix}{description[:available].rstrip()}..."


def render_issue(feature: LoadedFeature, task: SpecTask) -> RenderedIssue:
    prefix = f"[{feature.display_name}] {task.task_id}: "
    title = _bounded_title(prefix, task.description)
    key = task_key(feature.binding, task)
    metadata = [
        issue_marker(feature.binding, task),
        "",
        f"# Spec Kit Task {task.task_id}",
        "",
        f"- **Task key**: `{key}`",
        f"- **Feature**: `{feature.display_name}`",
        f"- **Source**: `{feature.source_path}`",
        f"- **State authority**: `{feature.source_path}`",
    ]
    if task.story:
        metadata.append(f"- **Story**: `{task.story}`")
    metadata.append(f"- **Parallelizable**: `{'yes' if task.parallel else 'no'}`")
    metadata.extend(
        [
            "",
            "## Task",
            "",
            task.description,
            "",
            "---",
            "Managed one-way from Spec Kit. Discuss work in comments; do not edit this body.",
            "",
        ]
    )
    return RenderedIssue(
        title=title,
        body="\n".join(metadata),
        labels=(
            "speckit-task",
            f"speckit:{feature.binding.feature_id}",
        ),
        desired_state="closed" if task.completed else "open",
    )


def _normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_body(value: str) -> str:
    normalized = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def _label_set(labels: tuple[str, ...]) -> set[str]:
    return {str(label).strip().casefold() for label in labels if str(label).strip()}


def _task_sort_key(task_id: str) -> int:
    return int(task_id[1:])


def _inspect_remote_issues(
    feature: LoadedFeature,
    remote_issues: list[RemoteIssue] | tuple[RemoteIssue, ...],
) -> tuple[dict[str, list[RemoteIssue]], list[BlockingDrift]]:
    managed: dict[str, list[RemoteIssue]] = {}
    blocking: list[BlockingDrift] = []
    local_ids = {task.task_id for task in feature.tasks}
    feature_label = f"speckit:{feature.binding.feature_id}".casefold()

    for issue in sorted(remote_issues, key=lambda item: item.number):
        body = _normalize_body(issue.body)
        labels = _label_set(issue.labels)
        markers = list(_MANAGED_MARKER_RE.finditer(body))
        prefix_count = body.count(_MARKER_PREFIX)

        if len(markers) > 1:
            current = any(
                marker.group("feature") == feature.binding.feature_id
                for marker in markers
            )
            if current or feature_label in labels:
                blocking.append(
                    BlockingDrift(
                        "multiple_markers",
                        f"issue #{issue.number} contains multiple managed task markers",
                        issue.number,
                    )
                )
            continue

        if len(markers) == 1:
            marker = markers[0]
            marker_feature = marker.group("feature")
            if marker_feature != feature.binding.feature_id:
                if feature_label in labels:
                    blocking.append(
                        BlockingDrift(
                            "feature_label_mismatch",
                            f"issue #{issue.number} carries the current feature label but a different marker",
                            issue.number,
                        )
                    )
                continue
            task_id = marker.group("task")
            if marker.group("version") != ISSUE_MARKER_VERSION:
                blocking.append(
                    BlockingDrift(
                        "unsupported_marker",
                        f"issue #{issue.number} uses unsupported managed marker version",
                        issue.number,
                        task_id,
                    )
                )
                continue
            if marker.group("repository") != feature.binding.repository:
                blocking.append(
                    BlockingDrift(
                        "repository_mismatch",
                        f"issue #{issue.number} marker repository does not match {feature.binding.repository}",
                        issue.number,
                        task_id,
                    )
                )
                continue
            if task_id not in local_ids:
                blocking.append(
                    BlockingDrift(
                        "orphan_task",
                        f"issue #{issue.number} references removed task {task_id}; restore {task_id} in tasks.md as completed or cancelled",
                        issue.number,
                        task_id,
                    )
                )
                continue
            managed.setdefault(task_id, []).append(issue)
            continue

        legacy_match = _LEGACY_TITLE_RE.match(_normalize_title(issue.title))
        legacy_task = legacy_match.group("task") if legacy_match else None
        if prefix_count or feature_label in labels:
            blocking.append(
                BlockingDrift(
                    "malformed_marker",
                    f"issue #{issue.number} is feature-associated but lacks one valid managed marker",
                    issue.number,
                    legacy_task,
                )
            )
        elif legacy_task in local_ids:
            blocking.append(
                BlockingDrift(
                    "legacy_task_issue",
                    f"issue #{issue.number} looks like legacy output for {legacy_task}; migrate it explicitly before synchronization",
                    issue.number,
                    legacy_task,
                )
            )

    for task_id, issues in sorted(managed.items(), key=lambda item: _task_sort_key(item[0])):
        if len(issues) > 1:
            numbers = ", ".join(f"#{item.number}" for item in issues)
            blocking.append(
                BlockingDrift(
                    "duplicate_task_key",
                    f"task {task_id} has duplicate managed issues: {numbers}",
                    issues[0].number,
                    task_id,
                )
            )
    blocking.sort(
        key=lambda item: (
            item.issue_number if item.issue_number is not None else -1,
            item.code,
            item.task_id or "",
        )
    )
    return managed, blocking


def build_plan(
    feature: LoadedFeature,
    remote_issues: list[RemoteIssue] | tuple[RemoteIssue, ...],
) -> ReconciliationPlan:
    managed, blocking = _inspect_remote_issues(feature, remote_issues)
    actions: list[ReconcileAction] = []

    for task in sorted(feature.tasks, key=lambda item: _task_sort_key(item.task_id)):
        desired = render_issue(feature, task)
        matches = managed.get(task.task_id, [])
        if len(matches) > 1:
            continue
        if not matches:
            actions.append(
                ReconcileAction(
                    task_id=task.task_id,
                    operation="create",
                    issue_number=None,
                    title=desired.title,
                    body=desired.body,
                    add_labels=desired.labels,
                    state=desired.desired_state,
                    reasons=("missing",),
                )
            )
            continue

        current = matches[0]
        reasons: list[str] = []
        if _normalize_title(current.title) != _normalize_title(desired.title):
            reasons.append("title")
        if _normalize_body(current.body) != _normalize_body(desired.body):
            reasons.append("body")
        current_labels = _label_set(current.labels)
        add_labels = tuple(
            label for label in desired.labels if label.casefold() not in current_labels
        )
        if add_labels:
            reasons.append("labels")
        state = str(current.state or "").strip().lower()
        if state != desired.desired_state:
            reasons.append("state")
        actions.append(
            ReconcileAction(
                task_id=task.task_id,
                operation="update" if reasons else "noop",
                issue_number=current.number,
                title=desired.title,
                body=desired.body,
                add_labels=add_labels,
                state=desired.desired_state,
                reasons=tuple(reasons),
            )
        )

    aligned = not blocking and all(action.operation == "noop" for action in actions)
    return ReconciliationPlan(
        feature=feature,
        aligned=aligned,
        blocking=tuple(blocking),
        actions=tuple(actions),
    )


def plan_to_dict(plan: ReconciliationPlan) -> dict[str, object]:
    return {
        "schema": "mesh.speckit.github-plan.v1",
        "repository": plan.feature.binding.repository,
        "feature_id": plan.feature.binding.feature_id,
        "tasks_file": plan.feature.source_path,
        "aligned": plan.aligned,
        "blocking": [
            {
                "code": item.code,
                "message": item.message,
                "issue_number": item.issue_number,
                "task_id": item.task_id,
            }
            for item in plan.blocking
        ],
        "actions": [
            {
                "task_id": item.task_id,
                "operation": item.operation,
                "issue_number": item.issue_number,
                "state": item.state,
                "add_labels": list(item.add_labels),
                "reasons": list(item.reasons),
                "title": item.title,
            }
            for item in plan.actions
        ],
    }
