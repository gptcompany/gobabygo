---
phase: 09-self-healing-resilience
plan: 03
subsystem: database
tags: [sqlite, wal, pragma, prometheus, watchdog, systemd, health-check]

# Dependency graph
requires:
  - phase: 06-observability
    provides: MeshMetrics Prometheus collector with Gauge/Counter/Summary
  - phase: 09-01
    provides: Graceful shutdown with sd_notify and watchdog_loop in server.py
provides:
  - RouterDB health check methods (check_wal_size, check_integrity, check_disk_space)
  - Watchdog DB health monitoring with configurable thresholds
  - Four Prometheus metrics for DB health observability
affects: [10-dashboard, monitoring, alerting]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Cycle-gated expensive checks: cycle > 0 and cycle % N == 0 to skip startup and amortize cost"
    - "sd_notify FIRST in watchdog cycle, health checks after (never block systemd heartbeat)"
    - "Exception-safe watchdog: all health checks wrapped in try/except to prevent thread death"

key-files:
  created:
    - tests/router/test_db_health.py
  modified:
    - src/router/db.py
    - src/router/server.py
    - src/router/metrics.py

key-decisions:
  - "sd_notify always first in watchdog cycle -- health checks must never delay systemd heartbeat"
  - "PRAGMA integrity_check gated to every N cycles (default 10 = ~100s) because it is expensive on large DBs"
  - "Cycle 0 skips integrity check to avoid delaying server readiness on startup"
  - "Escalation = log.error + Prometheus counter only (no external alerting in this phase)"

patterns-established:
  - "Cycle-gated expensive operations: use cycle > 0 and cycle % N == 0 for amortized costs"
  - "DB health config via env vars with sensible defaults (50MB WAL, 100MB disk, 10 cycle interval)"

requirements-completed: [RESL-04]

# Metrics
duration: 8min
completed: 2026-02-21
---

# Phase 9 Plan 3: Watchdog DB Health Checks Summary

**SQLite WAL size tracking, periodic PRAGMA integrity_check, and disk space monitoring integrated into watchdog thread with Prometheus metrics**

## Performance

- **Duration:** 8 min
- **Started:** 2026-02-21T11:29:00Z
- **Completed:** 2026-02-21T11:37:00Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- RouterDB exposes db_path property and three health check methods: check_wal_size() (WAL file bytes), check_integrity() (PRAGMA integrity_check bool), check_disk_space() (free bytes on DB partition)
- Watchdog thread performs WAL + disk checks every 10s cycle, integrity check every N cycles (default 10 = ~100s), with sd_notify always first
- Four new Prometheus metrics: mesh_db_wal_size_bytes, mesh_db_integrity_ok, mesh_db_disk_free_bytes, mesh_db_health_check_errors_total
- 13 new tests covering all health check methods, watchdog integration, cycle gating, error handling, and configuration

## Task Commits

Each task was committed atomically:

1. **Task 1: Add DB health check methods to RouterDB and Prometheus metrics** - `7ddfcf6` (feat)
2. **Task 2: Integrate DB health checks into watchdog thread with cycle counter** - `651f78d` (feat)

## Files Created/Modified
- `src/router/db.py` - Added db_path property, check_wal_size(), check_integrity(), check_disk_space() methods
- `src/router/server.py` - Enhanced watchdog_loop with DB health checks, cycle counter, env var config, db_health_config in router_state
- `src/router/metrics.py` - Added 4 DB health metrics (db_wal_size_bytes, db_integrity_ok, db_disk_free_bytes, db_health_check_errors_total)
- `tests/router/test_db_health.py` - 13 tests across 3 test classes (TestDBHealthChecks, TestDBHealthMetrics, TestWatchdogDBHealth)

## Decisions Made
- sd_notify always first in watchdog cycle to never block systemd heartbeat
- PRAGMA integrity_check gated to every N cycles (default 10 = ~100s) because it is expensive on large DBs
- Cycle 0 skips integrity check to avoid delaying server readiness on startup
- Escalation = log.error + Prometheus counter only (no external alerting in this phase)
- Config via env vars: MESH_DB_WAL_SIZE_THRESHOLD_BYTES (50MB), MESH_DB_DISK_FREE_THRESHOLD_BYTES (100MB), MESH_DB_INTEGRITY_CHECK_INTERVAL (10)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed sqlite3.Connection.execute mock approach in test**
- **Found during:** Task 1 (test_check_integrity_returns_false_on_error)
- **Issue:** sqlite3.Connection.execute is a read-only C attribute that cannot be patched with unittest.mock.patch.object
- **Fix:** Replaced _conn attribute on RouterDB instance with MagicMock instead of patching execute method
- **Files modified:** tests/router/test_db_health.py
- **Verification:** Test passes correctly, verifying the except sqlite3.Error branch
- **Committed in:** 7ddfcf6 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Minor test implementation adjustment. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Phase 9 (Self-Healing & Resilience) complete: all 3 plans executed
- Watchdog thread now monitors systemd heartbeat + DB health
- Ready for Phase 10 (if applicable) or milestone completion

## Self-Check: PASSED

All files exist, all commits verified.

---
*Phase: 09-self-healing-resilience*
*Completed: 2026-02-21*
