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
7. UI roles and runtime roles are related, but not identical.

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

Operator UI layout:

- `mesh ui <repo>` opens iTerm2 panels for:
  - `boss`
  - `president`
  - `lead`
  - `worker-claude`
  - `worker-codex`
  - `worker-gemini`
  - `verifier`

Important boundary:

- these panes are operator affordances
- orchestration truth still lives in router DB + worker/session records
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

Canonical built-in workflow templates are now session-native too:

- `confidence_gate_team`
- `gsd`
- `speckit`
- `speckit_codex`

Meaning:

- the template file itself now encodes interactive team orchestration
- it no longer relies on live `session-only` policy to silently override `batch` steps
- Claude is used primarily for `lead` creative work
- Codex is used primarily for `president` adjudication/review and worker-side verification
- Gemini is used as an independent worker challenger/validator where available

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
- `ccs claude` is not the canonical isolation mechanism for account-scoped Claude history
- default Claude account selection is policy-driven from `mapping/account_pools.yaml`
- Claude rate-limit recovery is policy-driven too: `429`, `You've hit your limit`, `You're out of extra usage`, and `rate limit error` are treated as `account_exhausted` and retried on the next isolated Claude profile

Current Unix-user policy:

- Claude session worker -> `sam`
- Codex session worker -> `mesh-worker`
- Gemini session worker -> `sam`

This matters because provider auth/state lives under the Unix user running the session worker.

## Role Model

Current runtime-enforced roles:

- `boss`
- `president`
- `lead`
- `worker`

Those are enforced in the communication policy layer.

Current operator/UI roles:

- `boss`
- `president`
- `lead`
- `worker-*`
- `verifier`

Current runtime communication policy:

- `boss` communicates with `president`
- `president` communicates with `boss`, `lead`, and `worker`
- `lead` communicates with `president` and `worker`
- `worker` communicates with `lead` and `president`

Operational meaning:

- `lead` is now a first-class runtime communication role
- `lead` can create tasks, dispatch tasks, and view all tasks
- direct `president` ↔ `worker` communication remains allowed for compatibility during the current transition
- `verifier` still exists operationally as review path / worker responsibility, not as a distinct communication enum

## Current Live Runtime Decisions

These decisions are already reflected in code/docs:

- session-first hard mode
- `MESH_SESSION_FALLBACK_TO_BATCH=0`
- `MESH_ENFORCE_SESSION_ONLY=1` where enabled in deploy/runtime policy
- lease renewal on heartbeat
- worker deregistration and recovery loops
- central provider runtime resolution
- centralized provider account pool selection
- current Claude binary path on WS session runtime should resolve to `/usr/local/bin/claude`
- router bind is externally reachable on `.100:8780`

## Current Live Continuation State

This repo must preserve precise continuation state for the downstream run and for the control-plane itself.

Current downstream run:

- target repo: `/media/sam/1TB/rektslug`
- feature: `spec-016`
- thread: `rektslug-spec-016-20260309-003627`
- thread_id: `8c9151d2-fea8-4293-8b43-00cd2884d605`
- first task: `d3980f6a-bfe5-4026-9141-308365ecf7e9`
- first session: `bd55bde4-9ea8-4118-9ddd-a16f04fd313b`
- current state: `failed`

Current control-plane recovery state:

- router runtime has been redeployed from a clean release path:
  - `/opt/mesh-router/releases/86c3f2b`
  - `/opt/mesh-router/current` -> current release symlink
- the historical dirty checkout under `/home/sam/work/gobabygo` was intentionally not used for deploy
- WS local operator env `~/.mesh/router.env` was realigned to the live router token
- WS worker service envs `/etc/mesh-worker/*.env` were realigned to the same token and router URL
- current active session workers on `.111` are healthy and heartbeating:
  - `ws-claude-session-dyn-01`
  - `ws-codex-session-dyn-01`

Interpretation:

- the work item belongs to `rektslug`
- the canonical orchestration state belongs to `gobabygo`
- the failed run should be treated as a historical attempt; the next implementation pass should start as a fresh run on the recovered control-plane

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
4. verify worker heartbeat freshness and account-pool policy
5. only then switch focus to the target repo (`rektslug`)

Minimal verification:

```bash
source ~/.mesh/router.env

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/threads/8c9151d2-fea8-4293-8b43-00cd2884d605/status" | python -m json.tool

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/sessions/bd55bde4-9ea8-4118-9ddd-a16f04fd313b" | python -m json.tool
```

For a recovered stack, also verify:

```bash
source ~/.mesh/router.env
./scripts/mesh status
```

## Document Map

- `README.md` -> entrypoint + live state
- `ARCHITECTURE.md` -> canonical runtime architecture
- `CLAUDE.md` -> operator playbook + live orchestration snapshot
- `QUICKSTART.md` -> commands/bootstrap/troubleshooting
- `HANDOFF.md` -> session-specific continuation log
- `kiss_mesh/README.md` -> historical KISS design notes
