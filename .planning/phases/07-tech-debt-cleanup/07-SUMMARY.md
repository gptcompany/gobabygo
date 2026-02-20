# Phase 7: Tech Debt Cleanup - Summary

**Completed:** 2026-02-20
**Plans:** 2/2

## Results

### Plan 07-01: Register Validation via WorkerManager (DEBT-01)

**Changes:**
- `server.py`: Rewired `_handle_register()` to delegate to `WorkerManager.register_worker()` instead of calling `db.upsert_worker()` directly
- `server.py`: Added `WorkerManager` instantiation in `run_server()` with `MESH_AUTH_TOKEN` bridged as token
- `server.py`: Added `MESH_DEV_MODE=1` env var for explicit open registration (fail-closed by default)
- `server.py`: Added `pydantic.ValidationError` catch for invalid worker data
- `server.py`: Case-insensitive Bearer scheme parsing per RFC 7235
- `worker_manager.py`: Added `dev_mode` parameter; returns `"re-registered"` for existing worker_id
- `test_server.py`: 8 registration tests (dev mode, valid token, invalid token, no token, account_in_use, re-register 200, invalid JSON, case-insensitive bearer)
- `test_server.py`: Updated auth tests to reflect `/register` using WorkerManager (not `_check_auth`)
- `test_worker_manager.py`: Updated re-register assertion to match new return value

**LOC changed:** ~80 production, ~70 test

### Plan 07-02: YAML Mapping + mypy Fix (DEBT-02, DEBT-03)

**Changes:**
- `command_rules.yaml`: Added `^gsd:implement-.*` to implement rule regex
- `heartbeat.py`: Added `Worker` to import from `src.router.models`
- `test_mapping.py`: Added 3 test cases for `gsd:implement-plan`, `gsd:implement-fix`, `gsd:implement-phase-sync`

**LOC changed:** ~2 production, ~10 test

## Success Criteria Verification

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Worker registration with invalid/missing fields rejected by WorkerManager | PASS | `test_register_with_invalid_token`, `test_register_without_token_when_required` — 401 returned |
| 2 | `gsd:implement-plan` resolves to correct semantic event type via YAML mapping | PASS | `test_implement_plan_command` — step == "implement" |
| 3 | `mypy src/` passes with zero errors on heartbeat.py Worker type annotation | PASS | `mypy src/router/heartbeat.py` — "Success: no issues found" |

## Test Results

- **Total tests:** 302 (was 291 pre-phase)
- **All passing:** 302/302
- **New tests:** 11 (8 server register + 3 mapping)

## Confidence Gate Feedback Addressed

| Issue | Resolution |
|-------|------------|
| Fail-open security | `MESH_DEV_MODE=1` required for open registration (fail-closed default) |
| 409 Conflict blocking crash restarts | WorkerManager returns 200 for re-registration of same worker_id |
| Content-Type ambiguity | N/A — kept current behavior (separate concern) |
| Race conditions 200/201 | DB transaction in WorkerManager prevents races |
| String comparison brittleness | Accepted for now — strings are internal constants, not user-facing |
| Pydantic ValidationError | Added `ValidationError` to catch block |
| Case-insensitive Bearer | Implemented case-insensitive scheme parsing |
