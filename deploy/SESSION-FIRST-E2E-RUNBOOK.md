# Session-First E2E Runbook (VPN-First, `.111` + `.112`)

Purpose:
- validate the interactive `session-first` path end-to-end with real CLIs (`claude`, `codex`)
- keep human approval gates CLI-native (manual/yolo/etc.)
- use router/DB as source of truth and iTerm2 on Mac `.112` as operator console

This runbook assumes the documented topology:
- `VPS` router host on WireGuard (`10.0.0.1`)
- `WS` worker host (LAN `.111`; WireGuard IP per `network-infra`, typically `10.0.0.2`)
- `Mac` operator host (LAN `.112`; use VPN-first if/when WG IP is assigned/documented)

## 0. Preconditions

1. Router and worker code deployed on VPS/WS.
2. `tmux` installed on WS.
3. Session worker env files installed on WS:
   - `/etc/mesh-worker/mesh-session-claude-work.env`
   - `/etc/mesh-worker/mesh-session-codex-work.env`
4. Claude/Codex CLIs installed on WS workers (for session execution).
5. Mac `.112` has operator CLIs and Claude Agent Teams flag verified:
   - `bash ./deploy/check-mac-112-cli.sh`

## 1. Bring Up Control Plane (VPS)

On VPS (`10.0.0.1`):

```bash
sudo systemctl restart mesh-router
sudo systemctl status --no-pager mesh-router
curl -s http://127.0.0.1:8780/health
```

Expected:
- router `active (running)`
- `/health` returns `status=healthy`

## 2. Bring Up Interactive Session Workers (WS / `.111`)

On WS (`.111`, preferably via WG path):

```bash
sudo systemctl restart mesh-session-worker@mesh-session-claude-work
sudo systemctl restart mesh-session-worker@mesh-session-codex-work

sudo systemctl status --no-pager mesh-session-worker@mesh-session-claude-work
sudo systemctl status --no-pager mesh-session-worker@mesh-session-codex-work
```

Live logs (separate panes recommended):

```bash
journalctl -u mesh-session-worker@mesh-session-claude-work -f
journalctl -u mesh-session-worker@mesh-session-codex-work -f
```

Expected:
- workers register successfully
- long-poll/heartbeat starts

## 3. Verify Worker Registration (Router)

From VPS (or anywhere that can reach router):

```bash
curl -s http://10.0.0.1:8780/workers | python3 -m json.tool
```

Check:
- `ws-*-session-*` workers appear
- `cli_type=claude` / `codex`
- workers are `idle` before task submission

## 4. Submit an Interactive Claude Task (`execution_mode=session`)

Submit from VPS (or operator shell that can reach router):

```bash
curl -s -X POST http://10.0.0.1:8780/tasks \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Interactive smoke test (Claude)",
    "phase": "implement",
    "target_cli": "claude",
    "target_account": "claude-samuele",
    "execution_mode": "session",
    "payload": {
      "prompt": "Inspect the repo root and tell me what files are relevant for router session persistence. Ask before risky commands."
    },
    "idempotency_key": "smoke-session-claude-001"
  }' | python3 -m json.tool
```

Expected:
- response contains `"status": "created"` and `task_id`

## 5. Confirm Session Persistence in Router DB (SoT)

List sessions:

```bash
curl -s http://10.0.0.1:8780/sessions | python3 -m json.tool
```

Find the new session and capture:
- `session_id`
- `task_id`
- `metadata.tmux_session`
- `worker_id`

Tail messages:

```bash
curl -s "http://10.0.0.1:8780/sessions/messages?session_id=<SESSION_ID>&limit=50" | python3 -m json.tool
```

Expected:
- `system` message: tmux session created
- `in` message: initial task prompt (`president`)
- `out` messages: CLI output (`role=cli`)

## 6. Human-In-The-Loop Test via Bus (Operator Message)

Send a manual operator message to the active session:

```bash
curl -s -X POST http://10.0.0.1:8780/sessions/send \
  -H 'Content-Type: application/json' \
  -d '{
    "session_id": "<SESSION_ID>",
    "direction": "in",
    "role": "operator",
    "content": "Pause and summarize what you are doing in 3 bullets before any edits."
  }' | python3 -m json.tool
```

Re-read session messages:

```bash
curl -s "http://10.0.0.1:8780/sessions/messages?session_id=<SESSION_ID>&limit=100" | python3 -m json.tool
```

Expected:
- `POST /sessions/send` accepted with a new `seq`
- subsequent `out` message(s) from CLI reflect the operator intervention

Note:
- command approval prompts/risky-action gates remain CLI-native (Claude/Codex config), not router logic

## 7. Attach from iTerm2 on Mac `.112` (tmux Operator Console)

Open iTerm2 on Mac `.112` and SSH to WS (`10.0.0.x` preferred, fallback LAN `.111`).

List tmux sessions:

```bash
ssh sam@10.0.0.2 'tmux ls'    # replace with actual WG IP for WS if different
# fallback
ssh sam@192.168.1.111 'tmux ls'
```

Attach to the session recorded in router metadata:

```bash
ssh -t sam@10.0.0.2 'tmux attach -t <TMUX_SESSION_NAME>'
# or use tmux control mode with iTerm2 integration if preferred
```

What to verify:
- same session visible in `tmux` as in router `/sessions`
- CLI prompts/approval gates visible directly in tmux
- operator can intervene either:
  - directly in tmux (interactive attach), or
  - via router bus (`/sessions/send`)

## 8. Submit a Codex Interactive Task (Optional but Recommended)

Repeat step 4 with:
- `"target_cli": "codex"`
- `"target_account": "work-codex"`
- unique `idempotency_key`

This validates multi-CLI session routing (Claude + Codex) with the same session bus model.

## 9. Success Criteria (MVP Session-First)

1. `execution_mode=session` task is assigned only to session worker (not batch worker).
2. Router exposes persisted session record (`/sessions`).
3. Initial task prompt is persisted as `direction=in`.
4. CLI output is persisted as `direction=out`.
5. Operator message via `/sessions/send` is delivered and reflected in later CLI output.
6. iTerm2 attach on `.112` can observe and steer the same tmux session.
7. CLI-native approval prompts remain visible/functional (depending on CLI config).

## 10. Cleanup

On WS:

```bash
sudo systemctl stop mesh-session-worker@mesh-session-claude-work
sudo systemctl stop mesh-session-worker@mesh-session-codex-work
```

On VPS (optional if test-only environment):

```bash
sudo systemctl stop mesh-router
```

## 11. If Something Fails (Fast Triage)

1. Router unreachable:
   - verify WG/LAN path (`10.0.0.x` first, fallback LAN)
   - `curl -s http://10.0.0.1:8780/health`
2. Worker not registering:
   - `journalctl -u mesh-session-worker@... -f`
   - confirm `MESH_AUTH_TOKEN` and `MESH_ROUTER_URL`
3. Session opens but no CLI output:
   - `tmux ls`, `tmux capture-pane -p -t <session>:0.0`
   - verify CLI binary exists in worker PATH (`claude`/`codex`)
4. Operator message accepted but no effect:
   - confirm session still `state=open`
   - inspect `/sessions/messages` for the new `in` message `seq`
   - check worker logs for delivery warnings
