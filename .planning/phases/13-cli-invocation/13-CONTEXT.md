# Phase 13: CLI Invocation - Context

## Goal

Workers execute tasks by invoking real CLI processes (subprocess), with dry-run mode for safe testing, guaranteed failure semantics (no task stuck in 'running'), and full configurability.

## Success Criteria (what must be TRUE)

1. `_execute_task` validates `payload.prompt` (fails if missing) and invokes CLI subprocess
2. Dry-run mode (`MESH_DRY_RUN=1`) logs command without executing and reports success
3. Every error scenario (missing prompt, timeout, command not found, non-zero exit, unexpected exception) results in a failure report -- no task left in 'running'
4. CLI command template supports `{account_profile}` interpolation (e.g., `ccs {account_profile}`)
5. Subprocess output truncated to 4KB (stdout) / 2KB (stderr) to prevent oversized reports
6. All config from env vars: `MESH_CLI_COMMAND`, `MESH_DRY_RUN`, `MESH_WORK_DIR`, `MESH_TASK_TIMEOUT_S`

## Requirements

- OPRDY-06: Real CLI invocation in worker
- OPRDY-07: Dry-run mode for deployment validation
- OPRDY-08: Guaranteed failure semantics (no stuck tasks)

## Current State Analysis

- `_execute_task` is a stub: ACKs task, reports fake success with placeholder text
- No subprocess invocation, no payload validation
- WorkerConfig has basic fields but no cli_command, dry_run, work_dir, task_timeout
- Pattern for HTTP reporting exists: _session.post to /tasks/complete and /tasks/fail

## Implementation Boundaries

- Extracted helper methods: `_ack_task()`, `_report_complete()`, `_report_failure()`
- `subprocess.run()` with `capture_output=True, text=True` (no `shell=True`)
- Timeout via `subprocess.TimeoutExpired`
- FileNotFoundError for missing CLI binary
- Global try/except ensures every error path calls `_report_failure()`
- Tests use `@patch("src.router.worker_client.subprocess.run")` -- no real CLI in tests

## Files Modified

- `src/router/worker_client.py` -- rewrite _execute_task, add config fields, extract helpers
- `src/router/metrics.py` -- CLI execution metrics
- `tests/router/test_worker_client.py` -- 10 new tests + 1 updated
