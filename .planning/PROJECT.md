# AI Mesh Network (KISS Implementation)

## What This Is

A distributed multi-agent orchestration system that coordinates AI CLI workers (Claude, Codex, Gemini) across VPN-connected machines and repositories. A hybrid architecture uses Claude Agent Teams for strategic coordination (BOSS/PRESIDENT) and an external router/scheduler with SQLite persistence as the authoritative execution state. Supports cross-repo thread orchestration with automatic context propagation, per-step error handling, and full audit trail. Built for a solo developer operating from MacBook, with VPS as control plane and Workstation as execution node.

## Core Value

Reliable, deterministic task orchestration across distributed AI workers — router/DB is the single source of truth, not terminal state.

## Current State

**Last shipped:** v1.3 Cross-Repo Orchestration (2026-03-04)
**Test suite:** 548 tests, all passing
**Production LOC:** 6,742 Python
**Tech stack:** Python 3.11+, SQLite (WAL), Pydantic, CloudEvents, prometheus-client
**Current focus:** Planning next milestone

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
- Long-polling transport for worker task dispatch — v1.1
- Worker auto-reregister on unknown_worker heartbeat response — v1.1
- Buffer replay trigger mechanism (periodic + on-next-emit) — v1.1
- Smart watchdog with DB health check — v1.1
- check_review_timeout integration into periodic event loop — v1.1
- Operator CLI (meshctl) for status inspection and worker drain — v1.1
- WorkerManager validation on registration — v1.1
- YAML semantic mapping for gsd:implement-* commands — v1.1
- Automatic dispatch loop (periodic daemon thread) — v1.2
- POST /tasks HTTP endpoint with TaskCreateRequest DTO — v1.2
- meshctl submit command — v1.2
- Real CLI invocation via subprocess (dry-run + failure semantics) — v1.2
- Result persistence with secret sanitization and 32KB truncation — v1.3
- GET /tasks/{id} and GET /tasks?status=... read endpoints — v1.3
- Thread model: ordered task groups with cross-repo context propagation — v1.3
- Thread step chaining via depends_on with automatic context injection — v1.3
- meshctl thread CLI (create, add-step, status, context) — v1.3
- Per-step on_failure policies (abort/skip/retry with backoff) — v1.3
- Full audit trail per step (input, output, timestamps, worker, repo) — v1.3

### Active

None — v1.3 complete

### Out of Scope

- GUI/dashboard — CLI-first, operator uses iTerm2 panes for visibility
- Multi-VPS / multi-region — single VPS is accepted SPOF for MVP
- OpenClaw lane queues — deferred unless performance requires it
- Worker-to-worker direct communication — strict hierarchy enforced by design
- Auto-scaling workers — fixed worker pool, manual provisioning
- Offline mode — VPN-connected design assumes network availability
- WebSocket/SSE transport — long-polling sufficient for current worker count
- /metrics auth — WireGuard-only network provides sufficient isolation

## Milestones

<details>
<summary>v1.0 MVP — SHIPPED 2026-02-19</summary>

6 phases, 15 plans, 291 tests, 3829 LOC.
Router core, worker lifecycle, communication, event bridge, deployment, monitoring.

</details>

<details>
<summary>v1.1 Production Readiness — SHIPPED 2026-02-21</summary>

4 phases, 9 plans, 404 tests.
Long-polling, self-healing, smart watchdog, operator CLI (meshctl).

</details>

<details>
<summary>v1.2 Operational Readiness — SHIPPED 2026-02-23</summary>

3 phases, 3 plans, 436 tests.
Dispatch loop, POST /tasks, CLI invocation.

</details>

<details>
<summary>v1.3 Cross-Repo Orchestration — SHIPPED 2026-03-04</summary>

3 phases, 4 plans, 548 tests (+2560 lines).
Result persistence, thread model, on_failure policies, audit trail, E2E cross-repo.

</details>

## Context

### Infrastructure (operational)
- **VPS**: WireGuard VPN tunnel to Workstation (keepalive 25s)
- **Workstation**: Docker with DOCKER-USER iptables hardening, VictoriaMetrics + Netdata + Grafana monitoring
- **MacBook**: iTerm2 operator terminal (not source of truth)
- **Networking**: autossh tunnel VPS, UFW firewall, verify-network.sh + mesh checks
- **Alerting**: 7 base rules + 5 mesh-specific rules, Grafana Cloud -> Discord

### Known Issues / Tech Debt
- session_spawner.py exists but unused in production (sessions are worker-owned by design)
- on_failure uses string-vs-enum comparisons (safe due to str-Enum, minor inconsistency)
- /metrics endpoint has no auth (acceptable: WireGuard-only network)

## Constraints

- **Transport**: VPN-only between VPS and Workstation (WireGuard)
- **Persistence**: SQLite v1 (no Postgres dependency)
- **Security**: Worker registration token (rotatable), per-worker state dirs (~/.mesh/agents/<worker_id>/)
- **Account isolation**: One active Claude account profile per concurrent worker
- **Heartbeat timing**: 5s interval, 35s stale threshold (WireGuard keepalive-aware)
- **Dispatch latency SLO**: p95 < 3s
- **Task success SLO**: >= 95% (excluding deterministic test failures)
- **Result size**: 32KB max per task result (truncation + hard fallback)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Hybrid model: Agent Teams (strategic) + External router (execution) | Agent Teams strongest for BOSS/PRESIDENT coordination; external router needed for cross-CLI + VPN reliability | Good |
| Router/DB as single source of truth (not GSD/tmux state) | Avoids split-brain; deterministic recovery after crash | Good |
| SQLite over Postgres for v1 | Minimal dependency, sufficient for single-VPS topology | Good |
| GSD as tracking layer, not orchestrator | GSD provides workflow UX; router FSM is transition authority | Good |
| Auto-instrumentation + YAML mapping (not per-command hardcoding) | High coverage with low maintenance; new commands auto-tracked | Good |
| Stale threshold 35s (not 20s) | Must exceed WireGuard keepalive 25s to avoid false stale detection | Good |
| stdlib ThreadingHTTPServer (no Flask/uvicorn) | Zero external deps for HTTP serving, sufficient for MVP | Good |
| Transport adapter pattern: InProcess + HTTP | Clean separation for test vs production | Good |
| SHA-256 idempotency keys (not built-in hash()) | Stable across Python sessions | Good |
| Dispatch loop drains all tasks per cycle | Maximizes throughput; CAS prevents double-dispatch | Good |
| TaskCreateRequest DTO separates public/internal fields | Prevents clients from setting status/assigned_worker | Good |
| Eager dispatch on POST /tasks (best-effort) | Reduces latency; dispatch loop is backup | Good |
| subprocess.run without shell=True | Prevents command injection; safe for user prompts | Good |
| result_json as inline TEXT column (not separate table) | YAGNI; single table query for task + result | Good |
| Sanitize + persist result in same DB transaction | Atomicity: no partial state on crash | Good |
| Secret patterns filtered via regex before persistence | Lightweight, no external deps (sk-, ghp_, xoxb-) | Good |
| Thread step = normal Task row with thread_id | Reuses dependency.py, scheduler, FSM — no parallel system | Good |
| thread_context as runtime enrichment (not persisted) | Computed on-the-fly from completed steps; no stale data | Good |
| Sessions are worker-owned (not router-managed) | Router stays stateless for tmux; workers own their runtime | Good |
| on_failure per-step (not per-thread) | Granular control; each step can have different policy | Good |
| Retry uses existing RetryPolicy for backoff | No new backoff implementation; consistent with task retry | Good |
| No /metrics auth | WireGuard-only network, same as /health | Revisit |

---
*Last updated: 2026-03-04 after v1.3 milestone*
