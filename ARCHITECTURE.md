# ARCHITECTURE.md

## Purpose

This is the canonical architecture document for the current `gobabygo` runtime.

Scope:

- router
- scheduler
- SQLite state
- worker runtime policy
- session-first execution
- live topology and current continuation state

This file documents the control-plane.  
It does not replace the implementation/docs inside target repos like `rektslug`.

## System Role

`gobabygo` is the orchestration/control-plane repository.

It owns:

- task/thread/session state
- router HTTP API
- worker dispatch and recovery
- provider runtime policy
- deployment/bootstrap logic
- operator workflows

It does not own the feature code being implemented in downstream repos.

Example current downstream target:

- repo: `/media/sam/1TB/rektslug`
- feature: `spec-016`

## Core Principles

1. Source of truth is router DB + task/thread/session records.
2. Runtime execution is done by workers, not by iTerm2.
3. `tmux` is the terminal runtime for interactive sessions.
4. `iTerm2` is operator UX only: observe, attach, split panes, manage tabs.
5. Session-first is the default operating mode.
6. Batch is fallback or special-purpose only, not the primary path.

## Runtime Topology

Current intended topology:

```text
Mac (.112)
  - iTerm2
  - operator shell
  - optional mesh UI forwarding

WS (.111)
  - target repos (for example /media/sam/1TB/rektslug)
  - session workers
  - batch/review workers
  - tmux sessions
  - provider runtimes (Claude/Codex/Gemini, CCS)

Router (.100)
  - mesh router HTTP service
  - scheduler
  - SQLite DB
  - recovery / heartbeat / lease control
```

## Execution Model

### Batch worker

Used for non-interactive runs.

Characteristics:

- one-shot subprocess execution
- suitable for deterministic or low-context tasks
- not the preferred path for high-value interactive orchestration

### Session worker

Used for interactive CLI execution inside `tmux`.

Characteristics:

- persistent interactive CLI session
- router-backed session record
- session message bus via `/sessions/*`
- operator can attach through tmux / UI flows

This is the primary path for:

- Claude execution
- interactive implementation
- complex planning/discussion/implementation loops

## Session Bus

The bus is not a replacement for a PTY.

Responsibilities:

- route session messages through router state
- persist operator/system/CLI messages
- expose `/sessions`, `/sessions/messages`
- preserve orchestration/auditability

The raw terminal remains `tmux`.

Meaning:

- bus = coordination + persistence
- tmux = actual terminal execution surface

That is the intended split.

## Provider Runtime Policy

Runtime resolution is centralized in:

- `mapping/provider_runtime.yaml`

Current default policy:

- `claude` -> `ccs {target_account}`
- `codex` -> `ccs codex`
- `gemini` -> `ccs gemini`

Important distinction:

- for Claude, `target_account` must be a real CCS profile created with `ccs auth create <profile>`
- `ccs claude` is not the canonical isolation mechanism for repo-scoped Claude history

Current Unix-user policy:

- Claude session worker -> `sam`
- Codex session worker -> `mesh-worker`

This matters because provider auth/state lives under the Unix user running the session worker.

## Current Live Runtime Decisions

These decisions are already reflected in code/docs:

- session-first hard mode
- `MESH_SESSION_FALLBACK_TO_BATCH=0`
- `MESH_ENFORCE_SESSION_ONLY=1` where enabled in deploy/runtime policy
- lease renewal on heartbeat
- worker deregistration and recovery loops
- central provider runtime resolution
- current Claude binary path on WS session runtime should resolve to `/usr/local/bin/claude`

## Current Live Continuation State

This repo must preserve precise continuation state for the active downstream run.

Current run:

- target repo: `/media/sam/1TB/rektslug`
- feature: `spec-016`
- thread: `rektslug-spec-016-20260309-003627`
- thread_id: `8c9151d2-fea8-4293-8b43-00cd2884d605`
- active task: `d3980f6a-bfe5-4026-9141-308365ecf7e9`
- active session: `bd55bde4-9ea8-4118-9ddd-a16f04fd313b`

Interpretation:

- the work item belongs to `rektslug`
- the canonical orchestration state belongs to `gobabygo`

That is why the continuation record must exist in this repo too.

## Recent Runtime Issue

Observed live issue:

- `GET /sessions/messages` alternated between:
  - `404 session_not_found`
  - `500 {"details":"bad parameter or other API misuse"}`

Observed at the same time:

- `GET /sessions/<id>` still returned valid open sessions

Most likely cause:

- shared SQLite connection misuse on the router session-message path under threaded access

Fix committed in this repo:

- `RouterDB` serializes session CRUD and message access with `RLock`
- `session_worker` stops polling when router truly returns `session_not_found`
- `session_worker` now logs real `upterm` `OSError` failures

If the live router still shows the old behavior, the router runtime on `.100` has not been redeployed on this commit yet.

## Canonical Resume Flow

When resuming work:

1. open this repo first
2. read:
   - `README.md`
   - `ARCHITECTURE.md`
   - `CLAUDE.md`
3. verify current thread/session state from this repo
4. only then switch focus to the target repo (`rektslug`)

Minimal verification:

```bash
source ~/.mesh/router.env

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/threads/8c9151d2-fea8-4293-8b43-00cd2884d605/status" | python -m json.tool

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/sessions/bd55bde4-9ea8-4118-9ddd-a16f04fd313b" | python -m json.tool
```

## Document Map

- `README.md` -> entrypoint + live state
- `ARCHITECTURE.md` -> canonical runtime architecture
- `CLAUDE.md` -> operator playbook + live orchestration snapshot
- `QUICKSTART.md` -> commands/bootstrap/troubleshooting
- `HANDOFF.md` -> session-specific continuation log
- `kiss_mesh/README.md` -> historical KISS design notes
