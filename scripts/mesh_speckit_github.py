#!/usr/bin/env python3
"""Deterministic Spec Kit task ledger primitives.

The module keeps Git and ``tasks.md`` authoritative. GitHub transport and
mutation are added as a separate layer so parsing and planning stay offline and
fully testable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol
from urllib.parse import urlparse


BINDING_SCHEMA = "mesh.speckit.github-ledger.v1"
ISSUE_MARKER_VERSION = "v1"
BINDING_FILE = "github-ledger.json"
TASKS_FILE = "tasks.md"
MAX_BINDING_BYTES = 16 * 1024
MAX_TASKS_BYTES = 1024 * 1024
MAX_DESCRIPTION_CHARS = 4_000
MAX_ISSUE_TITLE_CHARS = 256
MAX_GH_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_REMOTE_ISSUES = 1_000
MAX_BOUND_FEATURES = 100
MIN_GH_VERSION = (2, 40, 0)
CALLER_WORKFLOW = Path(".github/workflows/speckit-ledger.yml")
DEFAULT_RUNTIME_REPOSITORY = "gptcompany/gobabygo"

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


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


RunCommand = Callable[[tuple[str, ...], str | None], CommandResult]


@dataclass(frozen=True)
class ApplyResult:
    initial_plan: ReconciliationPlan
    final_plan: ReconciliationPlan
    mutations: int


@dataclass(frozen=True)
class BindingPlan:
    repo_root: Path
    feature_dir: Path
    binding_path: Path
    binding: FeatureBinding
    operation: str


@dataclass(frozen=True)
class CallerPlan:
    repo_root: Path
    workflow_path: Path
    repository: str
    runtime_repository: str
    runtime_ref: str
    content: str
    operation: str


class GitHubClient(Protocol):
    def list_issues(self) -> tuple[RemoteIssue, ...]: ...

    def list_labels(self) -> tuple[str, ...]: ...

    def create_label(self, name: str, *, color: str, description: str) -> None: ...

    def create_issue(self, *, title: str, body: str, labels: tuple[str, ...]) -> int: ...

    def update_issue(
        self,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
    ) -> None: ...

    def add_labels(self, number: int, labels: tuple[str, ...]) -> None: ...


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


def parse_github_remote(remote: str) -> str:
    value = str(remote or "").strip()
    scp_match = re.fullmatch(r"git@github\.com:([^/]+)/([^/]+?)(?:\.git)?", value)
    if scp_match:
        repository = f"{scp_match.group(1)}/{scp_match.group(2)}".lower()
    else:
        parsed = urlparse(value)
        if parsed.scheme not in {"https", "ssh"} or parsed.hostname != "github.com":
            raise LedgerError("origin is not a supported GitHub remote")
        if parsed.query or parsed.fragment or parsed.params:
            raise LedgerError("GitHub remote must not contain query or fragment data")
        if parsed.scheme == "https" and (parsed.username or parsed.password):
            raise LedgerError("GitHub remote must not contain credentials")
        if parsed.scheme == "ssh" and parsed.username not in {None, "git"}:
            raise LedgerError("SSH GitHub remote must use the git user")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2:
            raise LedgerError("GitHub remote must identify exactly owner/repo")
        repo_name = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
        repository = f"{parts[0]}/{repo_name}".lower()
    if not _REPOSITORY_RE.fullmatch(repository):
        raise LedgerError("invalid GitHub remote owner/repo")
    return repository


def _default_run(args: tuple[str, ...], input_text: str | None = None) -> CommandResult:
    try:
        result = subprocess.run(
            list(args),
            input=input_text,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LedgerError(f"cannot execute {args[0] if args else 'command'}") from exc
    return CommandResult(result.returncode, result.stdout, result.stderr)


def verify_checkout_binding(
    feature: LoadedFeature,
    *,
    run: RunCommand = _default_run,
    environ: Mapping[str, str] | None = None,
) -> None:
    args = (
        "git",
        "-C",
        str(feature.repo_root),
        "config",
        "--get",
        "remote.origin.url",
    )
    result = run(args, None)
    if result.returncode != 0:
        raise LedgerError("cannot resolve origin repository")
    origin = parse_github_remote(result.stdout)
    if origin != feature.binding.repository:
        raise LedgerError(
            f"origin repository {origin} does not match binding {feature.binding.repository}"
        )
    action_repository = (environ or {}).get("GITHUB_REPOSITORY", "").strip().lower()
    if action_repository and action_repository != feature.binding.repository:
        raise LedgerError(
            f"GITHUB_REPOSITORY {action_repository} does not match binding {feature.binding.repository}"
        )


def _decode_json_output(output: str, *, label: str) -> object:
    if len(output.encode("utf-8", errors="replace")) > MAX_GH_OUTPUT_BYTES:
        raise LedgerError(f"{label} output exceeds {MAX_GH_OUTPUT_BYTES} bytes")
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"{label} returned malformed JSON") from exc


class GhClient:
    """Bounded ``gh api`` transport with no shell interpolation."""

    def __init__(self, repository: str, *, run: RunCommand = _default_run) -> None:
        if not _REPOSITORY_RE.fullmatch(repository):
            raise LedgerError("invalid GitHub repository")
        self.repository = repository
        self._run = run
        self._version: tuple[int, int, int] | None = None

    def _execute(self, args: tuple[str, ...], input_text: str | None = None) -> str:
        result = self._run(args, input_text)
        if result.returncode != 0:
            # stderr may contain echoed credentials or environment values.
            raise LedgerError(f"{args[0]} command failed with exit {result.returncode}")
        if len(result.stdout.encode("utf-8", errors="replace")) > MAX_GH_OUTPUT_BYTES:
            raise LedgerError(f"GitHub output exceeds {MAX_GH_OUTPUT_BYTES} bytes")
        return result.stdout

    @property
    def version(self) -> tuple[int, int, int]:
        if self._version is None:
            output = self._execute(("gh", "version"))
            match = re.search(r"\bgh version (\d+)\.(\d+)\.(\d+)\b", output)
            if not match:
                raise LedgerError("cannot parse gh version")
            self._version = tuple(int(part) for part in match.groups())  # type: ignore[assignment]
            if self._version < MIN_GH_VERSION:
                raise LedgerError("gh 2.40 or newer is required")
        return self._version

    def _paginated(self, endpoint: str, *, label: str) -> list[object]:
        _ = self.version
        output = self._execute(("gh", "api", "--paginate", "--slurp", endpoint))
        payload = _decode_json_output(output, label=label)
        if not isinstance(payload, list) or any(not isinstance(page, list) for page in payload):
            raise LedgerError(f"{label} must return a paginated array")
        return [item for page in payload for item in page]

    def _api(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        _ = self.version
        args = ("gh", "api", "--method", method, endpoint)
        input_text = None
        if payload is not None:
            args = (*args, "--input", "-")
            input_text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        output = self._execute(args, input_text)
        if not output.strip():
            return None
        return _decode_json_output(output, label="GitHub API")

    def list_issues(self) -> tuple[RemoteIssue, ...]:
        items = self._paginated(
            f"repos/{self.repository}/issues?state=all&per_page=100",
            label="GitHub issues",
        )
        issues: list[RemoteIssue] = []
        for item in items:
            if not isinstance(item, dict):
                raise LedgerError("GitHub issues contained a non-object entry")
            if "pull_request" in item:
                continue
            number = item.get("number")
            title = item.get("title")
            body = item.get("body")
            state = item.get("state")
            raw_labels = item.get("labels")
            if (
                not isinstance(number, int)
                or number <= 0
                or not isinstance(title, str)
                or body is not None and not isinstance(body, str)
                or state not in {"open", "closed"}
                or not isinstance(raw_labels, list)
            ):
                raise LedgerError("GitHub issues contained invalid allowlisted fields")
            labels: list[str] = []
            for raw_label in raw_labels:
                if not isinstance(raw_label, dict) or not isinstance(raw_label.get("name"), str):
                    raise LedgerError("GitHub issue labels contained invalid fields")
                labels.append(raw_label["name"])
            issues.append(RemoteIssue(number, title, body or "", state, tuple(labels)))
            if len(issues) > MAX_REMOTE_ISSUES:
                raise LedgerError(f"GitHub issue count exceeds supported limit {MAX_REMOTE_ISSUES}")
        return tuple(issues)

    def list_labels(self) -> tuple[str, ...]:
        items = self._paginated(
            f"repos/{self.repository}/labels?per_page=100",
            label="GitHub labels",
        )
        labels: list[str] = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise LedgerError("GitHub labels contained invalid fields")
            labels.append(item["name"])
        return tuple(labels)

    def create_label(self, name: str, *, color: str, description: str) -> None:
        self._api(
            "POST",
            f"repos/{self.repository}/labels",
            {"name": name, "color": color, "description": description},
        )

    def create_issue(self, *, title: str, body: str, labels: tuple[str, ...]) -> int:
        response = self._api(
            "POST",
            f"repos/{self.repository}/issues",
            {"title": title, "body": body, "labels": list(labels)},
        )
        if not isinstance(response, dict) or not isinstance(response.get("number"), int):
            raise LedgerError("GitHub create issue response lacks an issue number")
        return response["number"]

    def update_issue(
        self,
        number: int,
        *,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
    ) -> None:
        payload: dict[str, object] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        if payload:
            self._api("PATCH", f"repos/{self.repository}/issues/{number}", payload)

    def add_labels(self, number: int, labels: tuple[str, ...]) -> None:
        if labels:
            self._api(
                "POST",
                f"repos/{self.repository}/issues/{number}/labels",
                {"labels": list(labels)},
            )


def validate_apply_environment(
    binding: FeatureBinding, environ: Mapping[str, str] | None = None
) -> None:
    env = dict(os.environ if environ is None else environ)
    if env.get("GITHUB_ACTIONS", "").lower() != "true":
        raise LedgerError("authoritative apply is restricted to GitHub Actions")
    if env.get("MESH_SPECKIT_LEDGER_APPLY") != "1":
        raise LedgerError("authoritative apply gate is not enabled")
    event = env.get("GITHUB_EVENT_NAME", "")
    if event not in {"push", "workflow_dispatch"}:
        raise LedgerError(f"GitHub event {event or '-'} cannot apply the ledger")
    repository = env.get("GITHUB_REPOSITORY", "").strip().lower()
    if repository != binding.repository:
        raise LedgerError("GitHub Actions repository does not match the feature binding")
    default_branch = env.get("MESH_DEFAULT_BRANCH", "").strip()
    if not default_branch or env.get("GITHUB_REF") != f"refs/heads/{default_branch}":
        raise LedgerError("authoritative apply must run from the default branch")


_LABEL_DEFINITIONS = {
    "speckit-task": ("1d76db", "Task derived one-way from Spec Kit"),
}


def apply_authoritative(
    feature: LoadedFeature,
    client: GitHubClient,
    environ: Mapping[str, str] | None = None,
) -> ApplyResult:
    validate_apply_environment(feature.binding, environ)
    initial = build_plan(feature, client.list_issues())
    if initial.blocking:
        codes = ", ".join(item.code for item in initial.blocking)
        raise LedgerError(f"refusing authoritative apply due to blocking drift: {codes}")
    if initial.aligned:
        return ApplyResult(initial, initial, 0)

    mutations = 0
    existing_labels = {label.casefold() for label in client.list_labels()}
    feature_label = f"speckit:{feature.binding.feature_id}"
    definitions = {
        **_LABEL_DEFINITIONS,
        feature_label: ("5319e7", f"Spec Kit feature {feature.binding.feature_id}"),
    }
    for name, (color, description) in definitions.items():
        if name.casefold() not in existing_labels:
            client.create_label(name, color=color, description=description)
            existing_labels.add(name.casefold())
            mutations += 1

    for action in initial.actions:
        if action.operation == "noop":
            continue
        if action.operation == "create":
            number = client.create_issue(
                title=action.title,
                body=action.body,
                labels=action.add_labels,
            )
            mutations += 1
            if action.state == "closed":
                client.update_issue(number, state="closed")
                mutations += 1
            continue
        if action.operation != "update" or action.issue_number is None:
            raise LedgerError(f"unsupported reconciliation operation: {action.operation}")
        content_reasons = set(action.reasons) & {"title", "body", "state"}
        if content_reasons:
            client.update_issue(
                action.issue_number,
                title=action.title if "title" in content_reasons else None,
                body=action.body if "body" in content_reasons else None,
                state=action.state if "state" in content_reasons else None,
            )
            mutations += 1
        if action.add_labels:
            client.add_labels(action.issue_number, action.add_labels)
            mutations += 1

    final = build_plan(feature, client.list_issues())
    if not final.aligned:
        raise LedgerError("GitHub ledger is not aligned after authoritative apply")
    return ApplyResult(initial, final, mutations)


def discover_git_root(path: Path, *, run: RunCommand = _default_run) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        start = candidate.resolve(strict=True)
    except OSError as exc:
        raise LedgerError(f"path does not exist: {candidate}") from exc
    result = run(("git", "-C", str(start), "rev-parse", "--show-toplevel"), None)
    if result.returncode != 0:
        raise LedgerError(f"path is not inside a Git checkout: {start}")
    raw_root = result.stdout.strip()
    if not raw_root or "\n" in raw_root:
        raise LedgerError("git returned an invalid repository root")
    try:
        root = Path(raw_root).resolve(strict=True)
    except OSError as exc:
        raise LedgerError("git repository root does not exist") from exc
    if not (root / ".git").exists():
        raise LedgerError(f"repository root is not an exact Git checkout: {root}")
    return root


def checkout_repository(repo_root: Path, *, run: RunCommand = _default_run) -> str:
    result = run(
        (
            "git",
            "-C",
            str(repo_root),
            "config",
            "--get",
            "remote.origin.url",
        ),
        None,
    )
    if result.returncode != 0:
        raise LedgerError("cannot resolve origin repository")
    return parse_github_remote(result.stdout)


def derive_feature_id(repository: str, feature_relative: str) -> str:
    name = Path(feature_relative).name.lower()
    slug = re.sub(r"^\d+[-_]", "", name)
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-") or "feature"
    slug = slug[:20].rstrip("-") or "feature"
    digest = hashlib.sha256(
        f"{repository}\n{feature_relative}".encode("utf-8")
    ).hexdigest()[:12]
    value = f"{slug}-{digest}"
    if not _FEATURE_ID_RE.fullmatch(value):
        raise LedgerError("cannot derive a valid feature_id")
    return value


def build_binding_plan(
    repo_root: Path,
    feature_dir: Path,
    *,
    repository: str,
    feature_id: str | None = None,
) -> BindingPlan:
    root, feature = _resolve_feature_dir(repo_root, feature_dir)
    if not _REPOSITORY_RE.fullmatch(repository) or repository.lower() != repository:
        raise LedgerError("repository must use canonical lowercase owner/repo")
    tasks_path = feature / TASKS_FILE
    parse_tasks(_read_bounded(tasks_path, MAX_TASKS_BYTES, "tasks file"))
    relative = feature.relative_to(root).as_posix()
    binding_path = feature / BINDING_FILE

    if binding_path.exists() or binding_path.is_symlink():
        existing = load_binding(binding_path)
        if existing.repository != repository:
            raise LedgerError("existing binding repository does not match origin")
        if feature_id is not None and feature_id != existing.feature_id:
            raise LedgerError("existing binding feature_id is immutable")
        return BindingPlan(root, feature, binding_path, existing, "noop")

    selected_id = feature_id or derive_feature_id(repository, relative)
    if not _FEATURE_ID_RE.fullmatch(selected_id):
        raise LedgerError("invalid feature_id; use 8-40 lowercase letters, digits, or hyphens")
    binding = FeatureBinding(BINDING_SCHEMA, selected_id, repository, True)
    return BindingPlan(root, feature, binding_path, binding, "create")


def binding_plan_to_dict(plan: BindingPlan, *, applied: bool) -> dict[str, object]:
    return {
        "schema": "mesh.speckit.github-binding-plan.v1",
        "repository": plan.binding.repository,
        "feature_id": plan.binding.feature_id,
        "feature_dir": plan.feature_dir.relative_to(plan.repo_root).as_posix(),
        "binding_file": plan.binding_path.relative_to(plan.repo_root).as_posix(),
        "operation": plan.operation,
        "applied": applied,
    }


def apply_binding_plan(plan: BindingPlan) -> bool:
    if plan.operation == "noop":
        return False
    if plan.operation != "create":
        raise LedgerError(f"unsupported binding operation: {plan.operation}")
    if plan.binding_path.exists() or plan.binding_path.is_symlink():
        raise LedgerError("ledger binding appeared while applying the plan")
    payload = {
        "schema": plan.binding.schema,
        "feature_id": plan.binding.feature_id,
        "repository": plan.binding.repository,
        "enabled": plan.binding.enabled,
    }
    fd, temporary = tempfile.mkstemp(
        prefix=".github-ledger.", suffix=".tmp", dir=plan.feature_dir
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if plan.binding_path.exists() or plan.binding_path.is_symlink():
            raise LedgerError("ledger binding appeared while applying the plan")
        os.replace(temp_path, plan.binding_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return True


def render_caller_workflow(runtime_repository: str, runtime_ref: str) -> str:
    if not _REPOSITORY_RE.fullmatch(runtime_repository):
        raise LedgerError("invalid runtime repository; expected lowercase owner/repo")
    if not re.fullmatch(r"[0-9a-f]{40}", runtime_ref):
        raise LedgerError("runtime ref must be a full lowercase 40-character commit SHA")
    reusable = (
        f"{runtime_repository}/.github/workflows/"
        f"speckit-ledger-reusable.yml@{runtime_ref}"
    )
    return f'''name: Spec Kit GitHub Ledger

on:
  pull_request:
    paths:
      - "specs/**/tasks.md"
      - "specs/**/github-ledger.json"
  push:
    paths:
      - "specs/**/tasks.md"
      - "specs/**/github-ledger.json"
  workflow_dispatch:

concurrency:
  group: speckit-ledger-${{{{ github.repository }}}}
  cancel-in-progress: false

jobs:
  check:
    if: github.event_name == 'pull_request'
    permissions:
      contents: read
      issues: read
    uses: {reusable}
    with:
      mode: plan
      runtime_ref: {runtime_ref}

  apply:
    if: (github.event_name == 'push' && github.ref_name == github.event.repository.default_branch) || github.event_name == 'workflow_dispatch'
    permissions:
      contents: read
      issues: write
    uses: {reusable}
    with:
      mode: apply
      runtime_ref: {runtime_ref}
'''


def build_caller_plan(
    repo_root: Path,
    *,
    repository: str,
    runtime_repository: str,
    runtime_ref: str,
) -> CallerPlan:
    root = repo_root.resolve(strict=True)
    if not (root / ".git").exists():
        raise LedgerError(f"repository root is not an exact Git checkout: {root}")
    content = render_caller_workflow(runtime_repository, runtime_ref)
    workflow_path = root / CALLER_WORKFLOW
    operation = "create"
    if workflow_path.exists() or workflow_path.is_symlink():
        if workflow_path.is_symlink() or not workflow_path.is_file():
            raise LedgerError(f"caller workflow must be a regular file: {workflow_path}")
        current = _read_bounded(workflow_path, MAX_TASKS_BYTES, "caller workflow")
        if current != content:
            raise LedgerError(
                "caller workflow already exists with different content; review it manually"
            )
        operation = "noop"
    return CallerPlan(
        root,
        workflow_path,
        repository,
        runtime_repository,
        runtime_ref,
        content,
        operation,
    )


def caller_plan_to_dict(plan: CallerPlan, *, applied: bool) -> dict[str, object]:
    return {
        "schema": "mesh.speckit.github-caller-plan.v1",
        "repository": plan.repository,
        "runtime_repository": plan.runtime_repository,
        "runtime_ref": plan.runtime_ref,
        "workflow_file": plan.workflow_path.relative_to(plan.repo_root).as_posix(),
        "operation": plan.operation,
        "applied": applied,
    }


def apply_caller_plan(plan: CallerPlan) -> bool:
    if plan.operation == "noop":
        return False
    if plan.operation != "create":
        raise LedgerError(f"unsupported caller operation: {plan.operation}")
    if plan.workflow_path.exists() or plan.workflow_path.is_symlink():
        raise LedgerError("caller workflow appeared while applying the plan")
    plan.workflow_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".speckit-ledger.", suffix=".tmp", dir=plan.workflow_path.parent
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(plan.content)
            stream.flush()
            os.fsync(stream.fileno())
        if plan.workflow_path.exists() or plan.workflow_path.is_symlink():
            raise LedgerError("caller workflow appeared while applying the plan")
        os.replace(temp_path, plan.workflow_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return True


def _render_plan(payload: dict[str, object]) -> str:
    if payload.get("schema") == "mesh.speckit.github-caller-plan.v1":
        return "\n".join(
            [
                "Spec Kit GitHub ledger caller",
                f"repository={payload['repository']}",
                f"runtime={payload['runtime_repository']}@{payload['runtime_ref']}",
                f"operation={payload['operation']}",
                f"workflow_file={payload['workflow_file']}",
                f"applied={'yes' if payload['applied'] else 'no'}",
            ]
        )
    if payload.get("schema") == "mesh.speckit.github-batch.v1":
        lines = [
            "Spec Kit GitHub ledger batch",
            f"repository={payload['repository']}",
            f"features={len(payload['features'])}",
            f"aligned={'yes' if payload['aligned'] else 'no'}",
            f"mutations={payload.get('mutations', 0)}",
        ]
        for feature in payload["features"]:
            lines.append(
                f"{feature['feature_id']} aligned={'yes' if feature['aligned'] else 'no'} "
                f"blocking={len(feature['blocking'])}"
            )
        return "\n".join(lines)
    lines = [
        "Spec Kit GitHub ledger",
        f"repository={payload['repository']}",
        f"feature_id={payload['feature_id']}",
    ]
    if "aligned" in payload:
        lines.append(f"aligned={'yes' if payload['aligned'] else 'no'}")
        for item in payload.get("blocking", []):
            lines.append(f"BLOCKING {item['code']}: {item['message']}")
        for item in payload.get("actions", []):
            reasons = ",".join(item["reasons"]) or "-"
            lines.append(
                f"{item['task_id']} {item['operation']} state={item['state']} reasons={reasons}"
            )
    else:
        lines.extend(
            [
                f"operation={payload['operation']}",
                f"binding_file={payload['binding_file']}",
                f"applied={'yes' if payload['applied'] else 'no'}",
            ]
        )
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Plan or create one feature ledger binding.")
    init.add_argument("feature_dir", type=Path)
    init.add_argument("--feature-id")
    init.add_argument("--apply", action="store_true")
    init.add_argument("--json", action="store_true")
    caller = sub.add_parser(
        "install-caller", help="Plan or install a pinned minimal caller workflow."
    )
    caller.add_argument("repo", type=Path)
    caller.add_argument("--runtime-ref", required=True)
    caller.add_argument(
        "--runtime-repository", default=DEFAULT_RUNTIME_REPOSITORY
    )
    caller.add_argument("--apply", action="store_true")
    caller.add_argument("--json", action="store_true")
    for name in ("plan", "check", "apply"):
        command = sub.add_parser(name, help=f"{name.title()} one feature ledger.")
        command.add_argument("feature_dir", nargs="?", type=Path)
        command.add_argument("--all", action="store_true", dest="all_features")
        command.add_argument("--repo-root", type=Path, default=Path.cwd())
        command.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def discover_bound_features(repo_root: Path) -> tuple[LoadedFeature, ...]:
    root = repo_root.resolve(strict=True)
    specs = root / "specs"
    if not specs.is_dir():
        raise LedgerError(f"Spec Kit specs directory not found: {specs}")
    binding_paths = sorted(specs.rglob(BINDING_FILE))
    if not binding_paths:
        raise LedgerError("no opted-in Spec Kit GitHub ledger features found")
    if len(binding_paths) > MAX_BOUND_FEATURES:
        raise LedgerError(
            f"opted-in feature count exceeds supported limit {MAX_BOUND_FEATURES}"
        )
    features: list[LoadedFeature] = []
    seen_ids: set[str] = set()
    for binding_path in binding_paths:
        if binding_path.is_symlink():
            raise LedgerError(f"ledger binding must not be a symlink: {binding_path}")
        feature = load_feature(root, binding_path.parent)
        if feature.binding.feature_id in seen_ids:
            raise LedgerError(
                f"duplicate feature_id across bindings: {feature.binding.feature_id}"
            )
        seen_ids.add(feature.binding.feature_id)
        features.append(feature)
    return tuple(features)


def _batch_to_dict(
    repository: str,
    plans: tuple[ReconciliationPlan, ...],
    *,
    mutations: int = 0,
) -> dict[str, object]:
    return {
        "schema": "mesh.speckit.github-batch.v1",
        "repository": repository,
        "aligned": all(plan.aligned for plan in plans),
        "mutations": mutations,
        "features": [plan_to_dict(plan) for plan in plans],
    }


def _select_features(
    args: argparse.Namespace, *, run: RunCommand
) -> tuple[Path, tuple[LoadedFeature, ...]]:
    if bool(args.feature_dir) == bool(args.all_features):
        raise LedgerError("provide exactly one feature directory or --all")
    if args.all_features:
        root = discover_git_root(args.repo_root, run=run)
        return root, discover_bound_features(root)
    root = discover_git_root(args.feature_dir, run=run)
    return root, (load_feature(root, args.feature_dir),)


def main(
    argv: list[str] | None = None,
    *,
    run: RunCommand = _default_run,
    environ: Mapping[str, str] | None = None,
) -> int:
    args = _parse_args(argv)
    env = dict(os.environ if environ is None else environ)
    try:
        if args.command == "install-caller":
            root = discover_git_root(args.repo, run=run)
            repository = checkout_repository(root, run=run)
            caller_plan = build_caller_plan(
                root,
                repository=repository,
                runtime_repository=args.runtime_repository,
                runtime_ref=args.runtime_ref,
            )
            changed = apply_caller_plan(caller_plan) if args.apply else False
            output = caller_plan_to_dict(
                caller_plan,
                applied=bool(args.apply and (changed or caller_plan.operation == "noop")),
            )
            exit_code = 0
        elif args.command == "init":
            root = discover_git_root(args.feature_dir, run=run)
            repository = checkout_repository(root, run=run)
            binding_plan = build_binding_plan(
                root,
                args.feature_dir,
                repository=repository,
                feature_id=args.feature_id,
            )
            changed = apply_binding_plan(binding_plan) if args.apply else False
            output = binding_plan_to_dict(
                binding_plan, applied=bool(args.apply and (changed or binding_plan.operation == "noop"))
            )
            exit_code = 0
        else:
            root, features = _select_features(args, run=run)
            repository = checkout_repository(root, run=run)
            if any(feature.binding.repository != repository for feature in features):
                raise LedgerError("one or more feature bindings do not match origin")
            for feature in features:
                verify_checkout_binding(feature, run=run, environ=env)
            if args.command == "apply":
                for feature in features:
                    validate_apply_environment(feature.binding, env)
            client = GhClient(repository, run=run)
            snapshot = client.list_issues()
            preflight = tuple(build_plan(feature, snapshot) for feature in features)
            if args.command == "apply":
                blocking = [item for plan in preflight for item in plan.blocking]
                if blocking:
                    codes = ", ".join(item.code for item in blocking)
                    raise LedgerError(
                        f"refusing authoritative batch apply due to blocking drift: {codes}"
                    )
                results = tuple(
                    apply_authoritative(feature, client, env) for feature in features
                )
                final_plans = tuple(result.final_plan for result in results)
                output = _batch_to_dict(
                    repository,
                    final_plans,
                    mutations=sum(result.mutations for result in results),
                )
                exit_code = 0
            elif len(features) == 1 and not args.all_features:
                reconciliation = preflight[0]
                output = plan_to_dict(reconciliation)
                if reconciliation.blocking:
                    exit_code = 2
                elif args.command == "check" and not reconciliation.aligned:
                    exit_code = 1
                else:
                    exit_code = 0
            else:
                output = _batch_to_dict(repository, preflight)
                if any(plan.blocking for plan in preflight):
                    exit_code = 2
                elif args.command == "check" and not all(
                    plan.aligned for plan in preflight
                ):
                    exit_code = 1
                else:
                    exit_code = 0
    except LedgerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print(_render_plan(output))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
