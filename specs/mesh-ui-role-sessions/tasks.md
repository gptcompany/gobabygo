# Tasks: Mesh UI Role Sessions

## Objective

Translate `spec.md` and `plan.md` into an implementable backlog for turning `mesh ui` into a mesh-backed role-session cockpit.

## Conventions

- `Txxx` = implementation task
- `Depends on` lists hard prerequisites
- `Done when` is the acceptance gate for the task itself
- Agent-role panes: `president`, `lead`, `worker-*`, `verifier`
- Operator-role pane: `boss`

## Task Breakdown

### T001. Extend task creation API for UI role sessions

Scope:
- Extend `TaskCreateRequest` to accept top-level `repo` and `role`
- Keep `ui_role_session`, `ui_role`, and `ui_group_id` in `payload`
- Ensure `POST /tasks` persists the new top-level fields correctly
- Keep task creation semantics aligned with thread-step-derived tasks

Likely files:
- `src/router/models.py`
- `src/router/server.py`
- `tests/router/test_server.py`
- `tests/router/test_server_coverage.py`
- `tests/test_meshctl_threads.py`
- `tests/router/test_thread_integration.py`

Depends on:
- none

Done when:
- `POST /tasks` accepts `repo` and `role`
- created tasks preserve those values in DB/API responses
- `_handle_create_task` persists `repo` and `role` correctly
- direct task creation and thread-step-derived task creation persist `repo` and `role` with the same semantics
- tests cover both plain tasks and UI role tasks

### T002. Add scheduler discrimination for UI role tasks

Scope:
- Prevent non-UI session workers from leasing UI role tasks
- Use `payload.ui_role_session=true` as the minimum discriminator in v1
- Use worker capability `ui_role` as the worker-side opt-in mechanism

Likely files:
- `src/router/scheduler.py`
- `src/router/worker_client.py`
- `src/router/session_worker.py`
- `tests/router/test_scheduler.py`
- `tests/router/test_server.py`

Depends on:
- `T001`

Done when:
- a UI role task is not leased to a worker that does not opt into UI role tasks
- a UI role task is leaseable only by a worker advertising capability `ui_role`
- normal pipeline session tasks still dispatch correctly
- scheduler tests cover both eligible and ineligible workers

### T003. Add UI group cache helpers on the operator side

Scope:
- Create local helpers for repo -> `ui_group_id` cache under `~/.mesh/ui_groups/`
- Implement read/write/clear behavior
- Reuse cached group only when router still reports a live session in that group

Likely files:
- `scripts/mesh_iterm_ui.py`
- `scripts/mesh_session_cli.py`
- `tests/test_mesh_ui_script.py`

Depends on:
- none

Done when:
- `mesh ui` can create and persist a `ui_group_id`
- stale cache is ignored when router has no live sessions in that group
- local helper logic is unit-testable without iTerm2

### T004. Extract headless attach-or-spawn decision logic

Scope:
- Pull repo/group/session resolution into pure functions separate from iTerm2 API wiring
- Keep `mesh_iterm_ui.py` as a thin client
- Make the decision layer testable without iTerm2

Likely files:
- `scripts/mesh_iterm_ui.py`
- `tests/test_mesh_ui_script.py`

Depends on:
- `T003`

Done when:
- attach-vs-spawn decisions can be unit tested without opening iTerm2
- iTerm2-specific code only handles pane creation and command dispatch

### T005. Implement group-scoped live session discovery

Scope:
- Query `/sessions?state=open`
- Filter by `metadata.ui_group_id` in Python for v1
- Read repo identity from `Session.metadata.repo` written at session-open time
- Keep exact-role and provider-aware attach precedence

Likely files:
- `scripts/mesh_iterm_ui.py`
- `scripts/mesh_ui_live_attach.py`
- `scripts/mesh_session_cli.py`
- `tests/test_mesh_ui_script.py`
- `tests/test_mesh_session_cli.py`

Depends on:
- `T001`
- `T003`
- `T004`

Done when:
- attach resolution prefers the active `ui_group_id`
- attach resolution reads repo from `Session.metadata.repo` without requiring a task join in v1
- cross-cockpit accidental attach does not happen by default
- existing role matching tests are updated for group-aware behavior, including exact role/provider/newest precedence within the group
- peer/session resolution remains router-backed, not cache-backed

### T006. Implement task-backed spawn path for agent panes

Scope:
- For missing agent-role panes, create a session task through `POST /tasks`
- Include `repo`, `role`, `target_cli`, `execution_mode=session`
- Include UI metadata in payload
- Poll for session materialization up to `60s`

Likely files:
- `scripts/mesh_iterm_ui.py`
- `src/meshctl.py` or shared client helpers if needed
- `tests/test_mesh_ui_script.py`
- `tests/test_meshctl.py`

Depends on:
- `T001`
- `T002`
- `T004`
- `T005`

Done when:
- missing agent panes create tasks and attach to the resulting sessions
- all agent spawns run in parallel
- timeout path shows explicit failure state and retry hint
- spawn wait stays as poll-with-timeout only; no local retry/backoff orchestration is added

### T007. Keep `boss` as operator pane with group-aware helpers

Scope:
- Do not spawn a provider-backed session for `boss`
- Export `MESH_UI_GROUP_ID` into every pane, including `boss`
- Ensure `boss` stays in repo context with mesh helper commands available
- Remove or ignore `boss.provider` in `mapping/operator_ui.yaml`

Likely files:
- `scripts/mesh_ui_role_shell.sh`
- `scripts/mesh_iterm_ui.py`
- `tests/test_mesh_ui_script.py`

Depends on:
- `T003`
- `T004`

Done when:
- `boss` remains a control shell
- `MESH_UI_GROUP_ID` is available in the `boss` environment
- role title/badge and banner clearly identify `boss` as operator

### T008. Finalize pane labeling from real runtime identity

Scope:
- Show role, repo, attach/spawn mode, provider, session short id in pane banner/title
- Keep labels truthful: no placeholder `session_id` before a real session exists

Likely files:
- `scripts/mesh_ui_role_shell.sh`
- `scripts/mesh_iterm_ui.py`
- `tests/test_mesh_ui_script.py`

Depends on:
- `T006`
- `T007`

Done when:
- agent panes show `attached` or `spawned`
- agent panes show provider and session short id
- `boss` shows operator label and current `ui_group_id`

### T009. Add role-addressed operator helpers

Scope:
- Add `mesh send <role> <text>`
- Add `mesh enter <role>`
- Add `mesh interrupt <role>`
- Resolve peer by `ui_group_id + role`
- Fail on ambiguity or missing peer

Likely files:
- `scripts/mesh`
- `scripts/mesh_session_cli.py`
- `src/meshctl.py`
- `tests/test_mesh_session_cli.py`
- `tests/test_meshctl.py`

Depends on:
- `T003`
- `T005`
- `T007`

Done when:
- operator can route text and key/signal controls to a peer role without knowing `session_id`
- ambiguity produces an explicit error instead of guessing
- helpers default to the current repo and active `ui_group_id`
- helper resolution is router-backed on each invocation, not satisfied from stale local cache

### T010. Emit structured completion summaries from session worker

Scope:
- On task completion/failure, have the session worker emit a structured completion summary
- Store it in session message metadata and/or task result metadata
- Keep payload aligned with the spec
- Emit summaries only for UI role tasks (`payload.ui_role_session=true`)

Likely files:
- `src/router/session_worker.py`
- `src/router/server.py`
- `tests/router/test_session_worker.py`
- `tests/test_result_capture.py`

Depends on:
- `T001`

Done when:
- completed/failed UI role sessions emit the structured summary payload
- non-UI tasks keep the current completion behavior without UI summaries
- summary is queryable from router-backed state
- non-UI tasks are not regressed

### T011. Route completion summaries to `boss` / `president`

Scope:
- Allow directed delivery of completion summary to peer roles in the same `ui_group_id`
- Make routed summary inspectable by operator tools

Likely files:
- `scripts/mesh_session_cli.py`
- `src/router/session_worker.py`
- `src/router/server.py`
- `tests/router/test_session_worker.py`
- `tests/test_mesh_session_cli.py`

Depends on:
- `T005`
- `T009`
- `T010`

Done when:
- a `lead` summary can be routed to `president` or `boss`
- operator can inspect the routed summary through mesh tooling

### T012. Add `mesh ui close` group teardown

Scope:
- Close or explicitly tear down all live sessions in the active `ui_group_id`
- Clear the local cache entry for the repo
- Make teardown explicit and testable

Likely files:
- `scripts/mesh`
- `scripts/mesh_session_cli.py`
- `src/meshctl.py`
- `tests/test_mesh_session_cli.py`
- `tests/test_meshctl.py`

Depends on:
- `T003`
- `T005`

Done when:
- `mesh ui close` tears down the active cockpit group
- the cached repo -> `ui_group_id` mapping is cleared
- relaunch after close creates a fresh group

### T013. Add headless integration coverage for attach/spawn

Scope:
- Cover the attach-or-spawn path without requiring iTerm2
- Mock router responses, spawned tasks, and discovered sessions
- Keep GUI verification only for final smoke, not for core correctness

Likely files:
- `tests/test_mesh_ui_script.py`
- new dedicated tests if needed

Depends on:
- `T004`
- `T005`

Done when:
- core attach/spawn logic is covered by headless tests
- iTerm2 is only needed for final operator smoke

### T014. Gemini-only E2E on `snake-game`

Scope:
- Open `mesh ui` in `/media/sam/1TB/snake-game`
- Validate default six-pane cockpit
- Use Gemini-only where possible to avoid quota burn
- Verify inter-role messaging and completion-summary flow

Depends on:
- `T006`
- `T007`
- `T008`
- `T009`
- `T010`
- `T011`
- `T012`

Done when:
- `boss` is operator-only
- agent panes are mesh-backed sessions
- peer messaging works
- completion summary is routed and inspectable
- `mesh ui close` tears down the active group cleanly
- two independent `mesh ui` launches for the same repo produce distinct `ui_group_id`s and do not cross-attach by default

## Recommended Execution Order

1. `T001`
2. `T002`
3. `T003`
4. `T004`
5. `T005`
6. `T006`
7. `T007`
8. `T008`
9. `T009`
10. `T010`
11. `T011`
12. `T012`
13. `T013`
14. `T014`

## Exit Criteria

The backlog is complete when:
- `mesh ui` no longer relies on standalone raw CLI panes for agent roles
- `boss` remains an operator pane
- role-to-role communication works through the router session bus
- completion summaries are router-backed and routable
- the cockpit can be closed explicitly by `ui_group_id`
