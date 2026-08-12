# Mesh Live Operator Runbook

`mesh live` operates existing tmux sessions directly. It does not require the
router, session workers, iTerm2, or the provider account manager.

## Daily Flow

Run these helpers from any directory in the Mac operator shell. The target repos,
tmux sessions, and AI CLIs remain on the Dell workstation.

```bash
mcodex rektslug                                 # create/attach codex-rektslug in the repo
mcoordinator rektslug --worker codex-rektslug  # Claude coordinator; prompt is automatic
```

Install or refresh the helpers once:

```bash
./scripts/install-shell-helpers.sh
source ~/.zshrc
```

On the first `mcodex rektslug`, start Codex in that persistent shell using the
normal configured command, then detach from tmux. Existing worker sessions can
be reused directly. `mcoordinator` starts Claude with the autonomous system
contract only when its tmux session is first created; later calls only attach.

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
Both options are used only when the tmux session is created; if the tmux already
exists and is a valid coordinator, `mcoordinator` only attaches to it and ignores
resume validation. New coordinators carry a tmux marker so they remain
recognizable if Claude exits back to a shell. An older, unmarked session whose
active process is only `bash`, `zsh`, `sh`, or `fish` fails closed instead of
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
| - | `mesh live tick` | Inspect local Claude sessions and print a metadata-only action plan |
| - | `mesh live tick --apply` | Apply only exact WAIT and idle-coordinator wake actions |
| `wsend <session> <text>` | `mesh live send ...` | Type literal text into the selected pane |
| `wsend <session> <text> --enter` | `mesh live send ... --enter` | Type text, then send Enter separately |
| - | `mesh live recover-codex-submit <session> <id>` | Guarded, stateful single-Enter recovery for one exact Codex delegation |
| `wsattach <session>` | `mesh live attach <session>` | Attach to an existing session; never creates or kills one |

Use `--owner <user>` when the same session name exists under multiple tmux
owners. Use `board --lines 0` when only metadata is needed.

## Coordinator Scope

### Automatic Mode

`mcoordinator <repo> --worker <session>` limits delegation to one exact existing
worker. Without `--worker`, the coordinator discovers candidates in scope but
still resolves one exact session before sending. `mcoordinator --all` enables
multi-repo observation and coordination.

The injected contract tells Claude to board and peek dynamically, debate viable
options, create a unique `DELEGATION_ID`, send a bounded assignment, peek again
to verify CLI acceptance, monitor completion, and inspect result/test evidence.
A successful tmux send is not treated as delivery: the coordinator must find the
delegation ID or clear CLI activity and must never resend blindly.

Codex can occasionally leave a rapidly typed/pasted task in its composer even
when the initial send included `--enter`. The coordinator contract includes one
bounded paste-settle recovery: after an immediate peek, and only when the exact
current `DELEGATION_ID` is still visible in the bottom Codex composer with no
Working/activity, it invokes the guarded recovery command and peeks again:

```bash
mesh live recover-codex-submit <codex-session> <DELEGATION_ID>
mesh live peek <codex-session> 80
```

The recovery command accepts no task text. It recaptures only the current visible
pane, checks the exact bottom composer and Codex process, rejects activity,
menus, confirmations, shell prompts, mismatched IDs, and records the attempt
before sending Enter. Its metadata-only state rejects every second attempt for
the same delegation and pane. The state path is fixed by the workstation code;
the coordinator cannot redirect that write through a CLI option or request
payload. This keeps the recovery evidence-driven. Screen
changes in the final interval between the atomic recapture and tmux input cannot
be eliminated completely; the command
narrows that race and verifies the screen again afterward. Any refusal or
unverified result requires operator review, never fallback to task resend or a
plain second `send --enter`.

The automatic path coordinates existing sessions. It does not create worker
CLIs. Start a manual persistent worker with `mcodex <repo>` / `mclaude <repo>`,
or use a router thread when a new managed worker and durable task history are
actually needed.

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

For an existing manual session, delegate through a reviewed `wsend` proposal.
This path does not use account selection or create another worker.

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

The provider account manager is not required for `mcoordinator` or existing
manual workers. It remains useful only when the router launches a new managed
worker and must select or rotate a provider account.

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

The send path immediately checks that the pane still belongs to the discovered
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
