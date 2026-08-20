# Tasks: Mesh Team CLI Adapter

## Objective

Implement a KISS adapter that lets Mesh use `claude-config` command and gate
policy while preserving the visible team CLI layout and controller-owned
execution.

## Conventions

- `Txxx` = implementation task
- `Status` is one of: `todo`, `in_progress`, `done`, `deferred`
- `Done when` is the acceptance gate
- MVP tasks must be unit-testable without opening iTerm

## Backlog

### T000. Create spec package

Status:

- done

Scope:

- create `spec.md`
- create `plan.md`
- create `tasks.md`
- link to the deeper mapping artifact

Likely files:

- `specs/mesh-team-cli-adapter/spec.md`
- `specs/mesh-team-cli-adapter/plan.md`
- `specs/mesh-team-cli-adapter/tasks.md`
- `specs/mesh-lite/claude-config-team-cli-map.md`

Done when:

- the scope is separate from `mesh-lite`
- MVP/non-goals are explicit
- loop policy is explicit
- implementation order is clear

### T001. Add claude-config root resolver

Status:

- done

Scope:

- resolve `claude-config` root from:
  - `--claude-config`
  - `MESH_CLAUDE_CONFIG`
  - `/Users/sam/claude-config`
  - `/media/sam/1TB/claude-config`
- fail closed when no valid root exists

Likely files:

- `scripts/mesh_iterm_control.py`
- `tests/test_mesh_iterm_control.py`

Depends on:

- `T000`

Done when:

- root resolution is pure/unit-testable
- missing root produces a clear error
- selected root is recorded in operator handoff when enabled

### T002. Add contract mapping

Status:

- done

Scope:

- map logical names to files under the resolved `claude-config` root:
  - `pipeline.speckit`
  - `speckit.analyze`
  - `speckit.implement`
  - `verify.quick`
  - `validate`
  - `confidence-gate`

Likely files:

- `scripts/mesh_iterm_control.py`
- `tests/test_mesh_iterm_control.py`

Depends on:

- `T001`

Done when:

- mappings are deterministic
- missing files are reported in a structured way
- mapping metadata can be serialized into `00-operator.json`

### T003. Add command-line flags to controller and wrapper

Status:

- done

Scope:

- add controller flags:
  - `--claude-config`
  - `--boss-contract`
  - `--president-contract`
  - `--worker-contract`
  - `--reviewer-contract`
- add wrapper passthrough/help in `scripts/mesh`

Likely files:

- `scripts/mesh_iterm_control.py`
- `scripts/mesh`
- `tests/test_mesh_iterm_control.py`

Depends on:

- `T001`
- `T002`

Done when:

- `./scripts/mesh speckit run ... --print-command` shows the expanded flags
- defaults preserve current behavior when flags are not set
- bash syntax check passes

### T004. Extract compact contract text

Status:

- done

Scope:

- read selected contract files
- extract compact prompt-safe text
- truncate deterministically when too large

Likely files:

- `scripts/mesh_iterm_control.py`
- `tests/test_mesh_iterm_control.py`

Depends on:

- `T002`

Done when:

- extraction works for markdown with YAML frontmatter
- extraction works for plain markdown
- output includes source file metadata
- output is bounded by a max character limit

### T005. Inject role contracts into prompts

Status:

- done

Scope:

- boss receives `pipeline.speckit` contract by default when enabled
- president receives `speckit.analyze`
- worker receives `speckit.implement`
- reviewer receives `verify.quick`
- Mesh-owned role restrictions are always included

Likely files:

- `scripts/mesh_iterm_control.py`
- `tests/test_mesh_iterm_control.py`

Depends on:

- `T004`

Done when:

- prompts include contract excerpt only when configured
- prompts still include markers at the start
- prompts explicitly prohibit hidden AI execution for non-worker roles
- current no-contract behavior remains supported

### T006. Add role output policy scanner

Status:

- done

Scope:

- scan role output before writing handoff JSON
- detect obvious bypass/loop/commit attempts

Forbidden for boss, president, reviewer:

- `mesh speckit run`
- `scripts/mesh`
- `gemini `
- `codex `
- `claude `
- `confidence_gate.py`
- `validation/orchestrator.py`
- `Task({`

Forbidden for worker:

- recursive `mesh speckit run`
- nested AI CLI launch
- `git commit`
- `git push`

Likely files:

- `scripts/mesh_iterm_control.py`
- `tests/test_mesh_iterm_control.py`

Depends on:

- `T005`

Done when:

- scanner returns structured findings
- policy violations can be written as JSON
- blocking violation makes the run fail
- scanner tests cover each role class

### T007. Add policy sidecar artifact

Status:

- done

Scope:

- write `.mesh/runs/<run_id>/policy-violations.json` when findings exist
- include findings in later prompts and final status

Likely files:

- `scripts/mesh_iterm_control.py`
- `tests/test_mesh_iterm_control.py`

Depends on:

- `T006`

Done when:

- sidecar path is repo-relative in output
- sidecar is not written when there are no findings
- sidecar schema is stable enough for reviewer/boss prompt input

### T008. Add quality quick mode

Status:

- done

Scope:

- add `--quality off|quick`
- default `off`
- `quick` builds deterministic quality evidence

Evidence:

- git status
- diff stat
- test status
- allowlist information
- policy scan summary

Likely files:

- `scripts/mesh_iterm_control.py`
- `scripts/mesh`
- `tests/test_mesh_iterm_control.py`

Depends on:

- `T007`

Done when:

- `quality-quick.json` is written when enabled
- JSON includes pass/fail status and reasons
- failed quality affects final status
- existing test-failure behavior is preserved

### T009. Add confidence gate command builder

Status:

- todo

Scope:

- construct the `confidence_gate.py` command safely
- include `--json`
- include `--no-iterate`
- include selected evidence files

Likely files:

- `scripts/mesh_iterm_control.py`
- `tests/test_mesh_iterm_control.py`

Depends on:

- `T001`
- `T008`

Done when:

- command construction is unit-tested
- missing script fails clearly
- MVP never adds `--evolve`
- evidence file list is deterministic

### T010. Execute confidence gate and parse JSON

Status:

- todo

Scope:

- add flags:
  - `--confidence-gate`
  - `--confidence-step`
  - `--allow-gate-failure`
  - `--gate-timeout`
- run the gate after worker/test/quality evidence exists
- write `gate-implement.json`

Likely files:

- `scripts/mesh_iterm_control.py`
- `scripts/mesh`
- `tests/test_mesh_iterm_control.py`

Depends on:

- `T009`

Done when:

- valid gate JSON is persisted
- invalid gate JSON fails closed
- `final_approved=false` fails unless allowed
- final boss/reviewer evidence includes gate status

### T011. Bind final run status to controller evidence

Status:

- todo

Scope:

- compute final status from deterministic evidence:
  - test status
  - quality status
  - policy violation status
  - gate status
- prevent boss wording from overriding controller status

Likely files:

- `scripts/mesh_iterm_control.py`
- `tests/test_mesh_iterm_control.py`

Depends on:

- `T008`
- `T010`

Done when:

- final status helper is unit-tested
- failed test/gate/policy produces non-zero exit
- report handoff records the controller decision

### T012. Low-cost live validation

Status:

- todo

Scope:

- run a local Gemini-only team test against a demo snake-game repo
- enable `--quality quick`
- keep `--confidence-gate` off for this first live pass

Depends on:

- `T011`

Done when:

- panes launch
- markers complete
- handoffs and quality sidecar are written
- controller exit status matches evidence

### T013. Claude boss live validation

Status:

- todo

Scope:

- run Claude boss, Codex president, Gemini worker, Gemini reviewer
- use contract injection
- use quality quick

Depends on:

- `T012`

Done when:

- Claude boss does not bypass controller
- president allowlist is honored
- worker edits only allowed files
- reviewer sees deterministic evidence

### T014. Confidence gate live validation

Status:

- todo

Scope:

- enable `--confidence-gate`
- run against a small demo repo
- inspect `gate-implement.json`

Depends on:

- `T013`

Done when:

- gate runs only from controller
- gate sidecar is valid JSON
- failed approval blocks final status
- allowed failure flag works when explicitly provided

### T015. Controlled iteration

Status:

- deferred

Scope:

- add controller-owned finite retry
- default `max_iterations=1`
- allow `max_iterations=2` for explicit test runs

Depends on:

- `T014`

Done when:

- iteration state is persisted
- stop reason is explicit
- progress-stalled behavior exists or is intentionally deferred
- reviewer/gate can recommend iteration without owning the loop
