# Phase 3: Communication & Verification - Context

**Gathered:** 2026-02-19
**Status:** Ready for planning

<vision>
## How This Should Work

The router enforces a strict communication hierarchy: BOSS talks to PRESIDENT, PRESIDENT talks to WORKERS, WORKERS report back to PRESIDENT. No shortcuts, no worker-to-worker chatter. If any entity attempts to violate this hierarchy, the router physically blocks it — not a warning, not a log, a hard block.

For critical tasks, there's a mandatory verification gate. When a task marked as critical reaches the point where it would normally complete, it stops at "review" instead. A verifier (the PRESIDENT) examines the work. If approved, the task proceeds to "completed". If rejected, a new fix task is created that blocks the original — the original stays in review until the fix is done and it can be re-evaluated.

The BOSS/PRESIDENT decides which tasks are critical when creating them (explicit flag). Workers cannot modify this flag. This prevents gaming the system.

</vision>

<essential>
## What Must Be Nailed

- **Hierarchy enforcement is absolute** — The router is the single enforcement point. BOSS->PRESIDENT->WORKERS->PRESIDENT is the only allowed communication pattern. Any violation is blocked, not just logged.
- **Verifier gate for critical tasks** — Tasks with critical=True must pass through the review state and get explicit approval before reaching "completed". No bypasses.
- **Rejection creates fix tasks** — When a task is rejected, a new remediation task is created with a dependency on the original. The original stays in "review" until the fix is done. After 3 failed fix attempts, escalation to BOSS.

</essential>

<specifics>
## Specific Ideas

- **Criticality is a flag, not a rule** — The BOSS/PRESIDENT explicitly marks tasks as critical=True. This is more flexible than phase-based classification and cannot be manipulated by workers.
- **Peer channel deferred** — The temporary worker-to-worker peer channel with TTL (mentioned in COMMS-01) is explicitly deferred to v2. The strict hierarchy is sufficient for MVP.
- **FSM reuse** — The "review" state already exists in the FSM from Phase 1. This phase adds the enforcement logic around it (who can approve, what happens on rejection).
- **Rejection flow**: review -> verifier rejects -> fix task created (blocks original) -> fix task completed -> original re-reviewed -> approved -> completed
- **Escalation**: After 3 fix task failures -> escalation to BOSS (reuses Phase 2 escalation pattern)

</specifics>

<notes>
## Additional Context

### Technical notes for planning (builder concerns from confidence gate)

These were identified during confidence gate review and will be resolved during planning:

1. **Verifier identity**: PRESIDENT acts as verifier for critical tasks
2. **Review timeout**: Tasks in "review" state need a timeout to prevent indefinite blocking
3. **Post-fix lifecycle**: After fix task completes, original task goes directly to re-review (not re-execution)
4. **Rejection tracking**: "rejected" is not a new FSM state — task stays in "review", rejection is logged as an event. The fix task's existence is the tracking mechanism
5. **Critical flag enforcement**: Router is the only entity that processes state transitions; workers interact via API endpoints that validate permissions

### Existing infrastructure to leverage

- FSM with "review" state (Phase 1, fsm.py)
- Dependency resolution system (Phase 1, dependency.py)
- Retry/escalation pattern (Phase 2, retry.py)
- Event logging with idempotency (Phase 1, db.py)
- Worker registry with roles (Phase 2, worker_manager.py)

### Confidence gate results

- Round 1: 87% (2 issues: criticality classification, rejection workflow)
- Round 2: 95% (residual technical details deferred to planning)

</notes>

---

*Phase: 03-communication-verification*
*Context gathered: 2026-02-19*
