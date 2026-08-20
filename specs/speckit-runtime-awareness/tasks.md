# Tasks: Spec Kit Runtime Awareness

## Objective

Keep the official Spec Kit CLI and project integrations aligned while making
the persistent Mesh coordinator and its delegated workers aware of the exact
installed capabilities. Spec Kit owns specification artifacts; Mesh Live keeps
ownership of tmux delivery, provider routing, rate-limit handling, and
independent review.

## Constraints

- Active providers are exactly Claude, Codex, and Antigravity (`agy`).
- Gemini is historical input only and cannot be selected for new work.
- `tasks.md` remains the task source of truth; GitHub Issues are opt-in output.
- Update discovery is automatic and read-only. Installation and project
  upgrades require an explicit `--apply` and a reviewable diff.
- Release notes and other remote prose are never injected into prompts.
- No Spec Kit workflow command may launch nested AI CLIs from the coordinator.
- Existing live worker panes are not mutated by tests.
- Each implementation task is committed separately after its acceptance gate.

## Backlog

### T000. Freeze and review the implementation backlog

Status: done

Scope:

- record the implementation boundary and source-of-truth rules
- identify exact files, dependencies, tests, and rollback boundaries
- review the backlog before implementation starts

Done when:

- every task has a bounded acceptance gate
- automatic checks and explicit mutations are separated
- E2E coverage includes Mac-local and Dell-canary paths
- the reviewed backlog is committed without staging unrelated worktree changes

### T001. Add a pinned Spec Kit runtime model

Status: done

Depends on: T000

Likely files:

- `config/speckit.lock.json`
- `scripts/mesh_speckit_cli.py`
- `tests/test_mesh_speckit_cli.py`

Scope:

- pin one exact official Spec Kit release and the integrations `claude`,
  `codex`, and `agy`
- implement `status`, `capabilities`, and read-only `update-check`
- derive installed capabilities from actual project-local skills and manifests
- store update-check state only under the operator state directory
- parse only allowlisted structured release metadata

Done when:

- status distinguishes required, installed, latest-known, and project state
- a missing `specify` binary is reported without crashing
- no network is used by `status` or `capabilities`
- `update-check` cannot modify the CLI, project, lock file, or Git state
- unit tests cover absent, aligned, outdated, malformed, and partial projects

### T002. Expose lifecycle commands through `mesh speckit`

Status: done

Depends on: T001

Likely files:

- `scripts/mesh`
- `tests/test_mesh_speckit_shell.py`

Scope:

- route `status`, `capabilities`, and `update-check` to the Python helper
- retain `run` and `smoke` as deprecated legacy iTerm2 entry points
- keep help and exit-code behavior deterministic

Done when:

- management commands never import or require iTerm2
- legacy commands remain callable during migration
- Bash and Zsh syntax checks pass
- wrapper tests verify exact argument forwarding

### T003. Add guarded CLI and project upgrades

Status: done

Depends on: T001, T002

Likely files:

- `scripts/mesh_speckit_cli.py`
- `scripts/mesh`
- `tests/test_mesh_speckit_cli.py`
- `tests/test_mesh_speckit_shell.py`

Scope:

- add explicit-version `install` and `project init|upgrade` commands
- make non-`--apply` execution a mutation-free plan
- refuse dirty target repositories unless an explicit future policy permits it
- invoke only official `specify` integration commands for Claude, Codex, AGY
- require explicit `--allow-multi-install-force` before passing the upstream
  `--force` needed to install AGY beside another integration in v0.16.5
- show resulting Git status and changed paths without committing them

Done when:

- no command accepts implicit `latest` for installation
- `--apply` is mandatory for every filesystem or tool installation mutation;
  AGY multi-install additionally requires `--allow-multi-install-force`
- unsupported integrations and Gemini are rejected
- failures preserve the previous lock and report partial project changes
- subprocess tests use fakes and never install tools on the test host

### T004. Make coordinator startup and resume capability-aware

Status: done

Depends on: T001, T002

Likely files:

- `scripts/mesh_live_cli.py`
- `scripts/mesh_live_shell_helpers.sh`
- `tests/test_mesh_live_cli.py`
- `tests/test_mesh_live_shell_helpers.py`

Scope:

- append a bounded structured Spec Kit status block to the coordinator contract
- collect repository status on the Dell through one exact read-only SSH command;
  never inspect a Dell path with the Mac filesystem
- use the repository project state for intra-repo coordination
- use the dedicated coordination repository for multi-repo coordination
- regenerate the block on new, continue, and exact UUID resume paths
- fail closed on malformed capability output without blocking direct workflow

Done when:

- the prompt contains version, alignment, enabled commands, and update status
- no remote prose, release body, or unbounded command output enters the prompt
- a Spec Kit mismatch prevents claiming unavailable phases but does not disable
  direct board, peek, review, or incident coordination
- resume tests prove the new contract is appended to the resumed conversation

### T005. Add phase-aware worker delegation context

Status: done

Depends on: T004

Likely files:

- `scripts/mesh_live_cli.py`
- `tests/test_mesh_live_cli.py`
- `MESH_LIVE.md`

Scope:

- define a compact delegation envelope containing version, phase, feature
  directory, allowed artifacts, and immutable review scope
- expose the envelope through a deterministic `mesh speckit context` command
- require the coordinator to derive it from the target repository state
- send only task-relevant capabilities, never the full capability inventory
- preserve tracked-send receipts and existing provider guards

Done when:

- delegation context is bounded and contains no captured pane text
- unsupported phases are rejected before worker input
- Codex and AGY receive the same artifact identity and version contract
- reviewer context remains read-only and different-provider policy is explicit

### T006. Retire Gemini from active Spec Kit paths

Status: done

Depends on: T002

Likely files:

- `scripts/mesh`
- `scripts/mesh_iterm_control.py`
- `mapping/pipeline_templates.yaml`
- focused tests and documentation

Scope:

- replace active team defaults with Claude, Codex, and Antigravity
- reject Gemini for new runs, workers, reviewers, and fallback chains
- preserve only bounded historical deserialization where required
- avoid changing unrelated dirty iTerm2 work in the current worktree

Done when:

- help and defaults expose no active Gemini lane
- built-in active templates contain only Claude, Codex, and Antigravity
- historical fixtures remain readable but cannot create work
- focused template and wrapper tests pass

### T007. Persist multi-repo Spec Kit state in a Git repository

Status: done

Depends on: T003, T004

Likely files:

- `scripts/mesh_live_shell_helpers.sh`
- `MESH_LIVE.md`
- `QUICKSTART.md`
- shell-helper tests

Scope:

- introduce `MESH_COORDINATOR_STATE_REPO`
- default Dell multi-repo coordination to `/data/sata/1TB/coordination`
- refuse a missing or non-Git state repository rather than falling back to the
  unversioned repository base
- initialize the real Dell repository only during the approved deployment step

Done when:

- `mcoordinator --all` has one deterministic Git working directory
- intra-repo coordinator behavior is unchanged
- typo or missing state repo fails before tmux creation or attachment
- tests cover new, continue, and resume paths

### T008. Add read-only automatic update awareness

Status: done

Depends on: T001, T004

Likely files:

- `scripts/install-speckit-update-check.sh`
- `scripts/mesh_live_cli.py`
- `MESH_LIVE.md`
- focused tests

Scope:

- install a daily metadata-only update check
- include update state in a normal coordinator tick wake, without creating a
  wake whose only purpose is announcing an update
- never message worker panes merely because an update exists
- never auto-install or auto-upgrade projects

Done when:

- repeated checks are idempotent
- state contains no release body, prompt text, pane capture, or credentials
- occupied coordinators and all workers receive no unsolicited input
- cron installation is explicit and syntax-tested

### T008H. Chain global and repository pre-push hooks

Status: done

Depends on: T000

Scope:

- keep one global `core.hooksPath` and never require per-repository overrides
- run the existing global review first, then an executable repository
  `.githooks/pre-push` when present
- replay exact Git hook arguments and stdin to both checks
- retain CI as the authoritative gate; local hooks are fast feedback only
- install explicitly and refuse replacement of unknown global hook content

Done when:

- either hook failure blocks the push and repository hooks cannot bypass global review
- repositories without a local hook keep the current global behavior
- tests cover stdin replay, arguments, failure ordering, and safe installation
- Dell installation is validated without performing a push

### T009. Run local and Dell-canary E2E validation

Status: todo

Depends on: T001-T008

Scope:

- run the complete focused unit/integration suite locally
- install the pinned official CLI on the Dell with explicit apply
- create an isolated Git canary repository outside operational repositories
- initialize Claude, Codex, and AGY integrations in the canary
- verify status and capability discovery against the real installed files
- generate new and resume coordinator contracts and inspect their bounded status
- generate worker delegation envelopes for implementation and review phases
- run update-check twice and verify idempotency
- inspect existing live sessions read-only to confirm no worker was mutated

Done when:

- official CLI version matches the lock
- all three project integrations are detected
- coordinator and worker contexts use only installed capabilities
- no operational repository, worker composer, or tmux session is modified
- command output, test logs, and rollback instructions are recorded

### T010. Documentation, final review, deployment, and rollout

Status: todo

Depends on: T009

Likely files:

- `README.md`
- `QUICKSTART.md`
- `MESH_LIVE.md`
- documentation tests

Scope:

- document operator commands and source-of-truth boundaries concisely
- deploy the reviewed commits to the immutable Dell runtime
- run final static review and focused regression suite
- push only after local and runtime revisions match

Done when:

- docs match CLI help and actual Dell behavior
- every implementation task has its own commit
- runtime checkout is clean and at the reviewed revision
- final review reports no unresolved high or medium findings

## Plan Review

Verdict: approved for incremental implementation after the following corrections.

### R001. Local/remote path confusion

Severity: high

The Mac helper creates the coordinator contract, while project skill files live
on the Dell. Reading `repo_root` locally would report false missing capabilities.
T004 now requires one bounded read-only SSH status command executed against the
Dell and treats its JSON as untrusted structured input.

### R002. Competing orchestration engines

Severity: high

Running the official Spec Kit workflow engine from the coordinator could launch
nested AI CLIs outside persistent tmux ownership. The backlog uses official
project artifacts and integration commands but leaves worker execution with
Mesh Live. Nested workflow execution remains explicitly out of scope.

### R003. Unsolicited update wakeups

Severity: medium

Waking a coordinator solely for a version notice adds input races without
advancing operator work. T008 now records updates automatically but only adds
the state to an otherwise justified coordinator wake or a new/resumed contract.

### R004. Free-form worker capability claims

Severity: medium

A prompt-only convention could claim phases absent from the target project.
T005 now requires a deterministic context command backed by actual installed
project skills; unsupported phases fail before worker input.

### R005. Generated state in Git

Severity: medium

The latest available release is dynamic and must not churn the repository.
Only the required version is committed. Latest-known state remains under the
operator state directory and is never a source of executable instructions.
