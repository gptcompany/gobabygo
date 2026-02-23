---
phase: 12-post-tasks
plan: 01
subsystem: api
tags: [http, rest, task-creation, idempotency, meshctl, cli]
duration: 8min
completed: 2026-02-23
---

# Phase 12 Plan 01: POST /tasks Endpoint Summary

## Performance

- Duration: 8min
- Tasks: 4
- Files modified: 6

## Accomplishments

- Added `TaskCreateRequest` Pydantic DTO in models.py -- public fields only (title required, defaults for phase/cli/account)
- Added `POST /tasks` handler with JSON validation, Pydantic validation, IntegrityError handling (409), eager dispatch
- Internal fields (status=queued, assigned_worker=None, attempt=1) always set server-side
- Added `meshctl submit` subcommand (--title, --cli, --account, --phase, --priority, --payload)
- Added 2 Prometheus counters: `mesh_tasks_created_total`, `mesh_tasks_create_errors_total` (labels: invalid/duplicate/db_error)
- 16 new tests: 9 server + 7 meshctl

## Task Commits

All delivered in single implementation pass (retroactive GSD tracking).

## Files Created/Modified

- `src/router/models.py` -- TaskCreateRequest class (15 LOC)
- `src/router/server.py` -- _handle_create_task handler + POST /tasks route (48 LOC)
- `src/router/metrics.py` -- tasks_created + tasks_create_errors counters
- `src/meshctl.py` -- cmd_submit + submit_parser + entry point (50 LOC)
- `tests/router/test_server.py` -- TestPostTasksEndpoint class (9 tests)
- `tests/test_meshctl.py` -- TestSubmitCommand class (7 tests)

## Decisions Made

1. TaskCreateRequest has `target_account="work"` (not "default") since "work" is the common case
2. Eager dispatch is best-effort: failure doesn't affect 201 response (dispatch loop is backup)
3. `sqlite3.IntegrityError` caught generically -- covers idempotency_key UNIQUE constraint
4. meshctl submit follows pure HTTP client pattern (no router imports)

## Deviations from Plan

None

## Issues Encountered

None

## Next Phase Readiness

Phase 13 (CLI Invocation) is unblocked -- tasks can now be submitted via HTTP and dispatched automatically.

## Self-Check: PASSED

- [x] POST /tasks returns 201 with task_id
- [x] Invalid fields return 400
- [x] Duplicate idempotency_key returns 409
- [x] Internal fields ignored from client
- [x] Eager dispatch works
- [x] meshctl submit works
- [x] 435 tests pass
