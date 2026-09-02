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

Status: done

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

Status: done

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

### T011. Migrate pre-manifest Spec Kit repositories conservatively

Status: done

Depends on: T010

Scope:

- distinguish current legacy artifacts from a genuinely missing integration
- generate pinned Claude, Codex, and AGY output in a temporary sandbox
- report additions, generated updates, preserved constitution, and collisions
- require a clean exact Git root, stable HEAD, and explicit update acceptance
- apply files atomically and roll back files touched by a failed migration
- validate project manifest version as part of aligned status
- discover `~/.local/bin/specify` in non-interactive SSH sessions
- exercise a legacy canary on the Dell without mutating operational repositories

Done when:

- legacy `specs/`, constitution, and custom commands remain unchanged
- official generated templates change only after explicit acceptance
- all three provider integrations expose the same common capabilities
- runtime drift, concurrent repo changes, and skill collisions fail closed
- migration behavior and residual provider smoke gaps are recorded

### T012. Harden project migration after external review

Status: done

Depends on: T011

Scope:

- make init/migrate rollback durable across filesystem failures and signals
- serialize apply and upgrade operations per repository
- bind plans to generated content while allowing only validated timestamp drift
- reject source/target symlink escapes and ignored generated output
- recognize historical layouts without classifying one generic command as Spec Kit
- report retained commands and ambiguous dual constitutions explicitly
- normalize process, filesystem, and project-inspection failures
- rerun local and Dell canaries and independent read-only review

Done when:

- no unresolved high or medium review findings remain
- local and Dell focused suites pass
- a real historical Dell canary reaches aligned state without content loss
- final reviewer checkout and control checkout remain clean

### T013. Freeze and review the Spec Kit 1.0.3 upgrade backlog

Status: done

Depends on: T012

Scope:

- verify the latest stable release and upgrade contract from official sources
- inventory the Mac and Dell CLI installations and every intended repository
- separate CLI installation, manifest-aware integration upgrade, and legacy migration
- define per-task rollback boundaries and a finite review stop condition
- make no CLI, project, worker, tmux, or repository mutation during discovery

Done when:

- the target is the exact official tag `v1.0.3`, never a moving `latest` reference
- the backlog reuses `specify integration upgrade` and existing Mesh migration guards
- dirty and active repositories are explicitly excluded from automatic rollout
- one independent plan review finds no unresolved high or medium issue

### T014. Adapt the Mesh lifecycle to the pinned 1.0.3 contract

Status: done

Depends on: T013

Scope:

- update the committed lock to `v1.0.3`
- remove behavior and messages that are specific to the old `v0.16.5` multi-install path
- inventory installed extensions without updating them; include an update only for
  explicitly approved extension IDs, sources, and target versions
- preserve plan-first execution, exact-version installation, clean-tree checks, locks, and stable-HEAD checks
- update focused unit, wrapper, help, and documentation contract tests

Done when:

- dry-run output contains only exact, official, reviewable commands
- installation and project mutation still require explicit `--apply`
- absent or unapproved extensions do not introduce a network or mutation step
- old manifest versions are reported as partial until upgraded

### T014a. Remove the unsafe Codex/AGY integration collision

Status: done

Depends on: T014

Scope:

- keep Claude as the single project-local Spec Kit integration and workflow owner
- keep Codex and AGY as Mesh worker providers consuming bounded delegation context
- reconcile historical multi-install manifests through non-forced official uninstall commands
- reject unknown integrations and never overwrite locally modified managed files
- document the upstream `.agents/skills` collision discovered by the isolated canary

Done when:

- `specify integration status` is `OK` with Claude as the only installed integration
- Codex and AGY remain selectable as Mesh writer/reviewer providers
- upgrade plans remove only known historical worker integrations before upgrading Claude
- no plan contains an upstream `--force` or claims simultaneous Codex/AGY skills

### T015. Validate 1.0.3 in an isolated fixture

Status: done

Depends on: T014a

Scope:

- install the pinned CLI in an isolated tool environment before changing either workstation runtime
- initialize a disposable historical fixture with Claude, Codex, and AGY, then
  reconcile it to the single Claude workflow integration
- exercise status, capabilities, project upgrade, extension handling, and idempotent replay
- compare generated paths with the 0.16.5 fixture and retain bounded evidence

Done when:

- Claude exposes the required workflow skills; Codex and AGY receive the same
  bounded artifact identity through Mesh context without project-local skill collisions
- specs, plans, tasks, constitution, source, and Git history remain unchanged during upgrade
- a second upgrade produces no unexpected managed-file changes
- rollback removes only the disposable fixture and isolated tool environment

### T016. Upgrade Gobabygo and the Dell runtime canary

Status: pending

Depends on: T015

Scope:

- upgrade the Mac CLI explicitly to the reviewed pin
- upgrade Gobabygo's own integrations through the manifest-aware path
- commit generated project changes separately from lifecycle implementation
- fast-forward a clean Dell runtime and explicitly upgrade its CLI and project integrations
- verify new and resumed coordinator contracts report required=installed=1.0.3

Done when:

- Mac and Dell report the same pinned CLI and Gobabygo capability set
- both Gobabygo checkouts are clean at the reviewed revisions
- downgrade commands and the previous Git revisions are recorded before rollout
- no existing coordinator or worker pane is mutated by the upgrade tests

### T017. Run a real development-orchestration E2E

Status: pending

Depends on: T016

Scope:

- create one disposable, non-production micro-feature through specify, plan, and tasks
- have Claude coordinate, AGY implement one bounded change, and Codex review an immutable range
- reconcile the authoritative task state to the existing GitHub Issue ledger
- verify CI, issue closure, replay idempotence, resume, and capability injection

Done when:

- the workflow completes without direct worker ownership of task or issue state
- evidence includes exact commits, task identity, review verdict, CI run, and ledger replay
- no mocks or direct operator edits substitute for provider dispatch in the real E2E
- all disposable remote and local artifacts have an explicit retention or cleanup decision

### T018. Roll out one operational repository at a time

Status: pending

Depends on: T017

Scope:

- process `coordination`, `rektslug`, `UTXOracle`, `ccxt-data-pipeline`, `nautilus_dev`, `monitoring-stack`, and `progressive-deploy` independently
- require a clean worktree, named branch, stable HEAD, reviewed dry-run, and repository-specific tests
- use manifest-aware upgrade for initialized projects and existing guarded migration for legacy or missing projects
- record the pre-upgrade revision and restoration command before each apply; on failure,
  restore only files changed by that operation or stop with an explicit partial-state report
- never batch repositories or resolve unrelated active work as part of this task

Done when:

- every migrated repository reports required=installed=manifest=1.0.3
- each repository has its own reviewed diff and commit or a documented blocker
- existing specs, constitutions, hooks, source, and custom skills are preserved
- a failure in one repository does not block or partially mutate another

### T019. Review, document, and close the 1.0.3 rollout

Status: pending

Depends on: T018

Scope:

- perform one exact-range independent review of lifecycle code and one review of generated rollout diffs
- fix every high or medium finding with a separate commit and focused regression test
- run focused suites, the full suite, Mac/Dell smoke tests, and the real E2E replay
- align concise operator docs, push reviewed commits, and fast-forward clean runtimes

Done when:

- no high or medium finding remains after at most two review rounds total; otherwise
  stop the rollout as blocked and record follow-up work
- CI and the GitHub ledger are green and the documented commands match actual behavior
- `required`, `installed`, and project manifests report `1.0.3`; `latest` remains
  truthful read-only metadata and may report a newer release
- future releases remain read-only notifications until another explicit reviewed upgrade

## 1.0.3 Upgrade Plan Review

Verdict: approved for incremental implementation with the constraints encoded in T013-T019.

### R006. A major-version pin change is not a project upgrade

Severity: high

Changing only `config/speckit.lock.json` would make every existing project partial
and could leave installed skills at 0.16.5. T014-T016 therefore treat CLI,
Gobabygo project files, and downstream repository files as separate transitions.

### R007. Operational repositories contain active work

Severity: high

The Dell inventory found dirty worktrees in `coordination`, `UTXOracle`,
`ccxt-data-pipeline`, `nautilus_dev`, `monitoring-stack`, and
`progressive-deploy`. T018 prohibits batching or touching any such repository
until its own active work is resolved and its dry-run is reviewed.

### R008. Upstream now provides a safer routine upgrade path

Severity: medium

Spec Kit 1.0.3 documents `specify integration upgrade <key>` as the default and
`init --here --force` only as a fallback. Existing Mesh code already uses the
manifest-aware command for initialized projects; T014 preserves it and limits
the custom transactional copier to legacy adoption.

### R009. Extension updates have a distinct trust boundary

Severity: medium

The official upgrade guide recommends updating installed extensions, but
community extensions are independently maintained code. T014 inventories them
without mutation and may update only explicitly approved IDs, sources, and
target versions. It must never install a new extension, catalog, preset, bundle,
or workflow during this version rollout.

### R010. Review loops need a finite stop condition

Severity: medium

The rollout allows at most two review rounds total. If a high or medium finding
remains after the second round, rollout stops as blocked and records follow-up
work; low residual risks are recorded instead of extending review indefinitely.

Independent review evidence: round one found four medium issues covering
extension trust, per-repository rollback, the review bound, and moving `latest`
metadata. The corrected second round returned `PASS` with no unresolved high or
medium finding.

### R011. Codex and AGY cannot coexist as project-local integrations

Severity: high

The isolated 1.0.3 canary proved that Codex and AGY both own
`.agents/skills/speckit-*` with provider-specific command syntax. Upstream marks
AGY as not multi-install-safe and reports the historical three-integration state
as `ERROR`. T014a makes Claude the sole workflow integration. Mesh continues to
delegate bounded Spec Kit artifacts to Codex and AGY, so worker routing remains
multi-provider without corrupting project-local skills.

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
