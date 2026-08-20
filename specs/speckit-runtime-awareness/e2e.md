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
