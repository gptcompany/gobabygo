# Tasks

- [x] T016: Add supervisor spec and reason-code taxonomy.
- [x] T017: Add controller failure classifier from pane tail and timeout context.
- [x] T018: Add bounded remediation registry for known controller failures.
- [x] T019: Add supervisor report payload and handoff/report fields.
- [x] T020: Add tests for classification and remediation limits.
- [x] T021: Run one live smoke with supervisor reporting enabled.
- [x] T022: Diagnose single-role repair visibility after iTerm tab creation in the existing UI group.
- [x] T023: Fix same-window single-role repair discovery and validate it with a live smoke.
- [x] T024: Add a stable `mesh speckit smoke` wrapper for the validated local regression path.
- [x] T025: Verify the smoke wrapper expansion and help output locally.
- [x] T026: Extract the provider-neutral policy and report core.
- [x] T027: Add debounced metadata-only `mesh live tick --observe` transitions.
- [x] T028: Record the same transitions in the existing guarded apply/cron path.
- [x] T029: Add a bounded, immutable `DECISION_ID` challenger contract.

## Phase 2: Provider Rate-Limit Scheduling

- [x] T030: Freeze a provider capability matrix for Claude, Codex, and Antigravity from currently observed exact UI evidence. Record which provider exposes a machine-parseable reset time and timezone; do not infer parity across CLIs.
- [x] T031: Make the persisted Claude session-limit schedule the sole timing authority for coordinator and worker wakeups. Require the exact current banner, IANA timezone, reset minute, 90-second grace, owner/session/pane fingerprint, one-attempt tombstone, and a fresh recapture before input; coordinator prose and arithmetic must never override `not_before`.
- [ ] T032: Keep Codex and Antigravity rate limits fail-closed until each provider exposes an exact, tested reset contract. Report a provider-specific blocker, allow an explicitly declared worker substitution, and forbid guessed wake times, blind Enter, task resend, or automatic session replacement.
- [ ] T033: Expose the provider, schedule source, and canonical `not_before` in tick/supervisor JSON and concise operator output. A passed timestamp means only that a guarded wake may be attempted; it never proves the provider is available or the delegated task resumed.
- [ ] T034: Add regression tests for reset minutes, midnight, DST/zone transitions, stale and changed banners, process/pane replacement, persisted state across restart, pending composer refusal, one-attempt behavior, and post-wake verification. Include a real local CLI E2E with a simulated clock and a read-only Dell smoke against any naturally present limit screen; never induce account exhaustion.
- [ ] T035: Align the coordinator contract and runbook, obtain an exact-range independent review, run the full suite, deploy by clean fast-forward to the Dell runtime, and verify the five-minute managed scheduler preserves and consumes the same persisted `not_before` after restart.

### Phase 2 Dependencies

- T031 and T032 depend on T030.
- T033 depends on T031 and T032.
- T034 depends on T031 through T033.
- T035 depends on T034.

### Phase 2 Completion Rules

- No provider reset is inferred from another provider, transcript prose, activity age, or coordinator calculation.
- No rate-limit path sends input without exact current-screen evidence and a persisted pre-input tombstone.
- Availability after reset requires fresh provider-state evidence; elapsed time alone is not success.
- Every task receives a separate commit after focused tests pass.
