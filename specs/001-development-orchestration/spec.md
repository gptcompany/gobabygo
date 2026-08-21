# Feature Specification: Development Orchestration Ledger

**Feature Branch**: `master`

**Created**: 2026-08-21

**Status**: Approved

**Input**: Make Spec Kit-driven development orchestration robust, KISS, agent-readable, and safely synchronized to GitHub without introducing another source of truth.

## User Scenarios & Testing

### User Story 1 - Publish Durable Work (Priority: P1)

An operator or coordinator turns one feature's approved Spec Kit tasks into a
shared GitHub work ledger without manually recreating or renumbering tasks.

**Why this priority**: Durable, unambiguous work identity is required before
multiple agents can delegate, review, and resume work reliably.

**Independent Test**: Publish a feature twice and verify that the second run
creates no duplicate work items and reports an aligned ledger.

**Acceptance Scenarios**:

1. **Given** a valid opted-in feature with unique task IDs, **When** it is published, **Then** each task has exactly one namespaced GitHub issue.
2. **Given** a previously published feature, **When** task wording or completion changes, **Then** the same issue is reconciled without changing task identity.
3. **Given** two features that both contain `T001`, **When** both are published, **Then** they remain distinct work items.

---

### User Story 2 - Detect Drift Before Mutation (Priority: P1)

A contributor or CI job can inspect the proposed reconciliation and detect
duplicates, orphaned issues, malformed tasks, wrong repositories, and state
drift without changing GitHub.

**Why this priority**: A sync process is only trustworthy when its intended
changes and refusal conditions are reviewable before mutation.

**Independent Test**: Supply a captured issue snapshot containing duplicates
and orphans and verify a deterministic non-zero result with no mutation.

**Acceptance Scenarios**:

1. **Given** malformed or duplicate task IDs, **When** validation runs, **Then** it fails before any network mutation.
2. **Given** an issue closed manually while its task remains pending, **When** checking runs, **Then** it reports state drift and does not rewrite `tasks.md`.
3. **Given** a repository different from the feature binding, **When** synchronization is requested, **Then** it fails before creating or editing an issue.

---

### User Story 3 - Coordinate Through One Ledger (Priority: P2)

The coordinator and workers receive a compact contract identifying the exact
feature and task, while GitHub Actions performs authoritative remote mutation.

**Why this priority**: Agents need shared visibility, but interactive prompts
and local hooks must not become an untracked mutation path.

**Independent Test**: From a task issue, derive the immutable feature/task key,
delegate work, and associate the resulting pull request without consulting a
second task database.

**Acceptance Scenarios**:

1. **Given** an opted-in feature committed to Git, **When** its tasks change on a pull request, **Then** CI checks the ledger plan without remote mutation.
2. **Given** the same change on the default branch, **When** the synchronization workflow runs, **Then** it applies only the one-way `tasks.md` to GitHub reconciliation.
3. **Given** a worker completes code, **When** it reports results, **Then** completion still requires reviewed evidence and the authoritative task update.

### Edge Cases

- A feature directory is renamed after its first publication.
- A task description changes while its issue contains human comments.
- A task is removed after publication, producing an orphaned issue.
- GitHub pagination contains pull requests, closed issues, or unrelated tasks.
- Two synchronizers run concurrently for the same feature.
- A completed task is published for the first time.
- GitHub is unavailable or returns a partial/malformed response.
- A fork or copied manifest points at the original repository.

## Requirements

### Functional Requirements

- **FR-001**: `tasks.md` MUST remain the sole authority for task identity and completion state.
- **FR-002**: Every published feature MUST have a committed immutable feature identifier independent of directory name and task description.
- **FR-003**: Every task MUST be identified by the composite key `<repository>:<feature-id>:<task-id>`.
- **FR-004**: Synchronization MUST reject duplicate task IDs, duplicate issue markers, malformed manifests, orphaned issues, and repository mismatches before mutation.
- **FR-005**: The system MUST provide a deterministic, machine-readable, mutation-free reconciliation plan.
- **FR-006**: Applying an unchanged normalized plan repeatedly MUST create no duplicate issue and perform no unnecessary update.
- **FR-007**: Synchronization MUST be one-way; GitHub state MUST NOT modify local Spec Kit artifacts.
- **FR-008**: Manual GitHub state changes that conflict with `tasks.md` MUST be reported as drift and reconciled only by the serialized authoritative Action.
- **FR-009**: Pull-request automation MUST be read-only, while default-branch and manually dispatched automation MAY apply reconciliation with only `contents: read` and `issues: write`.
- **FR-010**: Local hooks MUST be limited to validation or dry-run behavior and MUST NOT perform remote mutation.
- **FR-011**: Existing official Spec Kit skills, prerequisites, feature state, and task syntax MUST be reused wherever their contracts are safe.
- **FR-012**: The official `taskstoissues` capability MUST remain installed, but documentation MUST distinguish its interactive one-shot behavior from the authoritative reconciler.
- **FR-013**: Issue bodies MUST contain a versioned machine marker and bounded source metadata without secrets or pane captures.
- **FR-014**: Human discussion MUST remain in issue comments; reconciliation MUST update only machine-owned title, labels, body, and state.
- **FR-015**: The coordinator MUST use immutable feature/task keys in delegations and MUST NOT infer completion from worker idleness or prose alone.
- **FR-016**: Published task IDs MUST be append-only; removing one MUST block synchronization until the same ID is restored and explicitly completed or cancelled in `tasks.md`.
- **FR-017**: Unmarked issues whose titles look like official Spec Kit task issues MUST be reported as legacy drift rather than silently adopted or ignored.
- **FR-018**: All remote writers MUST run through one repository-serialized GitHub Actions workflow that refreshes remote state before applying its plan.
- **FR-019**: Coordinator startup status MUST expose an immutable Gobabygo runtime commit only when the runtime checkout is clean, has the expected GitHub origin, and resolves to a full commit SHA.
- **FR-020**: For planned work in an unconfigured repository, the coordinator MUST plan and stage the existing managed caller and feature binding automatically instead of asking the operator to run setup commands.
- **FR-021**: Automatic onboarding MUST change only Spec Kit planning artifacts, the managed caller, and the feature binding on a non-default planning branch; it MUST NOT overwrite custom workflow content or write source code.
- **FR-022**: The coordinator MUST include the onboarding files in the planning pull request and MUST wait for the read-only ledger check, merge, authoritative issue publication, and an aligned check before implementation delegation.
- **FR-023**: Workers MUST consume the exact Spec Kit context and immutable task key but MUST NOT install callers, initialize bindings, mutate issues, or create a competing specification pipeline.
- **FR-024**: Resume MUST receive the current bounded runtime and onboarding contract through the same startup injection path as a new coordinator.

### Key Entities

- **Feature Binding**: Committed opt-in metadata containing schema version, immutable feature ID, and exact GitHub repository.
- **Spec Task**: A checkbox task with a unique `T` identifier, description, optional story/parallel markers, and authoritative completion state.
- **Task Key**: Immutable composite identifier joining repository, feature ID, and task ID.
- **Ledger Issue**: GitHub issue carrying one exact task key in a versioned machine marker.
- **Reconciliation Plan**: Ordered set of create, update, close, reopen, no-op, or blocking drift decisions.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Re-running synchronization on an aligned feature performs zero GitHub mutations.
- **SC-002**: Two features containing the same local task ID publish with zero collisions.
- **SC-003**: All malformed, duplicate, orphaned, or wrong-repository fixtures fail before the first mutation.
- **SC-004**: Pull-request checks execute with read-only repository permissions and produce a reviewable report.
- **SC-005**: A controlled end-to-end test completes task publication, update, closure, and idempotent replay with exactly one issue per task.
- **SC-006**: An agent can identify the governing feature and task from an issue or delegation without reading tmux output or router internals.
- **SC-007**: One disposable fictitious development objective completes the real coordinator/worker/reviewer lifecycle from specification through a CI-verified pull request and ledger closure.
- **SC-008**: A second repository consumes the reconciler through a minimal caller pinned to an immutable reviewed Gobabygo commit, without copying implementation code.
- **SC-009**: From one `mcoordinator <repo> --workflow speckit` launch and one objective, an initially unconfigured disposable repository reaches an aligned published ledger without manual setup commands.
- **SC-010**: A custom caller, dirty runtime, malformed runtime origin, unavailable GitHub CLI, or failed Action stops onboarding with one explicit blocker and no direct default-branch or issue mutation.

## Assumptions

- GitHub remains the shared development ledger and pull-request system.
- Repository CI already has a trusted GitHub Actions execution path.
- Spec Kit task IDs remain stable after publication; new tasks receive new IDs.
- Cancellation preserves the published task line and ID, marks it complete, and records the reason in its description.
- The router database remains optional execution history, not task authority.
- GitHub Projects may consume issues through native auto-add rules and is not synchronized directly.
- Runtime alert remediation is explicitly outside this feature.
