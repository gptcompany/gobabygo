# Spec: Mesh Lite

## Summary

`mesh-lite` is not a new full cockpit.

The canonical operator runtime in this repo remains:

- `mesh ui` for layout lifecycle
- `mesh ui attach` / `mesh ui close` for reattach and shutdown
- `mesh term` for mechanical pane targeting

`mesh-lite` exists only to add the capability that the current mesh path is
missing:

- bind live role panes to transcript/JSONL sources
- keep a small local registry of that binding
- provide manual `relay-last` from one live role to another

This is a narrow augmentation layer, not a second session manager.

## Problem

The repo already has working operator mechanics:

- iTerm2 layout spawn
- pane role markers
- close/attach lifecycle
- direct send/focus/dump targeting

What is still missing is a clean way to:

1. discover which live pane corresponds to which transcript/JSONL source
2. persist that mapping locally
3. relay the last useful assistant response from one role to another

The previous spec was too loose about `keep` vs `replace`, which made it too
easy to drift into rebuilding spawn/close/lifecycle that already exist.

## Goal

Add transcript-aware relay on top of the existing mesh UI lifecycle.

The correct operator flow is:

1. open the layout with `mesh ui <repo>`
2. let `mesh ui` own spawn/attach/close
3. use `mesh-lite` only to discover/bind the live panes
4. use `mesh-lite` to inspect transcript bindings
5. use `mesh-lite` to manually relay the last assistant reply

## Non-Goals

`mesh-lite` v1 does **not**:

- replace `mesh ui`
- replace `mesh ui close`
- replace `mesh ui attach`
- replace `mesh term`
- introduce a second spawner
- introduce a second close lifecycle
- introduce a second sessions dashboard
- rely on router/session-worker/hooks in the critical path
- require MCP in the critical path
- attempt semantic auto-routing

## Keep vs Add

### Keep As Canonical

- `scripts/mesh`
- `scripts/mesh_iterm_ui.py`
- `scripts/mesh_iterm_control.py`
- `mapping/operator_ui.yaml`

### Add

- transcript discovery helpers
- local registry for role -> live pane -> transcript binding
- manual `relay-last`

### Do Not Build Yet

- custom `spawn-team`
- custom `close`
- custom `attach`
- custom full session dashboard
- PAL attached bridge

## Current Runtime Contract

### Existing Runtime We Reuse

`mesh ui` already provides:

- role layout opening
- pane identity markers:
  - `user.mesh_ui_tab`
  - `user.mesh_repo`
  - `user.mesh_role`
- close semantics
- attach/resume semantics

`mesh term` already provides:

- list
- focus
- send
- exec
- key
- dump

These remain the primary control plane for live panes.

`mesh-lite` may use a narrow internal transport helper for relay delivery, but
that helper must stay aligned with the same live-pane model rather than creating
an alternate control plane.

### New Runtime Contract

`mesh-lite` adds:

1. `discover`
   - scan mesh-marked panes already opened by `mesh ui`
   - bind each role to:
     - live session id
     - tty
     - repo
     - best-effort transcript path

2. `status`
   - show the local binding registry
   - not a replacement for `mesh sessions`

3. `relay-last`
   - read last assistant message from the source transcript
   - send it to the target live pane

## Minimal Commands

### Existing Commands We Keep

- `mesh ui <repo>`
- `mesh ui attach <repo>`
- `mesh ui close <repo>`
- `mesh term list`
- `mesh term focus`
- `mesh term send`
- `mesh term exec`
- `mesh term key`
- `mesh term dump`

### New Commands

- `mesh lite discover --project <repo>`
- `mesh lite status --project <repo>`
- `mesh lite probe --project <repo>`
- `mesh lite relay-last --project <repo> <source_role> <target_role>`

Optional later:

- `mesh lite logs <role>`

## References

### Primary Internal References

- `scripts/mesh`
  - keep as the operator-facing facade
  - `lite_usage()` is documentation only; do not duplicate runtime there
- `scripts/mesh_iterm_ui.py`
  - `_command_for_role(...)`
  - `_mark_mesh_ui_sessions(...)`
  - `_launch_layout(...)`
  - these remain the canonical layout/lifecycle hooks
- `scripts/mesh_iterm_control.py`
  - `_mesh_sessions(...)`
  - `_find_mesh_pane(...)`
  - `_screen_tail(...)`
  - these remain the canonical live-pane discovery/control primitives
- `mapping/operator_ui.yaml`
  - keep as the role definition source
  - do not clone role semantics into `mesh-lite`

### Current Internal Files To Use

- `scripts/mesh_lite/registry.py`
  - keep
  - narrow on-disk binding store for:
    - `project`
    - `role`
    - `session_id`
    - `tty`
    - `jsonl_path`
    - optional future-compatible fields:
      - `backend_id`
      - `provider`
      - `launch_mode`
      - `upstream_session_id`
- `scripts/mesh_lite/jsonl.py`
  - keep
  - project-scoped transcript discovery and assistant message extraction
- `scripts/mesh_lite/cli.py`
  - keep as the thin CLI for:
    - `probe`
    - `discover`
    - `status`
    - `relay-last`
- `scripts/mesh_lite/spike.py`
  - keep as Slice 0 regression anchor

### Current Internal Files To Archive Or Ignore

- `scripts/mesh_lite/iterm.py`
  - keep as the active narrow live-session transport helper:
    - `list_sessions()`
    - `get_session()`
    - `send_line()`
    - `ensure_safe_target()`
    - `dump_screen()`
  - the custom layout/spawn branch has already been archived
- `archived/mesh_lite_spawn_experiment.py`
  - archived custom layout/spawn branch
- `archived/test_mesh_lite_spawn_experiment.py`
  - archived test for the discarded custom spawn branch

### Secondary External References

- `maniple`
  - use only as reference for:
    - `/tmp/maniple/src/maniple_mcp/registry.py`
    - `/tmp/maniple/src/maniple_mcp/iterm_utils.py`
    - `/tmp/maniple/src/maniple_mcp/idle_detection.py`
  - what to take:
    - registry shape ideas
    - iTerm send semantics (`\r`, delays)
    - idle/transcript heuristics
  - what not to take now:
    - its runtime lifecycle
    - tmux/backend orchestration
- `claude-code-monitor`
  - use only as reference for:
    - `/tmp/claude-code-monitor/src/utils/send-text.ts`
    - `/tmp/claude-code-monitor/src/utils/focus.ts`
  - what to take later:
    - AppleScript escaping
    - TTY-based focus/send safety patterns
  - what not to take now:
    - alternate control plane
- `pal-mcp-server`
  - use only as future attached-bridge reference:
    - `/tmp/pal-mcp-server/tools/clink.py`
    - `/tmp/pal-mcp-server/clink/models.py`
    - `/tmp/pal-mcp-server/clink/constants.py`
    - `/tmp/pal-mcp-server/clink/agents/base.py`
    - `/tmp/pal-mcp-server/clink/agents/__init__.py`
    - `/tmp/pal-mcp-server/clink/agents/claude.py`
    - `/tmp/pal-mcp-server/clink/agents/codex.py`
    - `/tmp/pal-mcp-server/clink/agents/gemini.py`
    - `/tmp/pal-mcp-server/clink/registry.py`
    - `/tmp/pal-mcp-server/clink/parsers/base.py`
    - `/tmp/pal-mcp-server/clink/parsers/__init__.py`
    - `/tmp/pal-mcp-server/clink/parsers/claude.py`
    - `/tmp/pal-mcp-server/clink/parsers/codex.py`
    - `/tmp/pal-mcp-server/clink/parsers/gemini.py`
- `it2ag`
  - `/tmp/it2ag/src/it2ag/ui.py`
  - UI/status inspiration only

## Design Decisions

### D1. `mesh ui` remains the lifecycle owner

There is one canonical layout lifecycle:

- open with `mesh ui`
- reattach with `mesh ui attach`
- close with `mesh ui close`

`mesh-lite` must not fork this.

### D2. `mesh term` remains the mechanical pane controller

Direct pane targeting continues to use the existing mesh term mechanics.

`mesh-lite` may reuse or wrap those mechanics, but must not create a second
independent pane-control model in v1.

### D3. Registry scope is narrow

The local registry exists only to store transcript-aware bindings:

- project
- role
- live session id
- tty
- transcript/jsonl path
- timestamps

It is not a general replacement for the existing session picker/session list UX.

The registry should still reserve optional fields that avoid a future refactor:

- `backend_id`
  - for now expected to be `iterm`
- `provider`
  - expected CLI/provider family when known (`claude`, `codex`, `gemini`, etc.)
- `launch_mode`
  - optional mesh-ui launch mode metadata
- `upstream_session_id`
  - optional provider/router session identifier when available

The current mesh UI path already exposes some of this metadata indirectly through:

- `scripts/mesh_iterm_ui.py`
- `scripts/mesh_ui_role_shell.sh`

so `mesh-lite` should not hard-code a schema that blocks a later attached runner.

### D4. `relay-last` is the new value

The primary feature being added is:

- reliable transcript-backed manual relay between live role panes

Everything else is support code around that.

`mesh lite probe --project <repo>` is part of this value path because transcript
inspection is the operator-side debugging entrypoint for binding quality.

### D5. Transcript binding is the primary technical risk

The hardest real problem is not spawn/close.

It is:

1. locating the correct transcript for a live role pane
2. keeping that mapping stable enough for manual relay

### D6. Attached semantic bridge is postponed

Future PAL-style attached orchestration remains possible, but only after the
manual binding + relay path is reliable.

### D7. PAL is semantic plane only, not session control

PAL/clink already provides:

- role config loading
- prompt_path resolution
- role_args loading
- parser selection per CLI
- runner selection per CLI
- runtime client/role models
- parsed response abstraction

Relevant files:

- `/tmp/pal-mcp-server/tools/clink.py`
- `/tmp/pal-mcp-server/clink/models.py`
- `/tmp/pal-mcp-server/clink/constants.py`
- `/tmp/pal-mcp-server/clink/registry.py`
- `/tmp/pal-mcp-server/clink/agents/__init__.py`
- `/tmp/pal-mcp-server/clink/agents/base.py`
- `/tmp/pal-mcp-server/clink/agents/claude.py`
- `/tmp/pal-mcp-server/clink/agents/codex.py`
- `/tmp/pal-mcp-server/clink/agents/gemini.py`
- `/tmp/pal-mcp-server/clink/parsers/base.py`
- `/tmp/pal-mcp-server/clink/parsers/__init__.py`
- `/tmp/pal-mcp-server/clink/parsers/claude.py`
- `/tmp/pal-mcp-server/clink/parsers/codex.py`
- `/tmp/pal-mcp-server/clink/parsers/gemini.py`

PAL/clink does **not** currently provide the session-attached runtime we need in
v1, because `BaseCLIAgent.run()` is subprocess-oriented rather than live-pane-oriented.

That means:

- PAL should shape our future interfaces
- PAL should **not** shape the current runtime boundary
- the current implementation should still preserve enough metadata for a later
  attached runner to plug in cleanly

## Repo-By-Repo Implementation Map

### gobabygo

- use now:
  - `scripts/mesh`
  - `scripts/mesh_iterm_ui.py`
  - `scripts/mesh_iterm_control.py`
  - `scripts/mesh_lite/registry.py`
  - `scripts/mesh_lite/jsonl.py`
  - `scripts/mesh_lite/cli.py`
  - `scripts/mesh_lite/spike.py`
- do not extend now:
  - router/session-worker paths
  - new iTerm layout lifecycle
  - alternate session dashboard

### maniple

- use as reference only:
  - `registry.py` for narrow registry structure
  - `iterm_utils.py` for input semantics
  - `idle_detection.py` for later binding hardening
  - `tools/discover_workers.py` and `session_state.py` for transcript/session correlation
- do not import as runtime dependency in the current slice

### claude-code-monitor

- use as reference only:
  - AppleScript quoting
  - TTY focus/send safety
- do not use as a new operator runtime

### PAL

- do not use in v1 runtime
- do use now as interface-design reference
- the future attached bridge should preserve compatibility with PAL concepts:
  - client config
  - role config
  - runner selection
  - parser selection

### What PAL Already Gives Us

- semantic client/role registry:
  - `/tmp/pal-mcp-server/clink/registry.py`
- runtime client/role models:
  - `/tmp/pal-mcp-server/clink/models.py`
- CLI-family defaults:
  - `/tmp/pal-mcp-server/clink/constants.py`
- runtime role prompt loading:
  - `prompt_path`
  - `role_args`
- CLI-family-specific parsing/recovery:
  - `/tmp/pal-mcp-server/clink/agents/claude.py`
  - `/tmp/pal-mcp-server/clink/agents/codex.py`
  - `/tmp/pal-mcp-server/clink/agents/gemini.py`
- parser contract and parser registry:
  - `/tmp/pal-mcp-server/clink/parsers/base.py`
  - `/tmp/pal-mcp-server/clink/parsers/__init__.py`
  - `/tmp/pal-mcp-server/clink/parsers/claude.py`
  - `/tmp/pal-mcp-server/clink/parsers/codex.py`
  - `/tmp/pal-mcp-server/clink/parsers/gemini.py`

### What PAL Does Not Give Us Yet

- iTerm2 live pane discovery
- live pane identity mapping
- tty -> transcript binding
- safe send into already-running panes
- wait-for-response on a persistent live pane

Those remain the responsibility of the current mesh + mesh-lite path.

### What We Should Preserve Now To Stay PAL-Compatible

- registry fields that map cleanly onto PAL concepts:
  - `provider`
  - `role`
  - `backend_id`
  - `upstream_session_id`
- separation of concerns:
  - transcript parsing in `scripts/mesh_lite/jsonl.py`
  - live transport in `scripts/mesh_lite/iterm.py`
  - orchestration in `scripts/mesh_lite/cli.py`
- avoid provider-specific logic leaking across modules

## Reference Code Map

### Repo: gobabygo

Use directly now:

- `scripts/mesh`
- `scripts/mesh_iterm_ui.py`
- `scripts/mesh_iterm_control.py`
- `scripts/mesh_ui_role_shell.sh`
- `scripts/mesh_lite/registry.py`
- `scripts/mesh_lite/jsonl.py`
- `scripts/mesh_lite/cli.py`
- `scripts/mesh_lite/iterm.py`
- `scripts/mesh_lite/spike.py`

Ignore for the current slice:

- `src/router/*`
- session-worker lifecycle
- router-backed relay paths

## Analysis Workflow Note

This spec set is currently standalone inside this repo.

It does not yet ship the standard `.specify/` scaffolding used by the canonical
`/speckit.analyze` command implementation, so consistency analysis for this spec
set must currently be performed manually or by an equivalent local review until
that scaffolding is added.

### Repo: maniple

Use as patterns only:

- `/tmp/maniple/src/maniple_mcp/registry.py`
  - registry shape, terminal identity concepts
- `/tmp/maniple/src/maniple_mcp/iterm_utils.py`
  - send semantics, delays, `\r` enter behavior
- `/tmp/maniple/src/maniple_mcp/idle_detection.py`
  - idle heuristics for later hardening
- `/tmp/maniple/src/maniple_mcp/tools/discover_workers.py`
  - discovery shape for terminal session -> transcript correlation
- `/tmp/maniple/src/maniple_mcp/session_state.py`
  - Claude transcript parsing and marker concepts

Do not import directly now:

- MCP server/runtime
- terminal backend lifecycle
- tmux paths

### Repo: claude-code-monitor

Use as patterns only:

- `/tmp/claude-code-monitor/src/utils/send-text.ts`
  - AppleScript escaping and paste/send strategy
- `/tmp/claude-code-monitor/src/utils/focus.ts`
  - tty-based focus and safe quoting

Do not import directly now:

- alternate terminal abstraction
- extra terminal families beyond the current need

### Repo: pal-mcp-server

Use as patterns now:

- `/tmp/pal-mcp-server/tools/clink.py`
  - end-to-end semantic bridge shape
- `/tmp/pal-mcp-server/clink/models.py`
  - client/role runtime contract
- `/tmp/pal-mcp-server/clink/constants.py`
  - parser/runner naming defaults
- `/tmp/pal-mcp-server/clink/registry.py`
  - role/client config loading
- `/tmp/pal-mcp-server/clink/agents/__init__.py`
  - runner factory shape
- `/tmp/pal-mcp-server/clink/agents/base.py`
  - runner contract to mirror later
- `/tmp/pal-mcp-server/clink/agents/claude.py`
  - Claude-specific system prompt behavior
- `/tmp/pal-mcp-server/clink/agents/codex.py`
  - Codex-specific recovery shape
- `/tmp/pal-mcp-server/clink/agents/gemini.py`
  - Gemini-specific recovery shape
- `/tmp/pal-mcp-server/clink/parsers/base.py`
  - parsed response contract
- `/tmp/pal-mcp-server/clink/parsers/__init__.py`
  - parser registry shape
- `/tmp/pal-mcp-server/clink/parsers/claude.py`
- `/tmp/pal-mcp-server/clink/parsers/codex.py`
- `/tmp/pal-mcp-server/clink/parsers/gemini.py`
  - provider-specific parse contracts to reuse conceptually, not re-invent blindly

Do not copy into v1 runtime:

- subprocess runner model
- fresh-process execution path

### Repo: it2ag

Use only as UI inspiration:

- `/tmp/it2ag/src/it2ag/ui.py`

Do not use as runtime foundation:

- agent state detector
- web UI server

### it2ag

- do not depend on it for runtime behavior
- use only if we later want a better compact status renderer

## Acceptance Criteria

### AC1. Existing lifecycle is reused

`mesh-lite` does not require a separate spawner or closer to be useful.

### AC2. Discover works on live mesh UI panes

Given a layout already opened by `mesh ui`, `mesh lite discover --project <repo>`
can find the live role panes and persist their local bindings.

### AC3. Status shows bindings, not a second session universe

`mesh lite status` shows transcript-aware role bindings only.

### AC4. Relay-last works on live panes

`mesh lite relay-last --project <repo> boss president`:

- resolves the source role binding
- reads the last assistant reply from transcript/JSONL
- sends it to the target live pane

### AC5. No lifecycle regression

Nothing in `mesh-lite` may break:

- `mesh ui`
- `mesh ui attach`
- `mesh ui close`
- `mesh term`

## Future Path

After the transcript-aware manual path is stable:

1. harden transcript binding with stronger per-role association
2. improve send/focus safety
3. evaluate an attached semantic bridge inspired by PAL

That future work must build on the existing mesh UI lifecycle, not replace it by
default.
