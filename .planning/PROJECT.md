# AI Mesh Network (KISS Implementation)

## What This Is

A distributed multi-agent orchestration system that coordinates AI CLI workers (Claude, Codex, Gemini) across VPN-connected machines. A hybrid architecture uses Claude Agent Teams for strategic coordination (BOSS/PRESIDENT) and an external router/scheduler with SQLite persistence as the authoritative execution state. Built for a solo developer operating from MacBook, with VPS as control plane and Workstation as execution node.

## Core Value

Reliable, deterministic task orchestration across distributed AI workers — router/DB is the single source of truth, not terminal state.

## Current Milestone: v1.1 Production Readiness

**Goal:** Make the mesh reliable enough for daily unattended production operation and provide operator CLI tooling.

**Target features:**
- Long-polling transport (replace 2s short-polling)
- Self-healing workers (auto-reregister on unknown_worker)
- Deterministic event replay triggers (periodic + on-next-emit)
- Smart watchdog (DB health check in watchdog thread)
- Operator CLI (`meshctl status`, `meshctl drain`)
- Critical tech debt fixes (register validation, YAML mapping, mypy)

## Current State

**Shipped:** v1.0 MVP (2026-02-19)
**Codebase:** 3,829 LOC production + 4,313 LOC tests (Python)
**Test suite:** 294 tests, all passing
**Tech stack:** Python 3.11+, SQLite (WAL), Pydantic, CloudEvents, prometheus-client

## Requirements

### Validated

- Router with SQLite persistence + crash recovery — v1.0
- FSM transition guard with dead-letter stream — v1.0
- Worker registry with heartbeat (5s), stale detection (35s) — v1.0
- Deterministic scheduler (target_cli -> target_account -> oldest idle) — v1.0
- Bounded retry (3 attempts, 15s/60s/180s backoff, BOSS escalation) — v1.0
- Idempotent task finalization via CAS — v1.0
- Mandatory VERIFIER gate for critical changes — v1.0
- Hierarchical communication (BOSS->PRESIDENT->WORKERS->PRESIDENT) — v1.0
- Event bridge: CloudEvent emitter, NDJSON, JSON Schema — v1.0
- Rule-based YAML semantic mapping — v1.0
- systemd units for router and workers — v1.0
- Mesh monitoring alerts (RouterDown, WorkerStale, QueueDepthHigh, NoData, FailureRate) — v1.0
- One active account per worker (CCS isolation) — v1.0
- Append-only event log with idempotency key dedup — v1.0
- Fallback buffer with replay on reconnect — v1.0

### Active

- Long-polling transport for worker task dispatch
- Worker auto-reregister on unknown_worker heartbeat response
- Buffer replay trigger mechanism (periodic + on-next-emit)
- Smart watchdog with DB health check
- check_review_timeout integration into periodic event loop
- Operator CLI (meshctl) for status inspection and worker drain
- Fix server._handle_register() to use WorkerManager validation
- Complete YAML semantic mapping for gsd:implement-* commands
- Fix heartbeat.py Worker type annotation for mypy

### Out of Scope

- GUI/dashboard — CLI-first, operator uses iTerm2 panes for visibility
- Multi-VPS / multi-region — single VPS is accepted SPOF for MVP
- OpenClaw lane queues — deferred unless performance requires it
- Worker-to-worker direct communication — strict hierarchy enforced by design
- Auto-scaling workers — fixed worker pool, manual provisioning
- Offline mode — VPN-connected design assumes network availability

## Context

### Infrastructure (operational)
- **VPS**: WireGuard VPN tunnel to Workstation (keepalive 25s)
- **Workstation**: Docker with DOCKER-USER iptables hardening, VictoriaMetrics + Netdata + Grafana monitoring
- **MacBook**: iTerm2 operator terminal (not source of truth)
- **Networking**: autossh tunnel VPS, UFW firewall, verify-network.sh + mesh checks
- **Alerting**: 7 base rules + 5 mesh-specific rules, Grafana Cloud -> Discord

### Known Issues / Tech Debt
- `/tasks/ack` HTTP endpoint — FIXED in v1.0 post-release (commit 7e9d605)
- `server._handle_register()` bypasses WorkerManager validation — targeted for v1.1
- YAML mapping incomplete for `gsd:implement-*` — targeted for v1.1
- `heartbeat.py` Worker type annotation — targeted for v1.1
- Worker short-polling (2s) — long-polling targeted for v1.1
- Buffer replay trigger mechanism — targeted for v1.1

## Constraints

- **Transport**: VPN-only between VPS and Workstation (WireGuard)
- **Persistence**: SQLite v1 (no Postgres dependency)
- **Security**: Worker registration token (rotatable), per-worker state dirs (~/.mesh/agents/<worker_id>/)
- **Account isolation**: One active Claude account profile per concurrent worker
- **Heartbeat timing**: 5s interval, 35s stale threshold (WireGuard keepalive-aware)
- **Dispatch latency SLO**: p95 < 3s
- **Task success SLO**: >= 95% (excluding deterministic test failures)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Hybrid model: Agent Teams (strategic) + External router (execution) | Agent Teams strongest for BOSS/PRESIDENT coordination; external router needed for cross-CLI + VPN reliability | Good |
| Router/DB as single source of truth (not GSD/tmux state) | Avoids split-brain; deterministic recovery after crash | Good |
| SQLite over Postgres for v1 | Minimal dependency, sufficient for single-VPS topology | Good |
| GSD as tracking layer, not orchestrator | GSD provides workflow UX; router FSM is transition authority | Good |
| Auto-instrumentation + YAML mapping (not per-command hardcoding) | High coverage with low maintenance; new commands auto-tracked | Good |
| Stale threshold 35s (not 20s) | Must exceed WireGuard keepalive 25s to avoid false stale detection | Good |
| PoC code archived, fresh implementation | PoC had syntax errors, in-memory only — not suitable as base | Good |
| stdlib ThreadingHTTPServer (no Flask/uvicorn) | Zero external deps for HTTP serving, sufficient for MVP | Good |
| prometheus-client for metrics (not manual text format) | OpenMetrics compliant, standard | Good |
| Summary for task duration (not SQLite percentile) | Efficient, standard approach | Good |
| Transport adapter pattern: InProcess + HTTP | Clean separation for test vs production | Good |
| SHA-256 idempotency keys (not built-in hash()) | Stable across Python sessions | Good |
| Recovery uses direct CAS (not FSM) | Recovery transitions outside FSM table, needs atomic compound ops | Good |
| Worker short-polling 2s (not long-polling) | Simple, deferred long-polling to v2 | Revisit |
| No /metrics auth | WireGuard-only network, same as /health | Revisit |

---
*Last updated: 2026-02-20 after v1.1 milestone started*
