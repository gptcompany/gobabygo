# Handoff

Date: `2026-03-09`

## Scope closed in this session

The operational recovery path was closed:

1. router `.100` recovery from clean release runtime
2. operator token drift fix on WS
3. worker service env drift fix on WS
4. stale busy worker cleanup in router DB

## What changed operationally

Router `.100`:

- clean release deployed under:
  - `/opt/mesh-router/releases/7070070`
  - `/opt/mesh-router/current`
- router container is up and healthy on `0.0.0.0:8780`
- UFW rule was added for `8780/tcp` from `192.168.1.0/24`
- dirty checkout under `/home/sam/work/gobabygo` was intentionally not used for deploy

WS `.111`:

- local operator env `~/.mesh/router.env` was updated to the live router token
- all `/etc/mesh-worker/*.env` files were updated to:
  - `MESH_ROUTER_URL=http://192.168.1.100:8780`
  - current live `MESH_AUTH_TOKEN`
- restarted:
  - `mesh-session-worker@mesh-session-claude-work`
  - `mesh-session-worker@mesh-session-codex-work`
- verified healthy after restart:
  - `ws-claude-session-dyn-01`
  - `ws-codex-session-dyn-01`

Router DB cleanup:

- stale worker `ws-claude-session-rektaslug-01` was deregistered

## Current live state

Current downstream thread:

- thread: `rektslug-spec-016-20260309-003627`
- thread_id: `8c9151d2-fea8-4293-8b43-00cd2884d605`
- repo: `/media/sam/1TB/rektslug`
- status: `failed`

Historical first step:

- task: `d3980f6a-bfe5-4026-9141-308365ecf7e9`
- session: `bd55bde4-9ea8-4118-9ddd-a16f04fd313b`
- target account used then: `claude-rektslug`

Interpretation:

- that run belongs to the pre-recovery state
- it should not be resumed in place
- next execution should be a fresh rerun using the centralized Claude pool in `mapping/account_pools.yaml`

## Verified current status

- router health: healthy
- queue depth: `0`
- live session workers:
  - `ws-claude-session-dyn-01: idle`
  - `ws-codex-session-dyn-01: idle`
- historical worker records remain in DB for audit, but are offline/stale only

## Why the system looked broken

Three things drifted at once:

1. router runtime moved to a new deployment on `.100`
2. operator shell token on WS was stale
3. systemd worker env files on WS still carried the old token

This produced:

- `401` in worker polling
- stale/busy-looking worker records
- confusion between a failed historical run and current control-plane health

The key point is that this was infrastructure drift, not a fundamental mesh design failure.

## Follow-up closed after recovery

Two operator-facing follow-ups were closed after the stack recovery:

1. `mesh ui` no longer opens identical blank shells for every pane
   - pane bootstrap is now centralized in `mapping/operator_ui.yaml`
   - role launcher is `scripts/mesh_ui_role_shell.sh`
2. `mesh status` no longer drowns the operator in stale historical workers
   - default view shows active/recent workers only
   - `mesh status --all` shows the full audit-heavy table
3. `lead` is now a first-class runtime role
   - router policy roles are now `boss`, `president`, `lead`, `worker`
   - `lead` can create tasks, dispatch tasks, and view all tasks
   - direct `president` ↔ `worker` communication remains allowed for compatibility

Important boundary:

- historical worker rows still exist in the router DB for audit
- the default operator view is now clean even if the DB keeps those rows
- Gemini session runtime now runs as `sam`, matching the validated local `ccs gemini` runtime
- latest worker code adds longer Claude Code prompt readiness wait, a short tmux send-settle delay, and stale tmux cleanup on retry
- Gemini smoke `9f67c914-3588-44c1-9001-2718791f0954` completed with `GEMINI_OK`; that earlier run still needed manual `Enter` plus `/exit` because the old worker runtime was still live
- after deploy of router release `7070070` and restart of the Gemini session worker, post-deploy smoke `40836700-a56c-4bb6-b1e5-a3f4b852f017` completed with `GEMINI_POSTDEPLOY_OK`
- this validates the full live path:
  - router `.100`
  - WS session worker
  - tmux session
  - `ccs gemini`
  - Claude Code frontend on Gemini provider
- session semantic is now explicit:
  - `session` tasks still default to staying open until the CLI exits
  - per-task payload can request auto-close with:
    - `auto_exit_on_success: true`
    - `success_marker` or `success_markers`
    - optional `exit_command`
- auto-exit smoke `4ed1cac5-3a76-42bf-a42a-0fd1967b7c9d` completed with `GEMINI_AUTOEXIT_OK` without manual `/exit`
- `upterm` attach path is now fixed:
  - worker logs are written under `~/.cache/gobabygo/upterm`
  - closed session `3cef0e56-9af3-43fb-b180-ff33c6b19cac` published:
    - `attach_kind: upterm`
    - `attach_target: ssh://uptermd.upterm.dev:22`
- if another redundant Gemini smoke is still live at next session start, clean it before opening new tests

## Next session checks

```bash
source ~/.mesh/router.env

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/threads/8c9151d2-fea8-4293-8b43-00cd2884d605/status" | python -m json.tool

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/sessions/bd55bde4-9ea8-4118-9ddd-a16f04fd313b" | python -m json.tool
./scripts/mesh status
```

Expected now:

- router healthy
- queue `0`
- `ws-claude-session-dyn-01` idle with fresh heartbeat
- `ws-codex-session-dyn-01` idle with fresh heartbeat

## Current live rerun

Fresh rerun started on `2026-03-10`:

- thread id: `2221bbf6-d743-4449-83fd-550bf1168b79`
- thread name: `rektslug-spec-016`
- running task: `4630be01-1b88-4433-b2b1-50792134ac3d`
- step `0`: `Speckit Specify spec-016`
- worker: `ws-claude-session-dyn-01`
- target account: `claude-samuele`
- session id: `7975a13e-4ab1-4f5b-bb25-03412256fcf4`

Operational note:

- `./scripts/mesh run rektslug 016` emitted `409 duplicate_thread_name` after creating the thread and all 20 step tasks
- treat this as launcher bug/noise until fixed
- the authoritative state is the router:
  - thread status is `active`
  - step `0` is `running`
  - queue depth is `0`

## 2026-03-10 follow-up fix in repo (not yet assumed live)

Three concrete issues were identified on the live rerun and fixed in repo:

1. composer submit race
   - symptom: `Speckit Specify` prompt remained typed in the bottom `❯` composer with no assistant turn
   - live confirmation: a manual tmux `Enter` immediately moved Claude into `✻ Herding…`
   - repo fix: session worker now polls the bottom-most composer and retries `Enter` until the composer clears

2. Claude rate-limit TUI blocker
   - symptom: session hit `You're out of extra usage` and stalled on `/rate-limit-options`
   - repo fix: live pane detection now maps that screen to `account_exhausted`, allowing scheduler rotation to the next Claude profile

3. router concurrent SQLite failure
   - symptom: live router `.100` returned `POST /tasks/complete -> 500` and intermittent `POST /heartbeat -> 500`
   - evidence: worker log reported `Task 4630be01-1b88-4433-b2b1-50792134ac3d completed` while router still showed step `0` as `running`
   - repo fix: RouterDB now serializes more read/write paths with the existing `RLock`

Current interpretation:

- repo state is ahead of live runtime
- until `.100` router and WS worker runtime are redeployed, `rektslug-spec-016` should be treated as contaminated by old runtime behavior
- next clean validation should be:
  1. deploy router + WS worker runtime from current `master`
  2. verify `/tasks/complete` and `/heartbeat` stop returning `500`
  3. rerun `./scripts/mesh run rektslug 016`

## Next operator step

1. monitor `rektslug-spec-016` instead of reopening the old failed thread
2. use `mesh ui rektslug` if you want the multi-panel operator layout while observing the rerun
3. fix the `duplicate_thread_name` launcher noise after the feature run is stable

## 2026-03-10 Gemini fresh-repo write follow-up

- fresh-repo Gemini write smoke remains the last live checkpoint for session-first repo mutation on `ccs gemini`
- what is already proven:
  - spawn/orchestration works: `mesh -> router -> ws-gemini-session-dyn-01 -> tmux -> ccs gemini`
  - text-only Gemini smoke already passed earlier (`GEMINI_OK`, `GEMINI_POSTDEPLOY_OK`, `GEMINI_AUTOEXIT_OK`)
- what fresh-repo write smoke exposed:
  1. `auto_exit_on_success` could falsely trigger when the success marker was already present in the prompt text
  2. after a manual/session-bus resend, `auto_exit` still reused the old baseline instead of re-arming on the new inbound prompt
- both fixes are now in repo:
  - [src/router/session_worker.py](/media/sam/1TB/gobabygo/src/router/session_worker.py)
  - [tests/router/test_session_worker.py](/media/sam/1TB/gobabygo/tests/router/test_session_worker.py)
  - local result: `pytest -q tests/router/test_session_worker.py` -> `78 passed`
- live runtime note:
  - Gemini worker on `.111` was redeployed and restarted after the fixes
  - a later follow-up widened the Claude Code start-screen detector again, because partial home-screen captures were still bypassing the earlier heuristic
  - current local result after that detector tweak: `pytest -q tests/router/test_session_worker.py` -> `80 passed`
  - a final fresh-repo write smoke still needs to be rerun from a clean temp repo on the newest worker runtime to confirm actual file creation before calling Gemini repo-write E2E closed
