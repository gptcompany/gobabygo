# Spec Kit consolidation: T001 inventory

Observed 2026-09-08. Read-only inventory; no runtime update or session input.

## Hosts and provenance

Mac specify version and mesh_speckit_cli.py status report CLI 1.0.3, project
manifest 1.0.3, sole integration Claude, and ten skills including converge and
taskstoissues. Installed workflow: .specify/workflows/speckit/workflow.yml.
Worker providers Codex and AGY remain Mesh providers, not additional installed
project integrations. Mac orchestration trust reports commit_not_on_origin:
the newly committed plan has not been pushed. This is not CLI version drift.

Dell status, through read-only SSH sam@10.0.0.2, reports CLI and coordination
manifest 1.0.3, the same Claude capabilities, and an installed speckit workflow.
Production Gobabygo is still cb0e8878abf2fcf6baa8475df472335db8a16c33.
Its cached latest-known version is 1.0.4; no release lookup or upgrade was made.
Version alignment does not imply workflow enablement or execution parity.

Mac installation direct_url.json identifies requested tag v1.0.3 and commit
6906bc582230bb752776e23287ee97990c1af743. HTTPS git ls-remote confirms v1.0.3
is an annotated tag (8d754a947680b02cf585b64f7d45bde736feee83) that peels to
that commit. Installed workflows/engine.py matches the raw upstream file at
that immutable commit, SHA256:
de85b2545c0d56b983b1b3465f5f6ce1aaeffa18255ae8aec09fff6a428137e1.
This file comparison is not a whole-package integrity attestation. Dell's
direct_url.json independently reports the same requested tag and commit; its
installed source files have not been compared byte-for-byte.

## Reuse matrix

| Requirement | Installed 1.0.3 evidence | Disposition |
| --- | --- | --- |
| SDD artifacts and skills | Installed Claude skills and manifest | Reuse |
| Sequencing and gates | Installed speckit workflow, command/gate steps | Native, execution boundary must be tested |
| Bounded loops | while_loop step accepts max_iterations, default 10 | Native per-loop bound, not cumulative review history |
| State persistence | engine RunState saves using temp file + replace | Reuse candidate, not proof of cross-process ownership |
| Resume | engine.py nested execution comment and step_offset=-1 | Nested parent/body re-execution must be tested in T002 |
| Mesh invocation | shell step supports commands and JSON output | Adapter candidate; shell=True, inherited env and captured output are trust boundaries |
| Worker invocation | command step calls integration dispatch_command, which uses subprocess.run | Built-in workflow launches integration CLI; do not run against live coordinator |
| Overlay customization | Installed workflows/overlays implementation | Available in installed source; executable compatibility test still required |
| Review evidence and cross-replan budget | Existing Mesh ledger APIs | Retain pending actual upstream parity proof |
| GitHub conversion | Installed taskstoissues skill | Reuse intent/templates, not a drop-in replacement for reconciliation |
| Stable GitHub identity and reconciliation | Existing Mesh feature/task keys and sync adapter | Retain until T006 proves parity |
| Persistent tmux receipt and recovery | Existing Mesh tracked send/review dispatch | Retain; workflow completion is not delivery proof |

The taskstoissues skill deduplicates by Tnnn found in repository issue titles,
then skips existing IDs. It does not namespace this match by feature. Two
different features both containing T001 can therefore collide under that
documented algorithm. It also does not specify the Mesh closure/reopen/content
reconciliation contract. This is a static template finding, not a live GitHub
experiment. No GitHub issues were created or changed.

Shell-step output is captured into step output. Do not pass credentials in
workflow input or emit raw tmux captures into persisted workflow state. A
future adapter must expose bounded structured receipts only. Workflow YAML is
trusted executable configuration, not arbitrary coordinator-generated text.

## 096 migration blockers

Read-only Dell checks found no origin URL in coordination. Its tasks.md is
1,079,469 bytes, exceeding mesh_speckit_github.py MAX_TASKS_BYTES (1,048,576).
Thus removing the GitHub binding dependency alone cannot make this feature
loadable. Preserve the historical file and reconcile a concise canonical active
index at a writer checkpoint. Do not raise the size limit as a migration shortcut
or infer review PASS from old checkboxes.

## Decision and remaining proof

Proceed to T002, not to production executor replacement. Test harmless shell
steps, top-level and nested gate resume, crash and competing resume processes
in a dedicated temporary directory. No native command/prompt steps may launch
real AI CLIs in this initial boundary test. If nested replay or concurrency
cannot be contained by existing Mesh intent/locking, keep native execution out
of worker dispatch rather than introduce another scheduler.

T001 is an inventory, not an E2E. Runtime enablement, Dell installed-source
equivalence and operational workflow behavior must not be inferred from status.

## Independent review and disposition

Local Claude reviewed this supplied inventory with tools and MCP disabled. Its
first invocation returned a proposed file lookup instead of a review and was
not counted as approval. A constrained retry completed and explicitly approved
only proceeding to isolated T002. It did not read source or execute tests.

- Confirmed integration limitations: bare-ID issue deduplication and oversized
  096 tasks. The review labeled them critical; for this read-only inventory they
  are known blockers for replacement/migration, not newly activated incidents.
- Accepted clarification: workflow trust and output minimization are future
  implementation requirements, not existing enforcement. T003 now requires
  reviewed versioned definitions, digest verification at run/resume, no free-form
  shell interpolation, and bounded allowlisted receipt fields. It wraps the
  existing send/receipt path, never duplicates it. No sandbox claim is made.
- Rejected provenance finding: cb0e8878 is a Gobabygo commit, whereas 6906bc58
  is a Spec Kit commit and 8d754a94 its annotated tag object. They should not
  match. Dell installation metadata subsequently confirmed the same Spec Kit
  commit; full package integrity remains explicitly unverified.
- Retained test obligations: nested resume and competing processes need T002
  experiments; per-loop limits are not cumulative review budgets. The source
  observation is not reported as a runtime reproduction.
- Read-only describes the commands executed, not a restricted SSH credential.
  Latest-known 1.0.4 is unverified cached metadata, not an upgrade instruction.

Regression baseline: 98 tests passed in tests/test_mesh_speckit_review.py and
tests/test_mesh_speckit_github.py. No real-worker E2E was run for this inventory.
No runtime, workflow installation, GitHub issue or tmux session was changed.

## Review instructions

Independent reviewer: review this inventory and
docs/speckit-workflow-consolidation-plan.md against the supplied evidence.
Identify unsupported claims, missing safety boundaries and unnecessary new
components. Do not modify files, use tools, contact hosts or run workflows.
Distinguish a static finding from a demonstrated runtime failure. Findings first,
severity ordered; no claim of execution without evidence. T002 remains a gate.
