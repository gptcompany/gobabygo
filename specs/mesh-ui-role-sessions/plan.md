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
- Session grouping: use `ui_group_id` shared across all panes in one cockpit.
- Addressing model: helpers resolve `ui_group_id + role -> session_id`.
- Failure model: hard mesh error state, no silent raw-shell fallback.
- Default layout: keep the current six-pane operator layout.

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
2. choose the API surface:
   - extend `POST /tasks`
   - or add `POST /ui/role-sessions`
3. implement session discovery loop after spawn
4. add tests for spawn metadata and attach resolution

### 2. Mesh UI Attach-or-Spawn

Deliverables:
- `mesh ui` resolves each pane to either attach or spawn
- no raw CLI default path remains

Tasks:
1. keep exact-role attach preference
2. if no match exists, spawn a role session using the chosen client path
3. attach the pane to the resulting session
4. render visible pane labels:
   - role
   - repo
   - provider
   - session short id
   - attach/spawn mode

### 3. UI Group Tracking

Deliverables:
- stable grouping for all sessions opened by one `mesh ui` cockpit

Tasks:
1. generate `ui_group_id` when opening a new cockpit
2. persist the active repo -> `ui_group_id` mapping
3. reuse the active `ui_group_id` when relaunching the same repo cockpit
4. expose `ui_group_id` in operator-visible metadata where useful

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
1. define summary payload shape
2. store it in task result and/or session message metadata
3. allow directed delivery to `boss` or `president`
4. ensure operator inspection is possible from router-backed state

## Validation Plan

### Unit / Integration

- session spawn metadata tests
- attach-vs-spawn precedence tests
- `ui_group_id` persistence tests
- role-addressed helper resolution tests
- completion summary persistence/routing tests
- `mesh ui` pane label tests

### E2E

1. open `mesh ui` in `snake-game`
2. verify six panes become mesh-backed sessions
3. verify pane labels show role identity clearly
4. send a message from one role to another through the bus
5. verify the target pane receives it
6. complete one role session and inspect the routed stop summary
7. verify Matrix/operator controls still work on pane-backed sessions

## Risks

- duplicate spawn if attach/discovery races are not serialized
- ambiguous peer resolution when multiple live sessions exist for one role
- UX confusion if `ui_group_id` is hidden too aggressively
- provider/account mismatch if UI policy and provider runtime policy drift

## Rollout Order

1. land pane labeling improvements
2. land spawn metadata and router/client path
3. land `mesh ui` attach-or-spawn logic
4. land role-addressed helpers
5. land completion-summary routing
6. run Gemini-only E2E on `snake-game`
7. only then consider wider provider role mapping
