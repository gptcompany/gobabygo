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

Session-first routing policy (optional):

```bash
MESH_DEFAULT_EXECUTION_MODE=session
MESH_SESSION_FALLBACK_TO_BATCH=0   # session-first hard (no batch fallback)
MESH_ENFORCE_SESSION_ONLY=1        # reject batch tasks/steps at API level
```

With `MESH_DEFAULT_EXECUTION_MODE=session`, tasks created without explicit `execution_mode`
default to interactive session workers.

Token bootstrap helper (router + workers + local operator env):

```bash
./scripts/set-mesh-token.sh --generate \
  --vps-host root@10.0.0.1 \
  --ws-host sam@10.0.0.2 \
  --router-url http://10.0.0.1:8780
```

iTerm2 auto-start (Mac `.112`, optional):

1. Create dotenv file for operator shell (`~/.mesh/.env.mesh`):

```bash
mkdir -p ~/.mesh
cat > ~/.mesh/.env.mesh <<'EOF'
MESH_ROUTER_URL=http://10.0.0.1:8780
MESH_AUTH_TOKEN=REPLACE_WITH_REAL_TOKEN
EOF
chmod 600 ~/.mesh/.env.mesh
```

2. In iTerm2 profile settings: General -> Command -> Command:

```bash
/media/sam/1TB/gobabygo/scripts/iterm-mesh-shell.sh
```

Every new tab in that profile opens in `gobabygo` with mesh env loaded.

Note: repository deploy templates already enable this policy in
`deploy/mesh-router.env` (`MESH_DEFAULT_EXECUTION_MODE=session`,
`MESH_SESSION_FALLBACK_TO_BATCH=0`, `MESH_ENFORCE_SESSION_ONLY=1`).

## 2. Start a Worker

```bash
MESH_WORKER_ID=ws-claude-work-01 \
MESH_CLI_TYPE=claude \
MESH_ACCOUNT_PROFILE=work \
python -m src.router.worker_client
```

The worker registers itself, then long-polls `/tasks/next` waiting for work.
Account routing matches by:
- exact `MESH_ACCOUNT_PROFILE == task.target_account`
- or capability allowlist from `MESH_ALLOWED_ACCOUNTS` (`account:<name>` / `account:*`).

## 2b. Start an Interactive Session Worker (Claude/Codex)

Use this for tmux-backed interactive sessions (human can attach via iTerm2/tmux).

```bash
MESH_WORKER_ID=ws-claude-session-01 \
MESH_CLI_TYPE=claude \
MESH_ACCOUNT_PROFILE=work-claude \
MESH_EXECUTION_MODES=session \
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 \
python -m src.router.session_worker
```

Session workers persist session metadata/messages via router `/sessions/*`.
CLI approval prompts remain CLI-native (manual/yolo/etc.).

## 2c. Start an External Review Worker (Codex Verifier)

Use this worker to process tasks already in `review` state and call:
- `POST /tasks/review/approve`
- `POST /tasks/review/reject`

```bash
MESH_ROUTER_URL=http://localhost:8780 \
MESH_AUTH_TOKEN=... \
MESH_REVIEWER_ID=review-codex \
MESH_REVIEW_CLI_COMMAND="ccs codex --effort xhigh" \
MESH_ACCOUNT_PROFILE=review-codex \
python -m src.router.review_worker
```

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

Who executes commands in mesh:
- `BOSS` (human operator): starts orchestration (`meshctl pipeline create`, manual task/thread API calls).
- `PRESIDENT` (logical coordinator): authors/supervises interactive prompts inside session workflows.
- `session workers`: execute CLI commands in tmux/upterm for `execution_mode=session`.
- `review worker`: approves/rejects critical tasks in `review` state.

Pipeline orchestration example (from BOSS terminal):

```bash
dotenvx run -f ~/.mesh/.env.mesh -- python -m src.meshctl pipeline create \
  --template gsd \
  --thread-name "gsd-phase-17" \
  --repo /media/sam/1TB/gobabygo \
  --phase 17 \
  --project "AI Mesh Router" \
  --feature "session-first hard mode" \
  --account-scope repo
```

Optional shortcut for macOS operator shell:

```bash
alias meshctlx='dotenvx run -f ~/.mesh/.env.mesh -- python -m src.meshctl'
```

Interactive task example (`execution_mode=session`):

```bash
curl -s -X POST http://localhost:8780/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Interactive refactor with human oversight",
    "target_cli": "claude",
    "target_account": "work-claude",
    "execution_mode": "session",
    "payload": {"prompt": "Refactor auth module safely and ask before risky commands"}
  }'
```

Inspect sessions/messages:

```bash
curl -s http://localhost:8780/sessions | python -m json.tool
curl -s "http://localhost:8780/sessions/messages?session_id=<SESSION_ID>" | python -m json.tool
```

During execution, `session_worker` appends incremental CLI output to `/sessions/messages`
(`direction="out"`, `role="cli"`) using tmux pane deltas/snapshots, so operators can tail
progress without attaching immediately.

For a real `.111` (worker) + `.112` (iTerm2 operator) VPN-first validation run, use:
- `deploy/SESSION-FIRST-E2E-RUNBOOK.md`

Manual review API examples:

```bash
curl -s -X POST http://localhost:8780/tasks/review/approve \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"<TASK_ID>","verifier_id":"review-codex"}'

curl -s -X POST http://localhost:8780/tasks/review/reject \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"<TASK_ID>","verifier_id":"review-codex","reason":"missing tests"}'
```

Session control API examples (PTY bridge via bus):

```bash
curl -s -X POST http://localhost:8780/sessions/send-key \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<SESSION_ID>","key":"Up","repeat":1}'

curl -s -X POST http://localhost:8780/sessions/resize \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<SESSION_ID>","cols":120,"rows":40}'

curl -s -X POST http://localhost:8780/sessions/signal \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<SESSION_ID>","signal":"interrupt"}'
```

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
| `MESH_ACCOUNT_PROFILE` | `work` | Worker default account/profile identifier (still valid for exact-match routing) |
| `MESH_ALLOWED_ACCOUNTS` | `""` | Optional CSV allowlist published as capabilities (`foo,bar,*` -> `account:foo`, `account:bar`, `account:*`) for dynamic target account routing |
| `MESH_LONGPOLL_TIMEOUT_S` | `25` | Long-poll block duration (seconds) |
| `MESH_DEFAULT_EXECUTION_MODE` | `batch` | Router code default when task omits execution mode (`batch\|session`). Deploy template sets `session` in `deploy/mesh-router.env`. |
| `MESH_SESSION_FALLBACK_TO_BATCH` | `0` | Router code default. If `1`, session tasks may fallback to batch workers when no session worker is available. Deploy template keeps `0` for session-first hard mode. |
| `MESH_ENFORCE_SESSION_ONLY` | `0` | If `1`, router rejects any task/step with `execution_mode != session` (`400 session_only_mode`). Deploy template sets `1`. |
| `MESH_REVIEWER_ID` | `verifier-codex` | Verifier identity written to review events |
| `MESH_REVIEW_CLI_COMMAND` | `ccs codex --effort xhigh` | CLI command used by `review_worker` |
| `MESH_REVIEW_POLL_INTERVAL_S` | `8` | Review worker polling interval |
