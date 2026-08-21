# Tasks: Development Orchestration Ledger

**Input**: Design documents in `specs/001-development-orchestration/`

**Authority**: This file owns task identity and completion. GitHub Issues are a
derived shared ledger and never rewrite this file.

## Phase 1: Contract

- [x] T001 [US1] Formalize source-of-truth boundaries, immutable task identity, failure policy, and the reviewed implementation backlog in `specs/001-development-orchestration/`

## Phase 2: Local Model

- [ ] T002 [US1] Implement and test strict feature binding, task parsing, immutable identity, and canonical issue rendering in `scripts/mesh_speckit_github.py`

## Phase 3: Reconciliation Model

- [ ] T003 [US2] Implement and test mutation-free reconciliation planning, normalization, legacy detection, duplicate/orphan blocking, and idempotent replay in `scripts/mesh_speckit_github.py`

## Phase 4: GitHub Adapter

- [ ] T004 [US2] Implement and test bounded `gh` discovery and Action-only mutation with strict repository, version, JSON, and refresh contracts

## Phase 5: Operator Interface

- [ ] T005 [US3] Expose init, plan, and check through `mesh speckit github` with stable JSON and exit-code contracts and no local remote-write switch

## Phase 6: Authoritative Automation

- [ ] T006 [US3] Add a least-privilege repository-serialized GitHub Action that checks pull requests and applies opted-in features only on the default branch or workflow dispatch

## Phase 7: Coordinator Contract

- [ ] T007 [US3] Add immutable task keys and ledger behavior to the coordinator workflow projection, concise operator docs, legacy migration guidance, and documentation contract tests

## Phase 8: End-To-End Closure

- [ ] T008 [US1] Run local and controlled GitHub E2E tests, independent review, residual-risk documentation, and final regression suites

## Dependencies

- T002 depends on T001.
- T003 depends on T002.
- T004 depends on T003.
- T005 depends on T004.
- T006 depends on T004.
- T007 depends on T005 and T006.
- T008 depends on T006 and T007.

## Completion Rules

- Each task is committed separately after its focused tests pass.
- No task is complete based only on worker prose or an idle session.
- No remote mutation occurs from tests, pull-request checks, or local hooks.
- T008 requires idempotent replay evidence and an exact-range review.
