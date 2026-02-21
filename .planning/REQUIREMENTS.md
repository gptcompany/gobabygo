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
- [ ] **RESL-04**: Smart watchdog performs periodic DB health check (WAL size, integrity, disk space) and escalates on failure
- [x] **RESL-05**: check_review_timeout runs in periodic event loop to detect stale task reviews

### Operator CLI

- [ ] **OPSC-01**: `meshctl status` shows worker states (idle/busy/stale), queue depth, and running tasks with age
- [ ] **OPSC-02**: `meshctl drain <worker_id>` stops new task assignment to a worker, lets current task finish, then retires
- [ ] **OPSC-03**: `meshctl` communicates with router via existing HTTP API (no new transport)

### Tech Debt

- [x] **DEBT-01**: server._handle_register() validates through WorkerManager (not just global bearer token)
- [x] **DEBT-02**: YAML semantic mapping includes rules for gsd:implement-* commands
- [x] **DEBT-03**: heartbeat.py Worker type annotation properly imported (mypy clean)

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
| RESL-04 | Phase 9 | Pending |
| RESL-05 | Phase 9 | Complete |
| OPSC-01 | Phase 10 | Pending |
| OPSC-02 | Phase 10 | Pending |
| OPSC-03 | Phase 10 | Pending |
| DEBT-01 | Phase 7 | Done |
| DEBT-02 | Phase 7 | Done |
| DEBT-03 | Phase 7 | Done |

**Coverage:**
- v1.1 requirements: 14 total
- Mapped to phases: 14
- Unmapped: 0

---
*Requirements defined: 2026-02-20*
*Last updated: 2026-02-20 -- traceability updated with phase mappings*
