#!/usr/bin/env python3
"""E2E live verification script for mesh-lite."""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

def _project_slug(project_path: str) -> str:
    return project_path.replace("/", "-").replace(".", "-")

def _run_checked(args: list[str], *, attempts: int = 1, **kwargs) -> subprocess.CompletedProcess:
    last_proc: subprocess.CompletedProcess | None = None
    for attempt in range(max(1, attempts)):
        proc = subprocess.run(args, check=False, **kwargs)
        if proc.returncode == 0:
            return proc
        last_proc = proc
        if attempt + 1 < attempts:
            time.sleep(2)
    assert last_proc is not None
    raise subprocess.CalledProcessError(
        last_proc.returncode,
        args,
        output=getattr(last_proc, "stdout", None),
        stderr=getattr(last_proc, "stderr", None),
    )

def main():
    repo_path = os.getcwd()
    print(f"Running E2E for repo: {repo_path}")

    # 1. Open layout
    print("1. Opening layout...")
    ui_env = dict(os.environ)
    local_shell = 'printf "[mesh:%s] local e2e shell\\n" "${{MESH_UI_ROLE:-role}}"; exec "${{SHELL:-/bin/bash}}" -l'
    ui_env["MESH_UI_CMD_BOSS"] = local_shell
    ui_env["MESH_UI_CMD_PRESIDENT"] = local_shell
    _run_checked(["./scripts/mesh", "ui", repo_path, "--no-attach-live"], attempts=2, env=ui_env)
    time.sleep(3)  # Wait for panes to open

    try:
        with tempfile.TemporaryDirectory(prefix="mesh-lite-e2e-home-") as tmp_home_raw:
            tmp_home = Path(tmp_home_raw)
            lite_env = dict(os.environ)
            lite_env["MESH_LITE_CLAUDE_PROJECTS_DIR"] = str(tmp_home / ".claude" / "projects")
            registry_path = tmp_home / ".mesh-lite" / "registry.json"

            # 2. Discover
            print("2. Discovering...")
            _run_checked(
                ["./scripts/mesh", "lite", "--registry", str(registry_path), "discover", "--project", repo_path],
                attempts=3,
                env=lite_env,
            )

            # 3. Status
            print("3. Status...")
            status_proc = _run_checked(
                ["./scripts/mesh", "lite", "--registry", str(registry_path), "status", "--project", repo_path],
                attempts=1,
                capture_output=True,
                text=True,
                env=lite_env,
            )
            print(status_proc.stdout)

            # Find boss session ID
            session_id = None
            lines = status_proc.stdout.splitlines()
            for i, line in enumerate(lines):
                if "boss" in line and "[" in line:
                    for j in range(i + 1, i + 5):
                        if j < len(lines) and "session_id:" in lines[j]:
                            session_id = lines[j].split("session_id:")[1].strip()
                            break
                    break

            if not session_id or session_id == "-":
                raise RuntimeError("Could not find boss session ID")

            print(f"Found boss session_id: {session_id}")

            # 4. Generate isolated mock transcript
            print("4. Generating mock transcript...")
            mock_dir = Path(lite_env["MESH_LITE_CLAUDE_PROJECTS_DIR"]) / _project_slug(repo_path)
            mock_dir.mkdir(parents=True, exist_ok=True)
            mock_file = mock_dir / f"{session_id}.jsonl"

            with mock_file.open("w", encoding="utf-8") as f:
                f.write(json.dumps({"cwd": repo_path, "sessionId": session_id}) + "\n")
                f.write(
                    json.dumps(
                        {
                            "type": "assistant",
                            "message": {"role": "assistant", "content": "E2E_PAYLOAD_MARKER_SUCCESS"},
                        }
                    )
                    + "\n"
                )

            # Re-discover to bind against the isolated transcript + registry
            print("Re-discover to bind...")
            _run_checked(
                ["./scripts/mesh", "lite", "--registry", str(registry_path), "discover", "--project", repo_path],
                attempts=3,
                env=lite_env,
            )

            # 5. Relay
            print("5. Relaying...")
            _run_checked(
                [
                    "./scripts/mesh",
                    "lite",
                    "--registry",
                    str(registry_path),
                    "relay-last",
                    "--project",
                    repo_path,
                    "boss",
                    "president",
                ],
                attempts=3,
                env=lite_env,
            )
            time.sleep(1)

            # 6. Verify
            print("6. Verifying target pane...")
            dump_proc = _run_checked(
                ["./scripts/mesh", "term", "dump", repo_path, "president", "--lines", "120"],
                attempts=3,
                capture_output=True,
                text=True,
            )
            if "E2E_PAYLOAD_MARKER_SUCCESS" in dump_proc.stdout:
                print("Success: Payload found in target pane!")
            else:
                print("Failed: Payload not found in target pane!")
                print("Dump output:")
                print(dump_proc.stdout)
                raise RuntimeError("Verification failed")

    finally:
        # 7. Close
        print("7. Closing layout...")
        _run_checked(["./scripts/mesh", "term", "close", repo_path], attempts=3)

if __name__ == "__main__":
    main()
