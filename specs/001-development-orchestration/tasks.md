# Tasks: Development Orchestration Ledger

**Input**: Design documents in `specs/001-development-orchestration/`

**Authority**: This file owns task identity and completion. GitHub Issues are a
derived shared ledger and never rewrite this file.

## Phase 1: Contract

- [x] T001 [US1] Formalize source-of-truth boundaries, immutable task identity, failure policy, and the reviewed implementation backlog in `specs/001-development-orchestration/`

## Phase 2: Local Model

- [x] T002 [US1] Implement and test strict feature binding, task parsing, immutable identity, and canonical issue rendering in `scripts/mesh_speckit_github.py`

## Phase 3: Reconciliation Model

- [x] T003 [US2] Implement and test mutation-free reconciliation planning, normalization, legacy detection, duplicate/orphan blocking, and idempotent replay in `scripts/mesh_speckit_github.py`

## Phase 4: GitHub Adapter

- [x] T004 [US2] Implement and test bounded `gh` discovery and Action-only mutation with strict repository, version, JSON, and refresh contracts

## Phase 5: Operator Interface

- [x] T005 [US3] Expose init, plan, and check through `mesh speckit github` with stable JSON and exit-code contracts and no local remote-write switch

## Phase 6: Authoritative Automation

- [x] T006 [US3] Add a least-privilege repository-serialized GitHub Action that checks pull requests and applies opted-in features only on the default branch or workflow dispatch

## Phase 7: Coordinator Contract

- [x] T007 [US3] Add immutable task keys and ledger behavior to the coordinator workflow projection, concise operator docs, legacy migration guidance, and documentation contract tests

## Phase 8: End-To-End Closure

- [x] T008 [US1] Run local smoke tests and controlled GitHub ledger E2E tests for create, update, completion, idempotent replay, and retained run evidence

## Phase 9: Real Orchestration Canary

- [x] T009 [US3] Execute one real, disposable, non-production micro-feature through Spec Kit specify/plan/tasks, GitHub issue publication, coordinator delegation, one writer branch and pull request, independent reviewer verdict, CI evidence, authoritative task completion, and issue closure; retain a concise evidence report and remove only disposable branches/worktrees

## Phase 10: Pinned Multi-Repo Rollout

- [x] T010 [US3] Publish a reusable Gobabygo ledger workflow, add a plan-first installer for a minimal per-repository caller pinned to an immutable reviewed Gobabygo commit, and canary the caller in one second repository before broader rollout

## Phase 11: Automatic Runtime Preflight

- [x] T011 [US3] Expose and test a bounded immutable Gobabygo runtime pin in `mesh speckit status` only for a clean checkout with the expected origin, and pass it through new and resumed coordinator startup

## Phase 12: Plan-First Repository Onboarding

- [x] T012 [US3] Update and test the coordinator contract so planned work automatically composes the existing caller installer and feature binding on a planning branch, fails closed on custom or ambiguous state, waits for ledger alignment, and keeps workers outside ledger ownership

## Phase 13: Operator Contract

- [x] T013 [US3] Align concise operator documentation and contract tests around one-command onboarding, planning-plane authority, recovery behavior, and explicit blocker states

## Phase 14: Fresh-Repository Canary

- [x] T014 [US3] Execute a real disposable repository from no caller or binding through one coordinator objective, automatic Spec Kit onboarding, GitHub issue publication, Antigravity writer, Codex read-only review, CI, authoritative task closure, and mutation-free replay; retain exact evidence

## Dependencies

- T002 depends on T001.
- T003 depends on T002.
- T004 depends on T003.
- T005 depends on T004.
- T006 depends on T004.
- T007 depends on T005 and T006.
- T008 depends on T006 and T007.
- T009 depends on T008.
- T010 depends on T009.
- T011 depends on T010.
- T012 depends on T011.
- T013 depends on T012.
- T014 depends on T013.

## Completion Rules

- Each task is committed separately after its focused tests pass.
- No task is complete based only on worker prose or an idle session.
- No remote mutation occurs from tests, pull-request checks, or local hooks.
- T008 requires idempotent replay evidence and an exact-range review.
- T009 cannot be satisfied by mocks, dry-runs, worker prose, or direct edits that bypass the coordinator/worker/reviewer path.
- T010 must not vendor the Python reconciler into target repositories or reference a mutable Gobabygo branch.
- T014 must start without a ledger caller or feature binding and must not rely on operator-executed onboarding commands after the coordinator objective is submitted.
