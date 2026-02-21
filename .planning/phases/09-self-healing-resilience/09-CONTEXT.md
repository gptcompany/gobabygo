# Phase 09: Self-Healing Resilience - Context

## Goal

The mesh recovers from transient failures without operator intervention -- workers re-register, events replay, watchdog catches DB corruption, stale reviews are detected.

## Success Criteria

1. Worker heartbeat receives "unknown_worker" (e.g., after router restart) -> worker automatically re-registers and resumes
2. Buffered events replayed on periodic timer (default 60s configurable), buffer drains when connectivity restored
3. When next event emits successfully after buffered period, all previously buffered events replayed immediately (on-next-emit trigger)
4. Watchdog checks DB health (WAL size, integrity_check, disk space) each cycle, escalates via alerting if any check fails
5. Stale task reviews (stuck beyond timeout) detected and escalated by periodic event loop

## Requirements

- RESL-01: Auto-reregister
- RESL-02: Buffer replay timer
- RESL-03: On-next-emit drain
- RESL-04: Watchdog DB health
- RESL-05: Stale review detection

## Current State Analysis

### What Exists

| Criterion | Component | Status | Location |
|-----------|-----------|--------|----------|
| Auto-reregister | HeartbeatManager returns "unknown_worker" | Server-side done | heartbeat.py:118 |
| Auto-reregister | WorkerManager.register_worker() handles re-reg | Server-side done | worker_manager.py:88-96 |
| Auto-reregister | Worker client heartbeat loop | **Ignores response body** | worker_client.py:98-109 |
| Buffer replay | FallbackBuffer.replay() | On-demand only | buffer.py:59-90 |
| Buffer replay | EventEmitter.replay_buffer() | Public method, never auto-called | emitter.py:143-150 |
| On-next-emit drain | EventEmitter.emit() | No drain trigger on success | emitter.py:64-141 |
| Watchdog DB | Systemd sd_notify watchdog | Heartbeat only, no DB checks | server.py:415-421 |
| Watchdog DB | RouterDB WAL mode | Enabled, no monitoring | db.py:135 |
| Stale review | VerifierGate.check_review_timeout() | **Exists but never called** | verifier.py:186-212 |
| Stale review | Scheduler stores review_timeout_s | Default 3600s | scheduler.py:56 |

### Gaps Summary

1. **Auto-reregister**: Worker client discards heartbeat response. Needs to parse JSON, detect "unknown_worker", call `_register()`.
2. **Buffer replay**: No periodic scheduler. Needs timer thread (e.g. 60s) calling `emitter.replay_buffer()`.
3. **On-next-emit drain**: emit() doesn't check buffer after success. Needs `if success and buffer.has_events(): replay_buffer()`.
4. **Watchdog DB**: No WAL size check, no `PRAGMA integrity_check`, no disk space check. Needs new DB health methods + integration into watchdog thread.
5. **Stale review**: `check_review_timeout()` is dead code. Needs periodic scheduling.

## Architectural Patterns to Follow

- **Scheduler pattern**: HeartbeatManager already runs periodic stale sweep -- same thread-timer pattern for buffer replay and review timeout
- **Transaction safety**: notify after commit (established in Phase 08 LP-02 decision)
- **Exponential backoff**: worker_client already uses jitter+backoff pattern (Phase 08)
- **Metrics integration**: MeshMetrics + Prometheus gauges/counters for all new observability

## Implementation Boundaries

- No new external dependencies
- No new HTTP endpoints (use existing /health and /metrics)
- No new DB tables (use existing schema)
- Worker client changes must be backward-compatible (old server without these features should still work)
- Watchdog escalation = log.error + Prometheus counter (no external alerting integration in this phase)

## Recommended Plan Structure

Given the 5 requirements, suggest 3 plans:

1. **09-01**: Auto-reregister + stale review scheduling (both are "wire existing code into loops")
2. **09-02**: Buffer replay timer + on-next-emit drain (coupled buffer lifecycle)
3. **09-03**: Watchdog DB health checks (new functionality requiring DB methods + metrics)
