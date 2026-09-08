# T002: installed workflow boundary experiment

Date: 2026-09-08. Runtime: installed Spec Kit 1.0.3, Mac Python 3.11.13.
See speckit-workflow-inventory.md for version/source provenance.

## Reproduction

```sh
/Users/sam/.local/share/uv/tools/specify-cli/bin/python3 \
  tests/e2e_speckit_workflow_boundary.py --run
```

The opt-in harness requires the specify-cli Python environment, refuses other
versions, and uses unittest from the standard library. It does not install tools.
Eight tests passed. Passing means the expected behavior was reproduced, including
unsafe replay cases; it does not mean native dispatch is production-safe.

## Observations

| Case | Observed result |
| --- | --- |
| Actual CLI, local YAML, no Git/remote | Run pauses; resume approves and finishes |
| Top-level gate | Completed prefix runs once |
| Rejected gate | Following shell step never runs |
| Nested gate | Completed prefix inside the parent runs twice after resume |
| Shell effect followed by exit failure | Resume repeats the effect |
| KeyboardInterrupt after effect before result persistence | Resume repeats the effect |
| Two processes reading the same paused state | Both finish and append the same effect |
| Process death after effect | Durable state stays running; resume refuses it |

The concurrency test forces an interleaving using a barrier immediately after
the real RunState.load in each process. It does not change the loaded state or
replace engine persistence. This is a deterministic API-level schedule-injection
reproduction, not an uninstrumented CLI race or a claim about its frequency.
The abrupt-exit test terminates its own fixture process with os._exit(91), not
the test runner or any operator process. The KeyboardInterrupt test is a separate
cooperative-interruption case and must not be described as a hard crash.

## Safety and limitations

All effects are fixed printf commands in temporary directories. No workflow
command/prompt step, AI CLI, tmux, SSH or network client is invoked. The actual
CLI subprocess receives a minimal environment with temporary HOME. Fixture
processes are bounded and joined; only those processes can be terminated by
cleanup. Temporary state is removed after tests. This is isolation by controlled
fixtures, not an OS sandbox or proof for untrusted YAML.

The test uses the native engine directly for controlled fault/concurrency
injection and the actual CLI for the no-remote run/resume case. It does not prove
overlay compatibility, distributed locking, worker lifecycle, GitHub sync,
secret redaction, or Mac/Dell execution parity. It does not exercise real
delegation delivery, which remains T008 and the existing Mesh E2E harness.

## Decision gate

Do not replace Mesh worker execution with the native workflow engine in this
rollout. Native sequencing is useful, but a direct shell-step adapter would
still need ownership, crash reconciliation and irreversible-step replay controls.
Adding those around another scheduler has not shown a KISS benefit here.

Reuse upstream skills, spec/plan/tasks, project upgrade machinery and supported
artifact checks. Preserve existing Mesh review dispatch/receipt state as the
only managed worker-delivery path, and preserve the review ledger. Do not start
the built-in SDD workflow from the live coordinator: its command steps spawn
integration CLI subprocesses. Native read-only or isolated experiments can remain
separate; they must not claim ownership of the production task lifecycle.

T003 should therefore align capability reporting and the coordinator contract
with this measured boundary, not build an execution adapter or a second run
database. T004 local review identity and T005 readiness are still necessary:
the native engine experiment does not fix the 096 binding/parser blockers.

Reconsider native delegation only with an upstream change or a proven minimal
supported mechanism that passes the same interruption/ownership tests, including
Mesh unknown-receipt semantics. Do not weaken these tests to justify adoption.

## Independent review

The first local Claude review exceeded three minutes without output and was
terminated; it was not counted as approval. A bounded, tools-disabled retry
reviewed the final eight-test source and completed. It did not execute tests.

- Accepted: distinguish cooperative Ctrl-C from hard process death explicitly;
  improve child-exit and barrier-schedule diagnostics. These are now in the test.
- Rejected: a child validation exception would not be reported as a hang: the
  joined process has a non-91 exit status and fails that assertion, with traceback
  on stderr. The added message makes this clearer.
- Rejected: the pinned interpreter does not need its directory on PATH when
  subprocess receives the absolute sys.executable. The real CLI test succeeded
  with the minimal environment. Windows portability is not claimed.
- Claude found no issue with the injected two-process schedule or assertions
  characterizing duplicate effects. No confirmed high/medium test defect remains.

Eight boundary tests passed on repeated runs. Existing review/GitHub regression
suites also passed: 98 tests. py_compile and git diff --check passed. This
completes T002 locally, not T008 real-worker E2E or T010 deployment.
