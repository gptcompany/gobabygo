# Plan 03-02: Verifier Gate for Critical Tasks

## Status: COMPLETED

## What was built

### Task 1: Model + Schema + Verifier Module

**Model updates (`src/router/models.py`):**
- Added `critical: bool = False` field to Task
- Added `rejection_count: int = 0` field to Task
- Added `review_timeout_at: str | None = None` field to Task

**Schema updates (`src/router/db.py`):**
- Added `critical INTEGER NOT NULL DEFAULT 0` column
- Added `rejection_count INTEGER NOT NULL DEFAULT 0` column
- Added `review_timeout_at TEXT` column
- Updated `_task_from_row()` and `insert_task()` for new fields

**New module (`src/router/verifier.py`):**
- `VerifierGate` class with 5 methods:
  1. `should_review(task)` -- returns task.critical
  2. `has_pending_fixes(db, task_id)` -- queries non-terminal child fix tasks
  3. `approve_task(db, task_id, verifier_id)` -- review -> completed (blocks if pending fixes)
  4. `reject_task(db, task_id, verifier_id, reason, callbacks)` -- creates fix task, escalates after 3 rejections
  5. `check_review_timeout(db)` -- finds expired review tasks, transitions to failed

### Task 2: Scheduler Integration + Tests

**Scheduler updates (`src/router/scheduler.py`):**
- Added `review_timeout_s` parameter to `Scheduler.__init__`
- Added `VerifierGate` instance stored as `self._verifier`
- Refactored `complete_task()` into three methods:
  - `complete_task()` -- routes based on critical flag
  - `_route_to_review()` -- critical tasks: running -> review + sets review_timeout_at
  - `_route_to_completed()` -- non-critical tasks: running -> completed (original behavior)

**Tests (`tests/router/test_verifier.py`) -- 24 tests:**
- `TestShouldReview` (2): critical true/false
- `TestApproveTask` (4): success, logs event, blocked by pending fixes, after fix completed
- `TestRejectTask` (8): creates fix, increments count, inherits target, not critical, logs event, escalation after 3, invokes callbacks, task not found
- `TestHasPendingFixes` (4): no fixes, in progress, completed, mixed states
- `TestReviewTimeout` (3): expired transitions, not expired, no timeout set
- `TestSchedulerVerifierIntegration` (3): critical->review, non-critical->completed, full rejection cycle

## Metrics

| Metric | Value |
|--------|-------|
| Files modified | 3 (models.py, db.py, scheduler.py) |
| Files created | 2 (verifier.py, test_verifier.py) |
| LOC added (src) | ~170 |
| LOC added (tests) | ~210 |
| New tests | 24 |
| Total tests | 136 (112 existing + 24 new) |
| All tests passing | YES |
| Existing tests broken | 0 |

## Commits

1. `feat(03-02): add verifier gate for critical tasks with rejection workflow`
2. `test(03-02): add 24 verifier gate tests covering approval, rejection, escalation, timeout`

## Key Design Decisions

1. **Review timeout transitions to `failed`** (not `timeout`): The FSM allows review -> {completed, failed, canceled}. Since `timeout` is not a valid target from `review`, we use `failed` with reason "review_timeout".

2. **Escalation uses EscalationCallback protocol from retry.py**: Same callback pattern for consistency. Escalation fires after 3 rejections (`_MAX_REJECTIONS`).

3. **Fix tasks are not critical**: Prevents infinite review loops.

4. **Scheduler integration is backward-compatible**: Non-critical tasks (default) follow the same code path as before the change. Zero existing test changes needed.
