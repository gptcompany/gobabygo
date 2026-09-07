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
- [ ] R3 Bind planned correction dispatch and completion to ledger checks in
  the managed execution path. Specify retry semantics and test concurrent
  revisions, duplicate dispatch and interrupted delivery. Raw CLI access remains
  outside this guarantee.
- [ ] R4 Reconcile the 096 ledger after active delegations finish. Record
  imported evidence and uncertainty explicitly, and verify coordinator resume
  consumes its actual state. No automatic conversion of checkboxes into PASS.
- [ ] R5 Run an isolated real-worker E2E through rejection, documented replan,
  correction, review and completion. Obtain independent review of the committed
  implementation, fix findings, then deploy matching Mac/Dell revisions.

## Plan review

Two corrections are a local budget, not proof of non-convergence. A replan
document establishes accountability, not architectural correctness. The source
commit may remain unchanged while a new implementation strategy is planned.
Blocking findings must remain visible across cycles. Classification of related
defects and adequacy of a proposed remedy require reasoned review; neither is
inferred from filenames, text similarity or a changed digest.

R1 can ship independently. R2 is a prerequisite for adopting R3/R4 on legacy
work. Do not claim that the coordinator is mechanically gated until R3 and the
real-worker E2E have passed.

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
