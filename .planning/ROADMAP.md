# Roadmap: AI Mesh Network

## Milestones

- v1.0 MVP -- Phases 1-6 (shipped 2026-02-19)
- v1.1 Production Readiness -- Phases 7-10 (in progress)

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

### v1.1 Production Readiness

- [x] **Phase 7: Tech Debt Cleanup** - Fix register validation bypass, complete YAML mapping, resolve mypy annotation
- [ ] **Phase 8: Long-Polling Transport** - Replace 2s short-polling with server-held long-poll for worker task dispatch
- [x] **Phase 9: Self-Healing Resilience** - Auto-reregister, buffer replay triggers, smart watchdog, review timeout detection
- [x] **Phase 10: Operator CLI** - meshctl tool for status inspection and worker drain operations (completed 2026-02-21)

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
