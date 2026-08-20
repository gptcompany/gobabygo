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
    assert "speckit update-check" in proc.stdout
    assert "speckit install <version>" in proc.stdout
    assert "speckit project <init|upgrade>" in proc.stdout
    assert "Legacy iTerm2 run options" in proc.stdout


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
