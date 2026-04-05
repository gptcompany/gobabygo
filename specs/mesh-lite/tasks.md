# Tasks: Mesh Lite

## Objective

Implement the missing transcript-aware relay layer on top of the existing mesh
UI lifecycle.

## Conventions

- `Txxx` = implementation task
- `Depends on` lists prerequisites
- `Done when` is the acceptance gate

## Backlog

### T000. Slice 0 validation

Scope:

- validate live pane discovery
- validate transcript discovery
- validate safe relay into a live pane

Status:

- already proven

Done when:

- the spike remains available as reference and regression anchor

### T001. Correct the boundary

Status:

- done

Scope:

- remove ambiguity between existing mesh lifecycle and new mesh-lite work
- de-scope custom lifecycle paths from the critical path

Likely files:

- `specs/mesh-lite/spec.md`
- `specs/mesh-lite/plan.md`
- `specs/mesh-lite/tasks.md`
- `scripts/mesh_lite/cli.py`

Depends on:

- none

Done when:

- the plan clearly says `mesh ui` owns spawn/attach/close
- `mesh-lite` is framed as discover/status/relay only
- custom `spawn-team` and `list-live` are no longer in the active CLI path

### T002. Implement local binding registry

Status:

- done

Scope:

- keep a narrow registry for:
  - project
  - role
  - session id
  - tty
  - transcript path
  - plus optional future-compatible fields:
    - backend_id
    - provider
    - launch_mode
    - upstream_session_id

Likely files:

- `scripts/mesh_lite/registry.py`
- `tests/test_mesh_lite_registry.py`

Depends on:

- `T001`

Done when:

- registry persists bindings cleanly
- upsert is idempotent per project/role
- adding optional future fields does not require schema churn later
- active file description and schema expectations stay aligned with `spec.md`

Use:

- `scripts/mesh_lite/registry.py`
- `tests/test_mesh_lite_registry.py`

Reference only:

- `/tmp/maniple/src/maniple_mcp/registry.py`

### T003. Discover live mesh UI panes

Status:

- done

Scope:

- scan panes already opened by `mesh ui`
- read their existing role/repo markers
- write local bindings
- collect future-compatible metadata where already available without adding a
  second lifecycle

Likely files:

- `scripts/mesh_lite/cli.py`
- `scripts/mesh_iterm_control.py`
- `scripts/mesh_lite/registry.py`

Depends on:

- `T002`

Done when:

- `mesh lite discover --project <repo>` finds live role panes
- registry gets populated from those live panes
- repeated discover does not duplicate bindings
- repeated discover prunes stale bindings that are no longer live
- when available from the live session context, registry best-effort captures:
  - `provider`
  - `launch_mode`
  - `upstream_session_id`

Use:

- `scripts/mesh_iterm_control.py`
  - `_mesh_sessions(...)`
- `scripts/mesh_ui_role_shell.sh`
  - metadata conventions for provider/launch/session banners/env
- `scripts/mesh_lite/cli.py`
- `scripts/mesh_lite/registry.py`

Do not use:

- `scripts/mesh_lite/iterm.py` custom spawn helpers
- router/session-worker code

### T004. Best-effort transcript binding

Status:

- done

Scope:

- resolve transcript candidates for the discovered project
- bind best-effort transcript paths to roles
- expose the same binding quality through `mesh lite probe --project <repo>`

Likely files:

- `scripts/mesh_lite/jsonl.py`
- `scripts/mesh_lite/cli.py`
- `scripts/mesh_lite/registry.py`
- `tests/test_mesh_lite_jsonl.py`

Depends on:

- `T003`

Done when:

- each discovered role gets either:
  - a transcript path when the match is provable or non-ambiguous
  - or an explicit unresolved state
- `mesh lite probe --project <repo>` reports the same candidate/binding view used
  by `discover` and `relay-last`

Use:

- `scripts/mesh_lite/jsonl.py`
  - `transcript_candidates(...)`
  - `extract_last_assistant_msg(...)`
- `scripts/mesh_lite/registry.py`
- `scripts/mesh_lite/cli.py`
- `tests/test_mesh_lite_jsonl.py`

Reference only:

- `/tmp/maniple/src/maniple_mcp/idle_detection.py`
- `/tmp/maniple/src/maniple_mcp/tools/discover_workers.py`
- `/tmp/maniple/src/maniple_mcp/session_state.py`

### T005. Binding status

Status:

- done

Scope:

- show the local role/transcript bindings
- avoid duplicating `mesh sessions`

Likely files:

- `scripts/mesh_lite/cli.py`

Depends on:

- `T003`
- `T004`

Done when:

- `mesh lite status --project <repo>` shows role/session/tty/transcript binding
- output makes relay-ready vs unresolved state explicit
- output stays scoped to bindings, not generic session inventory

Use:

- `scripts/mesh_lite/cli.py`
- `scripts/mesh_lite/registry.py`

### T006. Relay-last

Status:

- done

Scope:

- read last assistant message from source transcript
- deliver it to target live pane

Likely files:

- `scripts/mesh_lite/cli.py`
- `scripts/mesh_lite/jsonl.py`
- `scripts/mesh_lite/iterm.py`

Depends on:

- `T004`

Done when:

- `mesh lite relay-last --project <repo> boss president` works on live panes
- failure is explicit when source transcript is missing or ambiguous
- `--dry-run` is reliable and side-effect free
- transport remains robust when the Python iTerm path is unavailable and the
  AppleScript fallback is used

Use:

- `scripts/mesh_lite/cli.py`
- `scripts/mesh_lite/jsonl.py`
- `scripts/mesh_lite/iterm.py`

Reference only:

- `/tmp/maniple/src/maniple_mcp/iterm_utils.py`

### T007. Send/focus hardening

Status:

- done

Scope:

- improve safety of live injection where needed

Likely files:

- `scripts/mesh_lite/iterm.py`
- `scripts/mesh_iterm_control.py`
- optional reference only:
  - `/tmp/claude-code-monitor/src/utils/send-text.ts`
  - `/tmp/claude-code-monitor/src/utils/focus.ts`

Depends on:

- `T006`

Done when:

- `relay-last` completes without error on live panes for Claude, Codex, and Gemini providers
- relay of content > 500 characters is delivered without truncation
- `ensure_safe_target()` rejects panes whose foreground command is outside the
  allowed safe target set

Use:

- `scripts/mesh_lite/iterm.py`
- `scripts/mesh_iterm_control.py`

Reference only:

- `/tmp/claude-code-monitor/src/utils/send-text.ts`
- `/tmp/claude-code-monitor/src/utils/focus.ts`
- `/tmp/maniple/src/maniple_mcp/iterm_utils.py`

### T008. Future attached bridge design slice

Status:

- done

Scope:

- define what comes after the manual relay path
- define it against PAL's real code contracts, not just its README

Likely files:

- docs/spec only
- PAL reference only

Depends on:

- `T006`

Done when:

- the attached semantic bridge path is documented without affecting v1
- the future attached path is defined against PAL's actual:
  - client/role models
  - parser contracts
  - runner contracts
- no v1 code written now would need a structural rewrite just to attach PAL later

Reference only:

- `/tmp/pal-mcp-server/tools/clink.py`
- `/tmp/pal-mcp-server/clink/models.py`
- `/tmp/pal-mcp-server/clink/constants.py`
- `/tmp/pal-mcp-server/clink/agents/base.py`
- `/tmp/pal-mcp-server/clink/registry.py`
- `/tmp/pal-mcp-server/clink/agents/__init__.py`
- `/tmp/pal-mcp-server/clink/agents/claude.py`
- `/tmp/pal-mcp-server/clink/agents/codex.py`
- `/tmp/pal-mcp-server/clink/agents/gemini.py`
- `/tmp/pal-mcp-server/clink/parsers/base.py`
- `/tmp/pal-mcp-server/clink/parsers/__init__.py`
- `/tmp/pal-mcp-server/clink/parsers/claude.py`
- `/tmp/pal-mcp-server/clink/parsers/codex.py`
- `/tmp/pal-mcp-server/clink/parsers/gemini.py`

Goal:

- keep the v1 registry and relay interfaces compatible with a future
  PAL-style attached runner, without introducing PAL as a runtime dependency now

### T009. Lifecycle regression smoke test

Status:

- done

Scope:

- verify that `mesh ui`, `mesh ui attach`, `mesh ui close`, and `mesh term`
  subcommands still work correctly after mesh-lite integration

Likely files:

- `scripts/mesh`
- `scripts/mesh_iterm_ui.py`
- `scripts/mesh_iterm_control.py`

Depends on:

- `T006`

Done when:

- `mesh ui <repo>` opens the layout without errors
- `mesh ui attach <repo>` reattaches an existing layout
- `mesh ui close <repo>` closes the layout cleanly
- `mesh term list` returns expected panes
- `mesh term send` delivers text to a targeted pane
- no mesh-lite code path interferes with any of the above

### T010. E2E validation

Status:

- done (Script implemented in `tests/smoke/test_mesh_lite_e2e_live.py`, requires manual interactive execution due to iTerm2 API requirements)

Scope:

- run the full operator flow described in plan.md validation plan
- verify the complete discover -> status -> relay-last cycle on live panes

Likely files:

- `scripts/mesh_lite/cli.py`
- `scripts/mesh_lite/registry.py`
- `scripts/mesh_lite/jsonl.py`
- `scripts/mesh_lite/iterm.py`

Depends on:

- `T006`
- `T009`

Done when:

- `mesh ui <repo>` opens a layout
- `mesh lite discover --project <repo>` populates registry
- `mesh lite status --project <repo>` shows bindings
- a reply is generated in one role pane
- `mesh lite relay-last --project <repo> <source> <target>` delivers the reply
- delivery is verified in the target pane
- `mesh ui close <repo>` closes cleanly

## Workflow Note

This task set currently lives outside the standard `.specify/` directory
structure expected by the canonical `/speckit.analyze` command implementation.
Until that scaffolding is added, consistency analysis for this spec set is a
manual/local review step rather than a built-in repo command.
