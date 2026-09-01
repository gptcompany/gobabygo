# GoBabyGo Mesh Router -- Quick Start

## Prerequisites

- Python 3.11+
- `pip install -e .` (installs pydantic, requests, etc.)
- Recommended on operator hosts: `uv` (`curl -LsSf https://astral.sh/uv/install.sh | sh`)

## 1. Start the Router

```bash
MESH_DEV_MODE=1 python -m src.router.server
```

Default: `http://localhost:8780`. Override with `MESH_ROUTER_PORT`.

Dev mode disables auth -- no token needed for registration.

Session-first routing policy (optional):

```bash
MESH_DEFAULT_EXECUTION_MODE=session
MESH_SESSION_FALLBACK_TO_BATCH=0   # session-first hard (no batch fallback)
MESH_ENFORCE_SESSION_ONLY=1        # reject batch tasks/steps at API level
```

With `MESH_DEFAULT_EXECUTION_MODE=session`, tasks created without explicit `execution_mode`
default to interactive session workers.

Token bootstrap helper (router + workers + local operator env):

```bash
./scripts/set-mesh-token.sh --generate \
  --vps-host root@10.0.0.1 \
  --ws-host sam@10.0.0.2 \
  --router-url http://10.0.0.1:8780
```

iTerm2 auto-start (Mac `.112`, optional):

1. Create dotenv file for operator shell (`~/.mesh/.env.mesh`):

```bash
mkdir -p ~/.mesh
cat > ~/.mesh/.env.mesh <<'EOF'
MESH_ROUTER_URL=http://10.0.0.1:8780
MESH_AUTH_TOKEN=REPLACE_WITH_REAL_TOKEN
EOF
chmod 600 ~/.mesh/.env.mesh
```

2. In iTerm2 profile settings: General -> Command -> Command:

```bash
/media/sam/1TB/gobabygo/scripts/iterm-mesh-shell.sh
```

Every new tab in that profile opens in `gobabygo` with mesh env loaded.

Note: repository deploy templates already enable this policy in
`deploy/mesh-router.env` (`MESH_DEFAULT_EXECUTION_MODE=session`,
`MESH_SESSION_FALLBACK_TO_BATCH=0`, `MESH_ENFORCE_SESSION_ONLY=1`).

## 2. Start a Worker

```bash
MESH_WORKER_ID=ws-claude-work-01 \
MESH_CLI_TYPE=claude \
MESH_ACCOUNT_PROFILE=work \
python -m src.router.worker_client
```

The worker registers itself, then long-polls `/tasks/next` waiting for work.
Account routing matches by:
- exact `MESH_ACCOUNT_PROFILE == task.target_account`
- or capability allowlist from `MESH_ALLOWED_ACCOUNTS` (`account:<name>` / `account:*`).

## 2b. Start an Interactive Session Worker (Claude/Codex)

Use this for tmux-backed interactive sessions (human can attach via iTerm2/tmux).

```bash
MESH_WORKER_ID=ws-claude-session-01 \
MESH_CLI_TYPE=claude \
MESH_ACCOUNT_PROFILE=claude-primary \
MESH_EXECUTION_MODES=session \
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 \
python -m src.router.session_worker
```

Session workers persist session metadata/messages via router `/sessions/*`.
CLI approval prompts remain CLI-native (manual/yolo/etc.).

Operational note:
- session worker Unix user is policy-driven, not hardcoded
- default policy runs Claude sessions as `sam` and Codex sessions as `mesh-worker`
- provider/runtime state therefore must exist under the Unix user selected in `mapping/provider_runtime.yaml`
- `MESH_ALLOWED_WORK_DIRS` should include every repo root you expect workers to enter; payload `working_dir` outside those roots is now rejected by both session and batch workers

## 2c. Start an External Review Worker (Codex Verifier)

Use this worker to process tasks already in `review` state and call:
- `POST /tasks/review/approve`
- `POST /tasks/review/reject`

```bash
MESH_ROUTER_URL=http://localhost:8780 \
MESH_AUTH_TOKEN=... \
MESH_REVIEWER_ID=review-codex \
MESH_REVIEW_CLI_COMMAND="ccs codex --effort xhigh" \
MESH_ACCOUNT_PROFILE=review-codex \
python -m src.router.review_worker
```

## 3. Check Status with meshctl

```bash
python -m src.meshctl status
```

Output:

```
WORKERS
ID         MACHINE      TYPE     STATUS     LAST HB      TASKS
ws-claud   workstation  claude   idle       2s ago       -

QUEUE
Queued: 0 | Workers: 1
Uptime: 45s
```

## 4. Submit a Task via curl

```bash
curl -s http://localhost:8780/health | python -m json.tool
```

Tasks are inserted directly into the DB by the orchestrator. For manual testing,
use the smoke test below or insert via Python:

```python
from src.router.db import RouterDB
from src.router.models import Task

db = RouterDB("/var/lib/mesh-router/router.db", check_same_thread=False)
task = Task(title="Hello world", phase="implement",
            target_cli="claude", target_account="work",
            idempotency_key="manual-001")
db.insert_task(task)
```

The scheduler dispatches it to the next eligible idle worker.

Conservative stale-state cleanup from the operator host:

```bash
python -m src.meshctl cleanup stale-state
python -m src.meshctl cleanup stale-state --apply
python -m src.meshctl cleanup stale-state --include-taskless-sessions
```

Dry-run is the default. `--apply` creates a DB backup on the router before it
closes open sessions linked to terminal/missing tasks and reconciles thread rows
whose computed status is already terminal. Taskless open sessions are skipped by
default and require `--include-taskless-sessions`.

Who executes commands in mesh:
- `BOSS` (human operator): starts orchestration (`meshctl pipeline create`, manual task/thread API calls).
- `PRESIDENT` (logical coordinator): authors/supervises interactive prompts inside session workflows.
- `session workers`: execute CLI commands in tmux/upterm for `execution_mode=session`.
- `review worker`: approves/rejects critical tasks in `review` state.

Canonical template policy:

- built-in `gsd` and `speckit` now run as interactive teams, not mixed batch/session pipelines
- `lead` defaults to Claude for research, planning, artifact generation, and implementation
- `president` defaults to Codex for adjudication and review-heavy checkpoints
- `worker` sessions use Codex and Antigravity for challenge, analyze, verify, and validate steps
- `speckit_codex` remains the fallback template when Claude is unavailable
- `antigravity_team_demo` is the canonical third-provider smoke/demo template
- `antigravity_team_demo` writes `lead_plan.md`, `worker_review.md`, and `president_decision.md`, each with deterministic success markers and automatic session exit
- text-marker auto-exit without `success_file_path` is now opt-in only and only matches standalone marker lines, not arbitrary substrings printed by tools

Pipeline orchestration example (from BOSS terminal):

```bash
dotenvx run -f ~/.mesh/.env.mesh -- python -m src.meshctl pipeline create \
  --template gsd \
  --thread-name "gsd-phase-17" \
  --repo /media/sam/1TB/gobabygo \
  --phase 17 \
  --project "AI Mesh Router" \
  --feature "session-first hard mode"
```

Optional shortcut for macOS operator shell:

```bash
alias meshctlx='dotenvx run -f ~/.mesh/.env.mesh -- python -m src.meshctl'
```

One-time shell helpers (Mac/WS):

```bash
./scripts/install-shell-helpers.sh
source ~/.zshrc   # or source ~/.bashrc on bash hosts
```

This enables:
- `wss` / `wss <repo>` (quick SSH to WS using the reachable LAN/VPN fallback)
- `wboard [query] [lines]` (live tmux board via `mesh live`, router/iTerm2 independent)
- `wsupervisor [--json]` (fresh supervisor snapshot from the Dell; no pane input)
- `wpeek <tmux-session> [lines]` (read one live tmux pane)
- `wsend <tmux-session> [text] [--enter]` (literal send; Enter is always explicit)
- `wbrief [--repo <repo>|--all]` (redacted dynamic prompt for coordinator debate and delegation)
- `wsattach <tmux-session>` (persistent attach: direct mosh on VPN/LAN, SSH fallback)
- `mclaude <repo>` / `mcodex <repo>` / `mtmux <repo>` (create-or-attach a persistent named tmux shell in that repo)
- `mcoordinator [<repo>|--all] [--workflow direct|speckit|adaptive] [--worker <session>] [--continue|--resume <id>]` (create-or-attach an adaptive Claude coordinator; explicit post-reboot resume)
- `mesh` (global wrapper to `gobabygo/scripts/mesh`)
- `mesh` with no args (interactive current-repo-root launcher: `attach`, `sessions`, `ui`, `start`)
- `mesh ui <repo>` (comando canonico: apre il layout iTerm2 del repo; default minimale `boss,president`)
- `mesh ui attach <repo>` (riattacca il layout esistente del repo)
- `mesh ui close <repo>` (chiude il layout/backend group del repo)
- `mesh sessions [--all] [repo|session|role]` (comando principale: su TTY apre un picker gerarchico `Layout -> Panels`; scegli `Attach Layout`, `Attach Panel` o `Kill`; usa `--list` per la lista grezza)
- `mesh session list [--all] [repo|session|role]` (lista grezza router-backed, senza wizard)
- `mesh attach [repo|session|role]` (alias compatibile legacy: apre lo stesso picker ma forza `attach`)
- `yazi` / `lf` aliases to `yazicd` / `lfcd` (keep selected directory on exit)

Ultra-short operator commands:

```bash
mesh bootstrap
mesh deploy
mesh                          # interactive launcher for current repo
mesh status
mesh status --all               # show historical stale/offline workers too
mesh sessions                   # wizard: pick a layout or one of its panels, then attach/kill
mesh sessions snake-game        # same wizard, already filtered to target repo
mesh sessions --all             # same wizard, across repos
mesh sessions --list --all      # raw list across repos
mesh session list --all         # raw list across repos
mesh attach                     # legacy alias: picker + forced attach
mesh attach snake-game          # same, filtered to target repo
mesh ui rektslug               # open operator layout
mesh ui attach rektslug        # reattach existing operator layout
mesh ui close rektslug         # close existing operator layout
mesh term list --repo /media/sam/1TB/rektslug
mesh term focus /media/sam/1TB/rektslug boss
mesh term send /media/sam/1TB/rektslug boss "status?"
mesh term key /media/sam/1TB/rektslug boss enter
mesh term dump /media/sam/1TB/rektslug president --lines 30
mesh live board rektslug --lines 40
mesh live peek claude-rektslug 120
MESH_LIVE_LOCAL=1 mesh live ensure-codex /data/sata/1TB/rektslug
MESH_LIVE_LOCAL=1 mesh live ensure-antigravity /data/sata/1TB/rektslug
mesh live send claude-rektslug "status?" --enter
mesh live attach claude-rektslug
mesh live brief --repo rektslug
mesh live brief --all --coordinator claude-coordinator
mesh live workflow show speckit --json
mesh live workflow show speckit --scope coordinator --json
mesh speckit manual-actions /data/sata/1TB/coordination --all
MESH_LIVE_LOCAL=1 mesh live tick --json
mesh thread create --name rektslug-live-delegation
mesh thread add-step --thread rektslug-live-delegation --title "Implement fix" --step-index 0 --repo /data/sata/1TB/rektslug --cli codex --payload '{"prompt":"Implement the approved fix."}'
mesh start                      # one-command start (feature label auto-generated)
mesh run 016                    # existing spec/phase flow
mesh thread                     # show last thread for current repo
python -m src.meshctl task cancel <task-id> --reason "stuck queued"
python -m src.meshctl task fail <task-id> --reason "stuck review"
wss <repo>
wboard 40
wsupervisor
wpeek claude-rektslug 120
wsend claude-rektslug "status?" --enter
wbrief --repo rektslug
wsattach <tmux-session>
mcoordinator rektslug --worker codex-rektslug
mcoordinator --all
mcoordinator rektslug --workflow speckit
mcoordinator rektslug --workflow direct
mcoordinator --all --continue
mcoordinator --all --resume <claude-session-id>
```

`mesh` with no args now opens a small interactive launcher for the current repo root and routes to `attach`, `sessions`, `ui`, `start`, or `attach --all`. `mesh ui`, `mesh start`, `mesh run <phase>`, and `mesh thread` also resolve the git repo root when you launch them from a nested subdirectory. `mesh sessions` is the single primary session helper: on a TTY it opens the picker; use `mesh sessions --list` or `mesh session list` only when you want the raw router list. `mesh attach`, `mesh session manage`, and `mesh ui resume` remain only as compatibility aliases. `wsattach` remains a low-level fallback when you already know the tmux session name.

### Direct live tmux workflow

The concise, canonical operator path is [MESH_LIVE.md](MESH_LIVE.md). In short:

- tmux is authoritative for existing live sessions
- router DB is authoritative for durable managed orchestration
- iTerm2 is optional layout/UX
- `mcoordinator <repo>` is the default automatic intra-repo path; `mcoordinator --all` is multi-repo
- multi-repo state lives in the exact Git root
  `/data/sata/1TB/coordination` (override with absolute
  `MESH_COORDINATOR_STATE_REPO`); missing, nested, or non-Git paths fail before
  tmux attach or creation
- without an explicit override, Mesh Live assigns Antigravity as the sole writer
  and Codex in a different session as the primary read-only reviewer; all
  providers may still review when explicitly selected
- `adaptive` is the default workflow: direct for incidents/audits/narrow fixes,
  Speckit for features, architecture, ambiguity, and independent challenge
- in multi-repo coordinator scope, a multi-task handoff always keeps durable
  Spec Kit specification, decisions, dependencies, and tasks; urgent direct
  recovery lanes do not replace that program state
- each write delegation declares `TDD_MODE: required|recommended|not_applicable`;
  RED/GREEN evidence is required only for behavior changes where a focused test
  is feasible, and the same contract applies to Codex and AGY
- `mesh live workflow show speckit` projects the canonical
  `mapping/pipeline_templates.yaml` phases without router, tmux discovery, or iTerm2
- `mcoordinator <repo>` binds Speckit to that repository and derives the
  feature/task from the objective; `mcoordinator --all` keeps Speckit at
  coordinator/program scope and binds repo plus feature/task only per concrete
  delegation, so no single pair is required at startup
- for planned repository work, a clean trusted Gobabygo runtime lets the
  coordinator stage a missing managed caller and feature binding automatically
  in the planning PR; the operator still submits only the objective
- coordinator scope does not create a router pipeline: the router/database is
  optional persistence for selected tasks and handoffs
- Speckit live keeps one writer per repo; Codex/Antigravity challenger roles use
  different existing sessions when available, and missing perspectives are
  reported as degraded coverage rather than silently skipped
- code review is scoped to an exact commit range or recorded diff snapshot;
  findings must be severity-ordered with `file:line`, impact, evidence, missing
  tests, residual risks, and an explicit PASS or CHANGES_REQUIRED verdict
- workflow role boundaries are coordinator contract rules, not filesystem
  locks or an OS sandbox; YOLO workers retain their Dell user permissions
- only `ensure-codex` and `ensure-antigravity` may create a missing live worker;
  template roles do not authorize Claude spawn, nested AI launch, router use, or iTerm2
- the coordinator reuses existing workers and may bootstrap one deterministic
  `codex-<repo>` or `antigravity-<repo>` through the matching local-only ensure
  command; it does not ask for per-worker authorization
- after a reboot use explicit `--continue` or deterministic `--resume <id>`;
  the helper resumes Claude and appends the current Gobabygo system contract in
  the same startup command
- bootstrap and resume require exact contract and transactional-review markers
  from the Dell runtime before tmux access; update a stale runtime rather than
  silently starting with an older contract
- exact `--resume <id>` rejects the same UUID in another active tmux coordinator
  and holds a private per-UUID lock while Claude runs; `--continue` cannot offer
  this guarantee because its UUID is selected inside Claude after startup

- run `mcoordinator` from any Mac directory after installing the shell helpers; the CLIs stay on the Dell
- a running coordinator retains the contract from its initial bootstrap; start
  a new coordinator or exit/resume the old tmux session after deploying contract changes
- valid coordinator sessions are only attached; a shell wrapper with a Claude
  child is valid, while a marked coordinator or older unmarked shell without Claude fails
  closed, so inspect it with `wsattach` or use `--session <fresh-name>` for a
  non-destructive bootstrap
- set `MESH_COORDINATOR_MESH_SCRIPT` to a clean Dell runtime checkout; never use a dirty development checkout
- a misspelled repo fails before tmux creation; persistent helpers never fall back to the repo-base directory
- `wbrief --repo <repo>` is intra-repo; `wbrief --all` is multi-repo
- review with `wpeek` before every sensitive `wsend`; `--enter` is explicit execution
- `send` accepts one literal line only; store long/multi-line briefs in the
  target repo and send one line containing the delegation ID and absolute path;
  Codex sends also use `--delegation-id <id>` to create a metadata-only receipt
- if an exact Codex delegation or a recent receipt-matched
  `[Pasted Content N chars]` remains visibly unsubmitted after delivery, the
  coordinator may invoke `mesh live recover-codex-submit <session> <id>` after a
  fresh peek; the guarded command accepts no text, recaptures and validates the
  visible Codex composer, records the attempt before its single Enter, and polls
  briefly; `submission=unknown` requires follow-up peeks, not task resend
- an untracked, stale, mismatched, active, or ambiguous composer requires manual
  inspection; mesh live does not auto-clear it or send a naked Enter
- on the Dell runtime, `mesh live tick` is read-only and `tick --apply` only
  handles an exact selected Claude WAIT menu or wakes an exactly idle coordinator
- Claude reset timing comes only from an exact vendor minute/timezone persisted
  as `not_before`; Codex and AGY rate limits report `schedule_source=unsupported`
  and are never assigned guessed wake times. Passing `not_before` permits one
  guarded attempt and still requires fresh provider-state evidence
- before `TICK_IDLE` or closure, the coordinator runs `mesh speckit
  manual-actions <state-repo> --all --json`; unresolved `DEC-* [D]` entries are
  reported as `MANUAL_REQUIRED` with blocked task IDs and appear as a supervisor
  warning, never as silent idle state
- Claude prompt suggestions/ghost text are vendor-generated UI, not submitted
  operator authority. Disable them globally with
  `"promptSuggestionEnabled": false` in `~/.claude/settings.json`; restart or
  resume an already-running Claude session for the setting to take effect
- board reports provider `screen` state and tmux `activity_age`; completion needs
  the exact marker plus stable idle state in two observations, while age alone
  never triggers worker replacement
- install optional 30-minute polling with
  `./scripts/install-mesh-live-cron.sh --mesh-script "$PWD/scripts/mesh"`; it
  defaults to the current tmux owner and must not target router-managed owners
- tick state contains metadata only; use `install-mesh-live-cron.sh --remove`
  to remove its marked crontab block without touching unrelated entries

### Spec Kit project lifecycle

The Dell has one pinned `specify` CLI; each repository owns its Spec Kit
artifacts and integrations. Use `status` to choose exactly one lifecycle path:

```bash
mesh speckit status /data/sata/1TB/rektslug

# state=missing: new project
mesh speckit project init /data/sata/1TB/rektslug \
  --allow-multi-install-force

# state=legacy: inspect first, then explicitly accept generated template updates
mesh speckit project migrate /data/sata/1TB/rektslug \
  --allow-multi-install-force
mesh speckit project migrate /data/sata/1TB/rektslug \
  --allow-multi-install-force --accept-generated-updates --apply

# state=aligned: future pinned release
mesh speckit project upgrade /data/sata/1TB/rektslug --apply
```

Every mutation requires the exact clean Git root. Legacy migration generates
Claude, Codex, and AGY integrations in a temporary sandbox, preserves existing
`specs/` and `.specify/memory/constitution.md`, copies an older
`memory/constitution.md` into the current location, and refuses custom skill or
ignored-output collisions. Existing legacy `.claude/commands` are listed but
never deleted; archive them only after verifying that no operator still invokes
them. If both constitution paths exist, neither is overwritten and the old path
is reported as unmigrated. New-project `init` uses the same sandbox, lock, and rollback path. Review
and commit the resulting diff before migrating the next repository.
`~/.local/bin/specify` is discovered explicitly for non-interactive SSH
sessions; no shell startup file is required.

### Spec Kit development ledger

Prerequisite: authenticated GitHub CLI `gh` 2.48 or newer.

For planned features, Git owns intent and `tasks.md` owns task identity and
completion. GitHub Issues are a one-way derived ledger; issue state never
rewrites Spec Kit artifacts.

Mandatory operator decisions stay in the same ledger:

```bash
# One feature, or every feature under a repository.
mesh speckit manual-actions specs/001-feature
mesh speckit manual-actions /data/sata/1TB/coordination --all --json
```

Only open checklist entries with a `DEC-*` ID marked `[D]` are projected. A
submitted operator answer is recorded back in the authoritative decision/task
artifact before dependent work continues. Silence, pane text, worker prose, and
Claude prompt suggestions are never consent.

```bash
# Once per feature: inspect, then commit the immutable binding with planning artifacts.
mesh speckit github init specs/001-feature
mesh speckit github init specs/001-feature --apply

# Read-only publication plan. Blocking drift returns 2.
mesh speckit github plan specs/001-feature

# After the planning PR merges and the Action runs: 0 means aligned, 1 means drift.
mesh speckit github check specs/001-feature
```

The planning PR contains specification, plan, tasks, and
`github-ledger.json`, but no source implementation. Its pull-request job checks
that reconciliation is safe without writing issues. After merge, the
repository-serialized Action creates or updates issues using the immutable
`<owner/repo>:<feature-id>:<Tnnn>` key. Implementation starts only after
`check` is aligned. Task completion follows the same direction: reviewed
evidence updates `tasks.md`; the next Action run closes the issue.

Do not use the official interactive `speckit-taskstoissues` command as this
authoritative path: it remains installed for upstream compatibility but its
bare `Tnnn` deduplication can collide across features. Local hooks validate
only. GitHub Projects should auto-add the `speckit-task` label instead of using
a second sync integration.

Gobabygo initially owns the implementation and canary. Other repositories must
not copy the Python reconciler or reference a mutable branch; install their
minimal caller only after the pinned reusable-workflow rollout is reviewed.

```bash
# Read-only plan, then explicit local file creation.
mesh speckit github install-caller /path/to/repo --runtime-ref <40-char-sha>
mesh speckit github install-caller /path/to/repo --runtime-ref <40-char-sha> --apply
```

The installer creates only `.github/workflows/speckit-ledger.yml`, uses the same
immutable SHA for the reusable workflow and its runtime, and refuses to replace
different existing content. The generated path filters include that workflow,
so a caller upgrade validates itself on its pull request. For private
repositories, allow the caller to use
Gobabygo reusable workflows in the repository Actions settings before canarying.
To advance an existing generated caller after reviewing a new runtime commit,
add `--accept-pin-update`; the prior managed template is migrated, while custom
workflow content is never overwritten.

Normally the operator does not run these onboarding commands. A newly created
or resumed `mcoordinator <repo>` receives the clean runtime SHA in its bounded
startup status, dry-runs the same installer and binding commands, and adds only
the managed caller plus Spec Kit artifacts to a non-default planning branch.
Custom workflow content, a dirty/unpublished runtime, malformed repository
state, unavailable `gh`, or a failed Action is an explicit blocker. Workers do
not own onboarding or issue mutation.

### Transactional Spec Kit review

For a planned, GitHub-bound task, use the feature-local review ledger rather
than reconstructing review rounds from chat history:

```bash
# Read the global CAS revision first. Status never writes.
mesh speckit review status /path/to/repo specs/001-feature --json

# Freeze one task cycle. Repeat --invariant for each critical invariant.
mesh speckit review init /path/to/repo specs/001-feature T001 \
  --scope commit:<writer-sha> --writer-session agy-repo \
  --invariant "release cannot bypass the gate" --mutation-budget 1 \
  --expect-revision <revision>

# Register the independent reviewer before dispatch.
mesh speckit review open /path/to/repo specs/001-feature T001 \
  --level RELEASE --scope commit:<writer-sha> \
  --reviewer-session codex-repo --delegation-id <id> \
  --expect-revision <revision>

# Save the exact reviewer output as a non-secret feature report, then record it.
mesh speckit review record /path/to/repo specs/001-feature T001 \
  --verdict PASS --evidence-file specs/001-feature/review-T001.md \
  --mutations-run 1 --expect-revision <revision>
```

Every mutation performs a global revision compare-and-swap under a Git-internal
`flock`, validates the closed FSM, fsyncs a same-directory temporary file, and
atomically replaces `review-ledger.json`. A stale revision, self-review,
mutable scope, duplicate review, invented invariant, mutation-budget overflow,
blocking PASS, third correction, or unsafe BACKLOG is rejected without changing
the ledger.

`open` also returns a canonical review deadline from the repository policy.
After that deadline, and only when no valid final marker exists, the coordinator
may transact `timeout --expect-revision <revision>`. The first timeout permits
one fallback on a different reviewer session for the same scope; the second
timeout moves the task to `ESCALATED`. Timeout is never PASS or consent, and the
periodic tick only wakes the coordinator to make this ledger-backed decision.

On failure, `correction` increments the persisted round; `decide` may stop and
REPLAN or ESCALATE immediately. After a DELTA PASS, `candidate` must freeze a
new full candidate before INVARIANT or RELEASE review. Only ledger status
`RELEASE_PASSED` satisfies the review gate. It remains separate from task
completion and from merge, push, deploy, or money-path authorization. The
ledger/report files use the repository's normal authorized Git flow; the CLI
does not perform those operations.

```bash
mesh speckit review check /path/to/repo specs/001-feature T001 \
  --scope commit:<current-writer-sha>
```

`check` exits `0` only when the supplied immutable scope is the frozen
`RELEASE_PASSED` candidate, `1` for its valid unsatisfied gate, and `2` when the
ledger, task, scope, or command is invalid or stale.

### Git hook chaining

Git supports one effective `core.hooksPath`. On the Dell, keep the global value
at `~/.claude/hooks`; do not run `git config core.hooksPath .githooks` inside a
repository because that silently disables the global review.

Install or inspect the Gobabygo dispatcher explicitly:

```bash
./scripts/install-mesh-git-hook.sh
./scripts/install-mesh-git-hook.sh --apply
git config --global --get core.hooksPath
```

The dispatcher replays the exact pre-push stdin and arguments first to
`~/.claude/hooks/pre-push-review.py`, then to an executable
`<repo>/.githooks/pre-push` when present. A failure in either check blocks the
push; unknown existing global hooks are not replaced without explicit
`--replace`. This is local feedback only: CI remains the authoritative gate, and
Git hooks are not a security boundary because users can bypass them.

Current canonical UI slice:

- default layout is intentionally minimal: `boss`, `president`
- `mesh ui <repo>` opens a fresh layout for that repo
- `mesh ui attach <repo>` reattaches the existing layout
- `mesh ui close <repo>` closes the existing layout/backend group
- on iTerm2 `3.6.9`, the launcher uses safe `tabs-only` mode by default to avoid `splitPane` crashes
- `boss <-> president` runtime relay is still under active hardening; do not treat that behavior as closed until a live smoke confirms president receives the boss summary

Matrix room inbound commands:

```text
!mesh approve <task-id-prefix>
!mesh reject <task-id-prefix> <reason>
!mesh send <session-id-prefix> <text>
!mesh enter <session-id-prefix>
!mesh interrupt <session-id-prefix>
```

The Matrix bridge resolves task/session prefixes against the router API/DB, scoped to the repo room when the room is mapped in topology.

`mesh bootstrap` now:
- keeps worker envs simple; runtime command resolution is policy-driven via `mapping/provider_runtime.yaml`
- enables `MESH_ALLOWED_ACCOUNTS=*`
- wires `MESH_UPTERM_BIN` automatically when `upterm` exists on WS
- normalizes `/home/mesh-worker/.ccs` and `/home/mesh-worker/.claude` ownership
- links `ccs` into `/usr/local/bin/ccs` when needed
- restarts session workers
- relies on session workers to preseed Claude repo metadata (`.claude.json`) at task start

Provider runtime policy:

```text
mapping/provider_runtime.yaml
```

Default behavior:
- `claude` -> real CCS account profile: `ccs {target_account}`
- `codex` -> provider direct: `ccs codex`
- `antigravity` -> native CLI: `/home/sam/.local/bin/agy`
- Claude session worker service user -> `sam`
- Antigravity session worker service user -> `sam`

Operator UI policy:

```text
mapping/operator_ui.yaml
```

Default behavior:
- `mesh ui` bootstraps each pane through `scripts/mesh_ui_role_shell.sh`
- `mesh ui` now auto-attaches role panes to matching live tmux sessions when the router already has an open session for the same repo/role
  - example: an active `lead` Codex step on repo `X` opens directly inside the `lead` pane
  - if no live session matches, the pane falls back to the role's configured CLI bootstrap instead of a detached control shell
  - live attach resolution is performed again on the WS during pane bootstrap, so it still works even when the Mac host cannot reach the router directly
- `mesh thread` without an explicit thread name now resolves the latest thread from router task metadata for the current repo path; it no longer depends on the thread name prefix matching the repo basename
- each role can run a different provider or remote init command
- the policy is user-editable in one file instead of being hardcoded or split across env vars
- Codex session worker service user -> `mesh-worker`

Override/disable:
- `MESH_PROVIDER_RUNTIME_CONFIG=/abs/path/file.yaml`
- `MESH_PROVIDER_RUNTIME_CONFIG=""`

`mesh thread` resolves latest thread from router (`GET /threads`), not from local state files.
`mesh` auto-discovers router env in this order:
1. shell env (`MESH_ROUTER_URL`, `MESH_AUTH_TOKEN`)
2. `~/.mesh/.env.mesh`
3. `~/.mesh/router.env`
4. `/etc/mesh-worker/common.env` (WS shared fallback)
5. `/etc/mesh-worker/*.env` (legacy WS fallback)

Recommended WS runtime split:

- shared values in `/etc/mesh-worker/common.env`
- batch defaults in `/etc/mesh-worker/mesh-worker.batch.common.env`
- interactive session defaults in `/etc/mesh-worker/mesh-session.common.env`
- instance-specific values in `/etc/mesh-worker/<instance>.env`
- service units load role-specific `*.common.env` before the instance env and `common.env` after it
- shared live overrides still win for:
  - `MESH_ROUTER_URL`
  - `MESH_AUTH_TOKEN`
  - `MESH_ALLOWED_WORK_DIRS`
- the checked-in `deploy/mesh-*.env` templates now intentionally omit both the shared router keys
  and the repeated batch/session defaults

Examples:

```bash
# once after deploy/config drift
mesh bootstrap
mesh deploy

# from inside /media/sam/1TB/rektslug
mesh start
mesh thread

# existing numbered phase flow
mesh run 016
mesh thread
```

If `mesh deploy` chooses wrong host mode:

```bash
MESH_DEPLOY_MODE=remote mesh deploy
```

WS host override:

```bash
MESH_WS_HOST=sam@192.168.1.111 mesh deploy
```

iTerm2 Python API setup (Mac only, one-time):

```bash
pip3 install iterm2
mesh ui rektslug --max-panes-per-tab 5
```

`mesh ui` now auto-falls back to `uv run --with iterm2 ...` if module `iterm2`
is missing and `uv` is available.
By default it replaces previous mesh-ui tabs to avoid tab accumulation.

From WS/Linux, `mesh ui ...` auto-forwards to Mac operator host by default
(`MESH_UI_FORWARD_HOST=sam@192.168.1.112`).

If Claude is disabled, switch to codex-only pipeline:

```bash
export MESH_PIPELINE_TEMPLATE=speckit_codex
```

For third-provider smoke/demo tests, use Antigravity:

```bash
export MESH_PIPELINE_TEMPLATE=antigravity_team_demo
mesh start "snake game demo"
```

Canonical E2E smoke expectation:
- step 0 (`lead`) writes `lead_plan.md` with `ANTIGRAVITY_LEAD_OK`
- step 1 (`worker`) writes `worker_review.md` with `ANTIGRAVITY_WORKER_OK`
- step 2 (`president`) writes `president_decision.md` with `ANTIGRAVITY_TEAM_OK`
- each Antigravity session auto-exits when its expected file marker is present

If you need explicit path/name mode:

```bash
mesh run rektslug 016
mesh run /media/sam/1TB/rektslug 016
```

UV-first execution:
- `scripts/mesh` now prefers `uv run -- python -m src.meshctl ...` when `uv` is available.
- fallback remains plain `python3/python` if `uv` is not installed.

CCS profile isolation (recommended for account-scoped history/context):

```bash
ccs auth create claude-samuele
ccs auth create claude-gptprojectmanager
```

Then set Claude task accounts to those real CCS profiles and keep them `isolated`.
Default account selection is now controlled centrally in:

- `mapping/account_pools.yaml`
- `mapping/provider_runtime.yaml`

Bootstrap also reads `mapping/provider_runtime.yaml` to install per-instance
systemd overrides for session worker Unix users.

Interactive task example (`execution_mode=session`):

```bash
curl -s -X POST http://localhost:8780/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Interactive refactor with human oversight",
    "target_cli": "claude",
    "target_account": "claude-samuele",
    "execution_mode": "session",
    "payload": {"prompt": "Refactor auth module safely and ask before risky commands"}
  }'
```

Inspect sessions/messages:

```bash
curl -s http://localhost:8780/sessions | python -m json.tool
curl -s "http://localhost:8780/sessions/messages?session_id=<SESSION_ID>" | python -m json.tool
```

During execution, `session_worker` appends incremental CLI output to `/sessions/messages`
(`direction="out"`, `role="cli"`) using tmux pane deltas/snapshots, so operators can tail
progress without attaching immediately.

Verified live behavior on the current stack:
- router dispatches to real tmux-backed session workers
- repo `working_dir` is honored when the path is correct
- long-lived interactive sessions now renew leases on heartbeat and are not requeued after the 5-minute lease window
- real account-scoped Claude CCS profiles are supported (`ccs <profile>`, not `ccs claude`)
- Claude limit recovery now rotates across those isolated profiles on retry when worker output matches `429`, `You've hit your limit`, `You're out of extra usage`, or `rate limit error`
- Codex/Antigravity quota detection feeds the same `account_exhausted` retry path when output matches provider-specific rate-limit or quota strings
- scheduler dispatch now requires a fresh worker heartbeat before leasing work to an `idle` worker
- Docker router reachability is controlled by `MESH_ROUTER_BIND_HOST` in `deploy/compose.yml`; for multi-host WS/router setups it must not stay pinned to `127.0.0.1`

Known operational gaps:
- `upterm` is installed on WS; attach URL discovery is implemented in code, but if workers still log `upterm binary not found ...` the running service has not picked up the latest code yet
- brand-new CCS profiles still require one real login/bootstrap under the Unix user that runs that provider
- if `GET /sessions/messages` returns `500 {"details":"bad parameter or other API misuse"}`, the live router is still running the pre-fix session DB path and needs redeploy
- session workers preseed Claude project trust/onboarding/MCP metadata automatically; remaining drift is provider/profile bootstrap, not the router bus
- `ccs codex` retains the existing frontend. Native `agy` has its own TUI; tracked Mesh Live sends require its exact idle footer and verify submission after Enter.

Current real pipeline snapshot:

- thread: `rektslug-spec-016-20260309-003627`
- thread id: `8c9151d2-fea8-4293-8b43-00cd2884d605`
- active step 0 task: `d3980f6a-bfe5-4026-9141-308365ecf7e9`
- session id: `bd55bde4-9ea8-4118-9ddd-a16f04fd313b`
- repo: `/media/sam/1TB/rektslug`

Useful live checks:

```bash
source ~/.mesh/router.env

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/threads/8c9151d2-fea8-4293-8b43-00cd2884d605/status" | python -m json.tool

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/sessions/bd55bde4-9ea8-4118-9ddd-a16f04fd313b" | python -m json.tool

curl -sS -H "Authorization: Bearer $MESH_AUTH_TOKEN" \
  "$MESH_ROUTER_URL/sessions/messages?session_id=bd55bde4-9ea8-4118-9ddd-a16f04fd313b&after_seq=630&limit=200" | python -m json.tool
```

For a real `.111` (worker) + `.112` (iTerm2 operator) VPN-first validation run, use:
- `deploy/SESSION-FIRST-E2E-RUNBOOK.md`

Manual review API examples:

```bash
curl -s -X POST http://localhost:8780/tasks/review/approve \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"<TASK_ID>","verifier_id":"review-codex"}'

curl -s -X POST http://localhost:8780/tasks/review/reject \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"<TASK_ID>","verifier_id":"review-codex","reason":"missing tests"}'
```

Session control API examples (PTY bridge via bus):

```bash
curl -s -X POST http://localhost:8780/sessions/send-key \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<SESSION_ID>","key":"Up","repeat":1}'

curl -s -X POST http://localhost:8780/sessions/resize \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<SESSION_ID>","cols":120,"rows":40}'

curl -s -X POST http://localhost:8780/sessions/signal \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"<SESSION_ID>","signal":"interrupt"}'
```

## 5. Run the Smoke Test

```bash
python -m pytest tests/smoke/test_e2e_live.py -v
```

This starts router + worker in-process and verifies the full lifecycle:
task creation, dispatch, ack, completion -- all in ~2 seconds.

## Manual Session Smoke

For ad-hoc live validation against a session worker, prefer explicit session mode:

```bash
source ~/.mesh/router.env
python -m src.meshctl submit \
  --title "Antigravity Smoke" \
  --cli antigravity \
  --account antigravity \
  --phase test \
  --mode session \
  --payload '{"prompt":"Reply with exactly ANTIGRAVITY_SMOKE_OK.","working_dir":"/media/sam/1TB/gobabygo","auto_exit_on_success":true,"success_marker":"ANTIGRAVITY_SMOKE_OK"}'
```

Note:
- `session` tasks stay open by default until the CLI exits
- for smoke tests, prefer `auto_exit_on_success=true` with a deterministic `success_marker`
- optional payload field `exit_command` defaults to `/exit`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MESH_ROUTER_PORT` | `8780` | Router HTTP port |
| `MESH_DB_PATH` | `/var/lib/mesh-router/router.db` | SQLite DB path |
| `MESH_DEV_MODE` | `""` | Set `1` to skip auth on `/register` |
| `MESH_AUTH_TOKEN` | `""` | Bearer token (shared by router, worker, meshctl) |
| `MESH_WORKER_ID` | `ws-unknown-01` | Unique worker identifier |
| `MESH_ROUTER_URL` | `http://localhost:8780` | Router URL (worker + meshctl) |
| `MESH_CLI_TYPE` | `claude` | Worker CLI type: `claude\|codex\|antigravity` (`gemini` is historical only) |
| `MESH_ACCOUNT_PROFILE` | `work` | Worker default account/profile identifier (still valid for exact-match routing) |
| `MESH_ALLOWED_ACCOUNTS` | `""` | Optional CSV allowlist published as capabilities (`foo,bar,*` -> `account:foo`, `account:bar`, `account:*`) for dynamic target account routing |
| `MESH_PROVIDER_RUNTIME_CONFIG` | repo default | Optional provider runtime policy file. `""` disables central policy and falls back to `MESH_CLI_COMMAND`. |
| `MESH_RUNTIME_STATE_DIR` | `~/.cache/gobabygo` | Session worker writable state dir used for helper files such as `upterm` logs. |
| `MESH_LONGPOLL_TIMEOUT_S` | `25` | Long-poll block duration (seconds) |
| `MESH_DEFAULT_EXECUTION_MODE` | `batch` | Router code default when task omits execution mode (`batch\|session`). Deploy template sets `session` in `deploy/mesh-router.env`. |
| `MESH_SESSION_FALLBACK_TO_BATCH` | `0` | Router code default. If `1`, session tasks may fallback to batch workers when no session worker is available. Deploy template keeps `0` for session-first hard mode. |
| `MESH_ENFORCE_SESSION_ONLY` | `0` | If `1`, router rejects any task/step with `execution_mode != session` (`400 session_only_mode`). Deploy template sets `1`. |
| `MESH_REVIEWER_ID` | `verifier-codex` | Verifier identity written to review events |
| `MESH_REVIEW_CLI_COMMAND` | `ccs codex --effort xhigh` | CLI command used by `review_worker` |
| `MESH_REVIEW_POLL_INTERVAL_S` | `8` | Review worker polling interval |

## Current Limitations

- `mesh ui` is operator UX plus live attach when available; it is not the source of truth for orchestration state.
- router DB/task/thread state still wins over what a pane appears to show.
- `mesh live` uses `10s`/`count=18` SSH keepalive. Direct VPN/LAN control calls may use `ControlPersist=30m`; ProxyJump and Cloudflare paths disable multiplexing. Persistent create/attach performs a read-only SSH validation preflight because mosh does not reliably propagate remote exit status; it then prefers direct mosh, falls back to SSH on transport failure, and does not retry validation errors or operator interruption.
- `mesh status` hides historical stale/offline worker rows by default; use `mesh status --all` when you need the full audit-heavy view.
- If tmux is alive but the task requeues after ~5 minutes, router or worker is still running old code without lease renewal.
- If a task opens tmux and then blocks on theme/security/trust-folder/MCP prompts, the problem is unattended CLI bootstrap under `mesh-worker`.
- If the initial Claude prompt remains visibly typed in the bottom `❯` composer with no assistant turn, deploy the latest worker code: the session worker now retries `Enter` automatically until the composer clears.
- If Claude lands on the `You're out of extra usage` / `/rate-limit-options` screen, deploy the latest worker code: the session worker now classifies that live TUI state as `account_exhausted` so the router can rotate to the next isolated Claude profile.
- If you need ad-hoc session tasks to finish without manual `/exit`, set `auto_exit_on_success=true` with a deterministic `success_marker`.
- If router `.100` shows `POST /tasks/complete -> 500` or intermittent `POST /heartbeat -> 500`, deploy the latest RouterDB locking changes before debugging task logic; the symptom matched concurrent SQLite access on a shared connection.
- `meshctl task cancel|fail` is intentionally conservative:
  - safe for `queued`, `assigned`, `blocked`, `review`
  - rejects `running` tasks because the live tmux session may still be executing
- A clean unattended demo still requires:
  - preseeded Claude/Codex runtime under `/home/mesh-worker`
  - unattended CLI bootstrap under `mesh-worker` (no theme/security/trust-folder/MCP first-run prompts)
