# Phase 8: Long-Polling Transport - Context

**Gathered:** 2026-02-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Replace the 2s short-polling mechanism (`GET /tasks/next`) with server-held long-polling. Workers block on the server until a task is available or timeout expires. The scheduler wakes waiting workers immediately via `threading.Condition` when dispatching a task. Scope is limited to transport change -- no new endpoints beyond replacing the poll semantics, no worker lifecycle changes, no scheduler logic changes beyond the wakeup hook.

</domain>

<decisions>
## Implementation Decisions

### Wakeup & dispatch semantics
- Per-worker `threading.Condition` -- each registered worker gets its own Condition object in memory
- **State predicate pattern**: each worker slot holds a `task_available` flag (or assigned task reference) checked inside the Condition lock. The wait loop is `while not task_available and not timed_out: cond.wait(timeout=remaining)`. This prevents missed notifications if dispatch happens between request arrival and `cond.wait()`
- On `Scheduler._try_dispatch()` success: acquire worker's Condition lock, set `task_available=True` (or store task ref), call `cond.notify()`
- Long-poll handler: acquire lock, check predicate, `cond.wait(timeout=remaining)` in loop, return task if predicate true or 204 on timeout
- If task targets a specific worker that is NOT currently long-polling: task stays assigned in DB, worker picks it up on next poll reconnect (no queuing beyond DB state)
- Enforce exactly one in-flight long-poll per worker -- second concurrent poll returns 409 Conflict
- **Zombie connection handling**: the `in_flight_poll` flag has a timestamp; if the flag is set but older than `longpoll_timeout + 5s` grace period, consider the previous connection dead and allow the new poll (reset the flag). This prevents half-open TCP connections from locking a worker out for 30s

### Timeout & reconnect behavior
- Server-side long-poll timeout: 25s default (configurable via `MESH_LONGPOLL_TIMEOUT_S` env var). 25s chosen over 30s to stay safely under common proxy idle timeouts (no reverse proxy in current WireGuard setup, but defensive default)
- On timeout (no task): server returns 204 No Content, worker immediately reconnects with small random jitter (100-500ms) to avoid thundering herd
- **Timeout race condition mitigation**: on timeout, before returning 204, the handler checks the DB for any assigned task for this worker (covers the race where dispatch happens right as `cond.wait()` expires). If task found in DB, return 200 + task instead of 204. Cost: one extra DB read per timeout, acceptable at this scale
- On server unreachable: exponential backoff 1s, 2s, 4s, 8s, 16s capped at 30s with random jitter (user decision). **Jitter applies to ALL reconnect scenarios** (unreachable, timeout, error) not just 204 -- prevents thundering herd after server restart
- On task received (200): worker processes task, then opens new long-poll when idle again

### Retrocompatibility
- Replace 2s short-polling entirely -- no backward compatibility layer
- Same endpoint path (`GET /tasks/next?worker_id=X`) but semantics change from instant-return to server-held
- Response codes unchanged: 200 + task JSON, 204 no task, 400 missing param, 409 duplicate poll
- Single operator system, all workers updated simultaneously -- no migration period needed

### Busy worker behavior
- Worker only opens long-poll connection when idle (after completing a task or on startup)
- While busy executing a task, no poll connection is held
- Heartbeat continues independently on its daemon thread (5s interval, unaffected by transport change)

### Observability
- Prometheus counter: `mesh_longpoll_total{result="task"|"timeout"|"conflict"|"error"}` -- per-result poll outcomes
- Prometheus histogram: `mesh_longpoll_wait_seconds` -- time spent waiting (buckets: 0.1, 0.5, 1, 5, 10, 20, 30)
- Prometheus gauge: `mesh_longpoll_waiting_workers` -- current number of workers blocked in long-poll
- Log format: `INFO poll_start worker_id=X` / `INFO poll_complete worker_id=X result=task|timeout duration=Ns`
- Distinguish timeout vs task-received via result label in both metrics and logs

### Thread safety & exhaustion
- ThreadingHTTPServer creates one thread per connection -- at 2-5 workers, max 5 threads held by long-polls (negligible)
- Per-worker Condition objects stored in a dict guarded by a module-level `threading.Lock` (for registration/deregistration)
- **Condition cleanup on disconnect/stale**: on stale detection (`HeartbeatManager.run_stale_sweep()`), remove Condition from dict. Also clean up on graceful worker deregistration if supported. At 2-5 workers, memory impact is negligible even without cleanup, but cleanup keeps the registry accurate
- **Cleanup also on re-registration**: if a worker re-registers (same worker_id), replace the old Condition with a fresh one
- No thread pool cap needed at current scale (deferred to v2 SCAL-02 if worker count grows)

### Claude's Discretion
- Exact internal data structure for the per-worker wait registry (dict vs dedicated class)
- Whether to use `threading.Condition` or `threading.Event` (both viable; Condition preferred for wait-with-timeout pattern)
- Config loading mechanism (env var parsing location -- server startup vs config module)
- InProcessTransport adaptation for tests (may need a way to trigger Condition wakeup in test mode)

</decisions>

<specifics>
## Specific Ideas

- The existing `_handle_task_poll()` at `server.py:95-117` is the modification target -- same endpoint, new blocking semantics
- Worker client `_poll_loop()` at `worker_client.py:112-131` replaces `time.sleep(poll_interval)` with immediate reconnect on 204
- Scheduler's `_try_dispatch()` at `scheduler.py:101-164` needs a hook after successful CAS to notify the worker's Condition
- The per-worker Condition dict should be accessible from both the HTTP handler and the scheduler -- likely via `router_state` dict already passed around
- Keep the `WorkerConfig.poll_interval` field but repurpose it as the long-poll timeout (or add a new `longpoll_timeout` field)

</specifics>

<deferred>
## Deferred Ideas

- Thread pool cap for long-poll connections under heavy worker count (v2 SCAL-02)
- Adaptive polling fallback for degraded mode (v2 SCAL-01)
- WebSocket/SSE transport upgrade (out of scope per REQUIREMENTS.md)

</deferred>

---

*Phase: 08-long-polling-transport*
*Context gathered: 2026-02-20*
