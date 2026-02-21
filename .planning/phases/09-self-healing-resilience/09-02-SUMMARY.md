---
phase: 09-self-healing-resilience
plan: 02
subsystem: bridge
tags: [buffer, replay, backoff, threading, drain, prometheus]

# Dependency graph
requires:
  - phase: 09-01
    provides: "FallbackBuffer with append/replay/clear, EventEmitter with replay_buffer()"
provides:
  - "Periodic buffer replay timer with exponential backoff (60s-600s)"
  - "On-next-emit drain trigger via threading.Event (async, non-blocking)"
  - "has_events() cheap existence check on FallbackBuffer"
  - "Prometheus counters mesh_buffer_replay_total and mesh_buffer_replay_events_total"
  - "Server-side emitter with replay timer started on server startup"
affects: [09-03, graceful-shutdown]

# Tech tracking
tech-stack:
  added: []
  patterns: ["exponential backoff with cap for retry loops", "threading.Event for async wakeup signaling"]

key-files:
  created: []
  modified:
    - src/router/bridge/emitter.py
    - src/router/bridge/buffer.py
    - src/router/server.py
    - src/router/metrics.py
    - tests/router/test_emitter.py
    - tests/router/test_buffer.py
    - tests/router/test_server.py

key-decisions:
  - "Drain uses threading.Event to wake timer thread, not synchronous replay inside emit()"
  - "Exponential backoff doubles interval on failure, caps at 600s, resets on full success"

patterns-established:
  - "Async signal pattern: emit() sets threading.Event, background thread acts on it"
  - "Cheap file check: has_events() uses os.stat() instead of reading entire file"

requirements-completed: [RESL-02, RESL-03]

# Metrics
duration: 9min
completed: 2026-02-21
---

# Phase 9 Plan 2: Buffer Replay Timer Summary

**Periodic buffer replay with exponential backoff (60s-600s) and async on-next-emit drain via threading.Event**

## Performance

- **Duration:** 9 min
- **Started:** 2026-02-21T11:14:18Z
- **Completed:** 2026-02-21T11:23:48Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- FallbackBuffer.has_events() provides O(1) existence check via os.stat (no file read)
- EventEmitter replay timer thread with exponential backoff: 60s -> 120s -> 240s... capped at 600s
- threading.Lock prevents concurrent replays; threading.Event enables async drain from emit()
- Server starts replay timer on startup, configurable via MESH_BUFFER_REPLAY_INTERVAL_S env var
- Prometheus counters mesh_buffer_replay_total (labeled by result) and mesh_buffer_replay_events_total

## Task Commits

Each task was committed atomically:

1. **Task 1: Add has_events() and replay timer with backoff** - `5471370` (feat)
2. **Task 2: Wire replay timer into server and add metrics** - `2640a43` (feat)

## Files Created/Modified
- `src/router/bridge/buffer.py` - Added has_events() method using os.stat for cheap check
- `src/router/bridge/emitter.py` - Added _replay_lock, _drain_event, start_replay_timer(), emit() drain trigger
- `src/router/server.py` - Create emitter/buffer, start replay timer, store in router_state
- `src/router/metrics.py` - Added buffer_replay_total and buffer_replay_events counters
- `tests/router/test_buffer.py` - 1 new test for has_events()
- `tests/router/test_emitter.py` - 8 new tests for replay timer, backoff, drain, lock, edge cases
- `tests/router/test_server.py` - 3 new tests for wiring, config, metrics

## Decisions Made
- Drain uses threading.Event to wake timer thread instead of synchronous replay in emit() -- keeps emit() non-blocking
- Exponential backoff doubles on failure (2x multiplier), caps at 600s (10 min), resets to base on full success
- has_events() uses Path.stat().st_size > 0 instead of read_all() for performance

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Buffer replay timer is running, ready for graceful shutdown integration (plan 09-03)
- The emitter and buffer are accessible in router_state for shutdown drain
- All 344 tests pass (332 existing + 12 new)

## Self-Check: PASSED

All 7 modified files verified present. Both task commits (5471370, 2640a43) verified in git log.

---
*Phase: 09-self-healing-resilience*
*Completed: 2026-02-21*
