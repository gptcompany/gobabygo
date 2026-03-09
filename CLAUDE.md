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
- worker deregistration and periodic recovery are live
- lease renewal on heartbeat is implemented and tested, so healthy long-lived sessions are no longer requeued after the 5-minute lease window
- `claude` runtime resolution is policy-driven (`ccs {target_account}` for real CCS profiles)
- session worker runtime on WS now uses current Claude Code (`/usr/local/bin/claude`, not the stale `/usr/bin/claude`)
- router `.100` is back on a clean release runtime, not the dirty checkout under `/home/sam/work/gobabygo`
- WS local shell auth and WS worker service auth have been realigned to the same live router token
- current healthy session workers are:
  - `ws-claude-session-dyn-01`
  - `ws-codex-session-dyn-01`

Not yet production-clean:

- `/sessions/messages` fix is committed/tested locally; if the live router still returns `500 bad parameter or other API misuse`, `.100` is still running the old runtime
- `upterm` launch logging is fixed in code; if the worker still logs `upterm binary not found ...` for an existing binary, the worker runtime has not been restarted on the new code yet
- brand-new Claude CCS profiles still need one first login/bootstrap in their own instance
- session worker Unix user must match where that provider/runtime state actually lives
- several offline historical worker records still remain in the router DB for audit history; they are not active incidents by themselves

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
- otherwise tasks can dispatch correctly but still fail later on provider auth/bootstrap
- Claude profile rotation on limit is handled by the router, not by `ccs claude`: keep the isolated profiles listed in `mapping/account_pools.yaml` valid and authenticated under `/home/sam/.ccs`

## Troubleshooting

- `mesh status` fails on missing Python deps: use `uv sync --frozen`.
- `mesh status` shows only active/recent workers by default; use `mesh status --all` when you need the full historical table.
- `mesh` points to `http://localhost:8780`: run `./scripts/install-shell-helpers.sh`, `source ~/.bashrc` (or `~/.zshrc`), then retry. `mesh` now auto-loads router env from `~/.mesh/*` and `/etc/mesh-worker/*.env`.
- `wss <repo>` still tries `/home/sam/<repo>`: your shell has a stale helper. Run `./scripts/install-shell-helpers.sh` then open a new shell (or `exec $SHELL -l`).
- `wss <repo>` on WS still does self-SSH (`Permission denied (publickey)`): stale shell helper in current shell. Run `./scripts/install-shell-helpers.sh` and `exec $SHELL -l`.
- `yazi`/`lf` exits without changing dir: use `yazicd`/`lfcd` (or aliases installed by helper).
- iTerm2 pane becomes unresponsive after idle: refresh shell helpers; `wss`/`wsattach` now use SSH keepalive + control persist by default.
- multiple repos sharing context unexpectedly: check that the Claude profile is `isolated`, and do not reuse the same profile across unrelated work unless you accept shared history/state.
- worker shell commands work but systemd workers still return `401`: local `~/.mesh/router.env` and `/etc/mesh-worker/*.env` are drifted; update both or run the token sync helper, then restart session workers.
- a session task gets requeued after ~5 minutes even though tmux is still alive: router or worker is still running old code without lease-renewal fixes; redeploy router + worker runtime.
- a session opens tmux but blocks on theme/security/trust-folder/MCP prompts: this is CLI bootstrap drift under `mesh-worker`, not a router bus failure.
- `mesh ui` is operator UX only; when in doubt, trust router task state plus `journalctl` on the session worker.
- if you ask whether the repo is still in scope for `boss/president/lead/workers` multi-panel operation: yes. `lead` is now a first-class communication role in the router policy layer, with create/dispatch/visibility permissions and runtime communication edges to both `president` and `worker`.
