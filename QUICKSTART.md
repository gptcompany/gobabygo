# GoBabyGo Mesh Router -- Quick Start

## Prerequisites

- Python 3.11+
- `pip install -e .` (installs pydantic, requests, etc.)

## 1. Start the Router

```bash
MESH_DEV_MODE=1 python -m src.router.server
```

Default: `http://localhost:8780`. Override with `MESH_ROUTER_PORT`.

Dev mode disables auth -- no token needed for registration.

## 2. Start a Worker

```bash
MESH_WORKER_ID=ws-claude-work-01 \
MESH_CLI_TYPE=claude \
MESH_ACCOUNT_PROFILE=work \
python -m src.router.worker_client
```

The worker registers itself, then long-polls `/tasks/next` waiting for work.

## 3. Check Status with meshctl

```bash
python -m src.meshctl status
```

Output:

```
WORKERS
ID         MACHINE      TYPE     STATUS     LAST HB      TASKS
ws-claud   workstation  claude   idle       2s ago       -

QUEUE
Queued: 0 | Workers: 1
Uptime: 45s
```

## 4. Submit a Task via curl

```bash
curl -s http://localhost:8780/health | python -m json.tool
```

Tasks are inserted directly into the DB by the orchestrator. For manual testing,
use the smoke test below or insert via Python:

```python
from src.router.db import RouterDB
from src.router.models import Task

db = RouterDB("/var/lib/mesh-router/router.db", check_same_thread=False)
task = Task(title="Hello world", phase="implement",
            target_cli="claude", target_account="work",
            idempotency_key="manual-001")
db.insert_task(task)
```

The scheduler dispatches it to the next eligible idle worker.

## 5. Run the Smoke Test

```bash
python -m pytest tests/smoke/test_e2e_live.py -v
```

This starts router + worker in-process and verifies the full lifecycle:
task creation, dispatch, ack, completion -- all in ~2 seconds.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MESH_ROUTER_PORT` | `8780` | Router HTTP port |
| `MESH_DB_PATH` | `/var/lib/mesh-router/router.db` | SQLite DB path |
| `MESH_DEV_MODE` | `""` | Set `1` to skip auth on `/register` |
| `MESH_AUTH_TOKEN` | `""` | Bearer token (shared by router, worker, meshctl) |
| `MESH_WORKER_ID` | `ws-unknown-01` | Unique worker identifier |
| `MESH_ROUTER_URL` | `http://localhost:8780` | Router URL (worker + meshctl) |
| `MESH_CLI_TYPE` | `claude` | Worker CLI type: `claude\|codex\|gemini` |
| `MESH_ACCOUNT_PROFILE` | `work` | Account profile for task matching |
| `MESH_LONGPOLL_TIMEOUT_S` | `25` | Long-poll block duration (seconds) |
