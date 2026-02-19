# Phase 05 Summary: Deployment

## Completed: 2026-02-19

### Plans Executed
| Plan | Description | Tests | LOC (prod) | LOC (test) |
|------|-------------|-------|-----------|-----------|
| 05-01 | HTTP server + worker client + systemd units | 32 | ~400 | ~420 |
| 05-02 | Infrastructure scripts + deploy config | 19 | ~210 (scripts) | ~130 |
| **Total** | | **51** | **~610** | **~550** |

### New Files
- `src/router/server.py` — ThreadingHTTPServer with Bearer auth, sd_notify watchdog
- `src/router/worker_client.py` — Worker client: registration, heartbeat, polling, execution
- `deploy/mesh-router.service` — systemd Type=notify unit for router (VPS)
- `deploy/mesh-worker@.service` — systemd template unit for workers (Workstation)
- `deploy/mesh-router.env` — Router env template
- `deploy/mesh-worker-claude-work.env` — Claude worker env template
- `deploy/mesh-worker-codex-work.env` — Codex worker env template
- `deploy/mesh-worker-gemini-work.env` — Gemini worker env template
- `deploy/install.sh` — Provisioning script (router|worker mode)
- `deploy/verify-network.sh` — Health check script (WireGuard, router, services, UFW)
- `deploy/ufw-setup.sh` — UFW rule for mesh port on wg0
- `deploy/BOOT-ORDER.md` — Boot order documentation and failure recovery
- `tests/router/test_server.py` — HTTP endpoint tests (24 tests)
- `tests/router/test_worker_client.py` — Worker client tests (8 tests)
- `tests/test_deploy_scripts.py` — Script/config validation tests (19 tests)

### Modified Files
- `src/router/db.py` — Added check_same_thread, upsert_worker, get_tasks_by_worker, count_tasks_by_status
- `pyproject.toml` — Added sdnotify>=0.3.0

### Architecture Decisions Made
1. **stdlib ThreadingHTTPServer** — zero external deps for HTTP serving
2. **Mesh router port 8780** (configurable via MESH_ROUTER_PORT env)
3. **SQLite check_same_thread=False** for ThreadingHTTPServer
4. **systemd Type=notify** with sd_notify READY=1 + WATCHDOG=1 (10s interval, 30s timeout)
5. **Worker short-polling** (2s interval) — long-polling deferred to v2
6. **uv for Python venv** management (both VPS and Workstation)
7. **Data paths**: /var/lib/mesh-router/ (DB), /etc/mesh-router/ (config), ~/.mesh/ (worker state)
8. **Dedicated service users**: mesh (router), mesh-worker (workers)

### Confidence Gates
- Context gate: 93/100 (auto-approve)
- Plan gate: 90/100 (auto-approve after iteration)
- Implementation gate: 90/100 (auto-approve)

### Test Suite
- Phase 05 new tests: 51
- Total test suite: 264 (zero regressions)
