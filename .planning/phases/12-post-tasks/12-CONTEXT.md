# Phase 12: POST /tasks Endpoint - Context

## Goal

Tasks can be submitted via HTTP POST, with a clean public API schema separated from internal Task fields. `meshctl submit` provides CLI-based task submission.

## Success Criteria (what must be TRUE)

1. `POST /tasks` accepts a `TaskCreateRequest` DTO and returns 201 with `task_id`
2. Internal fields (status, assigned_worker, attempt) are set server-side, ignoring any client-provided values
3. Duplicate `idempotency_key` returns 409 with clear error message
4. Eager dispatch attempts to assign the task immediately (dispatch loop is backup)
5. `meshctl submit --title "..." --payload '{...}'` creates tasks via HTTP
6. Prometheus metrics track task creation and errors

## Requirements

- OPRDY-03: HTTP task submission endpoint
- OPRDY-04: meshctl submit subcommand
- OPRDY-05: Idempotent task creation

## Current State Analysis

- Tasks can only be inserted via `db.insert_task()` (direct DB access)
- No POST endpoint for task creation exists
- `_handle_register()` provides the pattern for POST handlers with JSON validation
- `meshctl` has `status` and `drain` subcommands -- `submit` follows same pattern
- `idempotency_key` has UNIQUE constraint in schema -- IntegrityError on duplicate

## Implementation Boundaries

- `TaskCreateRequest` is a Pydantic model with public-facing fields only (no status, assigned_worker, etc.)
- Server converts `TaskCreateRequest` -> `Task` via `model_dump()`
- Auth required (uses existing `_check_auth()`)
- Eager dispatch is best-effort: failure logged but does not affect 201 response
- meshctl submit: pure HTTP client (no router imports), ~40 LOC

## Files Modified

- `src/router/models.py` -- TaskCreateRequest DTO
- `src/router/server.py` -- POST /tasks route + handler
- `src/router/metrics.py` -- tasks_created + tasks_create_errors counters
- `src/meshctl.py` -- submit subcommand
- `tests/router/test_server.py` -- 9 new tests
- `tests/test_meshctl.py` -- 7 new tests
