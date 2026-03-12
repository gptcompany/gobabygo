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
| `deploy/compose.env.example` | Optional compose-side overrides for external live config |
| `deploy/smoke-docker.sh` | Verifica container + health + workers |
| `uv.lock` | Lockfile per build deterministica |

## Operazioni

```bash
# Build e start
MESH_AUTH_TOKEN=xxx MESH_MATRIX_ACCESS_TOKEN=yyy docker compose -f deploy/compose.yml up -d --build

# Verifica
./deploy/smoke-docker.sh

# Logs
docker logs mesh-router -f
docker logs mesh-matrix-bridge -f

# Restart
docker compose -f deploy/compose.yml restart
```

## Live config without git drift

Per muletto, tieni tutta la config runtime Docker fuori dal checkout git:

```bash
sudo install -d -m 0755 /etc/mesh-router/config
sudo cp deploy/compose.env.example /etc/mesh-router/compose.env
sudoedit /etc/mesh-router/mesh-matrix-bridge.docker.env
sudo cp deploy/topology.v1.4.production.yml /etc/mesh-router/config/
sudo chown root:$USER /etc/mesh-router/compose.env
sudo chmod 0640 /etc/mesh-router/compose.env
```

`/etc/mesh-router/compose.env` deve contenere almeno:

```bash
MESH_AUTH_TOKEN=...
MESH_MATRIX_ACCESS_TOKEN=...
MESH_MATRIX_BRIDGE_DOCKER_ENV_FILE=/etc/mesh-router/mesh-matrix-bridge.docker.env
MESH_MATRIX_BRIDGE_CONFIG_DIR=/etc/mesh-router/config
```

Poi usa sempre:

```bash
./deploy/live-compose.sh up -d --build
./deploy/live-compose.sh restart
./deploy/live-compose.sh ps
```

In questo modo:
- token e override Compose non restano nel checkout
- room IDs / homeserver / topology path non dipendono dal worktree
- i rebuild da checkout pulito non perdono la config live
- il bridge continua a leggere il topology file da `/app/config/...`

Nota permessi:
- `deploy/live-compose.sh` gira come utente operatore, quindi `/etc/mesh-router/compose.env`
  deve essere leggibile da quell'utente

## Cosa NON cambia

- Codice applicativo (src/router/) — zero modifiche
- Worker systemd — identici
- Auth token, protocollo HTTP — identici
- Scaling worker: aggiungere istanze systemd come prima
