# OpenMemory Deployment — Muletto

Self-hosted shared memory layer for the mesh (Phase 21).

## Prerequisites

- Docker + Docker Compose on muletto (192.168.1.100)
- Ollama running locally with an embedding model (e.g., `nomic-embed-text`)
- (Optional) OPENAI_API_KEY in dotenvx SSOT for fallback embeddings

## Setup

```bash
# 1. Clone OpenMemory source (pinned to v1.2.3)
cd /opt  # or your preferred location on muletto
git clone --branch v1.2.3 --depth 1 https://github.com/CaviraOSS/OpenMemory.git openmemory-src

# 2. Copy env and configure
cp .env.example .env
# Edit .env if needed (Ollama defaults should work)

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

Add to operator's `~/.claude.json`:

```json
{
  "mcpServers": {
    "openmemory": {
      "type": "http",
      "url": "http://192.168.1.100:8080/mcp"
    }
  }
}
```

Or via CLI: `claude mcp add --transport http openmemory http://192.168.1.100:8080/mcp`

## Available MCP Tools

| Tool | Description |
|------|-------------|
| `openmemory_store` | Save a memory (decisions, summaries, context) |
| `openmemory_query` | Search memories by semantic similarity |
| `openmemory_list` | List all stored memories |
| `openmemory_get` | Fetch a specific memory by ID |
| `openmemory_reinforce` | Strengthen a memory's retention weight |

## Network

- LAN/VPN only (0.0.0.0:8080, no public exposure)
- No built-in auth (network isolation is the auth model for v1)
- Workers on mac-112 and ws-111 do NOT connect in v1

## Data

- SQLite database stored in Docker volume `openmemory_data`
- Persists across container restarts
- Backup: `docker cp openmemory:/data/openmemory.db ./openmemory-backup.db`
