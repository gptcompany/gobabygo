# Plan 03-01 Summary: Communication Hierarchy Policy Enforcement

## Status: COMPLETED

## What was built

### Communication Policy Module (`src/router/comms.py` — 68 LOC)
- `HIERARCHY_EDGES` directed graph: BOSS->PRESIDENT, PRESIDENT->WORKER/BOSS, WORKER->PRESIDENT
- `CommunicationPolicy` stateless class with 6 validation methods:
  1. `can_create_task()` — Only boss/president can create tasks
  2. `can_dispatch_task()` — Only president dispatches to workers
  3. `can_ack_task()` — Only assigned worker can acknowledge
  4. `can_complete_task()` — Only assigned worker can report completion
  5. `can_view_all_tasks()` — Boss/president full visibility, workers scoped
  6. `validate_communication()` — Validates sender->receiver edge in hierarchy

### Model Updates (`src/router/models.py`)
- `CommunicationRole` str enum: boss, president, worker
- `Worker.role` field (default: "worker")
- `Task.created_by` field (nullable, tracks creator identity)

### Schema Updates (`src/router/db.py`)
- `role TEXT NOT NULL DEFAULT 'worker'` column in workers table
- `created_by TEXT` column in tasks table
- `_task_from_row` and `_worker_from_row` updated for new fields
- `insert_task` and `insert_worker` SQL updated for new columns

## Tests

- `test_comms.py`: 20 tests across 7 test classes
  - `TestCanCreateTask`: 3 tests (boss/president allowed, worker blocked)
  - `TestCanDispatchTask`: 3 tests (president allowed, worker/boss blocked)
  - `TestCanAckTask`: 3 tests (assigned worker, wrong worker, unassigned)
  - `TestCanCompleteTask`: 2 tests (assigned worker, wrong worker)
  - `TestValidateCommunication`: 6 tests (3 valid edges, 3 blocked: lateral, skip)
  - `TestCanViewAllTasks`: 3 tests (boss/president view all, worker scoped)
- **20 new tests, all passing**

## Metrics

| Metric | Value |
|--------|-------|
| Production LOC | 68 (comms.py) + model/schema changes |
| Test LOC | 170 (test_comms.py) |
| Models modified | 2 (Worker: +role, Task: +created_by) |
| New module | 1 (comms.py) |
| Commits | 2 (feat + test) |
| Total tests | 112 (92 existing + 20 new) |
| Regression | 92/92 existing green, 0 failures |

## Key decisions
- `CommunicationPolicy` is stateless (no DB dependency) — pure policy engine, easy to test
- Role stored as string value in model fields, matching existing pattern (TaskStatus, CLIType)
- Boss cannot dispatch directly — must go through president (strict hierarchy enforcement)
- Worker lateral communication blocked (worker->worker edge does not exist)
- No existing function signatures modified — fully additive changes
