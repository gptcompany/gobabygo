# Mesh Lite Handoff

Date: `2026-04-05`

## Scope

This handoff covers the `mesh-lite` workstream only.

Do not use the repo-root [HANDOFF.md](/Users/sam/gobabygo/HANDOFF.md) for this
slice; it documents a different router/ops thread.

## Current Outcome

At code and test level, the `mesh-lite` v1 slice is functionally complete:

- conservative live-pane discovery
- local binding registry
- binding-focused status view
- manual `relay-last`
- Python iTerm path plus AppleScript fallback
- registry error hardening
- boss notice persistence and terminal-escape sanitization
- archived artifact cleanup
- PAL bridge design slice documentation

The only remaining open item is live interactive E2E confirmation on a machine
where the iTerm2 Python API is actually reachable.

## Canonical Spec Set

Use these files as the authoritative mesh-lite docs:

- [spec.md](/Users/sam/gobabygo/specs/mesh-lite/spec.md)
- [plan.md](/Users/sam/gobabygo/specs/mesh-lite/plan.md)
- [tasks.md](/Users/sam/gobabygo/specs/mesh-lite/tasks.md)
- [bridge.md](/Users/sam/gobabygo/specs/mesh-lite/bridge.md)

Status reflected there:

- `T001`–`T010`: implemented/documented
- `T010`: script exists, but final live execution remains manual because it
  depends on the local iTerm2 Python API

## Verified State

Verified locally:

- `pytest -q tests/test_mesh_lite_iterm.py tests/test_mesh_lite_cli.py tests/test_mesh_lite_registry.py tests/test_mesh_lite_jsonl.py`
  - `39 passed`
- `pytest -q tests/router/test_session_worker.py tests/test_mesh_claude_router_hook.py`
  - `141 passed`
- `pytest -q tests/test_archived_mesh_lite_spawn_experiment.py`
  - `2 passed`
- `python3 -m py_compile scripts/mesh_lite/iterm.py`
  - ok
- `python3 -m py_compile src/router/session_worker.py`
  - ok

Also verified:

- `tests/smoke/test_mesh_lite_e2e_live.py` was corrected to avoid mutating the
  operator's real `~/.claude` and `~/.mesh-lite/registry.json`
- `specs/mesh-lite/bridge.md` was corrected so it no longer overstates PAL
  compatibility and no longer places transcript tailing in the parser layer

## Important Behavioral Boundaries

Keep these boundaries intact:

- `mesh ui` is the lifecycle owner
- `mesh term` remains the mechanical pane-control plane
- `mesh-lite` is only discover / status / probe / relay
- no alternate spawner
- no alternate attach/close lifecycle
- no alternate sessions dashboard
- no router/session-worker dependency in the critical `mesh-lite` path

## Key Runtime Guarantees

### `discover`

- preserves existing transcript binding when present
- uses `upstream_session_id` only with unique prefix match
- keeps ambiguous roles unresolved
- prunes stale roles
- removes the project from the registry when no live panes remain

### `status`

- binding-focused only
- explicitly distinguishes:
  - `bound (relay ready)`
  - `unresolved (relay disabled)`

### `relay-last`

- registry-driven only
- no implicit rediscovery
- no transcript candidate selection in the relay path
- explicit errors for:
  - missing source role
  - missing target role
  - missing transcript binding
  - no assistant reply
  - missing target session
  - unsafe target

### iTerm transport

- Python iTerm path is primary
- AppleScript fallback supports:
  - multiline payloads
  - session lookup without the Python `iterm2` package
  - empty final `tty` field in parsed rows

## Boss Notice Hardening

The boss-pane notice path was hardened and should stay that way:

- inbound notices are visible persistently in-pane
- boss badge identity is preserved
- terminal notice rendering strips:
  - ANSI / ESC 7-bit sequences
  - OSC 7-bit sequences
  - C1 CSI / OSC 8-bit sequences
  - ST-terminated C1 OSC
  - truncated / unterminated OSC tails
  - other non-printable control characters

## Current Environment Blocker

The live E2E script is ready, but the local machine is still blocked by iTerm2
environment state, not by repo code.

Observed facts:

- AppleScript can see iTerm2 windows/tabs
- the Python `iterm2` client can be imported via `uv run --with iterm2`
- the iTerm2 Python API handshake still fails or hangs
- `~/Library/Application Support/iTerm2/private/socket` is missing in the
  failing state

Practical meaning:

- code/test verification is green
- true live E2E remains blocked until iTerm2 exports a working Python API
  socket/bridge again

## Relevant Workspace State

At handoff time, these local modifications are part of the mesh-lite thread and
should be preserved:

- [bridge.md](/Users/sam/gobabygo/specs/mesh-lite/bridge.md)
- [test_mesh_lite_e2e_live.py](/Users/sam/gobabygo/tests/smoke/test_mesh_lite_e2e_live.py)

These untracked files are local operator/debug artifacts, not part of the
mesh-lite handoff contract:

- `capture_mesh_panes.py`
- `check_env.py`
- `cleanup_router.py`
- `cleanup_router_ws.py`
- `mesh_ui.log`

## Next Actions

1. Restore a working iTerm2 Python API environment on the Mac.
2. Re-run the live smoke:
   - `python3 tests/smoke/test_mesh_lite_e2e_live.py`
3. If it passes, record the outcome and close the thread cleanly.
4. If it still fails, treat it as environment/debug work first, not as a
   `mesh-lite` redesign trigger.
