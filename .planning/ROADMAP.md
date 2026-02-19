# Roadmap: AI Mesh Network

## Overview

From zero to a production-usable distributed orchestration system in 6 phases. Phase 1 builds the router core (persistence + FSM), Phase 2 adds worker lifecycle management, Phase 3 wires up hierarchical communication and the verifier gate, Phase 4 bridges GSD commands into router events, Phase 5 deploys as systemd services, and Phase 6 closes the loop with mesh monitoring. Each phase delivers a self-contained, testable capability.

## Domain Expertise

None (custom distributed systems architecture — informed by project research docs in `kiss_mesh/`)

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

- [x] **Phase 1: Router Core** — SQLite persistence, FSM guard, event log, crash recovery
- [x] **Phase 2: Worker Lifecycle** — Registry, heartbeat, stale detection, scheduler, retry
- [ ] **Phase 3: Communication & Verification** — Hierarchical comms, verifier gate, review transitions
- [ ] **Phase 4: Event Bridge** — GSD->Router emitter, semantic mapping, fallback buffer
- [ ] **Phase 5: Deployment** — systemd units, UFW rules, boot order, infra prep
- [ ] **Phase 6: Monitoring & Hardening** — Mesh alerts, Grafana Cloud integration, health probes

## Phase Details

### Phase 1: Router Core
**Goal**: Persistent router with SQLite-backed task/event/worker/lease tables, FSM transition guard, idempotent finalization, and crash recovery (expired leases requeued on startup)
**Depends on**: Nothing (first phase)
**Requirements**: ROUTER-01, ROUTER-02, ROUTER-03, ROUTER-04
**Research**: Unlikely (SQLite + FSM are established patterns, schemas defined in KISS spec)
**Plans**: TBD

Plans:
- [ ] 01-01: SQLite schema + migration (tasks, task_events, workers, leases tables)
- [ ] 01-02: FSM transition guard + dead-letter handling
- [ ] 01-03: Recovery logic (lease expiry requeue, event replay, idempotent finalize)

### Phase 2: Worker Lifecycle
**Goal**: Worker registration with heartbeat monitoring, stale detection, deterministic scheduling, bounded retry with backoff, and account isolation enforcement
**Depends on**: Phase 1
**Requirements**: WORKER-01, WORKER-02, SCHED-01, SCHED-02
**Research**: Unlikely (patterns defined in KISS spec, heartbeat/lease well-understood)
**Plans**: TBD

Plans:
- [ ] 02-01: Worker registry + heartbeat receiver + stale detection
- [ ] 02-02: Deterministic scheduler (target_cli -> target_account -> oldest idle) + account uniqueness
- [ ] 02-03: Bounded retry policy (3 attempts, 15s/60s/180s backoff, BOSS escalation)

### Phase 3: Communication & Verification
**Goal**: Enforce hierarchical communication policy (BOSS->PRESIDENT->WORKERS), implement mandatory VERIFIER gate with review state transitions, and temporary peer channel with TTL
**Depends on**: Phase 2
**Requirements**: COMMS-01, VERIFY-01
**Research**: Likely (Agent Teams integration for BOSS/PRESIDENT coordination)
**Research topics**: Claude Agent Teams API for strategic layer, how to bridge Agent Teams task list with router dispatch
**Plans**: TBD

Plans:
- [ ] 03-01: Communication policy enforcement (allowed edges, peer channel TTL)
- [ ] 03-02: Verifier gate (review state, approval/rejection workflow, remediation task creation)

### Phase 4: Event Bridge
**Goal**: Auto-emitter that wraps GSD commands into CloudEvent envelopes, NDJSON transport with JSON Schema validation, rule-based YAML mapping for semantic inference, and fallback buffer with replay
**Depends on**: Phase 1, Phase 3
**Requirements**: BRIDGE-01, BRIDGE-02, BRIDGE-03
**Research**: Likely (CloudEvents spec, NDJSON framing patterns)
**Research topics**: CloudEvents Python SDK, JSON Schema Draft 2020-12 for event validation, NDJSON streaming patterns
**Plans**: TBD

Plans:
- [ ] 04-01: Event emitter (CloudEvent envelope, NDJSON transport, JSON Schema validation)
- [ ] 04-02: YAML mapping engine (command_rules.yaml + command_overrides.yaml)
- [ ] 04-03: Fallback buffer (.mesh/tasks-buffer.jsonl) + replay on reconnect

### Phase 5: Deployment
**Goal**: Production systemd units for router (VPS) and workers (Workstation), UFW rules for mesh port, boot order documentation, and verify-network.sh mesh checks
**Depends on**: Phase 2, Phase 4
**Requirements**: DEPLOY-01
**Research**: Unlikely (systemd patterns established in existing infrastructure)
**Plans**: TBD

Plans:
- [ ] 05-01: systemd units (mesh-router.service VPS, mesh-worker-*.service WS template)
- [ ] 05-02: Infrastructure prep (UFW mesh port, verify-network.sh, boot order runbook)

### Phase 6: Monitoring & Hardening
**Goal**: Mesh-specific Prometheus alert rules (RouterDown, WorkerStale, QueueDepthHigh), Grafana Cloud "no data" alert, metrics export (queue depth, task success rate, p95 duration, stale workers, retry rate)
**Depends on**: Phase 5
**Requirements**: MONITOR-01
**Research**: Unlikely (Grafana + VictoriaMetrics stack already operational)
**Plans**: TBD

Plans:
- [ ] 06-01: Mesh alert rules + Grafana Cloud "no data" configuration
- [ ] 06-02: Metrics export (queue depth, success rate, p95 duration, stale count, retry rate)

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Router Core | 3/3 | Done | 2026-02-18 |
| 2. Worker Lifecycle | 3/3 | Done | 2026-02-18 |
| 3. Communication & Verification | 0/2 | Not started | - |
| 4. Event Bridge | 0/3 | Not started | - |
| 5. Deployment | 0/2 | Not started | - |
| 6. Monitoring & Hardening | 0/2 | Not started | - |
