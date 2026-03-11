from __future__ import annotations

from src.router.workdir_guard import parse_allowed_work_dirs, resolve_work_dir


def test_parse_allowed_work_dirs_deduplicates_and_includes_default() -> None:
    roots = parse_allowed_work_dirs(
        "/tmp/mesh-tasks,/media/sam/1TB,/tmp/mesh-tasks",
        default_work_dir="/tmp/mesh-tasks",
    )
    assert roots == ["/tmp/mesh-tasks", "/media/sam/1TB"]


def test_resolve_work_dir_accepts_relative_child_under_default() -> None:
    resolved = resolve_work_dir(
        "repo-a",
        default_work_dir="/tmp/mesh-tasks",
        allowed_roots=["/tmp/mesh-tasks", "/media/sam/1TB"],
    )
    assert resolved == "/tmp/mesh-tasks/repo-a"


def test_resolve_work_dir_rejects_path_outside_allowed_roots() -> None:
    try:
        resolve_work_dir(
            "/etc",
            default_work_dir="/tmp/mesh-tasks",
            allowed_roots=["/tmp/mesh-tasks", "/media/sam/1TB"],
        )
    except ValueError as exc:
        assert "outside allowed roots" in str(exc)
    else:
        raise AssertionError("expected resolve_work_dir() to reject /etc")
