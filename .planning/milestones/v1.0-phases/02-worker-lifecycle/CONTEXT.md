# Phase 02: Worker Lifecycle — Context

## Phase Goal

Worker registration with heartbeat monitoring, stale detection, deterministic scheduling, bounded retry with backoff, and account isolation enforcement.

## Requirements Covered

- **WORKER-01**: Worker registry with heartbeat (5s), stale detection (35s WireGuard-aware), automatic lease requeue
- **WORKER-02**: One active Claude account profile per concurrent worker (CCS isolation, reject duplicate registration)
- **SCHED-01**: Deterministic scheduler routes by target_cli -> target_account -> oldest idle worker
- **SCHED-02**: Bounded retry: max 3 attempts, backoff 15s/60s/180s, escalation to BOSS on exhaust

## Dependencies

- Phase 1 (Router Core) COMPLETED: SQLite persistence, FSM guard, crash recovery, dependency resolution
- 33 tests passing, ~1,073 LOC production, ~937 LOC tests

## Existing Code to Build On

| Module | Location | Provides |
|--------|----------|----------|
| `models.py` | `src/router/models.py` | Worker, Lease, Task, TaskEvent Pydantic models |
| `db.py` | `src/router/db.py` | RouterDB with CRUD for workers, leases, tasks, events + CAS |
| `fsm.py` | `src/router/fsm.py` | FSM transition guard + apply_transition() |
| `dead_letter.py` | `src/router/dead_letter.py` | Dead-letter stream for failed transitions |
| `recovery.py` | `src/router/recovery.py` | Crash recovery (expired lease requeue, orphan detection) |
| `dependency.py` | `src/router/dependency.py` | Event-driven dependency resolution |

## User Decisions

### 1. Heartbeat Transport: HTTP Endpoint
- `POST /heartbeat` on the router
- Worker sends heartbeat every 5s with `worker_id` + health payload
- Router updates `last_heartbeat` in workers table
- Stale detection via periodic sweep (check `now() - last_heartbeat > 35s`)

### 2. Escalation: Event + Callback
- After 3 failed retry attempts, emit `escalation_to_boss` event
- Invoke configurable callback/hook (e.g., Discord webhook)
- Callback interface: `EscalationCallback` protocol with `on_escalation(task, worker, attempts)` method
- Default implementation: log-only; pluggable for Discord/webhook alerts

### 3. Worker Registration Token: Full Implementation
- Token rotatable with expiry
- `POST /register` requires `Authorization: Bearer <token>` header
- Token stored in router config (not DB — config file or env var)
- Token rotation: new token + grace period for old token
- Reject registration if token invalid/expired

### 4. Pipeline: Full Execution
- discuss -> plan -> confidence gate -> execute -> verify -> validate -> confidence gate

## Architecture Decisions

### Worker Status FSM
```
offline -> idle (registration + valid token)
idle -> busy (task assigned via scheduler)
busy -> idle (task completed/failed/timeout, set idle_since=now)
idle -> stale (heartbeat timeout > 35s, set stale_since=now)
busy -> stale (heartbeat timeout > 35s, set stale_since=now)
stale -> idle (heartbeat resumes or re-registration, set idle_since=now)
idle -> offline (explicit /deregister, frees account_profile)
busy -> offline (explicit /deregister, requeues active tasks first)
stale -> offline (explicit /deregister or admin)
```

### Scheduler Algorithm (Deterministic)
```
1. Filter workers: cli_type == task.target_cli
2. Filter workers: account_profile == task.target_account (if specified)
3. Filter workers: status == 'idle'
4. Sort by: idle_since ASC (longest idle = fairest round-robin)
5. Select first match
6. If no match: task stays queued
```
Note: Worker model gets new `idle_since` field (TEXT, ISO-8601 UTC). Updated when worker transitions to `idle` (registration, task completion, stale recovery). This avoids the flawed `last_heartbeat` sort which would select workers closest to stale threshold instead of longest-idle.

### Account Uniqueness Enforcement
- On `register_worker`: query `workers` table for `account_profile` WHERE `status IN ('idle', 'busy', 'stale')`
- Include `stale` in check: a stale worker may recover, so its account profile must remain reserved
- If match found AND `worker_id != existing.worker_id`: REJECT with 409 Conflict
- Allows re-registration of same worker_id (e.g., after restart)
- **Fast re-registration**: If registering with same `worker_id` but stale/offline status, immediately transition to `idle` (no 35s wait)
- **Force-deregister**: `/deregister` endpoint allows explicit teardown, freeing the account profile immediately for new worker registration

### Retry with Backoff
- Backoff schedule: `[15, 60, 180]` seconds (indexed by attempt-1)
- Implementation: `not_before` timestamp on requeued task
- Scheduler skips tasks where `not_before > now()`
- After attempt 3 exhausted: `failed` + escalation event + callback

### Stale Detection Sweep
- Router runs periodic sweep (configurable interval, default 10s)
- Queries: `SELECT * FROM workers WHERE status IN ('idle', 'busy') AND last_heartbeat < now() - 35s`
- For each stale worker:
  1. Set `worker.status = 'stale'`, record `stale_since` timestamp
  2. Find assigned tasks with active leases
  3. Expire leases, requeue tasks (respecting retry limits)
  4. Emit `worker_stale` event

### Ghost Execution Prevention (Heartbeat Response)
- `POST /heartbeat` returns status in response body: `{"status": "ok"}` or `{"status": "stale", "requeued_tasks": [...]}`
- When worker receives `stale` response, it MUST abort any running tasks (they've been requeued)
- Worker then re-sends heartbeat to transition `stale -> idle` and request fresh work
- This prevents ghost executions where a stale worker continues working on already-reassigned tasks

### Task Scheduling Timeout (Un-schedulable Tasks)
- Track `queued_since` on tasks (already exists as `created_at`)
- If task remains `queued` for > configurable TTL (default: 30 min), emit `task_unschedulable` event
- v1: event only (monitoring/alerting), no auto-action
- Future: could auto-cancel or widen target_cli/account constraints

### Thundering Herd Mitigation
- v1 has fixed pool of 3-5 workers, so thundering herd is negligible
- Mitigation for future scale: workers add jitter to poll interval (5s +/- random 0-2s)
- Alternative for v2: switch to long-polling or push-based dispatch
- Documented as v2 concern, no implementation needed in Phase 2

### HTTP API (Phase 2 scope)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/register` | POST | Worker registration with auth token |
| `/heartbeat` | POST | Worker heartbeat update |
| `/tasks/next` | POST | Worker requests next task (scheduler invoked) |
| `/tasks/{id}/ack` | POST | Worker acknowledges task (assigned -> running) |
| `/tasks/{id}/complete` | POST | Worker reports task completion |
| `/tasks/{id}/fail` | POST | Worker reports task failure |
| `/deregister` | POST | Explicit worker deregistration (frees account profile) |

### New Files to Create
| File | Purpose |
|------|---------|
| `src/router/scheduler.py` | Deterministic scheduling + account uniqueness |
| `src/router/heartbeat.py` | Heartbeat receiver + stale detection sweep |
| `src/router/worker_manager.py` | Registration, auth token, worker status FSM |
| `src/router/retry.py` | Bounded retry policy + backoff + escalation callback |
| `src/router/api.py` | HTTP endpoints (FastAPI/Flask lightweight) |
| `tests/router/test_scheduler.py` | Scheduler tests |
| `tests/router/test_heartbeat.py` | Heartbeat + stale detection tests |
| `tests/router/test_worker_manager.py` | Registration + auth tests |
| `tests/router/test_retry.py` | Retry + escalation tests |

## Constraints

- SQLite v1 (no Postgres)
- VPN-only transport (WireGuard)
- Heartbeat interval: 5s
- Stale threshold: 35s (WireGuard keepalive 25s + 10s margin)
- Max retry attempts: 3
- Backoff: 15s / 60s / 180s
- Dispatch latency SLO: p95 < 3s
- Task success SLO: >= 95%
- Concurrency: 1 task per worker (v1)

## Risks

| Risk | Mitigation |
|------|------------|
| HTTP server adds dependency | Use lightweight framework (stdlib http.server or uvicorn minimal) |
| Token rotation during active workers | Grace period: accept both old and new token for configurable window |
| Stale sweep race with heartbeat | Use transactions, CAS on worker status |
| Scheduler contention under load | Single-threaded dispatch loop (v1), lock-free CAS for status |

---
*Created: 2026-02-18 during /pipeline:gsd 02 discuss phase*
