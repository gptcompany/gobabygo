# Docker Migration Plan

## Decisione

Router + Matrix bridge in Docker per stabilita, restart automatico, build riproducibile.
Worker restano systemd — i CLI (claude/codex/gemini) richiedono host diretto.

## Architettura

```
CF Tunnel --> 127.0.0.1:8780 --> [Docker: mesh-router]
                                       ^            ^
                                       |  HTTP      |  HTTP API polling
                     [systemd: mesh-worker@claude-work]   [Docker: mesh-matrix-bridge]
                     [systemd: mesh-worker@codex-work]
                     [systemd: mesh-worker@gemini-work]
```

## Perche solo router in Docker

- Router: stateless Python + SQLite, perfetto per container
- Worker: invocano CLI reali (claude, codex, gemini) con auth locale,
  sessioni, config in ~/.claude/ — Docker aggiungerebbe solo frizione
- Porta bindata su 127.0.0.1 (Docker bypassa UFW, non esporre a 0.0.0.0)
- CF Tunnel si connette a localhost:8780

## File

| File | Scopo |
|------|-------|
| `deploy/router.Dockerfile` | Multi-stage build, uv + Python 3.12 |
| `deploy/compose.yml` | Router + Matrix bridge + volume + healthcheck + restart |
| `deploy/mesh-matrix-bridge.docker.env` | Matrix bridge runtime config |
| `deploy/smoke-docker.sh` | Verifica container + health + workers |
| `uv.lock` | Lockfile per build deterministica |

## Operazioni

```bash
# Build e start
cd deploy && MESH_AUTH_TOKEN=xxx MESH_MATRIX_ACCESS_TOKEN=yyy docker compose up -d --build

# Verifica
./deploy/smoke-docker.sh

# Logs
docker logs mesh-router -f
docker logs mesh-matrix-bridge -f

# Restart
docker compose -f deploy/compose.yml restart
```

## Cosa NON cambia

- Codice applicativo (src/router/) — zero modifiche
- Worker systemd — identici
- Auth token, protocollo HTTP — identici
- Scaling worker: aggiungere istanze systemd come prima
