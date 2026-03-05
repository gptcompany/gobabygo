---
gsd_state_version: 1.0
milestone: v1.4
milestone_name: Native Cross-Repo Handoff -- IN PROGRESS
status: execution_in_progress
last_updated: "2026-03-05"
progress:
  total_phases: 21
  completed_phases: 21
  total_plans: 32
  completed_plans: 32
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-04)

**Core value:** Reliable, deterministic task orchestration across distributed AI workers -- router/DB is the single source of truth.
**Current focus:** v1.4 post-deploy monitoring (OpenMemory su muletto operativo, MCP operator profiles allineati).

## Current Position

Milestone: v1.4 Native Cross-Repo Handoff -- IN PROGRESS
Status: Phase 17-21 complete; OpenMemory deploy eseguito su muletto, MCP configurato per operator Claude/Codex/Gemini, monitoraggio attivo

Progress: [======================------] v1.4 active
v1.0 MVP:              6 phases, 15 plans -- SHIPPED 2026-02-19
v1.1 Production:       4 phases, 9 plans  -- SHIPPED 2026-02-21
v1.2 Operational:      3 phases, 3 plans  -- SHIPPED 2026-02-23
v1.3 Cross-Repo:       3 phases, 4 plans  -- SHIPPED 2026-03-04
v1.4 Native Handoff:   5 phases, 5 plans  -- IN PROGRESS (17-21 done, stabilization window)

Total: 21 phases planned, 32 plans completed, 548+ tests, 6742+ production LOC

## Performance Metrics

**v1.0 Velocity:**
- Total plans completed: 15
- Total commits: 36
- Production LOC: 3,829
- Test LOC: 4,313
- Timeline: ~22 hours (2026-02-18 -> 2026-02-19)

**v1.1 Velocity:**
- Total plans completed: 9
- Started: 2026-02-20

| Phase | Plan | Duration | Tasks | Files |
|-------|------|----------|-------|-------|
| 08    | 01   | 9min     | 2     | 5     |
| 08    | 02   | 15min    | 3     | 8     |
| 09    | 01   | 7min     | 2     | 4     |
| 09    | 02   | 9min     | 2     | 7     |
| 09    | 03   | 8min     | 2     | 4     |
| 10    | 01   | 10min    | 3     | 7     |
| 10    | 02   | 7min     | 2     | 2     |

**v1.2 Velocity:**
- Total plans completed: 3
- Started: 2026-02-23

| Phase | Plan | Duration | Tasks | Files |
|-------|------|----------|-------|-------|
| 11    | 01   | 5min     | 3     | 4     |
| 12    | 01   | 8min     | 4     | 6     |
| 13    | 01   | 7min     | 3     | 3     |

**v1.3 Velocity:**
- Total plans completed: 4
- Started: 2026-03-03

| Phase | Plan | Duration | Tasks | Files |
|-------|------|----------|-------|-------|
| 14    | 01   | 10min    | 4     | 5     |
| 15    | 01   | -        | -     | -     |
| 15    | 02   | -        | -     | -     |
| 16    | 01   | -        | 6     | 9     |

## Accumulated Context

### Decisions

All decisions logged in PROJECT.md Key Decisions table.
Full decision history per milestone in Accumulated Context sections below.

<details>
<summary>v1.1 decisions (22 items)</summary>

- DEBT-01: fail-closed registration (MESH_DEV_MODE=1 required for open registration)
- DEBT-01: WorkerManager handles register auth; _check_auth() guards other endpoints
- DEBT-01: 200 for re-registration, 201 for new; case-insensitive Bearer parsing
- LP-01: PollResult dataclass for typed returns instead of sentinel object
- LP-01: Auto-create slot on wait_for_task if worker not pre-registered
- LP-01: Zombie grace period = timeout + 5s using monotonic timestamp
- LP-02: Notify after transaction commit so DB state is visible when worker reads
- LP-02: Worker client HTTP timeout = longpoll_timeout + 5s to avoid premature client timeout
- LP-02: Exponential backoff 1s-30s with jitter on server errors; immediate reconnect on 204
- RESL-01: Re-registration call wrapped in nested try/except to prevent heartbeat thread death
- RESL-01: Review check thread uses sleep-first pattern (same as watchdog_loop)
- RESL-02: Drain uses threading.Event to wake timer thread, not synchronous replay in emit()
- RESL-03: Exponential backoff doubles on failure, caps at 600s, resets on full success
- RESL-04: sd_notify always first in watchdog cycle; integrity_check gated to every N cycles (default 10)
- RESL-04: Cycle 0 skips integrity check to avoid delaying startup; escalation = log.error + Prometheus counter
- OPSC-01: Extracted _update_worker_post_task helper to DRY draining auto-retire across 3 call sites
- OPSC-01: draining -> stale allowed for heartbeat timeout safety; stale recovery cancels drain (acceptable)
- OPSC-01: GET /workers embeds running+assigned tasks inline (no N+1 concern for small fleet)
- OPSC-02: Pure HTTP client design for meshctl -- no imports from src.router.*, only argparse + requests
- OPSC-02: Worker IDs truncated to 8 chars in table display; queue summary uses only /health data
- OPSC-02: Drain polling at 2s intervals with configurable --timeout; drained_immediately short-circuits

</details>

<details>
<summary>v1.2 decisions (11 items)</summary>

- OPRDY-01: Dispatch loop drains all tasks per cycle (inner while True) for throughput; CAS prevents double-dispatch
- OPRDY-01: Sleep-first daemon thread pattern; no stop hook (consistent with review_check_loop)
- OPRDY-03: TaskCreateRequest DTO separates public/internal Task fields; client cannot set status/assigned_worker
- OPRDY-03: Eager dispatch on POST /tasks is best-effort; dispatch loop is backup
- OPRDY-03: sqlite3.IntegrityError caught generically for idempotency_key UNIQUE constraint (409)
- OPRDY-04: meshctl submit follows pure HTTP client pattern (no router imports)
- OPRDY-06: subprocess.run without shell=True prevents command injection with user prompts
- OPRDY-06: --print -p flags for claude CLI (print-only mode with prompt)
- OPRDY-07: Dry-run reports as completed (not failed) since it's intentional behavior
- OPRDY-08: Global try/except in _execute_task guarantees _report_failure on any error
- FIX-01: shlex.split() for multi-word cli_command tokenization (subprocess requires list of args, not single string)

</details>

<details>
<summary>v1.3 decisions (9 items)</summary>

- RPER-01: result_json as inline TEXT column on tasks (not separate table) -- YAGNI
- RPER-01: Sanitize + persist in same DB transaction as state change for atomicity
- RPER-01: Secret patterns filtered via regex before persistence (sk-, ghp_, xoxb-)
- RPER-01: 32KB size limit with recursive string truncation + `_truncated`; fallback compact summary with `_hard_truncated` if still oversized
- AGG-01: on_failure field on Task model (abort/skip/retry) -- thread-only enforcement via dependency.py
- AGG-01: Non-thread tasks preserve legacy behavior (failed = unblocks dependents)
- AGG-01: Retry uses existing RetryPolicy.calculate_not_before() for backoff; running->queued FSM transition added
- AGG-01: compute_thread_status ignores failed steps with on_failure=skip (thread completed not failed)
- AGG-01: get_thread_context includes skipped markers for downstream step awareness

</details>

### Completed Milestones

- **v1.0 MVP** (2026-02-19): 6 phases, 15 plans, 291 tests -- see `.planning/milestones/`
- **v1.1 Production Readiness** (2026-02-21): 4 phases, 9 plans, 404 tests
- **v1.2 Operational Readiness** (2026-02-23): 3 phases, 3 plans, 436 tests
- **v1.3 Cross-Repo Orchestration** (2026-03-04): 3 phases, 4 plans, 548 tests

### Deploy Status (2026-02-24)

**Infrastruttura live:**

| Componente | Host | Servizio systemd | Stato |
|-----------|------|-------------------|-------|
| Router | VPS 10.0.0.1 | `mesh-router.service` | active, healthy |
| Worker claude | Workstation 10.0.0.2 | `mesh-worker@claude-work.service` | active, idle |
| Worker codex | Workstation 10.0.0.2 | `mesh-worker@codex-work.service` | active, idle |
| Worker gemini | Workstation 10.0.0.2 | `mesh-worker@gemini-work.service` | active, idle |

### Pending Todos

- PROD-DEPLOY-01: Docker multi-worker rollout (capacity via replicas, no router concurrency)

### Blockers/Concerns

- Account-profile uniqueness blocks same-profile replicas (`account_in_use`) unless explicitly handled.

## Session Continuity

Last session: 2026-03-05
Stopped at: v1.4 phase 21 deployed on muletto; post-deploy monitoring in progress
Resume with: `.planning/RESUME.md`

## Prossimi Passi

### Next: Continue monitoring OpenMemory health/logs, then close v1.4 milestone
