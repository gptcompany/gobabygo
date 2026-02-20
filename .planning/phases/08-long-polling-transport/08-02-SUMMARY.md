---
phase: 08-long-polling-transport
plan: 02
subsystem: transport
tags: [long-poll, scheduler-wakeup, condition-variable, jitter, exponential-backoff, worker-client]

# Dependency graph
requires:
  - phase: 08-long-polling-transport
    plan: 01
    provides: "LongPollRegistry with per-worker Condition and predicate pattern"
provides:
  - "Scheduler wakeup hook notifying waiting workers via Condition after dispatch"
  - "Worker client long-poll reconnect loop with jitter and exponential backoff"
  - "Condition lifecycle cleanup on stale sweep and re-registration"
  - "End-to-end sub-1s dispatch latency via Condition wakeup"
affects: [09-graceful-shutdown, 10-worker-provisioning, worker-client]

# Tech tracking
tech-stack:
  added: []
  patterns: [post-transaction Condition notify, exponential backoff with jitter, longpoll_timeout config propagation]

key-files:
  created: []
  modified:
    - src/router/scheduler.py
    - src/router/heartbeat.py
    - src/router/worker_manager.py
    - src/router/server.py
    - src/router/worker_client.py
    - tests/router/test_scheduler.py
    - tests/router/test_worker_client.py
    - tests/router/test_server.py

key-decisions:
  - "Notify after transaction commit: wakeup call placed AFTER 'with db.transaction()' block so DB state is visible when worker reads"
  - "Worker client blocks on HTTP request (longpoll_timeout + 5s) instead of fixed 2s sleep"
  - "Exponential backoff 1s-30s with random jitter on server errors; immediate reconnect with 100-500ms jitter on 204 timeout"

patterns-established:
  - "Post-commit notification: scheduler.notify_task_available() called after transaction exits, not inside"
  - "Lifecycle hooks: all managers accept optional longpoll_registry for cleanup (None-safe)"
  - "Worker client reconnect loop: no fixed sleep, blocks on server, jitter prevents thundering herd"

requirements-completed: [TRNS-02, TRNS-03]

# Metrics
duration: 15min
completed: 2026-02-20
---

# Phase 8 Plan 2: Scheduler Wakeup Integration Summary

**Scheduler dispatches and wakes waiting workers via Condition wakeup in < 1s, worker client uses long-poll reconnect with jitter/backoff instead of fixed 2s sleep**

## Performance

- **Duration:** 15 min
- **Started:** 2026-02-20T16:31:29Z
- **Completed:** 2026-02-20T16:47:07Z
- **Tasks:** 3
- **Files modified:** 8

## Accomplishments
- Scheduler notifies LongPollRegistry after successful CAS dispatch, waking blocked worker via Condition
- HeartbeatManager cleans up Condition on stale sweep; WorkerManager resets on re-registration, cleans up on deregister
- Worker client replaces 2s fixed polling with long-poll reconnect loop (jitter 100-500ms on 204, exponential backoff 1s-30s on errors)
- End-to-end integration test proves sub-1s dispatch latency (wakeup, not timeout)
- 10 new tests, all 327 tests pass (317 base + 10 new)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add scheduler wakeup hook and lifecycle cleanup hooks** - `39acaa5` (feat)
2. **Task 2: Update worker client for long-poll reconnect with jitter and backoff** - `3a8e599` (feat)
3. **Task 3: End-to-end integration test for long-poll dispatch wakeup** - `e1f64f8` (test)

## Files Created/Modified
- `src/router/scheduler.py` - Added longpoll_registry param, notify_task_available() after CAS dispatch
- `src/router/heartbeat.py` - Added longpoll_registry param, unregister on stale sweep
- `src/router/worker_manager.py` - Added longpoll_registry param, register on re-reg, unregister on dereg
- `src/router/server.py` - Wires longpoll_registry to Scheduler, HeartbeatManager, WorkerManager; removed duplicate instantiation
- `src/router/worker_client.py` - Replaced _poll_loop() with long-poll reconnect, added longpoll_timeout config
- `tests/router/test_scheduler.py` - 3 new tests for wakeup notification (correct worker, no-registry, multi-worker)
- `tests/router/test_worker_client.py` - 5 new tests for reconnect, backoff, timeout config, 409 handling
- `tests/router/test_server.py` - 3 new integration tests (wakeup < 1s, timeout 204, conflict 409); updated fixtures

## Decisions Made
- Notify after transaction commit: moved `notify_task_available()` outside the `with db.transaction()` block so DB state is visible when the worker reads its assigned task. This prevents a race where the worker wakes up but the task isn't committed yet.
- Worker client HTTP timeout = `longpoll_timeout + 5s` to avoid client-side timeout before server responds with 204.
- Exponential backoff uses `min(backoff * 2 or 1.0, 30.0)` pattern: first error backs off 1s, doubles up to 30s cap, all with random jitter.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Long-poll transport is fully wired: registry, server handler, scheduler wakeup, lifecycle hooks, worker client
- Phase 08 complete: both plans delivered, 25 new tests total, 327 total passing
- Ready for Phase 09 (Graceful Shutdown) and Phase 10 (Worker Provisioning)

---
*Phase: 08-long-polling-transport*
*Completed: 2026-02-20*
