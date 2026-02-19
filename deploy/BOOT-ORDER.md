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
```
