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

## Automatic Onboarding Canary

- Repository: `gptcompany/gobabygo-onboarding-canary`; feature:
  `001-normalize-whitespace`; initial state: Spec Kit 0.16.5 integrations
  aligned, with no ledger caller and no feature binding.
- One coordinator objective used trusted Gobabygo runtime commit
  `0b298088689284b62f16a2246995c2d960892095`. The coordinator installed the
  pinned caller, created binding `normalize-whitespace-c4ec8b8bbe33`, and
  opened planning PR #1 without operator-executed onboarding commands.
- Planning PR #1 merged as `a2f25cf7`; ledger run `32474814391` published
  issues #2-#11 after pull-request check run `32474768853` passed.
- Antigravity produced implementation commit `e04b955`; Codex reviewed the
  frozen range read-only and returned `PASS` with validated low-severity test
  findings. Antigravity corrections `abcd35d` and `ebfa089` passed follow-up
  review and `git diff --check`.
- Implementation PR #12 passed CI on Python 3.11 and 3.14, then merged as
  `0b572af9`. Default-branch ledger run `32476378925` closed issues #2-#11.
- Workflow-dispatch replay `32477457045` reported `aligned=true`,
  `mutations=0`, and ten closed no-op operations.
- Manual interaction was limited to dismissing first-run Codex vendor prompts
  and delivering the already frozen first review pointer after a guard false
  positive. No onboarding, source edit, commit, push, issue mutation, or merge
  was performed manually. Commits `5fe1ef9` and `f3f217e` add the observed
  completed-reply regressions to the guard before this evidence is published.
