# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.3 — Cross-Repo Orchestration

**Shipped:** 2026-03-04
**Phases:** 3 | **Plans:** 4 | **Commits:** 12

### What Was Built
- Result persistence with secret sanitization and 32KB truncation
- Thread model for cross-repo task orchestration with automatic context propagation
- Per-step error handling (abort/skip/retry) with exponential backoff
- Full audit trail per step (input, output, timestamps, worker, repo)
- GET /tasks read path for result retrieval
- meshctl thread CLI (create, add-step, status, context)

### What Worked
- **Reusing existing primitives**: Thread steps are normal Task rows with thread_id. dependency.py unblocks steps automatically. No parallel system needed.
- **YAGNI discipline**: result_json as inline column (not separate table), on_failure per-step (not per-thread), context as runtime enrichment (not persisted).
- **Cross-verification with Codex**: The CROSS_VERIFICATION_BRIEF.md produced a clean design that mapped directly to implementation with minimal deviation.
- **Integration checker**: The automated cross-phase wiring check caught the session_spawner.py orphan and confirmed all 18 exports connected.

### What Was Inefficient
- **Missing SUMMARY.md for phases 15 and 16**: Execution completed but SUMMARY.md files were not generated, causing downstream audit to flag documentation gaps. The audit workflow relies on SUMMARY frontmatter for requirements cross-reference.
- **Missing VERIFICATION.md for phases 14 and 16**: Only Phase 15 had formal verification. The integration checker compensated but the gap added audit overhead.
- **Stale REQUIREMENTS.md**: 9 requirement checkboxes not updated during execution. The traceability table showed THRD/AGGR requirements as "Ready"/"Blocked" despite being implemented.
- **ROADMAP.md Phase 16 not updated**: Phase 16 still showed `[ ]` and `0/?` plans despite being complete.

### Patterns Established
- **Thread step = normal Task row**: Reuses scheduler, FSM, dependency resolution. No new dispatch system.
- **Runtime enrichment pattern**: thread_context computed on-the-fly from completed steps, injected into poll response. Not persisted, never stale.
- **Secret sanitization pipeline**: regex-based filtering before DB persistence. Reusable for any data path.
- **on_failure per-step**: Granular error policies without thread-level complexity. dependency.py handles unblocking logic.

### Key Lessons
1. **Generate SUMMARY.md and VERIFICATION.md during execute-phase, not after**: Missing artifacts caused audit friction. The GSD workflow should enforce these as part of phase completion.
2. **Update REQUIREMENTS.md traceability during execution**: Stale checkboxes and status values undermine the 3-source cross-reference. Should be updated when each phase completes.
3. **Worker-owned sessions are correct**: THRD-04 originally said "router creates tmux sessions" but the implementation correctly keeps sessions worker-owned. Requirements should be updated when architectural decisions change scope.
4. **Integration checker is high-value for multi-phase milestones**: The automated wiring check found the session_spawner orphan and verified all 14 requirements against actual code paths.

### Cost Observations
- Model mix: predominantly opus for execution, sonnet for integration checker
- Sessions: 3 (Phase 14, Phase 15+16 combined, milestone completion)
- Notable: 2-day milestone from design to completion (including cross-verification)

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Phases | Plans | Tests | Key Change |
|-----------|--------|-------|-------|------------|
| v1.0 MVP | 6 | 15 | 291 | Initial implementation, all patterns established |
| v1.1 Production | 4 | 9 | 404 | Self-healing, long-polling, operator CLI |
| v1.2 Operational | 3 | 3 | 436 | Dispatch automation, real CLI execution |
| v1.3 Cross-Repo | 3 | 4 | 548 | Thread orchestration, error policies, audit trail |

### Cumulative Quality

| Milestone | Tests | New Tests | Production LOC | Test Growth |
|-----------|-------|-----------|----------------|-------------|
| v1.0 | 291 | 291 | 3,829 | baseline |
| v1.1 | 404 | +113 | ~4,500 | +39% |
| v1.2 | 436 | +32 | ~5,000 | +8% |
| v1.3 | 548 | +112 | 6,742 | +26% |

### Top Lessons (Verified Across Milestones)

1. **Reuse existing primitives over new abstractions**: v1.1 reused watchdog pattern for review_check; v1.2 reused CAS for dispatch; v1.3 reused Task+dependency.py for threads. Consistency wins.
2. **YAGNI is correct for solo projects**: Every "just in case" feature deferred (Postgres, WebSocket, GUI) saved weeks of complexity. The mesh works with stdlib + SQLite.
3. **Update tracking artifacts during execution, not after**: Stale REQUIREMENTS.md and missing SUMMARY.md files create audit friction. The cost of updating during is lower than the cost of retroactive fixing.
4. **Cross-verification before implementation produces cleaner designs**: v1.3 cross-verification with Codex caught scope issues before code was written.
