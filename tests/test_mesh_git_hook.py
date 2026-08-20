from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DISPATCHER = ROOT / "scripts" / "mesh_global_pre_push.sh"
INSTALLER = ROOT / "scripts" / "install-mesh-git-hook.sh"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _install_review(home: Path, output: Path, *, exit_code: int = 0) -> None:
    hooks = home / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    review = hooks / "pre-push-review.py"
    review.write_text(
        "import os, pathlib, sys\n"
        "pathlib.Path(os.environ['GLOBAL_OUT']).write_text(repr(sys.argv[1:]) + '\\n' + sys.stdin.read())\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )


def test_dispatcher_replays_arguments_and_stdin_to_global_and_repo_hooks(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    global_output = tmp_path / "global.out"
    repo_output = tmp_path / "repo.out"
    _install_review(home, global_output)
    local_hook = repo / ".githooks" / "pre-push"
    local_hook.parent.mkdir()
    local_hook.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$REPO_OUT\"\ncat >> \"$REPO_OUT\"\n",
        encoding="utf-8",
    )
    local_hook.chmod(0o755)
    hook_input = "refs/heads/main abc refs/heads/main def\n"

    proc = subprocess.run(
        [str(DISPATCHER), "origin", "git@example.test:repo.git"],
        cwd=repo,
        env={
            **os.environ,
            "HOME": str(home),
            "GLOBAL_OUT": str(global_output),
            "REPO_OUT": str(repo_output),
        },
        input=hook_input,
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert "origin" in global_output.read_text(encoding="utf-8")
    assert hook_input in global_output.read_text(encoding="utf-8")
    assert repo_output.read_text(encoding="utf-8").splitlines() == [
        "origin",
        "git@example.test:repo.git",
        hook_input.strip(),
    ]


def test_dispatcher_stops_before_repo_hook_when_global_review_fails(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    global_output = tmp_path / "global.out"
    repo_output = tmp_path / "repo.out"
    _install_review(home, global_output, exit_code=9)
    local_hook = repo / ".githooks" / "pre-push"
    local_hook.parent.mkdir()
    local_hook.write_text(
        "#!/bin/sh\ntouch \"$REPO_OUT\"\n",
        encoding="utf-8",
    )
    local_hook.chmod(0o755)

    proc = subprocess.run(
        [str(DISPATCHER), "origin", "url"],
        cwd=repo,
        env={
            **os.environ,
            "HOME": str(home),
            "GLOBAL_OUT": str(global_output),
            "REPO_OUT": str(repo_output),
        },
        input="refs/heads/main abc refs/heads/main def\n",
        check=False,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 9
    assert global_output.is_file()
    assert not repo_output.exists()


def test_installer_migrates_known_shim_and_refuses_unknown_hook(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = home / ".claude" / "hooks" / "pre-push"
    target.parent.mkdir(parents=True)
    target.write_text(
        "#!/bin/bash\n# legacy\npython3 ~/.claude/hooks/pre-push-review.py\n",
        encoding="utf-8",
    )
    env = {**os.environ, "HOME": str(home)}

    plan = subprocess.run(
        [str(INSTALLER), "--target", str(target)],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert plan.returncode == 0, plan.stderr
    assert "Plan only" in plan.stdout
    assert "legacy" in target.read_text(encoding="utf-8")

    applied = subprocess.run(
        [str(INSTALLER), "--target", str(target), "--apply"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert applied.returncode == 0, applied.stderr
    assert target.read_bytes() == DISPATCHER.read_bytes()
    assert target.stat().st_mode & 0o777 == 0o755
    assert (
        subprocess.run(
            ["git", "config", "--global", "--get", "core.hooksPath"],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == str(target.parent)
    )

    target.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
    refused = subprocess.run(
        [str(INSTALLER), "--target", str(target), "--apply"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert refused.returncode == 2
    assert "unknown hook" in refused.stderr
