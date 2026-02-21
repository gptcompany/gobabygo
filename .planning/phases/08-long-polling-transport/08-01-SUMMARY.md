---
phase: 08-long-polling-transport
plan: 01
subsystem: transport
tags: [long-poll, threading, condition-variable, prometheus, metrics]

# Dependency graph
requires:
  - phase: 07-tech-debt-cleanup
    provides: "Stable server handler and metrics infrastructure"
provides:
  - "LongPollRegistry with per-worker Condition and predicate pattern"
  - "Blocking long-poll semantics on GET /tasks/next"
  - "Prometheus longpoll metrics (counter, histogram, gauge)"
  - "MESH_LONGPOLL_TIMEOUT_S env var configuration"
affects: [09-scheduler-wakeup, worker-client]

# Tech tracking
tech-stack:
  added: [prometheus-client Counter, prometheus-client Histogram]
  patterns: [per-worker Condition, state predicate wait loop, zombie connection detection, PollResult dataclass]

key-files:
  created:
    - src/router/longpoll.py
    - tests/router/test_longpoll.py
  modified:
    - src/router/server.py
    - src/router/metrics.py
    - tests/router/test_server.py

key-decisions:
  - "PollResult dataclass for typed returns instead of sentinel object"
  - "Auto-create slot on wait_for_task if worker not pre-registered"
  - "Zombie grace period = timeout + 5s using monotonic timestamp"

patterns-established:
  - "Per-worker Condition with predicate pattern: while not task_available and remaining > 0: cond.wait()"
  - "Timeout race condition mitigation: DB fallback check after timeout before returning 204"
  - "LongPollRegistry accessible via router_state dict for cross-module access"

requirements-completed: [TRNS-01, TRNS-03]

# Metrics
duration: 9min
completed: 2026-02-20
---

# Phase 8 Plan 1: Long-Poll Registry Summary

**Per-worker threading.Condition long-poll registry with predicate pattern, blocking server handler, and Prometheus metrics for poll outcomes**

## Performance

- **Duration:** 9 min
- **Started:** 2026-02-20T16:16:00Z
- **Completed:** 2026-02-20T16:25:27Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- LongPollRegistry module with per-worker Condition objects and state predicate wait loop
- Server handler blocks on long-poll, returns 200/204/409 correctly with configurable timeout
- Three Prometheus metrics: mesh_longpoll_total (Counter), mesh_longpoll_wait_seconds (Histogram), mesh_longpoll_waiting_workers (Gauge)
- 15 new tests (14 unit + 1 integration), all 317 tests pass

## Task Commits

Each task was committed atomically:

1. **Task 1: Create LongPollRegistry module** - `ba817ea` (feat)
2. **Task 2: Wire long-poll into server handler + Prometheus metrics** - `7d0b1b8` (feat)

## Files Created/Modified
- `src/router/longpoll.py` - LongPollRegistry class with register/unregister/wait_for_task/notify_task_available/waiting_count
- `src/router/server.py` - Replaced _handle_task_poll() with blocking long-poll semantics, added LongPollRegistry + timeout to router_state
- `src/router/metrics.py` - Added Counter, Histogram, Gauge imports and three longpoll metrics
- `tests/router/test_longpoll.py` - 14 unit tests for LongPollRegistry (register, wait, notify, conflict, zombie, race condition)
- `tests/router/test_server.py` - Updated both fixtures with longpoll state, added duplicate poll integration test

## Decisions Made
- Used PollResult dataclass for typed returns instead of sentinel object -- cleaner API, mypy-friendly
- Auto-create slot on wait_for_task if worker not pre-registered -- more resilient, avoids ordering dependency between register and first poll
- Zombie grace period = timeout + 5s using monotonic timestamp -- prevents half-open TCP connections from locking workers

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- LongPollRegistry is ready for scheduler wakeup integration (Plan 08-02)
- notify_task_available() is the hook the scheduler will call after successful dispatch
- Registry is accessible via router_state["longpoll_registry"] for cross-module access

---
*Phase: 08-long-polling-transport*
*Completed: 2026-02-20*
