#!/usr/bin/env python3
"""Transcript discovery and parsing helpers for Mesh Lite spike work."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _claude_projects_dir() -> Path:
    override = os.environ.get("MESH_LITE_CLAUDE_PROJECTS_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return CLAUDE_PROJECTS_DIR


@dataclass
class AssistantReply:
    path: Path
    text: str


@dataclass
class TranscriptCandidate:
    path: Path
    session_id: str
    cwd: str
    last_modified: float
    assistant_text: str | None

    @property
    def last_modified_iso(self) -> str:
        return datetime.fromtimestamp(self.last_modified).isoformat(timespec="seconds")


def _project_slug(project_path: str) -> str:
    return project_path.replace("/", "-").replace(".", "-")


def candidate_jsonl_paths(project_path: str) -> list[Path]:
    project_dir = _claude_projects_dir() / _project_slug(project_path)
    if not project_dir.exists():
        return []
    paths = [p for p in project_dir.glob("*.jsonl") if p.is_file() and not p.name.startswith("agent-")]
    return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)


def _extract_text_parts(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "text":
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
        elif item_type == "output_text":
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(part for part in parts if part).strip()


def extract_last_assistant_msg(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    last_text: str | None = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        if data.get("type") == "assistant":
            message = data.get("message", {})
            if isinstance(message, dict):
                text = _extract_text_parts(message.get("content"))
                if text:
                    last_text = text
            continue

        message = data.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            text = _extract_text_parts(message.get("content"))
            if text:
                last_text = text

    return last_text


def extract_session_meta(path: Path) -> tuple[str, str]:
    session_id = path.stem
    cwd = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data.get("sessionId"), str) and data["sessionId"]:
                    session_id = data["sessionId"]
                if isinstance(data.get("cwd"), str) and data["cwd"]:
                    cwd = data["cwd"]
                if session_id != path.stem or cwd:
                    break
    except OSError:
        pass
    return session_id, cwd


def transcript_candidates(project_path: str) -> list[TranscriptCandidate]:
    candidates: list[TranscriptCandidate] = []
    for path in candidate_jsonl_paths(project_path):
        session_id, cwd = extract_session_meta(path)
        candidates.append(
            TranscriptCandidate(
                path=path,
                session_id=session_id,
                cwd=cwd,
                last_modified=path.stat().st_mtime,
                assistant_text=extract_last_assistant_msg(path),
            )
        )
    return candidates


def resolve_best_candidate(project_path: str, *, active_within_seconds: int = 900) -> TranscriptCandidate | None:
    now = time.time()
    candidates = transcript_candidates(project_path)
    if not candidates:
        return None

    def score(candidate: TranscriptCandidate) -> tuple[int, float]:
        exact_cwd = 1 if candidate.cwd == project_path else 0
        recent = 1 if now - candidate.last_modified <= active_within_seconds else 0
        has_reply = 1 if candidate.assistant_text else 0
        return (exact_cwd * 10 + recent * 5 + has_reply * 3, candidate.last_modified)

    ranked = sorted(candidates, key=score, reverse=True)
    return ranked[0]


def wait_for_new_assistant_msg(
    path: Path,
    *,
    previous_mtime: float | None = None,
    timeout_seconds: float = 15.0,
    poll_interval: float = 0.25,
) -> str | None:
    start = time.time()
    baseline = previous_mtime if previous_mtime is not None else (path.stat().st_mtime if path.exists() else 0.0)

    while time.time() - start < timeout_seconds:
        if not path.exists():
            time.sleep(poll_interval)
            continue
        current_mtime = path.stat().st_mtime
        if current_mtime > baseline:
            text = extract_last_assistant_msg(path)
            if text:
                return text
        time.sleep(poll_interval)
    return None


def first_reply_for_project(project_path: str) -> AssistantReply | None:
    candidate = resolve_best_candidate(project_path)
    if candidate and candidate.assistant_text:
        return AssistantReply(path=candidate.path, text=candidate.assistant_text)
    return None


def iter_project_replies(project_path: str) -> Iterable[AssistantReply]:
    for candidate in transcript_candidates(project_path):
        if candidate.assistant_text:
            yield AssistantReply(path=candidate.path, text=candidate.assistant_text)
