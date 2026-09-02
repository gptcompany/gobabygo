# Spec Kit 1.0.3 Runtime Canary

Date: 2026-09-02

## Mac

- Installed exact official tag `v1.0.3` through the reviewed Mesh install plan.
- Gobabygo project reconciled through non-forced official integration commands.
- `specify integration status`: `OK`; default and sole integration: `claude`.
- Focused lifecycle/coordinator suite after generated changes: 104 passed.

## Dell 7670

- Clean `gobabygo-runtime` fast-forwarded over HTTPS to `b099501`.
- Installed exact CLI version `1.0.3`; the non-interactive follow-up uses
  `/home/sam/.local/bin/specify` rather than relying on shell startup files.
- Runtime and project status: required, installed, and manifest all `1.0.3`;
  aligned and orchestration runtime trusted.
- `specify integration status`: `OK`; no modified, missing, invalid, or unchecked
  managed files.
- A real coordinator contract rendered on the Dell reported Claude plus all ten
  expected workflow capabilities and `update_available=no`.
- Runtime worktree remained clean and equal to `origin/master`.

No operational worker session, coordination repository, or downstream project
was mutated during this canary.

## Rollback Boundary

- Last pre-rollout Gobabygo runtime revision: `ba6994df079cf59db4aaa67381dac7d046f2d0bd`.
- Last revision before generated project integration changes:
  `3b15830f71864995bf362925b116f3ef46b380a5`.
- Exact CLI downgrade, if the runtime is first redeployed from the reviewed
  pre-rollout revision:

  ```bash
  uv tool install --force specify-cli \
    --from git+https://github.com/github/spec-kit.git@v0.16.5
  ~/.local/bin/specify check
  ```

Rollback is a separate reviewed deployment: create or select a clean checkout
at the immutable pre-rollout revision, run its pinned install plan, verify
`required=installed=manifest=0.16.5`, and then switch the runtime entry point.
Do not use `git reset --hard`, downgrade only the CLI under 1.0.3 code, or apply
this procedure inside an operational or dirty repository.
