# Plan 02-01 Summary: Worker Registry + Heartbeat + Stale Detection

## Status: COMPLETED

## What was built

### Worker Manager (`src/router/worker_manager.py` — 235 LOC)
- Token-authenticated registration with configurable expiry
- Account uniqueness enforcement (CCS: one active profile per worker, includes stale)
- Atomic registration inside BEGIN IMMEDIATE transaction
- Deregistration with active task requeue (uses shared `requeue_task()`)
- Worker status FSM: offline→idle→busy→stale with CAS transitions

### Heartbeat Manager (`src/router/heartbeat.py` — 232 LOC)
- `receive_heartbeat()`: Updates last_heartbeat, recovers stale workers to idle
- `run_stale_sweep()`: Marks workers stale after threshold, requeues their tasks
- `requeue_task()`: Shared helper used by heartbeat, worker_manager, and retry modules
- Ghost execution prevention: heartbeat response tells stale/offline workers to abort

### Model & DB Updates
- `Worker` model: added `idle_since`, `stale_since` fields
- `Task` model: added `not_before` field for backoff scheduling
- 6 new DB methods: `update_worker`, `list_stale_candidates`, `find_worker_by_account`, `list_worker_leases`, `update_task_fields`, `list_queued_tasks`

## Tests

- `test_worker_manager.py`: 14 tests (registration, deregistration, status transitions, token rotation)
- `test_heartbeat.py`: 14 tests (heartbeat receive, stale sweep, requeue_task helper)
- **28 new tests, all passing**

## Metrics

| Metric | Value |
|--------|-------|
| Production LOC | 467 (worker_manager: 235, heartbeat: 232) |
| Test LOC | 365 (test_worker_manager: 187, test_heartbeat: 178) |
| Models modified | 2 (Worker, Task) |
| DB methods added | 6 |
| Commits | 1 |
| Regression | 61/61 green |

## Key decisions
- `idle_since` is nullable (set to None when worker goes busy)
- Shared `requeue_task()` in heartbeat.py avoids duplication across modules
- Token list supports multiple concurrent tokens for zero-downtime rotation
