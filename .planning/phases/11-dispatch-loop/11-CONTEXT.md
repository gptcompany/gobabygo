# Phase 11: Dispatch Loop - Context

## Goal

Tasks inserted in the DB are automatically assigned to idle workers via a periodic dispatch loop thread, eliminating the need for manual `scheduler.dispatch()` calls.

## Success Criteria (what must be TRUE)

1. A daemon thread in `run_server()` periodically calls `scheduler.dispatch()` and drains all dispatchable tasks per cycle
2. Dispatch interval is configurable via `MESH_DISPATCH_INTERVAL_S` (default 5s)
3. Prometheus metrics track dispatch cycle outcomes (dispatched/empty/error) and total tasks dispatched
4. E2E smoke test no longer requires manual `scheduler.dispatch()` -- the loop handles it automatically

## Requirements

- OPRDY-01: Automatic task dispatch without manual intervention
- OPRDY-02: Observability for dispatch operations

## Current State Analysis

- `scheduler.dispatch()` exists and works correctly (CAS-based, atomic)
- No code in `run_server()` calls it periodically
- Pattern for daemon threads already established: `review_check_loop` (line ~554), `watchdog_loop` (line ~579)
- E2E test (`test_e2e_live.py`) manually calls `scheduler.dispatch()` in a background thread

## Implementation Boundaries

- New daemon thread follows existing pattern (sleep-first, while True, exception-safe)
- Inner `while True` drains all tasks per cycle (not just 1)
- Thread is daemon -- dies with process, no stop hook needed (consistent with review_check_loop)
- Metrics: 2 new counters (`dispatch_cycles_total`, `tasks_dispatched_total`)

## Files Modified

- `src/router/server.py` -- dispatch_loop thread in run_server()
- `src/router/metrics.py` -- dispatch metrics
- `tests/router/test_server.py` -- 2 new tests
- `tests/smoke/test_e2e_live.py` -- remove manual dispatch, use loop
