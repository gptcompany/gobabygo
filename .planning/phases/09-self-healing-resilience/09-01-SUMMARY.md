---
phase: 09-self-healing-resilience
plan: 01
subsystem: resilience
tags: [heartbeat, self-healing, review-timeout, daemon-thread, worker-reregistration]

# Dependency graph
requires:
  - phase: 04-heartbeat-and-stale-detection
    provides: "HeartbeatManager with unknown_worker response and stale sweep"
  - phase: 05-verifier-gate
    provides: "VerifierGate.check_review_timeout() method"
provides:
  - "Worker auto-reregistration on unknown_worker heartbeat response"
  - "Periodic review timeout check thread in run_server()"
affects: [graceful-shutdown, monitoring]

# Tech tracking
tech-stack:
  added: []
  patterns: ["daemon thread with sleep-first pattern for periodic checks", "nested try/except to protect heartbeat thread from re-registration errors"]

key-files:
  created: []
  modified:
    - src/router/worker_client.py
    - src/router/server.py
    - tests/router/test_worker_client.py
    - tests/router/test_server.py

key-decisions:
  - "Re-registration call wrapped in its own try/except inside heartbeat loop to prevent thread death on transient errors"
  - "Review check thread uses sleep-first pattern (same as existing watchdog_loop) to avoid immediate fire on startup"

patterns-established:
  - "Nested try/except in heartbeat loop: outer catches network errors, inner protects re-registration from killing the thread"
  - "VerifierGate instance stored in router_state for testability"

requirements-completed: [RESL-01, RESL-05]

# Metrics
duration: 7min
completed: 2026-02-21
---

# Phase 9 Plan 1: Self-Healing Wiring Summary

**Worker auto-reregistration on unknown_worker heartbeat and periodic review timeout detection thread wired into runtime loops**

## Performance

- **Duration:** 7 min
- **Started:** 2026-02-21T11:01:39Z
- **Completed:** 2026-02-21T11:08:52Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- Worker heartbeat loop now parses response JSON and auto-reregisters on `unknown_worker` status, with nested try/except preventing thread death
- Server runs periodic daemon thread calling `VerifierGate.check_review_timeout(db)` every N seconds (default 60s, configurable via `MESH_REVIEW_CHECK_INTERVAL_S`)
- Backward compatibility preserved: non-JSON heartbeat responses from old servers handled gracefully
- 5 new tests (3 worker client, 2 server) covering auto-reregister, normal operation, non-JSON tolerance, interval config, and stale review detection

## Task Commits

Each task was committed atomically:

1. **Task 1: Auto-reregister worker on unknown_worker heartbeat response** - `f09e125` (feat)
2. **Task 2: Schedule periodic stale review timeout detection** - `ffc8484` (feat)

## Files Created/Modified
- `src/router/worker_client.py` - Heartbeat loop parses response JSON, detects unknown_worker, calls _register() with nested try/except
- `src/router/server.py` - Added VerifierGate import, review_check_interval config, review_check_loop daemon thread, verifier_gate in router_state
- `tests/router/test_worker_client.py` - Added configurable heartbeat_response/heartbeat_raw_response to MockRouterHandler, 3 new tests in TestAutoReregisterOnUnknownWorker
- `tests/router/test_server.py` - Added TestReviewTimeoutScheduling class with 2 tests

## Decisions Made
- Re-registration call wrapped in its own try/except inside heartbeat loop to prevent thread death on transient network errors (critical gate feedback)
- Test for "ok does not reregister" compares against initial register count (not assert == 0), per gate feedback
- Review check thread follows existing watchdog_loop pattern: daemon=True, sleep-first, exception-safe

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Self-healing wiring complete: both disconnected mechanisms now fire in their runtime loops
- Ready for Plan 09-02 (graceful drain) and Plan 09-03 (server shutdown orchestration)
- All 332 tests pass (327 existing + 5 new)

## Self-Check: PASSED

- All 4 modified files exist on disk
- Commit f09e125 (Task 1) verified in git log
- Commit ffc8484 (Task 2) verified in git log
- 332 tests pass, 0 failures

---
*Phase: 09-self-healing-resilience*
*Completed: 2026-02-21*
