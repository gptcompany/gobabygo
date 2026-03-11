"""Helpers for validating worker execution directories."""

from __future__ import annotations

import os


def parse_allowed_work_dirs(
    raw_value: str | list[str] | tuple[str, ...] | None,
    *,
    default_work_dir: str = "",
) -> list[str]:
    """Return normalized allowed roots for worker execution.

    ``raw_value`` accepts a CSV string or a pre-split list/tuple.
    ``default_work_dir`` is prepended when set so the worker's own sandbox root
    remains valid even if not repeated in env configuration.
    """

    values: list[str] = []
    if isinstance(raw_value, str):
        values.extend(part.strip() for part in raw_value.split(",") if part.strip())
    elif isinstance(raw_value, (list, tuple)):
        values.extend(str(part).strip() for part in raw_value if str(part).strip())

    if default_work_dir:
        values.insert(0, str(default_work_dir).strip())

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        resolved = os.path.realpath(os.path.abspath(value))
        if resolved not in seen:
            seen.add(resolved)
            normalized.append(resolved)
    return normalized


def resolve_work_dir(
    requested_work_dir: object,
    *,
    default_work_dir: str,
    allowed_roots: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Resolve and validate a task working directory.

    Relative task paths are resolved under ``default_work_dir``.
    When ``allowed_roots`` is non-empty, the resolved directory must stay within
    one of those roots.
    """

    default_root = os.path.realpath(os.path.abspath(str(default_work_dir or "/tmp")))
    raw = str(requested_work_dir or "").strip() or default_root
    candidate = raw if os.path.isabs(raw) else os.path.join(default_root, raw)
    resolved = os.path.realpath(os.path.abspath(candidate))

    roots = [
        os.path.realpath(os.path.abspath(str(root)))
        for root in (allowed_roots or [])
        if str(root).strip()
    ]
    if not roots:
        return resolved

    for root in roots:
        if resolved == root or resolved.startswith(root + os.sep):
            return resolved
    raise ValueError(
        f"working_dir '{resolved}' is outside allowed roots: {', '.join(roots)}"
    )
