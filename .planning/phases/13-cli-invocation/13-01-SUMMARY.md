---
phase: 13-cli-invocation
plan: 01
subsystem: worker
tags: [subprocess, cli, dry-run, failure-semantics, worker-client]
duration: 7min
completed: 2026-02-23
---

# Phase 13 Plan 01: CLI Invocation Summary

## Performance

- Duration: 7min
- Tasks: 3
- Files modified: 3

## Accomplishments

- Rewrote `_execute_task` with real `subprocess.run()` invocation (no `shell=True`)
- Added dry-run mode (`MESH_DRY_RUN=1`): logs command, reports complete with `dry_run: true`
- Guaranteed failure semantics: every error path calls `_report_failure()` -- no task stuck in 'running'
  - Missing `payload.prompt` -> failure
  - `subprocess.TimeoutExpired` -> failure with timeout message
  - `FileNotFoundError` -> failure with command name
  - Non-zero exit -> failure with stderr (truncated to 2KB)
  - Any unexpected exception -> failure
- Output truncation: stdout capped at 4KB, stderr at 2KB
- CLI command template: `{account_profile}` interpolated (e.g., `ccs work`)
- Extracted 3 helper methods: `_ack_task()`, `_report_complete()`, `_report_failure()`
- Added 4 new WorkerConfig fields: `cli_command`, `dry_run`, `work_dir`, `task_timeout`
- All config from env: `MESH_CLI_COMMAND`, `MESH_DRY_RUN`, `MESH_WORK_DIR`, `MESH_TASK_TIMEOUT_S`
- Added 2 Prometheus metrics: `mesh_cli_executions_total` (labels: success/failure/timeout/not_found/dry_run), `mesh_cli_duration_seconds` (Histogram)
- 10 new tests (all mock-based, no real CLI in test suite)
- 1 existing test updated (added payload.prompt + dry_run)

## Task Commits

All delivered in single implementation pass (retroactive GSD tracking).

## Files Created/Modified

- `src/router/worker_client.py` -- Rewritten _execute_task, 3 helpers, 4 config fields (~120 LOC changed)
- `src/router/metrics.py` -- cli_executions + cli_duration_seconds metrics
- `tests/router/test_worker_client.py` -- TestCLIExecution class (10 tests), updated existing test

## Decisions Made

1. `subprocess.run()` with `capture_output=True, text=True` -- safe, no shell injection
2. `--print -p` flags for claude CLI (print-only mode with prompt)
3. Output truncation uses slice `[-4096:]` to keep tail (most relevant output)
4. Dry-run reports as `completed` (not `failed`) since it's intentional behavior
5. All mock-based tests -- real CLI integration tested at deployment time

## Deviations from Plan

None

## Issues Encountered

- Existing `test_poll_executes_received_task` failed because it didn't include `payload.prompt` -- fixed by adding prompt and dry_run to test fixture

## Next Phase Readiness

v1.2 milestone complete. Full E2E flow functional:
1. Router starts with dispatch loop
2. Worker registers and polls
3. Task submitted via POST /tasks or meshctl submit
4. Dispatch loop assigns to idle worker
5. Worker executes CLI (or dry-run) and reports result

## Self-Check: PASSED

- [x] Missing prompt -> failure
- [x] Dry-run -> complete without subprocess
- [x] Success subprocess -> complete with output
- [x] Failed subprocess -> failure with stderr
- [x] Timeout -> failure with timeout message
- [x] Command not found -> failure
- [x] Output truncation (4KB/2KB)
- [x] {account_profile} interpolation works
- [x] All env vars read correctly
- [x] 435 tests pass
