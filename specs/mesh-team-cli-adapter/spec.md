# Spec: Mesh Team CLI Adapter

## Summary

Mesh Team CLI Adapter makes `claude-config` operational inside the existing
GobabyGo iTerm team runtime.

The adapter does not replace `mesh ui`, `mesh term`, or the current
`mesh speckit run` controller. It adds a bounded policy layer so that:

- Claude can act as boss because it is the most programmable CLI
- Codex/Gemini can act as president, worker, or reviewer
- `claude-config` remains the source for command, validation, and gate policy
- Mesh remains the only executor for panes, tests, gates, iterations, and final
  status

The main design rule is:

```text
Claude decides. Mesh executes.
```

## Problem

The current Mesh/iTerm path can already run a controlled local team cycle:

- launch visible CLI roles
- send prompts to boss, president, worker, and reviewer
- wait for markers
- write handoff JSON
- constrain worker edits through allowlists
- reject non-worker edit prompts
- run an optional test command
- close or keep the iTerm layout

What is missing is a safe way to reuse the richer `claude-config` assets:

- Speckit and GSD commands
- validation and quality hooks
- confidence gate scripts
- Ralph-style bounded validation loops
- role profiles such as planner, executor, verifier, and architecture validator

Those assets are useful, but many assume Claude Code can directly call tools,
spawn agents, invoke AI CLIs, or run hooks. If imported blindly, Claude boss may
bypass the visible team layout and create unbounded or hidden loops.

## Goal

Use `claude-config` as the source of command and gate policy while keeping Mesh
as the single mechanical executor.

The operator should be able to run:

```bash
./scripts/mesh speckit run /tmp/snake-game \
  --team claude-codex-gemini \
  --feature "add score counter" \
  --auto \
  --allow-edit index.html \
  --allow-edit snake.js \
  --with-reviewer \
  --quality quick \
  --confidence-gate
```

and get:

- visible role panes
- bounded role turns
- persistent handoff artifacts
- deterministic quality artifacts
- optional confidence gate artifacts
- controller-owned exit status
- no hidden worker AI launched by Claude boss
- no unbounded loops

## Non-Goals

MVP does not:

- migrate all Claude hooks
- execute arbitrary `claude-config` hooks as-is
- run the full validation template tree
- allow Claude boss to spawn Gemini/Codex directly
- allow recursive `mesh speckit run`
- allow commit or push automation
- enable `confidence_gate.py --evolve`
- implement distributed multi-machine orchestration
- replace router/session-worker templates
- replace `mesh-lite`

## Primary User Story

As an operator, I want to run a local team of CLI agents where Claude is boss,
Codex/Gemini are subordinate roles, and Mesh applies contracts, gates, edit
allowlists, and loop limits, so that the team can work on a repo without hidden
AI execution or runaway iterations.

## Secondary User Stories

### Contract-backed role prompts

As an operator, I want Mesh to inject relevant `claude-config` command contracts
into the right role prompts, so that the team follows existing Speckit/GSD
policy without duplicating those files in GobabyGo.

### Reviewer with quality evidence

As an operator, I want the reviewer to receive deterministic evidence from Mesh,
so that reviewer approval is grounded in tests, diff status, allowlist policy,
and gate results rather than self-reported worker output.

### Bounded iteration

As an operator, I want loops to be allowed only when controller-owned and capped,
so that Ralph-style refinement is possible without accidental infinite retries.

## Runtime Boundary

### Mesh controller

Mesh is the only component allowed to:

- launch iTerm panes
- send prompts to roles
- approve or reject CLI prompts
- run tests
- run validation or confidence gates
- write handoff and sidecar artifacts
- start a retry iteration
- decide process exit status
- close the layout

### Claude boss

Claude boss may:

- interpret the operator request
- choose the workflow contract
- delegate to president
- summarize final evidence

Claude boss must not:

- edit files
- run shell commands
- launch `gemini`, `codex`, `claude`, or `mesh`
- run validation or confidence gate scripts
- recursively start a Mesh run
- commit or push

### President

President may:

- translate the boss request into one scoped worker task
- set `ALLOWED_EDIT_PATHS`
- adjudicate worker results from controller evidence

President must not:

- edit files
- implement directly
- launch external AI CLIs
- bypass the worker pane
- approve work when controller evidence failed

### Worker

Worker may:

- implement one bounded task
- edit only inside effective allowlist when writes are allowed
- summarize changed files, tests, and risks

Worker must not:

- delegate to another AI CLI
- launch `claude`, `codex`, `gemini`, or recursive `mesh`
- edit outside allowlist
- commit or push

### Reviewer

Reviewer may:

- audit controller evidence
- classify the result as ready, blocked, iterate, or escalate
- call out policy violations and missing proof

Reviewer must not:

- edit files
- implement fixes
- launch external AI CLIs
- override deterministic controller failures

## Claude Config Source

The adapter resolves `claude-config` using this precedence:

1. `--claude-config <path>`
2. `MESH_CLAUDE_CONFIG`
3. `/Users/sam/claude-config`
4. `/media/sam/1TB/claude-config`
5. fail closed

The adapter records the resolved root and selected source files in
`00-operator.json`.

## Asset Classes

### Command contracts

Files such as `commands/pipeline.speckit.md`, `commands/speckit.analyze.md`,
and `commands/speckit.implement.md` are read as contracts and excerpted into
role prompts.

They are not executed directly by role output.

### Role profiles

Files such as `agents/gsd-plan-checker.md`, `agents/gsd-executor.md`, and
`agents/gsd-verifier.md` are used as role-policy references.

Their tool permissions are not imported as runtime permissions.

### Headless gates

Files such as `scripts/confidence_gate.py` and `skills/validate/SKILL.md` may be
executed by the controller when explicitly enabled.

They must write structured sidecar artifacts.

### Claude-specific hooks

Files under `scripts/hooks/quality/` are not executed as Claude hooks in MVP.
Their logic can later be adapted into Mesh controller events.

## Artifacts

Existing handoff artifacts remain:

```text
.mesh/runs/<run_id>/00-operator.json
.mesh/runs/<run_id>/01-discuss.json
.mesh/runs/<run_id>/02-analyze.json
.mesh/runs/<run_id>/03-implement.json
.mesh/runs/<run_id>/04-verify.json
.mesh/runs/<run_id>/05-reviewer.json
.mesh/runs/<run_id>/06-report.json
```

New sidecar artifacts do not renumber existing handoffs:

```text
.mesh/runs/<run_id>/quality-quick.json
.mesh/runs/<run_id>/gate-implement.json
.mesh/runs/<run_id>/policy-violations.json
```

Later controlled iterations may write:

```text
.mesh/runs/<run_id>/iteration-01-quality.json
.mesh/runs/<run_id>/iteration-01-gate-implement.json
.mesh/runs/<run_id>/iteration-01-verdict.json
```

## Loop Policy

Loops are allowed only when controller-owned, bounded, observable, and
persisted.

MVP defaults:

- `max_turns = 1`
- `max_iterations = 1`
- `max_gate_calls = 1`
- confidence gate uses `--no-iterate`
- `--evolve` is not used

Allowed future loop:

```text
controller iteration
  worker pass
  deterministic checks
  reviewer/gate verdict
  stop or retry based on max_iterations
```

Forbidden loop:

```text
boss runs mesh again
worker spawns another AI CLI
reviewer asks for retries forever
confidence gate evolves without controller state
```

Stop reasons must be explicit:

- `approved`
- `test_failed`
- `gate_failed`
- `policy_violation`
- `progress_stalled`
- `max_iterations`
- `human_review`

## Policy Violations

The controller must detect obvious bypass attempts in role output.

Examples:

- `mesh speckit run`
- `scripts/mesh`
- `gemini `
- `codex `
- `claude `
- `confidence_gate.py`
- `validation/orchestrator.py`
- `Task({`
- `git commit`
- `git push`

MVP may use conservative substring or regex scans. False positives can later be
handled through structured allow rules.

## Quality Quick

`--quality quick` creates a deterministic controller sidecar from:

- git status
- diff stat
- test command status
- allowlist checks
- role output policy scan

It does not require external AI.

## Confidence Gate

`--confidence-gate` calls `confidence_gate.py` only from the controller.

MVP call shape:

```bash
python3 <claude-config>/scripts/confidence_gate.py \
  --step implement \
  --files <evidence files> \
  --json \
  --no-iterate
```

The controller fails the run when `final_approved=false`, unless
`--allow-gate-failure` is provided.

## Success Criteria

MVP is complete when:

- Mesh resolves `claude-config` and records source metadata
- selected command contracts can be injected into role prompts
- anti-loop scan blocks obvious bypass attempts
- `--quality quick` writes JSON and affects exit status
- `--confidence-gate` writes JSON and affects exit status
- all logic is unit-tested without iTerm
- live snake-game flow remains single-machine and visible

## References

- `specs/mesh-lite/claude-config-team-cli-map.md`
- `scripts/mesh`
- `scripts/mesh_iterm_control.py`
- `/Users/sam/claude-config/commands/pipeline.speckit.md`
- `/Users/sam/claude-config/scripts/confidence_gate.py`
- `/Users/sam/claude-config/templates/validation/ralph_loop.py`
- `/Users/sam/claude-config/templates/validation/validators/confidence_loop/termination.py`
