# Plan: Mesh Lite

## Objective

Add transcript-aware manual relay on top of the existing `mesh ui` lifecycle.

The plan is intentionally narrow:

- keep `mesh ui` for spawn/attach/close
- keep `mesh term` for pane control
- add local binding + transcript-backed relay

## Scope

In scope:

- discover live mesh UI panes
- bind role -> session -> tty -> transcript
- persist that mapping locally
- show binding status
- relay the last assistant message manually

Out of scope:

- new spawner
- new closer
- replacing `mesh ui`
- replacing `mesh term`
- router/session-worker critical path
- MCP critical path
- automatic orchestration

## Current Status

As of April 5, 2026:

- completed:
  - boundary narrowing
  - local binding registry
  - live pane discovery
  - conservative transcript binding
  - binding-focused status view
  - `relay-last` close-out and final review loop
  - future attached bridge design slice
  - full E2E validation script (requires interactive run)
- not started as final acceptance:

## Design Decisions

- `mesh ui` is the lifecycle owner.
- `mesh term` is the existing pane-control mechanism.
- `mesh-lite` augments those with transcript awareness.
- `maniple` is a reference for transcript/session discovery patterns.
- `claude-code-monitor` is a reference for send/focus hardening patterns.
- `PAL` stays out of v1 runtime, but its runner/role/parser model must inform the
  interfaces we add now so we do not refactor later.

## Compatibility Constraints

The current slice should avoid future refactor by keeping these contracts stable:

- registry entries should allow optional future fields:
  - `backend_id`
  - `provider`
  - `launch_mode`
  - `upstream_session_id`
- `discover` should remain the place where live-pane metadata is normalized
- `relay-last` should remain registry-driven, not hard-coded to one provider
- transcript parsing should stay isolated in `scripts/mesh_lite/jsonl.py`
- live send/focus should stay isolated in `scripts/mesh_lite/iterm.py`
- `scripts/mesh_lite/iterm.py` is allowed as a narrow internal transport helper,
  but it must not grow into a second lifecycle or session manager

These boundaries align with a later PAL-style attached runner without forcing a
rewrite of the current v1 code.

PAL-specific constraint:

- the current v1 code should align with PAL concepts already present in:
  - `/tmp/pal-mcp-server/clink/models.py`
  - `/tmp/pal-mcp-server/clink/constants.py`
  - `/tmp/pal-mcp-server/clink/registry.py`
  - `/tmp/pal-mcp-server/clink/agents/base.py`
  - `/tmp/pal-mcp-server/clink/parsers/base.py`

## Workstreams

### 1. Slice 0 Validation

Already validated:

- live pane identification
- transcript discovery by project
- manual relay into a live CLI

This stays as the proof that the bridge is viable.

### 2. Narrow the Boundary

Deliverables:

- spec alignment
- no new custom lifecycle in `mesh-lite`

Tasks:

1. de-scope custom `spawn-team`
2. de-scope custom `close`
3. keep `mesh-lite` focused on discover/status/relay

Reference files:

- `scripts/mesh`
- `scripts/mesh_lite/cli.py`
- `scripts/mesh_lite/iterm.py`
- `archived/mesh_lite_spawn_experiment.py`
- `archived/test_mesh_lite_spawn_experiment.py`

### 3. Discover Live Mesh UI Panes

Deliverables:

- `mesh lite discover --project <repo>`
- registry entries for live roles already opened by `mesh ui`

Tasks:

1. scan panes marked by `mesh ui`
2. capture role, repo, session id, tty
3. write those bindings to the local registry
4. support repeated discover without duplicate role entries

Primary files to use:

- `scripts/mesh_iterm_control.py`
  - `_mesh_sessions(...)`
  - existing `MeshPane` model
- `scripts/mesh_ui_role_shell.sh`
  - future source for provider/launch metadata conventions
- `scripts/mesh_lite/registry.py`
- `scripts/mesh_lite/cli.py`

Do not use here:

- custom spawn/layout logic
- PAL
- router/session-worker code

### 4. Bind Transcript/JSONL

Deliverables:

- best-effort transcript binding for each discovered role
- `mesh lite probe --project <repo>` as the operator-facing inspection path

Tasks:

1. resolve transcript candidates for the project
2. assign transcript path only when the match is provable or otherwise
   non-ambiguous
3. record known ambiguity explicitly
4. harden the binding incrementally later
5. keep `probe` aligned with the same discovery/binding logic

Primary files to use:

- `scripts/mesh_lite/jsonl.py`
  - `candidate_jsonl_paths(...)`
  - `transcript_candidates(...)`
  - `resolve_best_candidate(...)`
  - `extract_last_assistant_msg(...)`
- `scripts/mesh_lite/registry.py`
- `scripts/mesh_lite/cli.py`

Reference-only files:

- `/tmp/maniple/src/maniple_mcp/registry.py`
- `/tmp/maniple/src/maniple_mcp/idle_detection.py`
- `/tmp/maniple/src/maniple_mcp/tools/discover_workers.py`
- `/tmp/maniple/src/maniple_mcp/session_state.py`

### 5. Status

Deliverables:

- `mesh lite status --project <repo>`

Tasks:

1. show per-role session/tty/transcript binding
2. keep the output small and focused
3. avoid duplicating the purpose of `mesh sessions`

Primary files to use:

- `scripts/mesh_lite/cli.py`
- `scripts/mesh_lite/registry.py`

### 6. Relay Last

Deliverables:

- `mesh lite relay-last --project <repo> <source_role> <target_role>`

Tasks:

1. resolve source and target from the registry
2. read the latest assistant message from the source transcript
3. send that text to the target live pane
4. fail clearly if transcript binding is missing or ambiguous
5. keep the transport robust across both Python iTerm and AppleScript fallback

Primary files to use:

- `scripts/mesh_lite/cli.py`
- `scripts/mesh_lite/jsonl.py`
- `scripts/mesh_lite/iterm.py`

Reference-only files:

- `/tmp/maniple/src/maniple_mcp/iterm_utils.py`
- `/tmp/claude-code-monitor/src/utils/send-text.ts`

### 7. Send/Focus Hardening

Deliverables:

- improved safety checks for live injection

Tasks:

1. reuse current foreground-command checks
2. add better send/focus hardening if needed
3. use external references only where the current mesh mechanics are insufficient

Primary active files:

- `scripts/mesh_lite/iterm.py`
- `scripts/mesh_iterm_control.py`

Reference-only files:

- `/tmp/maniple/src/maniple_mcp/iterm_utils.py`
- `/tmp/claude-code-monitor/src/utils/send-text.ts`
- `/tmp/claude-code-monitor/src/utils/focus.ts`

### 8. Future Attached Bridge Slice

Deliverables:

- clear boundary for future PAL-style attached bridge work

Tasks:

1. keep manual relay as the stable base
2. only after that, evaluate an attached semantic runner

Reference-only files:

- `/tmp/pal-mcp-server/tools/clink.py`
- `/tmp/pal-mcp-server/clink/models.py`
- `/tmp/pal-mcp-server/clink/constants.py`
- `/tmp/pal-mcp-server/clink/agents/__init__.py`
- `/tmp/pal-mcp-server/clink/agents/base.py`
- `/tmp/pal-mcp-server/clink/registry.py`
- `/tmp/pal-mcp-server/clink/agents/claude.py`
- `/tmp/pal-mcp-server/clink/agents/codex.py`
- `/tmp/pal-mcp-server/clink/agents/gemini.py`
- `/tmp/pal-mcp-server/clink/parsers/base.py`
- `/tmp/pal-mcp-server/clink/parsers/__init__.py`
- `/tmp/pal-mcp-server/clink/parsers/claude.py`
- `/tmp/pal-mcp-server/clink/parsers/codex.py`
- `/tmp/pal-mcp-server/clink/parsers/gemini.py`

## Validation Plan

### Unit / Integration

Current unit/integration baseline:

- mesh-lite:
  - registry/jsonl/cli/iterm targeted tests passing
- router/session-worker compatibility fixes:
  - targeted worker + Claude router-hook tests passing

Full live E2E remains a separate final validation step after `T006`.

- registry persistence
- transcript parsing
- discover idempotency
- relay-last success/failure paths

### E2E

1. `mesh ui <repo>`
2. `mesh lite discover --project <repo>`
3. `mesh lite status --project <repo>`
4. generate a reply in one role pane
5. `mesh lite relay-last --project <repo> <source_role> <target_role>`
6. verify delivery in the target pane
7. `mesh ui close <repo>`

## Risks

- per-role transcript association may still be ambiguous at first
- provider transcript formats may drift
- send safety may need hardening for interactive edge cases

## Speckit Workflow Note

This spec set currently does not include the standard `.specify/` scaffolding
expected by the canonical `/speckit.analyze` command implementation. Until that
exists, artifact analysis must be performed manually or by an equivalent local
review process.

## Rollout Order

1. spec correction
2. discover over live mesh UI panes
3. transcript binding
4. status
5. relay-last
6. send/focus hardening
7. future attached bridge slice

## What We Reuse vs What We Write

### Reuse directly

- existing pane markers and lifecycle from `scripts/mesh_iterm_ui.py`
- existing live pane discovery from `scripts/mesh_iterm_control.py`
- existing role launch/provider conventions from `scripts/mesh_ui_role_shell.sh`
- existing local registry code in `scripts/mesh_lite/registry.py`
- existing transcript parser code in `scripts/mesh_lite/jsonl.py`

### Write minimally

- `discover` over live `mesh ui` panes
- best-effort per-role transcript binding
- binding-focused `status`
- registry-backed `relay-last`
- optional future metadata capture that preserves PAL compatibility
- parser/runner boundaries that preserve PAL compatibility

### Archive from the current branch

- custom `spawn-team`
- custom `list-live`
- custom mesh-lite layout/fallback spawn path
