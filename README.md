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

1. [README.md](/media/sam/1TB/gobabygo/README.md) — entrypoint and current live state
2. [ARCHITECTURE.md](/media/sam/1TB/gobabygo/ARCHITECTURE.md) — canonical architecture and runtime topology
3. [CLAUDE.md](/media/sam/1TB/gobabygo/CLAUDE.md) — operator/BOSS playbook and current orchestration snapshot
4. [QUICKSTART.md](/media/sam/1TB/gobabygo/QUICKSTART.md) — commands, env, bootstrap, troubleshooting
5. [HANDOFF.md](/media/sam/1TB/gobabygo/HANDOFF.md) — session-specific continuation notes

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

## Current Live State

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

## Resume Checklist

Use this repo to resume work precisely:

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

- Default provider account selection is centralized in [mapping/account_pools.yaml](/media/sam/1TB/gobabygo/mapping/account_pools.yaml).
- Default operator multi-panel bootstrap is centralized in [mapping/operator_ui.yaml](/media/sam/1TB/gobabygo/mapping/operator_ui.yaml).
- For Claude, use isolated CCS account profiles such as `claude-samuele` and launch them from the target repo directory with `ccs <profile>`.
- Claude account autoswitch is router-driven, not CCS-provider-driven: worker failures tagged as `account_exhausted` rotate the next task attempt to the next isolated profile from `mapping/account_pools.yaml`.
- `mesh ui <repo>` is part of the intended operator flow and opens panels for `boss`, `president`, `lead`, `worker-claude`, `worker-codex`, `worker-gemini`, and `verifier`. It now boots each pane through a central role policy instead of dropping every pane into the same bare shell. The runtime source of truth is still the router DB, not iTerm2.
- `mesh status` now hides historical stale/offline worker rows by default; use `--all` when you explicitly want the full audit-heavy worker table.
- Runtime roles are now `boss`, `president`, `lead`, and `worker`. `lead` is first-class in the router policy layer and acts as a coordinator between `president` and workers while direct `president` ↔ `worker` communication remains allowed for compatibility.
- Historical architecture notes remain in [kiss_mesh/README.md](kiss_mesh/README.md).
- Canonical architecture for the current runtime is now in [ARCHITECTURE.md](/media/sam/1TB/gobabygo/ARCHITECTURE.md).
- Quick operator guidance is in [QUICKSTART.md](/media/sam/1TB/gobabygo/QUICKSTART.md).
