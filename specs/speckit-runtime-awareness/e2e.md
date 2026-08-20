# Spec Kit Runtime Awareness E2E

Date: 2026-08-20
Reviewed commit: `363d1034dd1e`

## Local

- Focused runtime, Mesh Live, shell, docs, worker, template, cron, and hook
  suites: `319 passed`.
- Python compile, Bash/Zsh syntax, and `git diff --check` passed per task.

## Dell Canary

- Transport: VPN `10.0.0.2` became unresponsive during the first project init;
  the operation completed and validation continued over LAN `172.23.0.42`.
- Control checkout: `/data/sata/1TB/gobabygo-speckit-e2e-363d1034dd1e`.
- Isolated project: `/data/sata/1TB/speckit-canary-363d1034dd1e`.
- Coordination Git root: `/data/sata/1TB/coordination`.
- Official `specify` version: `0.16.5`, installed from the pinned official tag.
- Project state: aligned; integrations exactly `claude`, `codex`, `agy`.
- Common capabilities: `analyze`, `checklist`, `clarify`, `constitution`,
  `converge`, `implement`, `plan`, `specify`, `tasks`, `taskstoissues`.
- Writer and reviewer contexts used the same feature/artifact identity. Reviewer
  scope was the immutable range
  `c67db3d24dbbde7c5f98b8428e57e44eabde7d46..b5675bfe8e8593483cb62d718347643d903a6e79`.
- New and exact-resume startup paths both included bounded Spec Kit status;
  transport/tmux was mocked for this contract-only check.
- Two update checks overwrote one mode `0600` state file containing only
  `checked_at`, `html_url`, `published_at`, `tag`, and `version`. Latest was
  `0.16.5`.
- Global hook dispatcher installed at `/home/sam/.claude/hooks/pre-push`;
  `core.hooksPath` remains `/home/sam/.claude/hooks`, and the Nautilus local
  hook is executable. No push was performed.
- Read-only board observed 7 sessions. Session names before and after were
  identical; no pane input, attach, create, close, or kill operation ran.

## Rollback

- Pinned CLI: `uv tool uninstall specify-cli` removes the newly installed tool.
- Canary: remove only the two paths carrying suffix `363d1034dd1e` after their
  evidence is no longer needed; they are outside operational repositories.
- Coordination state: retain `/data/sata/1TB/coordination` once used. Before
  first use it can be removed only after confirming it is still an empty Git
  repository with no specs, decisions, or handoffs.
- Hook: replacing the dispatcher with the prior shim restores its former
  behavior (`python3 ~/.claude/hooks/pre-push-review.py`) but disables repository
  hook chaining. CI remains authoritative in either state.
- Runtime deployment is not part of this canary step; rollback of T010 is a
  clean detached checkout of the previously recorded runtime revision.

## Legacy Migration Addendum

Date: 2026-08-20
Reviewed range: `d1b64d1..11d8c16`

### Validation

- Final local focused Spec Kit/Mesh Live suite: `323 passed`.
- Dell migration unit/shell suite: `44 passed`; only pre-existing pytest
  cleanup permission warnings were emitted.
- Control checkout:
  `/data/sata/1TB/gobabygo-speckit-migrate-e2e-e04c79e7bde6`.
- Legacy canary:
  `/data/sata/1TB/speckit-legacy-canary-e04c79e7bde6`.
- Canary transitioned from `legacy` to `aligned` with integrations exactly
  `claude`, `codex`, and `agy` and ten common capabilities.
- Dry-run reported the legacy template update and preserved constitution; apply
  replaced the template only after explicit acceptance.
- Existing constitution, feature spec, tasks, and legacy Claude command remained
  byte-identical. Git status contained only expected generated integration files.
- Writer and immutable reviewer contexts used identical feature/artifact identity.
- Non-interactive SSH discovered `/home/sam/.local/bin/specify` version `0.16.5`.
- A real read-only Codex turn read the installed plan skill and returned the
  exact expected marker without changing Git state.

### Provider Limits

- Claude CLI and skill integration were discovered, but no model turn ran while
  the account was rate-limited until 16:40 Asia/Bangkok.
- AGY CLI `1.1.15` and its installed skill were verified. Headless `agy --print`
  attempted a command required by its inherited instructions and the wrapper
  denied it because print mode cannot prompt. The repository remained unchanged;
  global command permissions were not weakened to make the smoke pass.
- Interactive AGY worker operation remains covered by the existing Mesh Live
  worker path; fixing its separate headless permission policy is not part of
  Spec Kit project migration.

### Operational Repositories

- `UTXOracle`, `rektslug`, `nautilus_dev`, and `gobabygo-runtime` are detected as
  current legacy projects. `ccxt-data-pipeline` has historical Spec Kit commits
  but no current legacy artifacts, so the current checkout is `missing`.
- Rektslug dry-run: 33 additions, eight generated script/template updates,
  constitution preserved, no blocking collisions.
- Gobabygo runtime dry-run: 42 additions and no collisions or replacements.
- UTXOracle, CCXT, and Nautilus currently have dirty worktrees and must not be
  migrated until their active changes are resolved and committed.

## Migration Hardening Addendum

Date: 2026-08-20
Implementation range: `a93425d..917912e`

### Validation

- Final local focused Spec Kit/Mesh Live suite: `385 passed`.
- Dell focused CLI/shell/docs suite: `72 passed`; only the pre-existing pytest
  cleanup permission warnings under `/tmp` were emitted.
- Final control checkout:
  `/data/sata/1TB/gobabygo-speckit-fixes-e2e-917912e8fccd1b1fcc1fc8c77dc73dde84e4a699`.
- Final historical canary:
  `/data/sata/1TB/speckit-historical-canary-917912e8fccd1b1fcc1fc8c77dc73dde84e4a699`.
- The real pinned `specify 0.16.5` migration transitioned the canary from
  `legacy` to `aligned`, installed Claude/Codex/AGY integrations, preserved the
  historical constitution byte-for-byte, retained the legacy Claude command,
  and left the control checkout clean.
- Planning and apply bind generated paths to content digests while normalizing
  only valid allowlisted timestamp fields in upstream metadata.
- Source and target symlink components, ignored generated output, concurrent
  apply, project drift, ambiguous constitutions, and malformed metadata fail
  closed. Rollback remains protected from SIGINT/SIGTERM before the signal is
  re-delivered under the restored handler.

### Review Chain

- Claude reviewed `a93425d..fc0e332` and reported one high, five medium, and
  bounded low findings. Each correction was committed separately.
- A fresh read-only Codex review of the corrected range found one remaining
  medium issue in deferred signal exit semantics.
- The final read-only review of `0ee4691..917912e` reported no findings and
  verdict `ACCEPTABLE_TO_CLOSE`; its checkout remained clean.
- A second Claude pass could not run before closure because the account was
  rate-limited until 21:40 Asia/Bangkok. No pass was claimed for that attempt.
