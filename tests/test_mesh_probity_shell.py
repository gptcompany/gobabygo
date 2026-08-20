from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MESH = ROOT / "scripts" / "mesh"


def test_probity_help_describes_opt_in_and_restart() -> None:
    proc = subprocess.run(
        ["bash", str(MESH), "probity", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0
    assert "probity status [repo-path]" in proc.stdout
    assert "plan-only unless --apply" in proc.stdout
    assert "without a Probity config remain unaffected" in proc.stdout
    assert "Existing Codex sessions must be" in proc.stdout


def test_probity_subcommands_forward_exact_arguments(tmp_path) -> None:
    capture = tmp_path / "args"
    fake_python = tmp_path / "python"
    fake_python.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE_FILE\"\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)

    proc = subprocess.run(
        ["bash", str(MESH), "probity", "status", "/tmp/example repo", "--json"],
        cwd=ROOT,
        env={
            **os.environ,
            "CAPTURE_FILE": str(capture),
            "MESH_PROBITY_PYTHON": str(fake_python),
        },
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0
    assert capture.read_text(encoding="utf-8").splitlines() == [
        str(ROOT / "scripts" / "mesh_probity_cli.py"),
        "status",
        "/tmp/example repo",
        "--json",
    ]


def test_unknown_probity_subcommand_fails_closed() -> None:
    proc = subprocess.run(
        ["bash", str(MESH), "probity", "enable-everywhere"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 2
    assert "unsupported probity subcommand" in proc.stderr
