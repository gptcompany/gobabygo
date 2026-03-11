# GoBabyGo Mesh Router -- Quick Start

## Prerequisites

- Python 3.11+
- `pip install -e .` (installs pydantic, requests, etc.)
- Recommended on operator hosts: `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

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
MESH_ACCOUNT_PROFILE=claude-primary \
MESH_EXECUTION_MODES=session \
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 \
python -m src.router.session_worker
```

Session workers persist session metadata/messages via router `/sessions/*`.
CLI approval prompts remain CLI-native (manual/yolo/etc.).

Operational note:
- session worker Unix user is policy-driven, not hardcoded
- default policy runs Claude sessions as `sam` and Codex sessions as `mesh-worker`
- provider/runtime state therefore must exist under the Unix user selected in `mapping/provider_runtime.yaml`
- `MESH_ALLOWED_WORK_DIRS` should include every repo root you expect workers to enter; payload `working_dir` outside those roots is now rejected by both session and batch workers

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

Canonical template policy:

- built-in `gsd` and `speckit` now run as interactive teams, not mixed batch/session pipelines
- `lead` defaults to Claude for research, planning, artifact generation, and implementation
- `president` defaults to Codex for adjudication and review-heavy checkpoints
- `worker` sessions use Codex and Gemini for challenge, analyze, verify, and validate steps
- `speckit_codex` remains the fallback template when Claude is unavailable
- `gemini_team_demo` is the canonical smoke/demo template and should be used for all future tests to avoid consuming Claude/Codex quota
- `gemini_team_demo` writes `lead_plan.md`, `worker_review.md`, and `president_decision.md`, each with deterministic success markers and automatic session exit
- text-marker auto-exit without `success_file_path` is now opt-in only and only matches standalone marker lines, not arbitrary substrings printed by tools

Pipeline orchestration example (from BOSS terminal):

```bash
dotenvx run -f ~/.mesh/.env.mesh -- python -m src.meshctl pipeline create \
  --template gsd \
  --thread-name "gsd-phase-17" \
  --repo /media/sam/1TB/gobabygo \
  --phase 17 \
  --project "AI Mesh Router" \
  --feature "session-first hard mode"
```

Optional shortcut for macOS operator shell:

```bash
alias meshctlx='dotenvx run -f ~/.mesh/.env.mesh -- python -m src.meshctl'
```

One-time shell helpers (Mac/WS):

```bash
./scripts/install-shell-helpers.sh
source ~/.zshrc   # or source ~/.bashrc on bash hosts
```

This enables:
- `wss` / `wss <repo>` (quick SSH to WS)
- `wsattach <tmux-session>` (attach robusto: auto-detect utente tmux service)
- `mesh` (global wrapper to `gobabygo/scripts/mesh`)
- `mesh ui <repo>` (iTerm2 layout auto tabs/panes for BOSS/PRESIDENT/LEAD/WORKERS)
- `mesh sessions [repo|session|role]` (lista umana, router API-backed)
- `mesh attach [repo|session|role]` (picker semplice + attach senza ricordare il nome tmux)
- `yazi` / `lf` aliases to `yazicd` / `lfcd` (keep selected directory on exit)

Ultra-short operator commands:

```bash
mesh bootstrap
mesh deploy
mesh status
mesh status --all               # show historical stale/offline workers too
mesh sessions                   # list live sessions for current repo
mesh sessions --all             # list live sessions across repos
mesh sessions snake-game        # filter by repo / session / role
mesh attach                     # interactive attach for current repo
mesh attach --all               # interactive attach across repos
mesh attach snake-game          # same, filtered to target repo
mesh ui rektslug               # iTerm2 default operator view (2 tab: 3 pane + 3 pane)
mesh ui rektslug --single-tab  # iTerm2: one-tab, multi-pane
mesh ui rektslug --keep-existing  # keep prior mesh-ui tabs
mesh ui rektslug --roles boss,president,lead,worker-claude,worker-codex,worker-gemini,verifier --preset team-4x3  # legacy wide view
mesh start                      # one-command start (feature label auto-generated)
mesh run 016                    # existing spec/phase flow
mesh thread                     # show last thread for current repo
python -m src.meshctl task cancel <task-id> --reason "stuck queued"
python -m src.meshctl task fail <task-id> --reason "stuck review"
wss <repo>
wsattach <tmux-session>
```

`mesh sessions` / `mesh attach` use the router session API as source of truth. By default they scope to the current repo and only look at live/open sessions; use `--all` for cross-repo inspection. `wsattach` remains a low-level fallback when you already know the tmux session name.

`mesh bootstrap` now:
- keeps worker envs simple; runtime command resolution is policy-driven via `mapping/provider_runtime.yaml`
- enables `MESH_ALLOWED_ACCOUNTS=*`
- wires `MESH_UPTERM_BIN` automatically when `upterm` exists on WS
- normalizes `/home/mesh-worker/.ccs` and `/home/mesh-worker/.claude` ownership
- links `ccs` into `/usr/local/bin/ccs` when needed
- restarts session workers
- relies on session workers to preseed Claude repo metadata (`.claude.json`) at task start

Provider runtime policy:

```text
mapping/provider_runtime.yaml
```

Default behavior:
- `claude` -> real CCS account profile: `ccs {target_account}`
- `codex` -> provider direct: `ccs codex`
- `gemini` -> provider direct: `ccs gemini`
- Claude session worker service user -> `sam`
- Gemini session worker service user -> `sam`

Operator UI policy:

```text
mapping/operator_ui.yaml
```

Default behavior:
- `mesh ui` bootstraps each pane through `scripts/mesh_ui_role_shell.sh`
- `mesh ui` now auto-attaches role panes to matching live tmux sessions when the router already has an open session for the same repo/role
  - example: an active `lead` Codex step on repo `X` opens directly inside the `lead` pane
  - if no live session matches, the pane falls back to the normal static role shell
  - that fallback is explicitly labeled as a detached control shell on the WS so operators do not mistake it for the live worker runtime
  - live attach resolution is performed again on the WS during pane bootstrap, so it still works even when the Mac host cannot reach the router directly
- `mesh thread` without an explicit thread name now resolves the latest thread from router task metadata for the current repo path; it no longer depends on the thread name prefix matching the repo basename
- each role can run a different remote init command
- the policy is user-editable in one file instead of being hardcoded or split across env vars
- Codex session worker service user -> `mesh-worker`

Override/disable:
- `MESH_PROVIDER_RUNTIME_CONFIG=/abs/path/file.yaml`
- `MESH_PROVIDER_RUNTIME_CONFIG=""`

`mesh thread` resolves latest thread from router (`GET /threads`), not from local state files.
`mesh` auto-discovers router env in this order:
1. shell env (`MESH_ROUTER_URL`, `MESH_AUTH_TOKEN`)
2. `~/.mesh/.env.mesh`
3. `~/.mesh/router.env`
4. `/etc/mesh-worker/*.env` (WS fallback)

Examples:

```bash
# once after deploy/config drift
mesh bootstrap
mesh deploy

# from inside /media/sam/1TB/rektslug
mesh start
mesh thread

# existing numbered phase flow
mesh run 016
mesh thread
```

If `mesh deploy` chooses wrong host mode:

```bash
MESH_DEPLOY_MODE=remote mesh deploy
```

WS host override:

```bash
MESH_WS_HOST=sam@192.168.1.111 mesh deploy
```

iTerm2 Python API setup (Mac only, one-time):

```bash
pip3 install iterm2
mesh ui rektslug --max-panes-per-tab 5
```

`mesh ui` now auto-falls back to `uv run --with iterm2 ...` if module `iterm2`
is missing and `uv` is available.
By default it replaces previous mesh-ui tabs to avoid tab accumulation.

From WS/Linux, `mesh ui ...` auto-forwards to Mac operator host by default
(`MESH_UI_FORWARD_HOST=sam@192.168.1.112`).

If Claude is disabled, switch to codex-only pipeline:

```bash
export MESH_PIPELINE_TEMPLATE=speckit_codex
```

For smoke/demo tests, use Gemini only:

```bash
export MESH_PIPELINE_TEMPLATE=gemini_team_demo
mesh start "snake game demo"
```

Canonical E2E smoke expectation:
- step 0 (`lead`) writes `lead_plan.md` with `GEMINI_LEAD_OK`
- step 1 (`worker`) writes `worker_review.md` with `GEMINI_WORKER_OK`
- step 2 (`president`) writes `president_decision.md` with `GEMINI_TEAM_OK`
- each Gemini session auto-exits when its expected file marker is present

If you need explicit path/name mode:

```bash
mesh run rektslug 016
mesh run /media/sam/1TB/rektslug 016
```

UV-first execution:
- `scripts/mesh` now prefers `uv run -- python -m src.meshctl ...` when `uv` is available.
- fallback remains plain `python3/python` if `uv` is not installed.

CCS profile isolation (recommended for account-scoped history/context):

```bash
ccs auth create claude-samuele
ccs auth create claude-gptprojectmanager
```

Then set Claude task accounts to those real CCS profiles and keep them `isolated`.
Default account selection is now controlled centrally in:

- `mapping/account_pools.yaml`
- `mapping/provider_runtime.yaml`

Bootstrap also reads `mapping/provider_runtime.yaml` to install per-instance
systemd overrides for session worker Unix users.

Interactive task example (`execution_mode=session`):

```bash
curl -s -X POST http://localhost:8780/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Interactive refactor with human oversight",
    "target_cli": "claude",
    "target_account": "claude-samuele",
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

Verified live behavior on the current stack:
- router dispatches to real tmux-backed session workers
- repo `working_dir` is honored when the path is correct
- long-lived interactive sessions now renew leases on heartbeat and are not requeued after the 5-minute lease window
- real account-scoped Claude CCS profiles are supported (`ccs <profile>`, not `ccs claude`)
- Claude limit recovery now rotates across those isolated profiles on retry when worker output matches `429`, `You've hit your limit`, `You're out of extra usage`, or `rate limit error`
- Codex/Gemini quota detection now feeds the same `account_exhausted` retry path when their output matches provider-specific rate-limit or quota strings
- scheduler dispatch now requires a fresh worker heartbeat before leasing work to an `idle` worker
- Docker router reachability is controlled by `MESH_ROUTER_BIND_HOST` in `deploy/compose.yml`; for multi-host WS/router setups it must not stay pinned to `127.0.0.1`

Known operational gaps:
- `upterm` is installed on WS; attach URL discovery is implemented in code, but if workers still log `upterm binary not found ...` the running service has not picked up the latest code yet
- brand-new CCS profiles still require one real login/bootstrap under the Unix user that runs that provider
- if `GET /sessions/messages` returns `500 {"details":"bad parameter or other API misuse"}`, the live router is still running the pre-fix session DB path and needs redeploy
- session workers preseed Claude project trust/onboarding/MCP metadata automatically; remaining drift is provider/profile bootstrap, not the router bus
- `ccs codex` and `ccs gemini` still present Claude Code UX. If the prompt is visibly typed but not submitted, treat it as a tmux/TUI timing issue first.

Current real pipeline snapshot:

- thread: `rektslug-spec-016-20260309-003627`
- thread id: `8c9151d2-fea8-4293-8b43-00cd2884d605`
- active step 0 task: `d3980f6a-bfe5-4026-9141-308365ecf7e9`
- session id: `bd55bde4-9ea8-4118-9ddd-a16f04fd313b`
- repo: `/media/sam/1TB/rektslug`

Useful live checks:

```bash
source ~/.mesh/router.env

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/threads/8c9151d2-fea8-4293-8b43-00cd2884d605/status" | python -m json.tool

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/sessions/bd55bde4-9ea8-4118-9ddd-a16f04fd313b" | python -m json.tool

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/sessions/messages?session_id=bd55bde4-9ea8-4118-9ddd-a16f04fd313b&after_seq=630&limit=200" | python -m json.tool
```

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

## Manual Session Smoke

For ad-hoc live validation against a session worker, prefer explicit session mode:

```bash
source ~/.mesh/router.env
python -m src.meshctl submit \
  --title "Gemini Smoke" \
  --cli gemini \
  --account gemini \
  --phase test \
  --mode session \
  --payload '{"prompt":"Reply with exactly GEMINI_SMOKE_OK.","working_dir":"/media/sam/1TB/gobabygo","auto_exit_on_success":true,"success_marker":"GEMINI_SMOKE_OK"}'
```

Note:
- `session` tasks stay open by default until the CLI exits
- for smoke tests, prefer `auto_exit_on_success=true` with a deterministic `success_marker`
- optional payload field `exit_command` defaults to `/exit`

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
| `MESH_PROVIDER_RUNTIME_CONFIG` | repo default | Optional provider runtime policy file. `""` disables central policy and falls back to `MESH_CLI_COMMAND`. |
| `MESH_RUNTIME_STATE_DIR` | `~/.cache/gobabygo` | Session worker writable state dir used for helper files such as `upterm` logs. |
| `MESH_LONGPOLL_TIMEOUT_S` | `25` | Long-poll block duration (seconds) |
| `MESH_DEFAULT_EXECUTION_MODE` | `batch` | Router code default when task omits execution mode (`batch\|session`). Deploy template sets `session` in `deploy/mesh-router.env`. |
| `MESH_SESSION_FALLBACK_TO_BATCH` | `0` | Router code default. If `1`, session tasks may fallback to batch workers when no session worker is available. Deploy template keeps `0` for session-first hard mode. |
| `MESH_ENFORCE_SESSION_ONLY` | `0` | If `1`, router rejects any task/step with `execution_mode != session` (`400 session_only_mode`). Deploy template sets `1`. |
| `MESH_REVIEWER_ID` | `verifier-codex` | Verifier identity written to review events |
| `MESH_REVIEW_CLI_COMMAND` | `ccs codex --effort xhigh` | CLI command used by `review_worker` |
| `MESH_REVIEW_POLL_INTERVAL_S` | `8` | Review worker polling interval |

## Current Limitations

- `mesh ui` is operator UX plus live attach when available; it is not the source of truth for orchestration state.
- router DB/task/thread state still wins over what a pane appears to show.
- `wss` / `wsattach` now enable more aggressive SSH keepalive + control persist by default (`15s`, `count=12`, `ControlPersist=30m`) to reduce idle pane freezes in iTerm2.
- `mesh status` hides historical stale/offline worker rows by default; use `mesh status --all` when you need the full audit-heavy view.
- If tmux is alive but the task requeues after ~5 minutes, router or worker is still running old code without lease renewal.
- If a task opens tmux and then blocks on theme/security/trust-folder/MCP prompts, the problem is unattended CLI bootstrap under `mesh-worker`.
- If the initial Claude prompt remains visibly typed in the bottom `❯` composer with no assistant turn, deploy the latest worker code: the session worker now retries `Enter` automatically until the composer clears.
- If Claude lands on the `You're out of extra usage` / `/rate-limit-options` screen, deploy the latest worker code: the session worker now classifies that live TUI state as `account_exhausted` so the router can rotate to the next isolated Claude profile.
- If you need ad-hoc session tasks to finish without manual `/exit`, set `auto_exit_on_success=true` with a deterministic `success_marker`.
- If router `.100` shows `POST /tasks/complete -> 500` or intermittent `POST /heartbeat -> 500`, deploy the latest RouterDB locking changes before debugging task logic; the symptom matched concurrent SQLite access on a shared connection.
- `meshctl task cancel|fail` is intentionally conservative:
  - safe for `queued`, `assigned`, `blocked`, `review`
  - rejects `running` tasks because the live tmux session may still be executing
- A clean unattended demo still requires:
  - preseeded Claude/Codex runtime under `/home/mesh-worker`
  - unattended CLI bootstrap under `mesh-worker` (no theme/security/trust-folder/MCP first-run prompts)
