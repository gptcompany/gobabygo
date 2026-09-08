# Spec Kit workflow consolidation

Status: reviewed locally; implementation not started.

## Objective and verified baseline

Use upstream Spec Kit capabilities wherever they meet the requirement. Keep
Mesh responsible for persistent worker delivery and its safety guarantees.
Make local reviews usable without GitHub, without creating a second scheduler
or resetting historical review budgets.

The repository pins Spec Kit 1.0.3 in config/speckit.lock.json. This is not
evidence of installed Mac/Dell versions or enabled project workflows.
mesh_speckit_review.py calls the GitHub module's load_feature, which requires
github-ledger.json. The normal binding CLI resolves origin; loading an existing
binding does not itself check origin. Do not fabricate a binding or remote.

The previous runtime-awareness plan deliberately excluded native workflow
execution because it could launch nested AI CLIs. Replacing that decision needs
an executable proof, not only current upstream documentation.

## Ownership

- Spec Kit owns spec, plan, task formats and supported workflow sequencing.
- tasks.md is the canonical task checklist; GitHub Issues are a projection.
- One workflow executor owns each run. Never run Mesh and upstream schedulers
  independently over the same task.
- Mesh owns tmux identity, guarded delivery, unknown receipts, rate-limit
  recovery and worker lifecycle. Workflow resume must not replay a delivery.
- The existing Mesh review ledger remains authoritative for verdict evidence
  and cumulative correction history until an upstream replacement passes parity
  tests and an explicit migration. Workflow state is not a second verdict ledger.
- coordination may hold global specifications without a remote. Each task must
  identify its actual worker repository. GitHub publishing is a separate opt-in.

## Tasks and acceptance

- [x] T001 Inventory the exact runtime and reuse matrix, read-only. Inspect
  installed binaries, integration manifests and workflow definitions on Mac and
  Dell, plus source at the pinned upstream tag. Compare sequencing, bounded
  loops, resume, gate enforcement, review evidence, issue reconciliation and
  worker invocation. Classify each as native, configurable, adapter required or
  unsupported, with executable evidence. A disconnected host stays unverified.
  No implicit installation of latest or community extensions.
  Evidence and external review disposition: speckit-workflow-inventory.md.
- [ ] T002 Prove the execution boundary in an isolated local repository with no
  remote. Run a tiny upstream workflow with harmless steps, pause/resume and
  failure injection. Verify whether supported shell/extension steps can call
  existing Mesh commands without launching unmanaged AI CLIs. Inspect subprocess
  trees and state. Verify concurrency and resume behavior, not only happy paths.
  Gate: adopt native sequencing only if single ownership and delivery safety are
  demonstrable. Otherwise retain the existing executor and document the precise
  gap; do not build a replacement workflow engine. Review this decision before
  production-facing implementation.
- [ ] T003 Implement only the selected integration adapter. Reuse
  mesh_speckit_cli.py inspect_project, inspect_orchestration_runtime and
  build_delegation_context, existing CLI routing and supported upstream extension
  points. Use overlays only if present in the verified release. Explicitly map
  workflow steps to ledger transitions. Review dispatch must persist intent
  before input; an unknown receipt requires reconciliation, never an automatic
  retry. Preserve exact repo/provider identity. Test interruption at each boundary.
  Only reviewed, versioned workflow definitions are eligible for managed
  dispatch; verify their approved digest at run/resume and reject unexpected
  changes. Do not interpolate free-form task text into shell commands. The
  adapter wraps existing dispatch/receipts; it cannot create a second delivery
  path. Persist only allowlisted bounded receipt fields, never pane captures,
  prompts or arbitrary stdout; test these negative cases before activation.
- [ ] T004 Decouple local review loading from GitHub publication. Reuse the
  existing review FSM, atomic writer, lock and revision checks. Introduce only
  the minimum stable local identity needed, following the T001/T002 decision.
  Existing GitHub-bound feature/task keys must remain unchanged. Adding a remote
  later must not reset history or create another logical task. Detect conflicting
  bindings explicitly. Test no remote, offline operation, relocation, collision,
  concurrent updates and unchanged GitHub-backed operation. No fake owner/repo.
- [ ] T005 Add deterministic readiness checks at managed spec start/resume.
  Report artifact, identity, ledger and execution capabilities separately from
  optional GitHub readiness. Initialize only missing, scoped state through the
  approved path; never overwrite an existing spec or invent review results.
  Refuse managed correction dispatch when prerequisites are missing. An already
  attached Claude process does not receive a new system prompt: document and
  verify the fresh-bootstrap/resume path. Raw CLI access remains outside this
  enforcement boundary. Test local-only and GitHub-enabled new specs.
- [ ] T006 Reconcile issue publishing with upstream taskstoissues. Reuse it only
  where tests prove required behavior: repeated runs, stable task identity,
  partial failures, manual issue edits, closure and reopening, and wrong-target
  protection. Otherwise retain mesh_speckit_github.py as the reconciliation
  adapter. Never run two publishers for the same feature. Offline sync failure
  must remain visible without blocking local reviews or falsely reporting sync.
  Remote E2E requires a designated disposable GitHub repository and separate
  authorization for mutations; mocks are not evidence of real GitHub sync.
- [ ] T007 Migrate 096 at an agreed idle checkpoint. Snapshot and hash current
  artifacts, bindings and active delegation receipts first. Preserve the full
  historical tasks file as evidence, build a concise canonical active checklist
  and plan with traceable mappings, and retain findings and known cumulative
  correction counts. Unknown historical counts remain explicitly unknown and
  require a recorded budget decision, not a fresh zero budget. Check the current
  1 MiB parser limit and legacy IDs. Do not edit under an active writer, copy
  checkboxes into PASS verdicts, or replay pending/obsolete delegations.
- [ ] T008 Run real isolated E2E: tiny intentional bug, worker correction,
  independent review, release check and completion; include crash/unknown-delivery
  resume and a rejected excess correction. Cover a no-remote state repo and
  distinct worker repo, plus GitHub mode under T006 authorization. Reuse
  tests/e2e_mesh_review_local.py and existing review/GitHub/CLI suites. Verify
  actual Claude/Codex integration; test AGY delivery separately where touched.
  Use only dedicated scratch tmux sessions. Preserve evidence and clean up only
  resources created by the test.
- [ ] T009 Obtain independent local Claude review of the implementation and
  evidence, fix confirmed findings in separate commits, rerun affected tests.
  Allow at most two review rounds for this rollout. Remaining high/medium issues
  block rollout; record follow-up rather than resetting the review count. Review
  process/credentials failures are not PASS. No production action is authorized
  by a review verdict alone.
- [ ] T010 Roll out matching tested revisions on Mac and Dell at a checkpoint,
  verify a real managed start/resume and readiness status, then align README,
  MESH_LIVE.md, help and the runtime-awareness/hardening task documents. Keep
  exact-version upgrade approval and capability checks; do not auto-update during
  active work. Record commits, test evidence, external review prompt and open
  risks. Retire superseded paths only after parity, never as part of first rollout.

Dependency order: T001 -> T002 decision gate -> T003/T004 -> T005/T006 ->
T007 checkpoint -> T008 -> T009 -> T010. T007 requires explicit reconciled
ownership of 096. Each completed implementation task gets a scoped commit.

## Files and reusable pieces

- config/speckit.lock.json: version source, not an automatic upgrade target.
- scripts/mesh_speckit_cli.py: inventory, capability reporting, existing project
  upgrade/migration locks and plan/apply helpers.
- scripts/mesh_speckit_review.py: initialize_task, review_status, register_alias,
  dispatch_correction and complete_task; preserve history and unknown receipts.
- scripts/mesh_speckit_github.py: load_feature, load_binding, task_key and
  reconciliation logic; separate local loading without weakening publication.
- scripts/mesh, scripts/mesh_live_cli.py and scripts/mesh_live_shell_helpers.sh:
  command routing, bounded coordinator contract, exact startup/resume behavior.
- tests/test_mesh_speckit_{cli,review,github,ledger_workflow,docs}.py,
  tests/test_mesh_review_convergence_e2e.py and tests/e2e_mesh_review_local.py:
  extend existing coverage rather than create a parallel test framework.

## Plan review

This is an internal code-grounded review, not an external Claude review.

1. HIGH: current online docs are not a versioned capability contract. Corrected
   by T001 pinned-source verification and T002 executable gate. Native executor
   adoption is conditional, not a preselected architectural conclusion.
2. HIGH: upstream resume may repeat a step that already sent worker input.
   Corrected by T003 fault tests and existing persisted intent/unknown receipts.
   A workflow gate or prompt instruction is not sufficient delivery protection.
3. HIGH: introducing a local identity can fork existing task keys and budgets.
   Corrected by T004 compatibility and later-GitHub-binding tests; T007 forbids
   converting missing history into a zero count or an invented verdict.
4. HIGH: adopting native workflows can violate the previous no-nested-CLI
   contract. Corrected by process-level proof in T002 and one execution owner.
5. MEDIUM: taskstoissues does not establish ongoing reconciliation parity.
   Corrected by T006 behavioral tests and one publisher per feature.
6. MEDIUM: successful local E2E does not prove Dell deployment or contract reload.
   Corrected by separate T008/T010 gates and explicit disconnected-host status.
7. MEDIUM: rollback after real dispatch cannot undo external side effects.
   Stop new dispatch, retain receipts and ledger, reconcile in-flight work, then
   roll back executable code only where schema compatibility is proven. Never
   restore an old ledger snapshot over newer dispatch records.

Verdict: ready for T001/T002 implementation and evidence gathering. Production
integration remains gated on that evidence; this plan does not certify native
workflow suitability, current host alignment or successful 096 migration.

## Sources

Current upstream references, to be checked against the pinned tag in T001:

- https://github.github.com/spec-kit/reference/workflows.html
- https://github.com/github/spec-kit/blob/main/templates/commands/taskstoissues.md

Local antecedents: specs/speckit-runtime-awareness/tasks.md and
docs/review-ledger-hardening.md. Their incomplete rollout/lifecycle tasks remain
open; this plan neither marks them complete nor authorizes worker retirement.
