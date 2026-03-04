# Roadmap: AI Mesh Network

## Milestones

- v1.0 MVP -- Phases 1-6 (shipped 2026-02-19)
- v1.1 Production Readiness -- Phases 7-10 (shipped 2026-02-21)
- v1.2 Operational Readiness -- Phases 11-13 (shipped 2026-02-23)
- v1.3 Cross-Repo Orchestration -- Phases 14-16 (in progress)

## Phases

<details>
<summary>v1.0 MVP (Phases 1-6) -- SHIPPED 2026-02-19</summary>

- [x] Phase 1: Router Core (3/3 plans) -- completed 2026-02-18
- [x] Phase 2: Worker Lifecycle (3/3 plans) -- completed 2026-02-18
- [x] Phase 3: Communication & Verification (2/2 plans) -- completed 2026-02-19
- [x] Phase 4: Event Bridge (3/3 plans) -- completed 2026-02-19
- [x] Phase 5: Deployment (2/2 plans) -- completed 2026-02-19
- [x] Phase 6: Monitoring & Hardening (2/2 plans) -- completed 2026-02-19

Full details: `.planning/milestones/v1.0-ROADMAP.md`

</details>

### v1.1 Production Readiness -- SHIPPED 2026-02-21

- [x] **Phase 7: Tech Debt Cleanup** - Fix register validation bypass, complete YAML mapping, resolve mypy annotation
- [x] **Phase 8: Long-Polling Transport** - Replace 2s short-polling with server-held long-poll for worker task dispatch
- [x] **Phase 9: Self-Healing Resilience** - Auto-reregister, buffer replay triggers, smart watchdog, review timeout detection
- [x] **Phase 10: Operator CLI** - meshctl tool for status inspection and worker drain operations

### v1.2 Operational Readiness

- [x] **Phase 11: Dispatch Loop** - Periodic dispatch daemon thread auto-assigns queued tasks to idle workers
- [x] **Phase 12: POST /tasks** - HTTP task submission endpoint with TaskCreateRequest DTO, meshctl submit
- [x] **Phase 13: CLI Invocation** - Real subprocess execution, dry-run mode, guaranteed failure semantics

## Phase Details

### Phase 7: Tech Debt Cleanup
**Goal**: Codebase is clean and correct before adding new features -- no validation bypasses, no incomplete mappings, no type errors
**Depends on**: Phase 6 (v1.0 complete)
**Requirements**: DEBT-01, DEBT-02, DEBT-03
**Success Criteria** (what must be TRUE):
  1. Worker registration request with invalid/missing fields is rejected by WorkerManager validation (not just bearer token check)
  2. A `gsd:implement-plan` command emitted through the event bridge resolves to the correct semantic event type via YAML mapping
  3. `mypy src/` passes with zero errors on heartbeat.py Worker type annotation
**Plans**: TBD

Plans:
- [x] 07-01: Register validation via WorkerManager
- [x] 07-02: YAML mapping + mypy fix

### Phase 8: Long-Polling Transport
**Goal**: Workers receive task assignments with minimal latency via server-held connections, eliminating wasteful 2s polling
**Depends on**: Phase 7
**Requirements**: TRNS-01, TRNS-02, TRNS-03
**Success Criteria** (what must be TRUE):
  1. Worker's poll request blocks on the server until a task is available or timeout expires (no repeated 2s polls visible in logs)
  2. When a new task is dispatched to a waiting worker, the worker receives it within 1 second (Condition wakeup, not next poll cycle)
  3. Long-poll timeout is configurable (environment variable or config), defaults to 30s, and worker gracefully reconnects after timeout with no error state
  4. Dispatch latency p95 remains under 3s SLO
**Plans**: 2 plans

Plans:
- [x] 08-01-PLAN.md — Long-poll registry, server handler, and Prometheus metrics
- [x] 08-02-PLAN.md — Scheduler wakeup, lifecycle hooks, and worker client reconnect

### Phase 9: Self-Healing Resilience
**Goal**: The mesh recovers from transient failures without operator intervention -- workers re-register, events replay, watchdog catches DB corruption, stale reviews are detected
**Depends on**: Phase 8
**Requirements**: RESL-01, RESL-02, RESL-03, RESL-04, RESL-05
**Success Criteria** (what must be TRUE):
  1. When a worker's heartbeat receives "unknown_worker" (e.g., after router restart), the worker automatically re-registers and resumes normal operation without manual restart
  2. Buffered events are replayed on a periodic timer (default 60s configurable) and the buffer drains when connectivity is restored
  3. When the next event emits successfully after a buffered period, all previously buffered events are replayed immediately (on-next-emit trigger)
  4. Watchdog thread checks DB health (WAL size, integrity_check, disk space) on each cycle and escalates via alerting if any check fails
  5. Stale task reviews (tasks stuck in review state beyond timeout) are detected and escalated by the periodic event loop
**Plans**: 3 plans

Plans:
- [x] 09-01-PLAN.md — Auto-reregister on unknown_worker heartbeat + stale review timeout scheduling
- [x] 09-02-PLAN.md — Buffer replay timer with exponential backoff + on-next-emit drain trigger
- [x] 09-03-PLAN.md — Watchdog DB health checks (WAL size, integrity, disk space) with Prometheus metrics

### Phase 10: Operator CLI
**Goal**: Operator can inspect mesh state and perform graceful worker management from the terminal without directly hitting HTTP endpoints
**Depends on**: Phase 9
**Requirements**: OPSC-01, OPSC-02, OPSC-03
**Success Criteria** (what must be TRUE):
  1. `meshctl status` displays a table of all workers (id, state: idle/busy/stale), current queue depth, and running tasks with their age in human-readable format
  2. `meshctl drain <worker_id>` marks a worker as draining (no new tasks assigned), waits for current task to finish, then retires the worker cleanly
  3. meshctl communicates exclusively via the existing router HTTP API (no new transport, no direct DB access, no new ports)
**Plans**: 2 plans

Plans:
- [x] 10-01-PLAN.md — Server-side endpoints (GET /workers, POST /drain) + draining FSM state
- [x] 10-02-PLAN.md — meshctl CLI (argparse, status/drain commands, auth, error handling)

### Phase 11: Dispatch Loop
**Goal**: Tasks inserted in DB are automatically assigned to idle workers via periodic dispatch daemon thread
**Depends on**: Phase 10
**Requirements**: OPRDY-01, OPRDY-02
**Success Criteria** (what must be TRUE):
  1. Daemon thread drains all dispatchable tasks per cycle
  2. Interval configurable via `MESH_DISPATCH_INTERVAL_S` (default 5s)
  3. Prometheus metrics track dispatch cycles and tasks dispatched
  4. E2E test works without manual `scheduler.dispatch()` call
**Plans**: 1 plan

Plans:
- [x] 11-01-PLAN.md — Dispatch loop thread, metrics, tests, E2E update

### Phase 12: POST /tasks
**Goal**: Tasks submitted via HTTP POST with clean public API, duplicate detection, eager dispatch, and meshctl submit
**Depends on**: Phase 11
**Requirements**: OPRDY-03, OPRDY-04, OPRDY-05
**Success Criteria** (what must be TRUE):
  1. `POST /tasks` accepts TaskCreateRequest DTO and returns 201 with task_id
  2. Internal fields (status, assigned_worker) set server-side, client values ignored
  3. Duplicate idempotency_key returns 409
  4. Eager dispatch assigns immediately when worker available
  5. `meshctl submit` creates tasks via HTTP
**Plans**: 1 plan

Plans:
- [x] 12-01-PLAN.md — POST /tasks handler, TaskCreateRequest, meshctl submit, tests

### Phase 13: CLI Invocation
**Goal**: Workers execute tasks by invoking real CLI subprocess with dry-run mode and guaranteed failure semantics
**Depends on**: Phase 12
**Requirements**: OPRDY-06, OPRDY-07, OPRDY-08
**Success Criteria** (what must be TRUE):
  1. `_execute_task` validates `payload.prompt` and invokes CLI subprocess
  2. Dry-run mode logs command without executing
  3. Every error scenario reports failure (no task stuck in 'running')
  4. CLI command template supports `{account_profile}` interpolation
  5. Output truncated (4KB stdout, 2KB stderr)
  6. All config from env vars
**Plans**: 1 plan

Plans:
- [x] 13-01-PLAN.md — Rewrite _execute_task, dry-run, failure semantics, config from env

### v1.3 Cross-Repo Orchestration

- [x] **Phase 14: Result Persistence + Read Path** - Persist worker results server-side, add GET /tasks/{id} and GET /tasks?status=...
- [x] **Phase 15: Thread Model + Cross-Repo Context** - Thread as ordered task group with runtime `thread_context`, worker-owned session runtime, meshctl thread CLI
- [ ] **Phase 16: Aggregator + Error Handling** - Fan-in aggregation, per-step on_failure (skip/retry/abort), E2E cross-repo test

### Phase 14: Result Persistence + Read Path
**Goal**: Il router persiste i result che i worker gia' inviano, e li rende leggibili via API.
**Depends on**: Phase 13
**Requirements**: RPER-01, RPER-02, RPER-03, RPER-04, RPER-05
**Success Criteria** (what must be TRUE):
  1. `POST /tasks/complete` con `result: {...}` persiste result in DB (stessa transazione che cambia stato)
  2. `POST /tasks/complete` senza result continua a funzionare (backward compatible)
  3. Result persistito anche su transition a `review` (task critici) -- stessa transazione
  4. `GET /tasks/{id}` ritorna task completo con result
  5. `GET /tasks?status=completed` lista task filtrati per status
  6. Result > 32KB viene troncato con `_truncated: true`; se ancora fuori limite, fallback compatto `_hard_truncated: true`
**Plans**: 1 plan

Plans:
- [x] 14-01-PLAN.md -- Result field, DB migration, sanitization, scheduler persistence, GET endpoints

### Phase 15: Thread Model + Cross-Repo Context
**Goal**: Thread come gruppo di task con contesto condiviso cross-repo. Costruito sopra Task + dependency.py esistenti.
**Depends on**: Phase 14
**Requirements**: THRD-01, THRD-02, THRD-03, THRD-04, THRD-05
**Success Criteria** (what must be TRUE):
  1. `meshctl thread create --name "..."` crea thread
  2. `meshctl thread add-step` aggiunge step come Task con thread_id, step_index, repo
  3. Per step `session`, il runtime interattivo resta worker-owned; il router non duplica sessioni tmux
  4. Step usano `depends_on` esistente -- dependency.py li sblocca automaticamente
  5. Al dispatch di step N+1, i `result` precedenti sono esposti come `thread_context` top-level (runtime enrichment, non mutazione del payload)
  6. `meshctl thread context {name}` mostra result aggregati
  7. `meshctl thread status {name}` mostra tabella con stato per step
**Plans**: 2 plans

Plans:
- [x] 15-01-PLAN.md -- Thread model, thread endpoints, meshctl thread CLI, context aggregation
- [x] 15-02-PLAN.md -- Follow-up implementation pass and verification of Phase 15 runtime hooks

### Phase 16: Aggregator + Error Handling
**Goal**: Aggregazione automatica dei risultati e gestione errori nei thread.
**Depends on**: Phase 15
**Requirements**: AGGR-01, AGGR-02, AGGR-03, AGGR-04
**Success Criteria** (what must be TRUE):
  1. Thread 3-step cross-repo esegue E2E senza copia-incolla
  2. Step fallito con `on_failure: retry` viene ri-eseguito (max 3 tentativi)
  3. Step fallito con `on_failure: skip` non blocca thread
  4. `meshctl thread status` mostra tabella leggibile con risultati per step
  5. Audit trail completo in DB: ogni step ha input, output, timestamps, worker, repo
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 7 -> 8 -> 9 -> 10

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Router Core | v1.0 | 3/3 | Done | 2026-02-18 |
| 2. Worker Lifecycle | v1.0 | 3/3 | Done | 2026-02-18 |
| 3. Communication & Verification | v1.0 | 2/2 | Done | 2026-02-19 |
| 4. Event Bridge | v1.0 | 3/3 | Done | 2026-02-19 |
| 5. Deployment | v1.0 | 2/2 | Done | 2026-02-19 |
| 6. Monitoring & Hardening | v1.0 | 2/2 | Done | 2026-02-19 |
| 7. Tech Debt Cleanup | v1.1 | 2/2 | Done | 2026-02-20 |
| 8. Long-Polling Transport | v1.1 | 2/2 | Done | 2026-02-20 |
| 9. Self-Healing Resilience | v1.1 | 3/3 | Done | 2026-02-21 |
| 10. Operator CLI | v1.1 | 2/2 | Done | 2026-02-21 |
| 11. Dispatch Loop | v1.2 | 1/1 | Done | 2026-02-23 |
| 12. POST /tasks | v1.2 | 1/1 | Done | 2026-02-23 |
| 13. CLI Invocation | v1.2 | 1/1 | Done | 2026-02-23 |
| 14. Result Persistence | v1.3 | 1/1 | Done | 2026-03-03 |
| 15. Thread + Cross-Repo | v1.3 | 2/2 | Done | 2026-03-04 |
| 16. Aggregator + Error | v1.3 | 0/? | Ready | - |
