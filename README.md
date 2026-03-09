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

This repo is the control-plane for a live `rektslug` run, not the target feature repo itself.

Current tracked pipeline:

- target repo: `/media/sam/1TB/rektslug`
- feature: `spec-016`
- thread: `rektslug-spec-016-20260309-003627`
- thread_id: `8c9151d2-fea8-4293-8b43-00cd2884d605`
- active step 0 task: `d3980f6a-bfe5-4026-9141-308365ecf7e9`
- active session: `bd55bde4-9ea8-4118-9ddd-a16f04fd313b`

Meaning:

- `gobabygo` owns router/worker/runtime state
- `rektslug` owns the feature implementation
- resuming `spec-016` requires both:
  - the target repo path in `rektslug`
  - the control-plane thread/task/session state stored and documented here

## Resume Checklist

Use this repo to resume work precisely:

1. verify router/thread state from here
2. verify worker/session health from here
3. only then continue the target repo flow in `rektslug`

Minimal checks:

```bash
source ~/.mesh/router.env

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/threads/8c9151d2-fea8-4293-8b43-00cd2884d605/status" | python -m json.tool

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/sessions/bd55bde4-9ea8-4118-9ddd-a16f04fd313b" | python -m json.tool
```

## Notes

- Historical architecture notes remain in [kiss_mesh/README.md](kiss_mesh/README.md).
- Canonical architecture for the current runtime is now in [ARCHITECTURE.md](/media/sam/1TB/gobabygo/ARCHITECTURE.md).
- Quick operator guidance is in [QUICKSTART.md](/media/sam/1TB/gobabygo/QUICKSTART.md).
