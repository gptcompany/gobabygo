from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MESH = ROOT / "scripts" / "mesh"


def test_runtime_status_is_available_without_iterm(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    proc = subprocess.run(
        ["bash", str(MESH), "speckit", "status", str(repo), "--json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "mesh.speckit.status.v1"
    assert payload["project"]["state"] == "missing"
    assert "iterm" not in proc.stderr.lower()


def test_runtime_subcommands_forward_exact_arguments(tmp_path) -> None:
    capture = tmp_path / "args.json"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE_FILE\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    proc = subprocess.run(
        ["bash", str(MESH), "speckit", "capabilities", "/tmp/example repo", "--json"],
        cwd=ROOT,
        env={
            **os.environ,
            "CAPTURE_FILE": str(capture),
            "MESH_SPECKIT_PYTHON": str(fake_python),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    args = capture.read_text(encoding="utf-8").splitlines()
    assert args[0] == str(ROOT / "scripts" / "mesh_speckit_cli.py")
    assert args[1:] == ["capabilities", "/tmp/example repo", "--json"]


def test_speckit_help_separates_runtime_and_legacy_commands() -> None:
    proc = subprocess.run(
        ["bash", str(MESH), "speckit", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0
    assert "speckit status [repo-path]" in proc.stdout
    assert "speckit capabilities [repo-path]" in proc.stdout
    assert "speckit context <repo-path>" in proc.stdout
    assert "speckit manual-actions <feature-dir|tasks.md>" in proc.stdout
    assert "speckit update-check" in proc.stdout
    assert "speckit install <version>" in proc.stdout
    assert "speckit project init" in proc.stdout
    assert "speckit project migrate" in proc.stdout
    assert "speckit project upgrade" in proc.stdout
    assert "speckit github init" in proc.stdout
    assert "speckit github plan" in proc.stdout
    assert "speckit github check" in proc.stdout
    assert "speckit github install-caller" in proc.stdout
    assert "speckit review <status|check|init|open|record|timeout|correction|candidate|budget|decide>" in proc.stdout
    assert "writes are intentionally restricted" in proc.stdout
    assert "Legacy iTerm2 run options" in proc.stdout


def test_manual_actions_subcommand_forwards_exact_arguments(tmp_path) -> None:
    capture = tmp_path / "args.json"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE_FILE\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    proc = subprocess.run(
        ["bash", str(MESH), "speckit", "manual-actions", "/tmp/example repo", "--all", "--json"],
        cwd=ROOT,
        env={
            **os.environ,
            "CAPTURE_FILE": str(capture),
            "MESH_SPECKIT_PYTHON": str(fake_python),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        str(ROOT / "scripts" / "mesh_speckit_cli.py"),
        "manual-actions",
        "/tmp/example repo",
        "--all",
        "--json",
    ]


def test_manual_actions_real_cli_e2e_on_fictitious_feature(tmp_path) -> None:
    feature = tmp_path / "specs" / "001-release-gate"
    feature.mkdir(parents=True)
    (feature / "tasks.md").write_text(
        """# Fictitious release gate

- [ ] **DEC-1** [D] Enable the fictitious money path?
- [ ] **T001** Ship the fictitious release
  *Blocked by*: DEC-1
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["bash", str(MESH), "speckit", "manual-actions", str(tmp_path), "--all", "--json"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "mesh.speckit.manual-actions.v1"
    assert payload["count"] == 1
    assert payload["actions"][0]["id"] == "DEC-1"
    assert payload["actions"][0]["blocked_tasks"] == ["T001"]


def test_github_ledger_subcommand_forwards_exact_arguments(tmp_path) -> None:
    capture = tmp_path / "args.json"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE_FILE\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    proc = subprocess.run(
        [
            "bash",
            str(MESH),
            "speckit",
            "github",
            "check",
            "/tmp/example feature",
            "--json",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "CAPTURE_FILE": str(capture),
            "MESH_SPECKIT_PYTHON": str(fake_python),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        str(ROOT / "scripts" / "mesh_speckit_github.py"),
        "check",
        "/tmp/example feature",
        "--json",
    ]


def test_review_ledger_subcommand_forwards_exact_arguments(tmp_path) -> None:
    capture = tmp_path / "args.json"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE_FILE\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    proc = subprocess.run(
        [
            "bash",
            str(MESH),
            "speckit",
            "review",
            "status",
            "/tmp/example repo",
            "specs/001-feature",
            "T001",
            "--json",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "CAPTURE_FILE": str(capture),
            "MESH_SPECKIT_PYTHON": str(fake_python),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        str(ROOT / "scripts" / "mesh_speckit_review.py"),
        "status",
        "/tmp/example repo",
        "specs/001-feature",
        "T001",
        "--json",
    ]


def test_context_subcommand_forwards_exact_arguments(tmp_path) -> None:
    capture = tmp_path / "args.json"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE_FILE\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    proc = subprocess.run(
        [
            "bash",
            str(MESH),
            "speckit",
            "context",
            "/tmp/example repo",
            "--phase",
            "plan",
            "--feature-dir",
            "specs/001-feature",
            "--artifact",
            "spec.md",
            "--role",
            "writer",
        ],
        cwd=ROOT,
        env={
            **os.environ,
            "CAPTURE_FILE": str(capture),
            "MESH_SPECKIT_PYTHON": str(fake_python),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert capture.read_text(encoding="utf-8").splitlines()[1:] == [
        "context",
        "/tmp/example repo",
        "--phase",
        "plan",
        "--feature-dir",
        "specs/001-feature",
        "--artifact",
        "spec.md",
        "--role",
        "writer",
    ]


def test_unknown_speckit_command_still_fails_closed() -> None:
    proc = subprocess.run(
        ["bash", str(MESH), "speckit", "upgrade-now"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 2
    assert "unsupported speckit subcommand 'upgrade-now'" in proc.stderr


def test_legacy_speckit_print_plan_uses_active_provider_defaults(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    proc = subprocess.run(
        [
            "bash",
            str(MESH),
            "speckit",
            "run",
            str(repo),
            "--feature",
            "provider migration",
            "--print-command",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert "--boss-cmd claude" in proc.stdout
    assert "--president-cmd codex" in proc.stdout
    assert "--worker-cmd agy" in proc.stdout
    assert "--reviewer-cmd codex" in proc.stdout
    assert "gemini" not in proc.stdout.lower()


def test_legacy_speckit_rejects_retired_gemini_team(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    proc = subprocess.run(
        [
            "bash",
            str(MESH),
            "speckit",
            "run",
            str(repo),
            "--feature",
            "provider migration",
            "--team",
            "local-codex-gemini",
            "--print-command",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 2
    assert "unsupported speckit team 'local-codex-gemini'" in proc.stderr
