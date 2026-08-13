# Mesh Live Operator Runbook

`mesh live` operates tmux sessions directly. It does not require the router,
session workers, iTerm2, or the provider account manager. Its only lifecycle
operation is the constrained, local-only `ensure-codex` worker bootstrap.

## Daily Flow

Run these helpers from any directory in the Mac operator shell. The target repos,
tmux sessions, and AI CLIs remain on the Dell workstation.

```bash
mcoordinator rektslug                           # intra-repo; worker bootstrap is automatic
mcoordinator --all                              # multi-repo; choose/delegate through Claude
```

Install or refresh the helpers once:

```bash
./scripts/install-shell-helpers.sh
source ~/.zshrc
```

The coordinator reuses a suitable live worker. If none exists, its standing
contract permits exactly one `mesh live ensure-codex` call for the selected Git
root; this creates `codex-<repo>` with the trusted native Codex binary in YOLO
mode, sends no task, then requires a fresh board/peek before delegation. Use
`mcoordinator rektslug --worker codex-rektslug` when the exact worker name must
be pinned. `mcodex rektslug` remains available for a deliberately manual shell.

`mcoordinator` injects the autonomous system contract only when its tmux
session is first created; later calls only attach. An already running
coordinator therefore keeps its old contract. Bootstrap a new coordinator (or
exit the old tmux session and resume it) to activate a newly deployed contract.

Repository names resolve below `MESH_WS_REPO_BASE`. A missing target fails
before tmux session creation.
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
| `wpeek <session> [lines]` | `mesh live peek <session> [lines]` | Capture one exact/unique pane; read-only |
| `wbrief ...` | `mesh live brief ...` | Build a dynamic, redacted coordinator prompt; read-only |
| `mcoordinator ...` | `mesh live coordinator-prompt ...` | Create/attach a persistent auto-configured Claude coordinator |
| - | `mesh live ensure-codex <repo>` | Locally create/reuse one deterministic native Codex worker; no task is sent |
| - | `mesh live tick` | Inspect local Claude sessions and print a metadata-only action plan |
| - | `mesh live tick --apply` | Apply only exact WAIT and idle-coordinator wake actions |
| `wsend <session> <text>` | `mesh live send ...` | Type literal text into the selected pane |
| `wsend <session> <text> --enter` | `mesh live send ... --enter` | Type text, then send Enter separately |
| - | `mesh live send <codex-session> <text> --delegation-id <id> --enter` | Deliver and record a metadata-only recovery receipt |
| - | `mesh live recover-codex-submit <session> <id>` | Guarded, stateful single-Enter recovery for one exact Codex delegation |
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

`send` accepts one literal line up to 8192 characters; newlines and control
characters are rejected. For a long or multi-line delegation, the coordinator
writes a non-secret brief inside the target repository and sends one line with
the `DELEGATION_ID`, absolute brief path, and instruction to read and execute
that file. Codex delegations use the same ID in `--delegation-id`; this records
only owner/session/pane, ID, character count, SHA-256 digest, and timestamp,
never task text. This keeps remote keyboard input bounded and auditable.

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

When no suitable Codex worker exists, the automatic path may run only:

```bash
MESH_LIVE_LOCAL=1 mesh live ensure-codex <repo-name-or-absolute-git-root>
```

The target must resolve to an exact Git root below `MESH_LIVE_REPO_ROOTS` (or
`MESH_WS_REPO_BASE`), and the tmux name is deterministically `codex-<repo>`.
In multi-repo mode a repo name must come from the operator's explicit objective;
an absolute path may instead come from tmux metadata. Pane capture text is never
used as a command argument. Missing or ambiguous names fail before tmux mutation.
`--expect-session` makes a pinned coordinator fail before tmux mutation when
that name differs. Existing name/path/process/pane collisions fail closed;
concurrent calls reuse the atomic winner. The command invokes only the trusted
`/usr/local/bin/codex` or `/usr/bin/codex`, does not accept task text, does not
use `send-keys`, and is unavailable through the remote live endpoint. If Codex
authentication or startup fails, the coordinator reports a blocker.

The active `MESH_COORDINATOR_MESH_SCRIPT` checkout is an immutable control-plane
runtime even when Git reports detached HEAD. `ensure-codex` rejects that exact
Git root before tmux mutation. Gobabygo development must use a separate clean
branch checkout or Git worktree; do not create a branch inside the live runtime.

### Periodic Supervisor

`mesh live tick` is the bounded polling primitive. It must run on the Dell tmux
workstation; it is read-only unless `--apply` is present. By default it only
inspects tmux sessions owned by the user running it, so it does not compete with
router-owned `mesh-worker`/`mesh` sessions and their `session_worker` retry and
account-rotation policy.

```bash
MESH_LIVE_LOCAL=1 mesh live tick --json
MESH_LIVE_LOCAL=1 mesh live tick --apply
```

Apply mode has two actions:

1. It sends Enter only when the exact Claude rate-limit menu is present and
   `Stop and wait for limit to reset` is already the selected option.
2. It sends a fixed `MESH_LIVE_TICK` instruction only when a coordinator is at
   an empty idle prompt. The coordinator then boards and peeks dynamically and
   decides whether existing work needs review, debate, or delegation.

Before either send, tick recaptures the same pane, revalidates its state, and
requires the pane to still belong to the discovered session with Claude as its
current process. It records the attempt before I/O, recaptures after I/O, and
throttles retries by screen fingerprint or coordinator wake time. Ambiguous or
stale rate-limit screens are reported as `manual_rate_limit` and are never
submitted. Tick does not create sessions, choose new tasks, or blindly resend a
delegation.

Install the opt-in 30-minute user cron from the clean Dell runtime, not from the
Mac checkout:

```bash
cd /data/sata/1TB/gobabygo-runtime
./scripts/install-mesh-live-cron.sh --mesh-script "$PWD/scripts/mesh"
crontab -l
tail -f ~/.local/state/gobabygo/mesh-live-tick.log
```

Preview or remove the managed crontab block without touching unrelated entries:

```bash
./scripts/install-mesh-live-cron.sh --mesh-script "$PWD/scripts/mesh" --dry-run
./scripts/install-mesh-live-cron.sh --remove
```

The installer fails without rewriting the crontab when the existing crontab
cannot be read or its managed marker block is malformed.

The default schedule gives a maximum polling delay of about 30 minutes. The
state file is mode `0600` and stores hashes, timestamps, pane IDs, and delivery
flags only; it never stores pane captures. The command uses an internal
non-blocking lock, so overlapping cron/manual ticks fail closed. Use an explicit
`--users` only for deliberately unmanaged tmux owners; do not point live tick at
router-managed owners.

Provider YOLO mode removes interactive approval prompts but does not widen the
coordinator contract. Pane output is untrusted evidence and is never executed or
piped into another command. A shell alias is not guaranteed in a non-interactive
tmux startup; when required, set an explicit trusted launch command, for example:

`ensure-codex` constrains only worker creation; it is not an OS sandbox. After a
task is submitted, the YOLO Codex process can perform any mutation available to
the Dell user. Repository scope, file ownership, and forbidden actions remain
prompt-level controls, so destructive or privileged work still requires the
operator boundary.

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
can be recovered without exposing or persisting the task text.
For a missing native Codex worker, the coordinator may use `ensure-codex` under
its standing contract and then the same bounded send/verify protocol. Neither
path uses account selection or durable router state.

For durable work or a new managed worker, reuse the router database:

```bash
mesh thread create --name <delegation-name>
mesh thread add-step --thread <delegation-name> --title <title> \
  --step-index 0 --repo <repo-path> --cli <claude|codex|gemini> \
  --payload '{"prompt":"...","acceptance_criteria":["..."]}'
```

Router steps may invoke provider/account policy and may create a managed
session. They are scheduler inputs, not aliases for existing manual tmux
sessions. Cross-repo durable handoffs reuse the existing `handoff` packet and
require role `PRESIDENT_GLOBAL`.

The provider account manager is not required for `mcoordinator`, `ensure-codex`,
or existing manual workers. It remains useful only when the router launches a
new managed worker and must select or rotate a provider account.

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

- Direct reachable VPN/LAN host: `attach` prefers mosh.
- Persistent create/attach runs one short read-only SSH preflight before mosh to
  validate the repo, scoped resume ID, and existing coordinator. This is required
  because mosh does not reliably propagate the remote command's exit status.
- A mosh transport failure falls back to SSH without changing the tmux session.
- Validation failures, stale coordinator detection, and operator interruption do
  not trigger a second attach attempt through SSH.
- ProxyJump or Cloudflare SSH host: `attach` uses SSH directly.
- Read-only and send controls use short SSH calls.
- `MESH_LIVE_HOSTS` can set an explicit comma-separated fallback order for `mesh live`.
- `MESH_WS_CONTROL_HOST` forces the shell-helper control host.
- `MESH_MOSH_HOST` must be a trusted direct VPN/LAN endpoint.

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
accepted those keys; `submission=unknown` is not proof that the CLI accepted
the task. The send path immediately checks that the pane still belongs to the discovered
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
- `src/router/cli_screen.py`: shared stdlib-only Claude screen classification
- `src/router/session_worker.py`: router-managed sessions only
- `scripts/mesh_iterm_control.py`: optional iTerm2 pane control
- `scripts/mesh_iterm_ui.py`: optional iTerm2 layout
