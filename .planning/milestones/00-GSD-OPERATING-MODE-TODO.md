# GSD Operating Mode TODO

## Purpose

This file defines the lightweight execution method for non-trivial work in `gobabygo`
without turning the repo into a general governance dump.

It is intentionally marked `TODO` because it is a working operating note, not a
frozen project milestone.

## Scope

Use this method only when work is:

- cross-file
- risky
- business-relevant
- likely to need rollback or explicit verification
- likely to involve multiple agents or handoffs

Do not use this process for trivial edits.

## Rules

1. Keep one execution SSOT per task.
2. Use one branch per step.
3. Keep scope narrow and auditable.
4. Do not mix unrelated docs, runtime, and tooling changes.
5. For high-risk work, execute one step at a time.

## What Belongs In This Repo

Keep only execution-bound artifacts in `gobabygo`:

- task plans
- task summaries
- task verification notes
- runbooks specific to this repo

Do not store broad cross-repo governance or reusable global agent policy here.

## Branch Pattern

Preferred branch names:

- `gsd/phase-XX-short-name`
- `fix/<scope>`
- `docs/<scope>`

## Commit Standard

Commits should be:

- small
- atomic
- easy to review
- honest about verification state

Do not claim a task is complete unless:

- code or docs are committed
- verification was actually run, or
- blocked verification is stated explicitly

## Verification Standard

Always separate these facts:

- code changed
- tests passed
- runtime healthy
- docs updated

A commit proves implementation changed.
A runtime check proves the system state.
They are not the same thing.

## Handoff Format

Every handoff should include:

- branch name
- commit hash
- short diff summary
- verification performed
- verification not performed
- next proposed step

## Execution Strategy

For `gobabygo`, prefer:

- a small task-specific plan in `.planning/phases/` only when the task is real
- no large framework-heavy milestone unless the work truly needs it
- direct, minimal planning tied to implementation

## Current Recommendation

If a future task is non-trivial:

1. create a branch for the step
2. add a small phase plan only if needed
3. implement the minimum viable change
4. verify
5. summarize
6. merge only after the scope is clean
