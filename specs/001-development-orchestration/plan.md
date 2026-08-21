# Implementation Plan: Development Orchestration Ledger

**Branch**: `master` | **Date**: 2026-08-21 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-development-orchestration/spec.md`

## Summary

Add a thin, deterministic GitHub ledger adapter around official Spec Kit
artifacts. A committed feature binding gives each `Tnnn` task a stable composite
identity. Local commands parse and plan without mutation; GitHub Actions checks
pull requests and applies one-way reconciliation on the default branch. Mesh
exposes the same adapter to coordinators without creating a second database or
making local hooks authoritative.

## Technical Context

**Language/Version**: Python 3.11 and POSIX shell

**Primary Dependencies**: Python standard library, existing PyYAML runtime, `gh` CLI for authenticated GitHub operations

**Storage**: Committed Spec Kit Markdown and one small feature binding; GitHub Issues as derived ledger

**Testing**: pytest unit, contract, subprocess-adapter, workflow static tests, controlled GitHub E2E

**Target Platform**: macOS operator client, Linux/Dell development host, GitHub Actions

**Project Type**: Existing CLI/orchestration repository

**Performance Goals**: Plan hundreds of tasks in under one second locally; avoid GitHub mutation when aligned

**Constraints**: KISS, one-way sync, fail closed, no new daemon/database, no local-hook remote writes, no iTerm2/router dependency

**Scale/Scope**: Multiple repositories and features; up to 1,000 issues per opted-in feature

## Constitution Check

The checked-in constitution is an uninitialized Spec Kit template and defines
no enforceable project gates. This plan therefore applies established Gobabygo
constraints: minimal moving parts, explicit mutation, deterministic structured
output, strict repository binding, tests proportional to remote side effects,
and independent review. No exception is required.

## Source Of Truth Contract

| Concern | Authority | Derived view |
|---|---|---|
| Feature intent and acceptance | `spec.md` and `plan.md` in Git | Issue/PR links |
| Task identity and completion | `tasks.md` in Git | GitHub Issues |
| Shared implementation discussion | GitHub Issue/PR comments | Coordinator summaries |
| Code validity | CI and reviewed commit range | Worker reports |
| Live process state | tmux/Mesh Live | Coordinator board |
| Managed execution lease/history | Router DB, when used | GitHub issue references |

No consumer may read GitHub issue state as authority for rewriting `tasks.md`.

## Design Decisions

1. **Opt in per feature**: only a feature with a committed binding is synchronized.
2. **Persist identity**: the binding carries an immutable opaque feature ID and exact `owner/repo`; directory display names may change.
3. **Mark every issue**: a versioned HTML comment stores repository, feature, and task identity; labels only narrow queries and are not identity.
4. **Plan before apply**: parsing and reconciliation produce an ordered action plan before any mutation. Blocking drift yields no writes.
5. **One-way state**: pending tasks require open issues; completed tasks require closed issues. Manual contrary changes are drift.
6. **Do not delete tasks**: published task IDs are append-only. An orphan blocks synchronization until its original task ID is restored as completed or explicitly cancelled.
7. **Do not sync Projects**: native GitHub Project auto-add rules consume labeled issues without another API integration.
8. **Keep upstream available**: official `speckit-taskstoissues` remains installed for compatibility, but the authoritative path uses the deterministic adapter.
9. **Detect legacy output**: an unmarked issue with an official `Tnnn: ...` title matching the current feature is blocking drift and requires explicit operator migration.
10. **Normalize comparisons**: line endings, trailing whitespace, title spacing, and label ordering are canonicalized before deciding that an update is needed.
11. **One remote writer**: only the repository-serialized GitHub Actions workflow applies plans. Local commands plan/check; operators request recovery through `workflow_dispatch`.

## Project Structure

```text
scripts/
├── mesh_speckit_github.py       # parser, planner, GitHub adapter, CLI
├── mesh_speckit_cli.py          # existing Spec Kit lifecycle
└── mesh                         # command dispatch

tests/
├── test_mesh_speckit_github.py
├── test_mesh_speckit_shell.py
└── test_mesh_speckit_docs.py

.github/workflows/
└── speckit-ledger.yml

specs/<feature>/
├── spec.md
├── plan.md
├── tasks.md
└── github-ledger.json           # committed opt-in and immutable identity
```

**Structure Decision**: One standalone Python module keeps pure parsing and
planning testable while isolating `gh` subprocess calls. Existing `mesh speckit`
dispatch remains the operator entry point. No package or service is added.

## Failure And Security Model

- Validate all paths under the exact Git root; reject symlinks escaping it.
- Parse only strict checkbox task lines and reject duplicate task IDs.
- Validate GitHub remote and manifest repository before network mutation.
- Treat all GitHub bodies and JSON as untrusted bounded input.
- Fetch all feature-labeled open and closed issues and reject duplicate markers.
- Serialize the ledger workflow per repository and re-read state immediately before apply.
- Give PR checks `contents: read` and `issues: read`; give default-branch/manual-dispatch apply `contents: read` and `issues: write` only.
- Never include environment values, pane captures, task comments, or secrets in issue bodies.
- Require `gh` 2.40 or newer and consume only allowlisted JSON fields from `gh api`.
- A partial GitHub failure stops subsequent mutation and leaves an auditable Action log; serialized replay reconciles idempotently.

## Verification Strategy

1. Pure parser and planner fixtures cover valid tasks, duplicate IDs, malformed bindings, same `T001` across features, rename, update, close/reopen, duplicate markers, and orphans.
2. Fake `gh` contract tests prove argument boundaries, repository checks, pagination/limits, and mutation ordering.
3. Workflow tests assert event-specific permissions and concurrency.
4. A disposable opted-in feature performs create, update, complete, and replay against GitHub; the canary issue is closed and retained as audit evidence.
5. A final independent review evaluates the exact commit range before push/activation.

## Complexity Tracking

No constitution violation or additional infrastructure is introduced.
