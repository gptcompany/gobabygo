# Plan 02-03 Summary: Bounded Retry Policy + Escalation

## Status: COMPLETED

## What was built

### RetryPolicy (`src/router/retry.py` — 194 LOC)
- **Bounded retry**: `should_retry()` checks attempt < max_attempts (default: 3)
- **Backoff schedule**: 15s / 60s / 180s configurable, clamps to last value if attempt exceeds schedule length
- **Requeue with backoff**: Uses shared `requeue_task()` from heartbeat module, then sets `not_before`
- **Escalation**: When max attempts exhausted, emits `escalation_to_boss` event + invokes all registered callbacks
- **EscalationCallback protocol**: `Protocol` class for type-safe callback registration
- **LogEscalation**: Default callback that logs a warning
- **Unschedulable detection**: Finds tasks queued longer than timeout (default: 30min)
- **Idempotent events**: `emit_unschedulable_events()` uses per-day idempotency key

### Key design
- Uses shared `requeue_task()` from `heartbeat.py` — single implementation for requeue logic
- Escalation callbacks are configurable (Protocol-based), supports multiple simultaneous callbacks
- Backoff via `not_before` field on Task model — scheduler respects this during `find_next_task()`

## Tests

- `test_retry.py`: 14 tests
  - should_retry: within limit, at limit, above limit
  - calculate_not_before: schedule mapping, exceeds schedule uses last
  - requeue_with_backoff: success (verify status+not_before), max attempts escalates, escalation event emitted, task not found
  - Multiple callbacks: all invoked
  - LogEscalation: does not raise
  - Unschedulable: find old queued tasks, emit events idempotent, no false positives

## Metrics

| Metric | Value |
|--------|-------|
| Production LOC | 194 |
| Test LOC | 177 |
| New tests | 14 |
| Commits | 1 (shared with 02-02) |
| Regression | 92/92 green |
