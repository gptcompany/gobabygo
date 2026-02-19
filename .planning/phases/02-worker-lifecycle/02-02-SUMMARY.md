# Plan 02-02 Summary: Deterministic Scheduler

## Status: COMPLETED

## What was built

### Scheduler (`src/router/scheduler.py` — 249 LOC)
- **Deterministic selection**: target_cli → target_account → idle_since ASC (longest idle first)
- **TOCTOU-safe dispatch**: CAS worker idle→busy + task queued→assigned + lease creation in single transaction
- **Candidate iteration**: If CAS fails for one worker (concurrent grab), tries next candidate
- **Task eligibility**: Respects `not_before` (backoff), `depends_on` (dependency resolution), priority ordering
- **Ack**: assigned→running via `apply_transition()` (standalone, no nesting issue)
- **Complete**: running→completed + lease expire + worker→idle (direct CAS in transaction)
- **Failure**: running→failed + lease expire + worker→idle + triggers `on_task_terminal`

### Architecture decision
- Compound operations (dispatch, complete, fail) use **direct CAS + event insert** inside `db.transaction()`
- Standalone transitions (ack) use `apply_transition(db, TransitionRequest(...))`
- This avoids nested transaction issues since `apply_transition()` manages its own `db.transaction()`

## Tests

- `test_scheduler.py`: 17 tests
  - Eligible worker: exact match, wrong cli, wrong account, busy excluded, fairness (longest idle)
  - Find next task: priority order, not_before respected, empty queue
  - Dispatch: success (full lifecycle verify), no worker, no task, atomic rollback (two sequential dispatches)
  - Ack: success, wrong worker rejected
  - Complete: success + cleanup, triggers dependency resolution
  - Failure: full cleanup verified

## Metrics

| Metric | Value |
|--------|-------|
| Production LOC | 249 |
| Test LOC | 201 |
| New tests | 17 |
| Commits | 1 (shared with 02-03) |
| Regression | 92/92 green |
