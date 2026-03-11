# CLAUDE.md

## Operator Mode (BOSS)

Use GoBabyGo as orchestration control-plane, not manual copy/paste between CLIs.

Primary docs for restart/resume in this repo:

- `README.md`
- `ARCHITECTURE.md`
- `CLAUDE.md`

`HANDOFF.md` is supplemental session log, not the only canonical source.

### Core principles

- Source of truth: router DB + task/thread state.
- Runtime execution: session workers in `tmux`.
- iTerm2: operator UX only (attach/split/observe), not orchestration state.
- Default policy: session-first (`MESH_SESSION_FALLBACK_TO_BATCH=0`).

## Verified Live Status

Verified on the real `.100` router + `.111` WS stack:

- router dispatches session tasks to tmux-backed session workers
- `working_dir` is honored by session workers when the repo path is correct
- `working_dir` is now bounded by `MESH_ALLOWED_WORK_DIRS`; out-of-bounds payload paths are rejected before tmux or subprocess execution
- worker deregistration and periodic recovery are live
- worker deregistration no longer requeues active tasks; it now fails them conservatively until a real remote tmux-kill handshake exists
- lease renewal on heartbeat is implemented and tested, so healthy long-lived sessions are no longer requeued after the 5-minute lease window
- `claude` runtime resolution is policy-driven (`ccs {target_account}` for real CCS profiles)
- `codex` and `gemini` runtime resolution use Claude Code as frontend plus CCS/CLIProxy provider routing
- session worker runtime on WS now uses current Claude Code (`/usr/local/bin/claude`, not the stale `/usr/bin/claude`)
- router `.100` is back on a clean release runtime, not the dirty checkout under `/home/sam/work/gobabygo`
- WS local shell auth and WS worker service auth have been realigned to the same live router token
- current healthy session workers are:
  - `ws-claude-session-dyn-01`
  - `ws-codex-session-dyn-01`
  - `ws-gemini-session-dyn-01`

Not yet production-clean:

- `/sessions/messages` fix is committed/tested locally; if the live router still returns `500 bad parameter or other API misuse`, `.100` is still running the old runtime
- `upterm` launch logging is fixed in code; if the worker still logs `upterm binary not found ...` for an existing binary, the worker runtime has not been restarted on the new code yet
- brand-new Claude CCS profiles still need one first login/bootstrap in their own instance
- session worker Unix user must match where that provider/runtime state actually lives
- Gemini session runtime now runs as `sam`
- initial Gemini smoke `9f67c914-3588-44c1-9001-2718791f0954` produced `GEMINI_OK` via `ccs gemini` under Claude Code frontend; that earlier run still needed manual `Enter` plus `/exit` because the old worker runtime was still live
- post-deploy Gemini smoke `40836700-a56c-4bb6-b1e5-a3f4b852f017` produced `GEMINI_POSTDEPLOY_OK` on router release `7070070` with the new worker runtime
- `session` tasks still default to staying open until the CLI exits
- task payload now supports `auto_exit_on_success=true` plus `success_marker`/`success_markers` and optional `exit_command`
- auto-exit Gemini smoke `4ed1cac5-3a76-42bf-a42a-0fd1967b7c9d` completed with `GEMINI_AUTOEXIT_OK` without manual `/exit`
- `upterm` logs now live under `~/.cache/gobabygo/upterm`; Gemini session `3cef0e56-9af3-43fb-b180-ff33c6b19cac` published a real attach handle via `attach_kind=upterm`
- if a redundant Gemini smoke task is still `running` or `assigned` at the next session start, clean it before new tests instead of treating it as a provider/auth regression
- fresh-repo Gemini write smoke uncovered two distinct bugs in `session_worker.py`:
  - `auto_exit_on_success` could falsely trigger when `success_marker` already appeared inside the prompt text
  - after a manual/session-bus resend, `auto_exit` still reused the old baseline instead of re-arming on the new inbound prompt
- the start-screen detector was then widened again because partial Claude Code home captures were slipping past the earlier heuristic while Gemini was already thinking or invoking tools
- fresh-repo Gemini write rerun `f9197066-ec87-4e45-a198-dbee9b90ba59` is now `completed`
  - repo: `/tmp/mesh-gemini-e2e10`
  - worker: `ws-gemini-session-dyn-01`
  - session: `ea96832c-c369-4df5-b63b-3eb0f14ae467`
  - artifact written: `/tmp/mesh-gemini-e2e10/GEMINI_E2E_OK.md`
  - content: `GEMINI_FILE_OK`
- session worker now also supports artifact-driven smoke completion with:
  - `auto_exit_on_success: true`
  - `success_file_path`
  - optional `success_file_contains`
- session worker timeouts are now less aggressive by default:
  - `MESH_HEARTBEAT_TIMEOUT_S=5`
  - `MESH_CONTROL_PLANE_TIMEOUT_S=30`
  - the same values were applied live to the WS session worker env files and the services were restarted
- post-hardening Gemini smoke `mesh-gemini-timeoutharden-timeout-harden-check-20260311-005348` is intentionally **not** treated as green:
  - `lead` completed
  - `worker` completed
  - `president` remained `running` without producing `president_decision.md`
  - this means timeout hardening is live, but one Gemini president edge case still exists under the current runtime
- several offline historical worker records still remain in the router DB for audit history; they are not active incidents by themselves

## Factory Droid Compatibility

Official references checked:

- Factory plugins: `https://docs.factory.ai/cli/configuration/plugins`
- Factory custom droids / Claude agent import: `https://docs.factory.ai/cli/configuration/custom-droids`
- Factory hook model: `https://docs.factory.ai/reference/hooks-reference`

Practical conclusion for `/media/sam/1TB/claude-config`:

- high compatibility:
  - `CLAUDE.md`
  - `.claude/agents`-style markdown agents
  - many command/skill markdown assets
- not safe to assume `100%` compatibility:
  - Claude Code hook lifecycle in `hooks/hooks.json`
  - `settings.json` matchers and hook wiring
  - scripts that assume `$HOME/.claude/...` paths or Claude-specific event names

Use Factory Droid as a strong reference and likely migration target for agents/plugins, not as a guaranteed drop-in replacement for a hook-heavy `claude-config` repo.

## Current Handoff Snapshot

Tracked downstream pipeline:

- thread: `rektslug-spec-016-20260309-003627`
- thread_id: `8c9151d2-fea8-4293-8b43-00cd2884d605`
- step 0 task: `d3980f6a-bfe5-4026-9141-308365ecf7e9`
- step 0 session: `bd55bde4-9ea8-4118-9ddd-a16f04fd313b`
- current thread status: `failed`
- repo: `/media/sam/1TB/rektslug`

Important boundary:

- `spec-016` belongs to `rektslug`
- precise orchestration continuity for that run belongs to `gobabygo`
- that is why this repo documents the thread/task/session IDs explicitly

Observed before the latest recovery work:

- `GET /sessions/messages` on the live router alternated between:
  - `404 session_not_found`
  - `500 {"details":"bad parameter or other API misuse"}`
  - `500 {"details":"the JSON object must be str, bytes or bytearray, not NoneType"}`
- direct `GET /sessions/<id>` still returned the session record
- this is consistent with:
  - shared SQLite connection misuse on the router session-message path
  - legacy/dirty metadata rows decoding as `None`

Committed fix in this session:

- `RouterDB` now serializes session CRUD/message access with an `RLock`
- `RouterDB` now tolerates `NULL` / empty metadata blobs on read
- `session_worker` treats router `404 session_not_found` as terminal and stops polling instead of spamming forever
- `session_worker` now logs real `OSError` details for `upterm` launch failures instead of collapsing everything into `binary not found`

Operational recovery completed after those code fixes:

- deployed router `.100` from a clean release path:
  - `/opt/mesh-router/releases/86c3f2b`
- external router bind exposed on `0.0.0.0:8780`
- UFW on `.100` allows `8780/tcp` from `192.168.1.0/24`
- WS local operator file `~/.mesh/router.env` was updated to the live router token
- WS service files `/etc/mesh-worker/*.env` were updated to the same router URL/token
- session workers were restarted and now register correctly
- stale busy worker `ws-claude-session-rektaslug-01` was deregistered

Interpretation:

- the old `spec-016` run is no longer blocked by control-plane drift
- it is still a failed historical run using old account targeting (`claude-rektslug`)
- the correct next step is a fresh rerun using the centralized account pool

## Provider Runtime Policy

Runtime resolution is centralized in:

`mapping/provider_runtime.yaml`

Default policy:
- `claude` -> real CCS account profile: `ccs {target_account}`
- `codex` -> CLIProxy provider direct: `ccs codex`
- `gemini` -> CLIProxy provider direct: `ccs gemini`
- Claude session worker service user defaults to `sam`
- Codex session worker service user defaults to `mesh-worker`
- Gemini session worker service user defaults to `sam`

If CCS changes syntax later, edit this file instead of patching worker code.

Optional override:
- `MESH_PROVIDER_RUNTIME_CONFIG=/abs/path/file.yaml`
- `MESH_PROVIDER_RUNTIME_CONFIG=""` to disable the policy file and fall back to `MESH_CLI_COMMAND`

## Bootstrap (one command)

After deploy/config drift, run once from BOSS host:

```bash
mesh bootstrap
```

This automatically:
- keeps WS worker envs simple; runtime command resolution is now policy-driven via `mapping/provider_runtime.yaml`
- enables `MESH_ALLOWED_ACCOUNTS=*`
- wires `MESH_UPTERM_BIN` automatically when `upterm` exists on WS
- normalizes `/home/mesh-worker/.ccs` and `/home/mesh-worker/.claude` ownership
- applies instance-specific systemd overrides for session worker Unix users from `mapping/provider_runtime.yaml`
- links `ccs` into `/usr/local/bin/ccs` when only the operator npm-global install exists
- restarts session workers
- relies on session workers to preseed Claude project metadata (`.claude.json`) per repo at task start

Optional: set `MESH_BOOTSTRAP_STOP_BATCH=1` before running bootstrap to stop batch workers.

## Auto Deploy

From Mac BOSS host:

```bash
mesh deploy
```

Behavior:
- updates WS repo (`/opt/mesh-router`) via `git pull --ff-only`
- syncs python editable install in WS venv
- restarts router
- restarts session workers only if no `mesh-*` tmux sessions are detected

Controls:
- `MESH_WS_HOST` (default `sam@192.168.1.111`)
- `MESH_DEPLOY_MODE=auto|remote|local` (use `remote` from Mac if needed)
- `MESH_PIPELINE_TEMPLATE=speckit_codex` when Claude is unavailable
- for smoke/demo validation, prefer `MESH_PIPELINE_TEMPLATE=gemini_team_demo` to avoid burning Claude/Codex quota

Canonical template model:
- built-in `gsd` and `speckit` are now session-first team templates
- `lead` work is Claude-first
- `president` adjudication is Codex-first
- `worker` challenge/validation uses Codex and Gemini session workers
- `speckit_codex` remains the pure-Codex fallback when Claude capacity is unavailable
- `gemini_team_demo` is the canonical cheap smoke/demo template and should be preferred for future test runs
- `gemini_team_demo` now writes deterministic artifacts and auto-exits each step:
  - `lead_plan.md` with `GEMINI_LEAD_OK`
  - `worker_review.md` with `GEMINI_WORKER_OK`
  - `president_decision.md` with `GEMINI_TEAM_OK`
- artifact-driven auto-exit is now guarded against stale reruns: a pre-existing success file only counts if it was created or modified after the current task started

## Minimal Daily Flow

From the target repo directory on WS:

```bash
mesh start
mesh thread

# existing numbered flow
mesh run 016
mesh thread
```

Examples:

```bash
mesh start
mesh thread
mesh run 016
mesh thread
```

No hardcoded path is required when run from inside the repo.
`mesh thread` resolves latest thread from router (server-side), not from local state files.
If `mesh start` has no arguments, feature label is auto-generated per run.

Current recommendation for `rektslug/spec-016`:

- do not try to revive `rektslug-spec-016-20260309-003627` in place
- start a fresh run after confirming the Claude pool order in `mapping/account_pools.yaml`

Admin cleanup for stuck tasks:

```bash
python -m src.meshctl task cancel <task-id> --reason "stuck queued"
python -m src.meshctl task fail <task-id> --reason "stuck review"
```

Use these for non-running tasks. They are intentionally conservative:
- safe for `queued`, `assigned`, `blocked`, `review`
- they reject `running` tasks, because a live tmux session may still be executing

## Required Helpers

Install once on each host (Mac + WS):

```bash
./scripts/install-shell-helpers.sh
source ~/.zshrc   # or ~/.bashrc
```

Provided commands:

- `mesh` -> global wrapper for `scripts/mesh`
- `mesh ui <repo>` -> iTerm2 Python API layout (tabs/panes for operator roles)
- `wss` / `wss <repo>` -> SSH WS shortcut
- `wsattach <tmux-session>` -> attach tmux on WS (auto-detect service user)
- `yazi`/`lf` -> mapped to `yazicd`/`lfcd` (keep selected directory)

Current `mesh ui` behavior:

- pane boot commands are centralized in `mapping/operator_ui.yaml`
- default launcher is `scripts/mesh_ui_role_shell.sh`
- each role can have its own non-destructive bootstrap command
- if the router already has an open session for the same repo and matching role/provider, the pane now auto-attaches to the live tmux session instead of staying a static shell
- exact role matches win first (`lead`, `president`, `verifier`, etc.); provider worker panes only attach when no higher-priority role already owns that same live session
- live attach resolution also runs on the WS during pane bootstrap, so it still works when the Mac operator host cannot reach the router directly
- `mesh thread` with no explicit thread name now resolves the latest thread by current repo task metadata, not by assuming the thread name starts with the repo basename
- if attach is not possible, `worker-*` and `verifier` panes explicitly warn that they are detached control shells on the WS, not the live worker runtime
- this closes the gap where every pane previously opened as the same blank shell

Mac iTerm2 setup (one-time):

```bash
pip3 install iterm2
mesh ui rektslug --max-panes-per-tab 5
```

When launched from WS/Linux, `mesh ui ...` auto-forwards to Mac operator host
(`MESH_UI_FORWARD_HOST`, default `sam@192.168.1.112`).
If `iterm2` Python module is missing, `mesh ui` auto-tries
`uv run --with iterm2` when `uv` is installed.
Default behavior replaces old mesh-ui tabs; use `--keep-existing` to preserve them.
Default preset is `team-4x3` (2 tab: 4 panes + 3 panes). Use `--preset auto`
to restore chunking by `--max-panes-per-tab`.

## Python Runtime

`scripts/mesh` is UV-first:

- if `uv` exists: uses `uv run -- python -m src.meshctl ...`
- fallback: `python3/python`

Recommended on operator hosts:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --frozen
```

## CCS Profile Isolation

Use account-scoped Claude CCS profiles, not repo-scoped naming.

Recommended model:

```bash
ccs auth create claude-samuele
ccs auth create claude-gptprojectmanager
ccs auth create claude-gptcoderassistant
```

Then keep those profiles `isolated` and select them per task/session.
Default provider account selection is controlled centrally in:

- `mapping/account_pools.yaml`
- `mapping/provider_runtime.yaml`

Important runtime note:

- repo/account profiles under `/home/sam/.ccs` are not sufficient by themselves
- session worker auth/state must exist under the Unix user selected in `mapping/provider_runtime.yaml`
- default policy runs Claude sessions as `sam`, so Claude account profiles should live under `/home/sam/.ccs`
- default policy runs Codex sessions as `mesh-worker`, so Codex CLIProxy state should live under `/home/mesh-worker/.ccs`
- default policy runs Gemini sessions as `sam`, so Gemini CLIProxy state should live under `/home/sam/.ccs`
- otherwise tasks can dispatch correctly but still fail later on provider auth/bootstrap
- Claude profile rotation on limit is handled by the router, not by `ccs claude`: keep the isolated profiles listed in `mapping/account_pools.yaml` valid and authenticated under `/home/sam/.ccs`

## Current Live Run

Fresh rerun started on `2026-03-10`:

- thread id: `2221bbf6-d743-4449-83fd-550bf1168b79`
- thread name: `rektslug-spec-016`
- running task: `4630be01-1b88-4433-b2b1-50792134ac3d`
- step: `0` (`Speckit Specify spec-016`)
- role: `lead`
- target account: `claude-samuele`
- assigned worker: `ws-claude-session-dyn-01`
- session id: `7975a13e-4ab1-4f5b-bb25-03412256fcf4`

Launcher caveat:

- `./scripts/mesh run rektslug 016` created the thread and all 20 tasks, then still emitted `409 duplicate_thread_name`
- this is launcher noise/bug, not a failed run
- trust router thread/task state over the shell exit code

## Troubleshooting

- `mesh status` fails on missing Python deps: use `uv sync --frozen`.
- `mesh status` shows only active/recent workers by default; use `mesh status --all` when you need the full historical table.
- `mesh` points to `http://localhost:8780`: run `./scripts/install-shell-helpers.sh`, `source ~/.bashrc` (or `~/.zshrc`), then retry. `mesh` now auto-loads router env from `~/.mesh/*` and `/etc/mesh-worker/*.env`.
- `wss <repo>` still tries `/home/sam/<repo>`: your shell has a stale helper. Run `./scripts/install-shell-helpers.sh` then open a new shell (or `exec $SHELL -l`).
- `wss <repo>` on WS still does self-SSH (`Permission denied (publickey)`): stale shell helper in current shell. Run `./scripts/install-shell-helpers.sh` and `exec $SHELL -l`.
- `yazi`/`lf` exits without changing dir: use `yazicd`/`lfcd` (or aliases installed by helper).
- iTerm2 pane becomes unresponsive after idle: refresh shell helpers; `wss`/`wsattach` now use more aggressive SSH keepalive defaults (`15s`, `count=12`, `ControlPersist=30m`) plus reconnect-friendly SSH options.
- multiple repos sharing context unexpectedly: check that the Claude profile is `isolated`, and do not reuse the same profile across unrelated work unless you accept shared history/state.
- worker shell commands work but systemd workers still return `401`: local `~/.mesh/router.env` and `/etc/mesh-worker/*.env` are drifted; update both or run the token sync helper, then restart session workers.
- a session task gets requeued after ~5 minutes even though tmux is still alive: router or worker is still running old code without lease-renewal fixes; redeploy router + worker runtime.
- a session opens tmux but blocks on theme/security/trust-folder/MCP prompts: this is CLI bootstrap drift under `mesh-worker`, not a router bus failure.
- `mesh ui` can now attach live tmux sessions, but it is still not the orchestration source of truth; when in doubt, trust router task state plus `journalctl` on the session worker.
- Claude Code-backed session workers now wait longer for the `❯` prompt, add a short settle before `Enter`, and kill stale tmux sessions on retry. If a task still sits on the typed prompt, inspect the tmux pane before blaming provider auth.
- Session workers now also re-check the bottom-most `❯` composer after the first send; if the prompt text is still pending, they retry `Enter` automatically instead of leaving the task stuck in the composer.
- Claude rate-limit TUI (`You're out of extra usage`, `/rate-limit-options`, reset menu) is now treated as `account_exhausted` when detected live in the pane so the router can rotate to the next isolated Claude profile.
- Router DB access is now serialized more aggressively with the existing `RLock` to reduce concurrent SQLite misuse that was surfacing live as `POST /heartbeat -> 500` and `POST /tasks/complete -> 500`.
- `scripts/mesh` no longer falls through from the `uv` path to the fallback Python path inside `run_meshctl()`. This was the real cause of the late `409 duplicate_thread_name` noise after a successful `mesh start`.
- if you ask whether the repo is still in scope for `boss/president/lead/workers` multi-panel operation: yes. `lead` is now a first-class communication role in the router policy layer, with create/dispatch/visibility permissions and runtime communication edges to both `president` and `worker`.

Live note from `2026-03-10`:

- the repo contains the composer-submit retry, live rate-limit detection, and RouterDB serialization fixes
- the active `.100` router/worker runtime was still on older code when `rektslug-spec-016` hit:
  - stuck composer before manual `Enter`
  - Claude rate-limit menu on `claude-samuele`
  - router `500` on `/tasks/complete` and intermittent `/heartbeat`
- do not assume the live rerun reflects current repo behavior until router `.100` and WS worker runtime are redeployed

## 2026-03-11 Gemini rerun proof

- Gemini auth/runtime was briefly broken again under `ccs gemini`:
  - live CLIProxy error: `500 auth_unavailable: no auth available`
  - recovery that worked live under `sam`:
    - `ccs gemini --use samuele.morzenti`
    - `ccs cliproxy restart`
  - post-restart proof:
    - `ccs gemini --print -p "Reply with exactly GEMINI_AUTH_OK"` returned `GEMINI_AUTH_OK`
- the stale-artifact review fix is now proven live, not just in tests:
  - repo reused on purpose: `/tmp/mesh-gemini-team-e2e-20260310-230556`
  - existing files before rerun:
    - `lead_plan.md`
    - `worker_review.md`
    - `president_decision.md`
  - rerun thread:
    - `mesh-gemini-team-e2e-20260310-230556-rerun-stale-artifact-proof-20260311-001545`
    - thread id: `e38fd28d-b5b9-4680-8173-f22f188bd628`
  - tasks:
    - lead: `45a31fe8-7981-4bd8-82d3-d99621c45620`
    - worker: `0ead5ccf-a8e5-495e-886f-2b7bc7ad847f`
    - president: `534f3416-12f0-4872-855e-e4188463ecbb`
- decisive live evidence:
  - a few seconds after launch, step `0` was still `running` and Gemini worker was `busy`
  - therefore the old `lead_plan.md` did **not** trigger immediate auto-exit
  - later all three files were rewritten with fresh mtimes:
    - `lead_plan.md` -> `1773188198.5144885830`
    - `worker_review.md` -> `1773188328.8796516460`
    - `president_decision.md` -> `1773188427.2637878150`
  - worker journal confirms all three tasks completed through artifact-driven auto-exit on the rewritten files
- residual live issue is now narrower and infrastructure-only:
  - router `.100` intermittently times out on:
    - `/heartbeat`
    - `/sessions/messages`
    - sometimes `mesh thread` without explicit thread name
  - these timeouts delay observability, but did **not** invalidate the Gemini rerun proof above

## 2026-03-11 launcher post-fix validation

- root cause of the late `409 duplicate_thread_name` was in `scripts/mesh`, not the router:
  - `run_meshctl()` invoked `uv run -- python -m src.meshctl ...`
  - then continued into the fallback `python3 -m src.meshctl ...`
  - the second invocation hit the already-created thread name and emitted the misleading `409`
- repo fix:
  - `run_meshctl()` now returns immediately after the first successful path (`uv` or fallback python)
- regression test:
  - `tests/test_deploy_scripts.py::TestMeshScript::test_mesh_status_does_not_fall_through_from_uv_to_python`
- live Gemini-only validation after the fix:
  - repo: `/tmp/mesh-gemini-dupfix`
  - command:
    - `MESH_PIPELINE_TEMPLATE=gemini_team_demo /media/sam/1TB/gobabygo/scripts/mesh start 'dupfix e2e'`
  - thread:
    - `mesh-gemini-dupfix-dupfix-e2e-20260311-003958`
    - thread id: `b94692c3-3610-4961-a7c5-8f50c2a5e26a`
  - output was clean:
    - `Pipeline thread created: ...`
    - `Started thread: ...`
    - no trailing `409 duplicate_thread_name`
  - final thread state:
    - `completed`
  - artifacts:
    - `/tmp/mesh-gemini-dupfix/lead_plan.md`
    - `/tmp/mesh-gemini-dupfix/worker_review.md`
    - `/tmp/mesh-gemini-dupfix/president_decision.md`

## 2026-03-11 hardening pass

- startup recovery now uses `fsm.apply_transition()` inside the recovery transaction instead of bypassing FSM guardrails
- account exhaustion rotation is no longer Claude-only; Codex and Gemini now classify provider-specific quota/rate-limit failures as `account_exhausted`
- scheduler dispatch now requires a fresh heartbeat before an `idle` worker can be leased
- text-marker auto-exit is stricter: it is opt-in without `success_file_path` and only matches standalone marker lines, not arbitrary substrings in the pane buffer
- tmux session naming uses a longer task fragment to reduce collision risk under parallel load
