# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-18)

**Core value:** Reliable, deterministic task orchestration across distributed AI workers — router/DB is the single source of truth.
**Current focus:** Phase 5 — Deployment (COMPLETE)

## Current Position

Phase: 5 of 6 (Deployment)
Plan: 05-02 COMPLETED (all 2 plans done)
Status: Phase 05 execution complete, verified, confidence gate passed (90/100)
Last activity: 2026-02-19 — Plans 05-01, 05-02 completed

Progress: ████████████████░░ ~83%

## Performance Metrics

**Velocity:**
- Total plans completed: 13
- Average duration: ~10 min
- Total execution time: ~2.5 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-router-core | 3/3 | ~33m | ~11m |
| 02-worker-lifecycle | 3/3 | ~27m | ~9m |
| 03-communication-verification | 2/2 | ~21m | ~10m |
| 04-event-bridge | 3/3 | ~20m | ~7m |
| 05-deployment | 2/2 | ~15m | ~7m |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Hybrid model: Agent Teams (strategic) + External router (execution)
- Router/DB as single source of truth (not GSD/tmux state)
- SQLite over Postgres for v1
- Stale threshold 35s (WireGuard keepalive-aware)
- Recovery uses direct CAS (not FSM) for recovery-only transitions
- Heartbeat transport: HTTP POST /heartbeat
- Escalation: Event + callback (configurable hooks, e.g. Discord webhook)
- Worker auth: Token-based with expiry and rotation support
- Shared requeue_task() helper in heartbeat.py (used by heartbeat, worker_manager, retry)
- Scheduler compound ops use direct CAS (not apply_transition) to avoid nested transaction issues
- Communication hierarchy: BOSS→PRESIDENT→WORKERS→PRESIDENT enforced via CommunicationPolicy
- Task criticality: explicit flag from BOSS/PRESIDENT (not phase-based)
- Rejection workflow: creates fix task with parent_task_id linkage, 3 max rejections → escalation → failed
- Peer channel: deferred to v2 (strict hierarchy sufficient for MVP)
- CommunicationPolicy integration: enforced at event bridge boundary (emitter validates sender_role)
- Transport adapter pattern: InProcessTransport (dev/test) + HttpTransport (VPN production)
- SHA-256 idempotency keys: stable across Python sessions (not built-in hash())
- fcntl file locking: FallbackBuffer uses LOCK_EX for concurrency safety
- FK disabled in InProcessTransport: bridge events may arrive before tasks exist
- YAML rule ordering: more specific rules (remediation) before generic ones (plan)
- **HTTP server: stdlib ThreadingHTTPServer (no Flask/uvicorn for MVP simplicity)**
- **Mesh router port: 8780 (configurable via MESH_ROUTER_PORT env)**
- **SQLite check_same_thread=False for HTTP server threading**
- **systemd Type=notify with sd_notify READY=1 + WATCHDOG=1 (10s interval, 30s timeout)**
- **Worker short-polling (2s interval): long-polling deferred to v2**
- **uv for Python venv management (both VPS and Workstation)**
- **Data paths: /var/lib/mesh-router/ (DB), /etc/mesh-router/ (config), ~/.mesh/ (worker state)**
- **Dedicated service users: mesh (router), mesh-worker (workers)**

### Completed Plans

- **01-01**: SQLite persistence layer — 12 tests, 3 commits, ~400 LOC production + 206 LOC tests
- **01-02**: FSM transition guard + dead-letter stream — 9 tests, 3 commits, 269 LOC production + 310 LOC tests
- **01-03**: Crash recovery + dependency resolution — 12 tests, 2 commits, 404 LOC production + 421 LOC tests
- **02-01**: Worker registry + heartbeat + stale detection — 28 tests, 1 commit, 467 LOC production + 365 LOC tests
- **02-02**: Deterministic scheduler — 17 tests, 1 commit, 249 LOC production + 201 LOC tests
- **02-03**: Bounded retry policy + escalation — 14 tests, 1 commit, 194 LOC production + 177 LOC tests
- **03-01**: Communication policy enforcement — 20 tests, 3 commits, 74 LOC production + 170 LOC tests
- **03-02**: Verifier gate + rejection workflow — 24+5 tests, 4 commits, 200 LOC production + 314 LOC tests
- **04-01**: Event emitter + transport + schema — 31 tests, 557 LOC production (bridge total)
- **04-02**: YAML mapping engine — 23 tests, included in 557 LOC
- **04-03**: Fallback buffer + integration — 18 tests, included in 557 LOC
- **05-01**: HTTP server + worker client + systemd units — 32 tests, ~400 LOC production + 420 LOC tests
- **05-02**: Infrastructure scripts + deploy config — 19 tests, 4 shell scripts + systemd units + env templates

### Test Summary

| Phase | Tests |
|-------|-------|
| Phase 1 | 33 |
| Phase 2 | 59 |
| Phase 3 | 49 |
| Phase 4 | 72 |
| Phase 5 | 51 |
| **Total** | **264** |

### Pending Todos

- check_review_timeout integration into periodic event loop (Phase 6 or v2)
- Buffer replay trigger mechanism (on-next-emit or periodic) (v2)
- Smart watchdog: health check DB in watchdog thread (v2)
- Worker auto-reregister on heartbeat "unknown_worker" response (v2)

### Blockers/Concerns

- HIGH: Mesh monitoring/alerts assenti (from cross-validated report) — addressed in Phase 6
- NOTE: Duration_ms tracking requires caller-side state management (optional field)

## Session Continuity

Last session: 2026-02-19
Stopped at: Phase 05 complete, ready for Phase 06
Resume file: .planning/phases/05-deployment/CONTEXT.md
