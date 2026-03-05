# Phase 21: Shared Memory Layer — PLAN

## Goal

Deploy OpenMemory (CaviraOSS) as self-hosted shared memory service on muletto.
Configure MCP access for operator sessions only. Verify graceful degradation (MEM-04).

## Decisions Applied

See DECISIONS.md: muletto standalone Docker, BOSS/PRESIDENT only, manual writes,
on-demand reads, OpenAI embeddings, LAN-only, smoke test for MEM-04.

---

## Plan 21-01: Deploy OpenMemory on Muletto

**Deliverables:**
- `deploy/openmemory/compose.yml` — standalone Docker Compose for OpenMemory
- `deploy/openmemory/.env.example` — env template with required variables
- `deploy/openmemory/README.md` — deployment runbook (start/stop/upgrade)

**Tasks:**

1. Check latest stable tag from `ghcr.io/caviraoss/openmemory` (or GitHub releases).
   Create `deploy/openmemory/compose.yml` with:
   - Service `openmemory` pinned to a specific version tag (NOT `:latest`)
   - Port `8080` bound to `0.0.0.0:8080` (LAN accessible, no public exposure)
   - Volume `openmemory_data:/data` for persistent SQLite storage
   - Environment: `OM_PORT=8080`, `OM_DB_PATH=/data/openmemory.db`
   - Embedding provider: Ollama locale (modello embeddings già disponibile su muletto)
     Env: `OM_EMBEDDING_PROVIDER=ollama`, `OLLAMA_BASE_URL=http://host.docker.internal:11434` (o IP muletto)
   - Fallback: `OPENAI_API_KEY` (from dotenvx) se Ollama non disponibile
   - Healthcheck on `http://localhost:8080/health`
   - Restart policy: `unless-stopped`
   - Logging: json-file, 10m max, 3 files (consistent with router compose)
   - **Auth model**: LAN/VPN isolation only (no built-in auth). Documented as acceptable for v1.

2. Create `deploy/openmemory/.env.example` documenting required env vars:
   - `OM_PORT` (default 8080)
   - `OM_DB_PATH` (default /data/openmemory.db)
   - `OM_EMBEDDING_PROVIDER` (default `ollama`)
   - `OLLAMA_BASE_URL` (default `http://host.docker.internal:11434`)
   - `OPENAI_API_KEY` (optional fallback if Ollama unavailable)

3. Create `deploy/openmemory/README.md` with start/stop/upgrade commands.

**Verification:**
- `docker compose -f deploy/openmemory/compose.yml config` validates
- (On muletto) `docker compose up -d` starts, healthcheck passes

---

## Plan 21-02: Configure MCP Client Access

**Deliverables:**
- Updated topology example with memory config finalized
- Documentation of how to add OpenMemory MCP to operator's `.claude.json`

**Tasks:**

1. Update `deploy/topology.v1.4.example.yml` → confirm memory section:
   ```yaml
   memory:
     provider: openmemory_mcp
     mcp_server_name: openmemory
     endpoint: http://${OPENMEMORY_HOST:-192.168.1.100}:8080/mcp
     write_policy: best_effort
     required: false
     scope: operator_only
   ```

2. Add `deploy/openmemory/mcp-config-snippet.json` — copy-paste snippet for operator's `~/.claude.json`:
   ```json
   {
     "mcpServers": {
       "openmemory": {
         "type": "http",
         "url": "http://OPENMEMORY_HOST:8080/mcp"
       }
     }
   }
   ```
   Note: Replace `OPENMEMORY_HOST` with the actual muletto IP (192.168.1.100) or a DNS hostname.

3. Document in README that only BOSS/PRESIDENT sessions get this MCP config.
   Workers do NOT get OpenMemory MCP access in v1.

**Verification:**
- `claude mcp add --transport http openmemory http://192.168.1.100:8080/mcp` succeeds (when server is running)
- MCP tools `openmemory_query`, `openmemory_store`, `openmemory_list` appear in tool list

---

## Plan 21-03: Smoke Test — Graceful Degradation (MEM-04)

**Deliverables:**
- `tests/smoke/test_openmemory_degradation.py` — smoke test verifying MEM-04

**Tasks:**

1. Create smoke test that:
   - Verifies router operates normally (dispatch, ack, complete cycle) when OpenMemory is unreachable
   - Verifies MCP client config with unreachable OpenMemory doesn't cause Claude Code to hang or error
   - Verifies topology config with `required: false` is respected

2. Test structure:
   ```python
   def test_router_operates_without_openmemory():
       """MEM-04: Router dispatch/ack/complete works when OpenMemory is down."""
       # POST /tasks → 200
       # GET /tasks/{id}/poll → task assigned
       # POST /tasks/{id}/ack → 200
       # POST /tasks/{id}/complete → 200
       # No dependency on OpenMemory anywhere in this flow

   def test_topology_memory_not_required():
       """Memory config has required: false."""
       # Parse topology.v1.4.example.yml
       # Assert memory.required == false

   def test_mcp_client_handles_unreachable_openmemory():
       """MCP HTTP client to unreachable host times out gracefully."""
       # HTTP GET to http://192.168.1.100:8080/mcp (or unreachable host)
       # Expect connection refused / timeout within reasonable bound (< 5s)
       # No hang, no unhandled exception
   ```

**Verification:**
- `pytest tests/smoke/test_openmemory_degradation.py -v` passes

---

## Execution Order

| Wave | Plan | Dependency |
|------|------|-----------|
| 1 | 21-01 (Deploy config) | None |
| 1 | 21-03 (Smoke test) | None (tests router, not OpenMemory) |
| 2 | 21-02 (MCP config) | 21-01 (needs endpoint URL confirmed) |

Plans 21-01 and 21-03 can execute in parallel. Plan 21-02 follows.

---

## Out of Scope (Phase 21 v1)

- Automated write triggers (hooks, bridge integration)
- Worker MCP access
- Proactive memory injection
- Dashboard UI deployment
- Memory retention/decay policies
- Public exposure via Cloudflare Access

## UAT Criteria

- [ ] `deploy/openmemory/compose.yml` is valid and documented
- [ ] Topology example includes finalized memory config with endpoint
- [ ] MCP config snippet is documented for operator sessions
- [ ] Smoke test for MEM-04 passes (router independent of OpenMemory)
- [ ] No router code changes required (MCP is client-side only)
