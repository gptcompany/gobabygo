# Spec: Mesh UI Role Sessions

## Summary

`mesh ui` must stop opening standalone CCS CLIs that are unaware of each other.
Each pane must represent a real mesh role session backed by the router session bus,
so role-to-role communication, operator intervention, and session identity all work
through the existing `mesh` runtime instead of ad-hoc terminal state.

## Problem

Current `mesh ui` behavior is only a visual launcher:

- panes can open a provider CLI (`ccs gemini`, `ccs codex`, `ccs <profile>`)
- panes can attach to an already-running mesh session if one exists
- panes do **not** create mesh-backed role sessions when none exist
- panes therefore do not share `thread_id`, `session_id`, or role-aware messaging state

As a result:

- a pane labeled `boss` or `president` is just a raw CLI process
- asking one pane to contact another role fails because there is no mesh peer identity
- iTerm2/tmux provide display and transport, but not message routing
- the existing router session bus is bypassed for fresh panes

## Goal

Make `mesh ui` the canonical operator cockpit for mesh-backed role sessions.

When an operator opens `mesh ui <repo>`:

1. each pane resolves to a target role (`boss`, `president`, `lead`, `worker-*`, `verifier`)
2. if a compatible live mesh session already exists for that role and repo, the pane attaches to it
3. otherwise the pane spawns a new mesh-backed session for that role
4. all inter-role messaging flows through the router session bus
5. the CLI inside the pane remains CCS/Claude-Code-native, but is now wrapped by a real mesh session lifecycle

## Non-Goals

- replacing CCS as the CLI frontend
- using iTerm2 itself as a message bus
- inventing a second messaging path outside router `/sessions/*`
- full autonomous multi-agent delegation in this first step
- retrofitting historical stale standalone CLI panes into mesh sessions

## Current Constraints

- router session bus already exists:
  - `POST /sessions/send`
  - `POST /sessions/send-key`
  - `POST /sessions/signal`
  - `GET /sessions/messages`
- session workers already poll and deliver inbound messages into tmux-backed CLIs
- `mesh ui` currently knows how to attach to live sessions, but not how to spawn role sessions on demand
- provider launch policy already exists in `mapping/provider_runtime.yaml`
- UI role policy already exists in `mapping/operator_ui.yaml`

## Target UX

### Operator Flow

1. operator connects to WS (`wss`)
2. operator enters target repo (`yazicd`)
3. operator runs `mesh ui`
4. iTerm2 opens the standard role layout
5. each pane title visibly shows the role name
6. each pane either:
   - attaches to an existing live mesh session for that role, or
   - starts a new mesh-backed role session and enters the CLI
7. a role can send messages to other roles using mesh session commands, because every pane is now backed by a real session

### Pane Identity

Every pane must visibly expose:

- role name
- repo name
- whether it is `attached` or `spawned`
- provider/runtime identity (`gemini`, `codex`, `claude`)
- session id short form

Minimum visible requirement for v1:

- pane/tab title includes role name
- startup banner prints role, provider, repo, and session id short form

## Resolved Design Decisions

### D1. Spawn primitive = task-backed session

`mesh ui` must not create router `session` rows directly.

The canonical spawn primitive is:

1. create a dedicated session task for the role
2. let the scheduler/session worker create the session normally
3. attach the pane to the resulting session

This keeps session lifecycle aligned with the existing router model instead of
creating a second path for session state.

### D2. Role sessions are grouped by `ui_group_id`

Spawned role tasks and resulting sessions must carry:

- `ui_role_session=true`
- `ui_role=<role>`
- `ui_group_id=<stable id>`
- `repo=<repo path>`

`ui_group_id` is the grouping primitive for one operator cockpit. It is lighter
than overloading pipeline `thread_id` and avoids conflating `mesh ui` with
pipeline execution.

Lifecycle rules:

1. `mesh ui` generates `ui_group_id` as `{repo_name}-ui-{timestamp}` when no live group exists.
2. The operator host stores the last active mapping at `~/.mesh/ui_groups/{repo_name}.json`.
3. On relaunch, `mesh ui` reuses that `ui_group_id` only if the router still has at least one open session in that group.
4. If no open sessions remain, `mesh ui` creates a fresh `ui_group_id`.
5. Group membership and liveness are router-authoritative; local state is only a cache.

### D3. Role types are split between operator and agent panes

`mesh ui` must distinguish between operator roles and agent roles.

Operator roles:

- `boss`
- control shell, not provider-backed
- bus-aware and able to address peer sessions
- not created as a session-worker task by default
- receives `MESH_UI_GROUP_ID` as an exported environment variable at pane bootstrap

Agent roles:

- `president`
- `lead`
- `worker-*`
- `verifier`
- task-backed
- session-backed
- bus-addressable

### D4. API surface = extend `POST /tasks`

`mesh ui` must reuse the normal task creation path.

There is no dedicated `POST /ui/role-sessions` endpoint in v1.

UI role session tasks are normal session tasks with extra metadata that the
router, scheduler, and clients understand.

Task field contract for v1:

- top-level `TaskCreateRequest` fields must include:
  - `repo`
  - `role`
- UI role metadata lives in `payload`:
  - `ui_role_session`
  - `ui_role`
  - `ui_group_id`

This keeps attach resolution aligned with the existing task model while avoiding
new task columns for purely UI-scoped metadata.

### D5. In-pane role messaging uses mesh helper commands

The first concrete UX for peer communication is shell/helper driven, not slash-command driven.

Examples:

- `mesh-send president "review this plan"`
- `mesh-send verifier "run checks on latest diff"`
- `mesh-enter president`
- `mesh-interrupt worker-gemini`

These helpers resolve the peer by `ui_group_id + role` and then call the
existing router session bus.

Peer resolution is strict:

- if exactly one live peer matches, use it
- if zero match, fail clearly
- if multiple match, fail clearly and require disambiguation

### D6. Default layout remains six panes

Default `mesh ui` keeps the current six-pane layout:

- `boss`
- `president`
- `lead`
- `worker-codex`
- `worker-gemini`
- `verifier`

`worker-claude` remains explicit/on-demand.

### D7. `mesh_iterm_ui.py` is a thin client, not a second orchestrator

`mesh_iterm_ui.py` may:

- create UI role tasks via `POST /tasks`
- poll router state for attachable sessions
- render pane status and errors

It must not become the source of truth for:

- group membership
- session reconciliation
- task/session lifecycle rules
- message routing semantics

All authoritative state and reconciliation logic belongs in the router/runtime.

Discovery loop constraint for v1:

- poll only for session materialization
- no local task recreation
- no local retry/backoff orchestration
- no local reconciliation beyond timeout + explicit error

## Functional Requirements

### F1. Mesh-backed pane lifecycle

For each configured role, `mesh ui` must resolve one of two modes:

- `attach`: use an existing open session for the same repo and compatible role/provider
- `spawn`: create a new mesh session for that role and repo, then attach to it

Standalone `ccs ...` boot without a mesh session is not valid default behavior anymore.

### F2. Role session identity

A spawned pane session must have explicit mesh identity:

- `repo`
- `role`
- `target_cli`
- `ui_group_id`
- `session_id`

The router must remain the source of truth.

Operator panes must have explicit mesh identity too:

- `repo`
- `role`
- `ui_group_id`
- current peer resolution scope
- `MESH_UI_GROUP_ID` exported in the pane environment

### F3. Message-bus integration

Role-to-role communication must use the existing mesh session bus.

At minimum, the implementation must support:

- sending plain text from one role session to another
- operator `send`, `send-key`, `signal` to any pane-backed session
- receiving those messages inside the target CLI through the existing session worker delivery loop

### F3a. Inter-role messaging contract

Each pane-backed role session must be able to address peer roles within the same
`ui_group_id`.

Minimum v1 contract:

- resolve `target_role + ui_group_id -> session_id`
- send plain-text messages to the resolved peer session through `/sessions/send`
- support operator-assisted control actions through `/sessions/send-key` and `/sessions/signal`
- persist all inter-role messages in router session history

The bus remains session-addressed internally; role-addressing is a helper layer on top.

### F3b. Stop/completion summary contract

When a pane-backed role session reaches a terminal state, the runtime must support
publishing a structured completion summary.

Minimum v1 behavior:

- the session can emit a final summary message to one or more peer roles
- the summary can also be stored in task result metadata
- the operator must be able to inspect the summary from router/session state
- the session worker emits the summary automatically when it completes or fails the underlying task in v1

This is the mesh equivalent of an Agent Teams-style stop hook: not a terminal-only
artifact, but a routed event/message tied to the real session lifecycle

Completion summary payload for v1:

```json
{
  "type": "completion_summary",
  "role": "lead",
  "ui_group_id": "snake-game-ui-20260313T160000Z",
  "status": "completed",
  "summary_text": "Implemented feature and updated tests.",
  "artifacts": ["spec.md", "plan.md", "tests/test_mesh_ui_script.py"]
}
```

Storage rules:

- persist as a `session_message` with structured metadata
- also permit storing the same payload in task result metadata
- make it queryable from router-backed inspection flows

### F4. Spawn semantics

`mesh ui` needs an internal spawn path for role panes.

That spawn path must:

- choose provider/account from `mapping/operator_ui.yaml` + `mapping/provider_runtime.yaml`
- create a dedicated session task bound to the repo, role, and `ui_group_id`
- create all agent-role spawns in parallel
- wait for the scheduler/session worker to materialize the session
- bootstrap the correct CCS CLI inside tmux/upterm
- return enough metadata for immediate attach from the pane

Spawn defaults for v1:

- per-role spawn timeout: `60s`
- pane must show explicit progress while waiting
- failed panes must render a retry hint, e.g. `mesh ui respawn <role>`

Scheduler/runtime rule for v1:

- UI role tasks must be explicitly distinguishable from pipeline session tasks
- workers that do not opt into UI role tasks must not lease them
- the minimum acceptable discriminator is `payload.ui_role_session=true`

### F5. Attach precedence

If a matching live role session already exists, `mesh ui` must attach instead of spawning a duplicate.

Matching rules must prefer:

1. exact `ui_group_id`
2. exact repo path
3. exact role
4. compatible provider
5. newest active session

If no live session matches the active `ui_group_id`, `mesh ui` may fall back to
repo-scoped discovery only when it is rehydrating a cockpit and can prove that
the local cache is stale.

### F6. Operator boss behavior

The `boss` pane is an operator control shell.

It must:

- show role identity and `ui_group_id`
- be able to address agent-role sessions through helper commands
- not spawn a provider-backed session by default
- remain in the target repo with mesh helpers loaded

### F7. Titles and labels

Pane labels must make roles visually distinct.

Minimum required:

- pane title contains role name and repo
- shell banner shows role + provider + session short id
- boss banner must explicitly say `operator`
- agent panes must explicitly say `attached` or `spawned`

### F8. Failure handling

If a role pane cannot attach or spawn, the pane must show an explicit mesh error state.

Valid failure states:

- router unavailable
- worker unavailable for target provider
- session spawn timeout
- attach target missing/stale
- repo path invalid/not allowed
- ambiguous peer resolution

A raw detached shell without mesh identity is not an acceptable silent fallback.

Failure recovery requirements:

- individual failed panes must be retryable
- the operator must be able to respawn a missing role without recreating the whole cockpit
- group-level cleanup must exist for explicit operator teardown

### F9. UI group cleanup

The runtime must support explicit cleanup for a cockpit group.

Minimum v1:

- `mesh ui close` or equivalent closes all sessions for the active `ui_group_id`
- if the operator closes iTerm2 without cleanup, stale groups remain queryable and recoverable
- group-scoped discovery in v1 uses `/sessions?state=open` plus Python-side filtering on `metadata.ui_group_id`

## Acceptance Criteria

### AC1. Spawned panes are real mesh sessions

Given no open sessions for repo `X`, when the operator runs `mesh ui X`, then each opened role pane becomes a real mesh session with a visible `session_id` and provider identity.

### AC2. Existing sessions are reused

Given an open `lead` session already exists for repo `X`, when the operator runs `mesh ui X`, then the `lead` pane attaches to that exact session instead of creating another one.

### AC3. Inter-role messaging works

Given `boss` and `president` panes are open for the same repo, when the operator sends a session-bus message from `boss` to `president`, then the message is persisted by the router and delivered to the `president` CLI.

### AC4. Operator controls still work

Given a pane-backed session is waiting for operator input, when the operator uses `mesh send`, `mesh enter`, `mesh interrupt`, or Matrix room commands, then the target pane session receives the action through the router bus.

### AC5. No silent raw-shell fallback

Given a pane cannot become a mesh-backed session, when `mesh ui` opens that pane, then the pane shows a hard failure banner instead of an unlabeled detached shell.

### AC6. Stop summary is routable

Given a `lead` pane session completes work for the current `ui_group_id`, when it emits a completion summary, then the summary is available as router-backed state and can be delivered to `president` or `boss` through the same mesh addressing model.

### AC7. Boss remains an operator pane

Given the operator opens `mesh ui X`, when the `boss` pane starts, then it remains a mesh-aware control shell and does not boot a provider CLI by default.

### AC8. `ui_group_id` is reused safely

Given the operator relaunches `mesh ui` for repo `X`, when at least one live session still exists for the cached group, then `mesh ui` reuses that `ui_group_id`; otherwise it creates a new one.

### AC9. Spawn is parallel and bounded

Given no live sessions exist for repo `X`, when the operator runs `mesh ui X`, then all agent-role spawns begin in parallel and each pane either attaches or fails within `60s`.

### AC10. Multi-operator isolation holds

Given two operators open `mesh ui` for the same repo independently, when each cockpit spawns new agent panes, then each cockpit uses a distinct `ui_group_id` and does not attach to the other cockpit's sessions by default.

### AC11. Task create path carries required identity

Given `mesh ui` spawns an agent-role pane, when it creates the underlying task, then `repo` and `role` are present as top-level task fields and `ui_role_session`, `ui_role`, and `ui_group_id` are present in task payload metadata.

### AC12. Scheduler does not leak UI tasks to pipeline workers

Given a UI role task exists and a non-UI session worker polls for work, when the scheduler evaluates eligibility, then that worker is not allowed to lease the UI role task.

### AC13. Boss knows the active group

Given the `boss` pane starts for repo `X`, when mesh helpers are loaded, then `MESH_UI_GROUP_ID` is available in the pane environment and peer-resolution commands use it by default.

### AC14. Group close is explicit and testable

Given an active cockpit group exists, when the operator runs `mesh ui close`, then all live sessions in the active `ui_group_id` are closed or marked for explicit teardown and the cached local group mapping is cleared.

## Workstream Outline

### Workstream 1. Role session spawn path

- extend `POST /tasks` usage with UI role session metadata
- implement spawn discovery and attach-ready polling
- ensure scheduler/runtime can distinguish UI role tasks from pipeline tasks

### Workstream 2. Mesh UI attach-or-spawn

- keep exact-role attach preference
- spawn missing agent panes in parallel
- keep boss as operator shell
- render pane mode and session identity clearly

### Workstream 3. `ui_group_id` tracking

- persist repo -> cached `ui_group_id` locally
- treat router state as authoritative
- reuse only live groups
- support explicit group close/recovery

### Workstream 4. Inter-role messaging helpers

- resolve `ui_group_id + role -> session_id`
- fail on ambiguity
- route `send`, `send-key`, and `signal`

### Workstream 5. Completion summary routing

- define structured summary payload
- persist it in router-backed state
- allow directed delivery to boss/president

## Remaining Open Questions

None blocking for v1.

Deferred post-v1 consideration:

- if cockpit count/session count grows materially, promote `ui_group_id` from metadata-only filtering to a first-class indexed query surface

## Review Checklist

- Does this spec correctly forbid raw standalone CLI panes as the default `mesh ui` behavior?
- Is router session bus reuse explicit enough?
- Are attach-vs-spawn semantics unambiguous?
- Are failure states strict enough to avoid misleading UX?
- Is the role identity model concrete enough to implement without inventing a second orchestration layer?
