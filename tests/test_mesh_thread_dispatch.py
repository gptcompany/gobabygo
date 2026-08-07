from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MESH = ROOT / "scripts" / "mesh"


def _run_with_fake_uv(tmp_path: Path, *args: str) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "uv-args.txt"
    uv_path = bin_dir / "uv"
    uv_path.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$@" >"$MESH_TEST_UV_LOG"\n',
        encoding="utf-8",
    )
    uv_path.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            "MESH_AUTH_TOKEN": "test-token",
            "MESH_MAC_ROUTER_TUNNEL": "0",
            "MESH_ROUTER_URL": "http://127.0.0.1:8780",
            "MESH_TEST_UV_LOG": str(log_path),
        }
    )

    proc = subprocess.run(
        [str(MESH), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    return log_path.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    ("mesh_args", "forwarded"),
    [
        (("thread", "create", "--name", "live-plan"), ["thread", "create", "--name", "live-plan"]),
        (
            ("thread", "add-step", "--thread", "live-plan", "--title", "Task", "--step-index", "0"),
            ["thread", "add-step", "--thread", "live-plan", "--title", "Task", "--step-index", "0"],
        ),
        (("thread", "context", "live-plan"), ["thread", "context", "live-plan"]),
        (("thread", "handoff", "live-plan", "0"), ["thread", "handoff", "live-plan", "0"]),
    ],
)
def test_mesh_forwards_router_thread_subcommands(
    tmp_path: Path,
    mesh_args: tuple[str, ...],
    forwarded: list[str],
) -> None:
    argv = _run_with_fake_uv(tmp_path, *mesh_args)

    assert argv == ["run", "--", "python", "-m", "src.meshctl", *forwarded]


def test_mesh_preserves_thread_name_as_status_shorthand(tmp_path: Path) -> None:
    argv = _run_with_fake_uv(tmp_path, "thread", "live-plan")

    assert argv == [
        "run",
        "--",
        "python",
        "-m",
        "src.meshctl",
        "thread",
        "status",
        "live-plan",
    ]
