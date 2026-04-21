# Mesh Controller Supervisor

## Purpose

Add a second control layer above the mechanical iTerm controller.

The controller stays deterministic and simple:

- open panes
- send prompts
- wait for markers
- auto-approve known prompts
- enforce turn and retry limits

The supervisor observes controller behavior and handles controller failure modes.

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

### Layer 2: Supervisor

Observes controller state and classifies anomalies.

Responsibilities:

- read pane dumps, handoffs, timeout context
- classify controller failure mode
- apply bounded remediation when known
- stop loops
- write explicit supervisor outcome

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

