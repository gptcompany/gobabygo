# Phase 10: Operator CLI - Context

## Goal

Operator can inspect mesh state and perform graceful worker management from the terminal without directly hitting HTTP endpoints.

## Success Criteria

1. `meshctl status` displays a table of all workers (id, state, idle/busy/stale), current queue depth, and running tasks with their age in human-readable format
2. `meshctl drain <worker_id>` marks a worker as draining (no new tasks assigned), waits for current task to finish, then retires the worker cleanly
3. meshctl communicates exclusively via the existing router HTTP API (no new transport, no direct DB access, no new ports)

## Requirements

- OPSC-01: meshctl status
- OPSC-02: meshctl drain
- OPSC-03: HTTP API only

## Current State Analysis

### Existing HTTP API

| Endpoint | Method | Returns |
|----------|--------|---------|
| /health | GET | worker_count, queue_depth, uptime_s |
| /metrics | GET | Prometheus text |
| /tasks/next | GET | Long-poll for task (worker use) |
| /events | POST | CloudEvent ingestion |
| /heartbeat | POST | Worker heartbeat |
| /register | POST | Worker registration |
| /tasks/ack | POST | Task acknowledgment |
| /tasks/complete | POST | Task completion |
| /tasks/fail | POST | Task failure |

### What Exists for meshctl status (OPSC-01)
- `/health` returns aggregate counts but NOT per-worker detail
- `db.list_workers()` returns all workers with full fields
- `db.count_all_task_statuses()` returns status breakdown
- `db.get_tasks_by_worker(worker_id, status)` returns tasks per worker
- Worker model has: worker_id, machine, cli_type, status, last_heartbeat, idle_since, stale_since

**Gap**: No HTTP endpoint returns per-worker details. Need GET /workers and GET /workers/<id>/tasks.

### What Exists for meshctl drain (OPSC-02)
- Worker deregistration exists: `WorkerManager.deregister_worker()` → sets offline, requeues tasks
- Worker FSM: offline, idle, busy, stale (no "draining" state)
- Scheduler only dispatches to "idle" workers

**Gap**: No "draining" state, no drain endpoint. Need:
- Add `draining` boolean to workers table (or new state)
- Scheduler must skip draining workers
- Drain endpoint sets flag, waits for tasks to complete, then deregisters

### Design Decision: Drain Implementation

**Option A**: Add "draining" state to FSM
- Pros: Clean FSM, explicit state
- Cons: Touches models, FSM, worker_manager transitions, scheduler, tests everywhere

**Option B**: Add `draining` boolean column to DB
- Pros: Minimal FSM changes, scheduler just checks flag
- Cons: Separate from status field

**Recommended: Option A** — Add `draining` to WORKER_TRANSITIONS:
- `idle → draining`, `busy → draining`
- Scheduler already filters `status == "idle"` → naturally skips draining
- Clean model: worker.status shows real operational state

## New Endpoints Needed

1. **GET /workers** → JSON list of all workers with status, task counts
2. **POST /workers/<id>/drain** → Initiate drain, returns when complete (or 202 + poll)
3. **GET /workers/<id>** → Single worker detail with running tasks

## Implementation Boundaries

- meshctl is a standalone CLI script (argparse, requests)
- No new dependencies beyond requests (already in requirements)
- No direct DB access from meshctl
- Same port, same auth (bearer token)
- Human-readable table output (no external table library — format with f-strings)

## Recommended Plan Structure

2 plans:
1. **10-01**: Server-side endpoints (GET /workers, GET /workers/<id>, POST /workers/<id>/drain) + draining state
2. **10-02**: meshctl CLI (argparse, status command, drain command, auth, error handling)
