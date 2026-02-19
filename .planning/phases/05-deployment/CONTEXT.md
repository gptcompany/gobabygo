# Phase 05: Deployment — Context

## Phase Goal

Production systemd units for router (VPS) and workers (Workstation), UFW rules for mesh port, boot order documentation, and verify-network.sh mesh checks.

**Requirement:** DEPLOY-01 — systemd units for router (VPS) and workers (Workstation) with Restart=always

## Infrastructure Map

| Machine | Role | IP (WireGuard) | OS |
|---------|------|-----------------|-----|
| VPS | mesh-router (control plane) | 10.x.x.1 (wg0) | Ubuntu |
| Workstation | mesh-workers (execution) | 10.x.x.2 (wg0) | Ubuntu 22.04 |
| MacBook | operator (iTerm2 only) | N/A | macOS 12.7.6 |

**Transport:** VPN-only (WireGuard) between VPS and Workstation.

## Key Decisions for This Phase

### 1. Mesh Router Port

- **Default:** `8780` (non-conflicting with existing services)
- **Configurable:** via `MESH_ROUTER_PORT` environment variable
- **Endpoints used:** `POST /events` (bridge → router), `POST /heartbeat` (worker → router)

### 2. Python Environment

- **Tool:** `uv` (fast venv creation + dependency management)
- **VPS venv:** `/opt/mesh-router/venv/`
- **Workstation venv:** `/opt/mesh-worker/venv/`
- **Install:** `uv venv && uv pip install -e .` from project root

### 3. Data Paths

| What | Path (VPS) | Path (Workstation) |
|------|------------|-------------------|
| SQLite DB | `/var/lib/mesh-router/router.db` | N/A |
| Event log | `/var/lib/mesh-router/events/` | N/A |
| Config | `/etc/mesh-router/` | `/etc/mesh-worker/` |
| Worker state | N/A | `~/.mesh/agents/<worker_id>/` |
| Fallback buffer | N/A | `~/.mesh/tasks-buffer.jsonl` |
| YAML rules | `/etc/mesh-router/command_rules.yaml` | N/A |
| Auth token | `/etc/mesh-router/token` (chmod 600) | `/etc/mesh-worker/token` (chmod 600) |

### 4. Worker Configuration

**Template unit:** `mesh-worker@.service` (systemd template for per-instance workers)

**Initial instances:**

| Instance | CLI | Account | Worker ID |
|----------|-----|---------|-----------|
| mesh-worker@claude-work | claude | work | ws-claude-work-01 |
| mesh-worker@codex-work | codex | work | ws-codex-work-01 |
| mesh-worker@gemini-work | gemini | work | ws-gemini-work-01 |

**Multi-account Claude (CCS):** Additional instances can be added:
- `mesh-worker@claude-clientA` → ws-claude-clientA-01
- CCS manages account profiles (`ccs auth create <profile>`)
- One active profile per worker (CCS isolation enforced)

### 5. UFW Rules

```bash
# VPS: allow mesh port from WireGuard only
ufw allow in on wg0 to any port 8780 proto tcp comment "mesh-router"

# Workstation: already has DOCKER-USER hardening
# Workers connect outbound to VPS:8780 — no inbound rule needed
```

### 6. Boot Order

```
VPS:
  1. WireGuard (wg-quick@wg0.service)
  2. mesh-router.service (After=network-online.target wg-quick@wg0.service)

Workstation:
  1. WireGuard (wg-quick@wg0.service)
  2. mesh-worker@*.service (After=network-online.target wg-quick@wg0.service)
```

### 7. Service Configuration

**mesh-router.service (VPS):**
- User=mesh (dedicated service user)
- Type=notify, WatchdogSec=30 (systemd health integration via sd_notify)
- Restart=always, RestartSec=5
- WorkingDirectory=/opt/mesh-router
- Environment: MESH_ROUTER_PORT=8780, MESH_DB_PATH=/var/lib/mesh-router/router.db
- StandardOutput=journal
- Security hardening: NoNewPrivileges=true, ProtectSystem=strict, ReadWritePaths=/var/lib/mesh-router

**mesh-worker@.service (Workstation):**
- User=mesh-worker (dedicated service user, member of `sam` group for repo access)
- Supplementary groups: sam (repo read), mesh (shared state)
- Restart=always, RestartSec=10
- WorkingDirectory=/opt/mesh-worker
- EnvironmentFile=/etc/mesh-worker/%i.env
- StandardOutput=journal
- Security hardening: NoNewPrivileges=true, ProtectSystem=strict, ReadWritePaths=~/.mesh
- Note: CCS profiles installed under mesh-worker home, CLI tools in venv PATH

## Existing Code References

- **HttpTransport:** `src/router/bridge/transport.py:88` — sends to `router_url/events`
- **Heartbeat:** `src/router/heartbeat.py` — 5s interval, needs HTTP endpoint
- **RouterDB:** `src/router/db.py` — SQLite with WAL mode
- **FallbackBuffer:** `src/router/bridge/buffer.py` — writes to `.mesh/tasks-buffer.jsonl`

## What Needs to Be Built

### Plan 05-01: systemd Units + Entry Points
- HTTP server wrapping the router (minimal: uvicorn/starlette or plain http.server)
- `POST /events` endpoint (receives CloudEvent JSON, writes to DB)
- `POST /heartbeat` endpoint (receives worker heartbeat)
- `GET /health` endpoint (liveness check)
- `GET /tasks/next?worker_id=X` endpoint (long-poll task dispatch — worker polls for assigned tasks)
- mesh-router.service unit file (Type=notify, WatchdogSec=30)
- mesh-worker@.service template unit file
- Worker entry point script (register + heartbeat loop + poll GET /tasks/next)
- Environment files and config templates
- logrotate config for /var/lib/mesh-router/ (event log rotation, 7d retention)

### Plan 05-02: Infrastructure Prep
- UFW rule for mesh port (VPS)
- verify-network.sh: WireGuard up, router reachable, workers registered
- Boot order runbook documentation
- install.sh: automated setup script (create user, dirs, venv, install deps, enable services)

## Constraints

- No Docker for mesh services (direct systemd for simplicity)
- SQLite v1 (no Postgres)
- VPN-only transport (no public internet exposure)
- Worker auth: bearer token (rotatable, stored in /etc/mesh-*/token)
- Heartbeat: 5s interval, 35s stale threshold (WireGuard keepalive 25s)

## Risks

- **Medium:** HTTP server choice — keep minimal (no heavy framework for MVP)
- **Low:** UFW rule conflict — isolated to wg0 interface
- **Low:** Service user creation — standard Linux pattern

## Not In Scope (Phase 5)

- Grafana dashboards (Phase 6)
- Alert rules (Phase 6)
- Metrics export endpoint (Phase 6)
- iTerm2 automation (v2)
- Auto-scaling workers (out of scope entirely)
