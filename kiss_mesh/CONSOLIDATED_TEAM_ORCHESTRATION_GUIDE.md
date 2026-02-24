# Consolidated Team Orchestration Guide (Hybrid Agent Teams + Multi-CLI Workers)

Updated: 2026-02-18
Scope: definitive practical model for your use case.

## 1) Final decision (consolidated)
Use a **hybrid model**:
- `Claude Agent Teams` for strategic coordination (`BOSS` + `PRESIDENT` + optional `CLAUDE_REVIEWER`).
- External control plane (`router + queue + state`) for execution dispatch.
- CLI workers for execution (`codex`, `gemini`, `claude`) on workstation nodes.

This gives official Agent Teams coordination semantics where it is strongest, while preserving cross-CLI and VPN-distributed execution.

## 2) Canonical topology

```text
MacBook (iTerm2 operator)
  -> SSH over VPN
VPS
  - Claude Agent Teams session (Boss/President)
  - Router/Scheduler service (authoritative state)
  - SQLite/Postgres task store + event log
  -> VPN dispatch
Workstation
  - worker-codex-impl
  - worker-gemini-research
  - worker-claude-exec
  - worker-claude-review
```

## 3) Source of truth (critical)
To avoid split-brain:
- **Authoritative execution state = external queue/store**.
- Agent Teams task list is used for strategic planning and leadership coordination, not as execution truth for non-Claude workers.
- President mirrors strategic decisions into executable queue tasks via router API (`POST /tasks`).
- If router API is temporarily unreachable, President writes to local fallback buffer (`.mesh/tasks-buffer.jsonl`) and replays on reconnect.
- Router ACK (`run_id`, `task_id`) is authoritative for downstream scheduling and audit.

## 4) Role model

### Strategic layer
- `BOSS`: strategy, phase priorities, escalation approvals.
- `PRESIDENT`: decomposition, scheduling, assignment, retries, closure decisions.

### Execution layer
- `worker-codex-impl`: implementation/refactor heavy tasks.
- `worker-gemini-research`: research/fact-check/enrichment tasks.
- `worker-claude-exec`: Claude execution tasks.
- `worker-claude-review`: review/gate tasks.

### Quality gate
- `VERIFIER` (mandatory): test + policy + quality validation before final completion.

## 5) Communication policy

Allowed by default:
- `BOSS -> PRESIDENT`
- `PRESIDENT -> WORKER_*`
- `WORKER_* -> PRESIDENT`
- `PRESIDENT -> VERIFIER`
- `VERIFIER -> PRESIDENT`

Blocked by default:
- `WORKER <-> WORKER`
- `WORKER -> BOSS`

Exception:
- President can open a temporary peer channel for a specific `task_id` and limited TTL.

## 6) Task lifecycle (shared structured model)

Phases:
- `plan -> implement -> test -> integrate -> release`

States:
- `queued -> assigned -> blocked -> running -> review -> completed`
- terminal failures: `failed | timeout | canceled`

Required fields:
- `task_id`, `phase`, `payload`
- `target_cli`, `target_account`
- `depends_on[]`
- `attempt`, `lease_expires_at`, `idempotency_key`
- `status`, `assigned_worker`

## 7) Persistence semantics

Important distinction:
- `it2`/`tmux` persistence = terminal UX continuity.
- real orchestration persistence = DB + event log + worker lease model + systemd restart.

Minimum reliability primitives:
- heartbeat every 5s
- stale worker after 35s without heartbeat (WireGuard-aware)
- lease expiry requeue (`attempt + 1`)
- idempotent finalize (no double completion)

## 8) Multi-account model

For Claude workers:
- one active worker per account profile.
- keep `target_account` in task routing.
- do not share one account profile across concurrent long-lived workers.

Recommended mechanism:
- CCS as primary runtime profile isolation.

## 9) Operational model (MacBook + iTerm2)

iTerm2 is control/visibility, not control-plane truth.

Recommended panes:
- VPS: router logs + queue status + heartbeat monitor.
- WS: each worker service log.
- optional dashboard/metrics pane.

System must continue if MacBook/iTerm2 disconnects.

## 10) Minimal implementation order

1. Router schema with `target_cli`, `target_account`, `depends_on`, `idempotency_key`.
2. Worker registry + heartbeat + lease logic.
3. Deterministic scheduler (`cli -> account -> idle oldest`).
4. Retry policy (max 3, bounded backoff).
5. Verifier gate before final completion.
6. systemd units for router/workers.
7. Metrics and incident runbook.

Runtime realignment addendum (interactive sessions):
- Add `execution_mode=session|batch` to avoid dispatching interactive tasks to batch workers.
- Prioritize session workers (`claude`, `codex`) + persisted session bus before GSD tracking integration.
- Keep GSD as tracking/integration workstream after runtime behavior matches the real operator workflow.

## 11) Read order (recommended)
1. `README.md`
2. `CC_ANALYSIS_V2.md`
3. `KISS_IMPLEMENTATION_SPEC_V1.md`
4. `CLAUDE_MULTI_ACCOUNT_SOURCES_CCS.md`
5. `FINDINGS_REVERSE_ENGINEERING_AGENT_TEAMS.md`
6. `FINDINGS_CLAUDE_FLOW_OPENCLAW_FIT.md`

## 12) Final practical stance
- Start simple and deterministic.
- Keep hierarchy strict.
- Use Agent Teams where native semantics are strongest.
- Use external scheduler/queue for cross-CLI execution and distributed reliability.

This is the most efficient and maintainable path for your environment.
