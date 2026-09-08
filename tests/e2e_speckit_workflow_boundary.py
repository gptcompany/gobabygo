"""Opt-in installed Spec Kit boundary characterization; no AI, tmux or network.

Run with the pinned specify-cli environment's Python:
  python tests/e2e_speckit_workflow_boundary.py --run

These tests deliberately assert observed replay hazards, not production safety.
All workflow output and state stays in temporary directories.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import io
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from specify_cli.workflows.engine import RunState, WorkflowDefinition, WorkflowEngine
from specify_cli.workflows.steps.shell import ShellStep


def competing_resume(root: str, run_id: str, barrier, results) -> None:
    original = RunState.load

    def synchronized_load(*args, **kwargs):
        state = original(*args, **kwargs)
        # Force both real processes to read PAUSED before either saves RUNNING.
        barrier.wait(timeout=15)
        return state

    try:
        with patch.object(RunState, "load", side_effect=synchronized_load):
            state = WorkflowEngine(Path(root)).resume(run_id, {"verdict": "approve"})
        results.put({"status": state.status.value})
    except Exception as exc:
        results.put({"error": type(exc).__name__ + ": " + str(exc)})


def marker(step_id: str) -> dict:
    # Only fixed identifiers authored in this test reach the trusted shell.
    if step_id not in {"before", "after", "effect"}:
        raise ValueError("unexpected fixture marker")
    return {"id": step_id, "type": "shell", "run": f"printf '{step_id}\\n' >> effects"}


def gate() -> dict:
    return {"id": "approval", "type": "gate", "message": "Fixture approval",
            "options": ["approve", "reject"], "on_reject": "abort",
            "verdict_input": "verdict"}


def crash_after_effect(root: str) -> None:
    execute = ShellStep.execute

    def exit_after_effect(instance, config, context):
        execute(instance, config, context)
        os._exit(91)

    definition = WorkflowDefinition({
        "workflow": {"id": "crash-fixture", "name": "Crash fixture"},
        "steps": [marker("effect")],
    })
    engine = WorkflowEngine(Path(root))
    if engine.validate(definition):
        raise ValueError("invalid crash fixture")
    with patch.object(ShellStep, "execute", exit_after_effect):
        engine.execute(definition, run_id="crash-fixture")


class WorkflowBoundary(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="mesh-workflow-boundary-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.engine = WorkflowEngine(self.root)
        self.stdin = patch("sys.stdin", io.StringIO(""))
        self.stdin.start()
        self.addCleanup(self.stdin.stop)

    def definition(self, steps):
        definition = WorkflowDefinition({
            "schema_version": "1.0",
            "workflow": {"id": "mesh-boundary", "name": "Mesh boundary fixture"},
            "inputs": {"verdict": {"type": "string", "default": ""}},
            "steps": steps,
        })
        self.assertEqual(self.engine.validate(definition), [])
        return definition

    def effects(self):
        path = self.root / "effects"
        return path.read_text().splitlines() if path.exists() else []

    def cli(self, *args):
        result = subprocess.run(
            [sys.executable, "-c", "from specify_cli import main; main()",
             "workflow", *args],
            cwd=self.root, input="", capture_output=True, text=True, timeout=20,
            env={"PATH": os.defpath, "HOME": str(self.root), "LC_ALL": "C"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_actual_cli_local_file_run_and_resume_without_remote(self):
        definition = self.definition([marker("before"), gate(), marker("after")])
        source = self.root / "workflow.yml"
        source.write_text(yaml.safe_dump(definition.data))
        state = self.cli("run", str(source), "--json")
        self.assertEqual(state["status"], "paused")
        resumed = self.cli("resume", state["run_id"], "-i", "verdict=approve", "--json")
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(self.effects(), ["before", "after"])
        self.assertFalse((self.root / ".git").exists())

    def test_top_level_resume_preserves_completed_prefix_without_remote(self):
        state = self.engine.execute(self.definition([marker("before"), gate(), marker("after")]))
        self.assertEqual(state.status.value, "paused")
        self.assertFalse((self.root / ".git").exists())
        self.assertEqual(self.effects(), ["before"])
        resumed = self.engine.resume(state.run_id, {"verdict": "approve"})
        self.assertEqual(resumed.status.value, "completed")
        self.assertEqual(self.effects(), ["before", "after"])

    def test_nested_resume_replays_completed_side_effect(self):
        state = self.engine.execute(self.definition([
            {"id": "branch", "type": "if", "condition": True,
             "then": [marker("before"), gate(), marker("after")]},
        ]))
        self.assertEqual(state.status.value, "paused")
        self.assertEqual(self.effects(), ["before"])
        resumed = self.engine.resume(state.run_id, {"verdict": "approve"})
        self.assertEqual(resumed.status.value, "completed")
        self.assertEqual(self.effects(), ["before", "before", "after"])

    def test_rejection_aborts_without_executing_following_step(self):
        state = self.engine.execute(self.definition([gate(), marker("after")]))
        resumed = self.engine.resume(state.run_id, {"verdict": "reject"})
        self.assertEqual(resumed.status.value, "aborted")
        self.assertEqual(self.effects(), [])

    def test_failed_step_reexecutes_its_side_effect_on_resume(self):
        step = marker("effect")
        step["run"] += "; test -f permit"
        state = self.engine.execute(self.definition([step]))
        self.assertEqual(state.status.value, "failed")
        self.assertEqual(self.effects(), ["effect"])
        (self.root / "permit").touch()
        resumed = self.engine.resume(state.run_id)
        self.assertEqual(resumed.status.value, "completed")
        self.assertEqual(self.effects(), ["effect", "effect"])

    def test_interrupt_after_effect_before_result_save_replays(self):
        # Models Ctrl-C in the real effect/save window, not abrupt process death.
        execute = ShellStep.execute

        def interrupt_after_effect(instance, config, context):
            execute(instance, config, context)
            raise KeyboardInterrupt

        with patch.object(ShellStep, "execute", interrupt_after_effect):
            state = self.engine.execute(self.definition([marker("effect")]))
        self.assertEqual(state.status.value, "paused")
        self.assertEqual(self.effects(), ["effect"])
        resumed = self.engine.resume(state.run_id)
        self.assertEqual(resumed.status.value, "completed")
        self.assertEqual(self.effects(), ["effect", "effect"])

    def test_two_process_resumes_can_both_execute_same_step(self):
        state = self.engine.execute(self.definition([gate(), marker("after")]))
        self.assertEqual(state.status.value, "paused")
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        results = context.Queue()
        processes = [context.Process(target=competing_resume,
                                     args=(str(self.root), state.run_id, barrier, results))
                     for _ in range(2)]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=25)
                self.assertFalse(process.is_alive(), "fixture resume timed out")
                self.assertEqual(process.exitcode, 0)
            outcomes = [results.get(timeout=2) for _ in processes]
            self.assertFalse(any("error" in result for result in outcomes),
                             f"race fixture did not complete its schedule: {outcomes}")
            self.assertEqual(outcomes, [{"status": "completed"}] * 2)
            self.assertEqual(self.effects(), ["after", "after"])
            # Well-formed JSON alone cannot establish single execution ownership.
            saved = self.root / ".specify/workflows/runs" / state.run_id / "state.json"
            self.assertEqual(json.loads(saved.read_text())["status"], "completed")
        finally:
            for process in processes:
                if process.pid is not None:
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=5)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=5)
            results.close()
            results.join_thread()

    def test_process_death_leaves_running_state_and_resume_refuses(self):
        process = multiprocessing.get_context("spawn").Process(
            target=crash_after_effect, args=(str(self.root),))
        try:
            process.start()
            process.join(timeout=20)
            self.assertFalse(process.is_alive(), "fixture crash timed out")
            self.assertEqual(process.exitcode, 91,
                             "fixture did not reach injected hard exit; inspect child stderr")
            self.assertEqual(self.effects(), ["effect"])
            state = RunState.load("crash-fixture", self.root)
            self.assertEqual(state.status.value, "running")
            with self.assertRaisesRegex(ValueError, "Cannot resume"):
                self.engine.resume("crash-fixture")
            self.assertEqual(self.effects(), ["effect"])
        finally:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True)
    parser.parse_args()
    version = importlib.metadata.version("specify-cli")
    if version != "1.0.3":
        parser.error(f"characterization requires specify-cli 1.0.3, found {version}")
    unittest.main(argv=[__file__], verbosity=2)
