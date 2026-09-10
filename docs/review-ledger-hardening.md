# Review ledger hardening

## Verified scope

The existing ledger already limits each task cycle to two corrections, uses
atomic writes and revision checks, and prohibits backlog decisions for blocking
findings. Before R1, reinitialization after REPLAN accepted an unchanged scope
without evidence and cleared current findings. Mesh live's obligation to consult
this ledger before correction dispatch is currently coordinator prompt policy.
The parser accepts canonical Tnnn IDs, not legacy subtask names such as T003x6-L1d.

## Tasks and acceptance

- [x] R1 Require a bounded replan artifact for a restarted cycle. Bind it to the
  task and previous cycle, preserve carried findings, retain event history, and
  expose cumulative correction counts. Test missing, stale, repeated and valid
  artifacts; unchanged source scope is legal when the plan changes.
- [x] R2 Define explicit canonical task mapping for legacy subtasks. Inspect
  GitHub binding and task parsing before migration; do not rename historical
  tasks or invent review results. Keep parent lineage across split tasks.
  Implemented as append-only aliases sharing the canonical parent cycle, not
  separate budgets. Actual 096 mapping/import remains R4.
- [x] R3 Bind planned correction dispatch and completion to ledger checks in
  the managed execution path. Specify retry semantics and test concurrent
  revisions, duplicate dispatch and interrupted delivery. Raw CLI access remains
  outside this guarantee.
  `review dispatch` runs on the host holding the ledger and tmux, persists intent
  before tracked input, and refuses replay. `review complete` checks the exact
  RELEASE report digest and updates only the canonical task checkbox atomically.
  Transport uncertainty stays unknown; it is not interpreted as failure to send.
- [ ] R4 Reconcile the 096 ledger after active delegations finish. Record
  imported evidence and uncertainty explicitly, and verify coordinator resume
  consumes its actual state. No automatic conversion of checkboxes into PASS.
- [x] R5a Run an isolated real-worker E2E through rejection, documented replan,
  correction, review and completion.
- [x] R5b Obtain independent implementation review and fix confirmed findings.
- [ ] R5c Deploy matching Mac/Dell revisions after connectivity and reconciliation.

## Plan review

Two corrections are a local budget, not proof of non-convergence. A replan
document establishes accountability, not architectural correctness. The source
commit may remain unchanged while a new implementation strategy is planned.
Blocking findings must remain visible across cycles. Classification of related
defects and adequacy of a proposed remedy require reasoned review; neither is
inferred from filenames, text similarity or a changed digest.

R1 can ship independently. R2 is a prerequisite for adopting R3/R4 on legacy
work. Do not claim that the coordinator is mechanically gated until R3 and the
real-worker E2E have passed. Production adoption additionally requires R4/R5c.

## R1 verification

The regression reproduced an evidence-free reset before the fix. Tests cover
missing evidence, incorrect task/cycle binding, boolean cycle values, empty or
oversized fields, retained findings/history, cumulative corrections, CLI argument
wiring, stale revision retries and artifact reuse after another REPLAN.
Existing duplicate-review checks remain intact even when initialization retains
the same source scope. No remote session was mutated or production ledger imported.

Independent Claude review initially timed out after 180 seconds. A later local
read-only review of commit 808b0f1 completed. Its conditional medium finding does
not apply: initialize_task already rejects all existing states except
REPLAN_REQUIRED before requiring the artifact. Low findings on duplicate length
validation, redundant UnicodeError and ambiguous cycle type diagnostics were
cleaned up. Artifact text is intentionally preserved verbatim to match its digest.
CLI integration tests are not a real-worker E2E and do not complete R5.

Integration follow-up: the observed 096 directory also lacks github-ledger.json,
which load_feature requires. The legacy task file must be reconciled with the
binding/parser contract before enabling transactional enforcement. Do not silently
fall back to manual counts and report the feature as ledger-backed.

## R2/R3 independent review

Claude reviewed the implementation read-only. The confirmed operational finding
was that unexpected transport exceptions could leave an attempt without a readable
receipt. Dispatch now catches ordinary exceptions after reservation, stores an
unknown receipt, and still prohibits replay. Process death/signals remain visible
through status.last_dispatch as unknown with receipt_recorded=false. Tests cover
both paths, failed persistence before input, and competing ledger transactions.

Two reported concerns do not change the design: completion belongs to tasks.md,
not a second completion flag in the review ledger, so its revision deliberately
does not advance for checkbox updates. Task status now projects completed from
tasks.md. External editors do not participate in the ledger lock: the pre-replace
content check reduces, but cannot eliminate, races with uncooperative writers.
The existing ledger atomic writer has the same boundary. Managed Mesh operations
share the lock; this is not an OS sandbox.

## Local E2E evidence

`tests/e2e_mesh_review_local.py --run --codex-executable <native-codex>` creates a
temporary Git repo and a dedicated tmux server via TMUX_TMPDIR. It runs actual
Codex with workspace-write sandboxing, then an independent read-only Claude
review of the exact diff and observed tests. It never publishes GitHub issues.

The first successful run retained artifacts under
`/private/tmp/mesh-review-e2e-z1c0c_35/repo`. The final unattended rerun on the
updated implementation passed at `/private/tmp/mesh-review-e2e-40y4wf3q/repo`.
They proved:

- The deliberately broken addition tests failed before correction.
- Missing replan evidence was rejected without restarting the cycle.
- Managed dispatch persisted its attempt before real worker input.
- Submission initially returned unknown; observation established completion
  without a second paste. Guarded recovery was not applicable and sent no input.
- Repeating dispatch with the current revision was rejected.
- Both unchanged tests passed; Claude returned REVIEW_VERDICT: PASS.
- DELTA review, candidate update, RELEASE review and complete finished at ledger
  revision 15 with only T001 checked.
- The dedicated tmux worker was removed in cleanup; artifacts remain inspectable.

The first attempt using the npm Node launcher stopped at startup without dispatch.
The successful run used the installed native Codex binary. The harness confirms
only the exact trust dialog of its own newly created scratch repository; it does
not weaken runtime provider/composer guards. This is a local end-to-end test, not
evidence that the disconnected Dell runtime has been upgraded.

A repeat exposed a transient preflight refusal while Codex still held the paste;
a later existing guarded recovery succeeded. The harness now continues bounded
observation and only invokes recovery when the exact delegation is still in the
composer. The helper itself recaptures and persists its one-shot Enter attempt;
there is never a second paste or an unguarded submit. The final rerun completed
without manual input. A narrow final Claude review confirmed the receipt fix and
reported no concrete regression. Local validation: 108 perimeter tests and 16
coordinator prompt tests passed; subsequent docs checks passed as well.

## Dell staging

After the operator reported Wi-Fi recovery, SSH worked through sam@10.0.0.2;
sam@172.23.0.42 still timed out. The tested implementation was pushed via HTTPS.
An immutable test-only worktree was prepared at
`/data/sata/1TB/gobabygo-review-hardening-fdcdd66`, commit
`fdcdd663cb826a575ade47cfd9bd8ce7e1a7cb61`. The same 108 perimeter tests passed
using `/data/sata/1TB/gobabygo-runtime/.venv/bin/python`; the staging worktree is
clean. Detached HEAD is intentional for this test checkout; do not develop or
commit there.

R4/R5c remain open because claude-coordinator is actively working on 096. After
Cloudflare SSH connectivity was re-established, the clean Dell runtime was
fast-forwarded to `fd1d3817`; Python compilation and shell syntax checks passed,
matching the pushed Mac revision. This does not itself reconcile the active 096
ledger or replace the contract of an already-running coordinator. At an idle
checkpoint, reconcile the current task and
delegations, migrate its canonical index/binding with evidence, then activate
the tested runtime and load its current coordinator contract. Attaching to an
existing tmux process does not replace its system prompt; a fresh bootstrap or
resume appends the newly generated contract. Verify the new dispatch/complete
instructions before claiming production adoption.

## Worker lifecycle follow-up

Verified on Dell: codex-gobabygo-speckit-103-canary still holds
DLG-T003X6HREV2-CODEX-20260905T0907Z in its bottom composer. The 096 tasks.md
already records it as an obsolete duplicate at line 13483 and says not to wake
it. Thus absence from active work is not proof that no record exists; the missing
operation is reconciliation of that decision with the live session.

- [x] W1 Expose visible pending Codex delegations in board and the existing
  debounced supervisor. Require reconciliation before TICK_IDLE, reuse or new
  scoped workers. This is an inspection signal, not proof of obsolescence.
- [x] W2 Add guarded retirement using existing state/identity helpers: a durable
  cancellation/supersession record and preserved brief/handoff first; exact pane
  and process identity, detached state, no active work or background tools,
  clean Git, and a fresh unchanged capture before termination. The first release
  deliberately treats only a clean worktree as accounted; it does not infer an
  operator's intent from a dirty diff.
  Never submit obsolete text. Unknown delivery requires reconciliation, not age
  based deletion. Test against isolated tmux sessions before production cleanup.
- [x] W3 Enforce reuse and duplicate-worker prevention in ensure paths using
  verified canonical repository/provider identity; explicitly exempt bounded
  independent review roles. Report why a new worker is needed. Do not infer
  ownership or terminate sessions from names alone.
- [ ] W4 Reconcile and retire the obsolete canary through the guarded path;
  validate lifecycle behavior end-to-end and deploy the updated tick contract.

W4 observation, 2026-09-10: after the guarded retirement runtime was deployed,
`codex-gobabygo-speckit-103-canary` was absent from the Dell tmux server
(`tmux has-session` exit 1 and an exact live-board query returned no session).
No Mesh Live retirement command was applied by this work, so the absence cannot
be attributed to the guarded path and does not close W4. Preserve the existing
supersession evidence; perform a new isolated lifecycle E2E or reconcile an
identified replacement session before marking this task complete.

W1 did not itself close workers or solve accumulation. W3-W4 remain required;
no production session was closed or submitted while investigating this report.
W1 validation: 286 live/supervisor/docs tests passed. Independent Claude review
found no confirmed regression: its conditional split concern does not apply
because _CODEX_FOOTER is a compiled regex. A DLG reference quoted in the current
composer can still produce an inspection warning; it never authorizes an action.
The follow-up live board request timed out, so live rollout is not claimed.
