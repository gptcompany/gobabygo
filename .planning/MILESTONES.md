# Milestones

## v1.4 Native Cross-Repo Handoff (Planned: 2026-03-04)

**Phases:** 5 | **Plans:** 5 | **Status:** SHIPPED (2026-03-05)
**Execution order:** 17 -> 18 -> 19 -> 20 -> 21

**Planned outcomes:**
1. Formalize cross-repo operational hierarchy: `BOSS_GLOBAL -> PRESIDENT_GLOBAL -> REPO_LEAD -> WORKER`
2. Add manual topology wiring (`repo -> lead -> worker -> host -> notification room`) without replacing existing env-based deployment
3. Declassify the current session bus to control-plane only; stop treating it as the primary interactive transport
4. Add native attach handles for interactive sessions via `upterm` (or `ssh+tmux` fallback)
5. Add Matrix notification bridge for approval and intervention flows
6. Add structured cross-repo handoff records without creating a second orchestration system
7. Add shared memory via `OpenMemory` server + MCP as a sidecar, not as operational source of truth

**Key constraints:**
- The current `/sessions/*` bus remains useful for audit, output streaming, and quick text only
- The primary interactive data-plane moves to `upterm` or `ssh+tmux`
- `OpenMemory` is memory only; it is not a message bus, scheduler, or task authority
- All initial routing remains manually configurable

**Planning docs:**
- `.planning/milestones/v1.4-ROADMAP.md`
- `.planning/milestones/v1.4-REQUIREMENTS.md`
- `.planning/milestones/v1.4-PHASE-17-18-BREAKDOWN.md`
- `.planning/milestones/v1.4-PHASE-19-BREAKDOWN.md`
- `deploy/topology.v1.4.example.yml`

**Phase contexts:**
- `.planning/milestones/v1.4-phases/17-role-topology/CONTEXT.md`
- `.planning/milestones/v1.4-phases/18-native-session-attach/CONTEXT.md`
- `.planning/milestones/v1.4-phases/19-approval-notification-bridge/CONTEXT.md`
- `.planning/milestones/v1.4-phases/20-structured-cross-repo-handoff/CONTEXT.md`
- `.planning/milestones/v1.4-phases/21-shared-memory-openmemory/CONTEXT.md`

**Execution status (2026-03-05):**
1. Phase 17 complete
2. Phase 18 complete
3. Phase 19 complete
4. Phase 20 complete
5. Phase 21 implementation complete (`deploy/openmemory` + smoke tests), deployed on muletto (`openmemory` healthy), and operator MCP profiles configured (Claude/Codex/Gemini)
6. Phase 21 audit PASS (MEM-04 + policy/auth/coherence evidence), v1.4 closed

---

## v1.3 Cross-Repo Orchestration (Shipped: 2026-03-04)

**Phases:** 3 | **Plans:** 4 | **Tests:** 548 | **New tests:** 112
**Production LOC:** 6,742 Python (+2,560 lines added)
**Timeline:** 2026-03-03 -> 2026-03-04 (2 days)
**Commits:** 12
**Git range:** `bfc355c..b18cb22`

**Key accomplishments:**
1. Result persistence with secret sanitization (sk-, ghp_, xoxb-) and 32KB truncation with hard fallback
2. Thread model: ordered task groups with cross-repo context propagation and automatic step chaining via depends_on
3. Context injection: step N result automatically available as thread_context in step N+1 (runtime enrichment, not payload mutation)
4. Per-step error policies: on_failure abort/skip/retry with exponential backoff (max 3 attempts)
5. Full audit trail per step: input, output, timestamps, worker, repo in DB
6. GET /tasks read path for result retrieval and debugging

**Known gaps (tech debt):**
- THRD-04: session_spawner.py exists but unused — sessions are worker-owned (intentional architectural decision)
- Missing VERIFICATION.md for phases 14 and 16 (implementation verified by integration checker + 548 tests)

**Archives:** `.planning/milestones/v1.3-ROADMAP.md`, `.planning/milestones/v1.3-REQUIREMENTS.md`

---

## v1.0 MVP (Shipped: 2026-02-19)

**Phases:** 6 | **Plans:** 15 | **Tests:** 291 | **Commits:** 36
**Production LOC:** 3,829 Python | **Test LOC:** 4,313 Python
**Timeline:** 2026-02-18 -> 2026-02-19 (~22 hours)
**Git range:** `4be5012..e12a825`

**Key accomplishments:**
1. SQLite persistence with WAL, FSM transition guard, dead-letter stream, crash recovery
2. Worker registry with heartbeat/stale detection, deterministic scheduler, bounded retry with escalation
3. Hierarchical communication policy enforcement (BOSS->PRESIDENT->WORKERS), mandatory verifier gate
4. Event bridge with CloudEvent envelopes, YAML semantic mapping, fallback buffer
5. HTTP server (stdlib ThreadingHTTPServer), worker client with polling, systemd units
6. Prometheus metrics export (15 families), Grafana Cloud alert rules (5 rules)

**Known gaps (tech debt):**
- Missing `/tasks/ack` HTTP endpoint (scheduler logic exists, HTTP wiring absent)
- server._handle_register() bypasses WorkerManager validation
- YAML mapping incomplete for `gsd:implement-*` commands

**Archives:** `.planning/milestones/v1.0-ROADMAP.md`, `.planning/milestones/v1.0-REQUIREMENTS.md`

---

## v1.1 Production Readiness (Shipped: 2026-02-21)

**Phases:** 4 | **Plans:** 9 | **Tests:** 404 | **New tests:** 110
**Timeline:** 2026-02-20 -> 2026-02-21

**Key accomplishments:**
1. Fail-closed worker registration with WorkerManager validation, dev mode bypass
2. Long-polling transport with LongPollRegistry, Condition-based wakeup, p95 < 1s dispatch
3. Self-healing: auto-reregister on unknown_worker, buffer replay timer, smart watchdog with DB health
4. Operator CLI: meshctl status (table + JSON), meshctl drain (poll-based graceful shutdown)

**Archives:** `.planning/phases/07-tech-debt-cleanup/` through `.planning/phases/10-operator-cli/`

---

## v1.2 Operational Readiness (Shipped: 2026-02-23)

**Phases:** 3 | **Plans:** 3 | **Tests:** 436 | **New tests:** 29 (+ 3 updated)
**Timeline:** 2026-02-23

**Key accomplishments:**
1. Periodic dispatch loop (daemon thread, configurable interval, drains all tasks per cycle)
2. POST /tasks endpoint (TaskCreateRequest DTO, idempotency, eager dispatch, 409 on duplicate)
3. meshctl submit command (--title, --cli, --account, --phase, --priority, --payload)
4. Real CLI invocation via subprocess (dry-run mode, guaranteed failure semantics, output truncation)
5. Full env-based worker configuration (MESH_CLI_COMMAND, MESH_DRY_RUN, MESH_WORK_DIR, MESH_TASK_TIMEOUT_S)

**Gaps closed:**
- Dispatch loop: tasks no longer stuck in queued state
- POST /tasks: tasks submittable via HTTP (not just direct DB insert)
- CLI invocation: workers execute real commands (not fake stubs)

**Post-release fix:**
- shlex.split() for multi-word CLI commands (e.g. `ccs {account_profile}` → `["ccs", "work"]`)

**Archives:** `.planning/phases/11-dispatch-loop/` through `.planning/phases/13-cli-invocation/`

---
