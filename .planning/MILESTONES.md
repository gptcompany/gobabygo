# Milestones

## v1.3 Cross-Repo Orchestration (Shipped: 2026-03-04)

**Phases completed:** 10 phases, 16 plans, 0 tasks

**Key accomplishments:**
- (none recorded)

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

## v1.3 Cross-Repo Orchestration (In Progress)

**Phases:** 3 | **Plans:** 3 complete, Phase 16 next | **Tests:** see phase verification reports
**Timeline:** Started 2026-03-03
**Source:** `CROSS_VERIFICATION_BRIEF.md` (cross-verified with Codex)
**Estimated LOC:** ~1,395

**Key objectives:**
1. Result persistence: workers already send results, server must persist and expose them
2. Thread model: ordered task groups with cross-repo context propagation and worker-owned session runtime
3. Aggregator: fan-in result aggregation with per-step error handling (skip/retry/abort)

**Design decisions (from cross-verification):**
- Worker GIA' inviano result — gap e' solo server-side persistence
- Thread step = normal Task row con thread_id aggiuntivo (riusa dependency.py)
- GoBabyGo unico orchestratore (Agent Teams non supporta cross-CLI)
- Router su WS (.111), session worker default, batch fallback
- Session runtime is worker-owned: router exposes thread state/context, workers own tmux lifecycle

---
