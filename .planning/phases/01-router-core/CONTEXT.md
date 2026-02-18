# Phase 1: Router Core — Context

## Goal
Build the persistent router core: SQLite schema, FSM transition guard, append-only event log, idempotent finalization, and crash recovery.

## Requirements Covered
- **ROUTER-01**: SQLite persistence (tasks, task_events, workers, leases) with crash recovery
- **ROUTER-02**: FSM transition guard with dead-letter + alert on invalid transitions
- **ROUTER-03**: Idempotent task finalization (compare-and-set, no double completion)
- **ROUTER-04**: Append-only event log with idempotency key deduplication

## Technical Context (from KISS spec)

### Task Schema (canonical)
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

### SQLite Tables (v1)
- `tasks` — task records with all fields above. `assigned_worker` and `lease_expires_at` are **denormalized snapshots** updated transactionally with leases table.
- `task_events` — append-only event log (event_id, task_id, event_type, payload, idempotency_key, ts)
- `workers` — worker registry (worker_id, machine, cli_type, account_profile, capabilities, status, last_heartbeat, concurrency)
- `leases` — active leases (lease_id, task_id, worker_id, granted_at, expires_at). **Source of truth** for active assignments; tasks.assigned_worker is derived.

**Schema invariant**: Updates to tasks.status + leases must be in the same transaction (`BEGIN IMMEDIATE`) to prevent data drift.

### FSM Transition Table
| From | To | Guard | Action |
|---|---|---|---|
| queued | assigned | eligible worker found | set assigned_worker, set lease |
| assigned | blocked | dependency unresolved | mark blocked + register dependency trigger |
| assigned | running | worker ACK | emit task_started |
| blocked | queued | dependency resolved (event-driven trigger, not polling) | re-enter scheduler |
| running | review | execution finished | enqueue verifier |
| review | completed | verifier_approved=true | finalize idempotently |
| review | failed | verifier failed | open remediation task |
| running | failed | worker reports terminal error | emit task_failed, open remediation if retriable |
| running | timeout | lease expired | requeue attempt+1 (if attempt < max_attempts) |
| running | failed | lease expired + attempt >= max_attempts | terminal failure, escalate to BOSS |
| any non-terminal | canceled | operator cancel | stop dispatch + audit event |

Invalid transitions: reject update, write dead-letter event, raise alert.

### Atomic FSM Transitions
All state transitions use `BEGIN IMMEDIATE` transactions with compare-and-set:
```sql
UPDATE tasks SET status = :new_status, updated_at = :now
WHERE task_id = :id AND status = :expected_old_status;
```
If `rows_affected == 0`, the transition is rejected (concurrent modification or invalid state). This prevents race conditions even in single-process (e.g., cancel arriving while lease expires).

### Blocked Task Resolution
Blocked tasks are resolved **event-driven**, not by polling:
- When a task reaches terminal state (`completed`/`failed`/`canceled`), the router checks `depends_on` references
- Any blocked task whose dependencies are all resolved is moved to `queued`
- This avoids livelock and inefficient polling cycles

### Recovery Rules (on router startup)
- `assigned`/`running` with expired lease → `queued` with attempt+1
- Replay events for audit timeline
- Idempotent finalization via compare-and-set on expected state

### Idempotency Key
Sender-generated UUID per event emission. The sender retains the same key on network re-delivery retries, but generates a new key for semantically distinct events (e.g., attempt 2 vs attempt 1).
- **Network re-delivery**: same `idempotency_key` → deduped on ingest (acknowledge, don't re-process)
- **New attempt**: new `idempotency_key` → processed normally
- **State transitions**: additionally protected by compare-and-set on `expected_old_status`

This two-layer approach (idempotency key + CAS) handles both network duplicates and concurrent state races.

## Infrastructure Context
- **Runtime**: Python (aligns with existing PoC patterns and bridge/emitter)
- **VPS**: Where router runs (systemd managed — Phase 5)
- **Database**: SQLite file on VPS filesystem
- **No external dependencies** for this phase

## Decisions Already Made
- SQLite over Postgres for v1 (minimal dependency)
- Router/DB as single source of truth
- 9 canonical states (including blocked)
- FSM from KISS spec Section 8 is definitive

## Risks & Mitigations
- **SQLite concurrent writes**: WAL mode from Phase 1 (`PRAGMA journal_mode=WAL`). Handle `SQLITE_BUSY` with retry+backoff (3 retries, 50ms/100ms/200ms). Required because heartbeat updates and task transitions overlap.
- **Schema redundancy** (tasks.assigned_worker vs leases): mitigated by transactional consistency invariant (`BEGIN IMMEDIATE`).
- **Poison pill tasks**: Max 3 attempts enforced (from SCHED-02). After max attempts, transition to terminal `failed` state + escalation event. No infinite requeue loop.
- **Event log growth**: Append-only `task_events` table. Retention policy: archive events older than 30 days to `task_events_archive` table. SQLite `VACUUM` on archive rotation. Backup via `sqlite3 .backup` (online, no service interruption with WAL).

## Source Documents
- `kiss_mesh/KISS_IMPLEMENTATION_SPEC_V1.md` sections 2, 5, 8
- `kiss_mesh/CC_CROSS_VALIDATED_REPORT.md` Sprint 1 items 1-2
- `kiss_mesh/CONSOLIDATED_TEAM_ORCHESTRATION_GUIDE.md` sections 6-7
