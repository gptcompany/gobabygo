# Plan 01-03 Summary: Crash Recovery + Dependency Resolution

**Phase:** 01-router-core
**Plan:** 01-03
**Status:** COMPLETED
**Date:** 2026-02-18

## Tasks Completed

### Task 1: Crash recovery on startup
- **Files:** `src/router/recovery.py`
- **Commit:** `feat: add crash recovery module for startup state restoration`
- **Details:**
  - `RecoveryResult` dataclass: tracks tasks_requeued, leases_expired, events_replayed, errors
  - `recover_on_startup(db, max_attempts=3)`:
    a. Finds all leases where `expires_at < now()` and expires them
    b. For each expired lease: requeues task (attempt+1) or transitions to failed (terminal) if max_attempts reached
    c. Finds orphaned tasks (assigned/running with no active lease) — same requeue logic
    d. Logs all recovery actions as TaskEvent entries with idempotency keys
    e. Clears denormalized fields (assigned_worker, lease_expires_at) on recovered tasks
  - `audit_timeline(db, task_id)`: replays events for a task in chronological order
  - Recovery uses direct CAS (not FSM) because recovery transitions (running->queued, assigned->queued) are not in the FSM transition table and require atomic compound operations
  - Idempotent: running twice on the same state produces the same result
  - LOC: 254

### Task 2: Event-driven dependency resolution
- **Files:** `src/router/dependency.py`
- **Commit:** `feat: add event-driven dependency resolution module`
- **Details:**
  - `check_dependencies(db, task_id) -> (bool, list[str])`: checks if all depends_on tasks are terminal (completed/failed/canceled)
  - `on_task_terminal(db, completed_task_id) -> int`: event-driven trigger — finds blocked tasks depending on the completed task, checks if ALL deps now resolved, transitions blocked->queued. Primary mechanism, no polling.
  - `resolve_blocked_tasks(db) -> int`: batch fallback for recovery scenarios only — scans all blocked tasks and unblocks eligible ones
  - Uses FSM apply_transition for blocked->queued transitions (valid FSM path), with ImportError fallback to direct CAS
  - LOC: 150

### Task 3: Recovery + dependency tests
- **Files:** `tests/router/test_recovery.py`
- **Commit:** committed by parallel agent (edc1332), test file created during implementation
- **Details:** 12 tests, all passing

## Test Results

```
tests/router/test_recovery.py::TestRecoverExpiredLease::test_requeues_and_increments_attempt PASSED
tests/router/test_recovery.py::TestRecoverMaxAttempts::test_transitions_to_failed PASSED
tests/router/test_recovery.py::TestRecoverOrphanedAssigned::test_requeues_orphaned PASSED
tests/router/test_recovery.py::TestRecoverIdempotent::test_second_run_is_noop PASSED
tests/router/test_recovery.py::TestRecoverCreatesEvents::test_events_logged PASSED
tests/router/test_recovery.py::TestAuditTimeline::test_chronological_order PASSED
tests/router/test_recovery.py::TestCheckDependenciesAllResolved::test_all_resolved PASSED
tests/router/test_recovery.py::TestCheckDependenciesPending::test_pending_dep PASSED
tests/router/test_recovery.py::TestOnTaskTerminalUnblocks::test_unblocks PASSED
tests/router/test_recovery.py::TestOnTaskTerminalPartial::test_partial_not_unblocked PASSED
tests/router/test_recovery.py::TestResolveBlockedBatch::test_batch_unblocks PASSED
tests/router/test_recovery.py::TestDependencyResolutionUsesFSM::test_uses_fsm_transition PASSED

12 passed in 0.16s
```

## Verification Checklist

- [x] `python -m pytest tests/router/test_recovery.py -v` all green (12/12)
- [x] Recovery requeues expired leases correctly (test_requeues_and_increments_attempt)
- [x] Max attempts triggers terminal failure (test_transitions_to_failed) — no infinite loop
- [x] Recovery is idempotent (test_second_run_is_noop) — safe to run multiple times
- [x] Orphaned tasks (assigned/running with no lease) recovered (test_requeues_orphaned)
- [x] Dependency resolution is event-driven via on_task_terminal (test_unblocks)
- [x] Partial dependency resolution does NOT unblock (test_partial_not_unblocked)
- [x] Batch resolve works as fallback (test_batch_unblocks)
- [x] Dependency transitions go through FSM (test_uses_fsm_transition) — verified by state_transition event
- [x] All timestamps UTC
- [x] Full test suite (33 tests) passes with no regressions

## Parallel Execution Notes

- Executed in parallel with Plan 01-02 (FSM guard)
- Did NOT modify `src/router/db.py` (owned by 01-02)
- Integrated with FSM module after it was deployed: dependency.py uses `apply_transition(db, TransitionRequest)` for blocked->queued
- Recovery uses direct CAS because recovery transitions are outside the FSM transition table
- Test file was committed by the 01-02 agent after a race condition fix (edc1332)

## Metrics

- **Total LOC (production):** 404 (recovery.py: 254, dependency.py: 150)
- **Total LOC (tests):** 421
- **Test count:** 12
- **Test duration:** 0.16s
- **2 atomic commits** (recovery, dependency) + 1 by parallel agent (tests)
