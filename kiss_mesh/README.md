# AI Mesh Network (KISS Implementation)

Canonical entrypoint for this project.

## Single-File Ingestion (Important)
If you are doing cross-check/review with Claude Code (or another CLI), ingest **only this file** first.

Then open extra docs only if needed, in this order:
1. `CC_ANALYSIS_V2.md`
2. `EXECUTIVE_ONE_PAGER_TEAM_ORCHESTRATION.md`
3. `CONSOLIDATED_TEAM_ORCHESTRATION_GUIDE.md`
4. `KISS_IMPLEMENTATION_SPEC_V1.md`
5. `GSD_TRACKING_LAYER_MAPPING.md`
6. `ROBUST_AUTO_MAPPING_STRATEGY.md`

## Final Architecture Decision
Hybrid model:
1. `Claude Agent Teams` for strategic layer (`BOSS`, `PRESIDENT`, optional `CLAUDE_REVIEWER`).
2. External router/scheduler + DB on VPS as authoritative execution state.
3. Workers on workstation for execution (`codex`, `gemini`, `claude-exec`, `claude-review`).
4. MacBook + iTerm2 for operations only (not source of truth).

## Topology
```text
MacBook (iTerm2 ops)
  -> SSH over VPN
VPS
  - Agent Teams strategic session
  - Router/Scheduler
  - DB + event log
  -> VPN dispatch
Workstation
  - worker-codex-impl
  - worker-gemini-research
  - worker-claude-exec
  - worker-claude-review
```

## Non-Negotiables
1. Authoritative state is router/DB, not tmux/iTerm2 state.
2. Strict hierarchy by default:
   - `BOSS -> PRESIDENT`
   - `PRESIDENT -> WORKERS`
   - `WORKERS -> PRESIDENT`
   - `PRESIDENT <-> VERIFIER`
   - no worker-to-worker free chat (temporary peer channel allowed only per-task with TTL).
3. One active Claude account profile per worker (`target_account` required).
4. Every task/event must have idempotency key.
5. Verifier/review gate required before terminal completion for critical changes.

## Task & State Model (Canonical)
Task states:
- `queued`, `assigned`, `blocked`, `running`, `review`, `completed`, `failed`, `timeout`, `canceled`

Required routing fields:
- `task_id`, `phase`, `target_cli`, `target_account`, `depends_on[]`, `attempt`, `lease_expires_at`, `idempotency_key`

## Reliability Baseline
1. Heartbeat interval: `5s`.
2. Stale worker threshold: `35s` (WireGuard-aware).
3. Lease expiry => requeue (`attempt+1`).
4. Bounded retries: `3` (`15s`, `60s`, `180s`).
5. Idempotent finalization + append-only event log.

## GSD Integration (Tracking Layer)
Two-layer model:
1. GSD commands (`/pipeline:gsd`, `/gsd:*`) = workflow UX + step tracking.
2. Router/DB = canonical orchestration state for multi-CLI + VPN.

Scope guard (important):
- GSD is a tracking/integration layer, not the execution runtime.
- Session workers + router/DB are the execution truth for interactive multi-CLI operation.
- GSD integration must not block runtime fixes (session persistence, message bus, iTerm2 operator control).
- Runtime realignment work (`S0`/`S1`) is still tracked inside the GSD roadmap/governance; sequencing changes execution order, not ownership/tracking.

Implementation direction:
- auto instrumentation for all commands (`started/completed/failed`),
- rule-based semantic mapping (YAML),
- small override set for critical commands,
- router FSM as final transition authority.

## Current Canonical Docs
- `CC_ANALYSIS_V2.md`
- `EXECUTIVE_ONE_PAGER_TEAM_ORCHESTRATION.md`
- `CONSOLIDATED_TEAM_ORCHESTRATION_GUIDE.md`
- `KISS_IMPLEMENTATION_SPEC_V1.md`
- `CHANGELOG_RUNTIME_REALIGNMENT.md`
- `SOTA_MULTI_AGENT_ORCHESTRATION_2026.md`
- `GSD_TRACKING_LAYER_MAPPING.md`
- `ROBUST_AUTO_MAPPING_STRATEGY.md`
- `CLAUDE_MULTI_ACCOUNT_SOURCES_CCS.md`
- `FINDINGS_REVERSE_ENGINEERING_AGENT_TEAMS.md`
- `FINDINGS_CLAUDE_FLOW_OPENCLAW_FIT.md`


## Legacy PoC Code (Archived)
Moved out of root to avoid confusion:
- `archive/mock-mesh/router_poc.py`
- `archive/mock-mesh/agent_poc.py`
- `archive/mock-mesh/setup_mesh_poc.sh`
- `archive/mock-mesh/autogen_topology_poc.py`

These files are historical mock/simulation scaffolding only, not production control-plane code.

## Archived (Superseded) Docs
Moved to `archive/` to reduce noise:
- `archive/ARCHITECTURE_BOSS_VPS_WORKERS_WS.md`
- `archive/FINDINGS_AGENT_TEAMS_MULTI_CLI.md`
- `archive/FINDINGS_AGENT_TEAMS_OFFICIAL_VS_COMMUNITY.md`
- `archive/OPENCLAW_PATTERNS_ADAPTATION.md`

## Immediate Next Build Slice
Traceability note:
- The session-first runtime fixes are a GSD-tracked milestone/workstream (not a parallel undocumented fork).
- Runtime implementation lives in router/session-worker code; GSD continues to provide milestone/status tracking and later semantic event enrichment.

1. **S0 Runtime Realignment (session-first)**: interactive session workers (`claude`, `codex`) + router-persisted session bus + iTerm2 operator attach flow.
2. Stabilize session streaming/attach workflows and operator controls (`open/send/read/close`, manual intervention path).
3. Keep `TaskPhase` runtime phases unchanged (`plan|implement|test|integrate|release`) to avoid mixing orchestration semantics with roadmap milestones.
4. **G1 GSD Integration (after S0/S1 runtime stability)**: auto event emitter + mapping rules + critical overrides.
