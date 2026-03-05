# OpenMemory Deployment — Muletto

Self-hosted shared memory layer for the mesh (Phase 21).

## Prerequisites

- Docker + Docker Compose on muletto (192.168.1.100)
- Ollama running locally with an embedding model (e.g., `nomic-embed-text`)
- (Optional) OPENAI_API_KEY in dotenvx SSOT for fallback embeddings

## Setup

All commands run from `deploy/openmemory/` on muletto.

```bash
cd deploy/openmemory   # or wherever you placed this directory

# 1. Clone OpenMemory source (pinned to v1.2.3)
git clone --branch v1.2.3 --depth 1 https://github.com/CaviraOSS/OpenMemory.git openmemory-src

# 2. Copy env and configure
cp .env.example .env
# Edit .env: set OM_API_KEY to a random secret, verify OLLAMA_URL

# 3. Start
dotenvx run -f .env -- docker compose up -d --build

# 4. Verify
curl http://localhost:8080/health
```

## Operations

```bash
# Stop
docker compose down

# Logs
docker compose logs -f openmemory

# Restart
docker compose restart openmemory

# Upgrade to new version
cd openmemory-src && git fetch && git checkout v1.X.Y && cd ..
docker compose up -d --build
```

## MCP Access

Only BOSS/PRESIDENT operator sessions connect to OpenMemory via MCP.
Workers do NOT get OpenMemory MCP access in v1.

Use `OM_API_KEY` from `.env` as the MCP auth key for all clients.

### Claude Code

Add to operator's `~/.claude.json` (replace `REPLACE_WITH_OM_API_KEY` with the value from `.env`):

```json
{
  "mcpServers": {
    "openmemory": {
      "type": "http",
      "url": "http://192.168.1.100:8080/mcp",
      "headers": {
        "X-API-Key": "REPLACE_WITH_OM_API_KEY"
      }
    }
  }
}
```

Or via CLI: `claude mcp add --transport http openmemory http://192.168.1.100:8080/mcp`
(Note: CLI method may not pass auth headers; use JSON config above for authenticated access.)

### Codex CLI

Codex streamable HTTP MCP supports bearer auth via env var:

```bash
codex mcp add openmemory --url http://192.168.1.100:8080/mcp --bearer-token-env-var OPENMEMORY_API_KEY
export OPENMEMORY_API_KEY="REPLACE_WITH_OM_API_KEY"
codex mcp list
```

`OPENMEMORY_API_KEY` value must match `OM_API_KEY`.

### Gemini CLI

Add to operator's `~/.gemini/settings.json`:

```json
{
  "mcpServers": {
    "openmemory": {
      "type": "http",
      "url": "http://192.168.1.100:8080/mcp",
      "headers": {
        "X-API-Key": "REPLACE_WITH_OM_API_KEY"
      }
    }
  },
  "mcp": {
    "allowed": [
      "openmemory"
    ]
  }
}
```

If `mcp.allowed` already exists, append `"openmemory"` to the array.

## Available MCP Tools

| Tool | Description |
|------|-------------|
| `openmemory_store` | Save a memory (decisions, summaries, context) |
| `openmemory_query` | Search memories by semantic similarity |
| `openmemory_list` | List all stored memories |
| `openmemory_get` | Fetch a specific memory by ID |
| `openmemory_reinforce` | Strengthen a memory's retention weight |

## Security

- LAN/VPN only (0.0.0.0:8080, no public exposure)
- API key auth via `OM_API_KEY` env var (required, set in `.env`)
- All MCP requests authenticated by OpenMemory's built-in auth middleware
- Workers on mac-112 and ws-111 do NOT connect in v1

## Data

- SQLite database stored in Docker volume `openmemory_data`
- Persists across container restarts
- Backup: `docker cp openmemory:/data/openmemory.db ./openmemory-backup.db`
