# GitHub Ledger Contract

## Feature Binding

```json
{
  "schema": "mesh.speckit.github-ledger.v1",
  "feature_id": "dev-orch-a1b2c3d4",
  "repository": "gptcompany/gobabygo",
  "enabled": true
}
```

Unknown fields and unsupported schema versions are rejected. The repository
must match the checked-out GitHub origin and, in Actions, `GITHUB_REPOSITORY`.

## Issue Marker

Every managed issue body starts with exactly one marker:

```text
<!-- mesh-speckit-task:v1 repo=gptcompany/gobabygo feature=dev-orch-a1b2c3d4 task=T001 -->
```

Identity comes only from this marker plus the manifest repository binding. Titles and labels are indexes and display
metadata. Multiple matching markers or multiple issues with the same task key
are blocking drift.

## Reconciliation Output

JSON output uses this top-level contract:

```json
{
  "schema": "mesh.speckit.github-plan.v1",
  "repository": "gptcompany/gobabygo",
  "feature_id": "dev-orch-a1b2c3d4",
  "tasks_file": "specs/001-development-orchestration/tasks.md",
  "aligned": false,
  "blocking": [],
  "actions": []
}
```

No action may be applied when `blocking` is non-empty. A successful replay on
an aligned ledger returns an empty mutation set.

Only the repository GitHub Actions workflow may apply a plan. Local callers and
pull-request jobs can produce or check plans but cannot request mutation.
