# Incident Report: Scheduler Concurrency Rollback (2026-03-04)

## Summary
- A scheduler-level concurrency implementation was introduced and then rolled back.
- The change allowed assignment behavior that was inconsistent with single-flight worker runtime.
- Final state on `master`: concurrency approach reverted, system behavior restored to deterministic single-flight dispatch.

## Timeline (UTC)
1. `c257d44` introduced scheduler concurrency + broad test additions.
2. Runtime/semantic inconsistencies were identified during review.
3. `158b810` applied surgical rollback of incorrect scheduler concurrency behavior.
4. `e4ce673` hardened upterm socket handling and kept planning continuity for next phase.

## Impact
- Short-lived architectural drift in scheduling semantics.
- Risk of ambiguous worker status/availability interpretation.
- No long-term production damage retained after rollback.

## Root Cause
- Capacity scaling concern (more throughput per host) was addressed in the wrong layer.
- Scheduler/router were expanded before worker execution model changed.
- Result: “virtual” scheduler concurrency without true worker-side parallel execution.

## What Worked
- Fast detection via review + targeted tests.
- Surgical rollback preserved useful test coverage work.
- Follow-up hardening (`upterm` socket cleanup) improved session attach reliability.

## Corrective Decisions
1. Keep router/scheduler single-flight and deterministic.
2. Scale capacity in production with Docker multi-worker replicas.
3. Treat `/sessions/*` as control-plane only, not primary interactive terminal transport.
4. Avoid router-level concurrency until end-to-end worker execution model requires it.

## Preventive Actions
- Require architecture gate for any scheduler state-model changes.
- Keep “capacity scaling” and “dispatch semantics” as separate design tracks.
- For v1.4 execution, prioritize deploy stability before reopening phase 19/20/21 scope.

## References
- Bad commit: `c257d44`
- Rollback commit: `158b810`
- Hardening + planning continuity: `e4ce673`
