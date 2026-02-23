# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-23)

**Core value:** Reliable, deterministic task orchestration across distributed AI workers -- router/DB is the single source of truth.
**Current focus:** v1.2 Operational Readiness -- COMPLETE (all 13 phases delivered)

## Current Position

Milestone: v1.2 Operational Readiness
Phase: 13 of 13 (CLI Invocation) -- COMPLETE
Plan: 1 of 1 in current phase -- COMPLETE
Status: Phase 13 Complete, v1.2 Milestone Complete
Last activity: 2026-02-23 -- Phases 11-13 completed (3 plans, 28 new tests, 435 total)

Progress: [============================] 100% overall (28/28 plans)
v1.2:    [============================] 100% (3/3 phases complete)

## Performance Metrics

**v1.0 Velocity:**
- Total plans completed: 15
- Total commits: 36
- Production LOC: 3,829
- Test LOC: 4,313
- Timeline: ~22 hours (2026-02-18 -> 2026-02-19)

**v1.1 Velocity:**
- Total plans completed: 9
- Started: 2026-02-20

| Phase | Plan | Duration | Tasks | Files |
|-------|------|----------|-------|-------|
| 08    | 01   | 9min     | 2     | 5     |
| 08    | 02   | 15min    | 3     | 8     |
| 09    | 01   | 7min     | 2     | 4     |
| 09    | 02   | 9min     | 2     | 7     |
| 09    | 03   | 8min     | 2     | 4     |
| 10    | 01   | 10min    | 3     | 7     |
| 10    | 02   | 7min     | 2     | 2     |

**v1.2 Velocity:**
- Total plans completed: 3
- Started: 2026-02-23

| Phase | Plan | Duration | Tasks | Files |
|-------|------|----------|-------|-------|
| 11    | 01   | 5min     | 3     | 4     |
| 12    | 01   | 8min     | 4     | 6     |
| 13    | 01   | 7min     | 3     | 3     |

## Accumulated Context

### Decisions

All v1.0 decisions logged in PROJECT.md Key Decisions table (15 decisions, 13 Good, 2 Revisit).

**v1.1 decisions:**
- DEBT-01: fail-closed registration (MESH_DEV_MODE=1 required for open registration)
- DEBT-01: WorkerManager handles register auth; _check_auth() guards other endpoints
- DEBT-01: 200 for re-registration, 201 for new; case-insensitive Bearer parsing
- LP-01: PollResult dataclass for typed returns instead of sentinel object
- LP-01: Auto-create slot on wait_for_task if worker not pre-registered
- LP-01: Zombie grace period = timeout + 5s using monotonic timestamp
- LP-02: Notify after transaction commit so DB state is visible when worker reads
- LP-02: Worker client HTTP timeout = longpoll_timeout + 5s to avoid premature client timeout
- LP-02: Exponential backoff 1s-30s with jitter on server errors; immediate reconnect on 204
- RESL-01: Re-registration call wrapped in nested try/except to prevent heartbeat thread death
- RESL-01: Review check thread uses sleep-first pattern (same as watchdog_loop)
- RESL-02: Drain uses threading.Event to wake timer thread, not synchronous replay in emit()
- RESL-03: Exponential backoff doubles on failure, caps at 600s, resets on full success
- RESL-04: sd_notify always first in watchdog cycle; integrity_check gated to every N cycles (default 10)
- RESL-04: Cycle 0 skips integrity check to avoid delaying startup; escalation = log.error + Prometheus counter
- OPSC-01: Extracted _update_worker_post_task helper to DRY draining auto-retire across 3 call sites
- OPSC-01: draining -> stale allowed for heartbeat timeout safety; stale recovery cancels drain (acceptable)
- OPSC-01: GET /workers embeds running+assigned tasks inline (no N+1 concern for small fleet)
- OPSC-02: Pure HTTP client design for meshctl -- no imports from src.router.*, only argparse + requests
- OPSC-02: Worker IDs truncated to 8 chars in table display; queue summary uses only /health data
- OPSC-02: Drain polling at 2s intervals with configurable --timeout; drained_immediately short-circuits

**v1.2 decisions:**
- OPRDY-01: Dispatch loop drains all tasks per cycle (inner while True) for throughput; CAS prevents double-dispatch
- OPRDY-01: Sleep-first daemon thread pattern; no stop hook (consistent with review_check_loop)
- OPRDY-03: TaskCreateRequest DTO separates public/internal Task fields; client cannot set status/assigned_worker
- OPRDY-03: Eager dispatch on POST /tasks is best-effort; dispatch loop is backup
- OPRDY-03: sqlite3.IntegrityError caught generically for idempotency_key UNIQUE constraint (409)
- OPRDY-04: meshctl submit follows pure HTTP client pattern (no router imports)
- OPRDY-06: subprocess.run without shell=True prevents command injection with user prompts
- OPRDY-06: --print -p flags for claude CLI (print-only mode with prompt)
- OPRDY-07: Dry-run reports as completed (not failed) since it's intentional behavior
- OPRDY-08: Global try/except in _execute_task guarantees _report_failure on any error

### Completed Milestones

- **v1.0 MVP** (2026-02-19): 6 phases, 15 plans, 291 tests -- see `.planning/milestones/`
- **v1.1 Production Readiness** (2026-02-21): 4 phases, 9 plans, 404 tests
- **v1.2 Operational Readiness** (2026-02-23): 3 phases, 3 plans, 435 tests

### Completed Phases (v1.1)

- **Phase 7: Tech Debt Cleanup** (2026-02-20): 2 plans, 11 new tests (302 total), confidence 92%
- **Phase 8: Long-Polling Transport** (2026-02-20): 2 plans, 25 new tests (327 total), confidence 95%
- **Phase 9: Self-Healing & Resilience** (2026-02-21): 3 plans, 30 new tests (357 total), confidence 95%
- **Phase 10: Operator CLI** (2026-02-21): 2 plans, 47 new tests (404 total), confidence 95%

### Completed Phases (v1.2)

- **Phase 11: Dispatch Loop** (2026-02-23): 1 plan, 2 new tests (406 total + E2E updated), confidence 95%
- **Phase 12: POST /tasks** (2026-02-23): 1 plan, 16 new tests (422 total), confidence 95%
- **Phase 13: CLI Invocation** (2026-02-23): 1 plan, 11 new tests (435 total), confidence 95%

### Pending Todos

None

### Blockers/Concerns

None

## Session Continuity

Last session: 2026-02-23
Stopped at: Completed v1.2 milestone (all 3 phases delivered, 435 tests passing)
Resume with: v1.3 planning or production deployment
