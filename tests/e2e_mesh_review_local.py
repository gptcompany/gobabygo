"""Opt-in real Codex/tmux + Claude review exercise, isolated from operator sessions.

Run: .venv/bin/python tests/e2e_mesh_review_local.py --run
Requires logged-in Codex and Claude. Artifacts remain in the printed temporary repo.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import mesh_speckit_review as review
from scripts import mesh_live_cli as live


def run(args, *, cwd=None, timeout=30, check=True, **kwargs):
    return subprocess.run(args, cwd=cwd, timeout=timeout, check=check,
                          capture_output=True, text=True, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.add_argument("--codex-executable", default="codex",
                        help="Native Codex binary, not a Node launcher.")
    options = parser.parse_args()
    base = Path(tempfile.mkdtemp(prefix="mesh-review-e2e-", dir="/tmp")).resolve()
    repo = base / "repo"
    repo.mkdir()
    os.environ["TMUX_TMPDIR"] = str(base)
    os.environ.pop("TMUX", None)
    live.DEFAULT_CODEX_RECOVERY_STATE_FILE = str(base / "codex-recovery.json")
    session = "mesh-review-e2e-codex"
    print(f"ARTIFACTS={base}", flush=True)
    run(["git", "init", "-q", str(repo)])
    (repo / "adder.py").write_text("def add(a, b):\n    return a - b\n")
    (repo / "test_adder.py").write_text(
        "import unittest\nfrom adder import add\n\n"
        "class Addition(unittest.TestCase):\n"
        "    def test_positive(self):\n        self.assertEqual(add(2, 3), 5)\n"
        "    def test_negative(self):\n        self.assertEqual(add(-2, -3), -5)\n"
    )
    run(["git", "add", "adder.py", "test_adder.py"], cwd=repo)
    run(["git", "-c", "user.name=Mesh E2E", "-c", "user.email=mesh-e2e@example.invalid",
         "-c", "core.hooksPath=/dev/null", "commit", "-qm", "E2E seed"], cwd=repo)
    scope = "commit:" + run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
    feature = repo / "specs" / "001-addition"
    feature.mkdir(parents=True)
    (feature / "github-ledger.json").write_text(json.dumps({
        "schema": "mesh.speckit.github-ledger.v1", "feature_id": "addition-e2e",
        "repository": "example/addition", "enabled": True,
    }))
    (feature / "tasks.md").write_text("- [ ] T001 Correct integer addition\n")
    common = dict(scope=scope, writer_session=session,
                  invariants=["integer addition"], mutation_budget=1)
    review.initialize_task(repo, feature, "T001", **common, expected_revision=0)
    red = run([sys.executable, "-m", "unittest", "-v"], cwd=repo, check=False)
    assert red.returncode != 0
    report = feature / "initial-failure.md"
    report.write_text(red.stdout + red.stderr)
    review.open_review(repo, feature, "T001", level="RELEASE", scope=scope,
                       reviewer_session="mesh-e2e-observer", delegation_id="initial-review",
                       invariant="", expected_revision=1)
    review.record_review(repo, feature, "T001", verdict="CHANGES_REQUIRED",
                         evidence_file=report, blocking_high=0, blocking_medium=1,
                         invalidates_safety=False, mutations_run=0, expected_revision=2)
    review.decide_exhausted(repo, feature, "T001", decision="REPLAN",
                           reason="Use arithmetic addition instead of subtraction", expected_revision=3)
    try:
        review.initialize_task(repo, feature, "T001", **common, expected_revision=4)
    except review.ReviewLedgerError:
        print("MISSING_REPLAN_REJECTED", flush=True)
    else:
        raise AssertionError("missing replan accepted")
    plan = feature / "replan.json"
    plan.write_text(json.dumps({
        "task_key": "example/addition:addition-e2e:T001", "previous_cycle": 1,
        "failure_evidence": "initial-failure.md", "approach_change": "Use arithmetic +",
        "acceptance_criteria": "Both integer addition tests pass unchanged",
        "finding_disposition": "Carry incorrect addition into the correction and release review",
    }))
    review.initialize_task(repo, feature, "T001", **common, replan_file=plan, expected_revision=4)
    # A new invariant review evaluates the revised plan without repeating the old RELEASE scope.
    review.open_review(repo, feature, "T001", level="INVARIANT", scope=scope,
                       reviewer_session="mesh-e2e-observer", delegation_id="plan-review",
                       invariant="integer addition", expected_revision=5)
    review.record_review(repo, feature, "T001", verdict="CHANGES_REQUIRED",
                         evidence_file=report, blocking_high=0, blocking_medium=1,
                         invalidates_safety=False, mutations_run=0, expected_revision=6)
    delegation = "DLG-MESH-E2E-ADDITION"
    review.open_correction(repo, feature, "T001", delegation_id=delegation, expected_revision=7)
    args = [options.codex_executable, "--cd", str(repo), "--sandbox", "workspace-write", "-a", "never",
            "--no-alt-screen", "-c", "check_for_update_on_startup=false",
            "-c", f'projects.{json.dumps(str(repo))}.trust_level="trusted"']
    started = False
    try:
        run(["tmux", "new-session", "-d", "-s", session, "-x", "200", "-y", "50",
             "-c", str(repo), "exec " + shlex.join(args)])
        started = True
        client = live.LiveClient(live.LiveEndpoint("", True, (getpass.getuser(),)))
        deadline = time.monotonic() + 90
        selected = None
        trust_confirmed = False
        while time.monotonic() < deadline:
            sessions, _ = client.discover()
            selected = next((item for item in sessions if item.name == session), None)
            if selected:
                captured, _ = client.capture([selected], 50)
                screen = captured[0].output
                if (not trust_confirmed and selected.pane_command == "codex"
                        and str(repo) in screen
                        and "Do you trust the contents of this directory?" in screen
                        and "1. Yes, continue" in screen and "2. No, quit" in screen):
                    # This repo was created above; this is never an operator's session.
                    run(["tmux", "send-keys", "-t", session, "Enter"])
                    trust_confirmed = True
                    continue
                if live.codex_screen_is_ready_for_delegation(captured[0].output):
                    break
            time.sleep(2)
        else:
            raise RuntimeError("Codex did not reach an idle composer within 90 seconds")
        message = (f"{delegation} example/addition:addition-e2e:T001 Fix only adder.py: add must add "
                   "integers instead of subtracting. Run python3 -m unittest -v before and after. "
                   "Do not change tests or specs, commit, push, or start other agents. "
                   f"When done report WORKER_DONE {delegation}.")
        result = review.dispatch_correction(repo, feature, "T001", delegation_id=delegation,
                                            message=message, worker_repo=repo, expected_revision=8)
        print("DISPATCH=" + json.dumps(result), flush=True)
        if result["submission"] == "unknown":
            time.sleep(2)
            # Existing one-shot recovery only, never another paste.
            try:
                result = client.recover_codex_submit(selected, delegation)
                print("RECOVERY=" + json.dumps(result), flush=True)
            except live.LiveReadError:
                print("RECOVERY_NOT_APPLICABLE; waiting for observed result", flush=True)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            captured, _ = client.capture([selected], 50)
            output = captured[0].output
            if live.codex_composer_has_delegation(output, delegation):
                try:
                    recovery = client.recover_codex_submit(selected, delegation)
                    print("GUARDED_RECOVERY=" + json.dumps(recovery), flush=True)
                except live.LiveReadError:
                    # The helper recaptures and permits at most one actual Enter.
                    # A refused preflight is not evidence of submission or completion.
                    pass
            if (f"WORKER_DONE {delegation}" in output
                    and live.codex_screen_is_ready_for_delegation(output)):
                green = run([sys.executable, "-m", "unittest", "-v"], cwd=repo, check=False)
                if green.returncode == 0:
                    break
            time.sleep(3)
        else:
            raise RuntimeError("worker did not finish the bounded objective within 180 seconds")
        try:
            review.dispatch_correction(repo, feature, "T001", delegation_id=delegation,
                                        message=message, worker_repo=repo, expected_revision=10)
        except review.ReviewLedgerError:
            print("DUPLICATE_DISPATCH_REJECTED", flush=True)
        else:
            raise AssertionError("duplicate dispatch accepted")
        diff = run(["git", "diff", "--", "adder.py", "test_adder.py"], cwd=repo).stdout
        assert not run(["git", "diff", "--", "test_adder.py"], cwd=repo).stdout
        assert (feature / "tasks.md").read_text() == "- [ ] T001 Correct integer addition\n"
        corrected = "diff-sha256:" + hashlib.sha256(diff.encode()).hexdigest()
        review_prompt = (
            "Read-only independent review. A scratch repo has add(a,b)=a-b; only the following diff "
            "was authorized. Acceptance: add returns integer sum, existing tests unchanged. "
            "Judge DELTA and RELEASE for this tiny objective. If correct output exactly "
            "REVIEW_VERDICT: PASS then brief reasoning; otherwise CHANGES_REQUIRED with findings. "
            "No tools, do not claim tests run by you.\nDIFF:\n" + diff + "\nTESTS:\n" +
            (repo / "test_adder.py").read_text() + "\nOBSERVED TEST OUTPUT:\n" + green.stderr
        )
        verdict = run(["claude", "-p", "--tools", "", "--strict-mcp-config", "--mcp-config",
                       '{"mcpServers":{}}', "--setting-sources", "", "--no-session-persistence"],
                      input=review_prompt, timeout=180).stdout
        assert verdict.strip().startswith("REVIEW_VERDICT: PASS"), verdict
        report = feature / "claude-review.md"
        report.write_text(verdict)
        assert run(["git", "diff", "--", "adder.py", "test_adder.py"], cwd=repo).stdout == diff
        review.open_review(repo, feature, "T001", level="DELTA", scope=corrected,
                           reviewer_session="mesh-e2e-claude", delegation_id="delta-review",
                           invariant="", expected_revision=10)
        review.record_review(repo, feature, "T001", verdict="PASS", evidence_file=report,
                             blocking_high=0, blocking_medium=0, invalidates_safety=False,
                             mutations_run=0, expected_revision=11)
        review.update_candidate(repo, feature, "T001", scope=corrected, expected_revision=12)
        review.open_review(repo, feature, "T001", level="RELEASE", scope=corrected,
                           reviewer_session="mesh-e2e-claude", delegation_id="release-review",
                           invariant="", expected_revision=13)
        review.record_review(repo, feature, "T001", verdict="PASS", evidence_file=report,
                             blocking_high=0, blocking_medium=0, invalidates_safety=False,
                             mutations_run=0, expected_revision=14)
        result = review.complete_task(repo, feature, "T001", scope=corrected, expected_revision=15)
        print("E2E_PASS=" + json.dumps(result), flush=True)
    finally:
        if started:
            run(["tmux", "kill-session", "-t", session], check=False)


if __name__ == "__main__":
    main()
