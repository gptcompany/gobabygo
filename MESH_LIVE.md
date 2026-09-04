# Mesh Live Operator Runbook

`mesh live` operates tmux sessions directly. It does not require the router,
session workers, iTerm2, or the provider account manager. Its only lifecycle
operations are the constrained, local-only `ensure-codex` and
`ensure-antigravity` worker bootstraps.

## Daily Flow

Run these helpers from any directory in the Mac operator shell. The target repos,
tmux sessions, and AI CLIs remain on the Dell workstation.

```bash
mcoordinator rektslug                           # intra-repo; worker bootstrap is automatic
mcoordinator --all                              # multi-repo; adaptive workflow across live repos
mcoordinator rektslug --workflow speckit        # force the canonical Speckit phases
mcoordinator rektslug --workflow direct         # bounded coordination without a formal pipeline
```

You do not need to `cd` or `yazicd` into `rektslug` on the Mac. The helper
resolves that name below the configured repository roots on the Dell, creates or
attaches the Claude coordinator there, and uses mosh/SSH only as transport. The
same helper can run on the Dell; local `mesh live` operations use
`MESH_LIVE_LOCAL=1` automatically inside the injected coordinator contract.

Then state the objective normally. Without an explicit override, the coordinator
uses Antigravity as the sole writer and Codex in a different session as the
primary independent read-only reviewer. Claude coordinates and adjudicates.
This is a default, not an exclusive capability map: every provider may review
when explicitly selected, but a writer's self-review is not independent review.
The coordinator boards and peeks itself, creates missing provider workers only
through authorized ensure commands, sends tracked delegations, and verifies the
latest worker-authored results. It does not require per-task copy/paste by the
operator.

Install or refresh the helpers once:

```bash
./scripts/install-shell-helpers.sh
source ~/.zshrc
```

Antigravity must be installed and authenticated once as the Dell user that owns
its tmux sessions (`sam`). Verify the native binary with `agy --version`, finish
Google OAuth interactively, and keep its token mode `0600`. The official CLI
documentation is at <https://antigravity.google/docs/cli>. Gobabygo does not use
the removed Gemini CLI as a fallback.

The coordinator reuses a suitable live worker. If none exists, its standing
contract permits exactly one provider-specific bootstrap for the selected Git
root: `ensure-codex` creates `codex-<repo>` and `ensure-antigravity` creates
`antigravity-<repo>`. Both use a trusted native binary in YOLO mode and require a
fresh board/peek before delegation. Codex receives no prompt. Antigravity 1.1.x
requires a fixed no-tools bootstrap prompt to establish an authenticated idle
TUI; `ensure-antigravity` reports `ready=yes` only after that prompt is visible
in history and the empty composer is stable across consecutive captures. It
receives no delegated work. Pin the exact provider with
`mcoordinator rektslug --worker codex-rektslug` or
`mcoordinator rektslug --worker antigravity-rektslug`. `mcodex rektslug`
remains available for a deliberately manual Codex shell.

The default `adaptive` workflow uses direct coordination for incidents, audits,
operational diagnosis, and narrow fixes. It selects Speckit for features,
architecture changes, ambiguous requirements, or work needing independent
challenge and adjudication. Override it with `--workflow direct|speckit|adaptive`
or `MESH_COORDINATOR_WORKFLOW`. Inspect the router-independent canonical
projection at any time:

```bash
mesh live workflow show speckit
mesh live workflow show speckit --json
mesh live workflow show speckit --scope coordinator --json
```

The projection is read from `mapping/pipeline_templates.yaml`; it does not
connect to the router, inspect tmux, or require iTerm2.

The scope is selected automatically by `mcoordinator`:

- `mcoordinator <repo>` uses repository scope. The repo is already fixed; the
  coordinator infers the feature or task from the operator objective and only
  asks when that objective is genuinely ambiguous.
- `mcoordinator --all` uses coordinator scope. The global specification,
  decisions, dependency graph, and adjudication stay with the coordinator.
  `{repo}` and `{feature}` are late-bound for each concrete delegation; they are
  not mandatory startup parameters and the coordinator must not request one
  global pair before cross-repo clarification or read-only analysis.

Multi-repo scope runs from the dedicated Git repository
`/data/sata/1TB/coordination`, configurable with the absolute
`MESH_COORDINATOR_STATE_REPO`. Intra-repo scope still runs from the selected
repository. Every coordinator transport validates that its target is the exact
Git root before inspecting or creating tmux state; it never falls back to the
unversioned repository base. Store global specs, decisions, task state, and
handoffs there, not pane captures or secrets.

Coordinator scope is a policy projection, not a durable pipeline instance. A
router thread/database may persist selected tasks and handoffs when useful, but
is not required to hold the global Speckit state or coordinate live tmux panes.

`mcoordinator` injects the autonomous system contract only when its tmux
session is first created; later calls only attach. An already running
coordinator therefore keeps its old contract. Bootstrap a new coordinator (or
exit the old tmux session and resume it) to activate a newly deployed contract.

Repository names resolve below `MESH_WS_REPO_BASE`. A missing target fails, and
a non-Git target also fails, before tmux session inspection or creation.
Persistent helpers never fall back to the repository-base directory. An
absolute repository path is also accepted.

For multi-repo coordination:

```bash
mcoordinator --all
```

After a workstation reboot, tmux sessions are gone but Claude conversation
history remains. Resume explicitly while recreating the coordinator:

```bash
mcoordinator --all --continue
mcoordinator --all --resume <claude-session-id>       # deterministic
mcoordinator rektslug --continue --worker codex-rektslug
```

`--continue` asks Claude to select the latest conversation in the coordinator
working directory. `--resume <id>` requires an exact UUID and is preferred when
the exact conversation is known. Before creating tmux, the workstation verifies
that the UUID exists in Claude's history for the exact coordinator working
directory; wrong, missing, malformed, and cross-repository IDs fail closed.
Before creating a differently named coordinator, the helper also rejects that
UUID when an active Claude process in another tmux session already uses it. This
works with both newly marked coordinators and legacy coordinators by parsing the
exact NUL-delimited `--resume` process argument internally; process arguments and
the UUID are never printed in diagnostics.

New deterministic resumes use a private per-UUID `flock` for the lifetime of the
Claude process. This closes the simultaneous-start race even when two preflights
run before either process becomes visible. The lock is released if Claude exits
back to the persistent shell. No database, daemon, tmux kill, or iTerm2 state is
involved.

Both options are used only when the tmux session is created; if the tmux already
exists and is a valid coordinator, `mcoordinator` only attaches to it and ignores
resume validation. New coordinators carry a tmux marker so a terminated
coordinator can be diagnosed distinctly, but attach still requires a running
Claude process. For compatibility, an unmarked
shell wrapper with a direct Claude child is also recognized without inspecting
process arguments. An older, unmarked session whose active process is only
`bash`, `zsh`, `sh`, or `fish` and has no Claude child fails closed instead of
silently attaching as though it were a running coordinator.

The helper runs in the Mac operator shell. It generates the current Gobabygo
contract before starting tmux, then launches Claude on the Dell with resume and
`--append-system-prompt` in the same command. Claude therefore receives both the
old conversation and the freshly generated contract. The contract uses the
absolute `MESH_COORDINATOR_MESH_SCRIPT` path on the Dell, so the running
coordinator does not depend on Mac aliases, shell functions, or `.zshrc`.
Before any tmux create or attach, the helper requires the exact coordinator
contract marker and transactional-review capability from that remote runtime.
A stale, truncated, or incompatible runtime therefore fails before Claude or
tmux mutation instead of silently resuming with an obsolete contract.
Coordinator bootstrap also sets `CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN=1` so the
conversation remains in native tmux/iTerm2 scrollback. This changes rendering
only; resume history, prompts, locks, and workflow selection are unchanged.
Every helper-driven tmux create or attach also raises the global and current
window `history-limit` default to 20,000 lines. Existing panes retain the limit
allocated when they were created; recreate or resume the session once to apply
20,000 lines to that pane. Configuration is best-effort and never blocks access
to an otherwise healthy session.

Using `/resume` interactively is possible, but the startup option is preferred:
it avoids a temporary conversation and makes contract injection explicit and
testable. Resume remains opt-in so a new coordinator cannot silently inherit an
unrelated conversation.

`--continue` cannot provide the per-UUID guarantee because Claude selects the
conversation only after startup. Use it only when no other coordinator can be
using the same history; prefer exact `--resume <id>` whenever concurrency safety
matters.

If `claude-coordinator` is reported as an unmarked shell, inspect it with
`wsattach claude-coordinator` or preserve it and create a fresh coordinator:

```bash
mcoordinator --all --session claude-live-coordinator
```

Keep the direct controls for diagnosis and deliberate manual intervention:

```bash
wboard 30
wsupervisor
wbrief --repo rektslug --lines 40
wbrief --all --lines 30
wsattach claude-coordinator
wpeek claude-rektslug 80
wsend claude-rektslug "approved action"
wsend claude-rektslug "approved action" --enter
```

## Authority

| Concern | Source of truth | Commands |
| --- | --- | --- |
| Live process, pane output, attached state | tmux on the workstation | `mesh live`, `wboard`, `wpeek`, `wsattach` |
| Durable tasks, dependencies, results, handoffs | router database | `mesh thread`, `mesh sessions` |
| Tabs, windows, and pane arrangement | iTerm2 local state | `mesh ui`, `mesh term` |

A manual tmux session does not need a router row. A router task does not need an
iTerm2 layout. iTerm2 is never authoritative for live or durable state.

## Commands

| Helper | Native command | Effect |
| --- | --- | --- |
| `wboard [query] [lines]` | `mesh live board [query] --lines N` | List/filter sessions; read-only |
| `wsupervisor [--json]` | remote `mesh live tick --observe` | Fresh supervisor state; never sends pane input |
| `wpeek <session> [lines]` | `mesh live peek <session> [lines]` | Capture one exact/unique pane; read-only |
| `wbrief ...` | `mesh live brief ...` | Build a dynamic, redacted coordinator prompt; read-only |
| `mcoordinator ...` | `mesh live coordinator-prompt ...` | Create/attach a persistent auto-configured Claude coordinator |
| - | `mesh live workflow show <name> [--scope repository\|coordinator]` | Project one canonical workflow; read-only, no router/tmux access |
| - | `mesh live ensure-codex <repo>` | Locally create/reuse one deterministic native Codex worker; no task is sent |
| - | `mesh live ensure-antigravity <repo>` | Locally create/reuse one deterministic native Antigravity worker; fixed no-tools bootstrap only |
| - | `mesh live tick` | Inspect local Claude sessions and print a metadata-only action plan |
| - | `mesh live tick --observe` | Persist debounced metadata-only supervisor transitions; never send input |
| - | `mesh live tick --apply` | Record supervisor state, then apply guarded WAIT/session-limit/coordinator wake actions |
| `wsend <session> <text>` | `mesh live send ...` | Type literal text into the selected pane |
| `wsend <session> <text> --enter` | `mesh live send ... --enter` | Type text, then send Enter separately |
| - | `mesh live send <codex-session> <text> --delegation-id <id> --enter` | Deliver and record a metadata-only recovery receipt |
| - | `mesh live send <antigravity-session> <text> --delegation-id <id> --enter` | Require idle Antigravity composer and verify one submission; no recovery Enter |
| - | `mesh live recover-codex-submit <session> <id>` | Guarded, stateful single-Enter recovery for one exact Codex delegation |
| - | `mesh live recover-coordinator <session> [--apply]` | Plan by default; explicitly resume one confirmed stopped local coordinator |
| `wsattach <session>` | `mesh live attach <session>` | Attach to an existing session; never creates or kills one |

Use `--owner <user>` when the same session name exists under multiple tmux
owners. Use `board --lines 0` when only metadata is needed.

## Coordinator Scope

### Automatic Mode

`mcoordinator <repo> --worker <session>` limits delegation and bootstrap to one
exact deterministic worker. Without `--worker`, the coordinator first discovers
candidates in scope and resolves one exact session before sending.
`mcoordinator --all` enables multi-repo observation and coordination.

The injected contract tells Claude to board and peek dynamically, debate viable
options, create a unique `DELEGATION_ID`, send a bounded assignment, peek again
to verify CLI acceptance, monitor completion, and inspect result/test evidence.
A successful tmux send is not treated as delivery: the coordinator must find the
delegation ID or clear CLI activity and must never resend blindly.

### Adaptive Speckit

Speckit live reuses the phases, prompts, roles, dependencies, critical flags,
and review policy in `mapping/pipeline_templates.yaml`; it is not a copied
prompt or a second workflow definition. `speckit.analyze` checks consistency
across spec, plan, and tasks. Independent perspectives come from the plan,
pre-implementation, and post-implementation confidence gates: Codex challenges
technical consistency, Antigravity challenges product/UX consequences, and the
coordinator performs the final operator-facing adjudication.

In repository scope, the phases describe one repo and the feature/task is
derived from the objective. In coordinator scope, the same phases govern a
program of work: specification, clarification, dependency analysis, gates, and
final decisions stay global, while each implementation or review lane receives
its exact repo and feature/task only at delegation time. Read-only evidence
lanes may span repositories before any writer is selected.

For consequential choices, the coordinator uses a bounded decision challenge,
not an open-ended agent conversation. It freezes one non-secret Spec Kit
decision artifact under a unique `DECISION_ID`, verifies its SHA-256, and sends
it to a separate read-only Codex challenger by default. The challenger may run
at most two rounds and returns objections plus
`CHALLENGE_VERDICT: ACCEPT|REVISE|ESCALATE`. Claude remains final adjudicator;
destructive, privileged, security-boundary, material-cost, and unresolved
product-authority choices return to the operator.

In adaptive coordinator scope, a handoff with multiple tasks, repositories, or
durable decisions is always program work and must be reconciled into versioned
Spec Kit specification, plan, decision, and task artifacts. An urgent backup,
push-safety, containment, or recovery lane may run directly, but it cannot
replace that durable state or close the enclosing objective.

Every write delegation carries a provider-neutral `TDD_MODE`. Use `required`
for behavior changes and regression fixes with a feasible focused test,
`recommended` only with a concrete harness or environment limitation, and
`not_applicable` for review, documentation, generated artifacts, deployment,
or operational work without a meaningful behavior test. Codex and AGY receive
the same RED/GREEN evidence contract; Gobabygo does not claim they run the
Claude-specific TDD Guard hook.

For selected Claude and Codex coding repositories, `mesh probity` can turn that
contract into a pre-tool gate. Runtime and hook wiring are central, but
activation and path policy stay in one reviewed `probity.config.*` at the target
Git root:

```bash
mesh probity install              # inspect the plan
mesh probity install --apply --replace-tdd-guard
mesh probity smoke --agent all --json
mesh probity status <repo> --json
```

The dispatcher returns immediately for every repository without a config. An
opted-in repository fails closed when its config is ambiguous or the runtime is
missing. Existing Claude and Codex sessions do not reload this integration and
must be restarted normally; Mesh Live never kills them during installation. The
pinned Probity CLI does not expose Antigravity, so AGY remains on brief-level TDD
policy and test evidence.

Claude user and project hooks are additive. The installer therefore refuses to
stack Probity with the legacy global TDD Guard. After review,
`--replace-tdd-guard` removes only the recognized TDD Guard command and writes
mode-`0600` backups beside changed user settings. It does not edit project
settings or any other Claude hook. `mesh probity status` reports stale/missing
dispatchers and TDD Guard conflicts, but Codex hook trust remains
`manual-unknown` because it is UI state.

Codex requires one explicit trust review whenever the installed hook hash
changes. Open a fresh Codex CLI, run `/hooks`, inspect the absolute dispatcher
command, and trust that exact entry. Until then Codex reports the hook as
untrusted and skips it. Do not make routine workers use
`--dangerously-bypass-hook-trust`; that flag is reserved for an isolated,
already-reviewed smoke and does not persist trust.

Keep this optional. `enforceTdd()` can invoke a model validator for matching
writes and therefore adds latency and usage. A Probity config is executable
project code and must be reviewed before Codex trusts it. CI and an independent
reviewer remain authoritative; YOLO permissions are unchanged. Revert the
repository config commit to disable one project. Restore the reviewed
`*.mesh-probity.bak` files only when rolling back the user-level integration.

`mcoordinator` reads one bounded `mesh speckit status <repo> --json` snapshot
from the Dell at every new, continue, or exact-resume startup. Invalid or
unavailable status disables claims about installed Spec Kit phases but does not
disable direct board, peek, incident coordination, or read-only review.
The remote snapshot is terminated after 20 seconds by default. A second local
wall-clock bound terminates the whole SSH client after at most 10 additional
seconds, including descendants that accidentally retain its pipes. A timeout
therefore omits the snapshot and continues coordinator bootstrap; it never
disables the requested workflow. `MESH_COORDINATOR_STATUS_TIMEOUT` can set a
different positive integer number of seconds for unusually slow workstations.

Legacy repositories are reported as `project.state=legacy` when old
`.specify/`, Speckit commands, or complete spec artifact sets exist without the
current integration manifest. Migrate them one clean repository at a time with
`mesh speckit project migrate`; the plan is read-only for the target repository
and installs the Claude coordinator skill integration only after
explicit `--apply`. Existing specifications and constitution remain local and
are preserved. A pre-`.specify` `memory/constitution.md` is copied into the
current path only when that path is absent; if both exist, neither is overwritten
and the historical path is reported as unmigrated. Retained legacy Claude
commands require a later manual decision rather than automatic deletion.
Codex and AGY remain Mesh worker providers and receive the bounded context below;
they are not simultaneous project-local Spec Kit integrations because upstream
maps both providers to incompatible files under `.agents/skills`.

Before a Spec Kit task is delegated, the coordinator generates one compact,
provider-neutral envelope from the target repository:

```bash
mesh speckit context /data/sata/1TB/rektslug \
  --phase plan \
  --feature-dir specs/001-feature \
  --artifact spec.md \
  --artifact tasks.md \
  --role writer

mesh speckit context /data/sata/1TB/rektslug \
  --phase plan \
  --feature-dir specs/001-feature \
  --artifact spec.md \
  --role reviewer \
  --review-scope commit:<base-sha>..<writer-sha>
```

The command succeeds only for an installed common phase in an aligned project.
Artifacts are feature-relative and cannot escape the feature directory. A
reviewer requires an immutable commit range, commit, or recorded diff SHA-256.
The resulting `SPECKIT_CONTEXT` contains no provider name, pane output, release
notes, or full capability inventory, so Codex and Antigravity can receive the
same artifact identity while retaining different writer/reviewer rules.

For feature development, the coordinator also enforces a planning publication
gate. With a clean runtime commit already contained in Gobabygo `origin/master`,
it dry-runs and applies the existing managed caller installer when needed, then
dry-runs and creates the feature's immutable binding with `mesh speckit github
init`. It validates with `mesh speckit github plan <feature-dir>` and prepares a
planning-only PR. It does not delegate source implementation
until that PR is merged, the repository ledger Action has run, and `mesh
speckit github check <feature-dir>` reports aligned.

Every implementation and review brief carries the exact
`<owner/repo>:<feature-id>:<Tnnn>` key. Worker prose, idle state, router history,
or manual issue closure cannot complete a task: reviewed evidence changes
`tasks.md`, then the Action derives issue state. The coordinator never uses
interactive `speckit-taskstoissues` as fallback. A custom caller, untrusted
runtime pin, unavailable GitHub CLI, or failed Action remains a precise blocker;
none permits direct issue mutation or worker-owned onboarding.

Manual product, architecture, security-boundary, cost, deploy, and money-path
decisions remain checklist entries in authoritative `tasks.md`; they do not get
a separate database. Inspect one feature or the coordinator program with:

```bash
mesh speckit manual-actions specs/001-feature
mesh speckit manual-actions /data/sata/1TB/coordination --all --json
```

The read-only command returns `MANUAL_CLEAR` or `MANUAL_REQUIRED` and correlates
each open `DEC-* [D]` with task IDs that declare it under `Blocked by` or
`Bloccato da`. The coordinator runs it at bootstrap/resume, before every
`TICK_IDLE`, and before closure. It presents the exact decision, bounded
options, recommendation, and blocked tasks, then records only an explicitly
submitted operator answer in the authoritative artifact. Silence, pane output,
worker prose, and Claude prompt suggestions/ghost text are never consent.
Disable vendor prompt suggestions in the Mac and Dell user configuration with
`"promptSuggestionEnabled": false` in `~/.claude/settings.json`. A coordinator
already running retains its startup setting until it is exited and resumed.
For retained sessions, tick captures terminal attributes and treats an exact
fully dimmed Claude composer line as a vendor suggestion, not pending input. It
never submits that text; after a valid reset it types only the fixed wake message.

Template roles are desired perspectives, not permission to spawn processes.
The following are coordinator contract rules, not filesystem locks or an OS
sandbox. `mesh live` validates session identity and bounded keyboard delivery,
but YOLO workers retain every permission of their Dell user. The coordinator
must recheck tmux ownership and Git state before and after delegated writes or
reviews.

The live adapter follows these boundaries:

1. Keep one active writer per repository.
2. By default use Antigravity as writer and Codex as primary reviewer. An
   explicit operator choice or `--worker` pin overrides this preference; a
   provider failure may justify a declared substitution with degraded coverage.
3. Use a different tmux session for each reviewer/challenger and give it an
   explicitly read-only brief. YOLO mode is not an OS sandbox, so verify Git
   afterward and stop if a reviewer changed the worktree.
4. Prefer model-diverse review. A second session of the same model provides an
   independent context but must not be reported as a different model view.
5. Fan out only after dependencies complete, with distinct delegation IDs and
   shared immutable evidence paths or commit IDs.
6. Map a template `target_cli` to an existing ready session. Only
   `ensure-codex` or `ensure-antigravity` may create a missing worker; the
   coordinator does not create Claude sessions.
7. Report missing preferred reviewers as degraded coverage. Never claim an
   unavailable perspective ran.
8. Send implementation to the authorized writer even when the router template
   names a different lead. The coordinator synthesizes and adjudicates but does
   not edit source code.

Before behavior-changing work, freeze acceptance criteria, named critical
invariants, and the mutation budget. The default is one representative mutation
per critical invariant; expansion requires a concrete uncovered failure mode,
not a desire to increase a mutation count.

Every code review freezes an exact commit range or a recorded HEAD, changed-file
list, status, and diff checksum after writer activity stops. Findings come first,
ordered by severity. Each finding includes exact `file:line`, impact, evidence
or reproduction, bounded fix direction, `SCOPE_CLASS:
IN_SCOPE|RELEASE_BOUNDARY|ADJACENT`, and `DISPOSITION:
FIX_NOW|REPLAN|BACKLOG`. Adjacent findings normally enter the durable backlog,
but cannot be deferred when they invalidate acceptance, a critical invariant,
or release safety.

The reviewer then reports missing tests, residual risks, `REVIEW_LEVEL:
DELTA|INVARIANT|RELEASE`, exact immutable `REVIEW_SCOPE`, and
`REVIEW_ROUND: 0|1|2` plus `REVIEW_VERDICT: PASS` or `REVIEW_VERDICT:
CHANGES_REQUIRED`. Round 0 is initial review; rounds 1 and 2 review corrections.
A delta PASS
accepts only its correction, an invariant PASS only the named invariants, and
only a release PASS satisfies the final review gate. Unresolved high/medium
in-scope or release-boundary findings forbid PASS. Release PASS does not
authorize merge, push, deploy, or money-path enablement; those remain separate
operator or standing-authority decisions.

The coordinator verifies afterward that the read-only reviewer did not mutate
tracked state. Corrections return to the writer under a new delegation ID and
the reviewer inspects the exact correction delta. One frozen task scope permits
at most two correction-and-review rounds. A second failure must end with
`REVIEW_LOOP_DECISION: REPLAN|ESCALATE|BACKLOG`; BACKLOG cannot hide a blocking
finding, and only explicit REPLAN resets the count. Run one independent release
review per frozen release candidate. Do not add reviewers after release PASS
unless the candidate changes or new concrete evidence invalidates it.
Before each correction, persist its scope and next round in authoritative tasks
or coordinator state. Resume and compaction reconstruct the count from that
state and prior review evidence; they never reset it implicitly.

For a bound Spec Kit task, `review-ledger.json` makes those transitions
transactional. `tasks.md` remains authoritative for intent and completion;
`review-ledger.json` is authoritative only for review scope, round, verdict,
and release-gate state; GitHub Issues remain a derived view. The coordinator
reads the current global revision before every mutation and supplies it as
`--expect-revision`. A mismatch requires a fresh status read and decision, not
an automatic retry.

```bash
mesh speckit review status <repo> <feature-dir> --json

mesh speckit review init <repo> <feature-dir> T001 \
  --scope commit:<writer-sha> --writer-session agy-repo \
  --invariant "named critical invariant" --mutation-budget 1 \
  --expect-revision <revision>

mesh speckit review open <repo> <feature-dir> T001 \
  --level RELEASE --scope commit:<writer-sha> \
  --reviewer-session codex-repo --delegation-id <id> \
  --expect-revision <revision>

mesh speckit review record <repo> <feature-dir> T001 \
  --verdict PASS --evidence-file <feature-dir>/review-T001.md \
  --mutations-run 1 --expect-revision <revision>
```

`record` hashes a real non-symlink report inside the feature. It does not parse
review prose. `PASS` with blocking high/medium or safety findings fails closed.
`open` records an event from which `status` derives the canonical review
deadline. Once elapsed, `timeout --expect-revision <revision>` permits exactly
one fallback using a different reviewer session for the same immutable scope;
a second timeout moves the task to `ESCALATED`. Timeout is never consent or a
review verdict. Tick only wakes the coordinator; it does not infer or write the
review transition from pane output.
A failed review permits an immediate `decide`, or at most two `correction`
transactions. Each correction must receive a DELTA review. DELTA PASS produces
`CANDIDATE_UPDATE_REQUIRED`; use `candidate --scope <new-immutable-scope>`
before another INVARIANT or RELEASE review. `budget` only increases the frozen
mutation budget with a concrete reason. `RELEASE_PASSED` closes the review gate
but never authorizes merge, push, deploy, or money-path activation.
`mesh speckit review check <repo> <feature-dir> <Tnnn> --scope
<current-immutable-scope>` exits `0` only when that exact frozen candidate is
`RELEASE_PASSED`, `1` while its gate is unsatisfied, and `2` for invalid or
stale input.

The ledger and referenced reports are normal non-secret feature artifacts.
Include them only through the repository's normal authorized Git flow. The
ledger CLI itself does not commit, push, touch source, send tmux input, use the
router, or contact GitHub.

The router/database remains optional for durable managed orchestration. Loading
the live projection never creates a router thread and never takes ownership of
manual tmux sessions.

Tracked Codex sends serialize receipt invalidation, tmux input, and receipt
creation under the recovery lock. Before a tracked send, Mesh recaptures the
visible pane and proceeds only when the main Codex composer is recognizable,
empty, and idle. A recognized stock placeholder counts as empty. MCP startup
warnings do not by themselves make the composer occupied. The transactional
preflight preserves tmux terminal styles internally: any single-line composer
suggestion rendered entirely with Codex's dim placeholder style counts as
empty, so rotating suggestions do not require a text whitelist. Normal styled
text remains occupied. Public board and peek output stays plain and redacted;
when style evidence is unavailable, only known exact placeholders count as
empty.
Existing draft text, a collapsed paste, activity, menus,
confirmations, and ambiguous startup screens are refused before any key input or
receipt invalidation. Mesh never clears that content automatically; inspect it
manually or select another authorized idle worker. If the composer contains a
correlated prior delegation, use only its guarded submit recovery. Otherwise
report the manual blocker; never overwrite the draft. `submission=not-submitted`
means task text was delivered but the requested Enter failed; the receipt remains
available for the guarded recovery, and the task must not be resent. A receipt
write failure after text delivery is reported as `tracked=no` and also requires
manual inspection, not resend.

A tracked-send refusal is a safety result, not a prompt-length problem. Never
bypass it by omitting `--delegation-id` or by replacing the task with a shorter
untracked pointer; doing so removes the occupied-composer guard and recovery
receipt.

`send` accepts one literal line up to 8192 characters; newlines and control
characters are rejected. For a long or multi-line delegation, the coordinator
writes a non-secret brief inside the target repository and sends one line with
the `DELEGATION_ID`, absolute brief path, and instruction to read and execute
that file. Codex delegations use the same ID in `--delegation-id`; this records
only owner/session/pane, ID, character count, and timestamp, never task text or
a task digest. Only the latest tracked delivery per pane is retained; any later
Gobabygo input to that Codex pane invalidates it before sending. This keeps remote
keyboard input bounded and auditable.

For Claude, a text send with `--enter` waits one second between literal text and
the separate Enter key. This bounded paste-settle also applies when a marked
resumed coordinator exposes a shell wrapper with one direct Claude child. It
does not retry, resend text, or delay Enter-only operations, Codex, or
Antigravity.

Tracked Antigravity sends use the same exact `DELEGATION_ID`, but do not create a
recovery receipt. Mesh recaptures the visible pane and proceeds only when the
current Antigravity footer is the observed empty idle composer (`>` plus
`? for shortcuts`). Pending text, generation, permission menus, login screens,
errors, and ambiguous redraws are refused before input. After one Enter, Mesh
polls for the submitted ID together with current activity or a new empty
composer. `submission=verified` requires that positive evidence in two
consecutive captures; a single transient redraw remains
`submission=unknown` and requires bounded peeks. There is no Antigravity
recovery command: never resend, clear the composer, or send another Enter
automatically. In controlled E2E on `agy` 1.1.13, one Enter submitted correctly;
this provider-specific policy must be revalidated when the TUI format changes.

Completion is not a substring search over the pane. Delegation briefs and CLI
composers can echo both `WORKER_DONE` and `WORKER_BLOCKED`. A status is a
candidate only when the latest worker-authored response after delegation ends
with one exact standalone marker carrying the current `DELEGATION_ID`; task
echoes, quoted text, history, composer content, and ambiguous reports are ignored.
The coordinator still verifies the claimed result and test evidence.

Codex can occasionally leave a rapidly typed/pasted task in its composer even
when the initial send included `--enter`; long pastes may render only as
`[Pasted Content N chars]`. The coordinator contract includes one bounded
paste-settle recovery. After an immediate peek it proceeds only when the bottom
Codex composer has no Working/activity and either shows the exact current
`DELEGATION_ID` or an exact collapsed placeholder correlated to the same recent
tracked send, pane, ID, and character count. The receipt must also be the latest
tracked delivery to that pane. It then invokes the guarded
recovery command and peeks again:

```bash
mesh live recover-codex-submit <codex-session> <DELEGATION_ID>
mesh live peek <codex-session> 80
```

The recovery command accepts no task text. It recaptures only the current visible
pane, checks the exact bottom composer and Codex process, rejects activity,
menus, confirmations, shell prompts, mismatched IDs, untracked, stale, or
length-mismatched placeholders, and records the attempt before sending Enter.
It then polls the visible TUI for a bounded interval.
`submission=verified` is positive redraw evidence; `submission=unknown` means
Enter was delivered but the TUI did not prove acceptance in time. Unknown
requires bounded follow-up peeks and must not alone mark the worker blocked.
Its metadata-only state rejects every second attempt for the same delegation and
pane. The state path is fixed by the workstation code;
the coordinator cannot redirect that write through a CLI option or request
payload. This keeps the recovery evidence-driven. Screen
changes in the final interval between the atomic recapture and tmux input cannot
be eliminated completely; polling narrows that race. A refusal occurs before
delivery and exits `2`; an inconclusive post-delivery verification exits `1` as
unknown; verified delivery exits `0`. Never fall back to task resend or a plain
second `send --enter`.

The receipt proves only what Gobabygo delivered to tmux; it cannot identify text
typed directly into the same composer afterward. Therefore placeholder recovery
expires after 15 minutes and requires an otherwise empty exact placeholder. If
correlation is unavailable, attach and inspect manually. Mesh live never clears
the composer automatically and never falls back to a naked Enter.

When no suitable provider worker exists, the automatic path may run only one of:

```bash
MESH_LIVE_LOCAL=1 mesh live ensure-codex <repo-name-or-absolute-git-root>
MESH_LIVE_LOCAL=1 mesh live ensure-antigravity <repo-name-or-absolute-git-root>
```

The target must resolve to an exact Git root below `MESH_LIVE_REPO_ROOTS` (or
`MESH_WS_REPO_BASE`), and the tmux name is deterministically `codex-<repo>` or
`antigravity-<repo>`.
In multi-repo mode a repo name must come from the operator's explicit objective;
an absolute path may instead come from tmux metadata. Pane capture text is never
used as a command argument. Missing or ambiguous names fail before tmux mutation.
`--expect-session` makes a pinned coordinator fail before tmux mutation when
that name differs. Existing name/path/process/pane collisions fail closed;
concurrent calls reuse the atomic winner. Codex invokes only trusted
`/usr/local/bin/codex` or `/usr/bin/codex`; Antigravity invokes only trusted
`/home/sam/.local/bin/agy` or `/usr/local/bin/agy` with
`--dangerously-skip-permissions --new-project`. Neither bootstrap accepts task
text or uses `send-keys`, and both are unavailable through the remote live
endpoint. Antigravity uses `--prompt-interactive` only for its fixed no-tools
bootstrap because a bare 1.1.x TUI may re-enter OAuth instead of loading the
persisted headless token. If authentication or startup fails, the coordinator
reports a blocker.

Gobabygo-created Codex workers launch with the documented
`check_for_update_on_startup=false` override. This prevents an update menu from
occupying a worker composer; it does not install or hide updates globally.
Codex upgrades remain a centrally managed, explicit `codex update` operation
outside active worker sessions. Existing workers retain their launch arguments
until they are deliberately recycled under the normal lifecycle guardrails.

The active `MESH_COORDINATOR_MESH_SCRIPT` checkout is an immutable control-plane
runtime even when Git reports detached HEAD. Both ensure commands reject that exact
Git root before tmux mutation. Gobabygo development must use a separate clean
branch checkout or Git worktree; do not create a branch inside the live runtime.

### Periodic Supervisor

`mesh live tick` is the bounded polling primitive. It must run on the Dell tmux
workstation; it is read-only unless `--apply` is present. By default it only
inspects tmux sessions owned by the user running it, so it does not compete with
router-owned `mesh-worker`/`mesh` sessions and their `session_worker` retry and
account-rotation policy.

Board headers expose the current provider `screen` classification and tmux
`activity_age`. These are operational signals, not completion claims. A monitor
notification is only a hint: before reporting completion the coordinator must
observe the exact current status marker and `screen=idle` in two fresh
board/peek cycles at least five seconds apart. Docker builds, tests, tool
activity, changing output, `busy`, `unknown`, or `awaiting_input` remain active
or uncertain.

```bash
MESH_LIVE_LOCAL=1 mesh live tick --json
MESH_LIVE_LOCAL=1 mesh live tick --observe --json
MESH_LIVE_LOCAL=1 mesh live tick --apply
MESH_LIVE_LOCAL=1 mesh live tick --apply --recover-coordinator
```

`--observe` is the shadow supervisor path. It classifies only controlled tmux
metadata, requires two consecutive observations before a transition, retains at
most 100 events in the existing mode-0600 tick state file, and never stores pane
captures or sends input. It reports every discovered Claude, Codex, and
Antigravity session: `busy` and `idle` are informational, while capture errors,
rate limits, unknown screens, and `awaiting_input` require inspection. `--apply`
records the same transitions under the same lock before evaluating its existing
guarded actions, so the installed cron does not need a second entry, daemon,
database, router, or iTerm2 dependency.

For a marked coordinator whose Claude process has exited, the supervisor reports
`coordinator_not_running_recoverable` only when the tmux session contains an
exact resume UUID, its recorded absolute Git root equals the current pane path,
its scope and workflow metadata are valid, and
`MESH_LIVE_COORDINATOR_RECOVERY_HOLD` is not `1`. The normal two-observation
debounce applies. Tick remains report-only for coordinator exits unless
`--apply --recover-coordinator` is explicitly enabled. Missing or malformed
metadata, root drift, and an operator hold all fail closed as
`coordinator_not_running`. This is report-only in every default and existing
cron installation. On the workstation, an operator can set or clear the hold
without changing pane content:

```bash
tmux set-environment -t claude-coordinator MESH_LIVE_COORDINATOR_RECOVERY_HOLD 1
tmux set-environment -u -t claude-coordinator MESH_LIVE_COORDINATOR_RECOVERY_HOLD
```

Coordinator sessions created by the helpers record their root. Deterministic
recovery additionally requires an explicit `mcoordinator --resume <UUID>`;
legacy and `--continue` sessions without an exact UUID remain manual.

On the workstation, inspect the planned recovery first and apply it explicitly:

```bash
MESH_LIVE_LOCAL=1 mesh live recover-coordinator claude-coordinator
MESH_LIVE_LOCAL=1 mesh live recover-coordinator claude-coordinator --apply
```

Apply requires the debounced supervisor incident already stored by
`mesh live tick --observe` or `--apply`, a detached single-window shell with no
child process, the exact Claude history file, an exact Git root, and the complete
recorded contract. It revalidates tmux identity and markers immediately before
`respawn-pane`, records one attempt before mutation, reacquires the UUID lock in
the replacement shell, and verifies a running Claude child. A lock race returns
the pane to an interactive shell. Unknown verification is not retried for the
same incident. Two subsequent healthy supervisor observations close that
incident so a later, distinct coordinator exit can be recovered. The standalone
command does not schedule itself; cron recovery requires explicit installer
opt-in.

Tick and supervisor JSON expose `provider`, `schedule_source`, and `not_before`
without requiring prose parsing. `not_before` is an epoch timestamp; concise
terminal output renders it as UTC ISO-8601 so Mac, Dell, and CI agree. Current
rate-limit policy is intentionally asymmetric:

| Provider state | `schedule_source` | Behavior |
| --- | --- | --- |
| Claude session-limit banner with exact minute and IANA timezone | `vendor_banner` | persist reset plus 90-second grace; recapture and attempt one guarded wake; retry only an unchanged pending composer as described below |
| Codex rate limit | `unsupported` | warning only; wait or declare another authorized worker |
| Antigravity rate limit | `unsupported` | warning only; wait or declare another authorized worker |

An elapsed `not_before` authorizes an attempt, not a claim that the provider is
available or that its task resumed. Coordinator calculations, transcript prose,
activity age, blind Enter, resend, and automatic session rotation cannot create
or shorten a schedule. The mode-0600 tick state survives cron invocations and
workstation restarts; a changed pane, process, banner, timezone, or pending
composer must pass fresh guards before any input.

When an idle coordinator's current visible output contains the exact standalone
`MANUAL_REQUIRED count=N` marker, the supervisor reports
`manual_action_required` as a warning and does not wake it again. Similar prose
and stale markers outside the bounded visible tail do not match.

Apply mode has eight actions; the eighth remains separately opt-in:

1. It sends Enter only when the exact Claude rate-limit menu is present and
   `Stop and wait for limit to reset` is already the selected option.
2. It sends a fixed `MESH_LIVE_TICK` instruction only when a coordinator is at
   an empty idle prompt. The coordinator then boards and peeks dynamically and
   decides whether existing work needs review, debate, or delegation. It may
   report `TICK_IDLE` only when the accepted objective has no dependency-ready
   incomplete task, unreviewed result, or unreconciled authoritative task
   state. Professional closure requires implementation and test evidence,
   required independent review and corrections, task reconciliation, and all
   authorized commits/pushes; otherwise it reports the concrete blocker.
3. It recognizes the exact Claude session-limit banner containing a reset time
   and IANA timezone, persists that schedule, and waits through a 90-second
   grace period. An empty pane receives one fixed `MESH_LIVE_RESET_WAKE`.
   When the coordinator already contains the interrupted prompt, two matching
   observations are required and only a bare Enter is sent; if delivery remains
   unverified, the same byte-identical composer may receive at most two further
   Enter-only attempts, each after at least four minutes (normally the next
   five-minute managed tick). The state stores its
   hash, attempt count, and timestamp, never its text. A changed prompt, pane, process, menu, or a pending
   prompt outside the coordinator fails closed. This resumes the interrupted
   request without bypassing or shortening the provider limit.
4. It dismisses the exact Antigravity experience survey only when that survey
   is the final visible UI and all options include `[0] Skip`. It verifies the
   same pane still runs `agy`, persists a one-attempt tombstone before input,
   sends only literal `0` without Enter, and then verifies that the survey
   disappeared. Altered or ambiguous prompts fail closed. This policy never
   applies to trust, permissions, authentication, destructive confirmation,
   billing, or project decisions.
5. It requests `/compact` before waking a marked coordinator only when Claude's
   exact `/context-action` footer reports at least 90% usage and the current
   composer is empty and idle. The attempt is persisted before input and is not
   repeated for the same screen. While `Compacting conversation` remains
   visible with its progress bar, including when Claude changes the context
   footer to `???`, the supervisor reports `coordinator_compacting` and never
   treats the empty composer or queued-message prompt as idle. A stalled compaction is a warning requiring a durable
   handoff and controlled fresh-session rotation; Mesh never sends `/clear` or
   kills/replaces the coordinator automatically.
6. It recovers one lost Enter only when the current Claude coordinator composer
   contains the exact `MESH_LIVE_TICK` token already persisted by that same
   tick state. Token text in history is ignored. The recovery tombstone is
   written before one Enter and every repeat is refused.
   A wake is verified only after Claude becomes `busy`; seeing its token in the
   composer is explicitly not proof of submission. Human text, delegations,
   unknown composer content, menus, and uncorrelated tokens never receive this
   recovery.
7. It treats an exact current Claude `API Error: 529` immediately above an
   empty composer as a transient provider failure, never as coordinator idle.
   Mesh stores only its redacted screen fingerprint, attempt count, and next
   eligible time in the existing tick state. Retries use bounded 5, 10, 20,
   and 40 minute backoff; after four attempts the supervisor reports a critical
   manual action instead of sending again. Historical `529` text and plain
   `429` errors outside Claude's exact WAIT menu do not activate this policy.
8. With `--recover-coordinator`, it applies the same recovery guards described
   above to at most one confirmed coordinator. Multiple eligible coordinators
   fail closed, and the recovery attempt is persisted before `respawn-pane`.

New coordinator sessions disable terminal XON/XOFF with `stty -ixon` before
launching Claude. This prevents an accidental `Ctrl+S` from freezing pane
rendering and delaying all tmux-delivered input; it does not alter existing
sessions or intercept any other key.

Before any send, tick recaptures the same pane, revalidates its state, and
requires the pane to still belong to the discovered session with the expected
provider process: Claude for limit/coordinator actions or `agy` for the exact
survey dismissal. A coordinator launched through the resume lock may have a
shell as the tmux pane command; tick accepts it only when the tmux coordinator
marker is present and that shell has exactly one direct child named `claude` or
`claude-code`. It records the attempt before I/O, recaptures after I/O, and
throttles retries by screen fingerprint or coordinator wake time. Ambiguous or
stale rate-limit screens are reported as `manual_rate_limit` and are never
submitted. Tick does not create sessions, choose new tasks, or blindly resend a
delegation.

An idle worker is reusable by default. Activity age alone never makes it stale.
The coordinator may report `ROTATION_CANDIDATE` only for a detached, stably idle
worker with an additional reason such as context at or below 20%, persistent
degraded TUI/provider configuration, or an explicit fresh-context request.
Before rotation it must verify no active delegation, an empty composer, no
build/test/tool activity, and clean or fully accounted Git plus durable handoff
evidence. The current contract does not authorize `kill-session` or automatic
replacement; those remain an explicit guarded operator lifecycle action.

The session-limit wake is guarded. The banner must include the exact
`/upgrade to increase your usage limit.` line. Unknown timezones, malformed
times, changed panes, changed processes, or non-coordinator pending composers
cause no send. A coordinator's pending composer must remain byte-identical
across two observations before one Enter is allowed. Because the banner has no
date, tick maps its time to the nearest past or future occurrence in the named
timezone, preferring the future occurrence on an exact tie. A future occurrence
waits through the 90-second grace; a past occurrence whose grace has elapsed
makes the guarded attempt at the next observation. For example, `resets 12am` first seen
at `5am` is due immediately, while the same banner seen at `4pm` waits for the
next midnight. This handles stale panes after a reboot without always
postponing them to the following day. An empty composer receives exactly one
fixed wake and is never retried. An unchanged pending coordinator composer may
receive at most three Enter-only attempts in total, at least four minutes apart,
so scheduler jitter cannot defer an eligible retry by an extra cron interval,
only while every pane, process, banner, timezone, and composer fingerprint guard
still matches and the previous delivery remains unverified. Each attempt is
recorded before keyboard I/O. An immediate post-send capture is diagnostic
only: terminal wrapping and redraw timing cannot prove submission. Delivery is
therefore confirmed only when a later tick observes that the pane is no longer
on the exact session-limit screen. The tombstone is retained until that later
observation,
preventing stale scrollback or changed input from triggering a duplicate.

Install the opt-in 5-minute user cron from the clean Dell runtime, not from the
Mac checkout:

```bash
cd /data/sata/1TB/gobabygo-runtime
./scripts/install-mesh-live-cron.sh --mesh-script "$PWD/scripts/mesh"
crontab -l
tail -f ~/.local/state/gobabygo/mesh-live-tick.log
```

Coordinator restart remains disabled in that default installation. Enable it
deliberately only after creating the coordinator with an explicit resume UUID:

```bash
./scripts/install-mesh-live-cron.sh --mesh-script "$PWD/scripts/mesh" --recover-coordinator
```

Preview or remove the managed crontab block without touching unrelated entries:

```bash
./scripts/install-mesh-live-cron.sh --mesh-script "$PWD/scripts/mesh" --dry-run
./scripts/install-mesh-live-cron.sh --remove
```

The installer fails without rewriting the crontab when the existing crontab
cannot be read or its managed marker block is malformed.

The same managed block runs `mesh speckit update-check --json` once daily. It
writes only validated release version, tag, timestamp, and official URL to
`~/.local/state/gobabygo/speckit-update.json`; it never installs the CLI or
upgrades a project. A later normal coordinator tick includes required/latest
versions only when that tick already has another reason to wake an idle or
post-limit coordinator. The tick state acknowledges the latest version only
after key delivery and reports it once; a newer release becomes reportable
again. An update never creates its own wake and is never sent to worker panes.
Reinstalling the managed block is idempotent.

The default schedule gives a maximum polling delay of about 5 minutes after a
review transition, completed compaction, or reset grace period. A verified
compaction receives one immediate-throttle exception on the next tick; normal
idle wakes remain throttled to avoid spending coordinator tokens every five
minutes. The
state file is mode `0600` and stores hashes, timestamps, pane IDs, and delivery
flags only; it never stores pane captures. The command uses an internal
non-blocking lock, so overlapping cron/manual ticks fail closed. Use an explicit
`--users` only for deliberately unmanaged tmux owners; do not point live tick at
router-managed owners.

Provider YOLO mode removes interactive approval prompts but does not widen the
coordinator contract. Pane output is untrusted evidence and is never executed or
piped into another command. A shell alias is not guaranteed in a non-interactive
tmux startup; when required, set an explicit trusted launch command, for example:

The ensure commands constrain only worker creation; they are not an OS sandbox.
After a task is submitted, YOLO Codex or Antigravity can perform any mutation
available to the Dell user. Repository scope, file ownership, and forbidden
actions remain prompt-level controls, so destructive or privileged work still
requires the operator boundary. Antigravity `--new-project` pins project
selection; omitting it can make `agy` reuse another cached project even when the
process cwd is correct.

```bash
export MESH_COORDINATOR_CLAUDE_CMD='claude --dangerously-skip-permissions'
```

### Manual Advisory Mode

`brief` discovers current sessions and recent pane output on every run. It asks
the coordinator for observed facts, conflicts, options, a decision, and bounded
delegations with acceptance criteria.

```bash
wbrief --repo rektslug                          # one repo
wbrief --all                                    # all live repos
wbrief --all --coordinator claude-coordinator   # explicit coordinator
```

The prompt is not auto-delivered. Review it before pasting it into the
coordinator. This prevents stale or malicious pane content from becoming an
instruction without a human boundary.

## Delegation Boundary

For an existing manual session, delegate through a bounded `mesh live send`.
For Codex include `--delegation-id <DELEGATION_ID> --enter` so a collapsed paste
can be recovered without exposing or persisting the task text. For Antigravity
include the same options to require an idle composer and post-Enter verification;
Antigravity has no automatic recovery Enter. For a missing native worker, the
coordinator may use the matching ensure command under its standing contract and
then the same bounded send/verify protocol. Neither path uses account selection
or durable router state.

For durable work or a new managed worker, reuse the router database:

```bash
mesh thread create --name <delegation-name>
mesh thread add-step --thread <delegation-name> --title <title> \
  --step-index 0 --repo <repo-path> --cli <claude|codex|antigravity> \
  --payload '{"prompt":"...","acceptance_criteria":["..."]}'
```

Router steps may invoke provider/account policy and may create a managed
session. They are scheduler inputs, not aliases for existing manual tmux
sessions. Cross-repo durable handoffs reuse the existing `handoff` packet and
require role `PRESIDENT_GLOBAL`.

The provider account manager is not required for `mcoordinator`, either ensure
command, or existing manual workers. It remains useful only when the router
launches a new managed worker and must select or rotate a provider account.

## Persistence And Transport

tmux owns process/session persistence. Mosh and SSH are reconnectable
transports; losing either transport does not terminate the tmux work.

The coordinator executes `mesh live` on the workstation. Do not point it at a
dirty or divergent development checkout. Keep a clean runtime checkout and set
its absolute path in the Mac operator shell:

```bash
export MESH_COORDINATOR_MESH_SCRIPT=/data/sata/1TB/gobabygo-runtime/scripts/mesh
```

Updating that runtime is a deployment operation; it must not reset, clean, or
pull through an unrelated dirty worktree.

- Direct reachable LAN/VPN host: `attach` prefers mosh. The shell helpers try
  authenticated SSH over LAN first, then VPN, avoiding a stale VPN UDP path while
  the operator is on the same LAN. Each reachability probe has both an SSH
  connect timeout and a local wall-clock deadline, so a half-open endpoint cannot
  stall fallback selection indefinitely.
- Persistent create/attach runs one short read-only SSH preflight before mosh to
  validate the repo, scoped resume ID, and existing coordinator. This is required
  because mosh does not reliably propagate the remote command's exit status.
- Launch and tmux startup commands are staged over SSH in private remote files
  and unlinked as execution begins, avoiding mosh and tmux command-length limits
  for coordinator prompts.
- A mosh transport failure falls back to SSH without changing the tmux session.
- SSH attach retries transport failures three times by default, then exits while
  leaving tmux untouched. Set `MESH_WS_SSH_RECONNECT_ATTEMPTS` to another
  non-negative count when a different operator policy is required.
- Validation failures, stale coordinator detection, and operator interruption do
  not trigger a second attach attempt through SSH.
- ProxyJump or Cloudflare SSH host: `attach` uses SSH directly.
- Read-only and send controls use short SSH calls.
- `MESH_LIVE_HOSTS` can set an explicit comma-separated fallback order for `mesh live`.
- `MESH_WS_CONTROL_HOST` forces the shell-helper control host.
- `MESH_MOSH_HOST` must be a trusted direct VPN/LAN endpoint and overrides the
  automatic LAN-first selection.

Do not expose mosh as a public bypass around VPN/firewall policy.

## Send Safety

Treat `send` as remote keyboard access, not as a messaging API.

1. Run `wpeek <session>` immediately before sensitive input.
2. Verify owner, session, repository, receiving prompt, and exact text.
3. Omit `--enter` unless immediate submission is intended.
4. Never pipe model/pane output into `wsend`, `eval`, or a shell.
5. Use one line only. Put long/multi-line briefs in a non-secret repository file
   and send its absolute path plus the `DELEGATION_ID`.

The output fields `text_delivered` and `enter_delivered` mean only that tmux
accepted those keys. `submission=verified` is provider-specific positive redraw
evidence; `submission=unknown` is not proof that the CLI accepted the task. The
send path immediately checks that the pane still belongs to the discovered
session; automatic tick sends also require a current Claude process. The pane
can still change after that check, so this narrows but cannot eliminate the
race. Redaction removes common terminal escapes, credentials, tokens, marked
private-key blocks, and consecutive PEM-like Base64 lines on a best-effort
basis. Isolated Base64 is preserved, so this is not a DLP boundary.

## Code Map

- `scripts/mesh_live_cli.py`: tmux discovery, capture, send, attach, brief, tick
- `scripts/mesh_live_shell_helpers.sh`: `w*`/`m*` operator helpers
- `scripts/install-mesh-live-cron.sh`: opt-in periodic local tick installer
- `scripts/mesh`: command dispatcher and router-thread bridge
- `src/router/cli_screen.py`: shared Claude and Antigravity screen classification
- `src/router/session_worker.py`: router-managed sessions only
- `scripts/mesh_iterm_control.py`: optional iTerm2 pane control
- `scripts/mesh_iterm_ui.py`: optional iTerm2 layout
