# Phase 7: Tech Debt Cleanup - Context

**Gathered:** 2026-02-20
**Status:** Ready for planning

<domain>
## Phase Boundary

Fix three correctness/quality issues from v1.0: register validation bypass, incomplete YAML mapping, and mypy type annotation. No new features, no API changes. Existing workers must continue to work without modification.

</domain>

<decisions>
## Implementation Decisions

### Register validation approach (DEBT-01)
- `_handle_register()` delegates to `worker_manager.register_worker(worker, token)` — no direct `db.upsert_worker()`
- HTTP handler keeps transport concerns: JSON parsing, body size, Content-Type (permissive — accept missing, reject wrong)
- WorkerManager owns domain logic: token validation, account uniqueness, status initialization, event emission, atomic transaction
- Bridge existing `MESH_AUTH_TOKEN` into WorkerManager token list for backward compatibility
- If `MESH_AUTH_TOKEN` is unset (dev mode), registration is open (no token required)

### Error response codes (DEBT-01)
- Invalid/expired token → **401 Unauthorized** with `{"error": "invalid_token"}`
- Account already in use → **409 Conflict** with `{"error": "account_in_use"}`
- Invalid JSON → **400** with `{"error": "invalid_json"}` (already exists)
- Invalid worker data → **400** with `{"error": "invalid_worker_data"}`
- Re-registration of same worker_id → **200 OK** (update-to-idle semantics)
- New worker → **201 Created**

### Auth unification (DEBT-01)
- Use WorkerManager tokens for `/register` endpoint only (Option A — minimal change)
- Keep `_check_auth()` with shared bearer token for operational endpoints (`/heartbeat`, `/tasks/*`, `/events`)
- Full auth consolidation deferred to future milestone

### YAML mapping (DEBT-02)
- Add `^gsd:implement-.*` to existing implement rule regex
- Maps to same "implement" semantic type (no new category)
- Add override entries if any implement-* commands need special handling (e.g., implement-sync)
- Add test cases in test_mapping.py for gsd:implement-plan, gsd:implement-fix, gsd:implement-phase-sync

### mypy fix (DEBT-03)
- Add `Worker` to import statement in heartbeat.py line 14
- Verify `mypy src/` passes with zero errors after fix

### Claude's Discretion
- Exact WorkerManager initialization and injection into server.router_state
- How to bridge MESH_AUTH_TOKEN → WorkerManager token format (simple wrapper)
- Test structure (can combine into single test file or keep separate)
- Whether to add Content-Type enforcement (permissive is fine)

</decisions>

<specifics>
## Specific Ideas

- WorkerManager.register_worker() already exists and is well-tested — the fix is primarily wiring, not new logic
- The 200/201 distinction (re-register vs new) may require checking if worker existed before upsert
- heartbeat.py uses `from __future__ import annotations` which hides the missing import at runtime — fix is purely for mypy correctness

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 07-tech-debt-cleanup*
*Context gathered: 2026-02-20*
