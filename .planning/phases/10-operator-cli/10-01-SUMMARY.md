---
phase: 10-operator-cli
plan: 01
subsystem: api
tags: [http, rest, fsm, draining, worker-management, graceful-shutdown]

# Dependency graph
requires:
  - phase: 08-long-polling-transport
    provides: LongPollRegistry for worker notification on drain/retire
  - phase: 09-self-healing
    provides: Heartbeat stale detection extended to draining workers
provides:
  - "GET /workers endpoint with embedded running_tasks"
  - "GET /workers/<id> endpoint with 404 handling"
  - "POST /workers/<id>/drain endpoint with FSM validation"
  - "draining state in WORKER_TRANSITIONS FSM"
  - "Auto-retire logic on task complete/fail/review"
affects: [10-operator-cli]

# Tech tracking
tech-stack:
  added: []
  patterns: [_update_worker_post_task helper for DRY auto-retire logic]

key-files:
  created: []
  modified:
    - src/router/worker_manager.py
    - src/router/server.py
    - src/router/scheduler.py
    - src/router/db.py
    - tests/router/test_worker_manager.py
    - tests/router/test_server.py
    - tests/router/test_scheduler.py

key-decisions:
  - "Extracted _update_worker_post_task helper to avoid triplicating draining check across _route_to_completed, _route_to_review, report_failure"
  - "draining -> stale allows heartbeat timeout to catch unresponsive draining workers; stale recovery cancels drain (acceptable)"
  - "GET /workers embeds running_tasks inline (both running + assigned statuses) -- no N+1 concern for 3-5 worker fleet"

patterns-established:
  - "Worker drain pattern: idle/busy -> draining -> offline (auto-retire on last task) or draining -> stale (heartbeat timeout)"
  - "Endpoint handler pattern: _handle_X methods with _check_auth() guard, state dict access, typed DB/WM references"

requirements-completed: [OPSC-01, OPSC-02, OPSC-03]

# Metrics
duration: 10min
completed: 2026-02-21
---

# Phase 10 Plan 01: Worker Management Endpoints Summary

**Three new HTTP endpoints (GET /workers, GET /workers/<id>, POST /drain) with draining FSM state for graceful worker shutdown**

## Performance

- **Duration:** 10 min
- **Started:** 2026-02-21T15:09:23Z
- **Completed:** 2026-02-21T15:19:52Z
- **Tasks:** 3
- **Files modified:** 7

## Accomplishments
- Added "draining" state to WORKER_TRANSITIONS FSM with correct edges (idle/busy -> draining, draining -> offline/stale)
- Three authenticated HTTP endpoints: list workers, get worker detail, initiate drain
- Auto-retire logic retires draining workers to offline when their last in-flight task completes, fails, or enters review
- 24 new tests covering FSM transitions, endpoint behavior, auth, error codes, and auto-retire scenarios

## Task Commits

Each task was committed atomically:

1. **Task 1: Add draining state to FSM + drain_worker() method** - `8970822` (feat)
2. **Task 2: Add GET /workers, GET /workers/<id>, POST /workers/<id>/drain endpoints** - `be6a8b3` (feat)
3. **Task 3: Auto-retire draining worker on task complete/fail** - `e6efd7e` (feat)

## Files Created/Modified
- `src/router/worker_manager.py` - WORKER_TRANSITIONS with draining state, drain_worker() method
- `src/router/server.py` - Three new endpoint handlers: _handle_list_workers, _handle_get_worker, _handle_drain_worker
- `src/router/scheduler.py` - _update_worker_post_task helper for draining auto-retire logic
- `src/router/db.py` - list_stale_candidates includes draining workers
- `tests/router/test_worker_manager.py` - 8 new tests for drain FSM and drain_worker()
- `tests/router/test_server.py` - 11 new tests for worker endpoints
- `tests/router/test_scheduler.py` - 5 new tests for auto-retire behavior

## Decisions Made
- Extracted `_update_worker_post_task` helper to DRY the draining check across three call sites (_route_to_completed, _route_to_review, report_failure)
- draining -> stale transition allowed so heartbeat timeout catches unresponsive draining workers; stale recovery cancels drain (stale -> idle) which is acceptable
- GET /workers embeds both running + assigned tasks inline (no separate N+1 calls needed by meshctl)
- Idle workers with no in-flight tasks are drained immediately (drain -> deregister -> offline in one call)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Worker management endpoints ready for meshctl CLI consumption (Plan 10-02)
- All 381 tests pass (357 existing + 24 new)
- draining state fully integrated into FSM, scheduler, heartbeat, and server

---
*Phase: 10-operator-cli*
*Completed: 2026-02-21*
