# CLAUDE.md

## Operator Mode (BOSS)

Use GoBabyGo as orchestration control-plane, not manual copy/paste between CLIs.

### Core principles

- Source of truth: router DB + task/thread state.
- Runtime execution: session workers in `tmux`.
- iTerm2: operator UX only (attach/split/observe), not orchestration state.
- Default policy: session-first (`MESH_SESSION_FALLBACK_TO_BATCH=0`).

## Bootstrap (one command)

After deploy/config drift, run once from BOSS host:

```bash
mesh bootstrap
```

This automatically:
- updates WS session worker envs to dynamic profile routing (`ccs {target_account}`)
- enables `MESH_ALLOWED_ACCOUNTS=*`
- restarts session workers

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

## Required Helpers

Install once on each host (Mac + WS):

```bash
./scripts/install-shell-helpers.sh
source ~/.zshrc   # or ~/.bashrc
```

Provided commands:

- `mesh` -> global wrapper for `scripts/mesh`
- `wss` / `wss <repo>` -> SSH WS shortcut
- `yazi`/`lf` -> mapped to `yazicd`/`lfcd` (keep selected directory)

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

For repo-scoped context/history isolation, create account profiles per repo:

```bash
ccs auth create claude-<repo> --context-group <repo>
ccs auth create codex-<repo> --context-group <repo>
```

Worker dynamic routing should use:

- `MESH_CLI_COMMAND=ccs {target_account}`
- `MESH_ALLOWED_ACCOUNTS=*` (or explicit allowlist)

So `target_account` from pipeline steps can map directly to repo profiles.

## Troubleshooting

- `mesh status` fails on missing Python deps: use `uv sync --frozen`.
- `yazi`/`lf` exits without changing dir: use `yazicd`/`lfcd` (or aliases installed by helper).
- multiple repos sharing context unexpectedly: use dedicated CCS profiles (`claude-<repo>`, `codex-<repo>`).
