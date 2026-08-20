<p align="center">
  <img src="logo.png" alt="AI Mesh Router Logo" width="800">
</p>

# AI Mesh Router

![CI](https://github.com/gptcompany/gobabygo/actions/workflows/ci.yml/badge.svg)
![Sandbox Validation](https://github.com/gptcompany/gobabygo/actions/workflows/sandbox-validate.yml/badge.svg)
![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/gptcompany/gobabygo/master/.github/badges/coverage.json)
![Python](https://img.shields.io/badge/python-3.11%2B-blue?style=flat-square&logo=python)
![Last Commit](https://img.shields.io/github/last-commit/gptcompany/gobabygo?style=flat-square)
![Issues](https://img.shields.io/github/issues/gptcompany/gobabygo?style=flat-square)
![Lines of Code](https://sloc.xyz/github/gptcompany/gobabygo)

**Distributed multi-agent task orchestration router with SQLite persistence**

This repository contains the session-first mesh router, worker coordination logic, deployment assets, and test suite for the AI mesh network runtime.

## Canonical Docs

Read in this order:

1. [README.md](README.md) - repository entrypoint
2. [MESH_LIVE.md](MESH_LIVE.md) - concise operator runbook for existing tmux sessions
3. [ARCHITECTURE.md](ARCHITECTURE.md) - canonical architecture and runtime topology
4. [CLAUDE.md](CLAUDE.md) - router-managed operator/BOSS playbook
5. [QUICKSTART.md](QUICKSTART.md) - extended commands, env, bootstrap, troubleshooting
6. [HANDOFF.md](HANDOFF.md) - session-specific continuation notes

## What is here

- `src/router/`: router runtime, scheduling, worker lifecycle, persistence, metrics, and bridge adapters
- `src/meshctl.py`: lightweight HTTP client CLI for inspecting and operating the mesh
- `deploy/`: systemd units, environment templates, deployment scripts, and monitoring configs
- `tests/`: unit, integration, and in-process smoke coverage

## Quick Start

```bash
python -m pip install '.[dev]'
pytest -q
```

## Historical Runtime Snapshot (March 2026)

This section is retained as recovery evidence. It is not authoritative for the
current workstation state. Use `wboard`, `mesh status`, and `mesh sessions` for
current live and router-managed state.

This repo is the control-plane for `rektslug`, not the target feature repo itself.

Current tracked downstream run:

- target repo: `/media/sam/1TB/rektslug`
- feature: `spec-016`
- thread: `rektslug-spec-016-20260309-003627`
- thread_id: `8c9151d2-fea8-4293-8b43-00cd2884d605`
- first step task: `d3980f6a-bfe5-4026-9141-308365ecf7e9`
- first step session: `bd55bde4-9ea8-4118-9ddd-a16f04fd313b`
- current thread status: `failed`

Current control-plane state:

- router `.100` has been recovered on a clean runtime release under `/opt/mesh-router/releases/86c3f2b`
- router bind is now external (`0.0.0.0:8780`), not localhost-only
- local operator token and WS worker service envs were realigned to the live router token
- active session workers on `.111` are healthy again:
  - `ws-claude-session-dyn-01`
  - `ws-codex-session-dyn-01`
- stale worker record `ws-claude-session-rektaslug-01` was deregistered from the router

Meaning:

- `gobabygo` owns router/worker/runtime state
- `rektslug` owns the feature implementation
- the old `spec-016` run is historical evidence, not the run to continue in place
- the next correct move is a clean rerun of `spec-016` using the current centralized Claude account pool

## Historical Recovery Checklist

These checks document the March 2026 recovery path; do not use the recorded IDs
as a current runbook:

1. verify router/thread state from here
2. verify worker/session health from here
3. confirm the current account pool policy
4. only then continue the target repo flow in `rektslug`

Minimal checks:

```bash
source ~/.mesh/router.env

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/threads/8c9151d2-fea8-4293-8b43-00cd2884d605/status" | python -m json.tool

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/sessions/bd55bde4-9ea8-4118-9ddd-a16f04fd313b" | python -m json.tool
```

Current expectation:

- router health is good
- session workers are `idle` with fresh heartbeats
- `rektslug-spec-016-20260309-003627` stays `failed`
- a new run should resolve Claude via [mapping/account_pools.yaml](/media/sam/1TB/gobabygo/mapping/account_pools.yaml), starting with `claude-samuele`

## Notes

### Spec Kit runtime

Gobabygo pins the official Spec Kit CLI and discovers project capabilities from
the installed Claude, Codex, and AGY skills. Update discovery is automatic and
read-only; CLI installation and project changes always require explicit
`--apply`.

```bash
mesh speckit status /data/sata/1TB/rektslug
mesh speckit capabilities /data/sata/1TB/rektslug --json
mesh speckit update-check --json
mesh speckit install 0.16.5                 # plan only
mesh speckit project migrate /data/sata/1TB/rektslug \
  --allow-multi-install-force                         # legacy plan only
mesh speckit project upgrade /data/sata/1TB/rektslug  # plan only
```

`status` reports pre-manifest projects as `legacy`, not `missing`. Migrate one
clean repository at a time: inspect the plan first, then add
`--accept-generated-updates --apply` only after reviewing generated template
replacements. Migration installs Claude, Codex, and AGY together, preserves
existing `specs/` and `.specify/memory/constitution.md`, and blocks collisions
with custom skills. A historical `memory/constitution.md` is copied byte-for-byte
only when the current constitution is absent; if both exist, the historical path
is reported as unmigrated and neither file is overwritten. Legacy
`.claude/commands` are retained and reported for manual review; migration never
deletes them. Later releases use `project upgrade`.

`mesh speckit context` creates the bounded, provider-neutral phase/artifact
envelope used for worker delegation. Intra-repo specs remain in that repo;
multi-repo specs, decisions, tasks, and handoffs live in the exact Git root
`/data/sata/1TB/coordination`. Mesh Live still owns tmux delivery and review;
Spec Kit does not launch nested CLI workers. See [MESH_LIVE.md](MESH_LIVE.md).

### Optional Codex TDD gate

Gobabygo pins Probity but does not enable TDD enforcement globally. The engine
and one Codex dispatcher are user-level; a repository opts in only by committing
exactly one `probity.config.ts|mts|js|mjs` at its Git root.

```bash
mesh probity install --json        # plan only
mesh probity install --apply       # pinned install + Codex hook merge
mesh probity smoke --json          # temporary repo; deterministic allow/block
mesh probity status /data/sata/1TB/rektslug --json
```

Add a reviewed project-specific config only where strict TDD is useful. Scope
`enforceTdd()` to the repository's real code and test paths; do not copy broad
globs across unrelated repositories. After installation, start Codex once and
use `/hooks` to review and trust the exact user hook; Codex skips untrusted or
changed hooks. Then restart any older Codex worker that should use it.
Repositories without a config remain unaffected after this one-time user hook
trust.

Probity `1.10.0` exposes Codex but not Antigravity through its CLI. Claude keeps
the existing Claude-specific TDD Guard, and AGY follows the provider-neutral
`TDD_MODE` plus RED/GREEN evidence contract. Do not stack Probity and TDD Guard
on the same Claude session. Probity configs execute as code and matching TDD
writes may invoke a model validator, so opt in only after config review and a
smoke; this is a guardrail, not an OS sandbox or a replacement for tests/CI.

- Default provider account selection is centralized in [mapping/account_pools.yaml](/media/sam/1TB/gobabygo/mapping/account_pools.yaml).
- Default operator multi-panel bootstrap is centralized in [mapping/operator_ui.yaml](/media/sam/1TB/gobabygo/mapping/operator_ui.yaml).
- Canonical built-in `gsd` and `speckit` templates are session-only team templates. Active provider lanes are Claude, Codex, and Antigravity; Gemini is retained only for historical row deserialization and is rejected for new work.
- For Claude, use isolated CCS account profiles such as `claude-samuele` and launch them from the target repo directory with `ccs <profile>`.
- Claude account autoswitch is router-driven, not CCS-provider-driven: worker failures tagged as `account_exhausted` rotate the next task attempt to the next isolated profile from `mapping/account_pools.yaml`.
- `ccs codex` keeps the existing provider bridge. Antigravity uses the native `agy` CLI as user `sam`, with `--new-project` to pin the selected repository; it does not use CCS or Claude hooks.
- For existing manual sessions, use `mesh live`; tmux is authoritative for live pane/process state. For router-managed tasks and sessions, the router DB remains authoritative. iTerm2 is only an optional view.
- `mcoordinator <repo> --worker <session>` bootstraps the default adaptive tmux coordinator; `mcoordinator --all` handles multi-repo scope without requiring iTerm2 or the router. Adaptive mode uses direct coordination for bounded operational work and projects the canonical `speckit` template for feature/architecture work with independent challenge and adjudication; `--workflow direct|speckit|adaptive` is explicit override. Repo scope binds one repository, while `--all` keeps the specification and decisions at coordinator level and late-binds repo plus feature/task for each concrete delegation.
- Unpinned Mesh Live coordination defaults to Antigravity as the sole writer and Codex as the primary reviewer in a different session. This is an overridable preference, not an exclusive capability map. Reviews are read-only, tied to an exact commit range or recorded diff snapshot, and require severity-ordered `file:line` findings plus an explicit verdict.
- `mesh ui <repo>` is part of the router-managed operator flow and opens role panels. It boots each pane through central role policy and may attach to a matching router-backed tmux session.
- `mesh ui` remains an optional legacy iTerm2 layout. Any `worker-gemini` role in that path is deprecated and cannot create new router work; use `mesh live ensure-antigravity` for the active third-provider lane.
- `mesh` with no arguments now opens a small interactive launcher for the current repo root (`attach`, `sessions`, `ui`, `start`, plus `attach --all`).
- For a simpler one-session workflow, `mesh sessions` and `mesh attach` are router-backed operator commands: they default to live sessions for the current repo, support `--all` for cross-repo selection, and only use tmux at the final attach step.
- The Matrix bridge now supports explicit room commands (`!mesh approve`, `!mesh reject`, `!mesh send`, `!mesh enter`, `!mesh interrupt`) resolved against the router API/DB, scoped to the repo room when topology maps one.
- If `mesh ui` cannot attach a live session, the pane falls back to the role's provider-backed CLI bootstrap from `mapping/operator_ui.yaml` and `mapping/provider_runtime.yaml`.
- `mesh status` now hides historical stale/offline worker rows by default; use `--all` when you explicitly want the full audit-heavy worker table.
- Runtime roles are now `boss`, `president`, `lead`, and `worker`. `lead` is first-class in the router policy layer and acts as a coordinator between `president` and workers while direct `president` ↔ `worker` communication remains allowed for compatibility.
- Worker execution paths are now bounded by `MESH_ALLOWED_WORK_DIRS`. Session and batch workers reject task payloads that resolve outside those roots.
- Worker deregistration is now conservative: active tasks are failed, not requeued, until there is a real remote-kill handshake for live tmux sessions. This avoids dual execution on the same repo.
- Account exhaustion classification applies to active `claude`, `codex`, and `antigravity` workers when their failure output matches configured quota/rate-limit signatures.
- Scheduler dispatch now requires a fresh worker heartbeat before leasing work, reducing 5-minute blackholes on recently-dead workers.
- Historical architecture notes remain in [kiss_mesh/README.md](kiss_mesh/README.md).
- Canonical architecture is in [ARCHITECTURE.md](ARCHITECTURE.md).
- Direct tmux operator guidance is in [MESH_LIVE.md](MESH_LIVE.md); extended setup and troubleshooting are in [QUICKSTART.md](QUICKSTART.md).
