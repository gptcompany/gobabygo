from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _load_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "mesh_live_worker.py"
    spec = importlib.util.spec_from_file_location("mesh_live_worker", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path.resolve()


def _completed(args: list[str], returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr=stderr)


def test_resolve_repo_requires_configured_exact_git_root(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "rektslug")
    subdir = repo / "src"
    subdir.mkdir()
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", str(tmp_path))
    monkeypatch.setattr(module, "_control_plane_root", lambda: None)

    assert module.resolve_repo("rektslug") == repo
    assert module.resolve_repo(str(repo)) == repo
    with pytest.raises(module.WorkerEnsureError, match="Git repository root"):
        module.resolve_repo(str(subdir))
    with pytest.raises(module.WorkerEnsureError, match="outside configured roots"):
        module.resolve_repo("/tmp")


def test_resolve_repo_rejects_ambiguous_name(monkeypatch, tmp_path) -> None:
    module = _load_module()
    first = tmp_path / "one"
    second = tmp_path / "two"
    _git_repo(first / "same")
    _git_repo(second / "same")
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", os.pathsep.join([str(first), str(second)]))
    monkeypatch.setattr(module, "_control_plane_root", lambda: None)

    with pytest.raises(module.WorkerEnsureError, match="ambiguous"):
        module.resolve_repo("same")


def test_resolve_repo_rejects_active_control_plane_before_tmux(monkeypatch, tmp_path) -> None:
    module = _load_module()
    runtime = _git_repo(tmp_path / "gobabygo-runtime")
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", str(tmp_path))
    monkeypatch.setattr(module, "_control_plane_root", lambda: runtime)
    tmux_called = False
    real_run = module._run_command

    def fake_run(args: list[str], *, timeout: float = 10.0):
        nonlocal tmux_called
        if args[0] == "tmux":
            tmux_called = True
        return real_run(args, timeout=timeout)

    monkeypatch.setattr(module, "_run_command", fake_run)

    with pytest.raises(module.WorkerEnsureError, match="control-plane checkout"):
        module.ensure_codex_worker(str(runtime))
    assert tmux_called is False


def test_session_name_is_sanitized_and_bounded() -> None:
    module = _load_module()

    assert module.session_name_for_repo(Path("My Repo")) == "codex-my-repo"
    assert (
        module.session_name_for_repo(Path("My Repo"), "antigravity")
        == "antigravity-my-repo"
    )
    with pytest.raises(module.WorkerEnsureError, match="cannot form"):
        module.session_name_for_repo(Path("---"))
    with pytest.raises(module.WorkerEnsureError, match="too long"):
        module.session_name_for_repo(Path("a" * 80))


def test_codex_executable_accepts_only_fixed_executable_candidates(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    trusted = tmp_path / "codex"
    trusted.write_text("#!/bin/sh\n", encoding="utf-8")
    trusted.chmod(0o755)
    monkeypatch.setattr(module, "_CODEX_CANDIDATES", (str(tmp_path / "missing"), str(trusted)))

    assert module._codex_executable() == str(trusted)

    trusted.chmod(0o644)
    with pytest.raises(module.WorkerEnsureError, match="trusted Codex executable"):
        module._codex_executable()


def test_antigravity_executable_accepts_only_fixed_candidates(monkeypatch, tmp_path) -> None:
    module = _load_module()
    trusted = tmp_path / "agy"
    trusted.write_text("#!/bin/sh\n", encoding="utf-8")
    trusted.chmod(0o755)
    monkeypatch.setattr(
        module,
        "_ANTIGRAVITY_CANDIDATES",
        (str(tmp_path / "missing"), str(trusted)),
    )

    assert module._antigravity_executable() == str(trusted)

    trusted.chmod(0o644)
    with pytest.raises(module.WorkerEnsureError, match="trusted Antigravity executable"):
        module._antigravity_executable()


@pytest.mark.parametrize(
    ("display_result", "message"),
    [
        ((1, "", "tmux inspect failed\n"), "tmux inspect failed"),
        ((0, "wrong output\n", ""), "metadata changed"),
    ],
)
def test_inspect_session_rejects_tmux_errors_and_changed_metadata(
    monkeypatch, display_result, message
) -> None:
    module = _load_module()
    returncode, stdout, stderr = display_result

    def fake_run(args: list[str], *, timeout: float = 10.0):
        if args[:2] == ["tmux", "has-session"]:
            return _completed(args)
        return _completed(args, returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(module, "_run_command", fake_run)

    with pytest.raises(module.WorkerEnsureError, match=message):
        module._inspect_session("codex-rektslug")


def test_ensure_codex_creates_fixed_yolo_worker_without_send_keys(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "rektslug")
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", str(tmp_path))
    monkeypatch.setattr(module, "_codex_executable", lambda: "/usr/local/bin/codex")
    commands: list[list[str]] = []
    created = False

    def fake_run(args: list[str], *, timeout: float = 10.0):
        nonlocal created
        if args[:2] == ["git", "-C"]:
            return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
        commands.append(args)
        if args[:2] == ["tmux", "has-session"]:
            return _completed(args, returncode=0 if created else 1)
        if args[:2] == ["tmux", "new-session"]:
            created = True
            return _completed(args)
        if args[:2] == ["tmux", "display-message"]:
            fields = module._FIELD_SEPARATOR.join(
                ["codex-rektslug", str(repo), "codex", "0", "1"]
            )
            return _completed(args, stdout=fields + "\n")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(module, "_run_command", fake_run)

    result = module.ensure_codex_worker(str(repo), expected_session="codex-rektslug")

    assert result == {
        "session": "codex-rektslug",
        "repo": str(repo),
        "created": True,
        "ready": True,
    }
    launch = next(command for command in commands if command[:2] == ["tmux", "new-session"])
    assert launch[:8] == [
        "tmux",
        "new-session",
        "-d",
        "-s",
        "codex-rektslug",
        "-c",
        str(repo),
        "exec /usr/local/bin/codex --dangerously-bypass-approvals-and-sandbox -C " + str(repo),
    ]
    assert not any("send-keys" in command for command in commands)


def test_ensure_antigravity_creates_repo_pinned_yolo_worker_without_send_keys(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "rektslug")
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", str(tmp_path))
    monkeypatch.setattr(module, "_antigravity_executable", lambda: "/home/sam/.local/bin/agy")
    commands: list[list[str]] = []
    created = False
    displays = 0

    def fake_run(args: list[str], *, timeout: float = 10.0):
        nonlocal created, displays
        if args[:2] == ["git", "-C"]:
            return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
        commands.append(args)
        if args[:2] == ["tmux", "has-session"]:
            return _completed(args, returncode=0 if created else 1)
        if args[:2] == ["tmux", "new-session"]:
            created = True
            return _completed(args)
        if args[:2] == ["tmux", "display-message"]:
            displays += 1
            command = "tmux" if displays == 1 else "agy"
            pane_path = "/home/sam" if displays == 1 else str(repo)
            fields = module._FIELD_SEPARATOR.join(
                ["antigravity-rektslug", pane_path, command, "0", "1"]
            )
            return _completed(args, stdout=fields + "\n")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(module, "_run_command", fake_run)

    result = module.ensure_antigravity_worker(
        str(repo), expected_session="antigravity-rektslug"
    )

    assert result == {
        "session": "antigravity-rektslug",
        "repo": str(repo),
        "created": True,
        "ready": True,
    }
    launch = next(command for command in commands if command[:2] == ["tmux", "new-session"])
    startup = launch[-1]
    assert launch[3:7] == ["-s", "antigravity-rektslug", "-c", str(repo)]
    assert startup.startswith(
        "exec /home/sam/.local/bin/agy --dangerously-skip-permissions --new-project "
        "--prompt-interactive "
    )
    assert module._ANTIGRAVITY_BOOTSTRAP_PROMPT in startup
    assert "Do not inspect or modify files and do not run commands." in startup
    assert not any("send-keys" in command for command in commands)


def test_ensure_codex_reuses_matching_live_worker(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "rektslug")
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", str(tmp_path))

    def fake_run(args: list[str], *, timeout: float = 10.0):
        if args[:2] == ["git", "-C"]:
            return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
        if args[:2] == ["tmux", "has-session"]:
            return _completed(args)
        if args[:2] == ["tmux", "display-message"]:
            fields = module._FIELD_SEPARATOR.join(
                ["codex-rektslug", str(repo), "codex", "0", "1"]
            )
            return _completed(args, stdout=fields + "\n")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(module, "_run_command", fake_run)

    result = module.ensure_codex_worker("rektslug")

    assert result["created"] is False
    assert result["ready"] is True


def test_ensure_antigravity_waits_for_existing_startup_path_transition(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "rektslug")
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", str(tmp_path))
    monkeypatch.setattr(module.time, "sleep", lambda _delay: None)
    displays = 0

    def fake_run(args: list[str], *, timeout: float = 10.0):
        nonlocal displays
        if args[:2] == ["git", "-C"]:
            return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
        if args[:2] == ["tmux", "has-session"]:
            return _completed(args)
        if args[:2] == ["tmux", "display-message"]:
            displays += 1
            pane_path = "/home/sam" if displays == 1 else str(repo)
            fields = module._FIELD_SEPARATOR.join(
                ["antigravity-rektslug", pane_path, "agy", "0", "1"]
            )
            return _completed(args, stdout=fields + "\n")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(module, "_run_command", fake_run)

    result = module.ensure_antigravity_worker("rektslug")

    assert result["created"] is False
    assert result["ready"] is True
    assert displays == 2


def test_ensure_codex_reuses_concurrent_atomic_winner(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "rektslug")
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", str(tmp_path))
    monkeypatch.setattr(module, "_codex_executable", lambda: "/usr/local/bin/codex")
    inspections = 0

    def fake_run(args: list[str], *, timeout: float = 10.0):
        nonlocal inspections
        if args[:2] == ["git", "-C"]:
            return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
        if args[:2] == ["tmux", "has-session"]:
            inspections += 1
            return _completed(args, returncode=1 if inspections == 1 else 0)
        if args[:2] == ["tmux", "new-session"]:
            return _completed(args, returncode=1, stderr="duplicate session: codex-rektslug\n")
        if args[:2] == ["tmux", "display-message"]:
            fields = module._FIELD_SEPARATOR.join(
                ["codex-rektslug", str(repo), "codex", "0", "1"]
            )
            return _completed(args, stdout=fields + "\n")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(module, "_run_command", fake_run)

    result = module.ensure_codex_worker("rektslug")

    assert result["created"] is False
    assert result["ready"] is True


def test_ensure_codex_reports_early_process_exit(monkeypatch, tmp_path) -> None:
    module = _load_module()
    _git_repo(tmp_path / "rektslug")
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", str(tmp_path))
    monkeypatch.setattr(module, "_codex_executable", lambda: "/usr/local/bin/codex")
    created = False

    def fake_run(args: list[str], *, timeout: float = 10.0):
        nonlocal created
        if args[:2] == ["git", "-C"]:
            return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
        if args[:2] == ["tmux", "new-session"]:
            created = True
            return _completed(args)
        if args[:2] == ["tmux", "has-session"]:
            return _completed(args, returncode=1)
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(module, "_run_command", fake_run)

    with pytest.raises(module.WorkerEnsureError, match="exited during startup"):
        module.ensure_codex_worker("rektslug")
    assert created is True


def test_ensure_codex_reports_creation_failure_without_atomic_winner(
    monkeypatch, tmp_path
) -> None:
    module = _load_module()
    _git_repo(tmp_path / "rektslug")
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", str(tmp_path))
    monkeypatch.setattr(module, "_codex_executable", lambda: "/usr/local/bin/codex")

    def fake_run(args: list[str], *, timeout: float = 10.0):
        if args[:2] == ["git", "-C"]:
            return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
        if args[:2] == ["tmux", "new-session"]:
            return _completed(args, returncode=1, stderr="tmux server unavailable\n")
        if args[:2] == ["tmux", "has-session"]:
            return _completed(args, returncode=1)
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(module, "_run_command", fake_run)

    with pytest.raises(module.WorkerEnsureError, match="tmux server unavailable"):
        module.ensure_codex_worker("rektslug")


def test_ensure_codex_waits_for_exec_transition(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "rektslug")
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", str(tmp_path))
    monkeypatch.setattr(module, "_codex_executable", lambda: "/usr/local/bin/codex")
    monkeypatch.setattr(module.time, "sleep", lambda _delay: None)
    created = False
    displays = 0

    def fake_run(args: list[str], *, timeout: float = 10.0):
        nonlocal created, displays
        if args[:2] == ["git", "-C"]:
            return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
        if args[:2] == ["tmux", "new-session"]:
            created = True
            return _completed(args)
        if args[:2] == ["tmux", "has-session"]:
            return _completed(args, returncode=0 if created else 1)
        if args[:2] == ["tmux", "display-message"]:
            displays += 1
            command = "bash" if displays == 1 else "codex"
            fields = module._FIELD_SEPARATOR.join(
                ["codex-rektslug", str(repo), command, "0", "1"]
            )
            return _completed(args, stdout=fields + "\n")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(module, "_run_command", fake_run)

    result = module.ensure_codex_worker("rektslug")

    assert result["created"] is True
    assert displays == 2


def test_ensure_codex_retries_transient_post_create_repo_path(monkeypatch, tmp_path) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "progressive-deploy")
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", str(tmp_path))
    monkeypatch.setattr(module, "_codex_executable", lambda: "/usr/local/bin/codex")
    monkeypatch.setattr(module.time, "sleep", lambda _delay: None)
    created = False
    displays = 0

    def fake_run(args: list[str], *, timeout: float = 10.0):
        nonlocal created, displays
        if args[:2] == ["git", "-C"]:
            return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
        if args[:2] == ["tmux", "new-session"]:
            created = True
            return _completed(args)
        if args[:2] == ["tmux", "has-session"]:
            return _completed(args, returncode=0 if created else 1)
        if args[:2] == ["tmux", "display-message"]:
            displays += 1
            pane_path = "/home/sam" if displays == 1 else str(repo)
            fields = module._FIELD_SEPARATOR.join(
                ["codex-progressive-deploy", pane_path, "codex", "0", "1"]
            )
            return _completed(args, stdout=fields + "\n")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(module, "_run_command", fake_run)

    result = module.ensure_codex_worker("progressive-deploy")

    assert result == {
        "session": "codex-progressive-deploy",
        "repo": str(repo),
        "created": True,
        "ready": True,
    }
    assert displays == 2


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (("/other/repo", "codex", "0", "1"), "different repository"),
        (("{repo}", "bash", "0", "1"), "not Codex"),
        (("{repo}", "codex", "0", "2"), "single-pane"),
    ],
)
def test_ensure_codex_fails_closed_on_session_collision(
    monkeypatch, tmp_path, metadata, message
) -> None:
    module = _load_module()
    repo = _git_repo(tmp_path / "rektslug")
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", str(tmp_path))
    path, command, dead, panes = metadata
    path = path.format(repo=repo)

    def fake_run(args: list[str], *, timeout: float = 10.0):
        if args[:2] == ["git", "-C"]:
            return subprocess.run(args, check=False, capture_output=True, text=True, timeout=timeout)
        if args[:2] == ["tmux", "has-session"]:
            return _completed(args)
        fields = module._FIELD_SEPARATOR.join(
            ["codex-rektslug", path, command, dead, panes]
        )
        return _completed(args, stdout=fields + "\n")

    monkeypatch.setattr(module, "_run_command", fake_run)

    with pytest.raises(module.WorkerEnsureError, match=message):
        module.ensure_codex_worker("rektslug")


def test_ensure_codex_rejects_expected_session_mismatch_before_tmux(monkeypatch, tmp_path) -> None:
    module = _load_module()
    _git_repo(tmp_path / "rektslug")
    monkeypatch.setenv("MESH_LIVE_REPO_ROOTS", str(tmp_path))
    tmux_called = False
    real_run = module._run_command

    def fake_run(args: list[str], *, timeout: float = 10.0):
        nonlocal tmux_called
        if args[0] == "tmux":
            tmux_called = True
        return real_run(args, timeout=timeout)

    monkeypatch.setattr(module, "_run_command", fake_run)

    with pytest.raises(module.WorkerEnsureError, match="does not match"):
        module.ensure_codex_worker("rektslug", expected_session="codex-other")
    assert tmux_called is False


def test_main_emits_json_and_bounded_error(monkeypatch, capsys) -> None:
    module = _load_module()
    result = {
        "session": "codex-rektslug",
        "repo": "/data/sata/1TB/rektslug",
        "created": False,
        "ready": True,
    }
    monkeypatch.setattr(module, "ensure_codex_worker", lambda *_args, **_kwargs: result)

    assert module.main(["rektslug", "--json"]) == 0
    assert '"session": "codex-rektslug"' in capsys.readouterr().out

    def fail(*_args, **_kwargs):
        raise module.WorkerEnsureError("bounded failure")

    monkeypatch.setattr(module, "ensure_codex_worker", fail)
    assert module.main(["rektslug"]) == 2
    assert "Error: bounded failure" in capsys.readouterr().err


def test_main_dispatches_antigravity_provider(monkeypatch, capsys) -> None:
    module = _load_module()
    result = {
        "session": "antigravity-rektslug",
        "repo": "/data/sata/1TB/rektslug",
        "created": False,
        "ready": True,
    }
    monkeypatch.setattr(
        module, "ensure_antigravity_worker", lambda *_args, **_kwargs: result
    )

    assert module.main(["rektslug", "--provider", "antigravity"]) == 0
    assert "[mesh live ensure-antigravity]" in capsys.readouterr().out
