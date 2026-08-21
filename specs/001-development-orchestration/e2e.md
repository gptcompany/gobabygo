# Development Orchestration E2E Evidence

## Ledger E2E

- Local focused regression: 250 tests passed before first publication.
- Independent exact-range review found two defects; commits `8cd10e3` and
  `6e4cc4e` fixed reserved marker injection and stale feature identity.
- Initial Action run `32457058492` created issues #5-#14 and exposed an
  eventually-consistent post-write read. Commit `32a9457` added bounded,
  read-only convergence retries.
- Replay Action run `32457189536` passed with `aligned=true` and `mutations=0`.
- Update Action run `32457250823` updated T008 in place as issue #12; the
  feature retained exactly ten issues and created no duplicate.
- This commit changes authoritative T008 state to complete. Its Action run must
  close issue #12 before T008 is considered operationally complete.

## Real Orchestration Canary

- Repository: `gptcompany/gobabygo-orchestration-canary`; feature:
  `001-is-even`; binding: `is-even-d4cb603bd73f`.
- Planning PR #1 merged as `53516904`; ledger run `32458497972` published
  issues #2-#9.
- Coordinator delegated writer work to Antigravity as
  `AGY-001-IS-EVEN-IMPL-01`. Implementation PR #10 retained the resulting
  TDD commits and CI evidence.
- Codex review `CDX-001-IS-EVEN-REVIEW-01` returned `CHANGES_REQUIRED` for a
  false-positive test. Antigravity correction `AGY-001-IS-EVEN-FIX-02` and a
  second bounded correction produced commit `fa97cea`.
- Final independent review `CDX-001-IS-EVEN-REVIEW-FINAL-03` returned `PASS`
  after observing the pre-guard test fail and the current suite pass with
  `14 passed`.
- PR #10 merged as `bb519cb4`; authoritative completion commit `1bd5f548`
  closed issues #2-#9 through ledger run `32460264713`.
- Workflow-dispatch replay `32468515187` reported `aligned=true`,
  `mutations=0`, with all eight task operations as closed no-ops.

## Pinned Multi-Repo Rollout

- The installer first reported `operation=update`, `applied=false`, then changed
  only `.github/workflows/speckit-ledger.yml` after explicit
  `--accept-pin-update --apply`.
- During the canary, a pin-only PR exposed that the original path filters did
  not validate the caller itself. Commit `3c8768d` added self-validation and a
  fail-closed migration for the prior managed template; arbitrary workflow
  content remains protected.
- Canary PR #11 pinned both reusable workflow and runtime checkout to immutable
  commit `3c8768dd4163ba9b02b849a1ac3778f892d6c0ce` and merged as `ffb4feee`.
- Pull-request ledger run `32468781942` completed read-only and aligned; default
  branch apply run `32468824413` succeeded.
- Final workflow-dispatch replay `32468848319` reported `aligned=true`,
  `mutations=0`, and only no-op operations for the closed feature.
