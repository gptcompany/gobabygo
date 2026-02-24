# Mesh Network Boot Order

## VPS Startup Sequence

1. **Network** — systemd-networkd / NetworkManager
2. **WireGuard** — `wg-quick@wg0.service` (After=network-online.target)
3. **Mesh Router** — `mesh-router.service` (After=wg-quick@wg0.service)
   - Recovery: requeues expired leases on startup
   - Health: `GET /health` returns 200 when ready
   - Watchdog: systemd `WatchdogSec=30`, notifies every 10s

## Workstation Startup Sequence

1. **Network** — systemd-networkd / NetworkManager
2. **WireGuard** — `wg-quick@wg0.service`
3. **Mesh Workers** — `mesh-worker@*.service` (After=wg-quick@wg0.service)
   - Each instance registers with router on start
   - Heartbeat begins immediately (5s interval)
   - Stale detection at 35s (WireGuard keepalive 25s)
   - `mesh-session-worker@*.service` is the interactive/tmux variant (Claude/Codex session workers)

## MacBook (.112) Control Terminal Sequence (iTerm2)

1. **Open iTerm2** on macOS `.112` (operator UX only; not source of truth)
2. **Ensure CLI PATH is loaded** (login shell / `zsh -l`)
3. **Claude Agent Teams flag enabled** in `~/.claude/settings.json`:
   - `"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"}`
4. **Open control sessions**:
   - VPS pane: router logs / queue / heartbeat monitors
   - WS pane(s): worker logs and `tmux` attach for interactive session workers
5. **Human gate behavior is CLI-native**:
   - approval/yolo/manual modes are configured in each CLI, not in router logic

See `deploy/MAC-112-ITERM2-CLI-SETUP.md` for install/update/verification commands.

## Verification

After boot, run:

```bash
./deploy/verify-network.sh http://10.x.x.1:8780
```

## Failure Recovery

| Scenario | Recovery |
|----------|----------|
| Router crash | systemd `Restart=always`, `RestartSec=5`. Recovery requeues expired leases. |
| Worker crash | systemd `Restart=always`, `RestartSec=10`. Re-registers on start. |
| WireGuard down | Workers enter stale state. Router requeues tasks at 35s threshold. |
| VPS reboot | Full sequence: WG -> Router -> Workers reconnect automatically. |
| DB corruption | Stop router, restore from backup, restart. WAL checkpoint on clean shutdown. |

## Service Management

```bash
# Router (VPS)
sudo systemctl start mesh-router
sudo systemctl stop mesh-router
sudo systemctl status mesh-router
journalctl -u mesh-router -f

# Workers (Workstation)
sudo systemctl start mesh-worker@claude-work
sudo systemctl stop mesh-worker@claude-work
sudo systemctl status mesh-worker@claude-work
journalctl -u mesh-worker@claude-work -f

# All workers
sudo systemctl start mesh-worker@claude-work mesh-worker@codex-work mesh-worker@gemini-work

# Interactive session workers (tmux-backed)
sudo systemctl start mesh-session-worker@mesh-session-claude-work
sudo systemctl start mesh-session-worker@mesh-session-codex-work

# MacBook (.112) quick checks (operator machine, VPN-first)
./deploy/check-mac-112-cli.sh
# or explicit (VPN)
ssh sam@10.0.0.112 'zsh -lic "command -v claude codex gemini"'
ssh sam@10.0.0.112 'zsh -lic "python3 - <<\"PY\"\nimport json, os; print(json.load(open(os.path.expanduser(\"~/.claude/settings.json\"))).get(\"env\",{}).get(\"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS\"))\nPY"'
```
