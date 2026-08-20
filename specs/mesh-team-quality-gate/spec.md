# Mesh Team Product Quality Gate

## Purpose

Add a small product-quality gate to the local CLI team workflow so a run cannot be reported as successful only because the file exists and smoke tests pass.

This spec does not redesign mesh routing. It extends the existing iTerm Speckit team cycle with a reviewer score, a bounded worker retry, and explicit final reporting.

## Problem

The current team cycle validates technical completion:

- role markers were emitted
- policy scanner is clean
- optional smoke test passed
- optional quick quality evidence passed

That can still produce a poor UI/game/toy. A reviewer can say the work is ready without a machine-readable score, and the controller has no threshold or retry rule.

## Scope

In scope:

- Product reviewer output contract.
- Parser for `PRODUCT_REVIEW`.
- CLI flags for product gate thresholds.
- At most N product-quality retries.
- Handoff/report fields that distinguish technical status from product status.
- Unit tests for parser, retry decisions, and flag plumbing.

Out of scope:

- New router APIs.
- New model selection system.
- Computer-vision screenshot scoring.
- Infinite critique loops.
- Automatic commits.

## Reviewer Contract

When product quality is enabled, the reviewer must emit:

```text
PRODUCT_REVIEW status=pass|retry score=0..10 visual=0..10 interaction=0..10 clarity=0..10 technical=0..10
FEEDBACK: concise actionable feedback
```

`status=pass` is accepted only when `score >= min_product_score`.

If parsing fails, the controller treats the product review as failed/retryable.

## Controller Rules

- `--product-quality` enables the gate.
- `--min-product-score N` sets the pass threshold. Default: `7`.
- `--max-quality-retries N` caps worker retry attempts. Default: `0`.
- The retry prompt includes only the reviewer feedback and the same edit allowlist.
- No loop can exceed `max-quality-retries`.
- Final report must include:
  - `product_quality_status`
  - `product_score`
  - `product_review`
  - `product_retry_count`

## Success Criteria

- A technically passing but low-scoring artifact returns `run_status: failed` after retries are exhausted.
- A low-scoring artifact gets at most the configured number of worker retries.
- A high-scoring artifact returns `product_quality_status: passed`.
- Existing non-product-quality runs keep the current behavior.
