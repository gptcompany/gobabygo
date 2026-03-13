# Plan: Mesh UI Role Sessions

## Objective

Implement `mesh ui` so each role pane is backed by a real mesh session instead of a raw standalone CLI process.

## Scope

In scope:
- pane identity and visible role labeling
- attach-or-spawn behavior for each role pane
- task-backed role session creation
- `ui_group_id` grouping for one operator cockpit
- router-backed inter-role messaging helpers
- completion-summary routing for pane-backed sessions

Out of scope for this phase:
- autonomous multi-agent delegation prompts
- replacing CCS as the frontend CLI
- full mobile UX redesign
- historical migration of old raw CLI panes

## Design Decisions

- Spawn primitive: create a normal session task, not a session row directly.
- API surface: extend `POST /tasks` with UI role session metadata; do not add a new endpoint in v1.
- Session grouping: use `ui_group_id` shared across all panes in one cockpit, with router-authoritative liveness and local cached lookup.
- Addressing model: helpers resolve `ui_group_id + role -> session_id` and fail on ambiguity.
- Role split: `boss` is an operator control shell; `president`, `lead`, `worker-*`, and `verifier` are agent-role sessions.
- Task contract: `repo` and `role` are top-level task fields; `ui_role_session`, `ui_role`, and `ui_group_id` live in task payload.
- Failure model: hard mesh error state, no silent raw-shell fallback.
- Default layout: keep the current six-pane operator layout.
- Spawn policy: agent-role spawns run in parallel with a `60s` per-role timeout.
- Completion summary payload is structured and router-backed.
- Group-scoped lookup in v1 uses `/sessions?state=open` plus Python-side filtering on `metadata.ui_group_id`.

## Workstreams

### 1. Role Session Spawn Path

Deliverables:
- a router/client path to create a task-backed role session
- metadata convention for:
  - `ui_role_session`
  - `ui_role`
  - `ui_group_id`
- attach-ready session discovery after spawn

Tasks:
1. define the task payload contract for spawned role sessions
2. extend `POST /tasks` usage with explicit UI role metadata
3. ensure scheduler/runtime can distinguish UI role tasks from pipeline tasks
4. implement session discovery loop after spawn
5. add tests for spawn metadata and attach resolution

### 2. Mesh UI Attach-or-Spawn

Deliverables:
- `mesh ui` resolves each pane to either attach or spawn
- no raw CLI default path remains

Tasks:
1. keep exact-role attach preference inside the active `ui_group_id`
2. keep `boss` as operator shell
3. if no match exists, spawn an agent-role session using the task client path
4. start agent-role spawns in parallel
5. enforce `60s` per-role timeout with visible progress state
6. attach the pane to the resulting session
7. render visible pane labels:
   - role
   - repo
   - provider
   - session short id
   - attach/spawn mode
8. export `MESH_UI_GROUP_ID` into every pane, including `boss`

### 3. UI Group Tracking

Deliverables:
- stable grouping for all sessions opened by one `mesh ui` cockpit

Tasks:
1. generate `ui_group_id` as `{repo_name}-ui-{timestamp}` when no live group exists
2. persist the cached repo -> `ui_group_id` mapping under `~/.mesh/ui_groups/`
3. on relaunch, reuse the cached group only if the router still reports at least one live session in it
4. create a new group when the cached one is fully dead
5. expose `ui_group_id` in operator-visible metadata where useful
6. add explicit group close/recovery commands

### 4. Inter-Role Messaging Helpers

Deliverables:
- shell/operator helpers for role-addressed routing

Initial commands:
- `mesh-send <role> <text>`
- `mesh-enter <role>`
- `mesh-interrupt <role>`

Tasks:
1. resolve peer session from current repo + `ui_group_id` + role
2. call existing router endpoints:
   - `/sessions/send`
   - `/sessions/send-key`
   - `/sessions/signal`
3. fail clearly on ambiguity or missing peer
4. add tests for peer resolution and operator errors

### 5. Completion Summary Routing

Deliverables:
- structured stop/completion summary for pane-backed sessions

Tasks:
1. define the v1 summary payload:
   - `type`
   - `role`
   - `ui_group_id`
   - `status`
   - `summary_text`
   - `artifacts`
2. have the session worker emit it automatically on task completion/failure in v1
3. store it in task result and/or session message metadata
4. allow directed delivery to `boss` or `president`
5. ensure operator inspection is possible from router-backed state

## Validation Plan

### Unit / Integration

- session spawn metadata tests
- scheduler/runtime filtering tests for UI role tasks
- attach-vs-spawn precedence tests
- `ui_group_id` persistence tests
- role-addressed helper resolution tests
- completion summary persistence/routing tests
- `mesh ui` pane label tests
- headless attach-or-spawn logic tests without iTerm2
- boss-pane environment tests for `MESH_UI_GROUP_ID`
- pure-function tests for attach/spawn decision logic extracted from iTerm2 wiring

### E2E

1. open `mesh ui` in `snake-game`
2. verify `boss` remains an operator pane and the other default panes become mesh-backed sessions
3. verify pane labels show role identity clearly
4. send a message from one role to another through the bus
5. verify the target pane receives it
6. complete one role session and inspect the routed stop summary
7. verify Matrix/operator controls still work on pane-backed sessions
8. relaunch `mesh ui` and verify `ui_group_id` reuse only happens for live groups
9. run `mesh ui close` and verify the active cockpit group is torn down cleanly

## Risks

- duplicate spawn if attach/discovery races are not serialized
- ambiguous peer resolution when multiple live sessions exist for one role
- UX confusion if `ui_group_id` is hidden too aggressively
- provider/account mismatch if UI policy and provider runtime policy drift
- stale group reuse if router-authoritative liveness checks are incomplete

## Rollout Order

1. land spawn metadata and router/client path
2. land `ui_group_id` tracking
3. land `mesh ui` attach-or-spawn logic and final pane labels
4. land role-addressed helpers
5. land completion-summary routing and `mesh ui close`
6. run Gemini-only E2E on `snake-game`
7. only then consider wider provider role mapping
