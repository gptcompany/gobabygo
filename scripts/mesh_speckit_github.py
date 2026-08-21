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
