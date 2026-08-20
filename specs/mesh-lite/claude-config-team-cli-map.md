# Claude Config Team CLI Adapter Map

This document maps the current GobabyGo Mesh/iTerm control plane to the
`claude-config` command, skill, hook, validation, and confidence-gate assets.
The goal is to reuse the existing Claude configuration as an operational source
of truth for a team of visible CLI roles without letting any single CLI bypass
the controller.

## Current State

### Mesh control plane

The current local path is already a controlled team run, not just a smoke test:

- `scripts/mesh speckit run <repo> --feature ...` is a KISS wrapper around the
  iTerm controller.
- `scripts/mesh_iterm_control.py` is the mechanical controller for pane launch,
  marker waits, prompt injection, handoff files, auto-approval, test execution,
  and cleanup.
- `--team local-codex-gemini` runs Codex as boss/president and Gemini as worker.
- `--team local-gemini` runs all visible roles through Gemini for cheaper tests.
- `--team claude-codex-gemini` exists for the target shape: Claude boss, Codex
  president, Gemini worker.
- `--with-reviewer` adds a visible reviewer role.
- `--auto` expands to bounded write mode, known prompt auto-approval, one max
  turn, and `./test.sh` when executable.
- `--allow-edit <path>` constrains auto-approved worker edit prompts.
- President output can narrow edits via `ALLOWED_EDIT_PATHS: ...`.
- Non-worker edit prompts are rejected by the controller.
- Handoff JSON files are written under `.mesh/runs/<run_id>/`.
- Test failure returns non-zero unless explicitly allowed.

Important current limitation: the visible reviewer is an AI audit prompt, but
there is not yet a first-class quality/confidence gate artifact integrated into
the controller result.

### Claude config copies

Observed copies:

- Mac: `/Users/sam/claude-config`, branch `main`, commit `873d63e`, clean.
- Workstation: `/media/sam/1TB/claude-config`, branch `main`, commit `e24368b`,
  with runtime dirtiness in `.claude-flow/daemon-state.json`.

The Mac copy is newer by Git history and contains a richer validation tree. The
adapter must still keep the root configurable because the workstation can become
the operational source of truth again.

Recommended config precedence:

1. explicit `--claude-config <path>`
2. `MESH_CLAUDE_CONFIG`
3. `/Users/sam/claude-config` on macOS
4. `/media/sam/1TB/claude-config` on Linux workstation
5. fail closed with a clear message

## Asset Taxonomy

### Command contracts

These should be read as role instructions/contracts, not copied into Mesh:

- `commands/pipeline.speckit.md`
- `commands/speckit.specify.md`
- `commands/speckit.clarify.md`
- `commands/speckit.plan.md`
- `commands/speckit.tasks.md`
- `commands/speckit.analyze.md`
- `commands/speckit.implement.md`
- `commands/verify/quick.md`
- `commands/verify/loop.md`
- `commands/gsd/*.md`

Use in Mesh:

- Boss receives a constrained excerpt from `pipeline.speckit.md` or the selected
  command.
- President receives planning/analyze/task-quality contracts.
- Worker receives only the implementation contract relevant to the assigned
  bounded task.
- Reviewer receives verify/audit contracts.

Do not execute these command files directly from boss output.

### Role profiles

Useful `claude-config` agents:

- `agents/gsd-plan-checker.md` maps to president/reviewer plan validation.
- `agents/gsd-executor.md` maps to worker behavior, but its autonomous commit
  behavior must be disabled in Mesh runs.
- `agents/gsd-verifier.md` maps well to reviewer: verify real outcome, not
  claimed task completion.
- `agents/architecture-validator.md` maps to optional reviewer specialization.

Use as prompt material and role policy. Do not import their tool permissions as
runtime permissions.

### Headless gates

These can run without a visible pane if they produce bounded, persisted output:

- `scripts/confidence_gate.py`
- `scripts/confidence-gate.sh`
- `skills/confidence-gate/SKILL.md`
- `skills/validate/SKILL.md`
- `templates/validation/orchestrator.py`

Use in Mesh:

- run only from the controller, never from boss/president free-form output
- write JSON output to `.mesh/runs/<run_id>/gate-*.json`
- use `--json`
- use `--no-iterate` for the first KISS integration
- fail the Mesh run when `final_approved=false`, unless explicitly allowed

Important caveat: `confidence_gate.py` currently hardcodes
`~/.claude/config/confidence_gate.json` as its config path. The adapter should
either call the installed `~/.claude` version or add a controlled environment
override before relying on a repo-local config file.

### Claude-specific hooks

Useful but not directly portable:

- `scripts/hooks/quality/plan-validator.js`
- `scripts/hooks/quality/pr-readiness.js`
- `scripts/hooks/quality/validation-orchestrator.js`
- `scripts/hooks/quality/confidence-gate-sync.js`
- `scripts/hooks/quality/architecture-validator.js`

These scripts assume Claude Code hook input, `CLAUDE_*` context, and
Claude-specific lifecycle events. Mesh should adapt their logic into explicit
controller events instead of running them as opaque hooks.

Suggested Mesh events:

- `pre_boss`
- `post_boss`
- `post_president`
- `post_worker`
- `post_test`
- `post_reviewer`
- `post_gate`
- `post_run`

### Heavy templates

The validation template tree is useful as a future capability catalog:

- API contract validation
- visual regression
- accessibility
- security
- performance
- confidence loop
- behavior validators

Do not wire the whole tree into the first Mesh adapter. It is too broad for the
current snake-game/team-CLI validation goal.

## Target Runtime Boundary

The core safety boundary is:

```text
Claude boss decides.
Mesh controller executes.
President plans and scopes.
Worker implements.
Reviewer audits.
Headless gates verify.
```

Visible roles may reason and report. The controller is the only component that
may launch panes, call tests, call quality gates, close layouts, or decide final
exit status.

## Role Policy

### Boss: `decision_only`

Allowed:

- read operator request
- choose command/pipeline contract
- delegate to president
- summarize final outcome

Forbidden:

- edit files
- run shell commands
- launch `gemini`, `codex`, `claude`, or `mesh`
- run confidence gate or validation directly
- recursively start another Mesh run
- commit or push

Expected output:

- concise routing decision
- structured intent when requested
- final marker

### President: `planning_only`

Allowed:

- analyze the boss request
- define one bounded worker task
- set `ALLOWED_EDIT_PATHS`
- adjudicate worker result based on controller evidence

Forbidden:

- edit files
- run implementation
- launch external AI CLIs
- bypass the worker pane
- approve work without considering test/gate evidence

Expected output:

- one worker assignment
- allowlist
- readiness verdict
- final marker

### Worker: `implementation`

Allowed:

- edit only inside effective allowlist when write mode is enabled
- run narrow local commands if the CLI asks and controller allows
- summarize changed files, tests, and risks

Forbidden:

- delegate to another AI
- launch `gemini`, `codex`, `claude`, or recursive `mesh`
- edit outside allowlist
- commit or push

Expected output:

- changed files
- tests run or skipped
- residual risks
- final marker

### Reviewer: `audit_only`

Allowed:

- inspect handoff evidence supplied by controller
- inspect git status/diff summaries supplied by controller
- classify result as ready, blocked, iterate, or escalate

Forbidden:

- edit files
- run implementation
- launch external AI CLIs
- approve work when deterministic controller checks failed

Expected output:

- independent verdict
- blockers and evidence
- final marker

## Loop And Bypass Risks

Primary risk: Claude boss has native access to programmable commands and tools.
If allowed to execute freely, it can trigger nested AI calls or recursive Mesh
runs while the controller believes it is only collecting a decision.

Failure modes to block:

- boss runs `mesh speckit run`
- boss runs `gemini`, `codex`, or `claude`
- boss runs `confidence_gate.py` directly
- worker spawns a second AI CLI instead of implementing
- reviewer edits code
- confidence gate uses evolve mode and loops
- gate/reviewer says iterate repeatedly without a hard controller limit
- worker reports done with empty diff when write mode required changes
- final boss report claims success despite failed test or gate

### Useful loops vs unsafe loops

Loops are not bad by default. `claude-config` already uses finite loops in a few
places:

- `templates/validation/ralph_loop.py` models validation as a state machine with
  `max_iterations`, thresholds, blockers, and a final state.
- `templates/validation/validators/confidence_loop/termination.py` stops on
  threshold reached, progress stalled, or max iterations.
- `scripts/confidence_gate.py` supports `--evolve --max-iterations`, but the
  callback is optional and the first Mesh integration should not use evolve.
- `commands/speckit.autofix.md` and `commands/speckit.specify.md` describe
  bounded fix/validation iterations.

The rule for Mesh is: loops are controller-owned, bounded, observable, and
persisted. Role-owned free-form loops are unsafe.

Allowed loop shape:

```text
controller iteration 1
  worker pass
  deterministic checks
  reviewer/gate verdict

if verdict == iterate and iteration < max_iterations:
  controller creates iteration handoff
  worker pass with reviewer feedback
else:
  controller stops with approved/blocked/escalate
```

Forbidden loop shape:

```text
boss decides to run another mesh command
worker launches another AI CLI
confidence gate launches evolve without controller callback
reviewer keeps asking for retry without a hard stop
```

Initial Mesh defaults:

- `max_iterations = 1`
- `max_gate_calls = 1`
- `max_turns = 1`
- confidence gate called with `--no-iterate`
- reviewer may recommend `ITERATE`, but the controller still stops unless
  `--max-iterations` is explicitly greater than `1`

Later controlled iteration defaults:

- `max_iterations = 2`
- stop reasons: `approved`, `test_failed`, `gate_failed`, `policy_violation`,
  `progress_stalled`, `max_iterations`, `human_review`
- each iteration writes sidecars:
  - `iteration-01-quality.json`
  - `iteration-01-gate-implement.json`
  - `iteration-01-verdict.json`

Controller guardrails:

- hard `max_turns` per role
- hard `max_iterations`, default `1`
- hard `max_gate_calls`, default `1`
- forbid recursive Mesh commands in role outputs
- forbid direct AI CLI invocations in boss/president/reviewer outputs
- auto-reject non-worker edit prompts
- fail if test failed unless `--allow-test-failure`
- fail if gate failed unless `--allow-gate-failure`
- never let role text decide process exit status

## KISS Implementation Plan

### Phase 1: Read-only adapter manifest

Add a small resolver to `scripts/mesh_iterm_control.py`:

- resolve `--claude-config <path>` or `MESH_CLAUDE_CONFIG`
- validate required files exist
- map known names to paths:
  - `pipeline.speckit`
  - `speckit.analyze`
  - `speckit.implement`
  - `verify.quick`
  - `validate`
  - `confidence-gate`
- expose resolved metadata in `00-operator.json`

No external AI calls in this phase.

### Phase 2: Contract injection

Add optional arguments:

- `--claude-config <path>`
- `--boss-contract pipeline.speckit`
- `--president-contract speckit.analyze`
- `--worker-contract speckit.implement`
- `--reviewer-contract verify.quick`

The controller reads short excerpts from command files and injects them into the
right role prompts as policy context. It must not paste huge files into every
prompt.

Minimum excerpt rule:

- title/description
- objective/process summary
- explicit constraints
- forbidden execution notes added by Mesh

### Phase 3: Anti-loop scan

Add a deterministic role-output scan before writing each handoff:

Forbidden patterns for boss, president, reviewer:

- `gemini `
- `codex `
- `claude `
- `mesh speckit run`
- `scripts/mesh`
- `confidence_gate.py`
- `validation/orchestrator.py`
- `Task({`

Forbidden patterns for worker:

- `mesh speckit run`
- nested `claude`, `codex`, or `gemini` invocations
- `git commit`
- `git push`

First version can be simple substring/regex matching with allowlist comments
for false positives later.

### Phase 4: Quality quick gate

Add:

- `--quality off|quick`
- default `off` initially
- `quick` means controller runs deterministic checks after worker:
  - git status
  - diff stat
  - optional test command
  - allowlist diff check
  - no forbidden output findings

Write sidecar:

```text
.mesh/runs/<run_id>/quality-quick.json
```

This should not renumber existing handoff files.

### Phase 5: Confidence gate integration

Add:

- `--confidence-gate`
- `--confidence-step implement`
- `--allow-gate-failure`
- `--gate-timeout <seconds>`
- `--gate-iterate` later, disabled by default

Controller calls:

```bash
python3 <claude-config>/scripts/confidence_gate.py \
  --step implement \
  --files <handoff files and optional evidence file> \
  --json \
  --no-iterate
```

Write sidecar:

```text
.mesh/runs/<run_id>/gate-implement.json
```

KISS rule: no `--evolve` in controller v1.

### Phase 6: Controlled iteration

Add optional finite retry support only after Phase 5 is stable:

- `--max-iterations N`
- default remains `1`
- controller may run one additional worker pass only when:
  - test or gate produced concrete feedback
  - no policy violation occurred
  - previous iteration changed the repo or produced a verifiable plan
  - iteration count is below the hard max

This is where Ralph-style behavior belongs: the controller owns the loop state,
not Claude boss or a hidden script.

### Phase 7: Final report binding

Final boss prompt must receive deterministic evidence:

- test status
- quality status
- confidence gate status
- reviewer verdict
- controller-blocked reasons

The final run exit code comes from the controller, not from boss wording.

## Proposed CLI Shape

KISS user-facing command:

```bash
./scripts/mesh speckit run /tmp/snake-game \
  --team claude-codex-gemini \
  --feature "add score counter" \
  --task "edit only index.html and snake.js" \
  --auto \
  --allow-edit index.html \
  --allow-edit snake.js \
  --with-reviewer \
  --quality quick \
  --confidence-gate
```

Low-cost test command:

```bash
./scripts/mesh speckit run /tmp/snake-game \
  --team local-gemini \
  --feature "add score counter" \
  --auto \
  --with-reviewer \
  --quality quick
```

## Implementation Files

First-code touch points:

- `scripts/mesh_iterm_control.py`
  - adapter path resolution
  - contract loading
  - forbidden output scan
  - quality sidecar
  - confidence gate sidecar
  - exit status binding

- `scripts/mesh`
  - wrapper flags for `--claude-config`, `--quality`, `--confidence-gate`,
    `--allow-gate-failure`

- `tests/test_mesh_iterm_control.py`
  - resolver tests
  - forbidden output scan tests
  - quality JSON tests
  - confidence gate command construction tests

- `tests/test_mesh_ui_script.py`
  - only if bootstrap prompt policy changes

Do not touch router/session-worker code for the first adapter. The iTerm
controller path is enough for local KISS validation.

## Success Criteria

The first implementation is good enough when:

- the adapter can locate `claude-config` and record source metadata
- boss/president/worker/reviewer prompts can include mapped contracts
- role output scan blocks obvious recursive/AI-bypass attempts
- `--quality quick` creates a sidecar artifact and affects final status
- optional `--confidence-gate` creates a JSON sidecar and affects final status
- all new behavior is covered by unit tests without opening iTerm
- live snake-game test remains a single-machine flow
