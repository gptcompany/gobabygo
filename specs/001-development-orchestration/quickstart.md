# Quickstart: Development Orchestration Ledger

The final operator path will be:

```bash
# Validate and preview locally; never mutates GitHub.
mesh speckit github plan specs/001-development-orchestration

# Compare against GitHub; never mutates GitHub.
mesh speckit github check specs/001-development-orchestration

# Request a serialized authoritative reconciliation.
gh workflow run speckit-ledger.yml -f feature=specs/001-development-orchestration
```

Normal operation does not require the final command. Pull requests run the
read-only check; a merge to the default branch performs the one-way apply.
`mesh` intentionally has no local remote-write switch.
