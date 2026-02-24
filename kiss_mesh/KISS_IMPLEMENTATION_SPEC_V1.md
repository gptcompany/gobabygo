# KISS Implementation Spec v1 (Executable Blueprint)

Updated: 2026-02-18
Scope: minimal, reliable implementation for your stack.

## Objective
Deliver a production-usable MVP with:
- persistent sessions/tasks
- shared structured task lifecycle
- hierarchical communication
- recovery after worker/router failures

Target runtime:
- VPS: `boss/president + state`
- Workstation: CLI workers
- MacBook: iTerm2 operations only

## 1) Roles and communication policy

### Roles
- `BOSS`: strategy, phase definition, escalation only
- `PRESIDENT`: scheduler, task graph owner, dispatch/retry/recovery
- `WORKER_*`: execution units (`claude|codex|gemini`)
- `VERIFIER`: mandatory quality gate before final completion

### Allowed communication edges (default)
- `BOSS -> PRESIDENT`
- `PRESIDENT -> WORKER`
- `WORKER -> PRESIDENT`
- `PRESIDENT -> VERIFIER`
- `VERIFIER -> PRESIDENT`

Disallowed by default:
- `WORKER <-> WORKER`
- `WORKER -> BOSS`

Exception path:
- PRESIDENT can open temporary `peer_channel` for a specific task_id and TTL.

## 2) Task schema (minimum)

```json
{
  "task_id": "uuid",
  "parent_task_id": "uuid|null",
  "phase": "plan|implement|test|integrate|release",
  "title": "short description",
  "payload": {"instruction": "..."},
  "target_cli": "claude|codex|gemini",
  "target_account": "work|clientA|clientB",
  "priority": 1,
  "deadline_ts": "ISO-8601|null",
  "depends_on": ["task_id", "..."],
  "status": "queued|assigned|blocked|running|review|completed|failed|timeout|canceled",
  "assigned_worker": "worker_id|null",
  "lease_expires_at": "ISO-8601|null",
  "attempt": 1,
  "idempotency_key": "string",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601"
}
```

## 3) Worker registry schema (minimum)

```json
{
  "worker_id": "ws-claude-work-01",
  "machine": "workstation-1",
  "cli_type": "claude",
  "account_profile": "work",
  "capabilities": ["code","tests","refactor"],
  "status": "idle|busy|stale|offline",
  "last_heartbeat": "ISO-8601",
  "concurrency": 1
}
```

## 4) Scheduling policy (KISS)

Deterministic selection order:
1. `task.target_cli` exact match
2. `task.target_account` exact match
3. `status=idle` workers only
4. oldest idle worker wins (fairness)

Retry:
- max 3 attempts
- backoff: `15s, 60s, 180s`
- after max attempts -> `failed` + escalation to BOSS

## 5) Persistence model (KISS)

SQLite (v1) tables:
- `tasks`
- `task_events` (append-only)
- `workers`
- `leases`

Recovery rules:
- on router startup:
  - `assigned/running` with expired lease -> `queued` attempt+1
  - replay events for audit timeline

## 6) Session persistence semantics

- `tmux`/`iTerm2`: operator terminal continuity
- authoritative continuity: DB + event log + systemd-managed services

Therefore:
- if terminal closes, system still runs
- if process crashes, systemd restarts and router resumes from DB

## 7) Health and SLO baseline

Metrics (minimum):
- queue depth
- task success rate
- p95 task duration
- stale workers count
- retry rate

Initial SLO targets:
- heartbeat interval: 5s
- stale threshold: 35s (WireGuard-aware)
- dispatch latency p95 < 3s
- task success >= 95% (excluding deterministic test failures)

## 8) FSM transition table (minimum)

| From | To | Guard | Action |
|---|---|---|---|
| queued | assigned | eligible worker found | set `assigned_worker`, set lease |
| assigned | blocked | dependency unresolved | wait + periodic recheck |
| assigned | running | worker ACK | emit `task_started` |
| blocked | queued | dependency resolved | re-enter scheduler |
| running | review | execution finished | enqueue verifier |
| review | completed | `verifier_approved=true` | finalize idempotently |
| review | failed | verifier failed | open remediation task |
| running | timeout | lease expired | requeue `attempt+1` |
| any non-terminal | canceled | operator cancel | stop dispatch + audit event |

Invalid transition handling:
- reject update
- write dead-letter event
- raise alert

## 9) Security baseline

- VPN-only transport between VPS and WS
- worker registration token (rotatable)
- per-worker isolated state dirs:
  - `~/.mesh/agents/<worker_id>/sessions`
  - `~/.mesh/agents/<worker_id>/logs`
- do not share one account profile across active workers

## 10) 3-phase rollout plan

Milestone alignment note (2026-02 runtime realignment):
- Do **not** change runtime `TaskPhase` values (`plan|implement|test|integrate|release`) to represent roadmap work.
- Add a separate milestone/workstream for session-first runtime fixes (`S0`, `S1`, `G1`, etc.).
- `GSD` remains a tracking/integration layer and should not block interactive session runtime rollout.
- The runtime realignment milestones are **inside** the GSD program tracking model (roadmap/governance), even when implementation lands directly in router/session-worker code first.

### Phase A (2-3 giorni) — Core Router/Worker Reliability
- task + worker schema
- routing by `target_cli/target_account`
- heartbeat + leases
- retries + idempotent finalize

### Phase A0 (runtime realignment, immediate) — Interactive Session Execution
- add `execution_mode` (`batch|session`) and worker compatibility matching
- persist sessions + session messages in router DB (`sessions`, `session_messages`)
- introduce tmux/PTY-backed session workers (`claude`, `codex`) with operator attach path
- keep human command approval gates CLI-native (manual/yolo/etc.)
- record progress/status of A0 under GSD milestone tracking (do not represent A0 via runtime `TaskPhase`)

### Phase B — Execution Quality + Controls
- verifier gate and phase transitions
- operator commands (`list/reassign/cancel/retry`)
- basic metrics export

### Phase G1 — GSD Tracking Integration (post-runtime stabilization)
- auto event emitter for GSD command lifecycle
- rule-based semantic mapping + overrides
- router FSM remains final execution transition authority

### Phase C
- iTerm2 automation profile
- richer dashboards/alerts
- optional OpenClaw-inspired lane queues

## 11) Definition of done (v1)
- router restart does not lose in-flight task semantics
- stale worker triggers automatic requeue
- same task completion cannot be finalized twice
- one command can show full task timeline from events
- all active workers expose heartbeat and profile identity

## References
- Architecture baseline: `CONSOLIDATED_TEAM_ORCHESTRATION_GUIDE.md`
- Agent Teams findings: `FINDINGS_REVERSE_ENGINEERING_AGENT_TEAMS.md`
- Multi-account model: `CLAUDE_MULTI_ACCOUNT_SOURCES_CCS.md`
- OpenClaw adaptation: `FINDINGS_CLAUDE_FLOW_OPENCLAW_FIT.md`
