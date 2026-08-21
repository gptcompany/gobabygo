# Data Model: Development Orchestration Ledger

## Feature Binding

| Field | Rules |
|---|---|
| `schema` | Exact supported schema identifier |
| `feature_id` | Immutable lowercase opaque ID, 8-40 characters |
| `repository` | Exact lowercase GitHub `owner/repo` |
| `enabled` | Must be `true` for synchronization |

The binding is committed inside the feature directory and moves with that
directory. Changing `feature_id` after publication is a migration and is not
performed automatically.

## Spec Task

| Field | Rules |
|---|---|
| `task_id` | `T` followed by at least three digits; unique in the feature |
| `completed` | Derived only from `[x]` or `[X]` in `tasks.md` |
| `description` | Non-empty bounded single-line Markdown text |
| `story` | Optional `[USn]` marker |
| `parallel` | Optional `[P]` marker |
| `line` | Source line for diagnostics only |

## Task Key

`<repository>:<feature_id>:<task_id>` is the permanent identity. Titles, descriptions,
directory names, labels, issue numbers, and GitHub Project cards are mutable
attributes and never identity.

## Ledger Issue

The machine-owned issue body contains a schema marker, task key, source path,
and bounded task metadata. Human discussion belongs in comments so body
reconciliation does not erase it.

## Reconciliation Action

Actions are ordered by task ID and are one of `create`, `update`, `close`,
`reopen`, or `noop`. Duplicate identities, orphan identities, matching legacy
issues without markers, malformed remote markers, and repository mismatch are
blocking drift rather than actions.
