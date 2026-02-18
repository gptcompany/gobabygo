# AI Mesh Network (KISS Implementation)

## What This Is

A distributed multi-agent orchestration system that coordinates AI CLI workers (Claude, Codex, Gemini) across VPN-connected machines. A hybrid architecture uses Claude Agent Teams for strategic coordination (BOSS/PRESIDENT) and an external router/scheduler with SQLite persistence as the authoritative execution state. Built for a solo developer operating from MacBook, with VPS as control plane and Workstation as execution node.

## Core Value

Reliable, deterministic task orchestration across distributed AI workers — router/DB is the single source of truth, not terminal state.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Router with SQLite persistence: `tasks`, `task_events`, `workers`, `leases` tables with crash recovery
- [ ] FSM transition guard enforcing canonical state machine (queued -> assigned -> blocked -> running -> review -> completed/failed/timeout/canceled)
- [ ] Worker registry with heartbeat (5s), stale detection (35s WireGuard-aware), automatic lease requeue
- [ ] Deterministic scheduler: route by `target_cli` -> `target_account` -> oldest idle worker
- [ ] Bounded retry policy: max 3 attempts, backoff 15s/60s/180s, escalation to BOSS on exhaust
- [ ] Idempotent task finalization (no double completion)
- [ ] Mandatory VERIFIER gate before terminal completion on critical changes
- [ ] Hierarchical communication policy: BOSS->PRESIDENT->WORKERS->PRESIDENT, no worker-to-worker (except temporary peer channel with TTL)
- [ ] Event bridge: auto-emitter for GSD commands -> router events (CloudEvent envelope, NDJSON, JSON Schema validation)
- [ ] Rule-based semantic mapping (YAML) for GSD command -> router state transitions
- [ ] systemd units for router (VPS) and workers (Workstation)
- [ ] Mesh-specific monitoring alerts: RouterDown, WorkerStale, QueueDepthHigh
- [ ] One active Claude account profile per worker (CCS isolation)
- [ ] Append-only event log with idempotency key deduplication
- [ ] Fallback buffer (.mesh/tasks-buffer.jsonl) when router unreachable, with replay on reconnect

### Out of Scope

- GUI/dashboard (v1) — CLI-first, operator uses iTerm2 panes for visibility
- Multi-VPS / multi-region — single VPS is accepted SPOF for MVP
- OpenClaw lane queues — deferred to Phase C if needed
- Worker-to-worker direct communication by default — strict hierarchy enforced
- Auto-scaling workers — fixed worker pool, manual provisioning

## Context

### Infrastructure (already operational)
- **VPS**: WireGuard VPN tunnel to Workstation (keepalive 25s)
- **Workstation**: Docker with DOCKER-USER iptables hardening, VictoriaMetrics + Netdata + Grafana monitoring
- **MacBook**: iTerm2 operator terminal (not source of truth)
- **Networking**: autossh tunnel VPS, UFW firewall, verify-network.sh checks
- **Alerting**: 7 base alert rules (CPU/Memory/Disk/NodeDown/ProcessDown), Grafana Cloud -> Discord

### Research completed
- 10+ analysis documents cross-validated across 3 sessions
- 21 architecture issues tracked: 15 CLOSED, 4 ACCEPTED, 2 OPEN (monitoring gaps)
- PoC code archived (not production base): router_poc.py, agent_poc.py
- Claude Agent Teams reverse-engineered, OpenClaw patterns adapted
- Multi-account model via CCS documented

### Rollout plan (from KISS spec)
- **Phase A**: Task + worker schema, routing, heartbeat, leases, retries, idempotent finalize
- **Phase B**: Verifier gate, operator commands (list/reassign/cancel/retry), basic metrics
- **Phase C**: iTerm2 automation profile, dashboards/alerts, optional OpenClaw lane queues

## Constraints

- **Transport**: VPN-only between VPS and Workstation (WireGuard)
- **Persistence**: SQLite v1 (no Postgres dependency for MVP)
- **Security**: Worker registration token (rotatable), per-worker isolated state dirs (~/.mesh/agents/<worker_id>/)
- **Account isolation**: One active Claude account profile per concurrent worker
- **Heartbeat timing**: 5s interval, 35s stale threshold (WireGuard keepalive-aware)
- **Dispatch latency SLO**: p95 < 3s
- **Task success SLO**: >= 95% (excluding deterministic test failures)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Hybrid model: Agent Teams (strategic) + External router (execution) | Agent Teams strongest for BOSS/PRESIDENT coordination; external router needed for cross-CLI + VPN reliability | -- Pending |
| Router/DB as single source of truth (not GSD/tmux state) | Avoids split-brain; deterministic recovery after crash | -- Pending |
| SQLite over Postgres for v1 | Minimal dependency, sufficient for single-VPS topology | -- Pending |
| GSD as tracking layer, not orchestrator | GSD provides workflow UX; router FSM is transition authority | -- Pending |
| Auto-instrumentation + YAML mapping (not per-command hardcoding) | High coverage with low maintenance; new commands auto-tracked | -- Pending |
| Stale threshold 35s (not 20s) | Must exceed WireGuard keepalive 25s to avoid false stale detection | -- Pending |
| PoC code archived, fresh implementation | PoC had syntax errors, in-memory only, fragile framing — not suitable as base | -- Pending |

---
*Last updated: 2026-02-18 after initialization*
