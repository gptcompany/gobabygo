"""Topology loader and validator for mesh role wiring.

Reads an optional YAML topology file (MESH_TOPOLOGY_PATH env) at startup.
When absent, all queries return None/empty — preserving legacy behavior.
When present, validates required structure and exposes repo-to-worker mappings.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

REQUIRED_TOP_LEVEL_KEYS = {"version", "global", "hosts", "workers", "repos"}


class TopologyError(Exception):
    """Raised when topology file is required but invalid or missing."""


class Topology:
    """Immutable topology loaded from a validated YAML file."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self._repos: dict[str, Any] = data.get("repos") or {}
        self._global: dict[str, Any] = data.get("global") or {}

    def get_repo_worker_pool(self, repo: str) -> list[str] | None:
        """Return worker_pool for a repo, or None if not defined."""
        repo_cfg = self._repos.get(repo)
        if repo_cfg is None:
            return None
        pool = repo_cfg.get("worker_pool")
        if not pool:
            return None
        return list(pool)

    def get_repo_preferred_host(self, repo: str) -> str | None:
        """Return preferred_host for a repo, or None if not defined."""
        repo_cfg = self._repos.get(repo)
        if repo_cfg is None:
            return None
        return repo_cfg.get("preferred_host")

    def get_repo_notify_room(self, repo: str) -> str | None:
        """Return notify_room for a repo, or None if not defined."""
        repo_cfg = self._repos.get(repo)
        if repo_cfg is None:
            return None
        return repo_cfg.get("notify_room")

    def is_president_handoff_required(self) -> bool:
        """Return whether cross-repo policy requires president handoff."""
        policy = self._global.get("cross_repo_policy") or {}
        return bool(policy.get("require_president_handoff", False))


def _validate(data: Any, path: str) -> dict[str, Any]:
    """Validate topology structure. Raises TopologyError on failure."""
    if not isinstance(data, dict):
        raise TopologyError(f"Topology file {path}: expected YAML mapping, got {type(data).__name__}")
    missing = REQUIRED_TOP_LEVEL_KEYS - set(data.keys())
    if missing:
        raise TopologyError(
            f"Topology file {path}: missing required keys: {', '.join(sorted(missing))}"
        )
    return data


def load_topology(path: str | None) -> Topology | None:
    """Load and validate topology from a YAML file path.

    Args:
        path: File path to YAML topology, or None to disable.

    Returns:
        Topology instance, or None if path is None (disabled).

    Raises:
        TopologyError: If path is set but file is missing or invalid.
    """
    if path is None:
        return None

    p = Path(path)
    if not p.is_file():
        raise TopologyError(f"Topology file not found: {path}")

    try:
        with open(p) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise TopologyError(f"Topology file {path}: invalid YAML: {exc}") from exc

    validated = _validate(data, path)
    logger.info("Topology loaded from %s (version=%s, repos=%d)",
                path, validated.get("version"), len(validated.get("repos") or {}))
    return Topology(validated)
