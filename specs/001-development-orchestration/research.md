# Research: Development Orchestration Ledger

## Official Spec Kit Capability

Spec Kit 0.16.5 ships `speckit-taskstoissues`. It correctly reuses the active
feature resolver, checks task prerequisites, validates a GitHub remote, creates
issues through GitHub MCP, and now scans open and closed issues.

It is retained, but it is not the authoritative synchronizer because its issue
identity is the bare task ID (`T001`). Spec Kit restarts task numbering for each
feature, so two features in one repository can collide. Its interactive MCP
mutation also cannot serve as a deterministic pull-request check.

## Existing Local Automation

The operator configuration contains a mature `taskstoissues.py` with labels,
milestones, Projects, and bidirectional synchronization. Reusing it wholesale
would import more policy than required and would allow closed GitHub issues to
rewrite `tasks.md`, violating the selected authority model.

The implementation will reuse its proven `gh` CLI transport pattern and strict
dry-run expectation, while omitting milestones, direct ProjectV2 mutation,
branch suggestions, and reverse synchronization.

## GitHub Projects

Projects is a presentation layer. Native auto-add rules can select the
`speckit-task` label, so writing ProjectV2 cards directly would duplicate
GitHub's own functionality and add another partial-failure boundary.

## Decision

Use official Spec Kit artifacts and lifecycle, a project-local deterministic
adapter for namespaced reconciliation, and GitHub Actions as the only automatic
remote writer. Keep hooks validation-only and keep the router out of ledger
state.

