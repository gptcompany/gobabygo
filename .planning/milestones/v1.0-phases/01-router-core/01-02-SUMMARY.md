# Plan 01-02 Summary: FSM Transition Guard + Dead-Letter Stream

**Phase:** 01-router-core
**Plan:** 01-02
**Status:** COMPLETED
**Date:** 2026-02-18

## Tasks Completed

### Task 1: FSM transition guard
- **Files:** `src/router/fsm.py`
- **Commit:** `feat: add FSM transition guard with atomic CAS transitions`
- **Details:**
  - `ALLOWED_TRANSITIONS`: dict mapping each TaskStatus to its valid target set
  - `TERMINAL_STATES`: {completed, failed, timeout, canceled} — no outgoing transitions
  - `validate_transition(from, to) -> bool`: checks against ALLOWED_TRANSITIONS
  - `TransitionRequest` dataclass: task_id, from_status, to_status, reason, timestamp
  - `TransitionResult` dataclass: success, reason, event_id
  - `apply_transition(db, request) -> TransitionResult`:
    1. Validates FSM transition
    2. If invalid: writes dead-letter, returns failure
    3. If valid: atomic `db.transaction()` with CAS + event insert
    4. If CAS fails: writes dead-letter, returns failure
    5. If CAS succeeds: returns success with event_id
  - Canceled reachable from ALL 5 non-terminal states (queued, assigned, blocked, running, review)
  - LOC: 164

### Task 2: Dead-letter stream
- **Files:** `src/router/dead_letter.py`, `src/router/db.py` (schema update)
- **Commit:** `feat: add dead-letter stream for rejected FSM transitions`
- **Details:**
  - `dead_letter_events` table added to schema: dl_id, task_id, attempted_from, attempted_to, reason, original_payload (JSON), ts
  - `write_dead_letter(db, task_id, from_status, to_status, reason, payload) -> dl_id`
  - `get_dead_letters(db, task_id, limit) -> list[dict]`: query with optional task_id filter
  - `count_dead_letters(db) -> int`: total count for monitoring
  - Dead-letter writes are independent commits (not rolled back with FSM transaction)
  - LOC: 105

### Task 3: FSM + dead-letter tests
- **Files:** `tests/router/test_fsm.py`
- **Commit:** `test: add FSM transition guard and dead-letter stream tests`
- **Details:** 9 tests, all passing
  - LOC: 310

## Test Results

```
tests/router/test_fsm.py::test_valid_transitions PASSED
tests/router/test_fsm.py::test_invalid_transitions PASSED
tests/router/test_fsm.py::test_terminal_states_immutable PASSED
tests/router/test_fsm.py::test_apply_transition_success PASSED
tests/router/test_fsm.py::test_apply_transition_invalid PASSED
tests/router/test_fsm.py::test_apply_transition_concurrent PASSED
tests/router/test_fsm.py::test_dead_letter_written PASSED
tests/router/test_fsm.py::test_dead_letter_query PASSED
tests/router/test_fsm.py::test_canceled_from_any_non_terminal PASSED

9 passed in 0.15s
```

Existing DB tests: 12/12 still passing (no regression from schema change).

## Verification Checklist

- [x] `python -m pytest tests/router/test_fsm.py -v` all green (9/9)
- [x] Every valid transition from CONTEXT.md FSM table works (`test_valid_transitions`)
- [x] Every invalid transition is rejected with dead letter (`test_apply_transition_invalid`, `test_invalid_transitions`)
- [x] CAS concurrent modification produces dead letter (`test_apply_transition_concurrent`)
- [x] Terminal states are truly terminal — no outgoing transitions (`test_terminal_states_immutable`)
- [x] Canceled reachable from all 5 non-terminal states (`test_canceled_from_any_non_terminal`)
- [x] Existing test_db.py: 12/12 still passing (no regression)

## Metrics

- **New LOC (production):** 269 (fsm.py: 164, dead_letter.py: 105)
- **Schema change:** +12 lines in db.py (dead_letter_events table + index)
- **New LOC (tests):** 310
- **Test count:** 9 new + 12 existing = 21 total
- **Test duration:** 0.15s (fsm) + 0.13s (db) = 0.28s total
- **3 atomic commits**
