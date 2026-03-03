# Requirements: AI Mesh Network

**Defined:** 2026-02-20
**Core Value:** Reliable, deterministic task orchestration across distributed AI workers -- router/DB is the single source of truth.

## v1.1 Requirements

Requirements for v1.1 Production Readiness. Each maps to roadmap phases.

### Transport

- [x] **TRNS-01**: Worker receives task assignments via long-polling (server holds connection until task available or timeout)
- [x] **TRNS-02**: Server notifies waiting workers immediately when new task is dispatched (Condition-based wakeup)
- [x] **TRNS-03**: Long-poll timeout is configurable (default 30s) with graceful reconnect on timeout

### Resilience

- [x] **RESL-01**: Worker auto-reregisters when heartbeat receives "unknown_worker" response from router
- [x] **RESL-02**: Event buffer replays buffered events on periodic tick (configurable interval, default 60s)
- [x] **RESL-03**: Event buffer replays buffered events on next successful emit (on-next-emit trigger)
- [x] **RESL-04**: Smart watchdog performs periodic DB health check (WAL size, integrity, disk space) and escalates on failure
- [x] **RESL-05**: check_review_timeout runs in periodic event loop to detect stale task reviews

### Operator CLI

- [x] **OPSC-01**: `meshctl status` shows worker states (idle/busy/stale), queue depth, and running tasks with age
- [x] **OPSC-02**: `meshctl drain <worker_id>` stops new task assignment to a worker, lets current task finish, then retires
- [x] **OPSC-03**: `meshctl` communicates with router via existing HTTP API (no new transport)

### Tech Debt

- [x] **DEBT-01**: server._handle_register() validates through WorkerManager (not just global bearer token)
- [x] **DEBT-02**: YAML semantic mapping includes rules for gsd:implement-* commands
- [x] **DEBT-03**: heartbeat.py Worker type annotation properly imported (mypy clean)

## v1.3 Requirements

Requirements for v1.3 Cross-Repo Orchestration. Each maps to roadmap phases.

### Result Persistence

- [x] **RPER-01**: Task model has `result` field; DB has `result_json TEXT` column (backward-compatible migration)
- [x] **RPER-02**: `/tasks/complete` extracts result from body and persists in same transaction as state change (covers both completed and review paths)
- [x] **RPER-03**: `GET /tasks/{id}` returns full task with result
- [x] **RPER-04**: `GET /tasks?status=...` returns filtered task list
- [x] **RPER-05**: Result > 32KB truncated with `_truncated: true` flag; secret patterns (sk-, ghp_, xoxb-) filtered before persistence

### Thread Model

- [ ] **THRD-01**: Thread CRUD: create, get, list, status via HTTP API + meshctl
- [ ] **THRD-02**: Thread steps are normal Task rows with `thread_id`, `step_index`, `repo`, `role` fields
- [ ] **THRD-03**: Step chaining: on step N complete, result injected as context into step N+1 payload
- [ ] **THRD-04**: Session spawner: router creates tmux sessions on-demand when thread step activates (`POST /sessions/spawn`)
- [ ] **THRD-05**: `meshctl thread` CLI commands: create, add-step, status, context

### Aggregation & Error Handling

- [ ] **AGGR-01**: Fan-in aggregator reads thread results and produces summary
- [ ] **AGGR-02**: Per-step `on_failure` configuration: skip, retry (max 3), abort
- [ ] **AGGR-03**: E2E test: 3-step cross-repo thread executes without manual intervention
- [ ] **AGGR-04**: Full audit trail in DB: input, output, timestamps, worker, repo per step

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Metrics

- **METR-01**: Summary metric migrated to Histogram for multi-router percentile aggregation
- **METR-02**: /metrics endpoint supports optional bearer token auth for non-WireGuard deployments

### Scaling

- **SCAL-01**: Adaptive polling fallback (10-30s idle, 1-2s busy) as alternative to long-polling
- **SCAL-02**: Thread pool cap for long-poll connections to prevent thread exhaustion under heavy worker count

## Out of Scope

| Feature | Reason |
|---------|--------|
| WebSocket/SSE transport | Long-polling sufficient for current worker count; ThreadingHTTPServer limitation |
| Multi-router support | Single VPS topology, no horizontal scaling planned for v1.x |
| GUI dashboard | CLI-first design, meshctl provides operator visibility |
| Worker auto-scaling | Fixed worker pool, manual provisioning sufficient |
| /metrics auth | WireGuard-only network provides sufficient isolation |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| TRNS-01 | Phase 8 | Complete |
| TRNS-02 | Phase 8 | Complete |
| TRNS-03 | Phase 8 | Complete |
| RESL-01 | Phase 9 | Complete |
| RESL-02 | Phase 9 | Complete |
| RESL-03 | Phase 9 | Complete |
| RESL-04 | Phase 9 | Complete |
| RESL-05 | Phase 9 | Complete |
| OPSC-01 | Phase 10 | Complete |
| OPSC-02 | Phase 10 | Complete |
| OPSC-03 | Phase 10 | Complete |
| DEBT-01 | Phase 7 | Done |
| DEBT-02 | Phase 7 | Done |
| DEBT-03 | Phase 7 | Done |

**Coverage:**
- v1.1 requirements: 14 total
- Mapped to phases: 14
- Unmapped: 0

| RPER-01 | Phase 14 | Complete |
| RPER-02 | Phase 14 | Complete |
| RPER-03 | Phase 14 | Complete |
| RPER-04 | Phase 14 | Complete |
| RPER-05 | Phase 14 | Complete |
| THRD-01 | Phase 15 | Ready |
| THRD-02 | Phase 15 | Ready |
| THRD-03 | Phase 15 | Ready |
| THRD-04 | Phase 15 | Ready |
| THRD-05 | Phase 15 | Ready |
| AGGR-01 | Phase 16 | Blocked (15) |
| AGGR-02 | Phase 16 | Blocked (15) |
| AGGR-03 | Phase 16 | Blocked (15) |
| AGGR-04 | Phase 16 | Blocked (15) |

**Coverage:**
- v1.3 requirements: 14 total
- Mapped to phases: 14
- Unmapped: 0

---
*Requirements defined: 2026-02-20*
*Last updated: 2026-03-03 -- v1.3 requirements added*
