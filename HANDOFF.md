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
- repeatable conservative cleanup is now available via:
  - `python -m src.meshctl cleanup stale-state`
  - `python -m src.meshctl cleanup stale-state --apply`
  - `python -m src.meshctl cleanup stale-state --include-taskless-sessions`
- Docker bridge live config is now meant to live outside git via:
  - `/etc/mesh-router/compose.env`
  - `/etc/mesh-router/mesh-matrix-bridge.docker.env`
  - `/etc/mesh-router/config/`
  - use `./deploy/live-compose.sh ...` for rebuild/restart
  - set `MESH_MATRIX_ALLOWED_SENDERS` there before enabling inbound room commands
- WS shared worker/runtime config is now meant to live in `/etc/mesh-worker/common.env`
  with role defaults in `/etc/mesh-worker/mesh-worker.batch.common.env` and
  `/etc/mesh-worker/mesh-session.common.env`, leaving only per-instance deltas
  in `/etc/mesh-worker/*.env`
- checked-in worker env templates now omit shared router/token/allowed-root keys
  and repeated batch/session defaults to reduce reintroduced drift

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
   - default role bootstrap now opens a real provider CLI when no live session exists
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
- `mesh ui` now also has live-attach semantics:
  - for a repo with an already-open session, panes try to attach to the matching tmux session instead of only opening static control shells
  - current mapping is conservative and prefers exact role matches before provider worker fallbacks
  - attach resolution also runs on the WS during pane bootstrap so Mac-side router reachability is no longer a blocker
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

- fresh-repo Gemini write smoke is now closed as a live checkpoint for session-first repo mutation on `ccs gemini`
- what is already proven:
  - spawn/orchestration works: `mesh -> router -> ws-gemini-session-dyn-01 -> tmux -> ccs gemini`
  - text-only Gemini smoke already passed earlier (`GEMINI_OK`, `GEMINI_POSTDEPLOY_OK`, `GEMINI_AUTOEXIT_OK`)
- what fresh-repo write smoke exposed:
  1. `auto_exit_on_success` could falsely trigger when the success marker was already present in the prompt text
  2. after a manual/session-bus resend, `auto_exit` still reused the old baseline instead of re-arming on the new inbound prompt
  3. a Claude Code home banner could remain visible while Gemini was already thinking or invoking tools, causing false prompt replays
- both fixes are now in repo:
  - [src/router/session_worker.py](/media/sam/1TB/gobabygo/src/router/session_worker.py)
  - [tests/router/test_session_worker.py](/media/sam/1TB/gobabygo/tests/router/test_session_worker.py)
  - local result after the full follow-up set: `pytest -q tests/router/test_session_worker.py` -> `84 passed`
- live runtime note:
  - Gemini worker on `.111` was redeployed and restarted after the fixes
  - a later follow-up widened the Claude Code start-screen detector again, because partial home-screen captures were still bypassing the earlier heuristic while Gemini was already doing work
  - session worker also now supports artifact-driven completion for smoke tasks using `success_file_path` and optional `success_file_contains`
- final live rerun:
  - task: `f9197066-ec87-4e45-a198-dbee9b90ba59`
  - status: `completed`
  - worker: `ws-gemini-session-dyn-01`
  - session: `ea96832c-c369-4df5-b63b-3eb0f14ae467`
  - repo: `/tmp/mesh-gemini-e2e10`
  - artifact: `/tmp/mesh-gemini-e2e10/GEMINI_E2E_OK.md`
  - content: `GEMINI_FILE_OK`

## 2026-03-10 Gemini team template E2E (`mesh start`)

- canonical smoke template now supports template-driven payload extras from `mapping/pipeline_templates.yaml`
- `gemini_team_demo` was upgraded from text-only prompts to deterministic file outputs with `auto_exit_on_success`
- live E2E run used the actual operator path:
  - repo: `/tmp/mesh-gemini-team-e2e-20260310-230556`
  - command: `MESH_PIPELINE_TEMPLATE=gemini_team_demo /media/sam/1TB/gobabygo/scripts/mesh start "snake game e2e"`
  - thread: `mesh-gemini-team-e2e-20260310-230556-snake-game-e2e-20260310-230556`
  - thread id: `7e2ee09f-8db8-4688-8b9f-d22849012115`
- task/result chain:
  - step 0 task `d6fd968c-8445-44a4-8a8b-54d81a6d3e7d` -> `completed`
  - step 1 task `f50f28f3-b6ab-42c7-92f4-f73ffe0973df` -> `completed`
  - step 2 task `45797c4f-ecfb-4037-a4bc-f8f27e0e51ba` -> `completed`
- artifacts verified on disk:
  - `/tmp/mesh-gemini-team-e2e-20260310-230556/lead_plan.md` contains `GEMINI_LEAD_OK`
  - `/tmp/mesh-gemini-team-e2e-20260310-230556/worker_review.md` contains `GEMINI_WORKER_OK`
  - `/tmp/mesh-gemini-team-e2e-20260310-230556/president_decision.md` contains `GEMINI_TEAM_OK`
- worker logs confirm artifact-driven auto-exit for all three Gemini session tasks
- known launcher bug still present:
  - `scripts/mesh start` creates the thread successfully and then can emit a late `409 duplicate_thread_name`
  - this is noise after creation; the live thread above still completed correctly
- review follow-up fixed:
  - stale success artifacts can no longer satisfy `auto_exit_on_success` on reruns
  - `session_worker.py` now requires `success_file_path` to be newer than the task start time before auto-exit triggers

## 2026-03-10 Factory Droid audit vs `claude-config`

Checked local repo:

- `/media/sam/1TB/claude-config`

Observed directly there:

- portable/high-likelihood assets:
  - `CLAUDE.md`
  - `agents/*.md`
  - many markdown command/skill assets under `commands/` and `skills/`
- migration-risk assets:
  - `hooks/hooks.json`
  - `settings.json`
  - scripts wired through Claude Code hook events and `$HOME/.claude/...` paths

Official references checked:

- `https://docs.factory.ai/cli/configuration/plugins`
- `https://docs.factory.ai/cli/configuration/custom-droids`
- `https://docs.factory.ai/reference/hooks-reference`

Conclusion:

- Factory Droid is a strong compatibility target for Claude-style agents/plugins
- it is not safe to claim `100%` compatibility with a hook-heavy `claude-config` repo without a dedicated migration pass

## 2026-03-11 follow-up: Gemini auth recovery and stale-artifact rerun proof

- live Gemini provider failure reproduced:
  - CLIProxy returned `500 auth_unavailable: no auth available`
  - this was provider/runtime state, not mesh orchestration and not the stale-file fix
- live recovery that worked under `sam`:
  - `ccs gemini --use samuele.morzenti`
  - `ccs cliproxy restart`
  - validation command returned success:
    - `ccs gemini --print -p "Reply with exactly GEMINI_AUTH_OK"` -> `GEMINI_AUTH_OK`
- stuck rerun-guard task recovered on the live tmux pane:
  - thread: `mesh-gemini-rerun-20260310-235052-rerun-guard-20260310-235052`
  - lead task: `24921f17-8749-48a1-8cab-955bdbad0293`
  - repo: `/tmp/mesh-gemini-rerun-20260310-235052`
  - artifact written after recovery: `lead_plan.md` with `GEMINI_LEAD_OK`
- final proof of the review fix used the repo that already contained all three success files:
  - repo: `/tmp/mesh-gemini-team-e2e-20260310-230556`
  - existing pre-rerun artifacts:
    - `lead_plan.md`
    - `worker_review.md`
    - `president_decision.md`
  - rerun thread:
    - `mesh-gemini-team-e2e-20260310-230556-rerun-stale-artifact-proof-20260311-001545`
    - thread id: `e38fd28d-b5b9-4680-8173-f22f188bd628`
  - tasks:
    - `45a31fe8-7981-4bd8-82d3-d99621c45620` lead
    - `0ead5ccf-a8e5-495e-886f-2b7bc7ad847f` worker
    - `534f3416-12f0-4872-855e-e4188463ecbb` president
- decisive live observation:
  - immediately after launch, step `0` was still `running`
  - Gemini worker was `busy`
  - stale `lead_plan.md` therefore did **not** trigger false auto-exit
- final rewritten artifact mtimes from the rerun:
  - `lead_plan.md` -> `1773188198.5144885830`
  - `worker_review.md` -> `1773188328.8796516460`
  - `president_decision.md` -> `1773188427.2637878150`
- worker journal confirms all three tasks completed via fresh artifact writes, not stale-file reuse
- residual blocker after this checkpoint:
  - router `.100` still shows intermittent timeouts on `/heartbeat` and `/sessions/messages`
  - this affects observability and helper commands more than execution

## 2026-03-11 cleanup and current operator state

- stale demo thread cleaned:
  - thread: `mesh-gemini-rerun-20260310-235052-rerun-guard-20260310-235052`
  - final state:
    - lead `completed`
    - worker `canceled`
    - president `canceled`
  - thread status now shows `failed` instead of misleading `active`
- temporary demo repos removed:
  - `/tmp/mesh-gemini-*`
  - `/tmp/mesh-*demo*`
  - `/media/sam/1TB/gobabygo/_mesh-snake-game-demo`
- workspace residue intentionally left untouched:
  - `Screenshot 2026-03-07 at 11.38.30.png`
- final live spot-check after cleanup:
  - `./scripts/mesh status --all` shows all three main workers idle
  - direct router probe returned fast `200` responses for:
    - `/health`
    - `/workers`
    - `/threads?limit=5`
- interpretation:
  - router timeout issue is real but intermittent
  - at the end of this turn the system is back in a clean, idle, Gemini-test-ready state

## 2026-03-11 launcher fix for `duplicate_thread_name`

- root cause:
  - `scripts/mesh` function `run_meshctl()` executed the `uv` path first
  - then fell through into the fallback `python3` path
  - this invoked `src.meshctl` twice and produced the misleading late `409 duplicate_thread_name`
- repo fix:
  - `run_meshctl()` now returns immediately after the first execution path
- regression test added:
  - `tests/test_deploy_scripts.py`
  - verifies `./scripts/mesh status` with fake `uv` does not fall through into `python3`
- live validation run after the fix:
  - repo: `/tmp/mesh-gemini-dupfix`
  - command:
    - `MESH_PIPELINE_TEMPLATE=gemini_team_demo /media/sam/1TB/gobabygo/scripts/mesh start 'dupfix e2e'`
  - thread:
    - `mesh-gemini-dupfix-dupfix-e2e-20260311-003958`
    - thread id: `b94692c3-3610-4961-a7c5-8f50c2a5e26a`
  - clean launcher output:
    - `Pipeline thread created: ...`
    - `Started thread: ...`
    - no `409 duplicate_thread_name`
  - final thread status:
    - `completed`
  - artifacts written:
    - `lead_plan.md`
    - `worker_review.md`
    - `president_decision.md`

## 2026-03-11 session worker timeout hardening

- repo change:
  - `src/router/session_worker.py`
  - default `heartbeat_timeout`: `3s -> 5s`
  - default `control_plane_timeout`: `15s -> 30s`
- deploy env templates updated:
  - `deploy/mesh-session-claude-work.env`
  - `deploy/mesh-session-codex-work.env`
  - `deploy/mesh-session-gemini-work.env`
  - `deploy/mesh-session-codex-review.env`
  - each now sets:
    - `MESH_HEARTBEAT_TIMEOUT_S=5`
    - `MESH_CONTROL_PLANE_TIMEOUT_S=30`
- live WS state:
  - the same values were written into `/etc/mesh-worker/*.env`
  - restarted:
    - `mesh-session-worker@mesh-session-claude-work`
    - `mesh-session-worker@mesh-session-codex-work`
    - `mesh-session-worker@mesh-session-gemini-work`
    - `mesh-session-worker@mesh-session-codex-review`
  - all restarted services came back `active`
- tests:
  - `pytest -q tests/router/test_session_worker.py`
  - `86 passed`
- live post-restart smoke:
  - repo: `/tmp/mesh-gemini-timeoutharden`
  - thread:
    - `mesh-gemini-timeoutharden-timeout-harden-check-20260311-005348`
    - thread id: `eb912cf2-56fb-4aed-8035-267337932a19`
  - result:
    - `lead` completed
    - `worker` completed
    - `president` stayed `running`
    - `president_decision.md` was never created
- interpretation:
  - the timeout hardening is deployed live
  - it does not regress the already-working Gemini path
  - but it does **not** close a separate Gemini president edge case on this specific smoke

## 2026-03-11 hardening pass

Repo fixes now in place and locally validated:

- `deregister_worker()` no longer requeues live `assigned`/`running` tasks. It fails them conservatively and clears worker/lease metadata to avoid dual execution on the same repo while the old tmux might still be alive.
- startup recovery now uses `fsm.apply_transition()` inside the recovery transaction instead of bypassing FSM guardrails
- `working_dir` is now bounded by `MESH_ALLOWED_WORK_DIRS` for both session and batch workers
- `mesh ui` fallback `worker-*` / `verifier` panes now warn explicitly when they are detached control shells instead of live worker sessions
- account exhaustion detection and router-side rotation now apply to `codex` and `gemini` too, not only `claude`
- scheduler dispatch now requires a fresh worker heartbeat before assigning a 5-minute lease
- tmux session naming uses a longer task fragment to reduce collision risk
- text-marker `auto_exit_on_success` is stricter:
  - no implicit text-marker auto-exit without opt-in
  - standalone marker lines only
  - stale artifact success remains blocked by the existing `mtime` guard

Local validation after the hardening pass:

- command:
  - `pytest -q tests/router/test_workdir_guard.py tests/router/test_worker_client.py tests/router/test_worker_manager.py tests/router/test_failure_classifier.py tests/router/test_recovery.py tests/router/test_scheduler.py tests/router/test_session_worker.py tests/test_mesh_ui_script.py`
- result:
  - `244 passed`

Known follow-up not yet implemented:

- there is still no remote kill/ack control channel for live tmux sessions during deregistration
- current safety policy is therefore "fail active task, do not requeue it"

## 2026-03-11 live deploy + Gemini post-deploy E2E

Deployment state closed:

- router `.100` now runs release `1478f35` from:
  - `/opt/mesh-router/releases/1478f35`
  - `/opt/mesh-router/current -> /opt/mesh-router/releases/1478f35`
- `mesh-router` container was rebuilt and came back `healthy`
- WS `.111` runtime files copied into:
  - `/opt/mesh-worker/src/router/`
- restarted session workers:
  - `mesh-session-worker@mesh-session-claude-work`
  - `mesh-session-worker@mesh-session-codex-work`
  - `mesh-session-worker@mesh-session-gemini-work`

Operational drift found live:

- `/etc/mesh-worker/*.env` still contained `MESH_AUTH_TOKEN=__REPLACE_WITH_TOKEN__`
- consequence:
  - session workers looped on `401 Unauthorized` during `/register`
- fix applied live:
  - replaced placeholder token with the live router token
  - rewrote `/home/sam/.mesh/router.env` with the same `.100` URL + token

Post-deploy Gemini-only validation:

1. first live thread:
   - `direct-gemini-101645`
   - failed correctly because `working_dir` was outside `MESH_ALLOWED_WORK_DIRS`
   - this validates the new path boundary enforcement
2. second live thread:
   - `allowed-gemini-103009`
   - failed correctly because `/tmp/mesh-tasks/...` was allowlisted but not writable by `sam`
   - root cause:
     - `mapping/provider_runtime.yaml` runs Gemini session workers as `sam`
     - at the time of the incident, `deploy/deploy-workers.sh` recreated `/tmp/mesh-tasks` as `mesh-worker:mesh-worker`
   - required live remediation before rerun:
     - make `/tmp/mesh-tasks` writable by `sam` again before launching the next Gemini smoke
     - reproducible example: `sudo chown -R sam:sam /tmp/mesh-tasks`
     - equivalent ACLs were also acceptable as a live workaround
   - follow-up repo fix:
     - `deploy/deploy-workers.sh`, `deploy/install.sh`, and `scripts/mesh bootstrap` now normalize `/tmp/mesh-tasks` for shared `mesh-worker`/`sam` access
     - worker unit templates now set `UMask=0002` so new repo content stays group-writable across provider-specific service users
3. final live thread:
   - `final-gemini-104529`
   - thread id: `7c8cd19a-c2fc-4dce-9a91-419124c2a48b`
   - repo: `/tmp/mesh-tasks/mesh-gemini-postdeploy-final`
   - all 3 steps `completed`

Final artifact verification on disk:

- `/tmp/mesh-tasks/mesh-gemini-postdeploy-final/lead_plan.md`
  - contains `GEMINI_LEAD_OK`
- `/tmp/mesh-tasks/mesh-gemini-postdeploy-final/worker_review.md`
  - contains `GEMINI_WORKER_OK`
- `/tmp/mesh-tasks/mesh-gemini-postdeploy-final/president_decision.md`
  - contains `GEMINI_TEAM_OK`

Interpretation:

- router deploy is live
- worker hardening is live
- provider path used for validation remained Gemini-only
- the new safety guards are not theoretical:
  - auth drift blocks worker registration
  - workdir boundary blocks out-of-scope repos
  - allowlisted but unwritable roots still fail fast
- after fixing those runtime preconditions, Gemini-only team E2E completes on the deployed stack

Canonical root validation follow-up:

4. canonical rerun after live env normalization:
   - live envs updated to include:
     - `MESH_ALLOWED_WORK_DIRS=/tmp/mesh-tasks,/media/sam/1TB`
   - canonical repo prepared at:
     - `/media/sam/1TB/mesh-gemini-canonical-smoke`
   - first canonical thread:
     - `mesh-gemini-canonical-smoke-canonical-gemini-smoke-20260311-1200-20260311-123550`
     - thread id: `d1ea02e1-ccab-4797-8aa6-f822310d7158`
   - result:
     - router accepted the canonical repo path under `/media/sam/1TB`
     - Gemini session worker opened the repo correctly
     - run eventually completed, but the first lead artifact was contaminated by an initial stuck session and was not a clean validation artifact set
5. root cause of the canonical smoke stall:
   - Gemini home rendered a suggestion row like `❯ Try "how do I log an error?"`
   - `session_worker.py` treated that line as pending composer text, so it only retried `Enter`
   - `_looks_like_start_screen()` also under-detected the Gemini home screen because it required `welcome back`
   - repo fix:
     - commit `b5103d1` `Handle Gemini home screen prompt bootstrap`
     - `_last_prompt_line_has_content()` now ignores Gemini home suggestion rows
     - `_looks_like_start_screen()` now accepts the Gemini home screen without requiring `welcome back`
   - targeted regression coverage:
     - `tests/router/test_session_worker.py`
6. final clean canonical rerun after deploying `b5103d1` live:
   - command:
     - `MESH_PIPELINE_TEMPLATE=gemini_team_demo ./scripts/mesh start "canonical gemini smoke rerun 20260311-1320"`
   - thread:
     - `mesh-gemini-canonical-smoke-canonical-gemini-smoke-rerun-20260311-1320-20260311-131941`
     - thread id: `7a06053e-18b3-4433-bafc-34a838acc891`
   - repo:
     - `/media/sam/1TB/mesh-gemini-canonical-smoke`
   - all 3 steps `completed`
   - artifact verification on disk:
     - `/media/sam/1TB/mesh-gemini-canonical-smoke/lead_plan.md`
       - contains `GEMINI_LEAD_OK`
     - `/media/sam/1TB/mesh-gemini-canonical-smoke/worker_review.md`
       - contains `GEMINI_WORKER_OK`
     - `/media/sam/1TB/mesh-gemini-canonical-smoke/president_decision.md`
       - contains `GEMINI_TEAM_OK`
