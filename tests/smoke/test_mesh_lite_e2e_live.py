#!/usr/bin/env python3
"""E2E live verification script for mesh-lite."""

import json
import os
import subprocess
import time
from pathlib import Path

def _project_slug(project_path: str) -> str:
    return project_path.replace("/", "-").replace(".", "-")

def main():
    repo_path = os.getcwd()
    print(f"Running E2E for repo: {repo_path}")

    # 1. Open layout
    print("1. Opening layout...")
    subprocess.run(["./scripts/mesh", "ui", repo_path], check=True)
    time.sleep(3)  # Wait for panes to open

    try:
        # 2. Discover
        print("2. Discovering...")
        subprocess.run(["./scripts/mesh", "lite", "discover", "--project", repo_path], check=True)

        # 3. Status
        print("3. Status...")
        status_proc = subprocess.run(
            ["./scripts/mesh", "lite", "status", "--project", repo_path],
            check=True, capture_output=True, text=True
        )
        print(status_proc.stdout)

        # Find boss session ID
        session_id = None
        lines = status_proc.stdout.splitlines()
        for i, line in enumerate(lines):
            if "boss" in line and "[" in line:
                for j in range(i+1, i+5):
                    if j < len(lines) and "session_id:" in lines[j]:
                        session_id = lines[j].split("session_id:")[1].strip()
                        break
                break

        if not session_id or session_id == "-":
            raise RuntimeError("Could not find boss session ID")
        
        print(f"Found boss session_id: {session_id}")

        # 4. Generate mock transcript
        print("4. Generating mock transcript...")
        mock_dir = Path.home() / ".claude" / "projects" / _project_slug(repo_path)
        mock_dir.mkdir(parents=True, exist_ok=True)
        mock_file = mock_dir / f"{session_id}.jsonl"
        
        with mock_file.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"cwd": repo_path, "sessionId": session_id}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": "E2E_PAYLOAD_MARKER_SUCCESS"}}) + "\n")

        # Re-discover to bind
        print("Re-discover to bind...")
        subprocess.run(["./scripts/mesh", "lite", "discover", "--project", repo_path], check=True)

        # 5. Relay
        print("5. Relaying...")
        subprocess.run(["./scripts/mesh", "lite", "relay-last", "--project", repo_path, "boss", "president"], check=True)
        time.sleep(1)

        # 6. Verify
        print("6. Verifying target pane...")
        dump_proc = subprocess.run(
            ["./scripts/mesh", "term", "dump", repo_path, "president"],
            check=True, capture_output=True, text=True
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
        subprocess.run(["./scripts/mesh", "ui", "close", repo_path], check=True)

if __name__ == "__main__":
    main()