# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-23)

**Core value:** Reliable, deterministic task orchestration across distributed AI workers -- router/DB is the single source of truth.
**Current focus:** v1.3 Cross-Repo Orchestration -- Phase 15 Thread Model

## Current Position

Milestone: v1.3 Cross-Repo Orchestration -- IN PROGRESS
Phase: 14 Result Persistence + Read Path -- COMPLETE
Status: Phase 14 complete (1/1 plans), ready for Phase 15
Last activity: 2026-03-03 -- Phase 14 executed: result persistence + read endpoints

Progress: [=========                   ] 33% v1.3 (1/3 phases)
v1.3:    Phase 14 [====================] COMPLETE
         Phase 15 [>                   ] next

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
- Total plans completed: 1
- Started: 2026-03-03

| Phase | Plan | Duration | Tasks | Files |
|-------|------|----------|-------|-------|
| 14    | 01   | 10min    | 4     | 5     |

## Accumulated Context

### Decisions

All v1.0 decisions logged in PROJECT.md Key Decisions table (15 decisions, 13 Good, 2 Revisit).

**v1.1 decisions:**
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

**v1.2 decisions:**
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

**v1.3 decisions:**
- RPER-01: result_json as inline TEXT column on tasks (not separate table) -- YAGNI
- RPER-01: Sanitize + persist in same DB transaction as state change for atomicity
- RPER-01: Secret patterns filtered via regex before persistence (sk-, ghp_, xoxb-)
- RPER-01: 32KB size limit with recursive string truncation + `_truncated`; fallback compact summary with `_hard_truncated` if still oversized

### Completed Milestones

- **v1.0 MVP** (2026-02-19): 6 phases, 15 plans, 291 tests -- see `.planning/milestones/`
- **v1.1 Production Readiness** (2026-02-21): 4 phases, 9 plans, 404 tests
- **v1.2 Operational Readiness** (2026-02-23): 3 phases, 3 plans, 436 tests

### Completed Phases (v1.1)

- **Phase 7: Tech Debt Cleanup** (2026-02-20): 2 plans, 11 new tests (302 total), confidence 92%
- **Phase 8: Long-Polling Transport** (2026-02-20): 2 plans, 25 new tests (327 total), confidence 95%
- **Phase 9: Self-Healing & Resilience** (2026-02-21): 3 plans, 30 new tests (357 total), confidence 95%
- **Phase 10: Operator CLI** (2026-02-21): 2 plans, 47 new tests (404 total), confidence 95%

### Completed Phases (v1.2)

- **Phase 11: Dispatch Loop** (2026-02-23): 1 plan, 2 new tests (406 total + E2E updated), confidence 95%
- **Phase 12: POST /tasks** (2026-02-23): 1 plan, 16 new tests (422 total), confidence 95%
- **Phase 13: CLI Invocation** (2026-02-23): 1 plan, 12 new tests (436 total), confidence 95%

### Completed Phases (v1.3)

- **Phase 14: Result Persistence** (2026-03-03): 1 plan, 10 new tests (467 total), confidence 95%

### Deploy Status (2026-02-24)

**Infrastruttura live:**

| Componente | Host | Servizio systemd | Stato |
|-----------|------|-------------------|-------|
| Router | VPS 10.0.0.1 | `mesh-router.service` | active, healthy |
| Worker claude | Workstation 10.0.0.2 | `mesh-worker@claude-work.service` | active, idle |
| Worker codex | Workstation 10.0.0.2 | `mesh-worker@codex-work.service` | active, idle |
| Worker gemini | Workstation 10.0.0.2 | `mesh-worker@gemini-work.service` | active, idle |

**Token:** `***REDACTED***`

**Comandi utili:**
```bash
# Health check
curl -sf http://10.0.0.1:8780/health | python3 -m json.tool

# Status completo
MESH_ROUTER_URL=http://10.0.0.1:8780 MESH_AUTH_TOKEN="***REDACTED***" python3 -m src.meshctl status --json

# Submit task
MESH_ROUTER_URL=http://10.0.0.1:8780 MESH_AUTH_TOKEN="***REDACTED***" python3 -m src.meshctl submit --title "Test" --payload '{"prompt":"hello"}'

# Logs
ssh root@10.0.0.1 journalctl -u mesh-router -f
journalctl -u mesh-worker@claude-work -f
```

**Bug fixati durante deploy (6):**
1. `setuptools.backends._legacy` → `setuptools.build_meta` (pyproject.toml)
2. FallbackBuffer path `~/.mesh` inaccessibile → env var `MESH_BUFFER_PATH`
3. Worker crash su 409 register → trattato come "already registered"
4. `account_in_use` con profile condiviso → profile unici per-worker
5. Python 3.10 su Workstation → uv managed Python 3.12 in `/opt/mesh-worker/.python/`
6. venv symlink a `/root/.local/` → Python locale in path accessibile

### Pending Todos

None

### Blockers/Concerns

None

## Session Continuity

Last session: 2026-03-03
Stopped at: Completed 14-01-PLAN.md (Phase 14 Result Persistence)
Resume with: `/pipeline:gsd 15` per continuare Phase 15

## Prossimi Passi

### Next: v1.3 Phase 15 — Thread Model + Cross-Repo Context
### Dopo Phase 15: Phase 16 — Aggregator + Error Handling
