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

For multi-repo coordination:

```bash
mcoordinator --all
```

If `claude-coordinator` already exists without the new contract, preserve it and
create a fresh coordinator instead:

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
| `wsend <session> <text>` | `mesh live send ...` | Type literal text into the selected pane |
| `wsend <session> <text> --enter` | `mesh live send ... --enter` | Type text, then send Enter separately |
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

The automatic path coordinates existing sessions. It does not create worker
CLIs. Start a manual persistent worker with `mcodex <repo>` / `mclaude <repo>`,
or use a router thread when a new managed worker and durable task history are
actually needed.

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

- Direct reachable VPN/LAN host: `attach` prefers mosh.
- ProxyJump or Cloudflare SSH host: `attach` falls back to SSH.
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

The pane can change between `peek` and `send`. Exact pane selection prevents
name ambiguity but cannot prevent that race. Redaction removes common terminal
escapes, credentials, tokens, marked private-key blocks, and consecutive
PEM-like Base64 lines on a best-effort basis. Isolated Base64 is preserved, so
this is not a DLP boundary.

## Code Map

- `scripts/mesh_live_cli.py`: tmux discovery, capture, send, attach, brief
- `scripts/mesh_live_shell_helpers.sh`: `w*`/`m*` operator helpers
- `scripts/mesh`: command dispatcher and router-thread bridge
- `src/router/session_worker.py`: router-managed sessions only
- `scripts/mesh_iterm_control.py`: optional iTerm2 pane control
- `scripts/mesh_iterm_ui.py`: optional iTerm2 layout
