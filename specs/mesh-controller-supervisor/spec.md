# Mesh Controller Supervisor

## Purpose

Provide one provider-neutral policy core above deterministic control adapters.

The controller stays deterministic and simple:

- open panes
- send prompts
- wait for markers
- auto-approve known prompts
- enforce turn and retry limits

The iTerm controller may classify and remediate its bounded protocol failures.
`mesh live tick` observes existing tmux sessions directly and records debounced
metadata-only transitions. Live observation never remediates or sends input.

## Problem

The current controller fails hard when CLI output deviates from the protocol:

- marker shape changes
- ack and marker are combined on one line
- marker is wrapped with extra prose
- a reviewer lacks artifact context
- a model fallback menu appears
- a known approval prompt changes shape

These are not product failures. They are controller/runtime failures.

## Design

### Layer 1: Controller

Mechanical, deterministic, no free-form reasoning.

Responsibilities:

- issue prompts
- capture pane tails
- detect markers
- auto-approve known prompts
- run smoke tests
- write handoff JSON

### Layer 2: Supervisor Core

Observes controller state and classifies anomalies.

Responsibilities shared by adapters:

- classify controller failure mode
- map known failures to bounded remediation policy
- stop loops
- write explicit supervisor outcome

### Adapters

- `mesh term`/iTerm2 may execute the existing allowlisted, bounded remediation.
- `mesh live tick --observe` persists only controlled state, severity, reason,
  timestamp, and transition history after two matching observations.
- `mesh live tick --apply` records the same transitions before its existing,
  separately guarded WAIT and coordinator wake actions.
- No adapter introduces a second AI process, daemon, database, or router dependency.

## Supervisor Failure Classes

- `marker_format_issue`
- `delivery_ack_issue`
- `approval_pattern_missing`
- `model_fallback_needed`
- `review_context_missing`
- `stalled_run`
- `unknown_controller_failure`

## Remediation Rules

Examples:

- `marker_format_issue`
  - normalize known marker variants
  - retry parse once

- `delivery_ack_issue`
  - accept known `DELIVERY_ACK ... ; MARKER` variants
  - retry parse once

- `approval_pattern_missing`
  - if allowlist-safe and prompt matches a known write/trust shape, approve once
  - otherwise fail explicitly

- `model_fallback_needed`
  - select fallback model once

- `review_context_missing`
  - resend reviewer prompt with artifact path and inspection guidance

- `stalled_run`
  - close run with failure after timeout budget is exhausted

## Guardrails

- Supervisor remediation must be bounded.
- Maximum one remediation per failure class per role per run.
- Supervisor never edits repo files directly.
- Supervisor never commits.
- Supervisor never turns into a free-form orchestration agent.
- Live supervisor state never contains pane captures.
- Live observation never sends keyboard input.
- A missing or unknown remediation mapping fails closed.

## Provider Rate-Limit Capability Matrix

This matrix describes only UI contracts currently recognized by Gobabygo. It
does not claim that providers expose equivalent limit semantics.

| Provider/UI state | Exact evidence required | Automated action | Timing authority |
| --- | --- | --- | --- |
| Claude session limit | Current banner with reset minute, IANA timezone, `/upgrade` line, valid Claude process and unchanged pane | Persist one schedule; after reset plus 90 seconds, recapture and send one guarded wake. An unchanged pending coordinator composer permits at most three Enter-only attempts total, at least four minutes apart (normally the next five-minute managed tick); an empty composer remains one-shot | Parsed vendor banner and persisted `not_before` only |
| Claude interactive rate menu | Current complete menu with `Stop and wait for limit to reset` visibly selected | Persist one-attempt tombstone, send one Enter, recapture | No clock calculation; current menu selection only |
| Codex account/rate exhaustion | Current Codex screen classified `rate_limit` | Warning and declared provider substitution only | None; automatic wake is unsupported |
| Antigravity account/rate exhaustion | Current AGY screen classified `rate_limit` | Warning and declared provider substitution only | None; automatic wake is unsupported |

The Antigravity experience survey is not a rate limit. Its exact `[0] Skip`
remediation remains a separate one-key UI rule.

### Scheduling Invariants

- Coordinator prose, transcript timestamps, activity age, and another provider's
  behavior cannot create or change a reset schedule.
- A persisted schedule is scoped to owner, session, pane, exact reset label,
  timezone, and pending-composer fingerprint. Changed current evidence is a new
  observation, never proof that the old schedule succeeded.
- Reaching `not_before` authorizes only a fresh guarded attempt. Provider
  availability and task resumption require post-input screen evidence.
- A provider without a parsed reset contract remains blocked. The coordinator
  may declare and use a different authorized worker, but cannot guess a time,
  send blind Enter, resend a task, or rotate the limited session automatically.

## Outputs

Add supervisor report fields:

- `supervisor_status`
- `supervisor_failure_class`
- `supervisor_remediation`
- `supervisor_attempts`

## Success Criteria

- Known protocol drift is classified instead of reported as a generic timeout.
- Repeated controller failures stop with explicit reason codes.
- Runs distinguish:
  - product failure
  - worker failure
  - controller/supervisor failure
